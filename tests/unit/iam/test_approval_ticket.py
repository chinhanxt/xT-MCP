# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`approval_ticket` (Phiếu xin duyệt, de-xuat-phe-duyet 0.3.0) trên policy thật của gói, riêng và
gộp mọi gói: người lập chỉ phiếu của mình; trưởng bộ phận cùng phòng + giám đốc quyết định; agent
làm thay đúng người (giao với quyền người đó); người lập không tự duyệt; không ai xóa."""

from __future__ import annotations

import pytest

from tests.unit.iam.test_agent_on_behalf import _agent, _guard, _person, _req
from xt.domain.iam import AccessRequest, Actor, Reason

MODEL = "approval_ticket"
PACK = "de-xuat-phe-duyet"


def _people() -> dict[str, Actor]:
    return {
        "e": _person("e", "employee"),
        "x": _person("x", "employee"),
        "h": _person("h", "dept_head", "employee"),
        "d": Actor(id="d", tenant="binhminh", roles=("director",), department="BGD", limit=0),
        "o": Actor(id="o", tenant="binhminh", roles=("dept_head",), department="KT", limit=0),
    }


@pytest.mark.parametrize("pack", [PACK, None])
def test_employee_creates_and_submits_only_own_ticket(pack: str | None) -> None:
    g = _guard(pack, _people())
    for action in ("record.create", "record.update", "request.submit"):
        assert g.evaluate(_req(_person("e", "employee"), action, MODEL, owner="e")).allowed
        assert not g.evaluate(_req(_person("e", "employee"), action, MODEL, owner="x")).allowed


@pytest.mark.parametrize("pack", [PACK, None])
def test_dept_head_same_department_and_director_decide(pack: str | None) -> None:
    people = _people()
    g = _guard(pack, people)
    assert g.evaluate(_req(people["h"], "request.decide", MODEL, owner="e")).allowed
    assert g.evaluate(_req(people["d"], "request.decide", MODEL, owner="e")).allowed
    other = g.evaluate(_req(people["o"], "request.decide", MODEL, owner="e"))
    assert not other.allowed  # trưởng phòng khác phòng (KT) không duyệt phiếu phòng KHO


@pytest.mark.parametrize("pack", [PACK, None])
def test_creator_never_decides_own_ticket(pack: str | None) -> None:
    people = _people()
    g = _guard(pack, people)
    d = g.evaluate(_req(people["h"], "request.decide", MODEL, owner="h"))
    assert not d.allowed and d.reason is Reason.SELF_APPROVAL
    via_agent = g.evaluate(_req(_agent("h"), "request.decide", MODEL, owner="h"))
    assert not via_agent.allowed and via_agent.reason is Reason.SELF_APPROVAL


@pytest.mark.parametrize("pack", [PACK, None])
def test_agent_on_behalf_follows_the_person(pack: str | None) -> None:
    g = _guard(pack, _people())
    for action in ("record.create", "record.update", "request.submit"):
        assert g.evaluate(_req(_agent("e"), action, MODEL, owner="e")).allowed
        no = g.evaluate(_req(_agent("e"), action, MODEL, owner="x"))
        assert not no.allowed and no.reason is Reason.BEHALF_PERMISSION
    # quyết định qua agent: người được đại diện là trưởng bộ phận (dispatcher còn human_only + OTP)
    assert g.evaluate(_req(_agent("h"), "request.decide", MODEL, owner="e")).allowed


@pytest.mark.parametrize("pack", [PACK, None])
def test_nobody_deletes_or_agent_approves(pack: str | None) -> None:
    people = _people()
    g = _guard(pack, people)
    for who in (people["e"], people["d"], _agent("e")):
        assert not g.evaluate(_req(who, "record.delete", MODEL, owner="e")).allowed
    res = {"owner": "e", "creator": "e", "department": "KHO"}
    approve = AccessRequest(_agent("h"), "record.approve", f"xrn:binhminh:{MODEL}:1", res, now=100)
    assert g.evaluate(approve).reason is Reason.EXPLICIT_DENY
