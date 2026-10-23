# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""D-DEMO-LEVELS-1: công ty demo chỉ còn MỘT tài khoản mỗi cấp (giám đốc, kế toán giữ cả
`chief_accountant` + `payment_accountant`, trưởng phòng, nhân sự, MỘT IT, quản lý kho, nhân viên).

Chạy đúng định nghĩa quy trình trong `packages/` (không phải fixture) trên tổ chức in-memory theo
roster đó, cộng người đã tắt (nghỉ) để chứng minh không bao giờ được giao việc."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from tests.unit.process.support import ENGINE, Services
from xt.application.process.memory import InMemoryOrgDirectory
from xt.application.process.runtime import ProcessInstance, ProcessRuntime
from xt.domain.process.assignment import AssigneeResolver
from xt.domain.process.errors import DecisionError, SpecError
from xt.domain.process.spec import Assignee

ROOT = pathlib.Path(__file__).resolve().parents[3]
TRI, OANH, BAO, NHI, KHANG, TRAM, DUNG = (
    "NV-0001", "NV-0004", "NV-0010", "NV-0026", "NV-0031", "NV-0014", "NV-0013",
)  # fmt: skip
QUYEN, VINH, TUNG = "NV-0005", "NV-0032", "NV-0020"  # login đã tắt -> person inactive

OPS: dict[str, list[str]] = {
    "check_overdue_advances": ["requester"], "notify": ["msg"],
    "create_lifecycle_tasks": ["case_id", "kind"], "activate_employee": ["employee_id"],
    "close_lifecycle_case": ["case_id"], "sync_access_grants": ["case_id", "allowed_roles"],
    "check_unreturned_assets": ["employee_id"], "apply_agent_jd": ["jd_id"],
    "post_stock": ["request_id"],
}  # fmt: skip


def roster(**inactive_extra: bool) -> InMemoryOrgDirectory:
    def u(dept: str, roles: list[str], manager: str | None, active: bool = True) -> dict[str, Any]:
        return {"active": active, "department": dept, "roles": roles, "manager": manager}

    users = {
        TRI: u("BGD", ["director", "employee"], None),
        OANH: u("KT", ["dept_head", "chief_accountant", "payment_accountant", "employee"], TRI),
        QUYEN: u("KT", ["payment_accountant", "employee"], OANH, active=False),
        BAO: u("KD", ["dept_head", "sales_manager", "project_manager", "employee"], TRI),
        DUNG: u("KD", ["employee", "sales", "technician"], BAO),
        TRAM: u("KD", ["employee", "warehouse_manager", "sales"], BAO),
        TUNG: u("KD", ["employee"], BAO, active=False),
        NHI: u("NS", ["hr", "employee"], TRI),
        KHANG: u("IT", ["it_admin", "employee"], TRI),
        VINH: u("IT", ["it_admin", "employee"], KHANG, active=False),
    }
    for k, v in inactive_extra.items():
        users[k.replace("_", "-")]["active"] = v
    depts = {"BGD": {"head": TRI}, "KT": {"head": OANH}, "KD": {"head": BAO},
             "NS": {"head": NHI}, "IT": {"head": KHANG}}  # fmt: skip
    return InMemoryOrgDirectory(users, depts)


class Svc(Services):
    def __init__(self) -> None:
        super().__init__()
        for op in ("sync_access_grants", "apply_agent_jd", "post_stock"):
            self[op] = self._rec(op)

    def _rec(self, name: str) -> Any:
        def f(**kw: Any) -> dict[str, Any]:
            self.calls.append((name, kw))
            return {"ok": 1}

        return f


def runtime(pack: str, process: str, org: InMemoryOrgDirectory, svc: Svc) -> ProcessRuntime:
    path = ROOT / "packages" / pack / "processes" / f"{process}.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    return ProcessRuntime(ENGINE.compile(spec, OPS), ENGINE, svc, org, admin_role="admin")


def step(i: ProcessInstance, sid: str) -> dict[str, Any]:
    box = [t for t in i.inbox() if t["step"] == sid]
    assert len(box) == 1, (sid, i.inbox())
    return box[0]


def act(rt: ProcessRuntime, i: ProcessInstance, sid: str, who: str, out: str) -> ProcessInstance:
    i = rt.load(json.loads(json.dumps(i.to_state(), sort_keys=True)))
    i.act(step(i, sid)["task_id"], who, out)
    return i


# ------------------------------------------------------------- tiếp nhận: một IT + giám đốc
def test_onboarding_v8_it_step_offers_director_as_alternate_with_one_it() -> None:
    svc = Svc()
    rt = runtime("nhan-su-vong-doi", "employee_onboarding", roster(), svc)
    i = rt.start({"case_id": "HS-1", "employee_id": "E-1", "employee_user": DUNG}, NHI)
    i = act(rt, i, "hr", NHI, "done")
    it = step(i, "it")
    assert it["candidates"] == [KHANG, TRI]  # Vinh đã nghỉ: không bao giờ được giao
    assert it["via"] == "primary"
    acc = step(i, "accounting")
    assert acc["candidates"] == [OANH]  # Quyên nghỉ; Oanh giữ thêm payment_accountant
    i = act(rt, i, "it", TRI, "done")  # Khang tạo access_grant, giám đốc duyệt bước IT
    sync = [kw for n, kw in svc.calls if n == "sync_access_grants"]
    assert len(sync) == 1 and sync[0]["case_id"] == "HS-1"
    i = act(rt, i, "accounting", OANH, "done")
    i = act(rt, i, "manager", BAO, "done")
    assert i.status() == "completed"


def test_onboarding_v8_still_lets_a_second_it_complete_the_it_step() -> None:
    """Add-only: tổ chức có hai IT vẫn chạy như v7 (IT B duyệt grant của IT A)."""
    org = roster(NV_0032=True)  # Vinh còn làm
    rt = runtime("nhan-su-vong-doi", "employee_onboarding", org, Svc())
    i = rt.start({"case_id": "HS-2", "employee_id": "E-2", "employee_user": DUNG}, NHI)
    i = act(rt, i, "hr", NHI, "done")
    assert step(i, "it")["candidates"] == [KHANG, VINH, TRI]
    i = act(rt, i, "it", VINH, "done")
    assert "it" not in {t["step"] for t in i.inbox()}


def test_onboarding_v8_non_candidate_cannot_complete_it_step() -> None:
    rt = runtime("nhan-su-vong-doi", "employee_onboarding", roster(), Svc())
    i = rt.start({"case_id": "HS-3", "employee_id": "E-3", "employee_user": DUNG}, NHI)
    i = act(rt, i, "hr", NHI, "done")
    with pytest.raises(DecisionError) as e:
        i.act(step(i, "it")["task_id"], BAO, "done")
    assert e.value.reason == "not_assigned_approver"


# ------------------------------------------------------------------- JD agent: Khang lập
def test_jd_v2_khang_submits_director_approves() -> None:
    svc = Svc()
    rt = runtime("nhan-su-ai", "duyet_jd_agent", roster(), svc)
    i = rt.start({"jd_id": "jd-1"}, KHANG)
    it = step(i, "it")
    assert it["candidates"] == [TRI]  # Khang bị SoD (người lập), Vinh nghỉ
    with pytest.raises(DecisionError) as e:
        i.act(it["task_id"], KHANG, "approve")
    assert e.value.reason in ("not_assigned_approver", "sod")
    i = act(rt, i, "it", TRI, "approve")
    assert i.status() == "completed"
    assert [n for n, _ in svc.calls if n == "apply_agent_jd"] == ["apply_agent_jd"]


def test_jd_v2_director_submitting_still_goes_to_it() -> None:
    rt = runtime("nhan-su-ai", "duyet_jd_agent", roster(), Svc())
    i = rt.start({"jd_id": "jd-2"}, TRI)
    assert step(i, "it")["candidates"] == [KHANG]  # giám đốc là người đề nghị: bị SoD loại


# ---------------------------------------------------- đề xuất: một kế toán giữ hai vai trò
@pytest.mark.parametrize(
    ("kind", "amount", "overdue", "path"),
    [
        # payment_request > 50tr: kttt (Oanh) rồi ktt -> Oanh bị distinct_from -> giám đốc
        ("payment_request", 60_000_000, 0,
         [("dept_head", BAO, "primary"), ("kttt", OANH, "primary"),
          ("ktt", TRI, "process_fallback"), ("director", TRI, "primary")]),
        ("advance_request", 60_000_000, 1,
         [("dept_head", BAO, "primary"), ("chief_acct_overdue", OANH, "primary"),
          ("ktt", OANH, "primary"), ("director", TRI, "primary")]),
        ("advance_settlement", 12_000_000, 0,
         [("dept_head", BAO, "primary"), ("kttt", OANH, "primary"),
          ("director", TRI, "primary")]),
    ],
)  # fmt: skip
def test_proposal_same_accountant_for_both_roles_completes(
    kind: str, amount: int, overdue: int, path: list[tuple[str, str, str]]
) -> None:
    svc = Svc()
    svc.overdue[DUNG] = overdue
    rt = runtime("de-xuat-phe-duyet", "proposal_approval", roster(), svc)
    i = rt.start({"kind": kind, "amount": amount}, DUNG)
    for sid, who, via in path:
        t = step(i, sid)
        assert who in t["candidates"], (sid, t)
        assert t["via"] == via, (sid, t)
        i = act(rt, i, sid, who, "approve")
    assert i.status() == "completed"


def test_proposal_oanh_cannot_take_ktt_after_kttt() -> None:
    rt = runtime("de-xuat-phe-duyet", "proposal_approval", roster(), Svc())
    i = rt.start({"kind": "payment_request", "amount": 60_000_000}, DUNG)
    i = act(rt, i, "dept_head", BAO, "approve")
    i = act(rt, i, "kttt", OANH, "approve")
    t = step(i, "ktt")
    assert OANH not in t["candidates"]
    with pytest.raises(DecisionError):
        i.act(t["task_id"], OANH, "approve")


def test_requester_never_approves_own_proposal() -> None:
    """requester != approver giữ nguyên: Oanh tự đề nghị -> bước trưởng KT không về chính Oanh."""
    rt = runtime("de-xuat-phe-duyet", "proposal_approval", roster(), Svc())
    i = rt.start({"kind": "advance_request", "amount": 60_000_000}, OANH)
    t = step(i, "dept_head")
    assert t["candidates"] == [TRI]
    i = act(rt, i, "dept_head", TRI, "approve")
    t = step(i, "ktt")
    assert OANH not in t["candidates"] and t["candidates"] == [TRI]


# ------------------------------------------------------------- người đã tắt không được giao
def test_inactive_person_never_role_assignee() -> None:
    org = roster()
    r = AssigneeResolver(org)
    a = r.resolve(Assignee.model_validate({"kind": "role", "role": "payment_accountant"}), {}, {},
                  None)  # fmt: skip
    assert a.candidates == [OANH]
    assert any(QUYEN in x for x in a.trail) or QUYEN not in a.candidates
    org.users[OANH]["active"] = False
    fb = Assignee.model_validate({"kind": "role", "role": "director"})
    a = r.resolve(Assignee.model_validate({"kind": "role", "role": "payment_accountant"}), {}, {},
                  fb)  # fmt: skip
    assert (a.candidates, a.via) == ([TRI], "process_fallback")


def test_inactive_department_head_falls_through_to_manager() -> None:
    org = roster()
    org.users[BAO]["active"] = False
    rt = runtime("de-xuat-phe-duyet", "approval_ticket_approval", org, Svc())
    i = rt.start({"ticket_id": "t-1"}, DUNG)
    t = step(i, "approver")
    assert BAO not in t["candidates"]
    assert t["candidates"] == [TRI]  # quản lý của Dũng là Bảo (nghỉ) -> giám đốc


def test_person_deactivated_while_holding_step_is_reassigned() -> None:
    org = roster(NV_0032=True)
    rt = runtime("nhan-su-vong-doi", "employee_onboarding", org, Svc())
    i = rt.start({"case_id": "HS-4", "employee_id": "E-4", "employee_user": DUNG}, NHI)
    i = act(rt, i, "hr", NHI, "done")
    org.users[KHANG]["active"] = False
    org.users[VINH]["active"] = False
    org.users[TRI]["active"] = False
    i = rt.load(json.loads(json.dumps(i.to_state())))
    i.refresh_assignments()
    t = step(i, "it")
    assert t["candidates"] == [] and t["via"] == "unresolved"
    with pytest.raises(DecisionError):
        i.act(t["task_id"], KHANG, "done")


# -------------------------------------------------------------------- alternates: ngữ nghĩa
def _a(spec: dict[str, Any]) -> Assignee:
    return Assignee.model_validate(spec)


def test_alternates_union_dedupe_and_sod_filter() -> None:
    r = AssigneeResolver(roster())
    a = _a({"kind": "role", "role": "it_admin", "alternates": ["role(director)", "role(it_admin)"]})
    assert r.resolve(a, {}, {}, None).candidates == [KHANG, TRI]
    got = r.resolve(a, {}, {TRI: "người đề nghị không tự xử lý"}, None)
    assert got.candidates == [KHANG]
    assert any("thay thế" in x and "SoD" in x for x in got.trail)


def test_alternates_when_primary_unresolved() -> None:
    org = roster()
    org.users[KHANG]["active"] = False
    a = AssigneeResolver(org).resolve(
        _a({"kind": "role", "role": "it_admin", "alternates": ["role(director)"]}), {}, {}, None
    )
    assert (a.candidates, a.via) == ([TRI], "fallback")


def test_alternates_must_be_static_role_in_roles() -> None:
    base = json.loads(
        (ROOT / "packages/nhan-su-ai/processes/duyet_jd_agent.json").read_text(encoding="utf-8")
    )
    bad = json.loads(json.dumps(base))
    bad["steps"][0]["assignee"]["alternates"] = ["role(hr)"]
    with pytest.raises(SpecError) as e:
        ENGINE.compile(bad, OPS)
    assert any("alternates" in x and "hr" in x for x in e.value.errors)
    bad["steps"][0]["assignee"]["alternates"] = ["requester.manager"]
    with pytest.raises(SpecError):
        ENGINE.compile(bad, OPS)
    assert "hoặc Vai trò director" in _a(base["steps"][0]["assignee"]).describe()
