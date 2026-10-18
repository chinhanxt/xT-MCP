# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""An incident must survive the failed tool action's savepoint rollback."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.domain.model.errors import ModelError
from xt.domain.process.errors import EngineError
from xt.entrypoints.composition import process


def test_failed_tool_retains_exact_incident_after_rollback(monkeypatch: pytest.MonkeyPatch) -> None:
    incident = {
        "id": "incident-a",
        "reason": "app_disabled",
        "status": "open",
        "pack": "crm",
        "model": "crm_m",
        "retry": {"operation": "tick"},
    }
    record = SimpleNamespace(state={"meta": {"incidents": []}})
    store = Mock()
    store.get_for_update.return_value = record
    rt: Any = SimpleNamespace(store=Mock(), uow=Mock())
    adapter = process.ProcessToolAdapter(rt, cursor_key=b"test", engine=Mock())
    pending: list[Any] = []
    monkeypatch.setattr(process, "after_rollback", pending.append)
    monkeypatch.setattr(process, "PgWorkflowStore", lambda uow: store)
    caller: Any = SimpleNamespace(workflow=lambda: "workflow-actor")
    adapter._incident(caller, "instance-a", EngineError("failed", incident=incident))
    assert not store.save.called
    pending[0]()
    assert record.state["meta"]["incidents"] == [incident]
    store.save.assert_called_once_with("instance-a", record)
    pending[0]()
    assert record.state["meta"]["incidents"] == [incident]


def test_stock_failure_schedules_incident_before_specialized_tool_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt: Any = SimpleNamespace(store=Mock(), uow=Mock())
    adapter = process.ProcessToolAdapter(rt, cursor_key=b"test", engine=Mock())
    service = Mock()
    incident: dict[str, Any] = {"id": "incident-a", "retry": {"operation": "act"}}
    error = EngineError("app unavailable", incident=incident)
    error.__cause__ = ModelError(
        "capability_unavailable", "App disabled", details={"reason": "app_disabled"}
    )
    service.act.side_effect = error
    monkeypatch.setattr(adapter, "pc", lambda caller: SimpleNamespace(service=lambda: service))
    monkeypatch.setattr(
        adapter,
        "_request",
        lambda *args: (None, None, {}, {"revision": 1, "owner_id": UUID(int=3)}),
    )
    recorded = Mock()
    monkeypatch.setattr(adapter, "_incident", recorded)
    ctx: Any = SimpleNamespace(
        actor=SimpleNamespace(kind=SimpleNamespace(value="human"), client_id="desk"),
        confirmed_by=("confirmed-user",),
    )
    caller: Any = SimpleNamespace(actor=object())
    plan = process.DecidePlan(
        "actor",
        "stock_request:record",
        "stock_request",
        UUID(int=1),
        1,
        "instance-a",
        "task-a",
        "approve",
        "Approve",
        "approve",
        "approve",
        None,
        None,
        str(UUID(int=2)),
    )

    with pytest.raises(ToolError) as caught:
        adapter._commit_decide(ctx, caller, plan)

    assert caught.value.code == ErrorCode.CAPABILITY_UNAVAILABLE
    assert caught.value.details == {"reason": "app_disabled"}
    recorded.assert_called_once_with(caller, "instance-a", error)
    assert incident["retry"]["decision"] == {
        "kind": "request",
        "ref": "stock_request:record",
        "step": "approve",
        "decision": "approve",
        "approver": str(UUID(int=2)),
        "requester": str(UUID(int=3)),
        "actor_kind": "human",
        "actor_client": "desk",
        "confirmed_by": ("confirmed-user",),
        "instance_id": "instance-a",
        "task_id": "task-a",
    }
    assert incident["retry"]["request_revision"] == {
        "model": "stock_request",
        "record_id": str(UUID(int=1)),
        "expected_revision": 1,
    }
