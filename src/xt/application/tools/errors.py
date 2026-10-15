# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`ToolError`: lỗi có kiểu theo contract §8. Port (model engine, records, process...) ném lỗi này;
dispatcher chiếu thành envelope lỗi. `retryable` suy từ mã, không do nơi ném tự chọn."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from xt.contracts.tools import GUIDANCE, RETRYABLE, ErrorCode

# Remediation mặc định: bước tiếp theo cho agent, câu ngắn (§8 "in plain words").
_DEFAULT_REMEDIATION: Mapping[ErrorCode, str] = {
    ErrorCode.INVALID_REQUEST: "Fix the arguments as described in details, then call again.",
    ErrorCode.UNAUTHENTICATED: "Ask the user to reconnect / sign in to xT again.",
    ErrorCode.ACCESS_DENIED: "Tell the user it is not allowed; call get_history(ref=explain_ref,"
    " view='explain') to see why. Do not retry with other arguments to get around it.",
    ErrorCode.NOT_FOUND: "Check the ref (search_records/get_inbox/find_people) and ask the user.",
    ErrorCode.REVISION_CONFLICT: "Re-read the item, show the user what changed, then retry with "
    "the new revision and a NEW idempotency_key.",
    ErrorCode.IDEMPOTENCY_CONFLICT: "Use a NEW idempotency_key for a different action; reuse a key "
    "only to retry the exact same call.",
    ErrorCode.RULE_VIOLATION: "Ask the user for values that satisfy the rules in details, then"
    " call again with a NEW idempotency_key.",
    ErrorCode.PRECONDITION_FAILED: "The current state does not allow this; read the item again "
    "(get_record) or use the continuation.",
    ErrorCode.CONFIRMATION_REQUIRED: "Show confirm_url to the user. After they confirm, call the "
    "same tool again with the same arguments and idempotency_key.",
    ErrorCode.CONFIRMATION_DECLINED: "Tell the user it was not confirmed. Only try again if the "
    "user asks, with a NEW idempotency_key.",
    ErrorCode.CAPABILITY_UNAVAILABLE: "Tell the user this feature is not available yet; do not "
    "simulate it.",
    ErrorCode.RESOURCE_CONSTRAINT: "Narrow the request (filters, limit) or use the continuation.",
    ErrorCode.RATE_LIMITED: "Wait details.retry_after_seconds, then retry the same call.",
    ErrorCode.TIMEOUT: "Retry the same call with the same idempotency_key.",
    ErrorCode.INTERNAL_ERROR: "Retry the same call once with the same idempotency_key; if it fails "
    "again, report trace_id to the user.",
}


class ToolError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        remediation: str | None = None,
        details: Mapping[str, Any] | None = None,
        continuation: Mapping[str, Any] | None = None,
        explain_ref: str | None = None,
    ) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message
        self.remediation = remediation or _DEFAULT_REMEDIATION.get(code) or GUIDANCE[code]
        self.details: dict[str, Any] = dict(details or {})
        self.continuation = dict(continuation) if continuation is not None else None
        self.explain_ref = explain_ref

    @property
    def retryable(self) -> bool:
        return RETRYABLE[self.code]

    def to_error(self) -> dict[str, Any]:
        err: dict[str, Any] = {
            "code": self.code.value,
            "message": self.message,
            "retryable": self.retryable,
            "remediation": self.remediation,
            "details": self.details,
        }
        if self.continuation is not None:
            err["continuation"] = self.continuation
        if self.explain_ref is not None:
            err["explain_ref"] = self.explain_ref
        return err


def continuation(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    return {"tool": tool, "arguments": dict(arguments)}


# ---- hàm tạo lỗi hay dùng (port và handler dùng chung) -------------------------------------


def invalid_request(
    message: str,
    reason: str | None = None,
    *,
    remediation: str | None = None,
    continuation: Mapping[str, Any] | None = None,
    **details: Any,
) -> ToolError:
    d = {"reason": reason, **details} if reason else dict(details)
    return ToolError(
        ErrorCode.INVALID_REQUEST,
        message,
        remediation=remediation,
        details=d,
        continuation=continuation,
    )


def not_found(ref: str | None = None) -> ToolError:
    """Không tồn tại HOẶC không được biết là tồn tại: cùng một câu trả lời (§8)."""
    return ToolError(
        ErrorCode.NOT_FOUND,
        "Not found, or you are not allowed to know it exists.",
        details={"ref": ref} if ref else {},
    )


def access_denied(
    message: str,
    reason: str,
    explain_ref: str | None,
    *,
    remediation: str | None = None,
    **details: Any,
) -> ToolError:
    return ToolError(
        ErrorCode.ACCESS_DENIED,
        message,
        remediation=remediation,
        details={"reason": reason, **details},
        explain_ref=explain_ref,
    )


def revision_conflict(current_revision: int, expected_revision: int | None = None) -> ToolError:
    d: dict[str, Any] = {"current_revision": current_revision}
    if expected_revision is not None:
        d["expected_revision"] = expected_revision
    return ToolError(
        ErrorCode.REVISION_CONFLICT,
        "The item changed since you read it.",
        details=d,
    )


def rule_violation(
    violations: Sequence[Mapping[str, str]],
    explain_ref: str,
    message: str | None = None,
    *,
    remediation: str | None = None,
) -> ToolError:
    vs = [dict(v) for v in violations]
    return ToolError(
        ErrorCode.RULE_VIOLATION,
        message or "; ".join(v.get("message", v.get("rule", "")) for v in vs) or "Rule failed.",
        remediation=remediation,
        details={"violations": vs},
        explain_ref=explain_ref,
    )


def precondition_failed(
    message: str,
    reason: str,
    *,
    continuation: Mapping[str, Any] | None = None,
    remediation: str | None = None,
    **details: Any,
) -> ToolError:
    return ToolError(
        ErrorCode.PRECONDITION_FAILED,
        message,
        remediation=remediation,
        details={"reason": reason, **details},
        continuation=continuation,
    )


def capability_unavailable(feature: str, available_in: str, remediation: str) -> ToolError:
    return ToolError(
        ErrorCode.CAPABILITY_UNAVAILABLE,
        f"{feature} is not available in this xT release (planned for {available_in}).",
        remediation=remediation,
        details={"capability": feature, "available_in": available_in},
    )
