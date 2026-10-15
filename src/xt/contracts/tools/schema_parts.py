# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
# ruff: noqa: E501  (chuỗi description chép nguyên văn từ docs/tool-contracts-v1.md)
"""Mảnh JSON Schema dùng chung của catalog v1 (chép đúng nguồn sinh `docs/tool-contracts-v1.md`).

Input: object phẳng, đóng, mọi property có description, không combinator ở root.
Output (`data`): object mở để tương thích tiến.
"""

from __future__ import annotations

from typing import Any

Schema = dict[str, Any]

D2020 = "https://json-schema.org/draft/2020-12/schema"

# ---------------------------------------------------------------- primitives
FIELD_ID = r"^[a-z][a-z0-9_]{0,62}$"
MODEL_ID = r"^[a-z][a-z0-9_]{0,62}$"
# short ref "model:id" (tool surface) or full XRN "xrn:tenant:model:id"
REF = r"^(xrn:[a-z0-9][a-z0-9_-]{0,63}:)?[a-z][a-z0-9_]{0,62}:[a-z0-9][a-z0-9_-]{0,63}$"
PERSON = r"^(me|(xrn:[a-z0-9][a-z0-9_-]{0,63}:)?person:[a-z0-9][a-z0-9_-]{0,63})$"
EXPLAIN_OR_REF = r"^(explain:[a-z0-9][a-z0-9_-]{0,63}|(xrn:[a-z0-9][a-z0-9_-]{0,63}:)?[a-z][a-z0-9_]{0,62}:[a-z0-9][a-z0-9_-]{0,63})$"
DATE_OR_DT = r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2}))?$"


def s(desc: str, **kw: Any) -> Schema:
    return {"type": "string", "description": desc, **kw}


def ref(desc: str) -> Schema:
    return {"type": "string", "pattern": REF, "description": desc}


def person(desc: str) -> Schema:
    return {"type": "string", "pattern": PERSON, "description": desc}


SCALAR: Schema = {"type": ["string", "number", "boolean", "null"]}
VALUE: Schema = {
    "type": ["string", "number", "boolean", "null", "array"],
    "items": {"type": ["string", "number", "boolean"]},
    "maxItems": 100,
}


def values(desc: str) -> Schema:
    return {
        "type": "object",
        "description": desc,
        "propertyNames": {"pattern": FIELD_ID},
        "additionalProperties": VALUE,
        "maxProperties": 200,
    }


IDEM: Schema = {
    "type": "string",
    "pattern": r"^[A-Za-z0-9_.:-]{8,128}$",
    "description": "Unique key you invent for this action (8-128 chars). Reuse the SAME key only when retrying the same action.",
}
EXPECTED_REV: Schema = {
    "type": "integer",
    "minimum": 1,
    "description": "Revision you last read (from get_record/search). Mismatch -> revision_conflict; re-read, do not guess.",
}
LIMIT: Schema = {
    "type": "integer",
    "minimum": 1,
    "maximum": 200,
    "default": 20,
    "description": "Max items per page (default 20).",
}
CURSOR: Schema = {
    "type": "string",
    "maxLength": 2048,
    "description": "Opaque next_cursor from the previous page. Omit for the first page; keep other params unchanged.",
}
WORKSPACE: Schema = {
    "type": "string",
    "pattern": FIELD_ID,
    "description": "Workspace id. Omit to use the session's default workspace.",
}
REASON = s("Why, in the user's words. Shown to humans and stored in audit.", maxLength=1000)

OPS: Schema = {
    "type": "object",
    "description": 'Operator object, e.g. {"gte":"5000000"}. Several keys = AND.',
    "properties": {
        "eq": {**SCALAR, "description": "equals"},
        "ne": {**SCALAR, "description": "not equal"},
        "gt": {"type": ["string", "number"], "description": ">"},
        "gte": {"type": ["string", "number"], "description": ">="},
        "lt": {"type": ["string", "number"], "description": "<"},
        "lte": {"type": ["string", "number"], "description": "<="},
        "in": {
            "type": "array",
            "items": {"type": ["string", "number", "boolean"]},
            "maxItems": 100,
            "description": "any of",
        },
        "contains": {"type": "string", "maxLength": 200, "description": "text contains"},
        "is_null": {"type": "boolean", "description": "true = empty"},
    },
    "additionalProperties": False,
    "minProperties": 1,
}
WHERE: Schema = {
    "type": "object",
    "description": 'Filter by field: {"state":"submitted","amount":{"gte":"5000000"},"owner":"me"}. Plain value = equals. All keys AND.',
    "propertyNames": {"pattern": FIELD_ID},
    "additionalProperties": {"anyOf": [SCALAR, OPS]},
    "maxProperties": 20,
}

# ---------------------------------------------------------------- output pieces (open: additionalProperties allowed)
REF_OUT: Schema = {"type": "string"}
NAMED: Schema = {
    "type": "object",
    "properties": {"ref": REF_OUT, "name": {"type": "string"}},
    "required": ["ref", "name"],
}
NAMED_OR_NULL: Schema = {"anyOf": [NAMED, {"type": "null"}]}
NEXT_CURSOR: Schema = {
    "type": ["string", "null"],
    "description": "Pass as cursor to get the next page; null = no more.",
}
TOTAL: Schema = {
    "type": ["integer", "null"],
    "description": "Count you are allowed to see; null if unknown.",
}
TRANSITION_OUT: Schema = {
    "type": "object",
    "properties": {
        "to_state": {"type": "string"},
        "label": {"type": "string"},
        "level": {"enum": ["L0", "L1", "L2", "L3"]},
        "required_fields": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["to_state", "label", "level"],
}
RECORD_SUMMARY: Schema = {
    "type": "object",
    "properties": {
        "ref": REF_OUT,
        "model_id": {"type": "string"},
        "title": {"type": "string"},
        "state": {"type": ["string", "null"]},
        "revision": {"type": "integer"},
        "updated_at": {"type": "string"},
        "values": {
            "type": "object",
            "description": "Field values; PII may be masked as [PII:token].",
        },
    },
    "required": ["ref", "model_id", "title", "state", "revision"],
}
RECORD_FULL: Schema = {
    "type": "object",
    "properties": {
        **RECORD_SUMMARY["properties"],
        "model_revision": {"type": "integer"},
        "owner": NAMED_OR_NULL,
        "created_at": {"type": "string"},
        "created_by": {
            "type": "object",
            "properties": {
                "ref": REF_OUT,
                "name": {"type": "string"},
                "kind": {"enum": ["human", "agent", "system"]},
            },
        },
        "authored_by_kind": {
            "enum": ["human", "agent", "system"],
            "description": "Who wrote the free text in this record.",
        },
        "available_transitions": {"type": "array", "items": TRANSITION_OUT},
        "attachments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "ref": REF_OUT,
                    "name": {"type": "string"},
                    "size_bytes": {"type": "integer"},
                    "content_type": {"type": "string"},
                    "scan_status": {"type": "string"},
                },
                "required": ["ref", "name"],
            },
        },
    },
    "required": [
        "ref",
        "model_id",
        "title",
        "state",
        "revision",
        "model_revision",
        "values",
        "available_transitions",
    ],
}
SUGGESTED_NEXT: Schema = {
    "type": "array",
    "description": "Server hints for the usual next step; optional to follow.",
    "items": {
        "type": "object",
        "properties": {
            "tool": {"type": "string"},
            "why": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "why"],
    },
}


def obj(
    props: Schema,
    required: tuple[str, ...] | list[str] = (),
    desc: str | None = None,
    strict: bool = True,
) -> Schema:
    o: Schema = {"type": "object", "properties": props, "required": list(required)}
    if strict:
        o["additionalProperties"] = False
    if desc:
        o["description"] = desc
    return o


def inp(props: Schema, required: tuple[str, ...] | list[str] = ()) -> Schema:
    """Tool input schema: flat object, strict, no root combinators."""
    return {
        "$schema": D2020,
        "type": "object",
        "properties": props,
        "required": list(required),
        "additionalProperties": False,
    }


def out(props: Schema, required: tuple[str, ...] | list[str] = ()) -> Schema:
    """Tool output DATA schema: open object (forward compatible)."""
    return {"type": "object", "properties": props, "required": list(required)}


# ---------------------------------------------------------------- model spec pieces (used by describe_model output and propose_model_change input)
FIELD_TYPES = [
    "string",
    "text",
    "integer",
    "decimal",
    "money",
    "boolean",
    "date",
    "datetime",
    "enum",
    "ref",
    "person",
    "file",
]
FIELD_SPEC = obj(
    {
        "id": {
            "type": "string",
            "pattern": FIELD_ID,
            "description": "snake_case field id, e.g. due_date.",
        },
        "label": s("Display label (Vietnamese ok).", maxLength=120),
        "type": {
            "enum": FIELD_TYPES,
            "description": "money/decimal values are decimal strings; ref/person/file hold refs.",
        },
        "required": {"type": "boolean", "description": "Must be set when the record is created."},
        "multiple": {"type": "boolean", "description": "Holds a list of values."},
        "enum_values": {
            "type": "array",
            "items": {"type": "string", "pattern": FIELD_ID},
            "maxItems": 100,
            "description": "Allowed values when type=enum.",
        },
        "ref_model": {
            "type": "string",
            "pattern": MODEL_ID,
            "description": "Target model when type=ref.",
        },
        "min": {"type": ["string", "number"], "description": "Minimum (numbers/dates)."},
        "max": {"type": ["string", "number"], "description": "Maximum (numbers/dates)."},
        "max_length": {
            "type": "integer",
            "minimum": 1,
            "description": "Max characters for string/text.",
        },
        "unique": {"type": "boolean", "description": "No two records may share this value."},
        "pii": {
            "type": "boolean",
            "description": "Personal data. Masked/omitted for LLM clients per tenant egress policy.",
        },
        "sensitive": {
            "type": "boolean",
            "description": "Sensitive personal data (health, ID number, salary...). Implies pii.",
        },
        "impact": {
            "enum": ["normal", "high"],
            "description": "high = changing it affects others; edits need L2.",
        },
        "description": s("Help text for humans and agents.", maxLength=300),
    },
    ["id", "label", "type"],
    "Field definition.",
)
STATE_SPEC = obj(
    {
        "id": {"type": "string", "pattern": FIELD_ID, "description": "State id, e.g. borrowed."},
        "label": s("Display label.", maxLength=120),
        "initial": {
            "type": "boolean",
            "description": "New records start here (exactly one state).",
        },
        "terminal": {"type": "boolean", "description": "No transitions leave this state."},
        "pauses_sla": {"type": "boolean", "description": "SLA clock stops while in this state."},
    },
    ["id", "label"],
    "Lifecycle state.",
)
TRANSITION_SPEC = obj(
    {
        "from": {
            "type": "array",
            "items": {"type": "string", "pattern": FIELD_ID},
            "minItems": 1,
            "description": "States this transition can start from.",
        },
        "to": {
            "type": "string",
            "pattern": FIELD_ID,
            "description": "Target state. One transition per (from,to).",
        },
        "label": s("Verb shown to users, e.g. 'Trả thiết bị'.", maxLength=120),
        "level": {"enum": ["L1", "L2", "L3"], "description": "Autonomy level needed by an agent."},
        "allowed": s("Who may do it: owner|assignee|requester|role:<id>|any.", maxLength=120),
        "required_fields": {
            "type": "array",
            "items": {"type": "string", "pattern": FIELD_ID},
            "description": "Fields that must be set by this transition.",
        },
        "guard_rule": {
            "type": "string",
            "pattern": FIELD_ID,
            "description": "Rule id that must pass.",
        },
        "starts_process": {
            "type": "string",
            "pattern": MODEL_ID,
            "description": "Process started on entry.",
        },
    },
    ["from", "to", "label"],
    "Allowed state change.",
)
RULE_SPEC = obj(
    {
        "id": {"type": "string", "pattern": FIELD_ID, "description": "Rule id."},
        "expr": s(
            "Bounded expression over fields, e.g. 'trip_end >= trip_start'. No code.", maxLength=500
        ),
        "message": s("Error shown on violation (Vietnamese ok).", maxLength=300),
    },
    ["id", "expr", "message"],
    "Validation rule.",
)
METRIC_SPEC = obj(
    {
        "id": {
            "type": "string",
            "pattern": FIELD_ID,
            "description": "Metric name, e.g. advance_total.",
        },
        "label": s("Display label.", maxLength=120),
        "op": {"enum": ["count", "sum", "avg", "min", "max"], "description": "Aggregate function."},
        "field": {
            "type": "string",
            "pattern": FIELD_ID,
            "description": "Field for sum/avg/min/max.",
        },
        "where": WHERE,
    },
    ["id", "label", "op"],
    "Named, approved metric.",
)

MODEL_OUT = out(
    {
        "id": {"type": "string"},
        "label": {"type": "string"},
        "description": {"type": "string"},
        "primitive": {
            "enum": [
                "record",
                "request",
                "task",
                "document",
                "comment",
                "notification",
                "person",
                "file",
            ]
        },
        "revision": {"type": "integer"},
        "title_field": {"type": "string"},
        "fields": {
            "type": "array",
            "items": {**{k: v for k, v in FIELD_SPEC.items() if k != "additionalProperties"}},
        },
        "states": {
            "type": "array",
            "items": {k: v for k, v in STATE_SPEC.items() if k != "additionalProperties"},
        },
        "transitions": {
            "type": "array",
            "items": {k: v for k, v in TRANSITION_SPEC.items() if k != "additionalProperties"},
        },
        "rules": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "message": {"type": "string"}},
            },
        },
        "metrics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "label": {"type": "string"}},
            },
        },
        "process": {
            "type": ["string", "null"],
            "description": "Approval process for request models.",
        },
        "values_schema": {
            "type": "object",
            "description": "JSON Schema 2020-12 the server validates `values` against.",
        },
    },
    ["id", "label", "primitive", "revision", "fields", "states", "transitions"],
)
