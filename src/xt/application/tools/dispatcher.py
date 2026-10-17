# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Pipeline chung cho mọi tool (tool-contracts-v1 §3.5, architecture-v2 §5.4):

1. Tool có trong catalog; JSON không có khóa trùng; args qua `input_schema` (đóng).
2. IAM + SoD (`AccessGuard`) -> `access_denied` + `explain_ref`, TRƯỚC mọi xác nhận.
3. Cờ năng lực theo mốc (`capability_unavailable`, không giả thành công).
4. Kiểm chéo field của handler; token `[PII:…]` ghi ngược -> `invalid_request` `masked_value`.
5. Ghi, trong MỘT transaction: claim key (insert-or-get, chờ nếu transaction khác đang giữ) ->
   replay / `idempotency_conflict` / `confirmation_required` / `confirmation_declined` -> plan
   (validate model, CAS, rule) -> mức tự chủ: thiếu thì lưu PendingOperation +
   `confirmation_required` có `confirm_url` -> commit + receipt. Lỗi = rollback (nhả claim).
6. Mọi envelope (thành công và lỗi) đi qua làn PII; dữ liệu chuẩn hóa về kiểu JSON gốc và được
   kiểm lại theo `outputSchema` (envelope đóng).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache
from ipaddress import ip_address
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from xt.application.tools.context import Actor, CallContext, PersonRef, iso
from xt.application.tools.errors import (
    ToolError,
    access_denied,
    capability_unavailable,
    continuation,
    invalid_request,
)
from xt.application.tools.handlers import HANDLERS, Handler, ReadHandler, ToolServices, WriteHandler
from xt.application.tools.ports import (
    APP_DISABLED,
    IN_PROGRESS,
    Args,
    CommitResult,
    MutationPlan,
    OperationKey,
    PendingOperation,
    PendingStatus,
    Receipt,
)
from xt.application.tools.safe_log import log_failure
from xt.contracts.tools import (
    BY_NAME,
    CATALOG,
    CONTRACT_VERSION,
    UNIVERSAL_ERRORS,
    ErrorCode,
    ToolContract,
    input_issues,
    success_envelope,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.iam import rank
from xt.domain.identity import (
    IdentityError,
    IdentityProviderUnavailableError,
    InvalidTokenError,
    PrincipalKind,
)

log = logging.getLogger("xt.tools")

PII_TOKEN = re.compile(r"\[PII:[^\]\s]{1,128}\]")
MAX_SAFE_INTEGER = 2**53 - 1

# Tool có tên + schema từ v0.1 nhưng năng lực tới sau (TCV1 §12.7, architecture-v2 §1.3).
GATED_CAPABILITIES: Mapping[str, tuple[str, str]] = {
    "upload_file": (
        "v0.2",
        "Tell the user file upload is not available in this xT release yet; do not pretend a file "
        "was attached. Keep the information as text in a record field if the user agrees.",
    ),
}
# aggregate_records: implemented server-side (D-UNI-AGG-1), no longer gated.

# Lỗi do server tự dựng, không mang dữ liệu doanh nghiệp: được trả khi chưa có ngữ cảnh gọi
# (chưa xác thực/không có tenant), nên không qua làn PII.
STATIC_ERRORS = frozenset(
    {
        ErrorCode.UNAUTHENTICATED,
        ErrorCode.ACCESS_DENIED,
        ErrorCode.INVALID_REQUEST,
        ErrorCode.INTERNAL_ERROR,
    }
)


def _is_loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ToolSettings:
    """Cấu hình bắt buộc từ composition root; không có giá trị mặc định cho URL."""

    confirm_base_url: str
    confirmation_ttl: timedelta = timedelta(minutes=10)
    channels: tuple[str, ...] = ("desk",)
    enabled_capabilities: frozenset[str] = frozenset()
    max_open_pending_per_actor: int = 20
    max_response_bytes: int = 1_048_576
    max_aggregate_groups: int = 1000

    def __post_init__(self) -> None:
        try:
            u = urlsplit(self.confirm_base_url)
            host = u.hostname
        except ValueError:
            raise ValueError("confirm_base_url không phải URL hợp lệ") from None
        if (
            u.scheme not in ("https", "http")
            or not host
            or u.username is not None
            or u.password is not None
            or u.query
            or u.fragment
            or any(c.isspace() for c in self.confirm_base_url)
        ):
            raise ValueError(
                "confirm_base_url phải là URL tuyệt đối của xT Desk (không userinfo/query/fragment)"
            )
        if u.scheme == "http" and not _is_loopback(host):
            raise ValueError("confirm_base_url phải dùng https (http chỉ cho loopback)")
        if self.max_aggregate_groups < 1:
            raise ValueError("max_aggregate_groups phải ≥ 1")
        unknown = self.enabled_capabilities - set(GATED_CAPABILITIES)
        if unknown:
            raise ValueError(f"năng lực không biết: {sorted(unknown)}")
        bad = set(self.channels) - {"desk", "telegram"}
        if bad or "desk" not in self.channels:
            raise ValueError("channels phải gồm 'desk' (và có thể 'telegram')")

    def confirm_url(self, confirmation_id: str) -> str:
        return f"{self.confirm_base_url.rstrip('/')}/desk/confirm/{confirmation_id}"

    def available(self, tool: str) -> bool:
        return tool not in GATED_CAPABILITIES or tool in self.enabled_capabilities


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    """`envelope`: JSON cho model (`content[0].text`); thành công thì cũng là structuredContent."""

    is_error: bool
    envelope: dict[str, Any]
    tool: str | None = None
    # `_meta` riêng của kết quả (vd vé `xt/reveal` của tool app-only): KHÔNG vào content/envelope.
    result_meta: Mapping[str, Any] | None = None


class NotJsonNative(TypeError):
    """Port trả giá trị không chuẩn hóa được về JSON gốc (vi phạm hợp đồng output)."""


def to_json_native(value: Any, path: str = "$") -> Any:
    """Chuẩn hóa tất định: `Decimal` nguyên -> chuỗi số nguyên (tiền là VND nguyên, như JCS của
    lớp danh tính); `date` -> `YYYY-MM-DD`; `datetime` có tz -> RFC 3339 UTC `Z`; `UUID` -> chuỗi.
    `Decimal` lẻ, float không hữu hạn, datetime naive, kiểu lạ -> `NotJsonNative`."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NotJsonNative(f"{path}: float không hữu hạn")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value():
            raise NotJsonNative(f"{path}: Decimal không nguyên (gửi chuỗi thập phân)")
        return str(int(value))
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise NotJsonNative(f"{path}: datetime không có múi giờ")
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise NotJsonNative(f"{path}: khóa không phải chuỗi")
            out[k] = to_json_native(v, f"{path}.{k}")
        return out
    if isinstance(value, list | tuple):
        return [to_json_native(v, f"{path}[{i}]") for i, v in enumerate(value)]
    raise NotJsonNative(f"{path}: kiểu {type(value).__name__}")


def canonical(value: Any) -> str:
    return json.dumps(
        to_json_native(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def plan_digest(tool: str, args: Args, plan: MutationPlan) -> str:
    """Digest của plan do server tính; gắn vào PendingOperation, kiểm lại khi người xác nhận."""
    body = {k: v for k, v in args.items() if k != "idempotency_key"}
    return sha256(
        {
            "tool": tool,
            "arguments": body,
            "target": plan.target_ref,
            "level": plan.level.value,
            "plan": plan.digest_source,
        }
    )


def allowed_errors(contract: ToolContract) -> frozenset[ErrorCode]:
    codes = {ErrorCode(c) for c in contract.errors} | UNIVERSAL_ERRORS
    if ErrorCode.CONFIRMATION_REQUIRED in codes:
        codes.add(ErrorCode.CONFIRMATION_DECLINED)  # lần gọi lại sau khi người từ chối/hết hạn
    if not contract.read_only:
        codes.add(ErrorCode.IDEMPOTENCY_CONFLICT)
    return frozenset(codes)


@cache
def _output_validator(name: str) -> Draft202012Validator:
    schema = success_envelope(BY_NAME[name].output_schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _meta_level(level: RiskLevel) -> str:
    # `meta.effective_level` chỉ có L0..L3; human_only là thao tác ghi cao nhất của thang (L3).
    return "L3" if level is RiskLevel.HUMAN_ONLY else level.value


def _contains_pii_token(value: Any) -> bool:
    if isinstance(value, str):
        return PII_TOKEN.search(value) is not None
    if isinstance(value, Mapping):
        return any(_contains_pii_token(v) for v in value.values())
    if isinstance(value, list | tuple):
        return any(_contains_pii_token(v) for v in value)
    return False


@dataclass(frozen=True, slots=True)
class _Replay:
    receipt: Receipt


@dataclass(frozen=True, slots=True)
class _Committed:
    receipt: Receipt


@dataclass(frozen=True, slots=True)
class _Refuse:
    """Lỗi trả agent sau khi transaction commit (vd đánh dấu PendingOperation hết hạn)."""

    error: ToolError


class ToolDispatcher:
    def __init__(
        self,
        services: ToolServices,
        settings: ToolSettings,
        handlers: Mapping[str, Handler] = HANDLERS,
    ) -> None:
        missing = {c.name for c in CATALOG} - set(handlers)
        if missing:
            raise ValueError(f"thiếu handler cho tool: {sorted(missing)}")
        self.services = services
        self.settings = settings
        self.handlers = handlers

    # ---- lối vào ---------------------------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        ctx: CallContext,
        *,
        duplicate_keys: Sequence[str] = (),
    ) -> ToolOutcome:
        contract = BY_NAME.get(name)
        try:
            if contract is None:
                raise invalid_request(
                    f"Unknown tool {name[:64]!r}.",
                    "wrong_tool",
                    remediation="Use one of the tools from tools/list.",
                    tools=[c.name for c in CATALOG],
                )
            if duplicate_keys:
                raise invalid_request(
                    "The arguments JSON repeats an object key.",
                    "duplicate_key",
                    remediation="Send each field once, then call again.",
                    keys=list(duplicate_keys),
                )
            return self._call(contract, dict(arguments or {}), ctx)
        except ToolError as e:
            return self._error(contract, ctx, e)
        except InvalidTokenError:
            return self._error(
                contract,
                ctx,
                ToolError(ErrorCode.UNAUTHENTICATED, "The access token is no longer active."),
            )
        except IdentityProviderUnavailableError:
            return self._error(
                contract,
                ctx,
                ToolError(
                    ErrorCode.TIMEOUT,
                    "Could not verify the session with the identity provider; nothing was done.",
                    details={"commit_state": "none", "retry_after_seconds": 5},
                ),
            )
        except IdentityError as e:
            log_failure(log, "identity failure", e, trace_id=ctx.trace_id, tool=name)
            return self._error(contract, ctx, _internal())
        except Exception as e:
            log_failure(log, "tool failure", e, trace_id=ctx.trace_id, tool=name)
            return self._error(contract, ctx, _internal())

    def _call(self, contract: ToolContract, args: dict[str, Any], ctx: CallContext) -> ToolOutcome:
        issues = input_issues(contract, args)
        if issues:
            raise invalid_request(
                "Arguments do not match the tool input schema.",
                "schema",
                remediation="Fix the listed arguments (unknown parameters are not allowed).",
                errors=issues,
            )
        handler = self.handlers[contract.name]
        s = self.services
        self._authorize(handler, ctx, args)
        if not self.settings.available(contract.name):
            version, remediation = GATED_CAPABILITIES[contract.name]
            raise capability_unavailable(contract.name, version, remediation)
        handler.precheck(s, ctx, args)
        if isinstance(handler, ReadHandler):
            inbound = dict(s.pii.inbound(ctx, contract.name, args))
            return self._success(contract, ctx, handler.read(s, ctx, inbound), RiskLevel.L0)
        assert isinstance(handler, WriteHandler)
        return self._write(contract, handler, args, ctx)

    def _authorize(self, handler: Handler, ctx: CallContext, args: Args) -> None:
        s = self.services
        decision = s.access.check(ctx, handler.access(s, ctx, args))
        if not decision.allowed and decision.reason == APP_DISABLED:
            # Ứng dụng của chính tenant đang tắt: cổng này chạy TRƯỚC `access_denied` của IAM.
            raise ToolError(
                ErrorCode.CAPABILITY_UNAVAILABLE,
                decision.message,
                remediation="Tell the user the app is disabled; an admin re-enables it with a"
                " proposal + approval (xt pack enable). The data is kept.",
                details={"reason": APP_DISABLED, **dict(decision.details)},
            )
        if not decision.allowed:
            remediation = None
            if decision.explain_ref is None:
                remediation = "Tell the user it is not allowed. Do not retry it another way."
            raise access_denied(
                decision.message or "Not allowed.",
                decision.reason,
                decision.explain_ref,
                remediation=remediation,
                **dict(decision.details),
            )

    # ---- ghi --------------------------------------------------------------------------------

    def _write(
        self, contract: ToolContract, handler: WriteHandler, args: dict[str, Any], ctx: CallContext
    ) -> ToolOutcome:
        s = self.services
        if _contains_pii_token(args):
            raise invalid_request(
                "Masked [PII:…] tokens cannot be written back.",
                "masked_value",
                remediation="Ask the user for the real value, or leave the field unchanged.",
            )
        preview = handler.preview(s, ctx, args)
        if preview is not None:
            return self._success(contract, ctx, preview, RiskLevel.L0)
        key = OperationKey(ctx.tenant, ctx.actor.key, contract.name, args["idempotency_key"])
        fingerprint = sha256(args)

        def tx() -> _Replay | _Committed | _Refuse:
            existing = s.operations.claim(key, fingerprint, ctx.now)
            if existing is IN_PROGRESS:
                raise ToolError(
                    ErrorCode.RATE_LIMITED,
                    "The same action (same idempotency_key) is still running.",
                    remediation="Wait a moment, then retry the same call with the same key.",
                    details={"reason": "idempotency_in_progress", "retry_after_seconds": 2},
                )
            if isinstance(existing, Receipt):
                if existing.fingerprint != fingerprint:
                    raise _idempotency_conflict()
                return _Replay(existing)
            if isinstance(existing, PendingOperation):
                if existing.fingerprint != fingerprint:
                    raise _idempotency_conflict()
                return _Refuse(self._pending_state(existing, ctx.now))
            plan = handler.plan(s, ctx, args)
            level = self._level(contract, plan)
            confirmers = self._gate(ctx, contract, level, plan)
            if confirmers:
                return _Refuse(
                    self._new_pending(
                        contract, args, fingerprint, ctx, key, plan, level, confirmers
                    )
                )
            return _Committed(self._commit(handler, ctx, key, fingerprint, plan, level))

        result = s.transactions.run(tx)
        if isinstance(result, _Refuse):
            raise result.error
        return self._receipt_success(
            contract, ctx, result.receipt, replayed=isinstance(result, _Replay)
        )

    def _commit(
        self,
        handler: WriteHandler,
        ctx: CallContext,
        key: OperationKey,
        fingerprint: str,
        plan: MutationPlan,
        level: RiskLevel,
    ) -> Receipt:
        """Chạy BÊN TRONG transaction đang giữ claim (hoặc khóa PendingOperation)."""
        if level.high_risk:
            ctx.ensure_active(level)  # introspection: token bị thu hồi -> unauthenticated
        s = self.services
        result: CommitResult = handler.commit(s, ctx, plan)
        receipt = Receipt(
            key=key,
            fingerprint=fingerprint,
            operation_ref=result.operation_ref,
            commit_state=result.commit_state,
            effective_level=level,
            data=to_json_native(dict(result.data)),
            committed_at=ctx.now,
        )
        s.operations.save_receipt(receipt)
        return receipt

    def _level(self, contract: ToolContract, plan: MutationPlan) -> RiskLevel:
        if contract.human_only:
            return RiskLevel.HUMAN_ONLY
        level = plan.level
        if level is RiskLevel.HUMAN_ONLY or rank(level) > rank(contract.ceiling):
            # Port tính vượt trần tĩnh của contract: lỗi lập trình, không phải lỗi người dùng.
            raise RuntimeError(f"{contract.name}: effective level {level} > {contract.ceiling}")
        return RiskLevel.L1 if level is RiskLevel.L0 else level

    def _gate(
        self, ctx: CallContext, contract: ToolContract, level: RiskLevel, plan: MutationPlan
    ) -> tuple[PersonRef, ...]:
        """Rỗng = được làm ngay; ngược lại = danh sách người phải xác nhận theo thứ tự.

        Bề mặt tool không có "người trực tiếp" cho L2+ (quyết định M6): actor `human` làm L2+ cũng
        phải qua trang xác nhận; chỉ `ConfirmationService` thực thi thay người.
        """
        actor = ctx.actor
        represented = actor.represented_subject
        if level is RiskLevel.HUMAN_ONLY:
            if not plan.confirmers or plan.confirmers[0].subject is None:
                raise RuntimeError(f"{contract.name}: port phải nêu người được giao bước")
            if represented is None or plan.confirmers[0].subject != represented:
                raise access_denied(
                    "Only the person assigned to this step can decide it.",
                    "not_assigned_approver",
                    None,
                    remediation="Tell the user only the assigned approver can decide this step.",
                )
            return plan.confirmers
        if actor.is_agent:
            ceiling = self.services.access.autonomy_ceiling(ctx)
            if rank(ceiling) < rank(RiskLevel.L1):
                raise access_denied(
                    "This agent has not been granted write access (autonomy L0).",
                    "autonomy_hard_limit",
                    None,
                    remediation="Ask the user to raise the agent's autonomy in xT Desk.",
                    required_level=level.value,
                    ceiling=ceiling.value,
                )
            needs = level is RiskLevel.L3 or rank(level) > rank(ceiling)
        else:
            needs = rank(level) >= rank(RiskLevel.L2)
        if not needs:
            return ()
        if plan.confirmers:
            return plan.confirmers
        person = (
            self.services.directory.person_for_subject(ctx, represented) if represented else None
        )
        if person is None:
            raise access_denied(
                "No human can confirm for this caller (service account, no represented user).",
                "autonomy_hard_limit",
                None,
                remediation="Tell the user this needs a person; a service account cannot confirm.",
                required_level=level.value,
            )
        return (replace(person, subject=represented),)

    def _new_pending(
        self,
        contract: ToolContract,
        args: dict[str, Any],
        fingerprint: str,
        ctx: CallContext,
        key: OperationKey,
        plan: MutationPlan,
        level: RiskLevel,
        confirmers: tuple[PersonRef, ...],
    ) -> ToolError:
        s = self.services
        open_count = s.operations.count_open_pending(ctx.tenant, ctx.actor.key, ctx.now)
        if open_count >= self.settings.max_open_pending_per_actor:
            raise ToolError(
                ErrorCode.RATE_LIMITED,
                "Too many actions are already waiting for confirmation.",
                details={
                    "retry_after_seconds": int(self.settings.confirmation_ttl.total_seconds()),
                    "open_confirmations": open_count,
                },
            )
        op = PendingOperation(
            confirmation_id=s.ids.new_id("cf"),
            key=key,
            tool=contract.name,
            arguments=dict(args),
            fingerprint=fingerprint,
            actor=ctx.actor,
            client_class=ctx.client_class,
            plan_digest=plan_digest(contract.name, args, plan),
            plan_summary=plan.summary,  # bản thật cho trang Desk; bản trả agent qua làn PII
            level=level,
            confirmers=confirmers,
            created_at=ctx.now,
            expires_at=ctx.now + self.settings.confirmation_ttl,
        )
        s.operations.save_pending(op)
        return self._confirmation_required(op)

    def _confirmation_required(self, op: PendingOperation) -> ToolError:
        who = op.next_confirmer or op.confirmers[-1]
        details: dict[str, Any] = {
            "confirmation_id": op.confirmation_id,
            "status": op.status.value,
            "confirm_url": self.settings.confirm_url(op.confirmation_id),
            "confirmer": who.named(),
            "level": op.level.value,
            "expires_at": iso(op.expires_at),
            "plan_digest": op.plan_digest,
            "plan_summary": op.plan_summary,
            "channels": list(self.settings.channels),
        }
        return ToolError(
            ErrorCode.CONFIRMATION_REQUIRED,
            f"Cần {who.name} xác nhận thao tác này trên trang xác nhận của xT.",
            remediation=f"Show confirm_url to the user. After they confirm, call {op.tool} again "
            "with the same arguments and idempotency_key. Never claim it is done before that.",
            details=details,
            continuation=continuation(op.tool, op.arguments),
        )

    def _pending_state(self, op: PendingOperation, now: datetime) -> ToolError:
        if op.status.open and now >= op.expires_at:
            op = replace(op, status=PendingStatus.EXPIRED)
            if not self.services.operations.update_pending(op):
                # Hàng vừa đổi dưới tay (store không giữ đúng hợp đồng khóa): không đoán.
                return ToolError(
                    ErrorCode.RATE_LIMITED,
                    "The confirmation state just changed.",
                    remediation="Retry the same call with the same idempotency_key.",
                    details={"reason": "idempotency_in_progress", "retry_after_seconds": 1},
                )
        if op.status.open:
            return self._confirmation_required(op)
        if op.status is PendingStatus.CONFIRMED:
            # Đã xác nhận nhưng chưa có receipt: trạng thái không nhất quán của kho.
            raise RuntimeError(f"confirmation {op.confirmation_id} confirmed without receipt")
        return ToolError(
            ErrorCode.CONFIRMATION_DECLINED,
            "The confirmation was declined, expired or became invalid.",
            details={"confirmation_id": op.confirmation_id, "status": op.status.value},
        )

    # ---- envelope ---------------------------------------------------------------------------

    def _receipt_success(
        self, contract: ToolContract, ctx: CallContext, receipt: Receipt, *, replayed: bool
    ) -> ToolOutcome:
        meta_receipt = {
            "operation_ref": receipt.operation_ref,
            "idempotency_key": receipt.key.idempotency_key,
            "replayed": replayed,
            "commit_state": receipt.commit_state,
        }
        return self._success(
            contract, ctx, receipt.data, receipt.effective_level, receipt=meta_receipt
        )

    def _success(
        self,
        contract: ToolContract,
        ctx: CallContext,
        data: Any,
        level: RiskLevel,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> ToolOutcome:
        try:
            native = to_json_native(data)
        except NotJsonNative as e:
            log.error(
                "output not JSON-native trace_id=%s tool=%s: %s", ctx.trace_id, contract.name, e
            )
            return self._error(contract, ctx, _internal())
        projected = self.services.pii.outbound(ctx, contract.name, native)
        meta: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": ctx.trace_id,
            "as_of": ctx.now_iso,
            "effective_level": _meta_level(level),
            "pii": projected.meta(),
        }
        if receipt is not None:
            meta["receipt"] = receipt
        envelope = {"ok": True, "untrusted": True, "data": projected.data, "meta": meta}
        problem = next(iter(_output_validator(contract.name).iter_errors(envelope)), None)
        if problem is not None:
            log.error(
                "output contract violation trace_id=%s tool=%s path=%s keyword=%s",
                ctx.trace_id,
                contract.name,
                "/".join(map(str, problem.absolute_path)),
                problem.validator,  # không log message: nó echo giá trị (R2-L3)
            )
            return self._error(contract, ctx, _internal())
        try:
            size = len(canonical(envelope).encode("utf-8"))
        except (NotJsonNative, ValueError) as e:
            log_failure(
                log, "output not serializable", e, trace_id=ctx.trace_id, tool=contract.name
            )
            return self._error(contract, ctx, _internal())
        if size > self.settings.max_response_bytes:
            return self._error(
                contract,
                ctx,
                ToolError(
                    ErrorCode.RESOURCE_CONSTRAINT,
                    "The result is too large to return in one response.",
                    remediation="Narrow the request (where, fields, limit) and use next_cursor.",
                    details={
                        "resource": "transport",
                        "size_bytes": size,
                        "limit_bytes": self.settings.max_response_bytes,
                    },
                ),
            )
        return ToolOutcome(False, envelope, contract.name)

    def _error(self, contract: ToolContract | None, ctx: CallContext, e: ToolError) -> ToolOutcome:
        """Mọi lỗi có ngữ cảnh gọi đều qua làn PII (H2): details/plan_summary/message do port
        dựng có thể echo giá trị thật."""
        name = contract.name if contract else None
        error = e.to_error()
        try:
            error = to_json_native(error)
            # `continuation` là lời gọi gọi lại được (args do agent tự gửi): không qua lưới, nếu
            # không token hóa sẽ làm lần gọi lại hỏng (`masked_value`, audit R2-M2).
            cont = error.pop("continuation", None)
            error = dict(self.services.pii.scrub_error(ctx, name or "", error))
            error.pop("continuation", None)
            if cont is not None:
                error["continuation"] = cont
        except Exception as exc:
            log_failure(log, "pii scrub failed", exc, trace_id=ctx.trace_id, tool=name)
            error = _internal().to_error()
        return self._envelope(contract, ctx.trace_id, e.code, error)

    def reject(self, name: str | None, trace_id: str, e: ToolError) -> ToolOutcome:
        """Lỗi trước khi có ngữ cảnh gọi (chưa xác thực, không có tenant, tool ngoài hồ sơ).
        Chỉ nhận lỗi do server tự dựng (`STATIC_ERRORS`); lỗi mang dữ liệu phải qua `_error`."""
        if e.code not in STATIC_ERRORS:
            raise ValueError(f"reject() chỉ cho lỗi tĩnh, không cho {e.code.value}")
        return self._envelope(BY_NAME.get(name) if name else None, trace_id, e.code, e.to_error())

    def _envelope(
        self,
        contract: ToolContract | None,
        trace_id: str,
        code: ErrorCode,
        error: dict[str, Any],
    ) -> ToolOutcome:
        if contract is not None and code not in allowed_errors(contract):
            log.warning(
                "error code outside contract trace_id=%s tool=%s code=%s",
                trace_id,
                contract.name,
                code.value,
            )
        envelope = {
            "ok": False,
            "untrusted": True,
            "error": error,
            "meta": {"contract_version": CONTRACT_VERSION, "trace_id": trace_id},
        }
        return ToolOutcome(True, envelope, contract.name if contract else None)


def _idempotency_conflict() -> ToolError:
    return ToolError(
        ErrorCode.IDEMPOTENCY_CONFLICT,
        "This idempotency_key was already used with different arguments.",
    )


def _internal() -> ToolError:
    return ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure.")


@dataclass(frozen=True, slots=True)
class ConfirmOutcome:
    """Kết quả cho trang `/desk/confirm/{id}` (Desk backend gọi sau khi kiểm step-up)."""

    status: PendingStatus
    receipt: Receipt | None = None
    error: dict[str, Any] | None = None
    operation: PendingOperation | None = field(default=None, repr=False)


class ConfirmationService:
    """Thực thi PendingOperation khi đúng người xác nhận (C7: trang xác nhận tự thực thi).

    Người gọi (Desk backend) đã kiểm `azp`, `sub`, xác thực tươi, nonce, CSRF. Ở đây, trong MỘT
    transaction có khóa hàng PendingOperation: kiểm hạn, đúng người kế tiếp, kiểm lại IAM của cả
    agent gốc lẫn người xác nhận lúc thực thi (M4), plan lại (CAS/rule), so digest, commit.
    """

    def __init__(self, dispatcher: ToolDispatcher, confirm_client_id: str = "xt-desk") -> None:
        self._d = dispatcher
        self._client_id = confirm_client_id

    def get(self, confirmation_id: str) -> PendingOperation | None:
        return self._d.services.operations.get_pending(confirmation_id)

    def decline(self, confirmation_id: str, subject: str, now: datetime) -> ConfirmOutcome:
        store = self._d.services.operations

        def tx() -> ConfirmOutcome:
            op = self._require(store.lock_pending(confirmation_id), confirmation_id)
            if not op.status.open:
                return ConfirmOutcome(op.status, operation=op)
            nxt = op.next_confirmer
            if nxt is None or nxt.subject != subject:
                return ConfirmOutcome(op.status, error=_wrong_confirmer(), operation=op)
            op = replace(op, status=PendingStatus.DECLINED)
            store.update_pending(op)
            return ConfirmOutcome(op.status, operation=op)

        return self._d.services.transactions.run(tx)

    def confirm(
        self, confirmation_id: str, subject: str, now: datetime, trace_id: str
    ) -> ConfirmOutcome:
        d = self._d
        store = d.services.operations
        failure: list[tuple[PendingOperation, dict[str, Any]]] = []

        def execute() -> ConfirmOutcome:
            op = self._require(store.lock_pending(confirmation_id), confirmation_id)
            if not op.status.open:
                return ConfirmOutcome(op.status, operation=op)
            if now >= op.expires_at:
                op = replace(op, status=PendingStatus.EXPIRED)
                store.update_pending(op)
                return ConfirmOutcome(op.status, operation=op)
            nxt = op.next_confirmer
            if nxt is None or nxt.subject != subject:
                return ConfirmOutcome(op.status, error=_wrong_confirmer(), operation=op)
            approvals = (*op.approvals, subject)
            handler = d.handlers[op.tool]
            assert isinstance(handler, WriteHandler)
            agent_ctx = CallContext(
                actor=op.actor,
                client_class=op.client_class,
                trace_id=trace_id,
                now=now,
                confirmed_by=approvals,
            )
            human_ctx = replace(
                agent_ctx,
                actor=Actor(op.actor.tenant, subject, PrincipalKind.HUMAN, self._client_id, None),
            )
            try:
                # Quyền có thể đã bị thu hồi trong TTL: kiểm lại cho cả hai bên lúc thực thi.
                d._authorize(handler, agent_ctx, op.arguments)
                d._authorize(handler, human_ctx, op.arguments)
                if len(approvals) < len(op.confirmers):
                    op = replace(
                        op, approvals=approvals, status=PendingStatus.AWAITING_SECOND_APPROVER
                    )
                    store.update_pending(op)
                    return ConfirmOutcome(op.status, operation=op)
                plan = handler.plan(d.services, agent_ctx, op.arguments)
                if plan_digest(op.tool, op.arguments, plan) != op.plan_digest:
                    raise ToolError(
                        ErrorCode.REVISION_CONFLICT,
                        "The item changed after the action was prepared; it was not executed.",
                    )
                receipt = d._commit(handler, agent_ctx, op.key, op.fingerprint, plan, op.level)
            except ToolError as e:
                failure.append((replace(op, approvals=approvals), e.to_error()))
                raise
            done = replace(op, approvals=approvals, status=PendingStatus.CONFIRMED)
            store.update_pending(done)
            return ConfirmOutcome(PendingStatus.CONFIRMED, receipt=receipt, operation=done)

        try:
            return d.services.transactions.run(execute)
        except ToolError:
            # Đã rollback (không có mutation); ghi trạng thái vô hiệu ở transaction riêng.
            op, error = failure[-1]

            def invalidate() -> ConfirmOutcome:
                cur = self._require(store.lock_pending(confirmation_id), confirmation_id)
                if not cur.status.open:
                    return ConfirmOutcome(cur.status, operation=cur)
                bad = replace(op, status=PendingStatus.INVALIDATED)
                store.update_pending(bad)
                return ConfirmOutcome(bad.status, error=error, operation=bad)

            return d.services.transactions.run(invalidate)

    @staticmethod
    def _require(op: PendingOperation | None, confirmation_id: str) -> PendingOperation:
        if op is None:
            raise KeyError(confirmation_id)
        return op


def _wrong_confirmer() -> dict[str, Any]:
    return ToolError(
        ErrorCode.ACCESS_DENIED,
        "You are not the person who must confirm this action.",
        details={"reason": "not_assigned_approver"},
    ).to_error()


__all__ = [
    "GATED_CAPABILITIES",
    "ConfirmOutcome",
    "ConfirmationService",
    "NotJsonNative",
    "ToolDispatcher",
    "ToolOutcome",
    "ToolSettings",
    "allowed_errors",
    "canonical",
    "plan_digest",
    "to_json_native",
]
