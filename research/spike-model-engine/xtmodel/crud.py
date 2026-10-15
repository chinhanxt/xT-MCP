# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 xT contributors
"""CRUD tổng quát — lớp mà MCP tool create_record/update_record/search_records gọi.

- Payload được validate bằng model pydantic sinh lúc chạy từ spec (extra=forbid).
- Lỗi pydantic và lỗi ràng buộc DB được dịch sang tiếng Việt, kèm tên trường/nhãn,
  gợi ý sửa (poka-yoke) để agent chuyển lại cho người dùng.
- Mỗi lời gọi = 1 transaction ngắn, ngữ cảnh RLS đặt bằng set_config(..., true).
"""

from __future__ import annotations

import difflib
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache
from typing import Any, Literal, Optional
from uuid import UUID

import psycopg
from psycopg import errors as E, sql
from psycopg.rows import dict_row
from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    TypeAdapter,
    ValidationError,
    create_model,
)
from typing_extensions import Annotated

from . import compiler as C
from .spec import (
    IDENT_RE,
    And,
    Catalog,
    Cmp,
    Expr,
    FieldSpec,
    ModelSpec,
    Not,
    Or,
    check_expr_types,
    coerce_value,
    expr_depth_and_fields,
)

MAX_LIMIT, MAX_OFFSET = 200, 10_000
SYSTEM_OUT = ["id", "revision", "owner_id", "team_id", "department_id", "created_at", "updated_at"]


@dataclass(frozen=True)
class Actor:
    """Lấy từ token Keycloak đã xác thực — KHÔNG từ tham số tool do agent truyền."""

    tenant_id: UUID
    actor_id: UUID
    roles: tuple[str, ...]
    team_ids: tuple[UUID, ...] = ()
    department_ids: tuple[UUID, ...] = ()

    def __post_init__(self):
        for r in self.roles:  # chặn chèn ',' vào danh sách role của GUC
            if not IDENT_RE.fullmatch(r):
                raise ValueError(f"role không hợp lệ: {r!r}")


class ToolError(Exception):
    def __init__(
        self, code: str, message: str, fields: list[dict] | None = None, hint: str | None = None
    ):
        super().__init__(message)
        self.payload = {
            "ok": False,
            "error": {
                "code": code,
                "message": message,
                "fields": fields or [],
                **({"hint": hint} if hint else {}),
            },
        }


# --------------------------------------------------------- pydantic động ----
def _py_type(f: FieldSpec):
    t = f.type
    if t == "text":
        # pattern dùng engine Rust của pydantic-core (tuyến tính), ngữ nghĩa 'search' như toán tử ~ của PG
        return Annotated[
            str,
            Field(
                min_length=int(f.min) if f.min is not None else None,
                max_length=int(f.max) if f.max is not None else None,
                pattern=f.regex,
            ),
        ]
    if t == "number":
        return Annotated[Decimal, Field(ge=f.min, le=f.max, allow_inf_nan=False)]
    if t == "money":
        return Annotated[int, Field(ge=f.min, le=f.max)]
    if t == "date":
        return Annotated[date, Field(ge=f.min, le=f.max)]
    if t == "datetime":
        return Annotated[AwareDatetime, Field(ge=f.min, le=f.max)]
    if t == "bool":
        return StrictBool
    if t == "enum":
        return Literal[tuple(o.value for o in f.options)]
    if t in ("ref", "user", "file"):
        return UUID
    if t == "json":
        return dict[str, Any] | list[Any]
    raise AssertionError(t)


def build_payload_model(m: ModelSpec, mode: str) -> type[BaseModel]:
    defs: dict[str, Any] = {}
    for f in m.fields:
        T = _py_type(f)
        if mode == "create" and f.required and f.default is None:
            defs[f.name] = (T, ...)
        elif mode == "create" or not f.required:
            defs[f.name] = (Optional[T], None)
        else:  # update: trường bắt buộc có thể bỏ qua nhưng không được gán null
            defs[f.name] = (T, None)
    for sysf in ("owner_id", "team_id", "department_id"):
        defs[sysf] = (Optional[UUID], None)
    return create_model(
        f"{m.name}_{mode}", __config__=ConfigDict(extra="forbid", str_strip_whitespace=True), **defs
    )


PYD_VI = {
    "missing": "là trường bắt buộc, chưa có giá trị",
    "extra_forbidden": "không tồn tại trong model",
    "string_too_short": "quá ngắn (tối thiểu {min_length} ký tự)",
    "string_too_long": "quá dài (tối đa {max_length} ký tự)",
    "greater_than_equal": "phải >= {ge}",
    "less_than_equal": "phải <= {le}",
    "literal_error": "chỉ nhận một trong: {expected}",
    "int_parsing": "phải là số nguyên",
    "int_type": "phải là số nguyên",
    "decimal_type": "phải là số",
    "dict_type": "phải là object JSON",
    "int_from_float": "phải là số nguyên (VND không có phần lẻ)",
    "decimal_parsing": "phải là số",
    "date_from_datetime_parsing": "phải là ngày dạng YYYY-MM-DD",
    "date_parsing": "phải là ngày dạng YYYY-MM-DD",
    "datetime_parsing": "phải là thời điểm ISO 8601",
    "timezone_aware": "phải có múi giờ (vd +07:00)",
    "bool_type": "phải là true/false",
    "uuid_parsing": "phải là UUID của bản ghi",
    "uuid_type": "phải là UUID của bản ghi",
    "string_type": "phải là chuỗi",
    "value_error": "{error}",
    "string_pattern_mismatch": "không đúng định dạng (mẫu {pattern})",
}


def _vi_pyd(m: ModelSpec, e: ValidationError) -> ToolError:
    names = [f.name for f in m.fields]
    out = []
    for err in e.errors():
        name = str(err["loc"][0]) if err["loc"] else "?"
        ctx = dict(err.get("ctx") or {})
        if "error" in ctx:
            ctx["error"] = str(ctx["error"])
        tmpl = PYD_VI.get(err["type"], "giá trị không hợp lệ ({msg})")
        ctx.setdefault("msg", err["msg"])
        fs = next((f for f in m.fields if f.name == name), None)
        if err["type"] == "literal_error" and fs is not None and fs.options:
            ctx["expected"] = ", ".join(f"{o.value} ({o.label})" for o in fs.options)
        try:
            msg = tmpl.format(**ctx)
        except (KeyError, IndexError):
            msg = err["msg"]
        label = next((f.label for f in m.fields if f.name == name), name)
        if err.get("input", 0) is None and err["type"] != "missing":
            msg = "không được để trống (trường bắt buộc)"
        item = {"field": name, "label": label, "code": err["type"], "message": f"'{label}' {msg}"}
        if err["type"] == "extra_forbidden":
            sug = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
            item["message"] = f"Trường '{name}' không tồn tại trong '{m.label}'" + (
                f" — có phải ý bạn là '{sug[0]}'?" if sug else ""
            )
            item["valid_fields"] = names
        out.append(item)
    return ToolError(
        "VALIDATION_FAILED",
        f"Dữ liệu '{m.label}' chưa hợp lệ ({len(out)} lỗi)",
        out,
        "Sửa các trường nêu trên rồi gọi lại; không đoán giá trị thay người dùng.",
    )


# ---------------------------------------------------- DSL: đánh giá 3VL ----
def eval3(e: Expr, row: dict, m: ModelSpec) -> Optional[bool]:
    """Logic 3 trị giống SQL: CHECK chỉ vi phạm khi kết quả là False."""
    if isinstance(e, And):
        r = [eval3(k, row, m) for k in e.and_]
        return False if False in r else (True if all(x is True for x in r) else None)
    if isinstance(e, Or):
        r = [eval3(k, row, m) for k in e.or_]
        return True if True in r else (False if all(x is False for x in r) else None)
    if isinstance(e, Not):
        r = eval3(e.not_, row, m)
        return None if r is None else not r
    f = m.field(e.field)
    v = row.get(e.field)
    if e.op == "is_null":
        return v is None
    if e.op == "not_null":
        return v is not None
    if v is None:
        return None
    if e.op == "in":
        return v in [coerce_value(f, x) for x in e.value]
    rhs = row.get(e.other_field) if e.other_field else coerce_value(f, e.value)
    if rhs is None:
        return None
    return {
        "=": v == rhs,
        "!=": v != rhs,
        "<": v < rhs,
        "<=": v <= rhs,
        ">": v > rhs,
        ">=": v >= rhs,
    }[e.op]


# --------------------------------------------------------------- engine ----
_SYS_FIELDS = [
    FieldSpec.model_construct(
        id=f"sys{i}",
        name=n,
        type=t,
        label=l,
        required=False,
        unique=False,
        min=None,
        max=None,
        regex=None,
        options=None,
        ref=None,
        default=None,
    )
    for i, (n, t, l) in enumerate(
        [
            ("created_at", "datetime", "Ngày tạo"),
            ("updated_at", "datetime", "Ngày sửa"),
            ("owner_id", "user", "Người sở hữu"),
            ("team_id", "user", "Nhóm"),
            ("department_id", "user", "Phòng ban"),
        ]
    )
]


class Engine:
    def __init__(self, pool, catalog: Catalog):
        self.pool = pool
        self.set_catalog(catalog)

    def set_catalog(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.cmap = C.constraint_map(catalog)
        self._models = {m.name: m for m in catalog.models}
        self._payload = {}
        self._fk_in = {}  # fk name -> (model nguồn, nhãn trường)
        for m in catalog.models:
            for f in m.fields:
                if f.type == "ref":
                    self._fk_in[C.cname(m, f, "fk")] = (m, f)

    def model(self, name: str) -> ModelSpec:
        m = self._models.get(name)
        if m is None:
            sug = difflib.get_close_matches(name, list(self._models), n=1)
            raise ToolError(
                "UNKNOWN_MODEL",
                f"Không có model '{name}'" + (f" — có phải '{sug[0]}'?" if sug else ""),
                hint=f"Các model: {sorted(self._models)}",
            )
        return m

    def payload_model(self, m: ModelSpec, mode: str):
        k = (m.name, mode)
        if k not in self._payload:
            self._payload[k] = build_payload_model(m, mode)
        return self._payload[k]

    @contextmanager
    def tx(self, a: Actor):
        with self.pool.connection() as conn:
            with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
                cur.execute(
                    "SELECT set_config('xt.tenant_id', %s, true), set_config('xt.actor_id', %s, true), "
                    "set_config('xt.roles', %s, true), set_config('xt.team_ids', %s, true), set_config('xt.department_ids', %s, true)",
                    (
                        str(a.tenant_id),
                        str(a.actor_id),
                        ",".join(a.roles),
                        ",".join(map(str, a.team_ids)),
                        ",".join(map(str, a.department_ids)),
                    ),
                )
                yield cur

    def _require(self, a: Actor, m: ModelSpec, action: str) -> None:
        if not any(r in m.permissions and action in m.permissions[r].actions for r in a.roles):
            allowed = sorted(r for r, p in m.permissions.items() if action in p.actions)
            raise ToolError(
                "FORBIDDEN",
                f"Vai trò {list(a.roles)} không có quyền '{action}' trên '{m.label}'",
                hint=f"Vai trò được phép: {allowed or 'không có'}",
            )

    def _scope_hint(self, a: Actor, m: ModelSpec, action: str, lit) -> sql.Composable:
        scopes = {
            m.permissions[r].scope
            for r in a.roles
            if r in m.permissions and action in m.permissions[r].actions
        }
        if "all" in scopes:
            return sql.SQL("true")
        parts = [sql.SQL("owner_id = {}").format(lit(a.actor_id))]
        if "department" in scopes and a.department_ids:
            parts.append(sql.SQL("department_id = ANY({})").format(lit(list(a.department_ids))))
        if "team" in scopes and a.team_ids:
            parts.append(sql.SQL("team_id = ANY({})").format(lit(list(a.team_ids))))
        return sql.SQL("({})").format(sql.SQL(" OR ").join(parts))

    def _db_error(self, m: ModelSpec, e: psycopg.Error, op: str) -> ToolError:
        d = e.diag
        cn = d.constraint_name
        if isinstance(e, E.ForeignKeyViolation) and op == "delete" and cn in self._fk_in:
            src, f = self._fk_in[cn]
            return ToolError(
                "IN_USE",
                f"Không xóa được '{m.label}': đang được '{f.label}' của '{src.label}' tham chiếu",
                hint="Gỡ tham chiếu ở các bản ghi kia trước.",
            )
        if cn in self.cmap and isinstance(
            e, (E.UniqueViolation, E.CheckViolation, E.ForeignKeyViolation)
        ):
            model, kind, fields, msg = self.cmap[cn]
            mm = self._models[model]
            return ToolError(
                {"unique": "DUPLICATE", "fk": "BAD_REFERENCE"}.get(kind, "CONSTRAINT_VIOLATION"),
                msg,
                [
                    {
                        "field": x,
                        "label": next((f.label for f in mm.fields if f.name == x), x),
                        "code": kind,
                        "message": msg,
                    }
                    for x in fields
                ],
            )
        if isinstance(e, E.NotNullViolation):
            label = next((f.label for f in m.fields if f.name == d.column_name), d.column_name)
            return ToolError(
                "VALIDATION_FAILED",
                f"'{label}' là bắt buộc",
                [
                    {
                        "field": d.column_name,
                        "label": label,
                        "code": "missing",
                        "message": f"'{label}' là bắt buộc",
                    }
                ],
            )
        if isinstance(e, E.InsufficientPrivilege):
            return ToolError(
                "FORBIDDEN",
                f"Bạn không có quyền {op} bản ghi này trên '{m.label}' (ngoài phạm vi được cấp)",
                hint="Kiểm tra owner/team/department của bản ghi hoặc nhờ người có quyền.",
            )
        if isinstance(e, (E.LockNotAvailable, E.QueryCanceled)):
            return ToolError("BUSY", "Hệ thống đang bận (khóa/timeout), thử lại sau ít giây")
        return ToolError("DB_ERROR", f"Lỗi CSDL: {d.message_primary}")

    @staticmethod
    def _out(row: dict, m: ModelSpec) -> dict:
        return {k: row[k] for k in SYSTEM_OUT + [f.name for f in m.fields]}

    # ---- tools
    def create_record(self, a: Actor, model: str, data: dict) -> dict:
        try:
            m = self.model(model)
            self._require(a, m, "create")
            try:
                obj = self.payload_model(m, "create").model_validate(data)
            except ValidationError as e:
                raise _vi_pyd(m, e)
            vals = obj.model_dump(exclude_unset=True)
            row = {
                f.name: vals.get(
                    f.name, coerce_value(f, f.default) if f.default is not None else None
                )
                for f in m.fields
            }
            bad = [r for r in m.rules if eval3(r.check, row, m) is False]
            if bad:
                raise ToolError(
                    "RULE_VIOLATION",
                    "; ".join(r.message for r in bad),
                    [
                        {
                            "field": x,
                            "label": m.field(x).label,
                            "code": "rule:" + r.id,
                            "message": r.message,
                        }
                        for r in bad
                        for x in sorted(expr_depth_and_fields(r.check)[1])
                    ],
                )
            cols = list(vals)
            q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
                C.table(m),
                sql.SQL(", ").join(map(sql.Identifier, cols)),
                sql.SQL(", ").join(sql.Placeholder() * len(cols)),
            )
            params = [
                psycopg.types.json.Jsonb(v) if isinstance(v, (dict, list)) else v
                for v in vals.values()
            ]
            try:
                with self.tx(a) as conn:
                    r = conn.execute(q, params).fetchone()
            except psycopg.Error as e:
                raise self._db_error(m, e, "create")
            return {"ok": True, "record": self._out(r, m)}
        except ToolError as te:
            return te.payload

    def update_record(
        self, a: Actor, model: str, id: str, expected_revision: int, patch: dict
    ) -> dict:
        try:
            m = self.model(model)
            self._require(a, m, "update")
            try:
                obj = self.payload_model(m, "update").model_validate(patch)
            except ValidationError as e:
                raise _vi_pyd(m, e)
            vals = obj.model_dump(exclude_unset=True)
            if not vals:
                raise ToolError("EMPTY_PATCH", "Không có trường nào để cập nhật")
            sets = sql.SQL(", ").join(
                sql.SQL("{} = {}").format(sql.Identifier(k), sql.Placeholder()) for k in vals
            )
            params = [
                psycopg.types.json.Jsonb(v) if isinstance(v, (dict, list)) else v
                for v in vals.values()
            ]
            try:
                with self.tx(a) as conn:
                    r = conn.execute(
                        sql.SQL(
                            "UPDATE {} SET {} WHERE id = %s AND revision = %s RETURNING *"
                        ).format(C.table(m), sets),
                        params + [id, expected_revision],
                    ).fetchone()
                    if r is None:
                        cur = conn.execute(
                            sql.SQL("SELECT revision FROM {} WHERE id = %s").format(C.table(m)),
                            [id],
                        ).fetchone()
                        if cur is None:
                            raise ToolError(
                                "NOT_FOUND",
                                f"Không tìm thấy bản ghi {id} trong '{m.label}' (hoặc ngoài phạm vi bạn được xem)",
                            )
                        if cur["revision"] != expected_revision:
                            raise ToolError(
                                "CONFLICT",
                                f"Bản ghi đã bị sửa bởi người khác (revision hiện tại {cur['revision']}, bạn gửi {expected_revision})",
                                hint="Đọc lại bản ghi, hỏi người dùng có giữ thay đổi không, rồi gửi lại với revision mới.",
                            )
                        raise ToolError(
                            "FORBIDDEN",
                            f"Bạn xem được nhưng không có quyền sửa bản ghi này trên '{m.label}'",
                        )
            except psycopg.Error as e:
                raise self._db_error(m, e, "update")
            return {"ok": True, "record": self._out(r, m)}
        except ToolError as te:
            return te.payload

    def delete_record(self, a: Actor, model: str, id: str, expected_revision: int) -> dict:
        try:
            m = self.model(model)
            self._require(a, m, "delete")
            try:
                with self.tx(a) as conn:
                    r = conn.execute(
                        sql.SQL(
                            "DELETE FROM {} WHERE id = %s AND revision = %s RETURNING id"
                        ).format(C.table(m)),
                        [id, expected_revision],
                    ).fetchone()
                    if r is None:
                        raise ToolError(
                            "NOT_FOUND",
                            "Không tìm thấy bản ghi, sai revision, hoặc ngoài phạm vi quyền",
                        )
            except psycopg.Error as e:
                raise self._db_error(m, e, "delete")
            return {"ok": True, "deleted": str(r["id"])}
        except ToolError as te:
            return te.payload

    def search_records(
        self,
        a: Actor,
        model: str,
        filter: dict | None = None,
        sort: list[dict] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict:
        try:
            m = self.model(model)
            self._require(a, m, "read")
            if not (1 <= limit <= MAX_LIMIT) or not (0 <= offset <= MAX_OFFSET):
                raise ToolError(
                    "BAD_PAGINATION",
                    f"limit 1..{MAX_LIMIT}, offset 0..{MAX_OFFSET}",
                    hint="Thu hẹp bộ lọc thay vì lật trang sâu.",
                )
            vm = ModelSpec.model_construct(**{**m.__dict__, "fields": list(m.fields) + _SYS_FIELDS})
            by = {f.name: f for f in vm.fields}
            params: list[Any] = []

            def lit(v):
                params.append(v)
                return sql.Placeholder()

            where = sql.SQL("true")
            if filter:
                try:
                    e = TypeAdapter(Expr).validate_python(filter)
                    expr_depth_and_fields(e)
                    check_expr_types(e, by, for_filter=True)
                except (ValidationError, ValueError) as ex:
                    if isinstance(ex, ValidationError):
                        x = ex.errors()[0]
                        loc = ".".join(
                            str(p)
                            for p in x["loc"]
                            if not str(p).startswith(("function-", "Cmp", "And", "Or", "Not"))
                        )
                        msg = {
                            "extra_forbidden": f"khóa '{loc}' không hợp lệ (chỉ field/op/value/other_field/and/or/not)",
                            "literal_error": f"toán tử không hỗ trợ; hợp lệ: {x.get('ctx', {}).get('expected', '')}",
                            "missing": f"thiếu '{loc}'",
                        }.get(x["type"], x["msg"].replace("Value error, ", ""))
                    else:
                        msg = str(ex)
                    raise ToolError(
                        "BAD_FILTER",
                        f"Bộ lọc không hợp lệ: {msg}",
                        hint='Dạng: {"and":[{"field":"so_tien","op":">=","value":1000000}, ...]}; trường hợp lệ: '
                        + ", ".join(by),
                    )
                where = compile_expr_q(e, vm, lit)
            # Gợi ý phạm vi: lặp lại điều kiện RLS dưới dạng tham số để planner dùng được index
            # (policy OR nhiều role không dùng index owner/department). RLS vẫn là chốt chặn.
            where = sql.SQL("{} AND {}").format(where, self._scope_hint(a, m, "read", lit))
            order = []
            for s in sort or [{"field": "created_at", "dir": "desc"}]:
                if (
                    s.get("field") not in by
                    or s.get("dir", "asc") not in ("asc", "desc")
                    or by[s["field"]].type == "json"
                ):
                    raise ToolError(
                        "BAD_SORT",
                        f"Không sắp xếp được theo {s}",
                        hint=f"Trường hợp lệ: {list(by)}",
                    )
                order.append(
                    sql.SQL("{} {}").format(
                        sql.Identifier(s["field"]), sql.SQL(s.get("dir", "asc").upper())
                    )
                )
            order.append(sql.SQL("id"))
            T = C.table(m)
            with self.tx(a) as conn:
                total = conn.execute(
                    sql.SQL("SELECT count(*) AS n FROM {} WHERE {}").format(T, where), params
                ).fetchone()["n"]
                rows = conn.execute(
                    sql.SQL("SELECT * FROM {} WHERE {} ORDER BY {} LIMIT %s OFFSET %s").format(
                        T, where, sql.SQL(", ").join(order)
                    ),
                    params + [limit, offset],
                ).fetchall()
            return {
                "ok": True,
                "total": total,
                "limit": limit,
                "offset": offset,
                "has_more": offset + len(rows) < total,
                "items": [self._out(r, m) for r in rows],
            }
        except ToolError as te:
            return te.payload


def compile_expr_q(e: Expr, m: ModelSpec, lit) -> sql.Composable:
    return C.compile_expr(e, m, lit)
