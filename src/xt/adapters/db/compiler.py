# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Compiler Postgres: ModelSpec / kế hoạch thay đổi → DDL (`psycopg.sql.Composed`).

Bố trí B2 (D6): bảng `xt_t<seq>.<model>` + `tenant_id` + RLS FORCE; view `api_t<seq>.<model>`
`security_invoker=false`, chủ `xt_api_owner`, không chứa trường `sensitive`.
Mọi định danh qua `sql.Identifier` (luôn quote), mọi giá trị qua `sql.Literal`.
Tên constraint/index/policy lấy từ id ổn định (`xt.domain.model.checks`).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from psycopg import sql

from xt.domain.model.checks import (
    CheckDef,
    all_checks,
    checks_by_name,
    field_checks,
    fk_name,
    index_name,
    notnull_check_name,
    pk_name,
    ref_index_name,
    tid_unique_name,
    unique_name,
)
from xt.domain.model.context import api_schema, data_schema
from xt.domain.model.expr import And, Cmp, Expr, Not, Or, coerce_literal
from xt.domain.model.plan import Change, Plan
from xt.domain.model.spec import Catalog, FieldSpec, IndexSpec, ModelSpec

APP_ROLE = "xt_app"
WF_ROLE = "xt_workflow"
API_OWNER = "xt_api_owner"
AUDIT_OWNER = "xt_audit_owner"
WEB_ROLE = "xt_web"
PG_TYPE = {
    "text": "text", "number": "numeric", "money": "bigint", "date": "date",
    "datetime": "timestamptz", "bool": "boolean", "enum": "text", "ref": "uuid",
    "user": "uuid", "file": "uuid", "json": "jsonb",
}  # fmt: skip
ACTIONS = {"read": "SELECT", "create": "INSERT", "update": "UPDATE", "delete": "DELETE"}
SCOPE_SQL = {
    "all": "true",
    "own": "owner_id = xt.actor()",
    "team": "(owner_id = xt.actor() OR xt.in_teams(team_id))",
    "department": "(owner_id = xt.actor() OR xt.in_departments(department_id))",
}
SYSTEM_COLS = (
    "id", "tenant_id", "owner_id", "team_id", "department_id", "created_at", "updated_at",
    "created_by", "updated_by", "revision",
)  # fmt: skip
INSERT_SYS = ("id", "owner_id", "team_id", "department_id")
UPDATE_SYS = ("owner_id", "team_id", "department_id")
Phase = Literal["tx", "backfill", "post", "concurrent"]
BACKFILL_BATCH = 1000


@dataclass(frozen=True)
class Layout:
    tenant_seq: int

    @property
    def schema(self) -> str:
        return data_schema(self.tenant_seq)

    @property
    def api(self) -> str:
        return api_schema(self.tenant_seq)

    def table(self, name: str) -> sql.Identifier:
        return sql.Identifier(self.schema, name)

    def view(self, name: str) -> sql.Identifier:
        return sql.Identifier(self.api, name)


Query = sql.Composed | sql.SQL


@dataclass(frozen=True)
class Step:
    sql: Query
    phase: Phase = "tx"
    order: int = 50  # bảng < cột < ràng buộc < FK < index < policy < view
    role: str | None = None  # chạy dưới SET ROLE (vd chủ view)
    index: tuple[str, str] | None = None  # (schema, index) cho bước CONCURRENTLY
    data: bool = False  # bước sửa dữ liệu: không bao giờ chạy khi diễn tập (AUD-02)
    # backfill ghi field sensitive: (cột, giá trị) để worker cấp HMAC (khóa không vào DB)
    audit_new: tuple[tuple[str, object], ...] = ()

    def text(self) -> str:
        return self.sql.as_string()


Resolver = Callable[[FieldSpec], sql.Composable]


def _col(f: FieldSpec) -> sql.Composable:
    return sql.Identifier(f.name)


def lit(f: FieldSpec, v: object) -> sql.Composable:
    return sql.Literal(coerce_literal(f, v))


# ------------------------------------------------------------------------ DSL → SQL ----
def compile_expr(
    e: Expr,
    by_name: dict[str, FieldSpec],
    literal: Callable[[FieldSpec, object], sql.Composable] = lit,
    col: Resolver = _col,
) -> sql.Composable:
    if isinstance(e, And):
        parts = [compile_expr(k, by_name, literal, col) for k in e.and_]
        return sql.SQL("({})").format(sql.SQL(" AND ").join(parts))
    if isinstance(e, Or):
        parts = [compile_expr(k, by_name, literal, col) for k in e.or_]
        return sql.SQL("({})").format(sql.SQL(" OR ").join(parts))
    if isinstance(e, Not):
        return sql.SQL("(NOT {})").format(compile_expr(e.not_, by_name, literal, col))
    assert isinstance(e, Cmp)
    f = by_name[e.field]
    c = col(f)
    if e.op == "is_null":
        return sql.SQL("({} IS NULL)").format(c)
    if e.op == "not_null":
        return sql.SQL("({} IS NOT NULL)").format(c)
    if e.op == "in":
        assert isinstance(e.value, list)
        vals = sql.SQL(", ").join(literal(f, v) for v in e.value)
        return sql.SQL("({} IN ({}))").format(c, vals)
    if e.op in ("contains", "startswith"):
        raw = str(e.value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pat = f"%{raw}%" if e.op == "contains" else f"{raw}%"
        # giá trị đi qua `literal` (placeholder ở truy vấn): '%' không lẫn với placeholder psycopg
        return sql.SQL("({} ILIKE {} ESCAPE '\\')").format(c, literal(f, pat))
    op = sql.SQL("<>" if e.op == "!=" else e.op)
    rhs = col(by_name[e.other_field]) if e.other_field else literal(f, e.value)
    return sql.SQL("({} {} {})").format(c, op, rhs)


def check_expr(m: ModelSpec, c: CheckDef, col: Resolver = _col) -> sql.Composable:
    if c.kind == "rule":
        assert c.expr is not None
        return compile_expr(c.expr, m.by_name(), lit, col)
    assert c.field_id is not None
    f = m.by_id()[c.field_id]
    cc = col(f)
    if f.type == "text" and c.kind in ("min", "max"):
        op = ">=" if c.kind == "min" else "<="
        return sql.SQL("char_length({}) {} {}").format(cc, sql.SQL(op), sql.Literal(c.value))
    if c.kind == "min":
        return sql.SQL("{} >= {}").format(cc, sql.Literal(c.value))
    if c.kind == "max":
        return sql.SQL("{} <= {}").format(cc, sql.Literal(c.value))
    if c.kind == "re":
        return sql.SQL("{} ~ {}").format(cc, sql.Literal(c.value))
    if c.kind == "enum":
        assert isinstance(c.value, tuple)
        return sql.SQL("{} IN ({})").format(cc, sql.SQL(", ").join(map(sql.Literal, c.value)))
    return sql.SQL("octet_length({}::text) <= {}").format(cc, sql.Literal(c.value))


def column_def(f: FieldSpec) -> sql.Composable:
    parts: list[sql.Composable] = [sql.Identifier(f.name), sql.SQL(PG_TYPE[f.type])]
    if f.default is not None:
        parts += [sql.SQL("DEFAULT"), lit(f, f.default)]
    if f.required:
        parts.append(sql.SQL("NOT NULL"))
    return sql.SQL(" ").join(parts)


# -------------------------------------------------------------------- khối DDL nhỏ ----
def _alter(lay: Layout, m: ModelSpec, body: sql.Composable) -> sql.Composed:
    return sql.SQL("ALTER TABLE {} {}").format(lay.table(m.name), body)


FILE_TABLE = sql.SQL("xt_meta.file_object")
FK_TYPES = ("ref", "file")


def fk_sql(lay: Layout, m: ModelSpec, f: FieldSpec) -> sql.Composed:
    # FK ghép (tenant_id, col) → đích(tenant_id, id): chặn tham chiếu chéo tenant ở mức DB.
    # Field `file` trỏ vào metadata tệp của nền tảng (migration 0009_files.sql).
    if f.type == "file":
        target: sql.Composable = FILE_TABLE
    else:
        assert f.ref is not None
        target = lay.table(f.ref)
    return _alter(
        lay,
        m,
        sql.SQL("ADD CONSTRAINT {} FOREIGN KEY (tenant_id, {}) REFERENCES {} (tenant_id, id)"
                " ON DELETE RESTRICT").format(
            sql.Identifier(fk_name(m, f)), sql.Identifier(f.name), target
        ),
    )  # fmt: skip


def unique_index(lay: Layout, m: ModelSpec, f: FieldSpec, concurrently: bool) -> Step:
    name = unique_name(m, f)
    q = sql.SQL("CREATE UNIQUE INDEX {}{} ON {} (tenant_id, {})").format(
        sql.SQL("CONCURRENTLY IF NOT EXISTS ") if concurrently else sql.SQL(""),
        sql.Identifier(name),
        lay.table(m.name),
        sql.Identifier(f.name),
    )
    if concurrently:
        return Step(q, "concurrent", 70, index=(lay.schema, name))
    return Step(q, "tx", 70)


def index_step(lay: Layout, m: ModelSpec, ix: IndexSpec, concurrently: bool) -> Step:
    name = index_name(m, ix.id)
    q = sql.SQL("CREATE {}INDEX {}{} ON {} (tenant_id, {})").format(
        sql.SQL("UNIQUE ") if ix.unique else sql.SQL(""),
        sql.SQL("CONCURRENTLY IF NOT EXISTS ") if concurrently else sql.SQL(""),
        sql.Identifier(name),
        lay.table(m.name),
        sql.SQL(", ").join(sql.Identifier(x) for x in ix.fields),
    )
    if concurrently:
        return Step(q, "concurrent", 70, index=(lay.schema, name))
    return Step(q, "tx", 70)


def drop_index_step(lay: Layout, name: str) -> Step:
    q = sql.SQL("DROP INDEX CONCURRENTLY IF EXISTS {}").format(sql.Identifier(lay.schema, name))
    return Step(q, "concurrent", 69)


def ref_index(lay: Layout, m: ModelSpec, f: FieldSpec, concurrently: bool) -> Step:
    name = ref_index_name(m, f)
    q = sql.SQL("CREATE INDEX {}{} ON {} (tenant_id, {})").format(
        sql.SQL("CONCURRENTLY IF NOT EXISTS ") if concurrently else sql.SQL(""),
        sql.Identifier(name),
        lay.table(m.name),
        sql.Identifier(f.name),
    )
    if concurrently:
        return Step(q, "concurrent", 70, index=(lay.schema, name))
    return Step(q, "tx", 70)


def _roles(*extra: str) -> sql.Composable:
    return sql.SQL(", ").join(sql.Identifier(r) for r in (APP_ROLE, WF_ROLE, *extra))


def audit_trigger_steps(lay: Layout, m: ModelSpec, replace: bool) -> list[Step]:
    """G-M2: trigger audit do xt_audit_owner sở hữu, cài qua `xt_audit.install` (CREATE OR REPLACE)
    với danh sách cột sensitive theo catalog hiện hành → audit chỉ lưu HMAC do app cấp.
    xt_migrator không tự tạo/sửa/tắt được trigger audit (event trigger chặn, R2-04)."""
    cols = [f.name for f in m.fields if f.pii == "sensitive"]
    q = sql.SQL("SELECT xt_audit.install({}, {}, {}::text[])").format(
        sql.Literal(lay.schema), sql.Literal(m.name), sql.Literal(cols)
    )
    return [Step(q, order=86 if replace else 23)]


def grant_steps(lay: Layout, m: ModelSpec, order: int = 21) -> list[Step]:
    """G-M1: xt_app chỉ INSERT/UPDATE cột tự do; xt_workflow UPDATE cả bảng; chủ view SELECT."""
    t = lay.table(m.name)
    free = [f.name for f in m.fields if f.managed_by is None]
    ins = sql.SQL(", ").join(sql.Identifier(c) for c in (*INSERT_SYS, *free))
    upd = sql.SQL(", ").join(sql.Identifier(c) for c in (*UPDATE_SYS, *free))
    app, wf, api = (sql.Identifier(r) for r in (APP_ROLE, WF_ROLE, API_OWNER))
    audit = sql.Identifier(AUDIT_OWNER)
    return [
        Step(sql.SQL("REVOKE ALL ON {} FROM {}, {}, {}").format(t, app, wf, api), order=order),
        Step(sql.SQL("GRANT SELECT, DELETE ON {} TO {}").format(t, app), order=order),
        Step(sql.SQL("GRANT INSERT ({}) ON {} TO {}").format(ins, t, app), order=order),
        Step(sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(upd, t, app), order=order),
        Step(sql.SQL("GRANT SELECT, UPDATE ON {} TO {}").format(t, wf), order=order),
        Step(sql.SQL("GRANT SELECT ON {} TO {}").format(t, api), order=order),
        Step(sql.SQL("GRANT TRIGGER ON {} TO {}").format(t, audit), order=order),
    ]


def policy_steps(lay: Layout, m: ModelSpec, drop_existing: bool = False) -> list[Step]:
    """Policy RLS của bảng model đi qua `xt_audit.set_policies` (superuser, mẫu cố định): luôn có
    policy RESTRICTIVE theo tenant + PERMISSIVE theo role/phạm vi của catalog; migrator không tự
    viết biểu thức policy được. `drop_existing` giữ cho tương thích (hàm luôn thay bộ policy)."""
    del drop_existing
    perms = {
        role: {"actions": list(p.actions), "scope": p.scope}
        for role, p in sorted(m.permissions.items())
    }
    q = sql.SQL("SELECT xt_audit.set_policies({}, {}, {}, {}::jsonb)").format(
        sql.Literal(lay.schema),
        sql.Literal(m.name),
        sql.Literal(m.id),
        sql.Literal(json.dumps(perms, sort_keys=True)),
    )
    return [Step(q, order=81)]


def comment_steps(lay: Layout, m: ModelSpec) -> list[Step]:
    t = lay.table(m.name)
    out = [Step(sql.SQL("COMMENT ON TABLE {} IS {}").format(t, sql.Literal(m.label)), order=90)]
    for f in m.fields:
        out.append(
            Step(
                sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                    t, sql.Identifier(f.name), sql.Literal(f.label)
                ),
                order=90,
            )
        )
    return out


def drop_view_step(lay: Layout, name: str) -> Step:
    q = sql.SQL("DROP VIEW IF EXISTS {}").format(lay.view(name))
    return Step(q, order=1, role=API_OWNER)


def view_steps(lay: Layout, m: ModelSpec, readers: Sequence[str] = ()) -> list[Step]:
    """View PostgREST/BI: security_invoker=false, chủ xt_api_owner, không có field sensitive."""
    cols = [*SYSTEM_COLS, *(f.name for f in m.fields if f.pii != "sensitive")]
    q = sql.SQL(
        "CREATE VIEW {} WITH (security_invoker = false) AS SELECT {} FROM {}"
        " WHERE session_user <> 'xt_authenticator' OR coalesce(NOT ("
        "(nullif(current_setting('xt.pgrst_ctx', true), '')::jsonb -> 'disabled_models')"
        " ? {}), false)"
    ).format(
        lay.view(m.name),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        lay.table(m.name),
        sql.Literal(m.name),
    )
    grantees = sql.SQL(", ").join(sql.Identifier(r) for r in (WEB_ROLE, *readers))
    return [
        Step(q, order=95, role=API_OWNER),
        Step(
            sql.SQL("GRANT SELECT ON {} TO {}").format(lay.view(m.name), grantees),
            order=96,
            role=API_OWNER,
        ),
    ]


def create_model_steps(lay: Layout, m: ModelSpec) -> list[Step]:
    t = lay.table(m.name)
    cols: list[sql.Composable] = [
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
        *(column_def(f) for f in m.fields),
        sql.SQL("CONSTRAINT {} PRIMARY KEY (id)").format(sql.Identifier(pk_name(m))),
        sql.SQL("CONSTRAINT {} UNIQUE (tenant_id, id)").format(sql.Identifier(tid_unique_name(m))),
        *(
            sql.SQL("CONSTRAINT {} CHECK ({})").format(sql.Identifier(c.name), check_expr(m, c))
            for c in all_checks(m)
        ),
    ]
    steps = [
        Step(sql.SQL("CREATE TABLE {} (\n  {}\n)").format(t, sql.SQL(",\n  ").join(cols)), order=10)
    ]
    for suffix, col in (("owner", "owner_id"), ("dept", "department_id"), ("team", "team_id")):
        steps.append(
            Step(
                sql.SQL("CREATE INDEX {} ON {} (tenant_id, {})").format(
                    sql.Identifier(index_name(m, suffix)), t, sql.Identifier(col)
                ),
                order=70,
            )
        )
    steps.append(
        Step(
            sql.SQL("CREATE INDEX {} ON {} (tenant_id, created_at DESC)").format(
                sql.Identifier(index_name(m, "created")), t
            ),
            order=70,
        )
    )
    steps += [unique_index(lay, m, f, False) for f in m.fields if f.unique]
    steps += [index_step(lay, m, ix, False) for ix in m.indexes]
    steps += [Step(fk_sql(lay, m, f), order=60) for f in m.fields if f.type in FK_TYPES]
    steps += [ref_index(lay, m, f, False) for f in m.fields if f.type in FK_TYPES]
    steps += [
        Step(
            sql.SQL(
                "CREATE TRIGGER xt_touch BEFORE INSERT OR UPDATE ON {} FOR EACH ROW"
                " EXECUTE FUNCTION xt.row_touch()"
            ).format(t),
            order=20,
        ),
        Step(sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(t), order=20),
        Step(sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(t), order=20),
    ]
    steps += audit_trigger_steps(lay, m, replace=False)
    steps += grant_steps(lay, m)
    steps += policy_steps(lay, m)
    steps += comment_steps(lay, m)
    return steps


def compile_catalog(lay: Layout, cat: Catalog) -> list[Step]:
    """DDL đầy đủ của một catalog trên schema trống (dùng cho test/so sánh)."""
    steps = [s for m in cat.models for s in create_model_steps(lay, m)]
    steps += [s for m in cat.models for s in view_steps(lay, m)]
    return sorted(steps, key=lambda s: s.order)


# ------------------------------------------------------------- kế hoạch → các bước ----
def _check_add(lay: Layout, m: ModelSpec, c: CheckDef) -> list[Step]:
    name = sql.Identifier(c.name)
    return [
        Step(
            _alter(
                lay,
                m,
                sql.SQL("ADD CONSTRAINT {} CHECK ({}) NOT VALID").format(name, check_expr(m, c)),
            ),
            order=40,
        ),
        Step(_alter(lay, m, sql.SQL("VALIDATE CONSTRAINT {}").format(name)), "post", 40),
    ]


def _add_field_steps(lay: Layout, n: ModelSpec, f: FieldSpec) -> list[Step]:
    steps = [Step(_alter(lay, n, sql.SQL("ADD COLUMN {}").format(column_def(f))), order=30)]
    for c in field_checks(n, f):
        steps += _check_add(lay, n, c)
    if f.unique:
        steps.append(unique_index(lay, n, f, True))
    if f.type in FK_TYPES:
        steps += [
            Step(sql.SQL("{} NOT VALID").format(fk_sql(lay, n, f)), order=60),
            Step(
                _alter(
                    lay, n, sql.SQL("VALIDATE CONSTRAINT {}").format(sql.Identifier(fk_name(n, f)))
                ),
                "post",
                60,
            ),
            ref_index(lay, n, f, True),
        ]
    steps.append(
        Step(
            sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                lay.table(n.name), sql.Identifier(f.name), sql.Literal(f.label)
            ),
            order=90,
        )
    )
    return steps


def change_steps(lay: Layout, c: Change, old: Catalog | None, new: Catalog) -> list[Step]:
    om = old.by_id() if old else {}
    nm = new.by_id()
    k = c.kind
    if k == "add_model":
        return create_model_steps(lay, nm[c.model_id])
    if k == "drop_model":
        return [
            Step(sql.SQL("DROP TABLE {} RESTRICT").format(lay.table(om[c.model_id].name)), order=3)
        ]
    o, n = om[c.model_id], nm[c.model_id]
    t = lay.table(n.name)
    if k == "rename_model":
        q = sql.SQL("ALTER TABLE {} RENAME TO {}").format(lay.table(o.name), sql.Identifier(n.name))
        return [Step(q, order=5)]
    if k == "meta_model":
        return [
            Step(sql.SQL("COMMENT ON TABLE {} IS {}").format(t, sql.Literal(n.label)), order=90)
        ]
    if k == "pii":
        return []  # trigger audit được sinh lại cho MỌI model bị chạm (compile_plan, AUD-01)
    if k == "grants":
        return grant_steps(lay, n, order=87)
    if k == "permission":
        return policy_steps(lay, n, drop_existing=True)
    if k in ("drop_check", "check"):
        name = str(c.details["check"])
        if c.details.get("message_only"):
            return []
        drop = Step(
            _alter(lay, n, sql.SQL("DROP CONSTRAINT IF EXISTS {}").format(sql.Identifier(name))),
            order=35,
        )
        if k == "drop_check":
            return [drop]
        steps = [drop] if c.details.get("replace") else []
        return steps + _check_add(lay, n, checks_by_name(n)[name])
    if k == "index":
        iid = str(c.details["index"])
        steps = []
        if c.details.get("drop"):
            steps.append(drop_index_step(lay, index_name(n, iid)))
        if c.details.get("create"):
            ix = next(x for x in n.indexes if x.id == iid)
            steps.append(index_step(lay, n, ix, True))
        return steps
    assert c.field_id is not None
    if k == "drop_field":
        f_old = o.by_id()[c.field_id]
        return [
            Step(
                _alter(lay, n, sql.SQL("DROP COLUMN {}").format(sql.Identifier(f_old.name))),
                order=22,
            )
        ]
    if k == "add_field":
        return _add_field_steps(lay, n, n.by_id()[c.field_id])
    if k == "retype_field":
        return []
    a, b = o.by_id()[c.field_id], n.by_id()[c.field_id]
    col = sql.Identifier(b.name)
    if k == "rename_field":
        q = sql.SQL("RENAME COLUMN {} TO {}").format(sql.Identifier(a.name), col)
        return [Step(_alter(lay, n, q), order=25)]
    if k == "label_field":
        return [
            Step(
                sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(t, col, sql.Literal(b.label)),
                order=90,
            )
        ]
    if k == "default_field":
        body = (
            sql.SQL("ALTER COLUMN {} SET DEFAULT {}").format(col, lit(b, b.default))
            if b.default is not None
            else sql.SQL("ALTER COLUMN {} DROP DEFAULT").format(col)
        )
        return [Step(_alter(lay, n, body), order=32)]
    if k == "nullable_field":
        return [
            Step(_alter(lay, n, sql.SQL("ALTER COLUMN {} DROP NOT NULL").format(col)), order=35)
        ]
    if k == "require_field":
        ck = sql.Identifier(notnull_check_name(n, b))
        steps = []
        if b.default is not None:
            # backfill theo lô ở pha 2 (worker), không giữ khóa bảng ở pha 1 (AUD-02)
            steps.append(
                Step(
                    sql.SQL(
                        "UPDATE {t} SET {c} = {v} WHERE id IN"
                        " (SELECT id FROM {t} WHERE {c} IS NULL LIMIT {n})"
                    ).format(t=t, c=col, v=lit(b, b.default), n=sql.Literal(BACKFILL_BATCH)),
                    "backfill",
                    34,
                    data=True,
                    audit_new=((b.name, b.coerced("default")),) if b.pii == "sensitive" else (),
                )
            )
        nn = sql.SQL("ADD CONSTRAINT {} CHECK ({} IS NOT NULL) NOT VALID").format(ck, col)
        return [
            *steps,
            Step(_alter(lay, n, nn), "post", 39),
            Step(_alter(lay, n, sql.SQL("VALIDATE CONSTRAINT {}").format(ck)), "post", 40),
            Step(_alter(lay, n, sql.SQL("ALTER COLUMN {} SET NOT NULL").format(col)), "post", 41),
            Step(_alter(lay, n, sql.SQL("DROP CONSTRAINT {}").format(ck)), "post", 42),
        ]
    if k == "unique_field":
        return [unique_index(lay, n, b, True)]
    if k == "ununique_field":
        return [drop_index_step(lay, unique_name(n, b))]
    raise AssertionError(f"kind chưa hỗ trợ: {k}")


@dataclass(frozen=True)
class CompiledStep:
    change_id: str
    step: Step


def compile_plan(
    lay: Layout,
    plan: Plan,
    old: Catalog | None,
    new: Catalog,
    readers: Sequence[str] = (),
) -> list[CompiledStep]:
    """Mọi bước của kế hoạch, sắp theo (order, thứ tự xuất hiện). View của model bị chạm được
    drop sớm (order 1) và tạo lại muộn (order 95) để DROP/RENAME COLUMN không vướng phụ thuộc."""
    out: list[CompiledStep] = []
    om = old.by_id() if old else {}
    touched: set[str] = set()
    for c in plan.changes:
        if c.cls.value == "BLOCKED":
            continue
        out += [CompiledStep(c.id, s) for s in change_steps(lay, c, old, new)]
        touched.add(c.model_id)
    nm = new.by_id()
    for mid in sorted(touched):
        # G-M2: trigger audit luôn được sinh lại từ catalog MỚI trong cùng migration, nên danh
        # sách cột sensitive (theo tên hiện hành) không bao giờ lệch sau rename (AUD-01).
        if mid in om and mid in nm:
            out += [
                CompiledStep(f"audit:{mid}", s) for s in audit_trigger_steps(lay, nm[mid], True)
            ]
        vid = f"view:{mid}"
        if mid in om:
            out.append(CompiledStep(vid, drop_view_step(lay, om[mid].name)))
        n = new.by_id().get(mid)
        if n is not None:
            out += [CompiledStep(vid, s) for s in view_steps(lay, n, readers)]
    indexed = list(enumerate(out))
    indexed.sort(key=lambda p: (p[1].step.order, p[0]))
    return [cs for _, cs in indexed]
