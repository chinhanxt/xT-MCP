# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Handler + pipeline với port giả: mọi nhánh mã lỗi, IAM trước xác nhận, idempotency, CAS,
tự chủ -> PendingOperation + confirm_url, thực thi khi người xác nhận, làn PII."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.unit.tools.fakes import (
    NOW,
    World,
    agent_ctx,
    human_ctx,
    later,
    make_dispatcher,
    make_world,
)
from xt.application.tools import (
    ClientClass,
    ConfirmationService,
    ToolDispatcher,
    ToolOutcome,
)
from xt.application.tools.dispatcher import allowed_errors
from xt.application.tools.ports import PendingStatus
from xt.contracts.tools import (
    BY_NAME,
    CONFIRMATION_DETAILS,
    ERROR_ENVELOPE,
    ErrorCode,
    success_envelope,
)
from xt.domain.autonomy import RiskLevel

ERR = Draft202012Validator(ERROR_ENVELOPE)
CONF = Draft202012Validator(CONFIRMATION_DETAILS)
SEEN: set[tuple[str, str]] = set()


def call(d: ToolDispatcher, tool: str, args: dict[str, Any], ctx: Any = None) -> ToolOutcome:
    out = d.call(tool, args, ctx or agent_ctx())
    if out.is_error:
        ERR.validate(out.envelope)
        code = out.envelope["error"]["code"]
        if tool in BY_NAME:
            assert ErrorCode(code) in allowed_errors(BY_NAME[tool]), (tool, code)
        if code == "confirmation_required":
            CONF.validate(out.envelope["error"]["details"])
        SEEN.add((tool, code))
    else:
        Draft202012Validator(success_envelope(BY_NAME[tool].output_schema)).validate(out.envelope)
        assert out.envelope["untrusted"] is True
    return out


def code(out: ToolOutcome) -> str:
    assert out.is_error, out.envelope
    return str(out.envelope["error"]["code"])


def err(out: ToolOutcome) -> dict[str, Any]:
    assert out.is_error, out.envelope
    e: dict[str, Any] = out.envelope["error"]
    return e


def data(out: ToolOutcome) -> dict[str, Any]:
    assert not out.is_error, out.envelope
    d: dict[str, Any] = out.envelope["data"]
    return d


@pytest.fixture
def world() -> World:
    return make_world()


@pytest.fixture
def d(world: World) -> ToolDispatcher:
    return make_dispatcher(world)


CREATE = {
    "model_id": "advance_request",
    "values": {"amount": "8000000", "purpose": "Công tác Đà Nẵng"},
    "idempotency_key": "adv-viet-0001",
}
DECIDE = {
    "ref": "advance_request:tu-0042",
    "decision": "approve",
    "expected_revision": 2,
    "idempotency_key": "dec-0042-mai-1",
}


# ---- lớp vào: tool, khóa trùng, schema -------------------------------------------------------


def test_unknown_tool(d: ToolDispatcher) -> None:
    out = call(d, "xt_apply_model_change", {})
    assert err(out)["details"]["reason"] == "wrong_tool"
    assert "inspect_workspace" in err(out)["details"]["tools"]


def test_duplicate_key_marker_is_rejected(d: ToolDispatcher) -> None:
    out = d.call("create_record", CREATE, agent_ctx(), duplicate_keys=["amount"])
    ERR.validate(out.envelope)
    assert err(out)["details"] == {"reason": "duplicate_key", "keys": ["amount"]}


def test_schema_rejects_gate_params_and_bad_types(d: ToolDispatcher, world: World) -> None:
    out = call(d, "decide_request", {**DECIDE, "approved": True})
    assert code(out) == "invalid_request" and err(out)["details"]["reason"] == "schema"
    out = call(d, "create_record", {**CREATE, "values": {"amount": {"nested": 1}}})
    assert code(out) == "invalid_request"
    assert world.access.checks == []  # chưa tới IAM


# ---- IAM trước tiên --------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(BY_NAME))
def test_iam_runs_first_for_every_tool(tool: str, world: World) -> None:
    d = make_dispatcher(world, enabled_capabilities=frozenset({"upload_file", "aggregate_records"}))
    world.access.deny_tools[tool] = "implicit_deny"
    out = call(d, tool, dict(BY_NAME[tool].examples[0]))
    assert code(out) == "access_denied"
    assert err(out)["explain_ref"] == "explain:deny-0001"
    assert world.access.checks[-1].action == BY_NAME[tool].iam[0]
    assert world.operations.pending == {} and world.store.commits == []


def test_sod_denied_before_any_confirmation(d: ToolDispatcher, world: World) -> None:
    # Storyboard S2a: Việt bảo agent tự duyệt đơn của mình -> access_denied, không có link.
    out = call(
        d,
        "decide_request",
        DECIDE,
        agent_ctx(
            "sub-viet",
        ),
    )
    assert code(out) == "access_denied"
    assert err(out)["details"]["reason"] == "sod" and err(out)["explain_ref"]
    assert "confirm_url" not in str(out.envelope)
    assert world.operations.pending == {}


# ---- năng lực theo mốc -----------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["upload_file", "aggregate_records"])
def test_gated_capabilities_until_v02(tool: str, d: ToolDispatcher, world: World) -> None:
    out = call(d, tool, dict(BY_NAME[tool].examples[0]))
    assert code(out) == "capability_unavailable"
    assert err(out)["details"]["available_in"] == "v0.2"
    assert world.store.commits == []


def test_enabled_capabilities_work(world: World) -> None:
    d = make_dispatcher(world, enabled_capabilities=frozenset({"upload_file", "aggregate_records"}))
    out = call(d, "aggregate_records", {"model_id": "advance_request"})
    assert data(out)["scope"] == "authorized_records"
    out = call(d, "upload_file", dict(BY_NAME["upload_file"].examples[0]))
    assert data(out)["file"]["status"] == "not_scanned"
    out = call(
        d,
        "upload_file",
        {"name": "a.txt", "source": "base64", "text": "x", "idempotency_key": "file-0000001"},
    )
    assert err(out)["details"]["reason"] == "conflicting_parameters"
    out = call(
        d, "upload_file", {"name": "a.txt", "source": "text", "idempotency_key": "file-0000002"}
    )
    assert err(out)["details"]["reason"] == "missing_parameter"
    out = call(
        d,
        "aggregate_records",
        {"model_id": "advance_request", "metric": "advance_total", "metrics": ["count"]},
    )
    assert err(out)["details"]["reason"] == "conflicting_parameters"


def test_inspect_workspace(d: ToolDispatcher) -> None:
    out = call(d, "inspect_workspace", {})
    assert data(out)["me"]["ref"] == "person:viet"
    assert out.envelope["meta"]["effective_level"] == "L0"


# ---- đọc -------------------------------------------------------------------------------------


def test_reads_not_found(d: ToolDispatcher) -> None:
    assert code(call(d, "describe_model", {"model_id": "nope"})) == "not_found"
    assert code(call(d, "describe_process", {"process_id": "nope"})) == "not_found"
    assert code(call(d, "get_record", {"ref": "advance_request:missing"})) == "not_found"
    assert code(call(d, "get_history", {"ref": "task:t9"})) == "not_found"


def test_reads_success(d: ToolDispatcher, world: World) -> None:
    assert data(call(d, "describe_model", {"model_id": "task"}))["model"]["primitive"] == "task"
    assert data(call(d, "describe_process", {"process_id": "advance_approval"}))["process"]
    assert data(call(d, "get_inbox", {"kinds": ["approval"]}))["items"][0]["kind"] == "approval"
    h = data(call(d, "get_history", {"ref": "explain:deny-0001", "view": "explain"}))
    assert h["explanation"]["outcome"] == "denied"
    s = data(call(d, "search_records", {"model_id": "task", "limit": 5}))
    assert s["items"][0]["ref"] == "task:t1"
    assert "search_records" in world.pii.inbound_calls  # where/PII token đi qua làn PII


def test_find_people_relations(d: ToolDispatcher) -> None:
    out = call(d, "find_people", {"direct_reports_of": "Mai", "manager_of": "Bình"})
    assert err(out)["details"]["reason"] == "conflicting_parameters"
    assert code(call(d, "find_people", {})) == "invalid_request"
    out = data(call(d, "find_people", {"direct_reports_of": "Mai"}))
    assert out["relation"] == "direct_reports_of"
    assert {p["ref"] for p in out["people"]} == {"person:binh", "person:viet"}


def test_search_requires_query_without_model(d: ToolDispatcher) -> None:
    out = call(d, "search_records", {"limit": 5})
    assert err(out)["details"] == {"reason": "missing_parameter", "parameter": "query"}
    assert not call(d, "search_records", {"query": "Đà Nẵng"}).is_error


def test_pii_lane_by_client_class(d: ToolDispatcher) -> None:
    cloud = call(d, "get_record", {"ref": "advance_request:tu-0042"}, agent_ctx())
    assert data(cloud)["record"]["values"]["national_id"] == "[PII:k3f9a2]"
    assert cloud.envelope["meta"]["pii"] == {"mode": "masked", "masked_fields": ["national_id"]}
    local = call(
        d,
        "get_record",
        {"ref": "advance_request:tu-0042"},
        agent_ctx(client_class=ClientClass.LOCAL),
    )
    assert data(local)["record"]["values"]["national_id"] == "079123456789"
    assert local.envelope["meta"]["pii"] == {"mode": "full"}


def test_masked_pii_token_cannot_be_written_back(d: ToolDispatcher, world: World) -> None:
    out = call(
        d,
        "update_record",
        {
            "ref": "advance_request:tu-0050",
            "set": {"national_id": "[PII:k3f9a2]"},
            "expected_revision": 1,
            "idempotency_key": "upd-pii-0001",
        },
    )
    assert err(out)["details"]["reason"] == "masked_value"
    assert world.store.commits == []


# ---- create_record ---------------------------------------------------------------------------


def test_create_record_success_receipt_and_replay(d: ToolDispatcher, world: World) -> None:
    first = call(d, "create_record", CREATE)
    rec = data(first)["record"]
    assert rec["state"] == "draft" and data(first)["suggested_next"][0]["tool"] == "submit_request"
    meta = first.envelope["meta"]
    assert meta["effective_level"] == "L1"
    assert meta["receipt"]["replayed"] is False and meta["receipt"]["commit_state"] == "committed"
    again = call(d, "create_record", CREATE)
    assert data(again) == data(first)
    assert again.envelope["meta"]["receipt"]["replayed"] is True
    assert world.store.commits == ["create"]  # không ghi lần hai
    assert world.transactions.runs == 2 and world.transactions.rollbacks == 0


def test_create_record_idempotency_conflict(d: ToolDispatcher) -> None:
    call(d, "create_record", CREATE)
    other = {**CREATE, "values": {**CREATE["values"], "amount": "9000000"}}
    assert code(call(d, "create_record", other)) == "idempotency_conflict"


def test_create_record_key_namespace_is_per_actor(d: ToolDispatcher, world: World) -> None:
    call(d, "create_record", CREATE, agent_ctx("sub-viet"))
    out = call(d, "create_record", CREATE, agent_ctx("sub-binh"))
    assert out.envelope["meta"]["receipt"]["replayed"] is False
    assert world.store.commits == ["create", "create"]


def test_create_record_wrong_tool_for_tasks(d: ToolDispatcher) -> None:
    out = call(
        d,
        "create_record",
        {
            "model_id": "task",
            "values": {"title": "Gửi báo giá", "assignee": "person:binh"},
            "idempotency_key": "task-bg-01",
        },
    )
    e = err(out)
    assert e["details"]["reason"] == "wrong_tool"
    assert e["continuation"] == {
        "tool": "create_task",
        "arguments": {
            "title": "Gửi báo giá",
            "assignee": "person:binh",
            "idempotency_key": "task-bg-01",
        },
    }


def test_create_record_model_errors(d: ToolDispatcher) -> None:
    assert code(call(d, "create_record", {**CREATE, "model_id": "nope"})) == "not_found"
    out = call(d, "create_record", {**CREATE, "values": {"amount": "1", "destination": "ĐN"}})
    assert err(out)["details"]["reason"] == "unknown_field"
    assert "purpose" in err(out)["details"]["valid_fields"]
    out = call(d, "create_record", {**CREATE, "values": {"amount": "80000000", "purpose": "x"}})
    assert code(out) == "rule_violation" and err(out)["explain_ref"] == "explain:rv-0001"
    assert err(out)["details"]["violations"][0]["field"] == "amount"


# ---- update_record / CAS ---------------------------------------------------------------------


UPDATE_TASK = {
    "ref": "task:t1",
    "set": {"assignee": "person:binh"},
    "expected_revision": 5,
    "reason": "Anh Tuấn nghỉ phép",
    "idempotency_key": "task-t1-reassign",
}


def test_update_state_field_redirects_to_transition(d: ToolDispatcher) -> None:
    out = call(d, "update_record", {**UPDATE_TASK, "set": {"state": "done"}})
    e = err(out)
    assert code(out) == "precondition_failed" and e["details"]["reason"] == "state_field"
    assert e["continuation"]["tool"] == "transition_record"
    assert e["continuation"]["arguments"]["to_state"] == "done"


def test_update_revision_conflict(d: ToolDispatcher) -> None:
    out = call(d, "update_record", {**UPDATE_TASK, "expected_revision": 4})
    assert code(out) == "revision_conflict"
    assert err(out)["details"]["current_revision"] == 5


def test_update_low_impact_is_l1(d: ToolDispatcher) -> None:
    out = call(d, "update_record", {**UPDATE_TASK, "set": {"result_note": "ok"}})
    assert data(out)["changed_fields"] == ["result_note"]
    assert out.envelope["meta"]["effective_level"] == "L1"


def test_l2_needs_confirmation_then_replays_after_confirm(d: ToolDispatcher, world: World) -> None:
    out = call(d, "update_record", UPDATE_TASK)
    e = err(out)
    assert e["code"] == "confirmation_required" and e["retryable"] is True
    det = e["details"]
    assert det["status"] == "pending" and det["level"] == "L2"
    assert det["confirm_url"] == f"https://xt.example.test/desk/confirm/{det['confirmation_id']}"
    assert det["confirmer"] == {"ref": "person:viet", "name": "Trần Văn Việt"}
    assert det["expires_at"] == "2026-11-15T09:10:00Z"
    assert e["continuation"] == {"tool": "update_record", "arguments": UPDATE_TASK}
    assert world.store.commits == []
    # Gọi lại khi còn chờ: cùng confirmation, không tạo mới.
    again = err(call(d, "update_record", UPDATE_TASK))
    assert again["details"]["confirmation_id"] == det["confirmation_id"]
    assert len(world.operations.pending) == 1
    # Khác args cùng key -> conflict.
    assert (
        code(call(d, "update_record", {**UPDATE_TASK, "reason": "khác"})) == "idempotency_conflict"
    )
    # Người được đại diện xác nhận trên Desk -> trang tự thực thi.
    conf = ConfirmationService(d)
    wrong = conf.confirm(det["confirmation_id"], "sub-binh", later(1), "tr_desk")
    assert wrong.error and wrong.error["details"]["reason"] == "not_assigned_approver"
    ok = conf.confirm(det["confirmation_id"], "sub-viet", later(1), "tr_desk")
    assert ok.status is PendingStatus.CONFIRMED and ok.receipt is not None
    assert world.store.commits == ["update"]
    replay = call(d, "update_record", UPDATE_TASK, agent_ctx(now=later(2)))
    assert replay.envelope["meta"]["receipt"]["replayed"] is True
    assert replay.envelope["meta"]["effective_level"] == "L2"
    assert data(replay)["record"]["values"]["assignee"] == "person:binh"


def test_ceiling_l2_agent_does_l2_directly(d: ToolDispatcher, world: World) -> None:
    world.access.ceiling = RiskLevel.L2
    out = call(d, "update_record", UPDATE_TASK)
    assert data(out)["record"]["revision"] == 6


def test_declined_and_expired_confirmations(d: ToolDispatcher, world: World) -> None:
    det = err(call(d, "update_record", UPDATE_TASK))["details"]
    conf = ConfirmationService(d)
    assert (
        conf.decline(det["confirmation_id"], "sub-viet", later(1)).status is PendingStatus.DECLINED
    )
    out = call(d, "update_record", UPDATE_TASK)
    assert code(out) == "confirmation_declined"
    assert err(out)["details"]["status"] == "declined"
    # hết hạn
    k2 = {**UPDATE_TASK, "idempotency_key": "task-t1-reassign-2"}
    det2 = err(call(d, "update_record", k2))["details"]
    out = call(d, "update_record", k2, agent_ctx(now=later(11)))
    assert code(out) == "confirmation_declined" and err(out)["details"]["status"] == "expired"
    assert (
        conf.confirm(det2["confirmation_id"], "sub-viet", later(12), "t").status
        is PendingStatus.EXPIRED
    )
    assert world.store.commits == []


def test_confirm_revalidates_and_invalidates_on_change(d: ToolDispatcher, world: World) -> None:
    det = err(call(d, "update_record", UPDATE_TASK))["details"]
    world.store.records["task:t1"].revision = 6  # ai đó sửa trong lúc chờ
    out = ConfirmationService(d).confirm(det["confirmation_id"], "sub-viet", later(1), "t")
    assert out.status is PendingStatus.INVALIDATED
    assert out.error and out.error["code"] == "revision_conflict"
    assert code(call(d, "update_record", UPDATE_TASK)) == "confirmation_declined"
    assert world.store.commits == []


def test_pending_limit_rate_limited(world: World) -> None:
    d = make_dispatcher(world, max_open_pending_per_actor=1)
    call(d, "update_record", UPDATE_TASK)
    out = call(d, "update_record", {**UPDATE_TASK, "idempotency_key": "task-t1-other"})
    assert code(out) == "rate_limited" and err(out)["details"]["retry_after_seconds"] == 600


# ---- transition_record -----------------------------------------------------------------------


def test_transition(d: ToolDispatcher) -> None:
    bad = call(
        d,
        "transition_record",
        {
            "ref": "task:t1",
            "to_state": "todo",
            "expected_revision": 5,
            "idempotency_key": "t1-back-0001",
        },
    )
    assert code(bad) == "precondition_failed"
    ok = call(
        d,
        "transition_record",
        {
            "ref": "task:t1",
            "to_state": "done",
            "expected_revision": 5,
            "values": {"result_note": "Đã gửi"},
            "idempotency_key": "t1-done-0001",
        },
    )
    assert data(ok)["from_state"] == "in_progress" and data(ok)["to_state"] == "done"


def test_l3_always_needs_confirmation_even_for_l3_agents_and_humans(
    d: ToolDispatcher, world: World
) -> None:
    world.access.ceiling = RiskLevel.L3
    args = {
        "ref": "employee:e1",
        "to_state": "offboarding",
        "expected_revision": 1,
        "idempotency_key": "off-e1-0001",
    }
    out = call(d, "transition_record", args)
    assert err(out)["details"]["level"] == "L3"
    out = call(d, "transition_record", args, human_ctx("sub-mai"))
    det = err(out)["details"]
    assert det["confirmer"]["ref"] == "person:mai"


# ---- decide_request (human_only) -------------------------------------------------------------


def test_decide_request_preconditions(d: ToolDispatcher) -> None:
    for decision in ("reject", "return"):
        out = call(d, "decide_request", {**DECIDE, "decision": decision}, agent_ctx("sub-mai"))
        assert err(out)["details"]["parameter"] == "reason"
    out = call(d, "decide_request", {**DECIDE, "decision": "delegate"}, agent_ctx("sub-mai"))
    assert err(out)["details"]["parameter"] == "delegate_to"
    out = call(d, "decide_request", {**DECIDE, "delegate_to": "person:long"}, agent_ctx("sub-mai"))
    assert err(out)["details"]["reason"] == "conflicting_parameters"
    out = call(
        d,
        "decide_request",
        {**DECIDE, "ref": "advance_request:tu-0050", "expected_revision": 1},
        agent_ctx("sub-mai"),
    )
    assert code(out) == "precondition_failed"
    out = call(d, "decide_request", {**DECIDE, "expected_revision": 1}, agent_ctx("sub-mai"))
    assert code(out) == "revision_conflict"


def test_decide_request_not_assigned_approver(d: ToolDispatcher, world: World) -> None:
    out = call(d, "decide_request", DECIDE, agent_ctx("sub-long"))
    assert err(out)["details"]["reason"] == "not_assigned_approver"
    assert world.operations.pending == {}


def test_decide_request_human_only_full_loop(d: ToolDispatcher, world: World) -> None:
    world.access.ceiling = RiskLevel.L3  # kể cả trần L3 vẫn phải xác nhận
    out = call(d, "decide_request", DECIDE, agent_ctx("sub-mai"))
    det = err(out)["details"]
    assert det["level"] == "human_only" and det["confirmer"]["ref"] == "person:mai"
    assert det["plan_digest"].startswith("sha256:")
    assert "8000000" in det["plan_summary"]
    out = ConfirmationService(d).confirm(det["confirmation_id"], "sub-mai", later(3), "tr_desk")
    assert out.status is PendingStatus.CONFIRMED
    replay = call(d, "decide_request", DECIDE, agent_ctx("sub-mai", now=later(4)))
    assert data(replay)["outcome"] == "request_approved"
    assert replay.envelope["meta"]["receipt"]["replayed"] is True
    assert replay.envelope["meta"]["effective_level"] == "L3"


def test_human_desk_session_still_confirms_decisions(d: ToolDispatcher) -> None:
    out = call(d, "decide_request", DECIDE, human_ctx("sub-mai"))
    assert code(out) == "confirmation_required"


# ---- tự chủ / danh tính ----------------------------------------------------------------------


def test_l0_agent_cannot_write(d: ToolDispatcher, world: World) -> None:
    world.access.ceiling = RiskLevel.L0
    out = call(d, "create_record", CREATE)
    assert err(out)["details"]["reason"] == "autonomy_hard_limit"
    assert not call(d, "get_record", {"ref": "task:t1"}).is_error  # vẫn đọc được


def test_service_account_without_human_cannot_get_confirmation(d: ToolDispatcher) -> None:
    out = call(d, "update_record", UPDATE_TASK, agent_ctx(None))
    assert err(out)["details"]["reason"] == "autonomy_hard_limit"


def test_revoked_token_on_high_risk_commit(d: ToolDispatcher, world: World) -> None:
    world.access.ceiling = RiskLevel.L2
    out = call(d, "update_record", UPDATE_TASK, agent_ctx(revoked=True))
    assert code(out) == "unauthenticated"
    assert world.store.commits == []
    ok = call(d, "create_record", CREATE, agent_ctx(revoked=True))  # L1: không introspect
    assert not ok.is_error


# ---- submit / task / model change ------------------------------------------------------------


def test_submit_request(d: ToolDispatcher) -> None:
    args = {"ref": "advance_request:tu-0050", "idempotency_key": "adv-0050-submit"}
    out = call(d, "submit_request", args)
    assert data(out)["route"][0]["approver"]["ref"] == "person:mai"
    assert code(call(d, "submit_request", {**args, "idempotency_key": "adv-0050-submit2"})) == (
        "precondition_failed"
    )
    out = call(
        d,
        "submit_request",
        {
            **args,
            "ref": "advance_request:tu-0050",
            "expected_revision": 9,
            "idempotency_key": "adv-0050-s3",
        },
    )
    assert code(out) == "revision_conflict"


def test_create_task_levels(d: ToolDispatcher) -> None:
    out = call(
        d,
        "create_task",
        {"title": "Báo cáo", "assignee": "person:tuan", "idempotency_key": "task-tuan-0001"},
    )
    assert data(out)["task"]["assignee"]["ref"] == "person:tuan"  # cấp dưới trực tiếp: L1
    out = call(
        d,
        "create_task",
        {"title": "Báo cáo", "assignee": "person:long", "idempotency_key": "task-long-0001"},
    )
    assert err(out)["details"]["level"] == "L2"  # ngoài tuyến
    out = call(
        d,
        "create_task",
        {"title": "x", "assignee": "person:nobody", "idempotency_key": "task-none-0001"},
    )
    assert code(out) == "not_found"


def test_propose_model_change_dry_run_and_submit(d: ToolDispatcher, world: World) -> None:
    ex = dict(BY_NAME["propose_model_change"].examples[0])
    out = call(d, "propose_model_change", ex)
    assert data(out)["dry_run"] is True and "receipt" not in out.envelope["meta"]
    assert world.operations.by_key == {}
    out = call(d, "propose_model_change", {**ex, "dry_run": False})
    assert data(out)["request"] == "model_change_request:m1"
    out = call(
        d,
        "propose_model_change",
        {**ex, "dry_run": False, "base_revision": 3, "idempotency_key": "pmc-device-loan-02"},
    )
    assert code(out) == "revision_conflict"


# ---- lỗi hạ tầng -----------------------------------------------------------------------------


def test_unexpected_failure_is_internal_error_without_leaking(
    d: ToolDispatcher, world: World
) -> None:
    world.records.explode = True
    out = call(d, "search_records", {"model_id": "task"})
    assert code(out) == "internal_error" and err(out)["retryable"] is True
    assert "hunter2" not in str(out.envelope)
    assert out.envelope["meta"]["trace_id"] == "tr_test"


def test_failure_logs_carry_no_message_or_pii(
    d: ToolDispatcher, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    """R2-L3: log chỉ có kiểu ngoại lệ + vị trí + trace_id, không có str(exc)."""
    world.records.explode = True
    with caplog.at_level("DEBUG", logger="xt.tools"):
        call(d, "search_records", {"model_id": "task"})
    text = caplog.text
    assert "tr_test" in text and "RuntimeError" in text and "fakes.py" in text
    assert "hunter2" not in text and "password" not in text


def test_output_contract_violation_is_caught(d: ToolDispatcher, world: World) -> None:
    world.records.bad_output = True
    assert code(call(d, "search_records", {"model_id": "task"})) == "internal_error"


def test_oversized_result_is_resource_constraint(world: World) -> None:
    d = make_dispatcher(world, max_response_bytes=200)
    out = call(d, "get_record", {"ref": "task:t1"})
    assert code(out) == "resource_constraint"
    assert err(out)["details"]["resource"] == "transport"


def test_port_timeout_passes_through(
    d: ToolDispatcher, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from xt.application.tools import ToolError

    def slow(ctx: Any, args: Any) -> Any:
        raise ToolError(ErrorCode.TIMEOUT, "Query timed out.", details={"commit_state": "none"})

    monkeypatch.setattr(world.records, "search", slow)
    out = call(d, "search_records", {"model_id": "task"})
    assert code(out) == "timeout" and err(out)["retryable"] is True


def test_failed_plan_rolls_back_the_claim(d: ToolDispatcher, world: World) -> None:
    bad = {**CREATE, "values": {"amount": "80000000", "purpose": "x"}}
    assert code(call(d, "create_record", bad)) == "rule_violation"
    assert world.transactions.rollbacks == 1 and world.operations.by_key == {}
    # Cùng key, args đã sửa: không bị coi là conflict vì claim đã nhả.
    fixed = {**bad, "values": {"amount": "8000000", "purpose": "x"}}
    assert not call(d, "create_record", fixed).is_error


def test_key_held_by_another_transaction_is_in_progress(world: World) -> None:
    import threading

    world.operations.wait_seconds = 0.05
    d = make_dispatcher(world)
    started, release = threading.Event(), threading.Event()
    plan_create = world.records.plan_create

    def slow(ctx: Any, args: Any) -> Any:
        started.set()
        release.wait(5)
        return plan_create(ctx, args)

    world.records.plan_create = slow  # type: ignore[method-assign]
    out: list[ToolOutcome] = []
    th = threading.Thread(target=lambda: out.append(d.call("create_record", CREATE, agent_ctx())))
    th.start()
    started.wait(5)
    busy = call(d, "create_record", CREATE)
    release.set()
    th.join()
    assert code(busy) == "rate_limited"
    assert err(busy)["details"]["reason"] == "idempotency_in_progress"
    assert not out[0].is_error and world.store.commits == ["create"]


def test_human_actor_cannot_do_l2_directly(d: ToolDispatcher, world: World) -> None:
    out = call(d, "update_record", UPDATE_TASK, human_ctx("sub-viet"))
    assert err(out)["details"]["level"] == "L2"
    ok = call(
        d,
        "update_record",
        {**UPDATE_TASK, "set": {"result_note": "x"}, "idempotency_key": "t1-note-human"},
        human_ctx("sub-viet"),
    )
    assert ok.envelope["meta"]["effective_level"] == "L1"


def test_port_data_is_normalized_to_json(d: ToolDispatcher, world: World) -> None:
    from datetime import date
    from decimal import Decimal

    get = world.records.get

    def typed(ctx: Any, args: Any) -> Any:
        out = get(ctx, args)
        out["record"]["values"].update(amount=Decimal("8000000"), due=date(2026, 11, 20), at=NOW)
        return out

    world.records.get = typed  # type: ignore[method-assign]
    values = data(call(d, "get_record", {"ref": "task:t1"}))["record"]["values"]
    assert values["amount"] == "8000000" and values["due"] == "2026-11-20"
    assert values["at"] == "2026-11-15T09:00:00Z"


def test_errors_carry_no_pii_for_cloud(d: ToolDispatcher, world: World) -> None:
    out = call(
        d,
        "create_record",
        {**CREATE, "values": {"amount": "1", "purpose": "x", "national_id": "079123456789"}},
    )
    assert not out.is_error  # local mask không đụng dữ liệu ghi
    assert "create_record" not in world.pii.scrubbed
    bad = call(d, "get_record", {"ref": "advance_request:079123456789"})
    assert "079123456789" not in str(bad.envelope) and "get_record" in world.pii.scrubbed


def test_double_click_on_confirm_executes_once(d: ToolDispatcher, world: World) -> None:
    import threading

    det = err(call(d, "update_record", UPDATE_TASK))["details"]
    conf = ConfirmationService(d)
    outs: list[Any] = []
    ths = [
        threading.Thread(
            target=lambda: outs.append(
                conf.confirm(det["confirmation_id"], "sub-viet", later(1), "t")
            )
        )
        for _ in range(2)
    ]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    assert world.store.commits == ["update"]
    assert sorted(o.status.value for o in outs) == ["confirmed", "confirmed"]
    assert sum(o.receipt is not None for o in outs) == 1


def test_settings_validation() -> None:
    from xt.application.tools import ToolSettings

    with pytest.raises(ValueError):
        ToolSettings(confirm_base_url="xt.example.test")
    with pytest.raises(ValueError):
        ToolSettings(confirm_base_url="https://x", enabled_capabilities=frozenset({"delete"}))
    with pytest.raises(ValueError):
        ToolSettings(confirm_base_url="https://x", channels=("cli",))
    for bad in ("http://xt.example.vn", "https://a@b.vn", "https://b.vn/?x=1", "https://b.vn/#f"):
        with pytest.raises(ValueError):
            ToolSettings(confirm_base_url=bad)
    assert (
        ToolSettings(confirm_base_url="http://127.0.0.1:8080").confirm_url("c").startswith("http")
    )
    s = ToolSettings(confirm_base_url="https://xt.example.vn/")
    assert s.confirm_url("cf_1") == "https://xt.example.vn/desk/confirm/cf_1"
    assert replace(s, channels=("desk", "telegram")).channels == ("desk", "telegram")


def test_every_error_code_path_was_exercised() -> None:
    """Chạy sau cùng trong module: mọi mã lỗi của §8 có ít nhất một đường được test."""
    codes = {c for _, c in SEEN}
    expected = {e.value for e in ErrorCode}
    assert expected - codes == set(), expected - codes
    assert NOW.tzinfo is not None
