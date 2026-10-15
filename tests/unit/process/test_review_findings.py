# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hồi quy cho các phát hiện của agent kiểm thử đối kháng (spike workflow v2 §7):
3 High, 4 Med, các Low đã sửa."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from tests.unit.process.conftest import MakeRt
from tests.unit.process.support import OPS, Clock, load_pack, only
from xt.adapters.db.process_timers import DbApiTimerFireStore
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.application.process.memory import InMemoryOrgDirectory, InMemoryTimerFireStore
from xt.application.process.timers import TimerDefinition, TimerPoller
from xt.domain.process.errors import DecisionError, EngineError, SpecError
from xt.domain.process.schedule import EPOCH, Schedule
from xt.domain.process.spec import TimerTrigger, parse_spec

HCM = ZoneInfo("Asia/Ho_Chi_Minh")
DIGEST_IN = {"from_date": "2026-10-12", "to_date": "2026-10-18", "department": ""}


def one_step(assignee: Any, **extra: Any) -> dict[str, Any]:
    return {
        "id": "t1", "name": "T", "version": 1, "roles": ["employee", "dept_head", "director"],
        "fallback_assignee": "role(director)",
        "steps": [{"id": "s", "type": "approve", "name": "S", "assignee": assignee, **extra}],
    }  # fmt: skip


# H1: role(x) in 'KT' từng bỏ qua phòng (is_static coi là tĩnh) -> giao cho toàn công ty
def test_h1_role_in_literal_department_is_scoped(make_rt: MakeRt) -> None:
    t = only(make_rt(one_step("role(employee) in 'KT'")).start({}, "NV-0006"))
    assert t["candidates"] == ["NV-0002", "NV-0003", "NV-0004"]


# H2: same_step + input sửa được -> bỏ qua các bước `when` phía trước
def test_h2_same_step_with_editable_inputs_rejected() -> None:
    s = load_pack("de_xuat_phe_duyet")
    s["steps"][5]["on_return"]["resume"] = "same_step"
    with pytest.raises(SpecError, match="same_step không dùng được khi có input sửa được"):
        compile_process(s, OPS)


# H3: distinct_from một chiều giữa hai nhánh song song
PAR = {
    "id": "par_sod", "name": "SoD song song", "version": 1, "roles": ["dept_head"],
    "steps": [{"id": "p", "type": "parallel", "name": "P", "branches": [
        [{"id": "a", "type": "approve", "name": "A", "assignee": "role(dept_head)"}],
        [{"id": "b", "type": "approve", "name": "B", "assignee": "role(dept_head)",
          "sod": {"distinct_from": ["a"]}}]]}],
}  # fmt: skip


@pytest.mark.parametrize(("first", "second"), [("a", "b"), ("b", "a")])
def test_h3_distinct_from_symmetric_across_parallel(
    make_rt: MakeRt, first: str, second: str
) -> None:
    i = make_rt(PAR).start({}, "NV-0006")
    box = {t["step"]: t for t in i.inbox()}
    i.act(box[first]["task_id"], "NV-0009", "approve")
    with pytest.raises(DecisionError, match="SoD"):
        i.act(box[second]["task_id"], "NV-0009", "approve")
    i.act(box[second]["task_id"], "NV-0012", "approve")
    assert i.status() == "completed"


# M1: on_event lỗi từng làm act dở dang
def test_m1_event_hook_failure_does_not_break_act(make_rt: MakeRt) -> None:
    def bad(ev: dict[str, Any]) -> None:
        raise RuntimeError("telegram down")

    rt = make_rt("de_xuat_phe_duyet")
    rt.on_event = bad
    i = rt.start({"kind": "advance_request", "amount": 1}, "NV-0005")  # sinh assignment.escalated
    assert any(e["type"] == "event.delivery_failed" for e in i.meta["events"])
    i.act(only(i)["task_id"], "NV-0001", "approve")
    assert i.status() == "completed"


# M2: người đã nghỉ vẫn act được / override không lọc
def test_m2_inactive_candidate_cannot_act_and_override_filtered(
    make_rt: MakeRt, org: InMemoryOrgDirectory
) -> None:
    i = make_rt("de_xuat_phe_duyet").start({"kind": "advance_request", "amount": 1}, "NV-0006")
    t = only(i)
    org.users["NV-0005"]["active"] = False
    with pytest.raises(DecisionError, match="không còn hoạt động"):
        i.act(t["task_id"], "NV-0005", "approve")
    org.users["NV-0005"]["active"] = True
    i.act(t["task_id"], "NV-0005", "request_info", comment="?")
    org.users["NV-0005"]["active"] = False  # người hỏi nghỉ trước khi được trả lời
    i.act(only(i)["task_id"], "NV-0006", "provide_info", comment="ok")
    back = only(i)
    assert back["via"] != "override" and "NV-0005" not in back["candidates"]


# M3: fallback của bước sửa từng được sửa input của người đề nghị rồi tự duyệt
def test_m3_revise_never_given_to_someone_else(make_rt: MakeRt, org: InMemoryOrgDirectory) -> None:
    rt = make_rt("de_xuat_phe_duyet")
    i = rt.start({"kind": "advance_request", "amount": 8_000_000}, "NV-0006")
    i.act(only(i)["task_id"], "NV-0005", "return_for_revision", comment="sửa")
    org.users["NV-0006"]["active"] = False
    i = rt.load(i.to_state())
    i.meta["assign"].clear()
    i.refresh_assignments()
    rv = only(i)
    assert rv["kind"] == "revise" and rv["via"] == "unresolved" and rv["candidates"] == []
    with pytest.raises(DecisionError, match="không gán lại bước sửa"):
        i.reassign(rv["task_id"], "NV-0012", "NV-0001", "thay người đề nghị")


# M4: ủy quyền qua lại vô hạn làm SLA khởi động lại mãi
def test_m4_delegate_ping_pong_capped(make_rt: MakeRt) -> None:
    i = make_rt("de_xuat_phe_duyet").start({"kind": "advance_request", "amount": 1}, "NV-0006")
    who = ["NV-0005", "NV-0007"]
    for n in range(5):
        i.act(only(i)["task_id"], who[n % 2], "delegate", delegate_to=who[(n + 1) % 2])
    with pytest.raises(DecisionError, match="5 lần"):
        i.act(only(i)["task_id"], who[1], "delegate", delegate_to=who[0])
    i.act(only(i)["task_id"], who[1], "approve")
    assert i.status() == "completed"


# Low: tham số thừa không được lọt vào audit
def test_low_param_hygiene(make_rt: MakeRt) -> None:
    i = make_rt("de_xuat_phe_duyet").start({"kind": "advance_request", "amount": 1}, "NV-0006")
    t = only(i)
    for kw in ({"delegate_to": "NV-0007"}, {"ask": "NV-0006"}, {"updates": {"amount": 2}}):
        with pytest.raises(DecisionError):
            i.act(t["task_id"], "NV-0005", "approve", **kw)  # type: ignore[arg-type]
    assert i.meta["history"] == []


# Low: load state của quy trình/phiên bản khác
def test_low_load_bound_to_definition(make_rt: MakeRt) -> None:
    rt = make_rt("de_xuat_phe_duyet")
    st = rt.start({"kind": "advance_request", "amount": 1}, "NV-0006").to_state()
    other = load_pack("de_xuat_phe_duyet")
    other["version"] = 3
    with pytest.raises(DecisionError, match="không thuộc định nghĩa"):
        make_rt(other).load(st)


# Low: lỗi biểu thức lúc start được bọc
def test_low_start_expression_error_wrapped(make_rt: MakeRt) -> None:
    s = load_pack("de_xuat_phe_duyet")
    # chia cho 0 lúc chạy (input tùy chọn thiếu default nay bị chặn lúc compile, AUD-3)
    s["steps"][0]["when"] = "amount / (amount - amount) > 1"
    with pytest.raises(EngineError, match="khởi tạo"):
        make_rt(s).start({"kind": "advance_request", "amount": 1}, "NV-0006")


# Low: kỳ giả mạo
@pytest.mark.parametrize(
    "fire",
    [
        {"fire_at": "2026-10-20T08:00:00+07:00", "period_key": "2026-10-20T08:00:00+07:00"},
        {"fire_at": "2026-10-19T08:00:00+07:00", "period_key": "khac"},
        {"fire_at": "2026-10-19T08:00:00", "period_key": "2026-10-19T08:00:00"},
        {"fire_at": "__import__('os')", "period_key": "x"},
    ],
)
def test_low_forged_fire_rejected(make_rt: MakeRt, fire: dict[str, str]) -> None:
    with pytest.raises(DecisionError):
        make_rt("ban_tin_tuan").start(DIGEST_IN, fire=fire)


# Low: INTERVAL>1 lệch pha khi version mới kích hoạt ngày khác
def test_low_interval_phase_independent_of_activation(make_rt: MakeRt, clock: Clock) -> None:
    s = load_pack("ban_tin_tuan")
    s["trigger"]["rrule"] = "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=8;BYMINUTE=0"
    trig = parse_spec(s).trigger
    assert isinstance(trig, TimerTrigger)
    now = datetime(2026, 11, 30, 9, tzinfo=HCM)
    occ = Schedule(trig).latest_at_or_before(now)
    assert occ is not None and occ.weekday() == 0
    assert (occ.date() - EPOCH.date()).days % 14 == 0  # pha neo vào mốc cố định
    rt = make_rt(s)
    clock.set(now)
    keys = []
    for nb in (datetime(2026, 10, 1, tzinfo=HCM), datetime(2026, 10, 8, tzinfo=HCM)):
        p = TimerPoller(InMemoryTimerFireStore(), [TimerDefinition("d", rt, nb)])
        keys.append(p.poll(now)[0]["period_key"])
    assert keys[0] == keys[1] == occ.isoformat()


# Low: một định nghĩa lỗi không chặn các định nghĩa khác
def test_low_one_bad_definition_does_not_block_poll(
    tmp_path: Path, make_rt: MakeRt, clock: Clock
) -> None:
    good = make_rt("ban_tin_tuan")
    bad_spec = load_pack("ban_tin_tuan")
    bad_spec["id"] = "digest_bad"
    bad = make_rt(bad_spec)
    bad.services = {"weekly_rollup": lambda **kw: 1 / 0, "notify": lambda msg: {}}
    conn = sqlite3.connect(tmp_path / "x.db")
    store = DbApiTimerFireStore(conn, "sqlite")
    store.ensure_schema()
    nb = datetime(2026, 10, 1, tzinfo=HCM)
    p = TimerPoller(
        store, [TimerDefinition("digest_bad", bad, nb), TimerDefinition("weekly_digest", good, nb)]
    )
    clock.set(datetime(2026, 10, 19, 8, 1, tzinfo=HCM))
    r = p.poll(datetime(2026, 10, 19, 8, 1, tzinfo=HCM))
    conn.close()
    assert [x["result"] for x in r] == ["error", "fired"]
