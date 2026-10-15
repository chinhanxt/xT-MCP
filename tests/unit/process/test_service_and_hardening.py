# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Use case ProcessService (store + catalog + hẹn timer), parser cứng, script engine, serializer."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest
from SpiffWorkflow.exceptions import WorkflowException

from tests.unit.process.support import ENGINE, OPS, Clock, Services, load_pack, seed_org
from xt.adapters.workflow.bpmn.engine import SafeEnvironment, safe_engine
from xt.adapters.workflow.bpmn.hardening import parse_bpmn
from xt.adapters.workflow.bpmn.serializer import reorder_tasks
from xt.adapters.workflow.bpmn.spiff import load_specs, new_workflow
from xt.application.process.memory import InMemoryInstanceStore
from xt.application.process.runtime import ProcessRuntime
from xt.application.process.service import InMemoryProcessCatalog, ProcessService
from xt.domain.process.errors import DecisionError, EngineError, UnsafeExpression
from xt.domain.process.expr import check_expr, evaluate


class Wakeups:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, datetime]] = []

    def wake_at(self, *, process_id: str, instance_id: str, due: datetime) -> None:
        self.calls.append((process_id, instance_id, due))


def _service(
    sv: Services, wake: Wakeups | None = None
) -> tuple[ProcessService, InMemoryInstanceStore]:
    org = seed_org()
    cat = InMemoryProcessCatalog()
    for name in ("de_xuat_phe_duyet", "giao_viec_thuong"):
        c = ENGINE.compile(load_pack(name), OPS)
        cat.add(ProcessRuntime(c, ENGINE, sv, org, admin_role="admin", scheduler=wake))
    store = InMemoryInstanceStore()
    return ProcessService(cat, store), store


def test_service_start_inbox_act_and_schedules_sla(clock: Clock) -> None:
    sv, wake = Services(), Wakeups()
    svc, store = _service(sv, wake)
    r = svc.start("proposal_approval", {"kind": "advance_request", "amount": 8_000_000}, "NV-0006")
    iid = r["instance"]
    assert wake.calls and wake.calls[-1][:2] == ("proposal_approval", iid)  # SLA 24h
    assert store.records[iid].next_due == wake.calls[-1][2]
    assert svc.inbox("NV-0006") == []
    (t,) = svc.inbox("NV-0005")
    assert (t["step"], t["process_id"]) == ("dept_head", "proposal_approval")
    svc.act(iid, t["task_id"], "NV-0005", "approve")
    (t2,) = svc.inbox("NV-0001")
    r = svc.act(iid, t2["task_id"], "NV-0001", "approve")
    assert r["status"] == "completed" and sv.ledger == [("NV-0006", 8_000_000)]
    assert store.records[iid].status == "completed" and store.records[iid].candidates == []
    assert svc.inbox("NV-0001") == []
    got = svc.get(iid)
    assert [h["outcome"] for h in got["history"]] == ["approve", "approve"]


def test_service_errors_carry_contract_codes() -> None:
    svc, _ = _service(Services())
    with pytest.raises(DecisionError) as e:
        svc.start("khong_co", {}, "NV-0006")
    assert e.value.code == "not_found"
    with pytest.raises(DecisionError) as e:
        svc.act("khong-co", "t", "NV-0005", "approve")
    assert e.value.code == "not_found"
    r = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    tid = r["tasks"][0]["task_id"]
    with pytest.raises(DecisionError) as e:
        svc.act(r["instance"], tid, "NV-0006", "approve")
    assert (e.value.code, e.value.reason) == ("access_denied", "not_assigned_approver")
    with pytest.raises(DecisionError) as e:
        svc.act(r["instance"], tid, "NV-0005", "approve_all")
    assert e.value.code == "invalid_request"
    with pytest.raises(DecisionError) as e:
        svc.start("proposal_approval", {"kind": "x", "amount": 1, "lạ": 1}, "NV-0006")
    assert e.value.code == "invalid_request"


def test_service_old_instance_keeps_its_version() -> None:
    sv = Services()
    svc, store = _service(sv)
    r = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    v3 = load_pack("de_xuat_phe_duyet")
    v3["version"] = 3
    cat = svc.catalog
    assert isinstance(cat, InMemoryProcessCatalog)
    cat.add(ProcessRuntime(ENGINE.compile(v3, OPS), ENGINE, sv, seed_org()))
    with pytest.raises(DecisionError, match="bất biến"):
        cat.add(ProcessRuntime(ENGINE.compile(v3, OPS), ENGINE, sv, seed_org()))
    (t,) = svc.inbox("NV-0005")
    svc.act(r["instance"], t["task_id"], "NV-0005", "approve")
    assert store.records[r["instance"]].version == 2 and sv.ledger == [("NV-0006", 1)]


def test_service_reassign_and_tick(clock: Clock) -> None:
    sv = Services()
    svc, store = _service(sv)
    r = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0001")
    tid = r["tasks"][0]["task_id"]
    assert r["tasks"][0]["via"] == "unresolved"
    svc.reassign(r["instance"], tid, "NV-0012", "NV-0002", "HĐQT ủy quyền")
    assert [t["task_id"] for t in svc.inbox("NV-0002")] == [tid]
    r2 = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    due = store.records[r2["instance"]].next_due
    assert due is not None and store.due(due) == sorted([r["instance"], r2["instance"]])
    clock.advance(hours=25)
    out = svc.tick(r2["instance"])
    assert out["tasks"][0]["spec"] == "ut_dept_head_esc"


# ------------------------------------------------------------------ parser / engine / serializer
def test_parse_bpmn_rejects_billion_laughs_and_garbage() -> None:
    lol = (
        '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa">'
        '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><l>&b;</l>'
    )
    for bad in (lol, "<not-closed>", b"\x00\x01", "<a/>" * 0 + "x" * 10):
        with pytest.raises(UnsafeExpression):
            parse_bpmn(bad)
    with pytest.raises(UnsafeExpression, match="lớn quá"):
        parse_bpmn(b"<a>" + b"x" * (2 * 1024 * 1024) + b"</a>")


def test_spiffworkflow_only_sees_hardened_tree() -> None:
    c = ENGINE.compile(load_pack("de_xuat_phe_duyet"), OPS)
    evil = c.bpmn.replace(
        '<bpmn:userTask id="ut_dept_head"',
        '<bpmn:scriptTask id="evil"><bpmn:script>__import__("os")</bpmn:script></bpmn:scriptTask>'
        '<bpmn:userTask id="ut_dept_head"',
    )
    with pytest.raises(UnsafeExpression, match="scriptTask"):
        load_specs(evil, c.process_id)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')", "().__class__", "[x for x in y]", "(lambda: 1)()",
        "f(1)", "a.b", "_x", "a ** 2", "a * 2", "{1: 2}", "{1}", "(a := 1)", "*a",
        "'%s' % a", "a // 2", "a @ b", "~a", "a << 1", "b'x'",
    ],
)  # fmt: skip
def test_expression_battery_rejected(expr: str) -> None:
    with pytest.raises(UnsafeExpression):
        check_expr(expr)


def test_expression_depth_cap_and_eval_has_no_builtins() -> None:
    with pytest.raises(UnsafeExpression, match="sâu"):
        check_expr("[[[[[[[[1]]]]]]]]")
    assert evaluate("a > 1 and b in ('x', 'y')", {"a": 2, "b": "x"}) is True
    with pytest.raises(NameError):
        evaluate("open", {})


def test_safe_environment_blocks_scripts_and_unknown_services() -> None:
    env = SafeEnvironment({})
    with pytest.raises(UnsafeExpression):
        env.execute("import os", {})
    with pytest.raises(UnsafeExpression, match="chưa được đăng ký"):
        env.call_service({}, operation_name="os_system", operation_params={})
    eng = safe_engine({})
    with pytest.raises(UnsafeExpression):
        eng.execute(None, "import os")


def test_reorder_rejects_corrupt_state() -> None:
    tasks: dict[str, Any] = {"r": {"children": ["a"]}, "a": {"children": []}, "x": {"children": []}}
    with pytest.raises(EngineError, match="mồ côi"):
        reorder_tasks(tasks, "r")
    with pytest.raises(EngineError, match="không tồn tại"):
        reorder_tasks({"r": {"children": ["zz"]}}, "r")
    with pytest.raises(EngineError, match="gốc"):
        reorder_tasks({}, "r")
    assert list(reorder_tasks({"a": {"children": []}, "r": {"children": ["a"]}}, "r")) == ["r", "a"]


def test_unregistered_service_never_runs_and_error_is_wrapped() -> None:
    c = ENGINE.compile(load_pack("ban_tin_tuan"), OPS)
    data = {
        "from_date": "a",
        "to_date": "b",
        "department": "",
        "xt_fire_at": "2020-01-01T00:00:00+00:00",
    }
    with pytest.raises(WorkflowException, match="chưa được đăng ký"):
        new_workflow(c.bpmn, c.process_id, safe_engine({}), data)


def test_manual_process_rejects_fire_and_timer_needs_tz() -> None:
    rt = ProcessRuntime(
        ENGINE.compile(load_pack("de_xuat_phe_duyet"), OPS), ENGINE, Services(), seed_org()
    )
    with pytest.raises(DecisionError, match="không nhận fire"):
        rt.start({"kind": "a", "amount": 1}, "NV-0006", fire={"fire_at": "x", "period_key": "x"})
    with pytest.raises(DecisionError, match="state không hợp lệ"):
        rt.load({"wf": {}})
