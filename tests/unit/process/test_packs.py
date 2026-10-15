# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chạy 6 process v2 của gói nghiệp vụ (đề xuất - phê duyệt, giao việc thường/khẩn, bản tin tuần,
tiếp nhận, nghỉ việc) với org từ seed của pack. Mỗi bước nạp lại từ JSON đã sắp key (JSONB).
Kịch bản và kết quả mong đợi lấy từ research/spike-workflow-v2/results/packs.txt."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from lxml import etree

from tests.unit.process.support import (
    OPS,
    PACK_NAMES,
    Clock,
    Services,
    load_pack,
    make_runtime,
    seed_org,
)
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.application.process.memory import InMemoryTimerFireStore
from xt.application.process.runtime import ProcessInstance, ProcessRuntime
from xt.application.process.timers import TimerDefinition, TimerPoller
from xt.domain.process.errors import DecisionError, EngineError

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
A, D = "approve", "done"
Plan = list[tuple[str, str, str, dict[str, Any]]]


def roundtrip(rt: ProcessRuntime, i: ProcessInstance) -> ProcessInstance:
    return rt.load(json.loads(json.dumps(i.to_state(), sort_keys=True)))


def drive(rt: ProcessRuntime, i: ProcessInstance, plan: Plan) -> tuple[ProcessInstance, list[str]]:
    path: list[str] = []
    for step, actor, outcome, kw in plan:
        i = roundtrip(rt, i)
        box = [t for t in i.inbox() if t["step"] == step]
        assert box, (step, i.inbox())
        t = box[0]
        try:
            i.act(t["task_id"], actor, outcome, **kw)
            via = f"(via {t['via']})" if t["via"] != "primary" else ""
            path.append(f"{step}[{t['kind']}]@{actor}:{outcome}{via}")
        except (DecisionError, EngineError) as e:
            path.append(f"DENY {step}@{actor}:{outcome} {type(e).__name__}")
    return i, path


def test_b0_compile_counts() -> None:
    # so với spike: +1 service bắt đầu SLA mỗi bước có SLA, +1 boundary trên ri_ut_X (AUD-2)
    expect = {
        "proposal_approval": (16, 0, 6, 8, 77, 94, False),
        "task_normal": (8, 0, 4, 6, 42, 39, False),
        "task_urgent": (8, 0, 4, 6, 42, 39, False),
        "weekly_digest": (0, 0, 0, 1, 6, 3, True),
        "employee_onboarding": (10, 2, 5, 10, 58, 50, False),
        "employee_offboarding": (12, 2, 5, 10, 68, 60, False),
    }
    got = {}
    for n in PACK_NAMES:
        c = compile_process(load_pack(n), OPS)
        r = etree.fromstring(c.bpmn.encode())

        def cnt(tag: str, r: Any = r) -> int:
            return len(r.xpath(f"//*[local-name()='{tag}']"))

        timer_start = bool(
            r.xpath("//*[local-name()='startEvent']/*[local-name()='timerEventDefinition']")
        )
        got[c.process_id] = (
            cnt("userTask"), cnt("parallelGateway"), cnt("boundaryEvent"), cnt("lane"),
            cnt("BPMNShape"), cnt("BPMNEdge"), timer_start,
        )  # fmt: skip
    assert got == expect


PROPOSAL: list[tuple[str, str, int, Plan, str, str, list[tuple[str, int]], dict[str, int]]] = [
    ("tạm ứng 4tr", "advance_request", 4_000_000, [("dept_head", "NV-0005", A, {})],
     "NV-0006", "completed", [("NV-0006", 4_000_000)], {}),
    ("tạm ứng 8tr", "advance_request", 8_000_000,
     [("dept_head", "NV-0005", A, {}), ("director", "NV-0001", A, {})],
     "NV-0006", "completed", [("NV-0006", 8_000_000)], {}),
    ("thanh toán 80tr", "payment_request", 80_000_000,
     [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("ktt", "NV-0002", A, {}),
      ("director", "NV-0001", A, {})], "NV-0006", "completed", [("NV-0006", 80_000_000)], {}),
    ("hoàn ứng 12tr", "advance_settlement", 12_000_000,
     [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("director", "NV-0001", A, {})],
     "NV-0006", "completed", [("NV-0006", 12_000_000)], {}),
    ("từ chối bước 1", "advance_request", 8_000_000,
     [("dept_head", "NV-0005", "reject", {"comment": "sai"})], "NV-0006", "rejected", [], {}),
    ("từ chối bước cuối 80tr", "payment_request", 80_000_000,
     [("dept_head", "NV-0005", A, {}), ("kttt", "NV-0003", A, {}), ("ktt", "NV-0002", A, {}),
      ("director", "NV-0001", "reject", {"comment": "hoãn"})], "NV-0006", "rejected", [], {}),
    ("người đề nghị là TBP", "advance_request", 8_000_000,
     [("dept_head", "NV-0001", A, {}), ("director", "NV-0001", A, {})],
     "NV-0005", "completed", [("NV-0005", 8_000_000)], {}),
    ("còn nợ quá hạn -> KTT", "advance_request", 8_000_000,
     [("dept_head", "NV-0005", A, {}), ("chief_acct_overdue", "NV-0002", A, {}),
      ("director", "NV-0001", A, {})], "NV-0006", "completed", [("NV-0006", 8_000_000)],
     {"NV-0006": 1}),
    ("G-P3 return_for_info", "advance_request", 8_000_000,
     [("dept_head", "NV-0005", "return_for_info", {})], "NV-0006", "running", [], {}),
    ("trả lại 8tr -> 4tr", "advance_request", 8_000_000,
     [("dept_head", "NV-0005", A, {}),
      ("director", "NV-0001", "return_for_revision", {"comment": "giảm dưới 5tr"}),
      ("director", "NV-0006", "resubmit", {"updates": {"amount": 4_000_000}}),
      ("dept_head", "NV-0005", A, {})], "NV-0006", "completed", [("NV-0006", 4_000_000)], {}),
    ("trả lại rồi rút", "advance_request", 4_000_000,
     [("dept_head", "NV-0005", "return_for_revision", {"comment": "thiếu"}),
      ("dept_head", "NV-0006", "withdraw", {})], "NV-0006", "withdrawn", [], {}),
    ("ủy quyền", "advance_request", 4_000_000,
     [("dept_head", "NV-0005", "delegate", {"delegate_to": "NV-0007"}),
      ("dept_head", "NV-0007", A, {})], "NV-0006", "completed", [("NV-0006", 4_000_000)], {}),
    ("hỏi thêm thông tin", "advance_request", 4_000_000,
     [("dept_head", "NV-0005", "request_info", {"comment": "mấy ngày?"}),
      ("dept_head", "NV-0006", "provide_info", {"comment": "3 ngày"}),
      ("dept_head", "NV-0005", A, {})], "NV-0006", "completed", [("NV-0006", 4_000_000)], {}),
    ("người đề nghị / sai phòng duyệt", "advance_request", 4_000_000,
     [("dept_head", "NV-0006", A, {}), ("dept_head", "NV-0012", A, {})],
     "NV-0006", "running", [], {}),
]  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "amount", "plan", "requester", "status", "ledger", "overdue"),
    [p[1:] for p in PROPOSAL],
    ids=[p[0] for p in PROPOSAL],
)
def test_b1_proposal_approval(
    kind: str, amount: int, plan: Plan, requester: str, status: str,
    ledger: list[tuple[str, int]], overdue: dict[str, int],
) -> None:  # fmt: skip
    sv = Services()
    sv.overdue.update(overdue)
    rt = make_runtime("de_xuat_phe_duyet", sv, seed_org())
    i, path = drive(rt, rt.start({"kind": kind, "amount": amount}, requester), plan)
    assert i.status() == status, path
    assert sv.ledger == ledger
    if status == "running":
        assert all(p.startswith("DENY") for p in path) and i.meta["history"] == []


def test_b1_director_self_request_unresolved_then_admin(clock: Clock) -> None:
    events: list[dict[str, Any]] = []
    sv = Services()
    rt = make_runtime("de_xuat_phe_duyet", sv, seed_org(), events=events)
    i = rt.start({"kind": "advance_request", "amount": 1_000_000}, "NV-0001")
    t = i.inbox()[0]
    assert (t["via"], t["candidates"], len(t["trail"])) == ("unresolved", [], 3)
    i.reassign(t["task_id"], "NV-0012", "NV-0002", "HĐQT ủy quyền")
    i, path = drive(rt, i, [("dept_head", "NV-0002", A, {})])
    assert path == ["dept_head[decision]@NV-0002:approve(via admin)"]
    assert [e["type"] for e in events] == ["assignment.unresolved"]
    assert sv.ledger == [("NV-0001", 1_000_000)]


def test_b1_sla_24h_escalates(clock: Clock) -> None:
    sv = Services()
    rt = make_runtime("de_xuat_phe_duyet", sv, seed_org())
    clock.set(datetime(2026, 10, 15, 9, tzinfo=HCM))
    i = rt.start({"kind": "advance_request", "amount": 4_000_000}, "NV-0006")
    assert i.wf.next_due() is not None  # SLA 24h -> hẹn đánh thức
    clock.advance(hours=25)
    i = roundtrip(rt, i)
    i.tick()
    t = i.inbox()[0]
    assert (t["spec"], t["via"], t["candidates"]) == (
        "ut_dept_head_esc",
        "process_fallback",
        ["NV-0001"],
    )
    i, _ = drive(rt, i, [("dept_head", "NV-0001", A, {})])
    assert i.status() == "completed" and sv.notes == ["SLA vượt hạn: Trưởng bộ phận duyệt"]


TASK_CASES: list[tuple[str, dict[str, str], Plan, str, bool]] = [
    ("happy", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
     [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", A, {})],
     "completed", True),
    ("không đạt -> làm lại -> đạt", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
     [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}),
      ("acceptance", "NV-0005", "return_for_revision", {"comment": "thiếu số liệu"}),
      ("work", "NV-0007", D, {}), ("acceptance", "NV-0005", A, {})], "completed", True),
    ("không đạt -> từ chối", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
     [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}),
      ("acceptance", "NV-0005", "reject", {"comment": "hủy"})], "rejected", False),
    ("tự giao cho mình", {"assignee_user": "NV-0005", "assigner_user": "NV-0005"},
     [("accept", "NV-0005", D, {}), ("work", "NV-0005", D, {}), ("acceptance", "NV-0005", A, {}),
      ("acceptance", "NV-0001", A, {})], "completed", True),
    ("người làm cố nghiệm thu", {"assignee_user": "NV-0007", "assigner_user": "NV-0005"},
     [("accept", "NV-0007", D, {}), ("work", "NV-0007", D, {}), ("acceptance", "NV-0007", A, {})],
     "running", False),
]  # fmt: skip


@pytest.mark.parametrize("pack", ["giao_viec_thuong", "giao_viec_khan"])
@pytest.mark.parametrize(
    ("inp", "plan", "status", "closed"), [c[1:] for c in TASK_CASES], ids=[c[0] for c in TASK_CASES]
)
def test_b2_task_assignment(
    pack: str, inp: dict[str, str], plan: Plan, status: str, closed: bool
) -> None:
    sv = Services()
    rt = make_runtime(pack, sv, seed_org())
    i, path = drive(rt, rt.start({"task_id": "CV-1", **inp}, inp["assigner_user"]), plan)
    assert i.status() == status, path
    assert any(c[0] == "close_task" for c in sv.calls) is closed


@pytest.mark.parametrize(("pack", "hours"), [("giao_viec_thuong", 73), ("giao_viec_khan", 9)])
def test_b2_work_sla_escalates_to_head_of_worker(pack: str, hours: int, clock: Clock) -> None:
    rt = make_runtime(pack, Services(), seed_org())
    clock.set(datetime(2026, 10, 15, 9, tzinfo=HCM))
    i = rt.start(
        {"task_id": "CV-9", "assignee_user": "NV-0013", "assigner_user": "NV-0005"}, "NV-0005"
    )
    i, _ = drive(rt, i, [("accept", "NV-0013", D, {})])
    clock.advance(hours=hours)
    i.tick()
    t = i.inbox()[0]
    assert (t["spec"], t["candidates"]) == ("ut_work_esc", ["NV-0012"])
    assert (
        t["reason"] == "Trưởng phòng của assignee_user: trưởng phòng IT của assignee_user NV-0013"
    )


def test_b3_weekly_digest_two_workers(clock: Clock) -> None:
    sv = Services()
    rt = make_runtime("ban_tin_tuan", sv, seed_org())
    store = InMemoryTimerFireStore()
    nb = datetime(2026, 10, 15, 9, tzinfo=HCM)
    workers = [TimerPoller(store, [TimerDefinition("weekly_digest", rt, nb)]) for _ in range(2)]
    seen = []
    for now in [
        datetime(2026, 10, 19, 7, 59, tzinfo=HCM), datetime(2026, 10, 19, 8, 0, 30, tzinfo=HCM),
        datetime(2026, 10, 19, 8, 5, tzinfo=HCM), datetime(2026, 10, 26, 8, 1, tzinfo=HCM),
        datetime(2026, 11, 16, 9, 0, tzinfo=HCM),
    ]:  # fmt: skip
        clock.set(now)
        for w in workers:
            seen.append([(x["period_key"][:10], x["result"]) for x in w.poll(now)])
    assert seen == [
        [], [],
        [("2026-10-19", "fired")], [("2026-10-19", "already")],
        [("2026-10-19", "already")], [("2026-10-19", "already")],
        [("2026-10-26", "fired")], [("2026-10-26", "already")],
        [("2026-11-16", "fired")], [("2026-11-16", "already")],
    ]  # fmt: skip
    assert sv.calls == [
        ("weekly_rollup", "2026-10-12", "2026-10-18"),
        ("weekly_rollup", "2026-10-19", "2026-10-25"),
        ("weekly_rollup", "2026-11-09", "2026-11-15"),  # catchup=latest: bỏ 02/11, 09/11
    ]
    assert len(store.instances) == 3


def test_b4_onboarding_parallel_with_request_info() -> None:
    sv = Services()
    rt = make_runtime("nv_onboarding", sv, seed_org())
    i = rt.start({"case_id": "HS-15", "employee_id": "E-15", "employee_user": "NV-0015"}, "NV-0010")
    i, _ = drive(rt, i, [("hr", "NV-0010", D, {})])
    assert [(t["step"], t["candidates"]) for t in i.inbox()] == [
        ("it", ["NV-0012", "NV-0013", "NV-0014"]), ("accounting", ["NV-0003"]),
        ("manager", ["NV-0005"]),
    ]  # fmt: skip
    i, path = drive(rt, i, [
        ("manager", "NV-0005", D, {}),
        ("it", "NV-0013", "request_info", {"comment": "laptop hay PC?", "ask": "NV-0005"}),
        ("it", "NV-0005", "provide_info", {"comment": "laptop"}),
        ("accounting", "NV-0003", D, {}), ("it", "NV-0013", D, {}),
    ])  # fmt: skip
    assert path[-1] == "it[task]@NV-0013:done(via override)"
    assert i.status() == "completed"
    assert [c for c in sv.calls if c[0] == "activate_employee"] == [
        ("activate_employee", {"employee_id": "E-15"})
    ]


@pytest.mark.parametrize(
    ("overdue", "unreturned", "status"),
    [(0, 0, "completed"), (1, 0, "completed"), (0, 1, "running")],
    ids=["sạch", "còn nợ tạm ứng", "còn tài sản chặn đóng hồ sơ"],
)
def test_b5_offboarding(overdue: int, unreturned: int, status: str) -> None:
    sv = Services()
    sv.overdue["NV-0011"] = overdue
    sv.unreturned = unreturned

    def close_case(case_id: str) -> dict[str, Any]:
        if sv.unreturned:
            raise RuntimeError(f"không đóng được hồ sơ: còn {sv.unreturned} tài sản chưa thu hồi")
        sv.calls.append(("close_lifecycle_case", case_id))
        return {"closed": case_id}

    sv["close_lifecycle_case"] = close_case
    rt = make_runtime("nv_offboarding", sv, seed_org())
    i = rt.start({"case_id": "HS-11", "employee_id": "E-11", "employee_user": "NV-0011"}, "NV-0010")
    plan: Plan = [
        ("hr_confirm", "NV-0010", D, {}), ("it", "NV-0014", D, {}), ("manager", "NV-0009", D, {}),
        ("accounting", "NV-0003", D, {}),
    ]  # fmt: skip
    if overdue or unreturned:
        plan.append(("chief_acct_overdue", "NV-0002", A, {}))
    plan.append(("hr_close", "NV-0010", D, {}))
    i, path = drive(rt, i, plan)
    assert i.status() == status, path
    if status == "running":
        assert path[-1] == "DENY hr_close@NV-0010:done EngineError"
        assert [t["step"] for t in i.inbox()] == ["hr_close"]  # state khôi phục, vẫn chờ
    else:
        assert ("close_lifecycle_case", "HS-11") in sv.calls
