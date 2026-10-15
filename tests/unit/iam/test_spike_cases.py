# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""32 ca của spike IAM (research/spike-iam/cases.py) trên evaluator thật và oracle pycasbin."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import Any

import pytest
from pydantic import ValidationError

from tests.unit.iam.support import (
    ENGINES,
    Host,
    actor,
    condition,
    document,
    receipt,
    request,
    statement,
    with_resource,
)
from xt.application.iam import PolicyApprovalError, PolicyStore
from xt.domain.iam import Decision, PolicyDocument, PolicyEvaluator

BASE = document(
    statement("own", conditions=[condition("resource.owner", "eq", {"ref": "subject.id"})]),
    statement(
        "team",
        subjects=["role:lead"],
        conditions=[condition("resource.team", "eq", {"ref": "subject.team"})],
    ),
    statement(
        "department",
        subjects=["role:manager"],
        conditions=[condition("resource.department", "eq", {"ref": "subject.department"})],
    ),
    statement(
        "approve",
        subjects=["role:approver"],
        actions=["record.approve"],
        conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})],
    ),
    statement(
        "agent",
        subjects=["role:worker"],
        conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})],
    ),
    statement("admin", subjects=["role:iam_admin"], actions=["*"], resources=["*"]),
)
BOSS = actor(id="boss", roles=("approver", "staff"), limit=5000)
ADMIN = actor(id="admin", roles=("iam_admin", "approver"), limit=9000)
BOT = actor(id="bot", kind="agent", roles=("worker",), behalf="boss", limit=5000)
OTHER = {"owner": "bob", "creator": "carol", "amount": 5000, "team": "red", "department": "sales"}


class Env:
    def __init__(self, engine: str) -> None:
        self.factory = ENGINES[engine]

    def evaluator(self, doc: dict[str, Any]) -> PolicyEvaluator:
        return self.factory(PolicyDocument.model_validate(deepcopy(doc)))

    def host(self, doc: dict[str, Any] | None = None, **kw: Any) -> Host:
        return Host(
            self.evaluator(BASE if doc is None else doc),
            actors={"boss": BOSS, "admin": ADMIN, "bot": BOT, "alice": actor()},
            **kw,
        )


def check(
    h: Host, req: Any, allowed: bool, reason: str | None = None, sid: str | None = None
) -> Decision:
    d = h.evaluate(req)
    assert d.allowed is allowed, d
    if reason:
        assert d.reason == reason, d
    if sid:
        assert sid in d.statements, d
    return d


def case_01(env: Env) -> None:
    check(env.host(), request(), True, "allow", "own")


def case_02(env: Env) -> None:
    check(env.host(), request(actor=actor(roles=())), False, "implicit_deny")
    check(env.host(document()), request(), False, "implicit_deny")


def case_03(env: Env) -> None:
    a = statement("allow")
    d = statement("block", effect="Deny", actions=["record.update"])
    for rules in ((a, d), (d, a)):
        check(env.host(document(*rules)), request(), False, "explicit_deny", "block")


def case_04(env: Env) -> None:
    check(env.host(document(statement(actions=["record.u*"]))), request(), True)
    check(env.host(document(statement(actions=["record.r*"]))), request(), False)


def case_05(env: Env) -> None:
    req = request()
    check(env.host(document(statement(resources=["xrn:acme:exp*:4*"]))), req, True)
    ev = env.evaluator(document(statement(actions=[req.action], resources=[req.xrn])))
    for action, xrn in [
        ("record.update.extra.record.update", req.xrn),
        (req.action, req.xrn + "extra" + req.xrn),
        ("Record.update", req.xrn),
    ]:
        d = ev.evaluate(replace(req, action=action, xrn=xrn))
        assert not d.allowed, d


def case_06(env: Env) -> None:
    h = env.host(document(statement(subjects=["*"], actions=["*"], resources=["*"])))
    for xrn, reason in [
        ("xrn:other:expense:42", "tenant_boundary"),
        ("xrn:acme:expense:42:extra", "invalid_xrn"),
        ("xrn:acme:expense:*", "invalid_xrn"),
    ]:
        check(h, request(xrn=xrn), False, reason)
    check(
        h,
        request(actor=actor(tenant="other"), xrn="xrn:other:expense:42"),
        False,
        "tenant_boundary",
    )


def case_07(env: Env) -> None:
    h = env.host()
    req = request()
    check(h, req, True)
    check(h, replace(req, actor=replace(req.actor, roles=())), False)
    check(h, req, True)
    check(h, replace(req, actor=actor(tenant="other")), False, "tenant_boundary")


def case_08(env: Env) -> None:
    h = env.host(
        document(
            statement(conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})])
        )
    )
    req = request()
    check(h, req, True)
    over = with_resource(req, amount=5001)
    check(h, over, False)
    why = h.evaluator.explain(over)
    assert why.reason == "implicit_deny"
    c = why.traces[0].condition_details[0]
    assert (c.field, c.op, c.actual, c.expected, c.passed) == (
        "resource.amount",
        "le",
        5001,
        5000,
        False,
    )


def case_09(env: Env) -> None:
    h = env.host(
        document(
            statement(conditions=[condition("resource.amount", "le", {"ref": "subject.limit"})])
        )
    )
    for bad in (None, "1", True, float("nan"), float("inf")):
        check(h, with_resource(request(), amount=bad), False)
    check(h, request(resource={}), False)


def case_10(env: Env) -> None:
    check(env.host(), request(), True, sid="own")
    check(env.host(), request(resource=OTHER), False)


def case_11(env: Env) -> None:
    h = env.host()
    r = request(actor=actor(roles=("lead",)), resource=OTHER)
    check(h, r, True, sid="team")
    check(h, with_resource(r, team="blue"), False)


def case_12(env: Env) -> None:
    h = env.host()
    r = request(actor=actor(roles=("manager",)), resource=OTHER)
    check(h, r, True, sid="department")
    check(h, with_resource(r, department="hr"), False)


def case_13(env: Env) -> None:
    bot = replace(BOT, autonomy=0, behalf="alice")
    h = env.host()
    check(h, request(actor=bot, action="record.read"), True)
    check(h, request(actor=bot), False, "autonomy_ceiling")


def case_14(env: Env) -> None:
    bot = replace(BOT, autonomy=1, behalf="alice")
    check(env.host(), request(actor=bot, action="record.create"), True)
    check(
        env.host(),
        request(actor=bot, action="record.approve", resource=OTHER),
        False,
        "autonomy_ceiling",
    )


def case_15(env: Env) -> None:
    r = request(
        actor=replace(BOT, autonomy=2), action="record.approve", resource=OTHER, approval="ok"
    )
    check(env.host(), replace(r, approval=None), False, "approval_required")
    h = env.host(approvals={"ok": receipt(r)})
    check(h, r, True)
    check(h, replace(r, revision=2), False, "approval_binding")
    check(h, with_resource(r, amount=1), False, "approval_binding")
    check(h, replace(r, now=200), False, "approval_binding")


def case_16(env: Env) -> None:
    bot = replace(BOT, behalf="admin")
    r = request(actor=bot, action="record.delete", resource=OTHER, approval="ok")
    check(env.host(), replace(r, approval=None), False, "approval_required")
    # Người độc lập đang có quyền thật, không phải cờ boolean.
    check(env.host(approvals={"ok": receipt(r, by="boss")}), r, False, "approver_permission")
    h = env.host(approvals={"ok": receipt(r, by="admin")})
    check(h, r, False, "invalid_approver")  # người được đại diện không tự đóng dấu cho mình
    h.actors["reviewer"] = replace(ADMIN, id="reviewer")
    h.approvals["ok"] = receipt(r, by="reviewer")
    check(h, r, True)
    h.approvals["ok"] = receipt(r, by="bot")
    check(h, r, False, "invalid_approver")


def case_17(env: Env) -> None:
    r = request(actor=BOT, action="record.approve", resource={**OTHER, "creator": "bot"})
    check(env.host(), r, False, "self_approval")


def case_18(env: Env) -> None:
    r = request(actor=BOT, action="record.approve", resource={**OTHER, "creator": "boss"})
    check(env.host(), r, False, "self_approval")
    check(env.host(), replace(r, actor=BOSS), False, "self_approval")


def case_19(env: Env) -> None:
    r = request(
        actor=BOT,
        action="record.approve",
        resource={**OTHER, "amount": 5001},
        approval="ok",
    )
    check(env.host(approvals={"ok": receipt(r)}), r, False, "implicit_deny")
    # Agent bị giới hạn bởi chính nó dù người được đại diện có trần cao hơn.
    bot = replace(BOT, behalf="admin", limit=10)
    r = request(actor=bot, resource={**OTHER, "amount": 11})
    check(env.host(), r, False, "implicit_deny")


def case_20(env: Env) -> None:
    r = request(actor=replace(BOT, behalf="alice"), resource=OTHER)
    check(env.host(), r, False, "behalf_permission")
    check(env.host(), replace(r, actor=replace(BOT, behalf="missing")), False, "invalid_behalf")
    h = env.host()
    h.actors["boss"] = replace(BOSS, tenant="other")
    check(h, request(actor=BOT), False, "invalid_behalf")


GRANT = {
    "by": "boss",
    "to": "alice",
    "absent": True,
    "revoked": False,
    "start": 50,
    "end": 150,
    "action": "record.approve",
    "resource": "xrn:acme:expense:*",
    "limit": 5000,
}


def _grant(**kw: Any) -> Any:
    from xt.domain.iam import DelegationGrant

    return DelegationGrant(**{**GRANT, **kw})


def case_21(env: Env) -> None:
    r = request(action="record.approve", resource=OTHER, delegation="leave")
    h = env.host(delegations={"leave": _grant()})
    check(h, r, True, "delegated_allow", "approve")
    check(h, replace(r, delegation=None), False)


def case_22(env: Env) -> None:
    r = request(action="record.approve", resource=OTHER, delegation="leave")
    h = env.host(delegations={"leave": _grant()})
    for key, val in [
        ("end", 100),
        ("start", 101),
        ("revoked", True),
        ("absent", False),
        ("to", "mallory"),
        ("by", "missing"),
    ]:
        h.delegations["leave"] = _grant(**{key: val})
        check(h, r, False, "invalid_delegation")
    h.delegations["leave"] = _grant()
    h.actors["boss"] = replace(BOSS, tenant="other")
    check(h, r, False, "invalid_delegation")


def case_23(env: Env) -> None:
    r = request(action="record.approve", resource=OTHER, delegation="leave")
    h = env.host(delegations={"leave": _grant()})
    check(h, with_resource(r, amount=5001), False, "delegation_scope")
    check(h, replace(r, action="record.delete"), False, "delegation_scope")
    h.actors["boss"] = replace(BOSS, limit=10)
    check(h, r, False, "implicit_deny")
    deny = statement(
        "recipient-deny", effect="Deny", subjects=["alice"], actions=["record.approve"]
    )
    h = env.host(document(*BASE["statements"], deny), delegations={"leave": _grant()})
    check(h, r, False, "explicit_deny", "recipient-deny")
    h = env.host(delegations={"leave": _grant()})
    check(h, with_resource(r, creator="boss"), False, "self_approval")


def case_24(env: Env) -> None:
    doc = PolicyDocument.model_validate(BASE)
    store = PolicyStore(doc, BOT)
    attempts: list[Callable[[], object]] = [
        store.active,
        lambda: store.approve(BOT),
        lambda: store.approve(actor()),
        lambda: store.approve(replace(ADMIN, tenant="other")),
        lambda: store.approve(replace(ADMIN, id="bot")),
    ]
    for attempt in attempts:
        with pytest.raises(PolicyApprovalError):
            attempt()
    store.approve(ADMIN)
    check(Host(env.factory(store.active()), actors={"alice": actor()}), request(), True)
    store.doc = PolicyDocument.model_validate(
        {
            **BASE,
            "statements": [*BASE["statements"], statement("new", subjects=["*"], actions=["*"])],
        }
    )
    with pytest.raises(PolicyApprovalError):
        store.active()


def case_25(env: Env) -> None:
    docs: list[dict[str, Any]] = []
    d = deepcopy(BASE)
    d["version"] = "999"
    docs.append(d)
    d = deepcopy(BASE)
    d["statements"][0]["condition"] = {"eval": "True"}
    docs.append(d)
    d = deepcopy(BASE)
    d["statements"][0]["conditions"] = [condition("resource.amount", "eval", '__import__("os")')]
    docs.append(d)
    d = deepcopy(BASE)
    d["statements"][0]["effect"] = "Maybe"
    docs.append(d)
    d = deepcopy(BASE)
    d["statements"][0]["conditions"] = [condition("resource.secret", "eq", 1)]
    docs.append(d)
    for d in docs:
        with pytest.raises(ValidationError):
            env.evaluator(d)
    check(env.host(), request(action="unknown.execute"), False, "unknown_action")


def case_26(env: Env) -> None:
    # Deny gắn role thắng Allow gắn id người; Deny có điều kiện chỉ cắn khi điều kiện đúng.
    allow = statement("user-allow", subjects=["alice"])
    deny = statement("role-deny", effect="Deny", subjects=["role:staff"], actions=["record.update"])
    check(env.host(document(allow, deny)), request(), False, "explicit_deny", "role-deny")
    cond = statement(
        "hr-only",
        effect="Deny",
        subjects=["*"],
        conditions=[condition("resource.department", "ne", "sales")],
    )
    h = env.host(document(allow, cond))
    check(h, request(), True, "allow", "user-allow")
    check(h, with_resource(request(), department="hr"), False, "explicit_deny", "hr-only")


def case_27(env: Env) -> None:
    foreign = document(statement(subjects=["*"], actions=["*"], resources=["*"]), tenant="other")
    check(env.host(foreign), request(), False, "tenant_boundary")
    h = env.host(document(statement(subjects=["*"], actions=["*"], resources=["xrn:other:*"])))
    check(h, request(xrn="xrn:other:expense:42"), False, "tenant_boundary")
    check(h, request(), False, "implicit_deny")


def case_28(env: Env) -> None:
    # '*' là glob toàn chuỗi, được vượt ':' và '.'; mọi ký tự khác là literal.
    check(env.host(document(statement(resources=["xrn:acme:*"]))), request(), True)
    check(env.host(document(statement(resources=["xrn:acme:exp.nse:42"]))), request(), False)
    check(env.host(document(statement(resources=["xrn:acme:expense:4"]))), request(), False)
    check(
        env.host(document(statement(actions=["record.*"]))), request(action="record.update"), True
    )
    ev = env.evaluator(document(statement(actions=["record.*"], resources=["*"])))
    assert not ev.evaluate(request(action="recordXupdate")).allowed


def case_29(env: Env) -> None:
    # Id người có dạng "role:staff" không được thừa hưởng statement của role staff.
    ev = env.evaluator(document(statement("staff", subjects=["role:staff"], actions=["*"])))
    spoof = request(actor=actor(id="role:staff", roles=()))
    assert not ev.evaluate(spoof).allowed
    assert not ev.evaluate(request(actor=actor(id="eve", roles=("other",)))).allowed
    assert ev.evaluate(request()).allowed


def case_30(env: Env) -> None:
    bot = replace(BOT, behalf="admin", limit=10)
    r = request(actor=bot, resource={**OTHER, "amount": 11}, delegation="d")
    grant = _grant(by="admin", to="bot", start=0, end=999, action="record.update", resource="*")
    check(env.host(delegations={"d": grant}), r, False, "invalid_delegation")


def case_31(env: Env) -> None:
    r = request(
        actor=replace(BOT, autonomy=2), action="record.approve", resource=OTHER, approval="ok"
    )
    h = env.host(approvals={"ok": receipt(r)})
    h.actors["boss2"] = replace(BOSS, id="boss2")
    check(h, r, True)
    check(h, replace(r, actor=replace(r.actor, behalf="boss2")), False, "approval_binding")


def case_32(env: Env) -> None:
    ev = env.evaluator(BASE)
    bad: list[Any] = []
    req = request()

    def work(r: Any, allowed: bool, sid: str | None) -> None:
        for _ in range(2000):
            try:
                d = ev.evaluate(r)
            except Exception as e:
                bad.append(repr(e))
                return
            if (
                d.allowed is not allowed
                or (sid and sid not in d.statements)
                or (not sid and d.statements)
            ):
                bad.append(d)
                return

    jobs = [
        (req, True, "own"),
        (request(actor=actor(id="zed", roles=()), resource=OTHER), False, None),
    ]
    threads = [threading.Thread(target=work, args=jobs[i % 2]) for i in range(8)]
    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)  # ép xen kẽ luồng
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert not bad, bad[:1]


CASES: dict[str, Callable[[Env], None]] = {
    "01_exact_allow": case_01,
    "02_default_deny": case_02,
    "03_deny_override_order": case_03,
    "04_action_wildcard": case_04,
    "05_resource_wildcard_literal": case_05,
    "06_tenant_and_xrn_boundary": case_06,
    "07_rbac_domain_and_revoke": case_07,
    "08_abac_inclusive_limit": case_08,
    "09_abac_missing_bad_type": case_09,
    "10_own_scope": case_10,
    "11_team_scope": case_11,
    "12_department_scope": case_12,
    "13_agent_L0": case_13,
    "14_agent_L1": case_14,
    "15_agent_L2_human_receipt": case_15,
    "16_agent_L3_human_receipt": case_16,
    "17_agent_self_approval": case_17,
    "18_behalf_self_approval": case_18,
    "19_agent_above_limit": case_19,
    "20_agent_intersection": case_20,
    "21_delegation_valid": case_21,
    "22_delegation_invalid": case_22,
    "23_delegation_attenuation": case_23,
    "24_policy_human_lifecycle": case_24,
    "25_schema_fail_closed": case_25,
    "26_deny_cross_subject_and_conditional": case_26,
    "27_policy_tenant_mismatch": case_27,
    "28_wildcard_semantics": case_28,
    "29_subject_namespace_collision": case_29,
    "30_agent_delegation_cannot_bypass_limit": case_30,
    "31_receipt_bound_to_behalf": case_31,
    "32_concurrent_evaluation_isolated": case_32,
}


def test_case_count() -> None:
    assert len(CASES) == 32


@pytest.mark.parametrize("engine", list(ENGINES))
@pytest.mark.parametrize("case", list(CASES))
def test_spike_case(engine: str, case: str) -> None:
    CASES[case](Env(engine))
