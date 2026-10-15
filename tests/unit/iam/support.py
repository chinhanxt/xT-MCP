# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dữ liệu và công cụ dùng chung cho test IAM, kể cả oracle pycasbin (chỉ dev)."""

from __future__ import annotations

import math
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from itertools import product
from pathlib import Path
from typing import Any

import casbin

from xt.application.iam import AccessGuard
from xt.domain.iam import (
    AccessRequest,
    Actor,
    ApprovalBinding,
    ApprovalReceipt,
    Decision,
    DelegationGrant,
    Effect,
    PolicyDocument,
    PolicyEvaluator,
    Reason,
    glob_match,
)
from xt.domain.iam.policy import Attr, Condition, Op, Ref

ROOT = Path(__file__).resolve().parents[3]
MODEL_CONF = Path(__file__).with_name("model.conf")


def actor(**kw: Any) -> Actor:
    base: dict[str, Any] = {
        "id": "alice",
        "tenant": "acme",
        "kind": "human",
        "roles": ("staff",),
        "team": "red",
        "department": "sales",
        "limit": 5000,
        "autonomy": 3,
        "behalf": None,
    }
    return Actor(**{**base, **kw})


def request(**kw: Any) -> AccessRequest:
    base: dict[str, Any] = {
        "actor": actor(),
        "action": "record.update",
        "xrn": "xrn:acme:expense:42",
        "resource": {
            "owner": "alice",
            "creator": "carol",
            "amount": 5000,
            "team": "red",
            "department": "sales",
        },
        "revision": 1,
        "approval": None,
        "delegation": None,
        "now": 100,
    }
    return AccessRequest(**{**base, **kw})


def condition(left: str, op: str, right: Any) -> dict[str, Any]:
    return {"left": left, "op": op, "right": right}


def statement(
    sid: str = "base",
    effect: str = "Allow",
    subjects: list[str] | None = None,
    actions: list[str] | None = None,
    resources: list[str] | None = None,
    conditions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "id": sid,
        "effect": effect,
        "subjects": subjects or ["role:staff"],
        "actions": actions or ["record.*"],
        "resources": resources or ["xrn:acme:*:*"],
        "conditions": conditions or [],
    }


def document(*statements: dict[str, Any], tenant: str = "acme") -> dict[str, Any]:
    return {"version": "1", "tenant": tenant, "statements": list(statements)}


# ---- oracle pycasbin -------------------------------------------------------------------------
def _value(attr: Attr, req: AccessRequest) -> Any:
    area, name = attr.value.split(".")
    v = getattr(req.actor, name, None) if area == "subject" else req.resource.get(name)
    return v


def _kind(v: Any) -> str | None:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)) and math.isfinite(v):
        return "num"
    if isinstance(v, str):
        return "str"
    return None


def _oracle_cond(req: AccessRequest, c: Condition) -> bool | None:
    left = _value(c.left, req)
    right = _value(c.right.ref, req) if isinstance(c.right, Ref) else c.right
    if left is None or right is None or _kind(left) is None:
        return None
    if c.op in (Op.LE, Op.GE) and _kind(left) != "num":
        return None
    if c.op is Op.IN:
        return any(_kind(x) == _kind(left) and x == left for x in right)  # type: ignore[union-attr]
    if _kind(right) != _kind(left):
        return None
    table: dict[Op, Callable[[Any, Any], bool]] = {
        Op.EQ: lambda a, b: a == b,
        Op.NE: lambda a, b: a != b,
        Op.LE: lambda a, b: a <= b,
        Op.GE: lambda a, b: a >= b,
    }
    return table[c.op](left, right)


def _subject_key(subject: str) -> str:
    # User và role ở hai namespace tách biệt trong casbin: id người không thể thành node role.
    return subject if subject == "*" or subject.startswith("role:") else "user:" + subject


class _Call:
    """Yêu cầu và các statement đã khớp trong một lần enforce (không dùng chung giữa luồng)."""

    def __init__(self, req: AccessRequest) -> None:
        self.req = req
        self.hits: list[str] = []


class CasbinOracle(PolicyEvaluator):
    """Oracle vi sai: cùng policy nạp vào pycasbin; gộp effect bằng `some(allow) && !some(deny)`."""

    def __init__(self, document: PolicyDocument) -> None:
        super().__init__(document)
        by_id = {s.id: s for s in document.statements}

        def checks(call: _Call, sid: str) -> bool:
            st = by_id[sid]
            results = [_oracle_cond(call.req, c) for c in st.conditions]
            if any(r is False for r in results):
                return False
            ok = all(r is True for r in results) or (
                st.effect is Effect.DENY and any(r is None for r in results)
            )
            if ok:  # `checks` là vế cuối của matcher: tới đây thì subject/action/resource đã khớp
                call.hits.append(sid)
            return ok

        self.enforcer = casbin.Enforcer(str(MODEL_CONF))
        self.enforcer.add_function("star", glob_match)
        self.enforcer.add_function("checks", checks)
        self.enforcer.enable_auto_save(False)
        self._loaded: dict[tuple[str, str], tuple[str, ...]] = {}
        # Enforcer của casbin ghi trạng thái chung khi đồng bộ role: oracle phải tuần tự hóa.
        self._lock = threading.Lock()
        for s in document.statements:
            for subject, resource, act in product(s.subjects, s.resources, s.actions):
                self.enforcer.add_policy(
                    _subject_key(subject),
                    document.tenant,
                    resource,
                    act,
                    s.id,
                    s.effect.value.lower(),
                )

    def evaluate(self, req: AccessRequest) -> Decision:
        blocked = self.check_boundary(req)
        if blocked:
            return blocked
        with self._lock:
            return self._enforce(req)

    def _enforce(self, req: AccessRequest) -> Decision:
        a = req.actor
        key = (a.tenant, a.id)
        roles = tuple(a.roles)
        if self._loaded.get(key) != roles:
            self.enforcer.remove_filtered_grouping_policy(0, "user:" + a.id, "", a.tenant)
            for role in roles:
                self.enforcer.add_role_for_user_in_domain("user:" + a.id, "role:" + role, a.tenant)
            self._loaded[key] = roles
        call = _Call(req)
        allowed, _ = self.enforcer.enforce_ex("user:" + a.id, a.tenant, req.xrn, req.action, call)
        ids = tuple(dict.fromkeys(call.hits))
        if allowed:
            return Decision(True, Reason.ALLOW, ids)
        if ids:
            return Decision(False, Reason.EXPLICIT_DENY, ids)
        return Decision(False, Reason.IMPLICIT_DENY)


ENGINES: dict[str, Callable[[PolicyDocument], PolicyEvaluator]] = {
    "native": PolicyEvaluator,
    "casbin": CasbinOracle,
}


# ---- host giả lập ------------------------------------------------------------------------------
@dataclass
class Host:
    """Gom guard với các kho dict để test sửa trực tiếp (thu hồi quyền, đổi receipt...)."""

    evaluator: PolicyEvaluator
    actors: dict[str, Actor] = field(default_factory=dict)
    approvals: dict[str, ApprovalReceipt] = field(default_factory=dict)
    delegations: dict[str, DelegationGrant] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.guard = AccessGuard(self.evaluator, self.actors, self.approvals, self.delegations)  # type: ignore[arg-type]

    def evaluate(self, req: AccessRequest) -> Decision:
        return self.guard.evaluate(req)


def receipt(req: AccessRequest, by: str = "admin", expires: int = 200) -> ApprovalReceipt:
    return ApprovalReceipt(by, expires, ApprovalBinding.of(req))


def with_resource(req: AccessRequest, **changes: Any) -> AccessRequest:
    return replace(req, resource={**req.resource, **changes})


Resource = Mapping[str, Any]
