# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""Compiler: ModelSpec -> DDL Postgres (bảng dùng chung + tenant_id + RLS).

Mọi định danh qua psycopg.sql.Identifier, mọi giá trị qua sql.Literal (DDL) hoặc
sql.Placeholder (truy vấn). Tên constraint/index/policy suy ra từ ID ỔN ĐỊNH
(không từ tên), nên rename trường/model không làm vỡ ánh xạ lỗi.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field as dc_field
from typing import Callable

from psycopg import sql

from .spec import And, Catalog, Cmp, Expr, FieldSpec, ModelSpec, Not, Or, coerce_value

SCHEMA = "xt_data"
APP_ROLE = "xt_app"
WF_ROLE = "xt_workflow"   # G-M1: chỉ role này UPDATE được cột managed_by=workflow
INSERT_SYS = ["id", "owner_id", "team_id", "department_id"]
PG_TYPE = {
    "text": "text", "number": "numeric", "money": "bigint", "date": "date",
    "datetime": "timestamptz", "bool": "boolean", "enum": "text", "ref": "uuid",
    "user": "uuid", "file": "uuid", "json": "jsonb",
}
ACTIONS = {"read": "SELECT", "create": "INSERT", "update": "UPDATE", "delete": "DELETE"}
JSON_MAX_BYTES = 65536


@dataclass
class Step:
    sql: sql.Composable
    phase: str = "tx"          # "tx" = trong transaction; "concurrent" = sau commit, autocommit
    order: int = 50            # thứ tự toàn cục: bảng < cột < constraint < FK < policy

    def text(self) -> str:
        return self.sql.as_string()


@dataclass(frozen=True)
class Layout:
    """Bố trí vật lý. A: Layout() = xt_data.<model> dùng chung.
    B1 (D6): Layout(table_prefix='t7__', index_prefix='t7_') = xt_data.t7__<model>.
    B2: Layout(schema='xt_t7') = xt_t7.<model> (schema theo tenant)."""
    schema: str = SCHEMA
    table_prefix: str = ""
    index_prefix: str = ""


LAYOUT: ContextVar[Layout] = ContextVar("xt_layout", default=Layout())


def table(m: ModelSpec | str) -> sql.Identifier:
    lay = LAYOUT.get()
    return sql.Identifier(lay.schema, lay.table_prefix + (m if isinstance(m, str) else m.name))


def iname(n: str) -> str:
    """Tên index/constraint có index: duy nhất trong SCHEMA => phải có tiền tố tenant ở B1."""
    n = LAYOUT.get().index_prefix + n
    assert len(n) <= 63, n
    return n


def cname(m: ModelSpec, f: FieldSpec | None, kind: str, sub: str | None = None) -> str:
    """Tên ổn định, <= 63 byte: id model, id trường đều <= 24 ký tự."""
    parts = [kind, m.id] + ([f.id] if f else []) + ([sub] if sub else [])
    n = "_".join(parts)
    assert len(n) <= 63, n
    return n


# ------------------------------------------------------- DSL -> SQL ----
Resolver = Callable[[FieldSpec], sql.Composable]


def _col(f: FieldSpec) -> sql.Composable:
    return sql.Identifier(f.name)


def compile_expr(e: Expr, m: ModelSpec, lit: Callable[[object], sql.Composable] = sql.Literal,
                 col: Resolver = _col) -> sql.Composable:
    if isinstance(e, And):
        return sql.SQL("({})").format(sql.SQL(" AND ").join(compile_expr(k, m, lit, col) for k in e.and_))
    if isinstance(e, Or):
        return sql.SQL("({})").format(sql.SQL(" OR ").join(compile_expr(k, m, lit, col) for k in e.or_))
    if isinstance(e, Not):
        return sql.SQL("(NOT {})").format(compile_expr(e.not_, m, lit, col))
    assert isinstance(e, Cmp)
    f = m.field(e.field)
    c = col(f)
    if e.op == "is_null":
        return sql.SQL("({} IS NULL)").format(c)
    if e.op == "not_null":
        return sql.SQL("({} IS NOT NULL)").format(c)
    if e.op == "in":
        return sql.SQL("({} IN ({}))").format(c, sql.SQL(", ").join(lit(coerce_value(f, v)) for v in e.value))
    if e.op in ("contains", "startswith"):
        esc = str(e.value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = ("%" + esc + "%") if e.op == "contains" else (esc + "%")
        return sql.SQL("({} ILIKE {} ESCAPE '\\')").format(c, lit(pat))
    op = sql.SQL("<>" if e.op == "!=" else e.op)
    rhs = col(m.field(e.other_field)) if e.other_field else lit(coerce_value(f, e.value))
    return sql.SQL("({} {} {})").format(c, op, rhs)


# --------------------------------------------------------- constraints ----
@dataclass
class Check:
    name: str
    kind: str                 # min|max|regex|enum|json|rule
    expr: sql.Composable      # biểu thức CHECK
    message: str              # tiếng Việt cho agent chuyển lại người dùng
    fields: list[str] = dc_field(default_factory=list)


def field_checks(m: ModelSpec, f: FieldSpec, col: Resolver = _col) -> list[Check]:
    c = col(f)
    out: list[Check] = []
    L = f.label
    if f.type == "text":
        ln = sql.SQL("char_length({})").format(c)
        if f.min is not None:
            out.append(Check(cname(m, f, "ck", "min"), "min", sql.SQL("{} >= {}").format(ln, sql.Literal(int(f.min))),
                             f"'{L}' phải có ít nhất {int(f.min)} ký tự", [f.name]))
        if f.max is not None:
            out.append(Check(cname(m, f, "ck", "max"), "max", sql.SQL("{} <= {}").format(ln, sql.Literal(int(f.max))),
                             f"'{L}' tối đa {int(f.max)} ký tự", [f.name]))
        if f.regex is not None:
            out.append(Check(cname(m, f, "ck", "re"), "regex", sql.SQL("{} ~ {}").format(c, sql.Literal(f.regex)),
                             f"'{L}' không đúng định dạng (mẫu {f.regex})", [f.name]))
    elif f.type in ("number", "money", "date", "datetime"):
        if f.min is not None:
            out.append(Check(cname(m, f, "ck", "min"), "min", sql.SQL("{} >= {}").format(c, sql.Literal(coerce_value(f, f.min))),
                             f"'{L}' phải >= {f.min}", [f.name]))
        if f.max is not None:
            out.append(Check(cname(m, f, "ck", "max"), "max", sql.SQL("{} <= {}").format(c, sql.Literal(coerce_value(f, f.max))),
                             f"'{L}' phải <= {f.max}", [f.name]))
    elif f.type == "enum":
        vals = [o.value for o in f.options]
        out.append(Check(cname(m, f, "ck", "enum"), "enum",
                         sql.SQL("{} IN ({})").format(c, sql.SQL(", ").join(map(sql.Literal, vals))),
                         f"'{L}' chỉ nhận: {', '.join(vals)}", [f.name]))
    elif f.type == "json":
        out.append(Check(cname(m, f, "ck", "json"), "json",
                         sql.SQL("octet_length({}::text) <= {}").format(c, sql.Literal(JSON_MAX_BYTES)),
                         f"'{L}' vượt {JSON_MAX_BYTES} byte", [f.name]))
    return out


def rule_checks(m: ModelSpec, col: Resolver = _col) -> list[Check]:
    from .spec import expr_depth_and_fields
    return [Check(f"ck_{m.id}_r_{r.id}", "rule", compile_expr(r.check, m, sql.Literal, col), r.message,
                  sorted(expr_depth_and_fields(r.check)[1])) for r in m.rules]


def all_checks(m: ModelSpec) -> list[Check]:
    out = []
    for f in m.fields:
        out += field_checks(m, f)
    return out + rule_checks(m)


def column_def(f: FieldSpec, with_not_null: bool = True) -> sql.Composable:
    parts = [sql.Identifier(f.name), sql.SQL(PG_TYPE[f.type])]
    if f.default is not None:
        parts += [sql.SQL("DEFAULT"), sql.Literal(coerce_value(f, f.default))]
    if f.required and with_not_null:
        parts.append(sql.SQL("NOT NULL"))
    return sql.SQL(" ").join(parts)


def fk_step(m: ModelSpec, f: FieldSpec) -> Step:
    # FK ghép (tenant_id, col) -> đích(tenant_id, id): chặn tham chiếu chéo tenant ở mức DB.
    return Step(sql.SQL("ALTER TABLE {} ADD CONSTRAINT {} FOREIGN KEY (tenant_id, {}) REFERENCES {} (tenant_id, id) ON DELETE RESTRICT").format(
        table(m), sql.Identifier(cname(m, f, "fk")), sql.Identifier(f.name), table(f.ref)), order=60)


def unique_index(m: ModelSpec, f: FieldSpec, concurrently: bool) -> Step:
    return Step(sql.SQL("CREATE UNIQUE INDEX {}{} ON {} (tenant_id, {})").format(
        sql.SQL("CONCURRENTLY IF NOT EXISTS ") if concurrently else sql.SQL(""),
        sql.Identifier(iname(cname(m, f, "ux"))), table(m), sql.Identifier(f.name)),
        phase="concurrent" if concurrently else "tx", order=70)


def index_step(m: ModelSpec, ix, concurrently: bool) -> Step:
    return Step(sql.SQL("CREATE {}INDEX {}{} ON {} (tenant_id, {})").format(
        sql.SQL("UNIQUE ") if ix.unique else sql.SQL(""),
        sql.SQL("CONCURRENTLY IF NOT EXISTS ") if concurrently else sql.SQL(""),
        sql.Identifier(iname(f"ix_{m.id}_{ix.id}")), table(m),
        sql.SQL(", ").join(sql.Identifier(x) for x in ix.fields)),
        phase="concurrent" if concurrently else "tx", order=70)


def comment_steps(m: ModelSpec) -> list[Step]:
    s = [Step(sql.SQL("COMMENT ON TABLE {} IS {}").format(table(m), sql.Literal(m.label)), order=90)]
    for f in m.fields:
        s.append(Step(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(table(m), sql.Identifier(f.name), sql.Literal(f.label)), order=90))
    return s


SCOPE_SQL = {
    "all": "true",
    "own": "owner_id = xt.actor()",
    "team": "(owner_id = xt.actor() OR xt.in_teams(team_id))",
    "department": "(owner_id = xt.actor() OR xt.in_departments(department_id))",
}


def _roles() -> sql.Composable:
    return sql.SQL(", ").join([sql.Identifier(APP_ROLE), sql.Identifier(WF_ROLE)])


def audit_trigger(m: ModelSpec, replace: bool = False) -> list[Step]:
    """G-M2: trigger audit nhận danh sách cột sensitive qua TG_ARGV => audit chỉ lưu HMAC, không lưu giá trị."""
    t = table(m)
    args = sql.SQL(", ").join(sql.Literal(f.name) for f in m.fields if f.pii == "sensitive")
    out = []
    if replace:
        out.append(Step(sql.SQL("DROP TRIGGER IF EXISTS xt_audit ON {}").format(t), order=85))
    out.append(Step(sql.SQL("CREATE TRIGGER xt_audit AFTER INSERT OR UPDATE OR DELETE ON {} FOR EACH ROW EXECUTE FUNCTION xt_audit.log({})").format(t, args), order=86 if replace else 20))
    return out


def grant_steps(m: ModelSpec) -> list[Step]:
    """G-M1: xt_app chỉ có INSERT/UPDATE trên cột không do workflow quản; xt_workflow có UPDATE cả bảng."""
    t = table(m)
    free = [f.name for f in m.fields if f.managed_by is None]
    ins = sql.SQL(", ").join(sql.Identifier(c) for c in INSERT_SYS + free)
    upd = sql.SQL(", ").join(sql.Identifier(c) for c in ["owner_id", "team_id", "department_id"] + free)
    return [Step(sql.SQL("REVOKE ALL ON {} FROM {}").format(t, sql.Identifier(APP_ROLE)), order=21),
            Step(sql.SQL("GRANT SELECT, DELETE ON {} TO {}").format(t, sql.Identifier(APP_ROLE)), order=21),
            Step(sql.SQL("GRANT INSERT ({}) ON {} TO {}").format(ins, t, sql.Identifier(APP_ROLE)), order=21),
            Step(sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(upd, t, sql.Identifier(APP_ROLE)), order=21),
            Step(sql.SQL("GRANT SELECT, UPDATE ON {} TO {}").format(t, sql.Identifier(WF_ROLE)), order=21)]


def policy_steps(m: ModelSpec, drop_existing: bool = False) -> list[Step]:
    t = table(m)
    steps: list[Step] = []
    names = [f"p_{m.id}_tenant"] + [f"p_{m.id}_{a}" for a in ACTIONS]
    if drop_existing:
        steps += [Step(sql.SQL("DROP POLICY IF EXISTS {} ON {}").format(sql.Identifier(n), t), order=80) for n in names]
    steps.append(Step(sql.SQL(
        "CREATE POLICY {} ON {} AS RESTRICTIVE FOR ALL TO {} USING (tenant_id = xt.tenant()) WITH CHECK (tenant_id = xt.tenant())"
    ).format(sql.Identifier(names[0]), t, _roles()), order=81))
    for action, verb in ACTIONS.items():
        clauses = [sql.SQL("(xt.has_role({}) AND {})").format(sql.Literal(role), sql.SQL(SCOPE_SQL[p.scope]))
                   for role, p in sorted(m.permissions.items()) if action in p.actions]
        if not clauses:
            continue  # không role nào được => không policy => từ chối mặc định
        cond = sql.SQL(" OR ").join(clauses)
        using = sql.SQL(" USING ({})").format(cond) if verb != "INSERT" else sql.SQL("")
        check = sql.SQL(" WITH CHECK ({})").format(cond) if verb in ("INSERT", "UPDATE") else sql.SQL("")
        steps.append(Step(sql.SQL("CREATE POLICY {} ON {} AS PERMISSIVE FOR {} TO {}{}{}").format(
            sql.Identifier(f"p_{m.id}_{action}"), t, sql.SQL(verb), _roles(), using, check), order=82))
    return steps


def create_model_steps(m: ModelSpec) -> list[Step]:
    t = table(m)
    cols = [
        sql.SQL("id uuid NOT NULL DEFAULT gen_random_uuid()"),
        sql.SQL("tenant_id uuid NOT NULL DEFAULT xt.tenant()"),
        sql.SQL("owner_id uuid DEFAULT xt.actor()"),
        sql.SQL("team_id uuid"),
        sql.SQL("department_id uuid"),
        sql.SQL("created_at timestamptz NOT NULL DEFAULT clock_timestamp()"),
        sql.SQL("updated_at timestamptz NOT NULL DEFAULT clock_timestamp()"),
        sql.SQL("created_by uuid"),
        sql.SQL("updated_by uuid"),
        sql.SQL("revision integer NOT NULL DEFAULT 1"),
    ] + [column_def(f) for f in m.fields]
    cons = [sql.SQL("CONSTRAINT {} PRIMARY KEY (id)").format(sql.Identifier(iname(f"pk_{m.id}"))),
            sql.SQL("CONSTRAINT {} UNIQUE (tenant_id, id)").format(sql.Identifier(iname(f"uq_{m.id}_tid")))]
    cons += [sql.SQL("CONSTRAINT {} CHECK ({})").format(sql.Identifier(c.name), c.expr) for c in all_checks(m)]
    steps = [Step(sql.SQL("CREATE TABLE {} (\n  {}\n)").format(t, sql.SQL(",\n  ").join(cols + cons)), order=10)]
    steps += [Step(sql.SQL("CREATE INDEX {} ON {} (tenant_id, owner_id)").format(sql.Identifier(iname(f"ix_{m.id}_owner")), t), order=70),
              Step(sql.SQL("CREATE INDEX {} ON {} (tenant_id, created_at DESC)").format(sql.Identifier(iname(f"ix_{m.id}_created")), t), order=70),
              Step(sql.SQL("CREATE INDEX {} ON {} (tenant_id, department_id)").format(sql.Identifier(iname(f"ix_{m.id}_dept")), t), order=70),
              Step(sql.SQL("CREATE INDEX {} ON {} (tenant_id, team_id)").format(sql.Identifier(iname(f"ix_{m.id}_team")), t), order=70)]
    steps += [unique_index(m, f, False) for f in m.fields if f.unique]
    steps += [index_step(m, ix, False) for ix in m.indexes]
    steps += [fk_step(m, f) for f in m.fields if f.type == "ref"]
    # FK tới đích thì đích phải có index (tenant_id, id): đã có uq_<id>_tid. Index phía nguồn:
    steps += [Step(sql.SQL("CREATE INDEX {} ON {} (tenant_id, {})").format(sql.Identifier(iname(cname(m, f, "ixr"))), t, sql.Identifier(f.name)), order=70)
              for f in m.fields if f.type == "ref"]
    steps += [
        Step(sql.SQL("CREATE TRIGGER xt_touch BEFORE INSERT OR UPDATE ON {} FOR EACH ROW EXECUTE FUNCTION xt.row_touch()").format(t), order=20),
        Step(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(t), order=20),
        Step(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(t), order=20),
    ]
    steps += audit_trigger(m) + grant_steps(m)
    steps += policy_steps(m)
    steps += comment_steps(m)
    return steps


def compile_catalog(cat: Catalog) -> list[Step]:
    steps = [s for m in cat.models for s in create_model_steps(m)]
    return sorted(steps, key=lambda s: s.order)


def constraint_map(cat: Catalog) -> dict[str, tuple[str, str, list[str], str]]:
    """conname -> (model, kind, [fields], message_vi). Dùng để dịch lỗi DB."""
    out = {}
    for m in cat.models:
        for c in all_checks(m):
            out[c.name] = (m.name, c.kind, c.fields, c.message)
        for f in m.fields:
            if f.unique:
                out[iname(cname(m, f, "ux"))] = (m.name, "unique", [f.name], f"'{f.label}' đã tồn tại, phải là duy nhất")
            if f.type == "ref":
                out[cname(m, f, "fk")] = (m.name, "fk", [f.name], f"'{f.label}' tham chiếu tới bản ghi không tồn tại hoặc không thuộc đơn vị của bạn")
        for ix in m.indexes:
            if ix.unique:
                labels = ", ".join(m.field(x).label for x in ix.fields)
                out[iname(f"ix_{m.id}_{ix.id}")] = (m.name, "unique", list(ix.fields), f"Tổ hợp ({labels}) đã tồn tại")
        out[iname(f"pk_{m.id}")] = (m.name, "unique", ["id"], "id bản ghi đã tồn tại")
    return out
