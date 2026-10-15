# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bộ đánh giá policy: Deny > Allow > implicit deny. Thuần, bất biến, an toàn đa luồng.

Chỉ quyết định theo policy + ranh giới tenant/XRN. Các ranh giới còn lại (tự chủ, SoD, receipt,
delegation, đại diện) thuộc PEP ở `xt.application.iam.guard`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from pydantic import JsonValue

from xt.domain.iam.decision import (
    AccessRequest,
    ConditionTrace,
    Decision,
    Explanation,
    Reason,
    StatementTrace,
)
from xt.domain.iam.glob import MAX_PATTERN_LEN, Glob
from xt.domain.iam.policy import (
    MAX_SAFE_INT,
    Attr,
    Condition,
    Effect,
    Op,
    PolicyDocument,
    Ref,
    Statement,
)
from xt.domain.iam.subject import Principal
from xt.domain.iam.xrn import parse_xrn

_MISSING: Final = object()
_ROLE_PREFIX = "role:"

_SUBJECT_ATTRS: Mapping[Attr, Callable[[Principal], Any]] = {
    Attr.SUBJECT_ID: lambda p: p.id,
    Attr.SUBJECT_LIMIT: lambda p: p.limit,
    Attr.SUBJECT_TEAM: lambda p: p.team,
    Attr.SUBJECT_DEPARTMENT: lambda p: p.department,
}
_RESOURCE_KEYS: Mapping[Attr, str] = {
    Attr.RESOURCE_OWNER: "owner",
    Attr.RESOURCE_CREATOR: "creator",
    Attr.RESOURCE_AMOUNT: "amount",
    Attr.RESOURCE_TEAM: "team",
    Attr.RESOURCE_DEPARTMENT: "department",
}


def attr_value(attr: Attr, actor: Principal, resource: Mapping[str, Any]) -> Any:
    """Giá trị thuộc tính, hoặc `_MISSING` nếu không có (None coi như không có)."""
    getter = _SUBJECT_ATTRS.get(attr)
    value = getter(actor) if getter else resource.get(_RESOURCE_KEYS[attr], None)
    return _MISSING if value is None else value


def _kind(v: Any) -> str | None:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "num" if abs(v) <= MAX_SAFE_INT else None
    if isinstance(v, float):
        return "num" if math.isfinite(v) else None
    if isinstance(v, str):
        return "str"
    return None


def _compare(op: Op, left: Any, right: Any) -> bool | None:
    """True/False, hoặc None nếu không so sánh được (thiếu, khác kiểu, không hữu hạn)."""
    if left is _MISSING or right is _MISSING:
        return None
    lk = _kind(left)
    if lk is None:
        return None
    if op is Op.IN:
        if not isinstance(right, (list, tuple)):
            return None
        return any(_kind(x) == lk and x == left for x in right)
    if _kind(right) != lk:
        return None
    if op is Op.EQ:
        return bool(left == right)
    if op is Op.NE:
        return bool(left != right)
    if lk != "num":
        return None
    return bool(left <= right) if op is Op.LE else bool(left >= right)


def _json(v: Any) -> JsonValue:
    if v is _MISSING:
        return None
    if isinstance(v, (str, bool, int)) or v is None:
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else str(v)
    if isinstance(v, (list, tuple)):
        return [_json(x) for x in v]
    return f"<{type(v).__name__}>"


@dataclass(frozen=True, slots=True)
class _Cond:
    left: Attr
    op: Op
    right: Attr | Any

    def check(self, actor: Principal, resource: Mapping[str, Any]) -> tuple[bool | None, Any, Any]:
        left = attr_value(self.left, actor, resource)
        right = (
            attr_value(self.right, actor, resource) if isinstance(self.right, Attr) else self.right
        )
        return _compare(self.op, left, right), left, right


@dataclass(frozen=True, slots=True)
class _Rule:
    id: str
    effect: Effect
    any_subject: bool
    ids: frozenset[str]
    roles: frozenset[str]
    actions: tuple[Glob, ...]
    resources: tuple[Glob, ...]
    conditions: tuple[_Cond, ...]

    def subject_ok(self, actor: Principal) -> bool:
        return (
            self.any_subject
            or actor.id in self.ids
            or (bool(self.roles) and not self.roles.isdisjoint(actor.roles))
        )

    def action_ok(self, action: str) -> bool:
        return any(g.matches(action) for g in self.actions)

    def resource_ok(self, xrn: str) -> bool:
        return any(g.matches(xrn) for g in self.resources)

    def conditions_ok(self, actor: Principal, resource: Mapping[str, Any]) -> bool | None:
        """False nếu có predicate sai; None nếu không đánh giá được; True nếu tất cả đúng."""
        indeterminate = False
        for c in self.conditions:
            result = c.check(actor, resource)[0]
            if result is False:
                return False
            if result is None:
                indeterminate = True
        return None if indeterminate else True

    def applies(self, conditions: bool | None) -> bool:
        # Fail-closed: predicate không đánh giá được thì Allow không khớp, Deny vẫn khớp.
        if conditions is True:
            return True
        return conditions is None and self.effect is Effect.DENY


def _compile(s: Statement) -> _Rule:
    cond: list[_Cond] = []
    for c in s.conditions:
        cond.append(_Cond(c.left, c.op, _right(c)))
    return _Rule(
        id=s.id,
        effect=s.effect,
        any_subject="*" in s.subjects,
        ids=frozenset(x for x in s.subjects if x != "*" and not x.startswith(_ROLE_PREFIX)),
        roles=frozenset(x[len(_ROLE_PREFIX) :] for x in s.subjects if x.startswith(_ROLE_PREFIX)),
        actions=tuple(Glob(p) for p in s.actions),
        resources=tuple(Glob(p) for p in s.resources),
        conditions=tuple(cond),
    )


def _right(c: Condition) -> Attr | Any:
    if isinstance(c.right, Ref):
        return c.right.ref
    return tuple(c.right) if isinstance(c.right, list) else c.right


class PolicyEvaluator:
    """Đánh giá một `PolicyDocument` đã validate. Tạo một lần cho mỗi (tenant, revision)."""

    def __init__(self, document: PolicyDocument) -> None:
        self.document = document
        self._rules = tuple(_compile(s) for s in document.statements)

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> PolicyEvaluator:
        """Validate dữ liệu thô (ném `pydantic.ValidationError`, là `ValueError`)."""
        return cls(PolicyDocument.model_validate(data))

    def check_boundary(self, req: AccessRequest) -> Decision | None:
        if len(req.action) > MAX_PATTERN_LEN or len(req.xrn) > MAX_PATTERN_LEN:
            return Decision(
                False,
                Reason.INVALID_XRN if len(req.xrn) > MAX_PATTERN_LEN else Reason.UNKNOWN_ACTION,
            )
        xrn = parse_xrn(req.xrn)
        if xrn is None:
            return Decision(False, Reason.INVALID_XRN)
        tenant = self.document.tenant
        if xrn.tenant != req.actor.tenant or req.actor.tenant != tenant:
            return Decision(
                False,
                Reason.TENANT_BOUNDARY,
                detail={
                    "actor_tenant": req.actor.tenant,
                    "xrn_tenant": xrn.tenant,
                    "policy_tenant": tenant,
                },
            )
        return None

    def evaluate(self, req: AccessRequest) -> Decision:
        """Đường nóng: dừng sớm theo từng statement; vết đầy đủ nằm ở `explain`."""
        blocked = self.check_boundary(req)
        if blocked:
            return blocked
        actor, resource = req.actor, req.resource
        hits: list[_Rule] = []
        for r in self._rules:
            if not (r.subject_ok(actor) and r.action_ok(req.action) and r.resource_ok(req.xrn)):
                continue
            if r.applies(r.conditions_ok(actor, resource)):
                hits.append(r)
        ids = tuple(r.id for r in hits)
        if any(r.effect is Effect.DENY for r in hits):
            return Decision(False, Reason.EXPLICIT_DENY, ids)
        if hits:
            return Decision(True, Reason.ALLOW, ids)
        return Decision(False, Reason.IMPLICIT_DENY)

    def explain(self, req: AccessRequest) -> Explanation:
        """Chẩn đoán không tác dụng phụ, không bao giờ dùng để cấp quyền; cần authorize riêng."""
        blocked = self.check_boundary(req)
        if blocked:
            return Explanation(
                allowed=False,
                reason=blocked.reason.value,
                stage="boundary",
                detail=dict(blocked.detail),
            )
        decision = self.evaluate(req)
        traces = tuple(self._trace(r, req) for r in self._rules)
        near = tuple(
            t.id
            for t in traces
            if t.subject and t.action and t.resource and not t.matched and t.effect == "Allow"
        )
        return Explanation(
            allowed=decision.allowed,
            reason=decision.reason.value,
            stage="policy",
            statements=decision.statements,
            near_misses=near if not decision.allowed else (),
            traces=traces,
        )

    @staticmethod
    def _trace(r: _Rule, req: AccessRequest) -> StatementTrace:
        subject = r.subject_ok(req.actor)
        action = r.action_ok(req.action)
        resource = r.resource_ok(req.xrn)
        details: list[ConditionTrace] = []
        for c in r.conditions:
            result, left, right = c.check(req.actor, req.resource)
            details.append(
                ConditionTrace(
                    field=c.left.value,
                    op=c.op.value,
                    actual=_json(left),
                    expected=_json(right),
                    passed=result,
                    error="missing_or_incomparable" if result is None else None,
                )
            )
        cond = r.conditions_ok(req.actor, req.resource)
        matched = subject and action and resource and r.applies(cond)
        return StatementTrace(
            id=r.id,
            effect=r.effect.value,
            matched=matched,
            subject=subject,
            action=action,
            resource=resource,
            conditions=cond,
            condition_details=tuple(details),
        )


__all__ = ["PolicyEvaluator", "attr_value"]
