# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Poka-yoke cho payload CRUD: model pydantic sinh lúc chạy từ spec + dịch lỗi sang tiếng Việt.

- `extra=forbid`; regex trường text chạy bằng engine Rust (tuyến tính) của pydantic-core.
- Tiền VND là số nguyên đồng; số thập phân gửi dạng chuỗi; số thực có phần lẻ bị từ chối.
- Trường `managed_by: workflow` không có trong payload create/update (G-M1).
- Trường PII không nhận giá trị đã che `[PII:…]` (tool-contracts-v1 §3.8).
"""

from __future__ import annotations

import difflib
import json
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictStr,
    ValidationError,
    create_model,
)

from xt.domain.model.errors import ModelError
from xt.domain.model.expr import eval3, expr_fields
from xt.domain.model.spec import FieldSpec, ModelSpec

Mode = Literal["create", "update", "workflow"]
SYSTEM_WRITABLE = ("owner_id", "team_id", "department_id")
MASK_PREFIX = "[PII:"


def _money(v: Any) -> Any:
    if isinstance(v, bool):
        raise ValueError("phải là số nguyên đồng")
    if isinstance(v, float):
        if v != int(v):
            raise ValueError("phải là số nguyên (VND không có phần lẻ)")
        return int(v)
    if isinstance(v, str | Decimal):
        try:
            d = Decimal(v)
        except InvalidOperation as e:
            raise ValueError("phải là số nguyên đồng") from e
        if not d.is_finite() or d != d.to_integral_value():
            raise ValueError("phải là số nguyên (VND không có phần lẻ)")
        return int(d)
    return v


def _number(v: Any) -> Any:
    if isinstance(v, bool):
        raise ValueError("phải là số")
    if isinstance(v, float):
        if v != int(v):
            raise ValueError('số có phần lẻ phải gửi dạng chuỗi thập phân, ví dụ "12.5"')
        return int(v)
    return v


def _not_masked(v: Any) -> Any:
    if isinstance(v, str) and v.startswith(MASK_PREFIX):
        raise ValueError("không được ghi giá trị đã che [PII:…] ngược vào dữ liệu")
    return v


def _py_type(f: FieldSpec) -> Any:
    t = f.type
    lo: Any = f.coerced("min")
    hi: Any = f.coerced("max")
    if t == "text":
        tp: Any = Annotated[
            StrictStr,
            Field(
                min_length=lo,
                max_length=hi,
                pattern=f.regex,
            ),
        ]
    elif t == "number":
        tp = Annotated[
            Decimal,
            BeforeValidator(_number),
            Field(ge=lo, le=hi, allow_inf_nan=False),
        ]
    elif t == "money":
        tp = Annotated[
            int,
            BeforeValidator(_money),
            Field(ge=lo, le=hi, strict=False),
        ]
    elif t == "date":
        tp = Annotated[date, Field(ge=lo, le=hi)]
    elif t == "datetime":
        tp = Annotated[AwareDatetime, Field(ge=lo, le=hi)]
    elif t == "bool":
        tp = StrictBool
    elif t == "enum":
        assert f.options is not None
        tp = Literal[tuple(o.value for o in f.options)]
    elif t in ("ref", "user", "file"):
        tp = UUID
    else:  # json
        tp = dict[str, Any] | list[Any]
    if f.pii != "none":
        tp = Annotated[tp, BeforeValidator(_not_masked)]
    return tp


SYSTEM_FIELDS = (
    FieldSpec(id="sys_created", name="created_at", type="datetime", label="Ngày tạo", pii="none"),
    FieldSpec(id="sys_updated", name="updated_at", type="datetime", label="Ngày sửa", pii="none"),
    FieldSpec(id="sys_owner", name="owner_id", type="user", label="Người sở hữu", pii="none"),
)


def with_system_fields(m: ModelSpec) -> dict[str, FieldSpec]:
    """Trường của model + cột hệ thống lọc/sắp xếp được (created_at, updated_at, owner_id)."""
    by = m.by_name()
    for f in SYSTEM_FIELDS:
        by[f.name] = f
    return by


def build_values_model(m: ModelSpec, mode: Mode) -> type[BaseModel]:
    """create: trường bắt buộc không default là bắt buộc; update/workflow: patch (thiếu = giữ
    nguyên, trường bắt buộc không được gán null). create/update loại trường managed_by=workflow."""
    defs: dict[str, Any] = {}
    for f in m.fields:
        if f.managed_by and mode != "workflow":
            continue
        tp = _py_type(f)
        if mode == "create" and f.required and f.default is None:
            defs[f.name] = (tp, ...)
        elif not f.required:
            defs[f.name] = (tp | None, None)
        else:
            defs[f.name] = (tp, None)
    for sysf in SYSTEM_WRITABLE:
        defs[sysf] = (UUID | None, None)
    model: type[BaseModel] = create_model(
        f"{m.name}__{mode}",
        __config__=ConfigDict(extra="forbid", regex_engine="rust-regex", title=m.label),
        **defs,
    )
    return model


_PYD_VI = {
    "missing": "là trường bắt buộc, chưa có giá trị",
    "extra_forbidden": "không tồn tại trong model",
    "string_too_short": "quá ngắn (tối thiểu {min_length} ký tự)",
    "string_too_long": "quá dài (tối đa {max_length} ký tự)",
    "greater_than_equal": "phải lớn hơn hoặc bằng {ge}",
    "less_than_equal": "phải nhỏ hơn hoặc bằng {le}",
    "literal_error": "chỉ nhận một trong: {expected}",
    "int_parsing": "phải là số nguyên",
    "int_type": "phải là số nguyên",
    "int_from_float": "phải là số nguyên (VND không có phần lẻ)",
    "decimal_type": "phải là số",
    "decimal_parsing": "phải là số",
    "finite_number": "phải là số hữu hạn",
    "dict_type": "phải là object JSON",
    "date_from_datetime_parsing": "phải là ngày dạng YYYY-MM-DD",
    "date_from_datetime_inexact": "phải là ngày dạng YYYY-MM-DD (không kèm giờ)",
    "date_parsing": "phải là ngày dạng YYYY-MM-DD",
    "date_type": "phải là ngày dạng YYYY-MM-DD",
    "datetime_parsing": "phải là thời điểm ISO 8601",
    "datetime_from_date_parsing": "phải là thời điểm ISO 8601",
    "datetime_type": "phải là thời điểm ISO 8601",
    "timezone_aware": "phải có múi giờ (vd +07:00)",
    "bool_type": "phải là true/false",
    "uuid_parsing": "phải là id (UUID) của bản ghi",
    "uuid_type": "phải là id (UUID) của bản ghi",
    "string_type": "phải là chuỗi",
    "value_error": "{error}",
    "string_pattern_mismatch": "không đúng định dạng (mẫu {pattern})",
}


def translate_validation(m: ModelSpec, e: ValidationError, mode: Mode) -> ModelError:
    names = [f.name for f in m.fields if mode == "workflow" or not f.managed_by]
    by = m.by_name()
    items: list[dict[str, Any]] = []
    recovery: set[str] = set()
    unknown = False
    for err in e.errors():
        loc = err.get("loc") or ()
        name = str(loc[0]) if loc else "?"
        typ = err["type"]
        ctx = {k: str(v) for k, v in (err.get("ctx") or {}).items()}
        fs = by.get(name)
        if name in names:
            recovery.add(name)
        label = fs.label if fs else name
        if typ == "literal_error" and fs is not None and fs.options:
            ctx["expected"] = ", ".join(f"{o.value} ({o.label})" for o in fs.options)
        try:
            msg = _PYD_VI.get(typ, "giá trị không hợp lệ").format(**ctx)
        except (KeyError, IndexError):
            msg = "giá trị không hợp lệ"
        if typ.startswith("value_error") and "error" in ctx:
            msg = ctx["error"].removeprefix("Value error, ")
        if err.get("input", 0) is None and typ != "missing":
            msg = "không được để trống (trường bắt buộc)"
        item: dict[str, Any] = {"field": name, "label": label, "code": typ}
        if typ == "extra_forbidden":
            unknown = True
            sug = difflib.get_close_matches(name, names, n=1, cutoff=0.5)
            text = f"Trường '{name}' không tồn tại trong '{m.label}'"
            if name in by and by[name].managed_by:
                text = f"Trường '{label}' do quy trình (workflow) quản lý"
            elif sug:
                text += f" — có phải ý bạn là '{sug[0]}'?"
                recovery.update(sug)
            item["message"] = text
        else:
            item["message"] = f"'{label}' {msg}"
        items.append(item)
    details: dict[str, Any] = {
        "reason": "unknown_field" if unknown else "validation",
        "fields": items,
    }
    if unknown:
        details["valid_fields"] = names
    # A compact profile may not advertise describe_model. Return catalog facts for the
    # invalid/missing fields so a client can correct supplied values without guessing.
    # Never echo user values or catalog defaults. Oversize metadata is explicitly omitted.
    contracts, omitted = [], []
    remaining = 16384
    for name in sorted(recovery):
        f = by[name]
        contract = f.model_dump(
            mode="json",
            exclude_none=True,
            include={"label", "type", "required", "min", "max", "regex", "options", "ref"},
        )
        contract["field"] = name
        size = len(json.dumps(contract, ensure_ascii=False).encode()) + 2
        if size > remaining:
            omitted.append(name)
        else:
            contracts.append(contract)
            remaining -= size
    details["field_contracts"] = contracts
    if omitted:
        details["field_contracts_omitted"] = omitted
    return ModelError(
        "invalid_request",
        f"Dữ liệu '{m.label}' chưa hợp lệ ({len(items)} lỗi): "
        + "; ".join(i["message"] for i in items[:5]),
        details=details,
    )


def reject_managed(m: ModelSpec, data: dict[str, Any], record_ref: str | None = None) -> None:
    """G-M1 lớp app: create/update không được chạm trường do workflow quản."""
    bad = [f for f in m.fields if f.managed_by and f.name in data]
    if not bad:
        return
    labels = ", ".join(f"'{f.label}'" for f in bad)
    raise ModelError(
        "precondition_failed",
        f"Trường {labels} do quy trình (workflow) quản lý, không sửa trực tiếp được",
        remediation="Dùng transition_record (hoặc decide_request với bước duyệt) thay vì"
        " create_record/update_record.",
        details={
            "reason": "state_field",
            "fields": [
                {
                    "field": f.name,
                    "label": f.label,
                    "message": f"'{f.label}' chỉ đổi qua bước duyệt/chuyển trạng thái",
                }
                for f in bad
            ],
        },
        continuation={"tool": "transition_record", "arguments": {"ref": record_ref}}
        if record_ref
        else None,
    )


def rule_violations(m: ModelSpec, row: dict[str, Any]) -> ModelError | None:
    """Đánh giá rule bằng logic 3 trị (khớp CHECK ở DB); None nếu không vi phạm."""
    by = m.by_name()
    bad = [r for r in m.rules if eval3(r.check, row, by) is False]
    if not bad:
        return None
    return ModelError(
        "rule_violation",
        "; ".join(r.message for r in bad),
        details={
            "violations": [
                {"field": x, "label": by[x].label, "rule": r.id, "message": r.message}
                for r in bad
                for x in sorted(expr_fields(r.check))
            ]
        },
    )
