# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bề mặt tool của model engine theo tool-contracts-v1: `describe_model` (§5.2) và
`propose_model_change` (§5.17).

Ánh xạ DLCN (architecture-v2 §6.1): `basic` ⇔ `pii: true`; `sensitive` ⇔ `pii: true,
sensitive: true`; `none` ⇔ cả hai false. Kiểu field của engine được chiếu sang enum kiểu của
TCV1 (`user` → `person`, `bool` → `boolean`, `number` → `decimal`...).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from xt.domain.model.plan import CLASS_VI, Plan, RiskClass
from xt.domain.model.spec import FieldSpec, ModelSpec, PiiLevel
from xt.domain.model.values import build_values_model

CONTRACT_ID = r"^[a-z][a-z0-9_]{0,62}$"
ContractId = Annotated[str, Field(pattern=CONTRACT_ID)]
TCV1Type = Literal[
    "string", "text", "integer", "decimal", "money", "boolean", "date", "datetime", "enum", "ref",
    "person", "file",
]  # fmt: skip
SHORT_TEXT = 255
_TYPE_OUT = {
    "number": "decimal", "money": "money", "date": "date", "datetime": "datetime",
    "bool": "boolean", "enum": "enum", "ref": "ref", "user": "person", "file": "file",
}  # fmt: skip


def pii_flags(level: PiiLevel) -> tuple[bool, bool]:
    return level != "none", level == "sensitive"


def pii_level(pii: bool | None, sensitive: bool | None) -> PiiLevel | None:
    """None khi không khai cả hai cờ → engine gợi ý theo NĐ 356/2025 (pii_suggested)."""
    if pii is None and sensitive is None:
        return None
    if sensitive:
        return "sensitive"
    return "basic" if pii else "none"


def contract_type(f: FieldSpec) -> str:
    if f.type == "text":
        mx = f.coerced("max")
        return "string" if isinstance(mx, int) and mx <= SHORT_TEXT else "text"
    if f.type == "json":
        return "text"
    return _TYPE_OUT[f.type]


def _bound(v: object) -> str | int | None:
    if v is None:
        return None
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


def describe_field(f: FieldSpec) -> dict[str, Any]:
    pii, sensitive = pii_flags(f.pii)
    out: dict[str, Any] = {
        "id": f.name,
        "label": f.label,
        "type": contract_type(f),
        "required": f.required,
        "multiple": False,
        "unique": f.unique,
        "pii": pii,
        "sensitive": sensitive,
        "pii_level": f.pii,
        "pii_suggested": f.pii_suggested,
        "impact": "normal",
        "managed_by": f.managed_by,
    }
    if f.description:
        out["description"] = f.description[:300]
    if f.type == "json":
        out["format"] = "json"
    if f.options:
        out["enum_values"] = [o.value for o in f.options]
        out["enum_labels"] = {o.value: o.label for o in f.options}
    if f.ref:
        out["ref_model"] = f.ref
    if f.type == "text":
        if f.max is not None:
            out["max_length"] = f.coerced("max")
        if f.min is not None:
            out["min_length"] = f.coerced("min")
        if f.regex:
            out["pattern"] = f.regex
    else:
        if f.min is not None:
            out["min"] = _bound(f.coerced("min"))
        if f.max is not None:
            out["max"] = _bound(f.coerced("max"))
    if f.default is not None:
        out["default"] = _bound(f.coerced("default"))
    return out


def values_schema(m: ModelSpec, mode: Literal["create", "update"] = "create") -> dict[str, Any]:
    s = build_values_model(m, mode).model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return s


def describe_model(
    m: ModelSpec, revision: int, *, include_values_schema: bool = False
) -> dict[str, Any]:
    """`data` của describe_model (TCV1 §5.2)."""
    model: dict[str, Any] = {
        "id": m.name,
        "label": m.label,
        "description": m.description or "",
        "primitive": "record",
        "revision": revision,
        "title_field": m.title_field or "",
        "fields": [describe_field(f) for f in m.fields],
        "states": [],
        "transitions": [],
        "rules": [{"id": r.id, "message": r.message} for r in m.rules],
        "metrics": [],
        "process": None,
        "workflow_fields": [f.name for f in m.fields if f.managed_by],
    }
    if include_values_schema:
        model["values_schema"] = values_schema(m, "create")
        model["update_values_schema"] = values_schema(m, "update")
    return {"model": model}


# ------------------------------------------------------------- propose_model_change ----
class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


Bound = StrictInt | StrictStr | None


class FieldInput(_Closed):
    id: ContractId
    label: Annotated[str, Field(min_length=1, max_length=120)]
    type: TCV1Type
    required: StrictBool = False
    multiple: StrictBool = False
    enum_values: Annotated[list[ContractId], Field(max_length=100)] | None = None
    ref_model: ContractId | None = None
    min: Bound = None
    max: Bound = None
    max_length: Annotated[int, Field(ge=1)] | None = None
    unique: StrictBool = False
    pii: StrictBool | None = None
    sensitive: StrictBool | None = None
    impact: Literal["normal", "high"] = "normal"
    description: Annotated[str, Field(max_length=300)] | None = None


class FieldChanges(_Closed):
    label: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    required: StrictBool | None = None
    enum_values: Annotated[list[ContractId], Field(max_length=100)] | None = None
    min: Bound = None
    max: Bound = None
    max_length: Annotated[int, Field(ge=1)] | None = None
    unique: StrictBool | None = None
    pii: StrictBool | None = None
    sensitive: StrictBool | None = None
    impact: Literal["normal", "high"] | None = None
    description: Annotated[str, Field(max_length=300)] | None = None


class RuleInput(_Closed):
    id: ContractId
    expr: Annotated[str, Field(min_length=1, max_length=500)]
    message: Annotated[str, Field(min_length=1, max_length=300)]


class ModelHeader(_Closed):
    label: Annotated[str, Field(min_length=1, max_length=120)]
    primitive: Literal["record", "request", "task", "document"]
    description: Annotated[str, Field(max_length=500)] | None = None
    title_field: ContractId | None = None
    process: ContractId | None = None


OpName = Literal[
    "create_model", "add_field", "update_field", "remove_field", "add_state", "add_transition",
    "remove_transition", "add_rule", "remove_rule", "add_metric",
]  # fmt: skip


class Operation(_Closed):
    op: OpName
    field: FieldInput | None = None
    field_id: ContractId | None = None
    changes: FieldChanges | None = None
    state: dict[str, Any] | None = None
    transition: dict[str, Any] | None = None
    rule: RuleInput | None = None
    rule_id: ContractId | None = None
    metric: dict[str, Any] | None = None
    model: ModelHeader | None = None


class ProposeModelChange(_Closed):
    model_id: ContractId
    operations: Annotated[list[Operation], Field(min_length=1, max_length=50)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    dry_run: StrictBool = True
    base_revision: Annotated[int, Field(ge=0)] | None = None
    idempotency_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{8,128}$")]


def _path(c: Any) -> str:
    return f"{c.model}.{c.field}" if c.field else str(c.model)


def classification(plan: Plan) -> str:
    live = [c.cls for c in plan.changes if c.cls is not RiskClass.BLOCKED]
    if RiskClass.DESTRUCTIVE in live:
        return "destructive"
    if RiskClass.RISKY in live:
        return "tightening"
    return "additive"


def propose_result(
    plan: Plan,
    *,
    base_revision: int,
    dry_run: bool,
    proposal: str | None = None,
    request: str | None = None,
    extra_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """`data` của propose_model_change (TCV1 §5.17) + `plan_hash`/`plan_vi` cho người duyệt."""
    findings: list[dict[str, Any]] = list(extra_findings or [])
    for c in plan.changes:
        if c.cls is RiskClass.BLOCKED:
            sev = "blocker"
        elif c.cls in (RiskClass.RISKY, RiskClass.DESTRUCTIVE):
            sev = "warning"
        else:
            continue
        msg = f"[{CLASS_VI[c.cls]}] {c.message}"
        if c.rehearsal_error:
            msg += f" — diễn tập lỗi: {c.rehearsal_error}"
        findings.append(
            {
                "severity": sev,
                "message": msg,
                "affected_count": c.violations if c.violations is not None else c.affected,
            }
        )
    counts = [x for c in plan.changes for x in (c.affected, c.violations) if x is not None]
    destructive = any(c.cls is RiskClass.DESTRUCTIVE for c in plan.changes)
    return {
        "dry_run": dry_run,
        "base_revision": base_revision,
        "classification": classification(plan),
        "diff": [
            {
                "op": c.kind,
                "path": _path(c),
                "before": c.details.get("from"),
                "after": c.details.get("to"),
                "class": c.cls.value,
                "change_id": c.id,
            }
            for c in plan.changes
        ],
        "findings": findings,
        "affected_records": max(counts) if counts else None,
        "approvals_required": 2 if destructive else 1,
        "proposal": proposal,
        "request": request,
        "plan_hash": plan.hash,
        "destructive_change_ids": plan.ids(RiskClass.DESTRUCTIVE),
        "plan_vi": plan.render_vi(),
    }


def error_envelope(
    error: dict[str, Any], *, contract_version: str, trace_id: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "untrusted": True,
        "error": error,
        "meta": {"contract_version": contract_version, "trace_id": trace_id},
    }
