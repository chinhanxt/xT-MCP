# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Hồi quy từ audit process engine (docs/archive/audit-process.md). Ban đầu là xfail(strict=True); cả 10
đã pass sau bản sửa nên bỏ marker."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from tests.unit.process.support import OPS, Clock, load_pack, only
from xt.adapters.workflow.bpmn.compiler import compile_process
from xt.adapters.workflow.bpmn.hardening import parse_bpmn
from xt.application.process.memory import InMemoryInstanceStore, InMemoryOrgDirectory
from xt.application.process.runtime import ProcessRuntime
from xt.application.process.service import InMemoryProcessCatalog, ProcessService
from xt.domain.process.errors import DecisionError, ProcessError, SpecError, UnsafeExpression

MakeRt = Callable[..., ProcessRuntime]
PAY = {"kind": "payment_request", "amount": 1000}


# AUD-1 (đã sửa)
def test_aud1_sla_escalation_works_with_long_step_name(make_rt: MakeRt, clock: Clock) -> None:
    s = load_pack("de_xuat_phe_duyet")
    s["steps"][1]["name"] = "D" * 195  # hợp lệ: Text cho phép tới 200 ký tự
    i = make_rt(s).start(PAY, "NV-0006")
    clock.advance(hours=25)
    i.tick()  # hiện tại: EngineError "hằng chuỗi dài quá 200 ký tự", SLA không bao giờ leo thang
    assert [t["spec"] for t in i.inbox()] == ["ut_dept_head_esc"]


# AUD-2 (đã sửa)
def test_aud2_request_info_does_not_freeze_sla(make_rt: MakeRt, clock: Clock) -> None:
    i = make_rt("de_xuat_phe_duyet").start(PAY, "NV-0006")
    i.act(only(i)["task_id"], "NV-0005", "request_info", "hỏi", ask="NV-0007")
    clock.advance(days=30)
    i.tick()
    # chờ bổ sung thông tin không dừng đồng hồ: hạn 24h vẫn chạy, task hỏi bị hủy, leo thang
    assert [t["spec"] for t in i.inbox()] == ["ut_dept_head_esc"]


# AUD-3 (đã sửa)
def test_aud3_optional_input_in_when_does_not_wedge_instance(make_rt: MakeRt) -> None:
    spec = {
        "id": "opt_when", "name": "x", "version": 1, "roles": ["director", "chief_accountant"],
        "inputs": {"note": {"type": "str", "required": False}},
        "steps": [
            {"id": "a", "type": "approve", "name": "A", "assignee": "role(director)"},
            {"id": "b", "type": "approve", "name": "B", "assignee": "role(chief_accountant)",
             "when": "note == 'x'"},
        ],
    }  # fmt: skip
    try:
        rt = make_rt(spec)
    except SpecError:
        return  # cách sửa chấp nhận được: compile từ chối
    i = rt.start({}, "NV-0006")
    i.act(only(i)["task_id"], "NV-0001", "approve")  # hiện tại: EngineError NameError mãi mãi
    assert i.status() == "completed"


# AUD-4 (đã sửa)
def test_aud4_load_rejects_workflow_state_of_other_definition(make_rt: MakeRt) -> None:
    rt = make_rt("giao_viec_thuong")
    other = make_rt("de_xuat_phe_duyet")
    mine = rt.start({"task_id": "T-1", "assignee_user": "NV-0007", "assigner_user": "NV-0005"},
                    "NV-0005")  # fmt: skip
    st = mine.to_state()
    st["wf"] = other.start(PAY, "NV-0006").to_state()["wf"]
    with pytest.raises(ProcessError):
        rt.load(st)


# AUD-5 (đã sửa)
def test_aud5_service_rejects_state_with_foreign_instance_id(make_rt: MakeRt) -> None:
    cat = InMemoryProcessCatalog()
    cat.add(make_rt("de_xuat_phe_duyet"))
    store = InMemoryInstanceStore()
    svc = ProcessService(cat, store)
    svc.start("proposal_approval", PAY, "NV-0006", instance_id="A")
    svc.start("proposal_approval", PAY, "NV-0007", instance_id="B")
    store.records["A"].state = copy.deepcopy(store.records["B"].state)
    with pytest.raises(ProcessError):
        svc.get("A")  # hiện tại: trả về instance "B"


# AUD-6 (đã sửa)
def test_aud6_doctype_rejected_in_any_encoding() -> None:
    body = compile_process(load_pack("giao_viec_thuong"), OPS).bpmn.split("\n", 1)[1]
    raw = ('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE d [<!ENTITY e "x">]>' + body).encode(
        "utf-16"
    )
    with pytest.raises(UnsafeExpression):
        parse_bpmn(raw)


# AUD-7 (đã sửa)
@pytest.mark.parametrize("ch", ["￾", "￿"])
def test_aud7_xml_invalid_chars_in_name_are_spec_errors(make_rt: MakeRt, ch: str) -> None:
    s = load_pack("de_xuat_phe_duyet")
    s["steps"][1]["name"] = f"Duyệt {ch}"
    with pytest.raises(SpecError):
        make_rt(s)  # hiện tại: UnsafeExpression (invalid_request) từ parse_bpmn


# AUD-8 (đã sửa)
def test_aud8_inactive_sole_candidate_is_reresolved(
    make_rt: MakeRt, org: InMemoryOrgDirectory
) -> None:
    i = make_rt("de_xuat_phe_duyet").start(PAY, "NV-0006")
    i.act(only(i)["task_id"], "NV-0005", "approve")
    assert only(i)["candidates"] == ["NV-0003"]
    org.users["NV-0003"]["active"] = False
    i.tick()
    box = i.inbox()
    assert any(org.users[u]["active"] for t in box for u in t["candidates"]) or any(
        e["type"] == "assignment.unresolved" for e in i.meta["events"]
    )


# AUD-9 (đã sửa)
def test_aud9_unresolved_revise_can_be_withdrawn_by_admin(
    make_rt: MakeRt, org: InMemoryOrgDirectory
) -> None:
    i = make_rt("de_xuat_phe_duyet").start(PAY, "NV-0006")
    org.users["NV-0006"]["active"] = False  # nghỉ trước khi bị trả lại
    i.act(only(i)["task_id"], "NV-0005", "return_for_revision", "thiếu chứng từ")
    t: dict[str, Any] = only(i)
    assert t["via"] == "unresolved"  # thông báo: "chờ quản trị (chỉ được rút)"
    try:
        # NV-0012 có vai trò admin
        i.act(t["task_id"], "NV-0012", "withdraw", "người đề nghị đã nghỉ")
    except DecisionError:
        pytest.fail("không ai rút được: instance kẹt vĩnh viễn")
    assert i.status() == "withdrawn"


def test_aud2_delegate_and_info_keep_original_deadline(make_rt: MakeRt, clock: Clock) -> None:
    i = make_rt("de_xuat_phe_duyet").start(PAY, "NV-0006")
    due = i.wf.next_due()
    clock.advance(hours=20)
    i.act(only(i)["task_id"], "NV-0005", "delegate", delegate_to="NV-0007")
    assert i.wf.next_due() == due  # ủy quyền không đặt lại hạn
    i.act(only(i)["task_id"], "NV-0007", "request_info", "hỏi", ask="NV-0006")
    i.act(only(i)["task_id"], "NV-0006", "provide_info", "đây")
    assert i.wf.next_due() == due
    clock.advance(hours=5)  # 25h kể từ khi vào bước
    i.tick()
    assert [t["spec"] for t in i.inbox()] == ["ut_dept_head_esc"]


def test_aud3_optional_input_with_default_works_and_missing_default_rejected(
    make_rt: MakeRt,
) -> None:
    spec: dict[str, Any] = {
        "id": "opt_when", "name": "x", "version": 1, "roles": ["director", "chief_accountant"],
        "inputs": {"note": {"type": "str", "required": False, "default": ""}},
        "steps": [
            {"id": "a", "type": "approve", "name": "A", "assignee": "role(director)"},
            {"id": "b", "type": "approve", "name": "B", "assignee": "role(chief_accountant)",
             "when": "note == 'x'"},
        ],
    }  # fmt: skip
    i = make_rt(spec).start({}, "NV-0006")
    i.act(only(i)["task_id"], "NV-0001", "approve")
    assert i.status() == "completed"
    i = make_rt(spec).start({"note": "x"}, "NV-0006")
    i.act(only(i)["task_id"], "NV-0001", "approve")
    assert only(i)["step"] == "b"
    bad = copy.deepcopy(spec)
    del bad["inputs"]["note"]["default"]
    with pytest.raises(SpecError, match="chưa có default"):
        make_rt(bad)
    bad["inputs"]["note"] = {"type": "int", "required": False, "default": "x"}
    with pytest.raises(SpecError, match="default"):
        make_rt(bad)


def test_aud3_engine_failure_is_recorded_as_admin_visible_incident(make_rt: MakeRt) -> None:
    rt = make_rt("de_xuat_phe_duyet")

    def boom(**kw: Any) -> None:
        raise RuntimeError("sổ khóa")

    rt.services = {**rt.services, "post_to_ledger": boom}
    cat = InMemoryProcessCatalog()
    cat.add(rt)
    store = InMemoryInstanceStore()
    svc = ProcessService(cat, store)
    r = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    with pytest.raises(ProcessError) as e:
        svc.act(r["instance"], r["tasks"][0]["task_id"], "NV-0005", "approve")
    assert e.value.code == "internal_error"
    got = svc.get(r["instance"])
    assert got["status"] == "running" and [t["step"] for t in got["tasks"]] == ["dept_head"]
    (inc,) = got["incidents"]
    assert "sổ khóa" in inc["error"] and inc["actor"] == "NV-0005"
