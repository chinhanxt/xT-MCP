# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Áp các `operations` của propose_model_change (TCV1 §5.17) lên catalog hiện tại → catalog mới.

Field id của TCV1 là tên snake_case; engine cấp id ổn định riêng (`f<n>`) nên rename về sau
vẫn giữ dữ liệu. Biến đổi chưa hỗ trợ trả `capability_unavailable` có remediation, không giả
thành công.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from xt.contracts.model import FieldChanges, FieldInput, Operation, pii_level
from xt.domain.model.errors import ModelError
from xt.domain.model.expr import parse_rule_expr
from xt.domain.model.ident import STABLE_ID_MAX
from xt.domain.model.spec import Catalog

_TYPE_IN = {
    "string": "text", "text": "text", "integer": "number", "decimal": "number",
    "money": "money", "boolean": "bool", "date": "date", "datetime": "datetime", "enum": "enum",
    "ref": "ref", "person": "user", "file": "file",
}  # fmt: skip


def _unavailable(what: str, remedy: str) -> ModelError:
    return ModelError(
        "capability_unavailable",
        f"{what} chưa được hỗ trợ ở bản này",
        remediation=remedy,
        details={"capability": what},
    )


def stable_model_id(name: str) -> str:
    if len(name) <= STABLE_ID_MAX:
        return name
    return f"{name[:15]}_{hashlib.sha256(name.encode()).hexdigest()[:8]}"


def _next_field_id(fields: list[dict[str, Any]]) -> str:
    used = {str(f["id"]) for f in fields}
    n = len(fields) + 1
    while f"f{n}" in used:
        n += 1
    return f"f{n}"


def _bounds(target: dict[str, Any], src: FieldInput | FieldChanges, eng_type: str) -> None:
    if eng_type == "text":
        if src.max_length is not None:
            target["max"] = src.max_length
        if src.min is not None or src.max is not None:
            raise ModelError(
                "invalid_request",
                "Trường chuỗi dùng max_length (độ dài), không dùng min/max",
                details={"reason": "validation"},
            )
        return
    if src.max_length is not None:
        raise ModelError(
            "invalid_request",
            "max_length chỉ dùng cho string/text",
            details={"reason": "validation"},
        )
    if src.min is not None:
        target["min"] = src.min
    if src.max is not None:
        target["max"] = src.max


def _field_from_input(fi: FieldInput, fid: str) -> dict[str, Any]:
    if fi.multiple:
        raise _unavailable(
            "Trường nhiều giá trị (multiple)", "Tạo model con tham chiếu (ref) thay thế."
        )
    if fi.impact == "high":
        raise _unavailable("impact=high", "Bỏ impact hoặc để normal; mức L2 theo field có từ v0.2.")
    t = _TYPE_IN[fi.type]
    out: dict[str, Any] = {
        "id": fid,
        "name": fi.id,
        "type": t,
        "label": fi.label,
        "required": fi.required,
    }
    if fi.unique:
        out["unique"] = True
    if fi.description:
        out["description"] = fi.description
    if t == "enum":
        out["options"] = [{"value": v, "label": v} for v in fi.enum_values or []]
    elif fi.enum_values is not None:
        raise ModelError(
            "invalid_request",
            "enum_values chỉ dùng khi type=enum",
            details={"reason": "validation"},
        )
    if t == "ref":
        out["ref"] = fi.ref_model
    _bounds(out, fi, t)
    level = pii_level(fi.pii, fi.sensitive)
    if level is not None:
        out["pii"] = level
    return out


def apply_operations(current: Catalog | None, model_name: str, ops: list[Operation]) -> Catalog:
    data: dict[str, Any] = current.dump() if current else {"version": 0, "models": []}
    data["version"] = int(data["version"]) + 1
    models: list[dict[str, Any]] = data["models"]
    target = next((m for m in models if m["name"] == model_name), None)
    for i, op in enumerate(ops):
        where = f"operations[{i}] ({op.op})"
        if op.op == "create_model":
            if target is not None:
                raise ModelError(
                    "invalid_request",
                    f"Model '{model_name}' đã tồn tại; dùng add_field/update_field",
                    details={"reason": "validation", "at": where},
                )
            if op.model is None:
                raise ModelError(
                    "invalid_request", f"{where}: thiếu 'model'", details={"reason": "validation"}
                )
            if op.model.primitive != "record" or op.model.process:
                raise _unavailable(
                    f"primitive '{op.model.primitive}'/process",
                    "Tạo model primitive=record; luồng duyệt gắn qua process ở bản sau.",
                )
            target = {
                "id": stable_model_id(model_name),
                "name": model_name,
                "label": op.model.label,
                "fields": [],
                "indexes": [],
                "rules": [],
                "permissions": {},
            }
            if op.model.description:
                target["description"] = op.model.description
            if op.model.title_field:
                target["title_field"] = op.model.title_field
            models.append(target)
            continue
        if target is None:
            raise ModelError(
                "not_found",
                f"Không có model '{model_name}'; thao tác đầu tiên phải là create_model",
                details={"reason": "unknown_model"},
            )
        fields: list[dict[str, Any]] = target["fields"]
        by_name = {f["name"]: f for f in fields}
        if op.op == "add_field":
            if op.field is None:
                raise ModelError(
                    "invalid_request", f"{where}: thiếu 'field'", details={"reason": "validation"}
                )
            if op.field.id in by_name:
                raise ModelError(
                    "invalid_request",
                    f"Trường '{op.field.id}' đã có trong '{model_name}'",
                    details={"reason": "validation"},
                )
            fields.append(_field_from_input(op.field, _next_field_id(fields)))
        elif op.op in ("update_field", "remove_field"):
            f = by_name.get(op.field_id or "")
            if f is None:
                raise ModelError(
                    "invalid_request",
                    f"Trường '{op.field_id}' không tồn tại trong '{model_name}'",
                    details={"reason": "unknown_field", "valid_fields": sorted(by_name)},
                )
            if op.op == "remove_field":
                fields.remove(f)
                target["indexes"] = [
                    ix for ix in target["indexes"] if f["name"] not in ix["fields"]
                ]
                if target.get("title_field") == f["name"]:
                    target.pop("title_field")
            else:
                _update_field(f, op.changes or FieldChanges())
        elif op.op == "add_rule":
            if op.rule is None:
                raise ModelError(
                    "invalid_request", f"{where}: thiếu 'rule'", details={"reason": "validation"}
                )
            try:
                expr = parse_rule_expr(op.rule.expr)
            except ValueError as e:
                raise ModelError(
                    "invalid_request",
                    f"Biểu thức rule không hợp lệ: {e}",
                    details={"reason": "validation"},
                ) from e
            if any(r["id"] == op.rule.id for r in target["rules"]):
                raise ModelError(
                    "invalid_request",
                    f"Rule '{op.rule.id}' đã tồn tại",
                    details={"reason": "validation"},
                )
            target["rules"].append(
                {
                    "id": op.rule.id,
                    "check": expr.model_dump(mode="json", by_alias=True, exclude_none=True),
                    "message": op.rule.message,
                }
            )
        elif op.op == "remove_rule":
            before = len(target["rules"])
            target["rules"] = [r for r in target["rules"] if r["id"] != op.rule_id]
            if len(target["rules"]) == before:
                raise ModelError(
                    "invalid_request",
                    f"Rule '{op.rule_id}' không tồn tại",
                    details={"reason": "validation"},
                )
        else:
            raise _unavailable(
                f"Thao tác {op.op} (state/transition/metric)",
                "State machine và metric chạy qua process-spec; hiện chỉ đổi field và rule.",
            )
    try:
        return Catalog.model_validate(data)
    except ValidationError as e:
        errs = [
            {
                "loc": ".".join(str(x) for x in err["loc"]),
                "message": str(err["msg"]).removeprefix("Value error, "),
            }
            for err in e.errors()
        ]
        raise ModelError(
            "invalid_request",
            "Spec sau khi áp thao tác chưa hợp lệ: " + "; ".join(x["message"] for x in errs[:5]),
            details={"reason": "validation", "errors": errs},
        ) from e


def _update_field(f: dict[str, Any], ch: FieldChanges) -> None:
    if ch.impact == "high":
        raise _unavailable("impact=high", "Bỏ impact hoặc để normal.")
    if ch.label is not None:
        f["label"] = ch.label
    if ch.description is not None:
        f["description"] = ch.description
    if ch.required is not None:
        f["required"] = ch.required
    if ch.unique is not None:
        f["unique"] = ch.unique
    if ch.enum_values is not None:
        if f["type"] != "enum":
            raise ModelError(
                "invalid_request",
                "enum_values chỉ dùng cho trường enum",
                details={"reason": "validation"},
            )
        labels = {o["value"]: o["label"] for o in f.get("options") or []}
        f["options"] = [{"value": v, "label": labels.get(v, v)} for v in ch.enum_values]
    _bounds(f, ch, str(f["type"]))
    if ch.pii is not None or ch.sensitive is not None:
        cur = str(f.get("pii", "none"))
        pii = ch.pii if ch.pii is not None else cur != "none"
        sens = ch.sensitive if ch.sensitive is not None else cur == "sensitive"
        level = pii_level(pii, sens)
        f["pii"] = level
        f["pii_suggested"] = False
