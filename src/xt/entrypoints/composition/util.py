# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tiện ích dùng chung: phân tích ref ở bề mặt tool, cursor phân trang có MAC, lỗi engine."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import UUID

from xt.application.model_engine.service import jsonable
from xt.application.tools.context import CallContext
from xt.application.tools.errors import (
    ToolError,
    capability_unavailable,
    invalid_request,
    not_found,
)
from xt.application.tools.ports import Args
from xt.contracts.tools import ErrorCode
from xt.domain.model.errors import ModelError
from xt.domain.process.errors import EngineError, ProcessError, SpecError

if TYPE_CHECKING:
    from xt.entrypoints.composition.runtime import Caller

MAX_OFFSET = 10_000


def engine_call(fn: Callable[[], Any]) -> Any:
    """Dịch lỗi model engine / process engine sang lỗi contract (giữ thông điệp tiếng Việt)."""
    try:
        return fn()
    except ModelError as exc:
        raise ToolError(
            ErrorCode(exc.code),
            exc.message,
            remediation=exc.remediation,
            details=exc.details,
            continuation=exc.continuation,
        ) from exc
    except EngineError:
        raise
    except SpecError as exc:
        raise ToolError(
            ErrorCode.RULE_VIOLATION, exc.message, details={"errors": exc.errors}
        ) from exc
    except ProcessError as exc:
        code = exc.code if exc.code in {e.value for e in ErrorCode} else "invalid_request"
        details: dict[str, Any] = {"reason": exc.reason} if exc.reason else {}
        raise ToolError(ErrorCode(code), exc.message, details=details) from exc


def unavailable(feature: str, version: str = "v0.2") -> NoReturn:
    raise capability_unavailable(
        feature, version, f"Tell the user {feature} is not available in this xT release yet."
    )


def parse_record_ref(ctx: CallContext, ref: str) -> tuple[str, UUID]:
    """`model:uuid` hoặc `xrn:<tenant>:model:uuid`. Tenant khác -> not_found (không lộ tồn tại)."""
    parts = ref.split(":")
    if len(parts) == 4 and parts[0] == "xrn":
        if parts[1] != ctx.tenant:
            raise not_found(ref)
        parts = parts[2:]
    if len(parts) != 2:
        raise invalid_request("Expected a record ref model:id.", "bad_reference")
    try:
        return parts[0], UUID(parts[1])
    except ValueError as exc:
        raise not_found(ref) from exc


def parse_person_ref(caller: Caller, ref: str) -> UUID:
    if ref == "me":
        return caller.person.id
    parts = ref.split(":")
    if len(parts) == 4 and parts[0] == "xrn":
        if parts[1] != caller.alias:
            raise not_found(ref)
        parts = parts[2:]
    if len(parts) != 2 or parts[0] != "person":
        raise invalid_request("Expected a person ref (person:<id> or me).", "bad_reference")
    try:
        return UUID(parts[1])
    except ValueError as exc:
        raise not_found(ref) from exc


@dataclass(frozen=True, slots=True)
class SignedPage:
    """Cursor opaque = offset + phạm vi (tenant, actor, tham số) ký HMAC-SHA256."""

    key: bytes
    scope: str
    cursor: str | None

    def offset(self) -> int:
        if not self.cursor:
            return 0
        try:
            payload, mac = self.cursor.split(".")
            want = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(want, mac):
                raise ValueError("signature")
            obj = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if obj["scope"] != self.scope:
                raise ValueError("scope")
            off = obj["offset"]
            if type(off) is not int or not 0 <= off <= MAX_OFFSET:
                raise ValueError("offset")
            return off
        except (ValueError, KeyError, TypeError, UnicodeError) as exc:
            raise invalid_request(
                "This cursor does not belong to the current request.",
                "cursor_mismatch",
                remediation="Call again without cursor.",
            ) from exc

    def next(self, offset: int) -> str:
        raw = json.dumps({"scope": self.scope, "offset": offset}, separators=(",", ":"))
        payload = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
        mac = hmac.new(self.key, payload.encode(), hashlib.sha256).hexdigest()
        return f"{payload}.{mac}"


def signed_page(key: bytes, ctx: CallContext, args: Args) -> SignedPage:
    scope = {
        "tenant": ctx.tenant,
        "actor": ctx.actor.key,
        "args": {k: v for k, v in args.items() if k != "cursor"},
    }
    digest = hashlib.sha256(
        json.dumps(jsonable(scope), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    cursor = args.get("cursor")
    return SignedPage(key, digest, str(cursor) if cursor else None)
