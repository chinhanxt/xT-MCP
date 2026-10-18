# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-UNI-AGENT-1 trên policy thật của 4 gói có phiếu đề nghị (kho-hang, crm, cham-cong, tai-san).

Agent (`agent:xt-desk-agent`, behalf = người P) chỉ tạo/gửi/chuyển phiếu do CHÍNH P sở hữu; không
bao giờ cho người khác. `request.decide` qua agent chỉ khi P có vai trò duyệt và không phải người
lập (SoD); `record.approve` của agent vẫn bị Deny. Guard chạy với catalog L0 như PEP production
(mức tự chủ/xác nhận của người thuộc dispatcher; `request.decide` là `human_only`).
Chạy từng gói riêng và một document gộp mọi gói của tenant.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.iam.support import ROOT
from xt.application.iam import AccessGuard
from xt.domain.autonomy import RiskLevel
from xt.domain.iam import (
    DEFAULT_ACTION_LEVELS,
    AccessRequest,
    Actor,
    PolicyDocument,
    PolicyEvaluator,
    Reason,
)

TENANT = "binhminh"
PACKAGES = ROOT / "packages"
L0 = {name: RiskLevel.L0 for name in DEFAULT_ACTION_LEVELS}
WRITE = ("record.create", "record.update", "request.submit", "record.transition")

# gói -> (model phiếu, vai trò người lập, vai trò người duyệt)
PACKS: dict[str, tuple[str, str, str]] = {
    "kho-hang": ("stock_request", "employee", "warehouse_manager"),
    "crm": ("de_nghi_giam_gia", "sales", "sales_manager"),
    "cham-cong": ("bang_cong_tuan", "employee", "project_manager"),
    "tai-san": ("phieu_bao_tri", "technician", "facility_manager"),
}


def _doc(pack: str | None) -> dict[str, Any]:
    paths = (
        [PACKAGES / pack / "policies" / "iam.json"]
        if pack
        else sorted(PACKAGES.glob("*/policies/iam.json"))
    )
    statements = [s for p in paths for s in json.loads(p.read_text())["statements"]]
    return {"version": "1", "tenant": TENANT, "statements": statements}


def _guard(pack: str | None, people: dict[str, Actor]) -> AccessGuard:
    ev = PolicyEvaluator(PolicyDocument.model_validate(_doc(pack)))
    return AccessGuard(ev, people, {}, {}, catalog=L0)


def _person(pid: str, *roles: str) -> Actor:
    return Actor(id=pid, tenant=TENANT, roles=roles, department="KHO", limit=0)


def _agent(behalf: str, roles: tuple[str, ...] = ("agent",)) -> Actor:
    return Actor(
        id="agent:xt-desk-agent",
        tenant=TENANT,
        kind="agent",
        roles=roles,
        department="KHO",
        limit=0,
        autonomy=1,
        behalf=behalf,
    )


def _req(actor: Actor, action: str, model: str, owner: str, creator: str | None = None) -> Any:
    rid = model if action == "record.create" else "42"
    res = {"owner": owner, "creator": creator or owner, "department": "KHO"}
    return AccessRequest(actor, action, f"xrn:{TENANT}:{model}:{rid}", res, now=100)


CASES = [(pack, merged) for pack in PACKS for merged in (False, True)]
IDS = [f"{p}-{'merged' if m else 'alone'}" for p, m in CASES]


@pytest.fixture(params=CASES, ids=IDS)
def world(request: pytest.FixtureRequest) -> tuple[AccessGuard, str, dict[str, Actor]]:
    pack, merged = request.param
    model, maker, approver = PACKS[pack]
    people = {
        "p": _person("p", maker),  # người được đại diện, người lập phiếu
        "q": _person("q", maker),  # đồng nghiệp: agent của P không được làm thay
        "m": _person("m", approver),  # người duyệt
        "adm": _person("adm", "admin", approver),  # admin + người duyệt: vẫn không viết hộ Q
    }
    return _guard(None if merged else pack, people), model, people


@pytest.mark.parametrize("action", WRITE)
def test_agent_writes_request_owned_by_represented_person(world: Any, action: str) -> None:
    guard, model, _ = world
    d = guard.evaluate(_req(_agent("p"), action, model, owner="p"))
    assert d.allowed, d


@pytest.mark.parametrize("action", WRITE)
@pytest.mark.parametrize("behalf", ["p", "m", "adm"])
def test_agent_never_writes_request_owned_by_someone_else(
    world: Any, action: str, behalf: str
) -> None:
    guard, model, _ = world
    d = guard.evaluate(_req(_agent(behalf), action, model, owner="q"))
    assert not d.allowed
    assert d.reason is Reason.BEHALF_PERMISSION
    assert d.detail["principal"] == behalf


def test_delegated_human_roles_do_not_bypass_owner(world: Any) -> None:
    """Grant chứa vai trò người (như E13) vẫn bị giao với quyền của P: không viết hộ Q."""
    guard, model, people = world
    roles = ("agent", *people["adm"].roles)
    d = guard.evaluate(_req(_agent("p", roles), "record.create", model, owner="q"))
    assert not d.allowed


def test_agent_decides_only_for_represented_approver(world: Any) -> None:
    guard, model, _ = world
    ok = guard.evaluate(_req(_agent("m"), "request.decide", model, owner="p"))
    assert ok.allowed, ok
    not_approver = guard.evaluate(_req(_agent("q"), "request.decide", model, owner="p"))
    assert not not_approver.allowed
    assert not_approver.reason is Reason.BEHALF_PERMISSION


def test_agent_cannot_decide_own_request(world: Any) -> None:
    guard, model, people = world
    people["m2"] = _person("m2", *people["m"].roles)
    for owner, creator in (("m2", "m2"), ("p", "m2")):
        d = guard.evaluate(_req(_agent("m2"), "request.decide", model, owner, creator))
        assert not d.allowed and d.reason is Reason.SELF_APPROVAL


def test_agent_record_approve_stays_denied(world: Any) -> None:
    guard, model, _ = world
    d = guard.evaluate(_req(_agent("m"), "record.approve", model, owner="p"))
    assert not d.allowed and d.reason is Reason.EXPLICIT_DENY


def test_agent_cannot_act_without_valid_behalf(world: Any) -> None:
    guard, model, _ = world
    for behalf in ("ghost", None):
        d = guard.evaluate(_req(_agent(behalf), "record.create", model, owner="p"))  # type: ignore[arg-type]
        assert d.reason is Reason.INVALID_BEHALF


def test_decide_is_human_only_for_agents() -> None:
    """Với catalog gốc agent không bao giờ tự quyết: dispatcher luôn đòi người xác nhận."""
    assert DEFAULT_ACTION_LEVELS["request.decide"] is RiskLevel.HUMAN_ONLY
    people = {"m": _person("m", "warehouse_manager")}
    ev = PolicyEvaluator(PolicyDocument.model_validate(_doc("kho-hang")))
    guard = AccessGuard(ev, people, {}, {})
    d = guard.evaluate(_req(_agent("m"), "request.decide", "stock_request", owner="p"))
    assert d.reason is Reason.HUMAN_ONLY


def test_agent_statements_have_no_condition_and_no_decide_deny() -> None:
    """Hình dạng chung với `apv.agent-l0-l1`: owner kiểm ở người được đại diện, không ở agent."""
    for pack, (model, _, _) in PACKS.items():
        doc = _doc(pack)
        xrn = f"xrn:{TENANT}:{model}:*"
        agent = [s for s in doc["statements"] if "role:agent" in s["subjects"]]
        allow = [s for s in agent if s["effect"] == "Allow" and xrn in s["resources"]]
        assert any(set(WRITE) | {"request.decide"} <= set(s["actions"]) for s in allow), pack
        assert all(not s["conditions"] for s in allow), pack
        deny = [s for s in agent if s["effect"] == "Deny" and xrn in s["resources"]]
        assert all("request.decide" not in s["actions"] for s in deny), pack
        assert any("record.approve" in s["actions"] for s in deny), pack
