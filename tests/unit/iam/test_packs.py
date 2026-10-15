# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Nạp policy thật của 3 workflow pack và chạy lại 42 ca IAM của packs-draft/REPORT.md.

Chạy ở hai chế độ như REPORT: từng pack riêng và một document gộp cả tenant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Any

import pytest

from tests.unit.iam.support import ROOT, Host, receipt, request
from xt.domain.iam import Actor, PolicyDocument, PolicyEvaluator

PACKS_DIR = ROOT / "packages"
PACKS = ("de-xuat-phe-duyet", "giao-viec-sla", "nhan-su-vong-doi")
TENANT = "binhminh"
M = 1_000_000


def _users(pack: str) -> dict[str, dict[str, Any]]:
    path = next((PACKS_DIR / pack / "seed").glob("00_*.json"))
    return {u["key"]: u for u in json.loads(path.read_text())["users"]}


def _actor(u: dict[str, Any], **kw: Any) -> Actor:
    return (
        Actor(
            id=u["key"],
            tenant=TENANT,
            kind=u.get("kind", "human"),
            roles=tuple(u["roles"]),
            team="none",
            department=u["department"],
            limit=u.get("limit_amount", 0),
            autonomy=u.get("autonomy", 3),
            behalf=u.get("behalf"),
        )
        if not kw
        else replace(_actor(u), **kw)
    )


def _doc(pack: str, merged: bool) -> dict[str, Any]:
    if not merged:
        return json.loads((PACKS_DIR / pack / "policies" / "iam.json").read_text())
    docs = [json.loads((PACKS_DIR / p / "policies" / "iam.json").read_text()) for p in PACKS]
    return {
        "version": "1",
        "tenant": TENANT,
        "statements": [s for d in docs for s in d["statements"]],
    }


def _res(
    owner: str, creator: str | None = None, amount: int = 0, dept: str = "KD"
) -> dict[str, Any]:
    return {
        "owner": owner,
        "creator": creator or owner,
        "amount": amount,
        "team": "none",
        "department": dept,
    }


@dataclass(frozen=True)
class Case:
    pack: str
    label: str
    who: str  # khóa người dùng, hoặc "agent:<behalf>:<autonomy>"
    action: str
    type: str
    rid: str
    resource: dict[str, Any]
    expect: str
    stmt: str | None = None
    receipt_by: str | None = None
    foreign_tenant: bool = False


P1, P2, P3 = PACKS
XR = ("proposal", "TU-2026-0042")
R8 = _res("NV-0006", amount=8 * M)
CASES = [
    Case(
        P1,
        "Việt tạo đề xuất của mình",
        "NV-0006",
        "record.create",
        *XR,
        R8,
        "allow",
        "emp-own-create",
    ),
    Case(
        P1,
        "Việt đọc đề xuất của Anh",
        "NV-0006",
        "record.read",
        "proposal",
        "TU-2026-0050",
        _res("NV-0007"),
        "implicit_deny",
    ),
    Case(P1, "Việt tự duyệt", "NV-0006", "record.approve", *XR, R8, "self_approval"),
    Case(
        P1,
        "Mai duyệt đề xuất của Việt",
        "NV-0005",
        "record.approve",
        *XR,
        R8,
        "allow",
        "dept-head-approve",
    ),
    Case(
        P1,
        "Mai tự duyệt đề xuất của mình",
        "NV-0005",
        "record.approve",
        "proposal",
        "TU-2026-0051",
        _res("NV-0005", amount=8 * M),
        "self_approval",
    ),
    Case(P1, "Long duyệt", "NV-0001", "record.approve", *XR, R8, "allow", "director-read-approve"),
    Case(P1, "Thịnh (sai phòng) duyệt", "NV-0012", "record.approve", *XR, R8, "implicit_deny"),
    Case(
        P1,
        "Hà duyệt thanh toán 80tr",
        "NV-0002",
        "record.approve",
        "proposal",
        "TT-2026-0007",
        _res("NV-0006", amount=80 * M),
        "allow",
        "chief-acct-read-approve",
    ),
    Case(
        P1,
        "Long xóa đề xuất",
        "NV-0001",
        "record.delete",
        *XR,
        R8,
        "explicit_deny",
        "deny-delete-vouchers",
    ),
    Case(
        P1,
        "agent L1 thay Việt tạo nháp",
        "agent:NV-0006:1",
        "record.create",
        *XR,
        R8,
        "allow",
        "agent-l0-l1",
    ),
    Case(
        P1,
        "agent L1 thay Việt đọc đề xuất của Anh",
        "agent:NV-0006:1",
        "record.read",
        "proposal",
        "TU-2026-0050",
        _res("NV-0007"),
        "behalf_permission",
    ),
    Case(
        P1,
        "agent L1 thay Việt tự duyệt",
        "agent:NV-0006:1",
        "record.approve",
        *XR,
        R8,
        "self_approval",
    ),
    Case(
        P1,
        "agent L1 thay Mai duyệt",
        "agent:NV-0005:1",
        "record.approve",
        *XR,
        R8,
        "autonomy_ceiling",
    ),
    Case(
        P1,
        "agent L3 + receipt Long duyệt",
        "agent:NV-0005:3",
        "record.approve",
        *XR,
        R8,
        "explicit_deny",
        "deny-agent-decide",
        receipt_by="NV-0001",
    ),
    Case(
        P1,
        "agent L3 xóa không receipt",
        "agent:NV-0005:3",
        "record.delete",
        *XR,
        R8,
        "approval_required",
    ),
    Case(
        P1,
        "agent L3 xóa, receipt Long",
        "agent:NV-0005:3",
        "record.delete",
        *XR,
        R8,
        "approver_permission",
        receipt_by="NV-0001",
    ),
    Case(
        P1,
        "tenant ankhang vào xrn binhminh",
        "NV-0006",
        "record.read",
        *XR,
        R8,
        "tenant_boundary",
        foreign_tenant=True,
    ),
    Case(
        P2,
        "Việt cập nhật tiến độ",
        "NV-0006",
        "record.update",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "allow",
        "assignee-update",
    ),
    Case(
        P2,
        "Anh cập nhật việc của Việt",
        "NV-0007",
        "record.update",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "implicit_deny",
    ),
    Case(
        P2,
        "Việt tự nghiệm thu",
        "NV-0006",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "self_approval",
    ),
    Case(
        P2,
        "Mai nghiệm thu",
        "NV-0005",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "allow",
        "acceptance",
    ),
    Case(
        P2,
        "Mai nghiệm thu khi creator = người giao",
        "NV-0005",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006", creator="NV-0005"),
        "self_approval",
    ),
    Case(
        P2,
        "Thịnh nghiệm thu việc phòng KD",
        "NV-0012",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "implicit_deny",
    ),
    Case(
        P2,
        "Mai tạo công việc",
        "NV-0005",
        "record.create",
        "task",
        "CV-2026-0100",
        _res("NV-0006", creator="NV-0005"),
        "allow",
        "dept-head-assign",
    ),
    Case(
        P2,
        "agent L1 thay Mai tạo công việc",
        "agent:NV-0005:1",
        "record.create",
        "task",
        "CV-2026-0100",
        _res("NV-0006", creator="NV-0005"),
        "allow",
        "agent-l0-l1",
    ),
    Case(
        P2,
        "agent L1 thay Mai nghiệm thu",
        "agent:NV-0005:1",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "autonomy_ceiling",
    ),
    Case(
        P2,
        "agent L3 + receipt Long nghiệm thu",
        "agent:NV-0005:3",
        "record.approve",
        "task",
        "CV-2026-0001",
        _res("NV-0006"),
        "explicit_deny",
        "deny-agent-acceptance",
        receipt_by="NV-0001",
    ),
    Case(
        P3,
        "Hùng đọc PII của mình",
        "NV-0011",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "allow",
        "emp-own-read",
    ),
    Case(
        P3,
        "Việt đọc PII của Hùng",
        "NV-0006",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "implicit_deny",
    ),
    Case(
        P3,
        "Linh (hr_pii) đọc PII",
        "NV-0010",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "allow",
        "hr-pii",
    ),
    Case(
        P3,
        "Tâm (hr, không hr_pii) đọc PII",
        "NV-0009",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "implicit_deny",
    ),
    Case(
        P3,
        "agent thay Linh (hr_pii) đọc PII: IAM cho phép, làn PII che theo lớp client",
        "agent:NV-0010:1",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "allow",
        "agent-l0-l1",
    ),
    Case(
        P3,
        "agent thay Việt (không hr_pii) đọc PII",
        "agent:NV-0006:1",
        "record.read",
        "employee_pii",
        "NV-0011",
        _res("NV-0011", dept="NS"),
        "behalf_permission",
    ),
    Case(
        P3,
        "agent thay Linh đọc hồ sơ không PII",
        "agent:NV-0010:1",
        "record.read",
        "employee",
        "NV-0015",
        _res("NV-0015"),
        "allow",
        "agent-l0-l1",
    ),
    Case(
        P3,
        "Linh duyệt bước HR",
        "NV-0010",
        "record.approve",
        "lifecycle_case",
        "ON-2026-0002",
        _res("NV-0016", creator="NV-0009"),
        "allow",
        "hr-all",
    ),
    Case(
        P3,
        "Tâm tự duyệt bước HR",
        "NV-0009",
        "record.approve",
        "lifecycle_case",
        "ON-2026-0002",
        _res("NV-0016", creator="NV-0009"),
        "self_approval",
    ),
    Case(
        P3,
        "Vy (it_admin) duyệt bước IT",
        "NV-0013",
        "record.approve",
        "lifecycle_case",
        "ON-2026-0002",
        _res("NV-0016", creator="NV-0009"),
        "allow",
        "it-admin",
    ),
    Case(
        P3,
        "Mai duyệt bước quản lý hồ sơ KD",
        "NV-0005",
        "record.approve",
        "lifecycle_case",
        "ON-2026-0002",
        _res("NV-0016", creator="NV-0009"),
        "allow",
        "dept-head-handover",
    ),
    Case(
        P3,
        "Mai duyệt hồ sơ phòng IT",
        "NV-0005",
        "record.approve",
        "lifecycle_case",
        "ON-2026-0002",
        _res("NV-0014", creator="NV-0009", dept="IT"),
        "implicit_deny",
    ),
    Case(
        P3,
        "Anh tự duyệt hồ sơ nghỉ việc",
        "NV-0007",
        "record.approve",
        "lifecycle_case",
        "OF-2026-0001",
        _res("NV-0007", creator="NV-0009"),
        "self_approval",
    ),
    Case(
        P3,
        "Hà duyệt quyết toán",
        "NV-0002",
        "record.approve",
        "offboarding_settlement",
        "OF-2026-0001",
        _res("NV-0007", creator="NV-0009"),
        "allow",
        "accounting",
    ),
    Case(
        P3,
        "Long xóa hồ sơ nhân viên",
        "NV-0001",
        "record.delete",
        "employee",
        "NV-0007",
        _res("NV-0007"),
        "explicit_deny",
        "deny-delete-case",
    ),
]


def _users_all() -> dict[str, dict[str, Any]]:
    users: dict[str, dict[str, Any]] = {}
    for p in PACKS:
        users.update(_users(p))
    return users


@pytest.mark.parametrize("merged", [False, True], ids=["per-pack", "merged"])
@pytest.mark.parametrize("case", CASES, ids=[c.label for c in CASES])
def test_pack_iam_case(case: Case, merged: bool) -> None:
    users = _users(case.pack)
    humans = {k: _actor(u) for k, u in users.items() if u.get("kind", "human") == "human"}
    if case.who.startswith("agent:"):
        _, behalf, autonomy = case.who.split(":")
        agent_user = {**users["agent-claude"], "behalf": behalf, "autonomy": int(autonomy)}
        who = _actor(agent_user)
        humans[who.id] = who
    else:
        who = humans[case.who]
    if case.foreign_tenant:
        who = replace(who, tenant="ankhang")
    ev = PolicyEvaluator(PolicyDocument.model_validate(_doc(case.pack, merged)))
    req = request(
        actor=who,
        action=case.action,
        xrn=f"xrn:{TENANT}:{case.type}:{case.rid}",
        resource=case.resource,
        approval="rc1" if case.receipt_by else None,
    )
    approvals = {"rc1": receipt(req, by=case.receipt_by)} if case.receipt_by else {}
    d = Host(ev, actors=humans, approvals=approvals).evaluate(req)
    assert d.reason == case.expect, d
    assert d.allowed is (case.expect == "allow")
    if case.stmt:
        assert any(x == case.stmt or x.endswith("." + case.stmt) for x in d.statements), d


def test_case_count() -> None:
    assert len(CASES) == 42


@pytest.mark.parametrize("pack", PACKS)
def test_every_policy_file_loads(pack: str) -> None:
    for path in sorted((PACKS_DIR / pack / "policies").glob("*.json")):
        doc = PolicyDocument.model_validate_json(path.read_text())
        assert doc.tenant == TENANT
        assert doc.statements


def test_merged_document_loads_with_unique_ids() -> None:
    doc = PolicyDocument.model_validate(_doc(P1, merged=True))
    assert len(doc.statements) == len({s.id for s in doc.statements}) >= 31


def test_agent_never_approves_in_any_pack() -> None:
    """Policy của mọi pack có Deny tường minh cho agent trên hành động duyệt: kể cả L3 + receipt."""
    ev = PolicyEvaluator(PolicyDocument.model_validate(_doc(P1, merged=True)))
    users = _users_all()
    for behalf in ("NV-0001", "NV-0002", "NV-0005"):
        agent = _actor({**users["agent-claude"], "behalf": behalf, "autonomy": 3})
        req = request(
            actor=agent,
            action="record.approve",
            xrn=f"xrn:{TENANT}:proposal:TU-1",
            resource=_res("NV-0006", amount=M),
        )
        assert not ev.evaluate(req).allowed
