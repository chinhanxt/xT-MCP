# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Runtime: happy, reject, G-P3 (outcome lạ fail closed), vòng trả lại, ủy quyền, hỏi thông tin, song song (G-P1),
người nhận động (G-P4), SoD theo bước (G-I1 tầng workflow), lỗi service (G-P9), idempotency key (G-P10)."""
import copy
import json

import pytest
from SpiffWorkflow.exceptions import WorkflowException
from SpiffWorkflow.util.task import TaskState

from conftest import jsonb_roundtrip, only
from xtwf.engine import ready_user_tasks
from xtwf.runtime import DecisionError, EngineError


def start_proposal(rt, kind="advance_request", amount=4_000_000, requester="NV-0006", **kw):
    return rt.start({"kind": kind, "amount": amount, **kw}, requester)


# ------------------------------------------------------------------ happy / reject
def test_happy_advance_small_only_dept_head(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    t = only(i)
    assert (t["step"], t["candidates"], t["via"]) == ("dept_head", ["NV-0005"], "primary")
    i.act(t["task_id"], "NV-0005", "approve")
    assert i.status() == "completed"
    assert services.ledger == [("NV-0006", 4_000_000)]


def test_happy_payment_80m_full_chain_with_reload(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, kind="payment_request", amount=80_000_000)
    path = []
    for who in ("NV-0005", "NV-0003", "NV-0002", "NV-0001"):
        i = rt.load(jsonb_roundtrip(i.to_state(), seed=len(path)))
        t = only(i)
        path.append((t["step"], who))
        i.act(t["task_id"], who, "approve")
    assert path == [("dept_head", "NV-0005"), ("kttt", "NV-0003"), ("ktt", "NV-0002"), ("director", "NV-0001")]
    assert i.status() == "completed" and services.ledger == [("NV-0006", 80_000_000)]


def test_reject_terminates_without_ledger(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, amount=8_000_000)
    i.act(only(i)["task_id"], "NV-0005", "approve")
    i.act(only(i)["task_id"], "NV-0001", "reject", comment="không cần thiết")
    assert i.status() == "rejected" and services.ledger == []


# ------------------------------------------------------------------ G-P3: outcome lạ không bao giờ thành duyệt
def test_gp3_unknown_outcome_rejected_by_runtime_state_unchanged(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    before = json.dumps(i.to_state(), sort_keys=True)
    t = only(i)
    for bad in ("return_for_info", "tra_lai_bo_sung", "APPROVE", "", None, 1, "approve "):
        with pytest.raises(DecisionError, match="không hợp lệ"):
            i.act(t["task_id"], "NV-0005", bad)
    assert json.dumps(i.to_state(), sort_keys=True) == before
    assert i.status() == "running" and services.ledger == []


def test_gp3_engine_gateway_fails_closed_even_if_runtime_bypassed(make_rt, services):
    """Bỏ qua runtime, ghi thẳng giá trị lạ vào task như harness cũ: gateway không có nhánh mặc định -> lỗi, không ghi sổ."""
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    t = ready_user_tasks(i.wf)[0]
    t.data["dept_head_decision"] = "return_for_info"
    t.run()
    with pytest.raises(WorkflowException, match="No conditions satisfied"):
        i.wf.do_engine_steps()
    assert services.ledger == [] and not i.wf.is_completed()
    assert not any(x.task_spec.name == "ghiso" for x in i.wf.get_tasks(state=TaskState.COMPLETED))


def test_gp3_outcome_not_declared_for_step_rejected(make_rt):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, kind="payment_request", amount=80_000_000)
    i.act(only(i)["task_id"], "NV-0005", "approve")
    i.act(only(i)["task_id"], "NV-0003", "approve")
    t = only(i)
    assert t["step"] == "ktt" and t["outcomes"] == ["approve", "reject"]
    with pytest.raises(DecisionError):
        i.act(t["task_id"], "NV-0002", "return_for_revision", comment="x")
    with pytest.raises(DecisionError):
        i.act(t["task_id"], "NV-0002", "delegate", delegate_to="NV-0004")


def test_gp3_bpmn_decision_gateways_have_no_default_flow(make_rt):
    from lxml import etree
    rt = make_rt("de_xuat_phe_duyet")
    root = etree.fromstring(rt.c.bpmn.encode())
    ns = {"b": "http://www.omg.org/spec/BPMN/20100524/MODEL"}
    decs = root.xpath("//b:exclusiveGateway[starts-with(@id,'dec_')]", namespaces=ns)
    assert decs and all(g.get("default") is None for g in decs)


# ------------------------------------------------------------------ return_for_revision (vòng về người đề nghị)
def test_revise_loop_restart_reroutes_by_new_amount(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, amount=8_000_000)
    i.act(only(i)["task_id"], "NV-0005", "approve")
    t = only(i)
    assert t["step"] == "director"
    with pytest.raises(DecisionError, match="lý do trả lại"):
        i.act(t["task_id"], "NV-0001", "return_for_revision")
    i.act(t["task_id"], "NV-0001", "return_for_revision", comment="Giảm xuống dưới 5 triệu")
    i = rt.load(jsonb_roundtrip(i.to_state(), 7))
    rv = only(i)
    assert (rv["kind"], rv["candidates"]) == ("revise", ["NV-0006"])
    with pytest.raises(DecisionError, match="không được sửa"):
        i.act(rv["task_id"], "NV-0006", "resubmit", updates={"kind": "payment_request"})
    with pytest.raises(DecisionError, match="không hợp lệ cho kiểu int"):
        i.act(rv["task_id"], "NV-0006", "resubmit", updates={"amount": "4tr"})
    i.act(rv["task_id"], "NV-0006", "resubmit", updates={"amount": 4_000_000})
    i = rt.load(jsonb_roundtrip(i.to_state(), 8))
    t = only(i)
    assert t["step"] == "dept_head"          # restart: chạy lại từ đầu (kiểm nợ lại, TBP duyệt lại)
    i.act(t["task_id"], "NV-0005", "approve")
    assert i.status() == "completed"         # 4tr: không còn bước giám đốc
    assert services.ledger == [("NV-0006", 4_000_000)]
    assert [c for c in services.calls if c[0] == "check_overdue_advances"] == [("check_overdue_advances", "NV-0006")] * 2
    assert [h["outcome"] for h in i.meta["history"]] == ["approve", "return_for_revision", "resubmit", "approve"]


def test_revise_max_rounds_then_must_decide(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, amount=8_000_000)
    for rnd in range(2):
        i.act(only(i)["task_id"], "NV-0005", "approve")
        i.act(only(i)["task_id"], "NV-0001", "return_for_revision", comment=f"vòng {rnd}")
        i.act(only(i)["task_id"], "NV-0006", "resubmit")
    i.act(only(i)["task_id"], "NV-0005", "approve")
    t = only(i)
    with pytest.raises(DecisionError, match="tối đa 2"):
        i.act(t["task_id"], "NV-0001", "return_for_revision", comment="lần 3")
    i.act(t["task_id"], "NV-0001", "approve")
    assert i.status() == "completed" and len(services.ledger) == 1


def test_revise_withdraw_ends_without_ledger(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    i.act(only(i)["task_id"], "NV-0005", "return_for_revision", comment="thiếu chứng từ")
    i.act(only(i)["task_id"], "NV-0006", "withdraw")
    assert i.status() == "withdrawn" and services.ledger == []


def test_return_to_step_rework_loop_and_sod_on_acceptance(make_rt, services):
    rt = make_rt("giao_viec_thuong")
    i = rt.start({"task_id": "CV-1", "assignee_user": "NV-0007", "assigner_user": "NV-0005"}, "NV-0005")
    i.act(only(i)["task_id"], "NV-0007", "done")
    i.act(only(i)["task_id"], "NV-0007", "done")
    t = only(i)
    assert (t["step"], t["candidates"]) == ("acceptance", ["NV-0005"])   # người giao = người khởi tạo vẫn nghiệm thu được
    i.act(t["task_id"], "NV-0005", "return_for_revision", comment="Thiếu số liệu tháng 9")
    t = only(i)
    assert (t["step"], t["candidates"]) == ("work", ["NV-0007"])          # quay về đúng người làm, không kết thúc
    i.act(t["task_id"], "NV-0007", "done")
    i.act(only(i)["task_id"], "NV-0005", "approve")
    assert i.status() == "completed"
    assert ("close_task", {"task_id": "CV-1"}) in services.calls


def test_sod_self_assigned_task_cannot_accept_own_work(make_rt, services):
    events = []
    rt = make_rt("giao_viec_thuong", events=events)
    i = rt.start({"task_id": "CV-2", "assignee_user": "NV-0005", "assigner_user": "NV-0005"}, "NV-0005")
    i.act(only(i)["task_id"], "NV-0005", "done")
    i.act(only(i)["task_id"], "NV-0005", "done")
    t = only(i)
    # NV-0005 vừa làm vừa giao -> bị loại ở nghiệm thu; trưởng phòng KD cũng là NV-0005 -> leo thang tới giám đốc
    assert t["candidates"] == ["NV-0001"] and t["via"] == "process_fallback"
    with pytest.raises(DecisionError):
        i.act(t["task_id"], "NV-0005", "approve")
    assert events[-1]["type"] == "assignment.escalated"
    i.act(t["task_id"], "NV-0001", "approve")
    assert i.status() == "completed"


# ------------------------------------------------------------------ delegate / request_info
def test_delegate_reassigns_and_sod_blocks_delegating_to_requester(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    t = only(i)
    with pytest.raises(DecisionError, match="người đề nghị"):
        i.act(t["task_id"], "NV-0005", "delegate", delegate_to="NV-0006")
    with pytest.raises(DecisionError, match="không tồn tại"):
        i.act(t["task_id"], "NV-0005", "delegate", delegate_to="NV-9999")
    i.act(t["task_id"], "NV-0005", "delegate", delegate_to="NV-0007", comment="tôi nghỉ phép")
    t2 = only(i)
    assert (t2["step"], t2["candidates"], t2["via"]) == ("dept_head", ["NV-0007"], "override")
    with pytest.raises(DecisionError, match="không phải người được giao"):
        i.act(t2["task_id"], "NV-0005", "approve")
    i.act(t2["task_id"], "NV-0007", "approve")
    assert i.status() == "completed" and services.ledger


def test_request_info_round_trip_returns_to_asker(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    t = only(i)
    i.act(t["task_id"], "NV-0005", "request_info", comment="Đi công tác mấy ngày?")
    ri = only(i)
    assert (ri["kind"], ri["candidates"]) == ("info", ["NV-0006"])
    with pytest.raises(DecisionError, match="thông tin bổ sung"):
        i.act(ri["task_id"], "NV-0006", "provide_info")
    i = rt.load(jsonb_roundtrip(i.to_state(), 3))
    i.act(ri["task_id"], "NV-0006", "provide_info", comment="3 ngày Đà Nẵng")
    back = only(i)
    assert (back["step"], back["candidates"]) == ("dept_head", ["NV-0005"])
    assert i.data()["dept_head_info"] == "3 ngày Đà Nẵng"
    i.act(back["task_id"], "NV-0005", "approve")
    assert i.status() == "completed"


# ------------------------------------------------------------------ G-P1 parallel
def test_parallel_onboarding_fork_join_with_jsonb_reload(make_rt, services):
    rt = make_rt("nv_onboarding")
    i = rt.start({"case_id": "HS-1", "employee_id": "E-15", "employee_user": "NV-0015"}, "NV-0010")
    i.act(only(i)["task_id"], "NV-0010", "done")
    box = {t["step"]: t for t in i.inbox()}
    assert set(box) == {"it", "accounting", "manager"}                      # 3 nhánh cùng READY
    assert box["manager"]["candidates"] == ["NV-0005"]                       # quản lý của NV-0015 theo org
    assert box["it"]["candidates"] == ["NV-0012", "NV-0013", "NV-0014"]
    assert box["accounting"]["candidates"] == ["NV-0003"]
    order = [("manager", "NV-0005"), ("it", "NV-0013"), ("accounting", "NV-0003")]
    for n, (step, who) in enumerate(order):
        i = rt.load(jsonb_roundtrip(i.to_state(), seed=100 + n))
        t = next(x for x in i.inbox() if x["step"] == step)
        i.act(t["task_id"], who, "done")
        if n < 2:
            assert not any(c[0] == "activate_employee" for c in services.calls)   # join chờ đủ nhánh
            assert len(i.inbox()) == 2 - n
    assert i.status() == "completed"
    assert [c for c in services.calls if c[0] == "activate_employee"] == [("activate_employee", {"employee_id": "E-15"})]
    d = i.data()
    assert (d["it_decision"], d["accounting_decision"], d["manager_decision"]) == ("done", "done", "done")


def test_parallel_offboarding_services_in_branches_and_conditional_after_join(make_rt, services):
    rt = make_rt("nv_offboarding")
    services.overdue["NV-0011"] = 1
    i = rt.start({"case_id": "HS-2", "employee_id": "E-11", "employee_user": "NV-0011"}, "NV-0010")
    i.act(only(i)["task_id"], "NV-0010", "done")
    assert {t["step"] for t in i.inbox()} == {"manager", "it", "accounting"}   # chk_overdue đã chạy trước accounting
    for step, who in (("it", "NV-0014"), ("accounting", "NV-0003"), ("manager", "NV-0009")):
        i = rt.load(jsonb_roundtrip(i.to_state(), seed=hash(step) % 1000))
        i.act(next(x for x in i.inbox() if x["step"] == step)["task_id"], who, "done")
    t = only(i)
    assert (t["step"], t["candidates"]) == ("chief_acct_overdue", ["NV-0002"])   # còn nợ -> kế toán trưởng
    i.act(t["task_id"], "NV-0002", "approve")
    i.act(only(i)["task_id"], "NV-0010", "done")
    assert i.status() == "completed"


def test_parallel_reject_in_branch_terminates_whole_process(make_rt, services):
    spec = {"id": "par_reject", "name": "Song song có từ chối", "version": 1, "roles": ["director", "chief_accountant"],
            "inputs": {"amount": {"type": "int"}},
            "steps": [{"id": "p", "type": "parallel", "name": "Hai người duyệt song song", "branches": [
                [{"id": "a", "type": "approve", "name": "KTT", "assignee": "role(chief_accountant)"}],
                [{"id": "b", "type": "approve", "name": "GĐ", "assignee": "role(director)"}]]},
                {"id": "post", "type": "service", "name": "Ghi sổ", "op": "post_to_ledger", "params": {"amount": "amount", "requester": "requester"}}]}
    rt = make_rt(spec)
    i = rt.start({"amount": 9}, "NV-0006")
    assert {t["step"] for t in i.inbox()} == {"a", "b"}
    i.act(next(t for t in i.inbox() if t["step"] == "b")["task_id"], "NV-0001", "approve")
    i = rt.load(jsonb_roundtrip(i.to_state(), 5))
    i.act(only(i)["task_id"], "NV-0002", "reject")
    assert i.status() == "rejected" and services.ledger == [] and i.inbox() == []


def test_parallel_sla_escalation_in_branch(make_rt, services, clock):
    rt = make_rt("nv_onboarding")
    i = rt.start({"case_id": "HS-3", "employee_id": "E-15", "employee_user": "NV-0015"}, "NV-0010")
    i.act(only(i)["task_id"], "NV-0010", "done")
    i = rt.load(jsonb_roundtrip(i.to_state(), 11))
    clock.advance(hours=25)
    i.tick()
    box = {t["spec"]: t for t in i.inbox()}
    assert box["ut_it_esc"]["candidates"] == ["NV-0012"]                    # department_head('IT')
    assert box["ut_accounting_esc"]["candidates"] == ["NV-0002"]
    assert "ut_manager" in box                                               # SLA 48h chưa tới
    assert "SLA vượt hạn: IT: email, tài khoản, thiết bị" in services.notes


# ------------------------------------------------------------------ G-P4 dynamic assignees
def test_dynamic_requester_is_dept_head_escalates_to_manager(make_rt):
    events = []
    rt = make_rt("de_xuat_phe_duyet", events=events)
    i = start_proposal(rt, requester="NV-0005")
    t = only(i)
    assert (t["candidates"], t["via"]) == (["NV-0001"], "fallback")
    assert "bị loại do SoD" in t["trail"][0]
    assert events and events[0]["type"] == "assignment.escalated"


def test_dynamic_missing_manager_escalates_never_skips(make_rt, services):
    events = []
    rt = make_rt("de_xuat_phe_duyet", events=events)
    i = start_proposal(rt, requester="NV-0001", amount=1_000_000)
    t = only(i)
    # NV-0001: trưởng BGD là chính mình (SoD), không có quản lý, fallback giám đốc cũng là chính mình -> unresolved
    assert t["via"] == "unresolved" and t["candidates"] == []
    assert any("không có quản lý" in x for x in t["trail"])
    assert events[-1]["type"] == "assignment.unresolved"
    for who in ("NV-0001", "NV-0005", "NV-0002"):
        with pytest.raises(DecisionError):
            i.act(t["task_id"], who, "approve")
    assert i.status() == "running" and services.ledger == []                # không bị bỏ qua
    with pytest.raises(DecisionError, match="không có vai trò admin"):
        i.reassign(t["task_id"], "NV-0006", "NV-0002", "test")
    with pytest.raises(DecisionError, match="SoD"):
        i.reassign(t["task_id"], "NV-0012", "NV-0001", "giám đốc tự duyệt")
    i.reassign(t["task_id"], "NV-0012", "NV-0002", "ủy quyền của HĐQT")
    i = rt.load(jsonb_roundtrip(i.to_state(), 2))
    i.act(only(i)["task_id"], "NV-0002", "approve")
    assert i.status() == "completed" and services.ledger == [("NV-0001", 1_000_000)]


def test_dynamic_inactive_head_falls_back(make_rt, org):
    org.users["NV-0005"]["active"] = False
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt, requester="NV-0006")
    t = only(i)
    # trưởng KD nghỉ, quản lý trực tiếp của NV-0006 cũng là NV-0005 -> fallback quy trình: giám đốc
    assert (t["candidates"], t["via"]) == (["NV-0001"], "process_fallback")
    assert len(t["trail"]) == 2 and all("không còn hoạt động" in x for x in t["trail"])


def test_dynamic_role_in_department_of_requester(make_rt):
    spec = {"id": "peer_review", "name": "Đồng nghiệp cùng phòng xem", "version": 1, "roles": ["employee", "director"],
            "fallback_assignee": "role(director)",
            "steps": [{"id": "peer", "type": "approve", "name": "Đồng nghiệp xem", "assignee": "role(employee) in department(requester)"}]}
    rt = make_rt(spec)
    t = only(rt.start({}, "NV-0006"))
    assert t["candidates"] == ["NV-0005", "NV-0007", "NV-0008", "NV-0015"]     # KD, trừ người đề nghị
    assert "phòng KD" in t["reason"]


def test_dynamic_manager_levels_and_department_literal(make_rt):
    spec = {"id": "two_levels", "name": "Hai cấp", "version": 1, "roles": ["director"], "fallback_assignee": "role(director)",
            "steps": [{"id": "m2", "type": "approve", "name": "Quản lý cấp 2", "assignee": "manager(requester, 2)"},
                      {"id": "it", "type": "approve", "name": "Trưởng IT", "assignee": "department_head('IT')"}]}
    rt = make_rt(spec)
    i = rt.start({}, "NV-0013")
    t = only(i)
    assert t["candidates"] == ["NV-0001"] and t["via"] == "primary"          # NV-0013 -> NV-0012 -> NV-0001
    i.act(t["task_id"], "NV-0001", "approve")
    t = only(i)
    assert t["candidates"] == ["NV-0012"]


def test_resolver_unknown_kind_in_tampered_manifest_does_not_eval(make_rt):
    rt = make_rt("de_xuat_phe_duyet")
    rt.resolver.kinds.pop("department_head")         # resolver chưa đăng ký kind -> không có đường thực thi khác
    t = only(start_proposal(rt))
    assert "chưa đăng ký resolver" in t["trail"][0] and t["candidates"] == ["NV-0005"] and t["via"] == "fallback"


# ------------------------------------------------------------------ inputs / requester
def test_inputs_strictly_validated(make_rt):
    rt = make_rt("de_xuat_phe_duyet")
    with pytest.raises(DecisionError, match="input lạ"):
        rt.start({"kind": "advance_request", "amount": 1, "requester_is_dept_head": True}, "NV-0006")
    with pytest.raises(DecisionError, match="thiếu input"):
        rt.start({"kind": "advance_request"}, "NV-0006")
    with pytest.raises(DecisionError, match="kiểu int"):
        rt.start({"kind": "advance_request", "amount": True}, "NV-0006")
    with pytest.raises(DecisionError, match="người đề nghị"):
        rt.start({"kind": "advance_request", "amount": 1}, "NV-9999")
    rt2 = make_rt("giao_viec_thuong")
    with pytest.raises(DecisionError, match="kiểu user"):
        rt2.start({"task_id": "x", "assignee_user": "__import__('os')", "assigner_user": "NV-0005"}, "NV-0005")


def test_comment_control_chars_rejected(make_rt):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    with pytest.raises(DecisionError, match="ký tự điều khiển"):
        i.act(only(i)["task_id"], "NV-0005", "approve", comment="ok\x00\x1b[2J")
    with pytest.raises(DecisionError):
        i.act(only(i)["task_id"], "NV-0005", "approve", comment="x" * 5000)


# ------------------------------------------------------------------ G-P9 / G-P10
def test_service_error_wrapped_and_state_restored(make_rt, services):
    def boom(amount, requester):
        assert False, {"ok": False, "why": "sổ đang khóa"}      # args[0] là dict: SpiffWorkflow 3.2.0 gốc nổ TypeError
    services["post_to_ledger"] = boom
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)
    t = only(i)
    with pytest.raises(EngineError, match="sổ đang khóa"):
        i.act(t["task_id"], "NV-0005", "approve")
    assert only(i)["step"] == "dept_head" and i.status() == "running"       # khôi phục: vẫn chờ TBP, thử lại được
    assert i.meta["history"] == []


def test_idempotency_key_is_instance_and_task(make_rt, services):
    rt = make_rt("de_xuat_phe_duyet")
    i = rt.start({"kind": "advance_request", "amount": 1}, "NV-0006", instance_id="inst-42")
    i.act(only(i)["task_id"], "NV-0005", "approve")
    assert len(services.keys) == 1 and services.keys[0].startswith("inst-42:")


# ------------------------------------------------------------------ dữ liệu sau join (phát hiện khi làm spike)
PAR_LOOP = {"id": "par_loop", "name": "Song song trong vòng trả lại", "version": 1, "roles": ["chief_accountant", "it_admin", "director"],
            "inputs": {"amount": {"type": "int", "editable_on_revision": True}},
            "steps": [{"id": "p", "type": "parallel", "name": "Kiểm song song", "branches": [
                [{"id": "a", "type": "approve", "name": "KTT", "assignee": "role(chief_accountant)"}],
                [{"id": "b", "type": "task", "name": "IT", "assignee": "role(it_admin)", "outcomes": ["done", "request_info"]}]]},
                {"id": "g", "type": "approve", "name": "GĐ", "assignee": "role(director)", "outcomes": ["approve", "reject", "return_for_revision"]},
                {"id": "post", "type": "service", "name": "Ghi sổ", "op": "post_to_ledger", "params": {"amount": "amount", "requester": "requester"},
                 "when": "a_decision == 'approve' and b_decision == 'done'"}]}


def test_spiff_join_merge_is_last_writer_wins_documented():
    """Bằng chứng hành vi SpiffWorkflow 3.2.0: biến có sẵn trước fork, bị một nhánh sửa, có thể bị bản cũ của nhánh khác đè."""
    import sys, pathlib
    from xtwf.engine import new_workflow, safe_engine
    bpmn = (pathlib.Path(__file__).parent / "data" / "join_merge.bpmn").read_text()
    wf = new_workflow(bpmn, "jm", safe_engine({}), {"x": "old"})
    a, b = sorted(ready_user_tasks(wf), key=lambda t: t.task_spec.name)
    a.data["x"] = "new_from_a"; a.run(); wf.do_engine_steps()
    b.run(); wf.do_engine_steps()
    assert wf.is_completed() and wf.data["x"] == "old"       # giá trị mới của nhánh a bị mất


def test_parallel_inside_restart_loop_no_stale_merge(make_rt, services):
    rt = make_rt(PAR_LOOP)
    i = rt.start({"amount": 7}, "NV-0006")
    for rnd in range(2):
        box = {t["step"]: t for t in i.inbox()}
        if rnd == 0:
            i.act(box["b"]["task_id"], "NV-0013", "request_info", comment="cần mã dự án", ask="NV-0006")
            i.act(only(i, "NV-0006")["task_id"], "NV-0006", "provide_info", comment="DA-1")
            box = {t["step"]: t for t in i.inbox()}
        i = rt.load(jsonb_roundtrip(i.to_state(), rnd))
        i.act(box["b"]["task_id"], "NV-0013", "done")
        i.act(box["a"]["task_id"], "NV-0002", "approve")
        d = i.data()
        assert (d["a_decision"], d["b_decision"]) == ("approve", "done"), d
        g = only(i)
        if rnd == 0:
            i.act(g["task_id"], "NV-0001", "return_for_revision", comment="sửa số tiền")
            i.act(only(i)["task_id"], "NV-0006", "resubmit", updates={"amount": 5})
            assert "a_decision" not in i.data() and "b_decision" not in i.data()   # xóa, không phải gán None
        else:
            i.act(g["task_id"], "NV-0001", "approve")
    assert i.status() == "completed" and services.ledger == [("NV-0006", 5)]


def test_sla_escalation_never_back_to_same_overdue_person(make_rt, services, clock):
    rt = make_rt("de_xuat_phe_duyet")
    i = start_proposal(rt)                       # TBP = NV-0005; requester.manager của NV-0006 cũng là NV-0005
    clock.advance(hours=25)
    i = rt.load(jsonb_roundtrip(i.to_state(), 4))
    i.tick()
    t = only(i)
    assert t["spec"] == "ut_dept_head_esc" and t["candidates"] == ["NV-0001"] and t["via"] == "process_fallback"
    assert "đang giữ việc bị quá hạn" in t["trail"][0]
    i.act(t["task_id"], "NV-0001", "approve")
    assert i.status() == "completed" and services.notes == ["SLA vượt hạn: Trưởng bộ phận duyệt"]
