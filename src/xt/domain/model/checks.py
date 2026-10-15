# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ràng buộc suy ra từ spec, đặt tên theo id ổn định (không theo tên).

Tên constraint/index/policy chỉ dùng id model/field/rule/index (mỗi id ≤ 24 ký tự) nên luôn
≤ 63 byte và sống qua rename: lỗi DB tra ngược được về trường/nhãn/thông điệp tiếng Việt.
`key` là chữ ký ngữ nghĩa (theo id) để planner so sánh cũ/mới mà không phụ thuộc SQL.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal

from xt.domain.model.expr import Expr, map_fields
from xt.domain.model.spec import JSON_MAX_BYTES, Catalog, FieldSpec, ModelSpec

CheckKind = Literal["min", "max", "re", "enum", "json", "rule"]
ConstraintKind = Literal["min", "max", "re", "enum", "json", "rule", "unique", "fk", "nn"]
PG_NAME_MAX = 63


def cname(kind: str, model_id: str, *parts: str) -> str:
    n = "_".join([kind, model_id, *parts])
    if len(n.encode()) > PG_NAME_MAX:  # không xảy ra với id hợp lệ; chặn cứng nếu bị bỏ qua
        raise ValueError(f"tên ràng buộc vượt {PG_NAME_MAX} byte: {n}")
    return n


def pk_name(m: ModelSpec) -> str:
    return cname("pk", m.id)


def tid_unique_name(m: ModelSpec) -> str:
    return cname("uq", m.id, "tid")


def unique_name(m: ModelSpec, f: FieldSpec) -> str:
    return cname("ux", m.id, f.id)


def fk_name(m: ModelSpec, f: FieldSpec) -> str:
    return cname("fk", m.id, f.id)


def ref_index_name(m: ModelSpec, f: FieldSpec) -> str:
    return cname("ixr", m.id, f.id)


def index_name(m: ModelSpec, index_id: str) -> str:
    return cname("ix", m.id, index_id)


def notnull_check_name(m: ModelSpec, f: FieldSpec) -> str:
    return cname("ck", m.id, f.id, "nn")


def policy_name(m: ModelSpec, what: str) -> str:
    return cname("p", m.id, what)


@dataclass(frozen=True)
class CheckDef:
    name: str
    kind: CheckKind
    model_id: str
    field_id: str | None
    key: tuple[object, ...]
    message: str
    fields: tuple[str, ...]
    value: object = None
    expr: Expr | None = field(default=None, compare=False)


def field_checks(m: ModelSpec, f: FieldSpec) -> list[CheckDef]:
    out: list[CheckDef] = []
    lb = f.label

    def add(kind: CheckKind, value: object, message: str) -> None:
        out.append(
            CheckDef(
                cname("ck", m.id, f.id, kind),
                kind,
                m.id,
                f.id,
                (kind, f.type, _freeze(value)),
                message,
                (f.name,),
                value,
            )
        )

    if f.type == "text":
        if f.min is not None:
            add("min", f.coerced("min"), f"'{lb}' phải có ít nhất {f.coerced('min')} ký tự")
        if f.max is not None:
            add("max", f.coerced("max"), f"'{lb}' tối đa {f.coerced('max')} ký tự")
        if f.regex is not None:
            add("re", f.regex, f"'{lb}' không đúng định dạng (mẫu {f.regex})")
    elif f.type in ("number", "money", "date", "datetime"):
        if f.min is not None:
            add("min", f.coerced("min"), f"'{lb}' phải lớn hơn hoặc bằng {f.min}")
        if f.max is not None:
            add("max", f.coerced("max"), f"'{lb}' phải nhỏ hơn hoặc bằng {f.max}")
    elif f.type == "enum":
        assert f.options is not None
        vals = tuple(o.value for o in f.options)
        shown = ", ".join(f"{o.value} ({o.label})" for o in f.options)
        add("enum", vals, f"'{lb}' chỉ nhận một trong: {shown}")
    elif f.type == "json":
        add("json", JSON_MAX_BYTES, f"'{lb}' vượt {JSON_MAX_BYTES} byte")
    return out


def rule_checks(m: ModelSpec) -> list[CheckDef]:
    from xt.domain.model.expr import expr_fields

    to_id = {f.name: f.id for f in m.fields}
    out = []
    for r in m.rules:
        canon = map_fields(r.check, to_id).model_dump_json(by_alias=True)
        out.append(
            CheckDef(
                cname("ck", m.id, "r", r.id),
                "rule",
                m.id,
                None,
                ("rule", canon),
                r.message,
                tuple(sorted(expr_fields(r.check))),
                expr=r.check,
            )
        )
    return out


def all_checks(m: ModelSpec) -> list[CheckDef]:
    out: list[CheckDef] = []
    for f in m.fields:
        out += field_checks(m, f)
    return out + rule_checks(m)


def checks_by_name(m: ModelSpec) -> dict[str, CheckDef]:
    return {c.name: c for c in all_checks(m)}


def _freeze(v: object) -> object:
    if isinstance(v, list):
        return tuple(v)
    return v


@dataclass(frozen=True)
class ConstraintInfo:
    """conname → (model, kind, trường, thông điệp tiếng Việt) để dịch lỗi Postgres."""

    model: str
    kind: ConstraintKind
    fields: tuple[str, ...]
    message: str


def constraint_map(catalog: Catalog) -> dict[str, ConstraintInfo]:
    out: dict[str, ConstraintInfo] = {}
    for m in catalog.models:
        for c in all_checks(m):
            out[c.name] = ConstraintInfo(m.name, c.kind, c.fields, c.message)
        for f in m.fields:
            if f.unique:
                out[unique_name(m, f)] = ConstraintInfo(
                    m.name, "unique", (f.name,), f"'{f.label}' đã tồn tại, phải là duy nhất"
                )
            if f.type == "ref":
                out[fk_name(m, f)] = ConstraintInfo(
                    m.name,
                    "fk",
                    (f.name,),
                    f"'{f.label}' tham chiếu tới bản ghi không tồn tại hoặc không thuộc đơn vị"
                    " của bạn",
                )
            out[notnull_check_name(m, f)] = ConstraintInfo(
                m.name, "nn", (f.name,), f"'{f.label}' là trường bắt buộc"
            )
        for ix in m.indexes:
            if ix.unique:
                labels = ", ".join(m.field(x).label for x in ix.fields)
                out[index_name(m, ix.id)] = ConstraintInfo(
                    m.name, "unique", tuple(ix.fields), f"Tổ hợp ({labels}) đã tồn tại"
                )
        out[pk_name(m)] = ConstraintInfo(m.name, "unique", ("id",), "id bản ghi đã tồn tại")
    return out


def fk_sources(catalog: Catalog) -> dict[str, tuple[ModelSpec, FieldSpec]]:
    """fk name → (model nguồn, trường) để báo 'đang được tham chiếu' khi xóa."""
    out: dict[str, tuple[ModelSpec, FieldSpec]] = {}
    for m in catalog.models:
        for f in m.fields:
            if f.type == "ref":
                out[fk_name(m, f)] = (m, f)
    return out


def names(items: Iterable[CheckDef]) -> list[str]:
    return [c.name for c in items]
