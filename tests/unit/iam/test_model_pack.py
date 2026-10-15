# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Policy thật của `packages/quan-tri-mo-hinh`: agent thay mặt người có vai trò đề xuất được (L1);
duyệt chỉ cho director/it, không bao giờ cho agent; người đề xuất không tự duyệt (SoD)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.iam.support import ROOT, Host, request
from xt.domain.iam import Actor, PolicyDocument, PolicyEvaluator, Reason

T = "binhminh"
DOC = json.loads((ROOT / "packages" / "quan-tri-mo-hinh" / "policies" / "iam.json").read_text())
PROPOSAL = f"xrn:{T}:model_proposal:P-1"
WORKSPACE = f"xrn:{T}:workspace:workspace"


def _human(key: str, *roles: str) -> Actor:
    return Actor(id=key, tenant=T, kind="human", roles=roles, department="KD", autonomy=0)


def _agent(behalf: str, autonomy: int = 1) -> Actor:
    return Actor(
        id="agent:xt-desk-agent",
        tenant=T,
        kind="agent",
        roles=("agent",),
        autonomy=autonomy,
        behalf=behalf,
    )


def _host() -> Host:
    people = {
        p.id: p
        for p in (
            _human("head", "employee", "dept_head"),
            _human("boss", "employee", "director"),
            _human("staff", "employee"),
        )
    }
    ev = PolicyEvaluator(PolicyDocument.model_validate(DOC))
    return Host(ev, actors={**people, "agent:xt-desk-agent": _agent("head")})


@pytest.mark.parametrize("behalf", ["head", "boss"])
def test_agent_on_behalf_of_proposer_role_may_propose(behalf: str) -> None:
    d = _host().evaluate(
        request(actor=_agent(behalf), action="model.propose", xrn=WORKSPACE, resource={})
    )
    assert d.allowed, d


def test_agent_on_behalf_of_user_without_role_cannot_propose() -> None:
    d = _host().evaluate(
        request(actor=_agent("staff"), action="model.propose", xrn=WORKSPACE, resource={})
    )
    assert not d.allowed and d.reason is Reason.BEHALF_PERMISSION


def test_agent_never_executes_approval_even_for_director() -> None:
    h = _host()
    for behalf in ("boss", "head"):
        d = h.evaluate(
            request(
                actor=_agent(behalf, autonomy=3),
                action="request.decide",
                xrn=PROPOSAL,
                resource={"owner": "someone", "creator": "someone"},
            )
        )
        assert not d.allowed and d.reason is Reason.HUMAN_ONLY


@pytest.mark.parametrize(
    ("who", "roles", "ok"),
    [("boss", ("director",), True), ("it", ("it",), True), ("head", ("dept_head",), False)],
)
def test_only_approver_roles_decide(who: str, roles: tuple[str, ...], ok: bool) -> None:
    ev = PolicyEvaluator(PolicyDocument.model_validate(DOC))
    res: dict[str, Any] = {"owner": "head", "creator": "head"}
    d = ev.evaluate(
        request(actor=_human(who, *roles), action="request.decide", xrn=PROPOSAL, resource=res)
    )
    assert d.allowed is ok


def test_proposer_cannot_approve_own_proposal() -> None:
    h = _host()
    h.actors["it"] = _human("it", "it")
    d = h.evaluate(
        request(
            actor=_human("it", "it"),
            action="request.decide",
            xrn=PROPOSAL,
            resource={"owner": "it", "creator": "it"},
        )
    )
    assert not d.allowed and d.reason is Reason.SELF_APPROVAL
