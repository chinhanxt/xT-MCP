# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""PEP: mọi ranh giới tin cậy của IAM, dùng chung một đường cho mọi tool.

Thứ tự (tool-contracts-v1 §3.5, IAM+SoD chạy trước xác nhận):
thuộc tính -> XRN/tenant -> action -> đại diện -> SoD -> tự chủ/human_only -> receipt ->
policy của chính chủ thể (+ delegation) -> giao với quyền của người được đại diện.
Mọi nhánh lỗi đều fail-closed; mọi từ chối mang `Reason` và `detail` để giải thích.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from pydantic import JsonValue

from xt.application.iam.ports import ApprovalStore, DelegationStore, PrincipalDirectory
from xt.domain.autonomy import RiskLevel
from xt.domain.iam import (
    DEFAULT_ACTION_LEVELS,
    HUMAN,
    AccessRequest,
    ActionCatalog,
    ApprovalBinding,
    Decision,
    Explanation,
    PolicyEvaluator,
    Principal,
    Reason,
    glob_match,
    normalize_resource,
    rank,
)
from xt.domain.iam.policy import MAX_SAFE_INT

_TEXT_KEYS = ("owner", "creator", "team", "department")
_POLICY_STAGE = frozenset(
    {
        Reason.ALLOW,
        Reason.DELEGATED_ALLOW,
        Reason.EXPLICIT_DENY,
        Reason.IMPLICIT_DENY,
        Reason.BEHALF_PERMISSION,
        Reason.APPROVER_PERMISSION,
    }
)
_STAGE: Mapping[Reason, str] = {
    Reason.INVALID_ATTRIBUTES: "attributes",
    Reason.INVALID_XRN: "boundary",
    Reason.TENANT_BOUNDARY: "boundary",
    Reason.UNKNOWN_ACTION: "boundary",
    Reason.INVALID_BEHALF: "behalf",
    Reason.SELF_APPROVAL: "sod",
    Reason.HUMAN_ONLY: "autonomy",
    Reason.AUTONOMY_CEILING: "autonomy",
    Reason.APPROVAL_REQUIRED: "approval",
    Reason.INVALID_APPROVER: "approval",
    Reason.APPROVAL_BINDING: "approval",
    Reason.INVALID_DELEGATION: "delegation",
    Reason.DELEGATION_SCOPE: "delegation",
}
DEFAULT_APPROVAL_ACTIONS = frozenset({"record.approve", "request.decide"})


def _is_number(v: Any) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return abs(v) <= MAX_SAFE_INT
    return isinstance(v, float) and math.isfinite(v)


def _deny(reason: Reason, **detail: JsonValue) -> Decision:
    return Decision(False, reason, detail=detail)


class AccessGuard:
    """Quyết định cuối cùng cho một `AccessRequest`. Không giữ trạng thái thay đổi: an toàn đa luồng
    nếu các kho (`directory`, `approvals`, `delegations`) an toàn đa luồng."""

    def __init__(
        self,
        evaluator: PolicyEvaluator,
        directory: PrincipalDirectory,
        approvals: ApprovalStore,
        delegations: DelegationStore,
        *,
        catalog: ActionCatalog = DEFAULT_ACTION_LEVELS,
        approval_actions: frozenset[str] = DEFAULT_APPROVAL_ACTIONS,
    ) -> None:
        self.evaluator = evaluator
        self._directory = directory
        self._approvals = approvals
        self._delegations = delegations
        self._catalog = catalog
        self._approval_actions = approval_actions

    # ---- thuộc tính bản ghi -------------------------------------------------------------
    def _attributes_ok(self, req: AccessRequest) -> str | None:
        """Tên thuộc tính sai, hoặc None. Khóa vắng mặt thì để policy fail-closed; khóa có mặt
        phải đúng kiểu. Hành động duyệt bắt buộc có owner và creator (cho SoD)."""
        res = req.resource
        if not isinstance(res, Mapping):
            return "resource"
        for k in _TEXT_KEYS:
            if k in res and (not isinstance(res[k], str) or not res[k]):
                return k
        if "amount" in res and (not _is_number(res["amount"]) or res["amount"] < 0):
            return "amount"
        if req.action in self._approval_actions:
            for k in ("owner", "creator"):
                if k not in res:
                    return k
        return None

    # ---- đánh giá ---------------------------------------------------------------------
    def evaluate(self, req: AccessRequest) -> Decision:
        """Không bao giờ ném exception về phía caller: lỗi dữ liệu thành từ chối có giải thích."""
        try:
            return self._evaluate(req)
        except (OverflowError, TypeError, ValueError, ArithmeticError) as e:
            return _deny(Reason.INVALID_ATTRIBUTES, error=type(e).__name__)

    def _evaluate(self, req: AccessRequest) -> Decision:
        if isinstance(req.now, bool) or not isinstance(req.now, int) or req.now <= 0:
            return _deny(Reason.INVALID_ATTRIBUTES, attribute="now")
        if isinstance(req.resource, Mapping):
            try:
                req = replace(req, resource=normalize_resource(req.resource))
            except ValueError as e:
                return _deny(Reason.INVALID_ATTRIBUTES, attribute=str(e))
        bad = self._attributes_ok(req)
        if bad:
            return _deny(Reason.INVALID_ATTRIBUTES, attribute=bad)
        blocked = self.evaluator.check_boundary(req)
        if blocked:
            return blocked
        level = self._catalog.get(req.action)
        if level is None:
            return _deny(Reason.UNKNOWN_ACTION, action=req.action)
        actor = req.actor
        # Fail-closed: chỉ đúng "human" là người; kind lạ ("service", "Agent", "") đi cổng agent.
        is_agent = actor.kind != HUMAN
        represented: Principal | None = None
        if is_agent:
            represented = self._directory.get(actor.behalf) if actor.behalf else None
            if (
                represented is None
                or represented.kind != HUMAN
                or represented.tenant != actor.tenant
            ):
                return _deny(Reason.INVALID_BEHALF, behalf=actor.behalf)
        identities = {actor.id} | ({represented.id} if represented else set())
        involved = {req.resource.get("creator"), req.resource.get("owner")}
        if req.action in self._approval_actions and identities & involved:
            return _deny(Reason.SELF_APPROVAL, identities=list(sorted(identities)))
        if is_agent:
            denied = self._agent_gate(req, level, identities)
            if denied:
                return denied
        direct = self.evaluator.evaluate(req)
        direct = replace(direct, detail={"principal": actor.id})
        if req.delegation and direct.reason is not Reason.EXPLICIT_DENY:
            direct = self._delegate(req, direct)
        if not direct.allowed:
            return direct
        if represented:
            owner = self.evaluator.evaluate(replace(req, actor=represented))
            if not owner.allowed:
                return Decision(
                    False,
                    Reason.BEHALF_PERMISSION,
                    owner.statements,
                    {"principal": represented.id, "owner_reason": owner.reason.value},
                )
        return direct

    def _agent_gate(
        self, req: AccessRequest, level: RiskLevel, identities: set[str]
    ) -> Decision | None:
        actor = req.actor
        if level is RiskLevel.HUMAN_ONLY:
            return _deny(Reason.HUMAN_ONLY, action=req.action)
        ceiling = (
            actor.autonomy
            if isinstance(actor.autonomy, int) and not isinstance(actor.autonomy, bool)
            else 0
        )
        ceiling = min(max(ceiling, 0), 3)
        if rank(level) > ceiling:
            return _deny(Reason.AUTONOMY_CEILING, required_level=level.value, ceiling=f"L{ceiling}")
        if rank(level) < rank(RiskLevel.L2):
            return None
        receipt = self._approvals.get(req.approval) if req.approval else None
        if receipt is None:
            return _deny(Reason.APPROVAL_REQUIRED, required_level=level.value)
        approver = self._directory.get(receipt.by)
        if (
            approver is None
            or approver.kind != HUMAN
            or approver.tenant != actor.tenant
            or approver.id in identities
            or approver.id in (req.resource.get("creator"), req.resource.get("owner"))
        ):
            return _deny(Reason.INVALID_APPROVER, approver=receipt.by)
        wrong = ApprovalBinding.of(req).mismatches(receipt.binding)
        if wrong or receipt.expires <= req.now:
            return _deny(
                Reason.APPROVAL_BINDING,
                mismatched=list(wrong),
                expired=receipt.expires <= req.now,
            )
        # Người duyệt đúng thao tác này cũng phải đang có quyền; không phải cờ boolean.
        authority = self.evaluator.evaluate(replace(req, actor=approver))
        if not authority.allowed:
            return Decision(
                False,
                Reason.APPROVER_PERMISSION,
                authority.statements,
                {"principal": approver.id, "approver_reason": authority.reason.value},
            )
        return None

    def _delegate(self, req: AccessRequest, direct: Decision) -> Decision:
        actor = req.actor
        grant = self._delegations.get(req.delegation or "")
        donor = self._directory.get(grant.by) if grant else None
        # Chỉ người nhận ủy quyền; agent hành động qua behalf + trần tự chủ, không qua ủy quyền.
        if (
            grant is None
            or donor is None
            or actor.kind != HUMAN
            or donor.kind != HUMAN
            or donor.tenant != actor.tenant
            or grant.to != actor.id
            or not grant.absent
            or grant.revoked
            or not grant.start <= req.now < grant.end
        ):
            return _deny(Reason.INVALID_DELEGATION, delegation=req.delegation)
        amount = req.resource.get("amount")
        if (
            req.action != grant.action
            or not glob_match(grant.resource, req.xrn)
            or not isinstance(amount, (int, float))
            or not _is_number(amount)
            or not 0 <= amount <= grant.limit
        ):
            return _deny(
                Reason.DELEGATION_SCOPE,
                action=req.action,
                grant_action=grant.action,
                amount=amount if _is_number(amount) else None,
                limit=grant.limit,
            )
        if req.action in self._approval_actions and donor.id in (
            req.resource.get("creator"),
            req.resource.get("owner"),
        ):
            return _deny(Reason.SELF_APPROVAL, identities=[donor.id])
        delegated = self.evaluator.evaluate(replace(req, actor=donor))
        if delegated.allowed:
            return Decision(
                True,
                Reason.DELEGATED_ALLOW,
                delegated.statements,
                {"principal": donor.id, "delegate": actor.id},
            )
        return direct

    # ---- giải thích -------------------------------------------------------------------
    def explain(self, req: AccessRequest) -> Explanation:
        """Giải thích quyết định: statement khớp, lý do từ chối, predicate actual vs expected.

        Phải được authorize riêng trước khi trả cho người gọi.
        """
        d = self.evaluate(req)
        detail: dict[str, JsonValue] = dict(d.detail)
        if d.reason not in _POLICY_STAGE:
            return Explanation(
                allowed=d.allowed,
                reason=d.reason.value,
                stage=_STAGE.get(d.reason, "policy"),
                statements=d.statements,
                detail=detail,
            )
        who = detail.get("principal")
        if who not in (None, req.actor.id):
            # Người duyệt / người ủy quyền / người được đại diện: không trả vết (lộ hạn mức và thuộc
            # tính của người khác). Chỉ lý do, tầng và statement quyết định.
            return Explanation(
                allowed=d.allowed,
                reason=d.reason.value,
                stage="policy",
                statements=d.statements,
                detail=detail,
            )
        inner = self.evaluator.explain(req)
        return Explanation(
            allowed=d.allowed,
            reason=d.reason.value,
            stage="policy",
            statements=d.statements,
            detail=detail,
            near_misses=inner.near_misses,
            traces=inner.traces,
        )
