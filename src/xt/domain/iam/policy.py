# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tài liệu policy IAM: dữ liệu JSON đóng, validate bằng pydantic (`extra=forbid`).

Agent đề xuất policy dưới dạng dữ liệu; mọi khóa lạ, toán tử lạ, thuộc tính ngoài whitelist, literal
không hữu hạn đều bị từ chối (fail-closed). Không có biểu thức, không `eval`.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from xt.domain.iam.glob import MAX_PATTERN_LEN, MAX_STARS

MAX_STATEMENTS = 2000
MAX_PATTERNS = 32
MAX_CONDITIONS = 16
MAX_IN_VALUES = 64
#: Số nguyên lớn hơn ngưỡng này không còn so sánh chính xác với float và dễ gây tràn: bị từ chối.
MAX_SAFE_INT = 2**53


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError(f"khóa JSON trùng: {k!r}")
        out[k] = v
    return out


def _reject_constant(name: str) -> Any:
    raise ValueError(f"hằng JSON không hợp lệ: {name}")


def loads_policy_json(raw: str | bytes | bytearray) -> Any:
    """Bộ nạp JSON duy nhất cho policy: người duyệt và evaluator thấy cùng một nội dung."""
    return json.loads(raw, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)


class Effect(StrEnum):
    ALLOW = "Allow"
    DENY = "Deny"


class Attr(StrEnum):
    """Thuộc tính whitelist mà condition được tham chiếu."""

    SUBJECT_ID = "subject.id"
    SUBJECT_LIMIT = "subject.limit"
    SUBJECT_TEAM = "subject.team"
    SUBJECT_DEPARTMENT = "subject.department"
    RESOURCE_OWNER = "resource.owner"
    RESOURCE_CREATOR = "resource.creator"
    RESOURCE_AMOUNT = "resource.amount"
    RESOURCE_TEAM = "resource.team"
    RESOURCE_DEPARTMENT = "resource.department"


NUMERIC_ATTRS = frozenset({Attr.SUBJECT_LIMIT, Attr.RESOURCE_AMOUNT})


class Op(StrEnum):
    EQ = "eq"
    NE = "ne"
    LE = "le"
    GE = "ge"
    IN = "in"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Ref(_Strict):
    """Vế phải lấy từ một thuộc tính khác, ví dụ `{"ref": "subject.id"}`."""

    ref: Attr


Text = Annotated[StrictStr, Field(max_length=256)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
SafeInt = Annotated[StrictInt, Field(ge=-MAX_SAFE_INT, le=MAX_SAFE_INT)]
Scalar = Text | StrictBool | SafeInt | Number


class Condition(_Strict):
    """Một predicate ABAC `left op right`. Thiếu thuộc tính hoặc khác kiểu thì không thể đánh giá:
    Allow không khớp, Deny vẫn khớp (fail-closed)."""

    left: Attr
    op: Op
    right: Ref | Annotated[list[Scalar], Field(min_length=1, max_length=MAX_IN_VALUES)] | Scalar

    @model_validator(mode="after")
    def _check_shape(self) -> Self:
        right = self.right
        if self.op is Op.IN:
            if not isinstance(right, list):
                raise ValueError("op 'in' cần danh sách literal ở vế phải")
            return self
        if isinstance(right, list):
            raise ValueError("danh sách chỉ dùng với op 'in'")
        if self.op in (Op.LE, Op.GE):
            if self.left not in NUMERIC_ATTRS:
                raise ValueError(f"op '{self.op}' chỉ áp dụng cho thuộc tính số")
            if isinstance(right, (str, bool)):
                raise ValueError(f"op '{self.op}' cần vế phải là số hoặc ref")
            if isinstance(right, Ref) and right.ref not in NUMERIC_ATTRS:
                raise ValueError(f"op '{self.op}' cần ref tới thuộc tính số")
        return self


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_ROLE = re.compile(r"role:[A-Za-z0-9_.-]{1,64}")
_PATTERN = re.compile(rf"[A-Za-z0-9_.:*-]{{1,{MAX_PATTERN_LEN}}}")
_STATEMENT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_TENANT = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _check_pattern(p: str) -> str:
    if not _PATTERN.fullmatch(p):
        raise ValueError(f"pattern không hợp lệ: {p!r}")
    if p.count("*") > MAX_STARS:
        raise ValueError(f"pattern có quá {MAX_STARS} dấu '*'")
    return p


def _check_subject(s: str) -> str:
    # `*` hoặc `role:<tên>` hoặc id thuần (không chứa ':' nên không thể giả mạo namespace role).
    if s == "*" or _ROLE.fullmatch(s) or _ID.fullmatch(s):
        return s
    raise ValueError(f"subject không hợp lệ: {s!r}")


Pattern = Annotated[StrictStr, Field(min_length=1, max_length=MAX_PATTERN_LEN)]


class Statement(_Strict):
    id: StrictStr = Field(min_length=1, max_length=128)
    effect: Effect
    subjects: tuple[Pattern, ...] = Field(min_length=1, max_length=MAX_PATTERNS)
    actions: tuple[Pattern, ...] = Field(min_length=1, max_length=MAX_PATTERNS)
    resources: tuple[Pattern, ...] = Field(min_length=1, max_length=MAX_PATTERNS)
    conditions: tuple[Condition, ...] = Field(default=(), max_length=MAX_CONDITIONS)

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        if not _STATEMENT_ID.fullmatch(self.id):
            raise ValueError(f"id statement không hợp lệ: {self.id!r}")
        for s in self.subjects:
            _check_subject(s)
        for p in (*self.actions, *self.resources):
            _check_pattern(p)
        return self


class PolicyDocument(_Strict):
    """`{version, tenant, statements[]}`; Deny > Allow > implicit deny."""

    version: Literal["1"]
    tenant: StrictStr
    statements: tuple[Statement, ...] = Field(max_length=MAX_STATEMENTS)

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **kwargs: Any) -> Self:
        """Luôn qua `loads_policy_json`: từ chối khóa JSON trùng và hằng NaN/Infinity."""
        return cls.model_validate(loads_policy_json(json_data), **kwargs)

    @model_validator(mode="after")
    def _check_document(self) -> Self:
        if not _TENANT.fullmatch(self.tenant):
            raise ValueError(f"tenant không hợp lệ: {self.tenant!r}")
        seen: set[str] = set()
        for s in self.statements:
            if s.id in seen:
                raise ValueError(f"id statement trùng: {s.id!r}")
            seen.add(s.id)
        return self


def policy_json_schema() -> dict[str, Any]:
    """JSON Schema 2020-12 của policy document (để agent đề xuất policy dưới dạng dữ liệu)."""
    schema = PolicyDocument.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "xT IAM policy document v1"
    return schema


def policy_digest(doc: PolicyDocument) -> str:
    """sha256 của dạng chuẩn hóa: gắn lượt duyệt của người với đúng nội dung này."""
    canonical = json.dumps(
        doc.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
