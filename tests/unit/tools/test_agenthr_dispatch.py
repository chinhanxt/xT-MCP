# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dispatcher gọi cổng AgentHR (D-AGENTHR-1) đúng chỗ: chỉ cho agent; trạng thái/ca cho mọi tool;
JD/hạn mức/giấy phép cho tool ghi, TRƯỚC kiểm tự chủ và trong transaction của lời gọi; thao tác chờ
xác nhận không chạy khi agent đã bị đình chỉ. Không có cổng (None) = hành vi cũ."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from tests.unit.tools.fakes import agent_ctx, human_ctx, later, make_dispatcher, make_world
from xt.application.tools import ConfirmationService
from xt.application.tools.context import CallContext
from xt.application.tools.errors import access_denied
from xt.application.tools.ports import Args, MutationPlan, PendingStatus
from xt.domain.autonomy import RiskLevel

CREATE: dict[str, Any] = {
    "model_id": "advance_request",
    "values": {"amount": "8000000", "purpose": "Công tác Đà Nẵng"},
    "idempotency_key": "adv-hr-0001",
}


class FakeGate:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.writes: list[tuple[str, dict[str, Any], str]] = []
        self.block_call: str | None = None
        self.block_write: str | None = None

    def check_call(self, ctx: CallContext, tool: str) -> None:
        self.calls.append((ctx.actor.key, tool))
        if self.block_call:
            raise access_denied("blocked", self.block_call, None, remediation="Explain it.")

    def check_write(self, ctx: CallContext, tool: str, args: Args, plan: MutationPlan) -> None:
        self.writes.append((tool, dict(args), plan.summary))
        if self.block_write:
            raise access_denied("blocked", self.block_write, None, remediation="Explain it.")


def _setup() -> tuple[Any, Any, FakeGate]:
    world = make_world()
    gate = FakeGate()
    world.services = replace(world.services, agenthr=gate)
    return world, make_dispatcher(world), gate


def test_humans_never_reach_the_gate() -> None:
    _, d, gate = _setup()
    out = d.call("create_record", CREATE, human_ctx())
    assert not out.is_error, out.envelope
    assert gate.calls == [] and gate.writes == []


def test_agent_read_passes_status_gate_only() -> None:
    _, d, gate = _setup()
    out = d.call("search_records", {"model_id": "advance_request"}, agent_ctx())
    assert not out.is_error, out.envelope
    assert [t for _, t in gate.calls] == ["search_records"] and gate.writes == []


def test_status_block_stops_reads_with_reason_and_remediation() -> None:
    _, d, gate = _setup()
    gate.block_call = "agent_suspended"
    out = d.call("search_records", {"model_id": "advance_request"}, agent_ctx())
    assert out.is_error
    e = out.envelope["error"]
    assert e["code"] == "access_denied" and e["details"]["reason"] == "agent_suspended"
    assert e["remediation"] == "Explain it."


def test_write_gate_runs_with_plan_and_success_commits() -> None:
    world, d, gate = _setup()
    out = d.call("create_record", CREATE, agent_ctx())
    assert not out.is_error, out.envelope
    assert [w[0] for w in gate.writes] == ["create_record"]
    assert gate.writes[0][1]["model_id"] == "advance_request" and gate.writes[0][2]
    assert world.store.commits == ["create"]


def test_write_block_rolls_back_and_wins_over_autonomy() -> None:
    world, d, gate = _setup()
    world.access.ceiling = RiskLevel.L0  # không có cổng: autonomy_hard_limit
    gate.block_write = "outside_job_description"
    out = d.call("create_record", CREATE, agent_ctx())
    assert out.envelope["error"]["details"]["reason"] == "outside_job_description"
    assert world.store.commits == [] and world.transactions.rollbacks == 1
    # claim của idempotency_key đã rollback: gọi lại (sau khi người sửa JD) chạy bình thường
    gate.block_write = None
    world.access.ceiling = RiskLevel.L1
    assert not d.call("create_record", CREATE, agent_ctx()).is_error


def test_without_gate_behaviour_is_unchanged() -> None:
    world = make_world()
    assert world.services.agenthr is None
    world.access.ceiling = RiskLevel.L0
    out = make_dispatcher(world).call("create_record", CREATE, agent_ctx())
    assert out.envelope["error"]["details"]["reason"] == "autonomy_hard_limit"


def test_pending_confirmation_does_not_run_after_suspension() -> None:
    world, d, gate = _setup()
    update_task = {
        "ref": "task:t1",
        "set": {"assignee": "person:binh"},
        "expected_revision": 5,
        "reason": "Anh Tuấn nghỉ phép",
        "idempotency_key": "task-t1-reassign",
    }
    out = d.call("update_record", update_task, agent_ctx())  # L2 -> chờ người xác nhận
    assert out.envelope["error"]["code"] == "confirmation_required", out.envelope
    cid = out.envelope["error"]["details"]["confirmation_id"]
    gate.block_call = "agent_suspended"  # người phụ trách bấm Đình chỉ trong lúc chờ
    res = ConfirmationService(d).confirm(cid, "sub-viet", later(1), "tr_cf")
    assert res.status is PendingStatus.INVALIDATED
    assert res.error is not None and res.error["details"]["reason"] == "agent_suspended"
    assert gate.calls[-1][1] == "update_record" and world.store.commits == []
