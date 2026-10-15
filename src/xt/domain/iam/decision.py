# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Yêu cầu, quyết định và giải thích (explain) của IAM."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from xt.domain.iam.subject import Principal


class Reason(StrEnum):
    """Mã lý do; giá trị là hợp đồng ổn định (đi vào audit và `details` của `access_denied`)."""

    ALLOW = "allow"
    DELEGATED_ALLOW = "delegated_allow"
    EXPLICIT_DENY = "explicit_deny"
    IMPLICIT_DENY = "implicit_deny"
    INVALID_ATTRIBUTES = "invalid_attributes"
    INVALID_XRN = "invalid_xrn"
    TENANT_BOUNDARY = "tenant_boundary"
    UNKNOWN_ACTION = "unknown_action"
    INVALID_BEHALF = "invalid_behalf"
    SELF_APPROVAL = "self_approval"
    HUMAN_ONLY = "human_only"
    AUTONOMY_CEILING = "autonomy_ceiling"
    APPROVAL_REQUIRED = "approval_required"
    INVALID_APPROVER = "invalid_approver"
    APPROVAL_BINDING = "approval_binding"
    APPROVER_PERMISSION = "approver_permission"
    INVALID_DELEGATION = "invalid_delegation"
    DELEGATION_SCOPE = "delegation_scope"
    BEHALF_PERMISSION = "behalf_permission"


@dataclass(frozen=True, slots=True)
class AccessRequest:
    """Một lần kiểm quyền. `resource` do host đọc từ DB, không phải input của agent."""

    actor: Principal
    action: str
    xrn: str
    resource: Mapping[str, Any] = field(default_factory=dict)
    revision: int = 1
    approval: str | None = None
    delegation: str | None = None
    #: Giờ epoch giây từ Clock của host; bắt buộc, không mặc định (tránh receipt hết hạn sống lại).
    now: int = field(kw_only=True)


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    reason: Reason
    statements: tuple[str, ...] = ()
    #: Dữ kiện ngắn giải thích tầng PEP (vd trần tự chủ so với mức yêu cầu), không chứa bí mật.
    detail: Mapping[str, JsonValue] = field(default_factory=dict)


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ConditionTrace(_Out):
    field: str
    op: str
    actual: JsonValue = None
    expected: JsonValue = None
    #: `None` = không đánh giá được (thiếu thuộc tính / khác kiểu).
    passed: bool | None
    error: str | None = None


class StatementTrace(_Out):
    id: str
    effect: str
    matched: bool
    subject: bool
    action: bool
    resource: bool
    conditions: bool | None
    condition_details: tuple[ConditionTrace, ...] = ()


class Explanation(_Out):
    """Đầu ra cho `get_history(view="explain")` và `details` của lỗi `access_denied`."""

    allowed: bool
    reason: str
    #: `policy` (đánh giá statement) hoặc tên ranh giới PEP: boundary, sod, autonomy, approval...
    stage: str
    statements: tuple[str, ...] = ()
    detail: dict[str, JsonValue] = Field(default_factory=dict)
    #: Statement cùng khớp subject+action+resource nhưng trượt condition: gợi ý sửa để được phép.
    near_misses: tuple[str, ...] = ()
    traces: tuple[StatementTrace, ...] = ()
