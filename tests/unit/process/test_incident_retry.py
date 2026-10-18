# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Structured incidents preserve failed actions for safe, audited replay after app enable."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from tests.unit.process.conftest import MakeRt
from tests.unit.process.support import Services
from tests.unit.process.test_service_and_hardening import _service
from xt.adapters.workflow.bpmn.spiff import SpiffWorkflowHandle
from xt.application.process.memory import InMemoryInstanceStore
from xt.application.process.service import InMemoryProcessCatalog, ProcessService
from xt.application.tools.errors import ToolError
from xt.contracts.tools import ErrorCode
from xt.domain.model.errors import ModelError
from xt.domain.process.errors import EngineError

DETAILS = {"reason": "app_disabled", "model": "advance", "pack": "finance"}


def disabled(kind: str = "model") -> Exception:
    if kind == "tool":
        return ToolError(ErrorCode.CAPABILITY_UNAVAILABLE, "App disabled", details=DETAILS)
    return ModelError("capability_unavailable", "App disabled", details=DETAILS)


class TrackedService(ProcessService):
    def __init__(self, base: ProcessService) -> None:
        super().__init__(base.catalog, base.store)
        self.acts: list[tuple[Any, ...]] = []

    def act(
        self,
        instance_id: str,
        task_id: str,
        actor: str,
        outcome: str,
        comment: str | None = None,
        **kw: Any,
    ) -> dict[str, Any]:
        self.acts.append((instance_id, task_id, actor, outcome, comment, kw))
        return super().act(instance_id, task_id, actor, outcome, comment, **kw)


def fail_action(kind: str = "model") -> tuple[TrackedService, Any, Services, str, str, EngineError]:
    services = Services()
    base, store = _service(services)
    svc = TrackedService(base)

    def boom(**kw: Any) -> None:
        if kind == "text":
            raise RuntimeError("capability_unavailable app_disabled advance finance")
        try:
            raise disabled(kind)
        except Exception as cause:
            if kind == "context":
                raise RuntimeError("wrapper") from None
            raise RuntimeError("wrapper") from cause

    services["post_to_ledger"] = boom
    out = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    iid, tid = out["instance"], out["tasks"][0]["task_id"]
    before = store.records[iid].state["wf"]
    with pytest.raises(EngineError) as caught:
        svc.act(iid, tid, "NV-0005", "approve", "Confirmed")
    assert store.records[iid].state["wf"] == before
    assert svc.get(iid)["history"] == []
    return svc, store, services, iid, tid, caught.value


@pytest.mark.parametrize("kind", ["model", "tool", "context"])
def test_incident_has_structured_failure_and_exact_replay(kind: str) -> None:
    svc, store, _, iid, tid, error = fail_action(kind)
    (inc,) = svc.get(iid)["incidents"]
    assert UUID(inc["id"]) and inc["status"] == "open"
    assert {k: inc[k] for k in DETAILS} == DETAILS
    assert inc["code"] == "capability_unavailable" and inc["attempts"] == 0
    assert inc["retry"] == {
        "operation": "act",
        "task_id": tid,
        "actor": "NV-0005",
        "outcome": "approve",
        "comment": "Confirmed",
        "kwargs": {},
    }
    assert error.incident == inc
    assert store.records[iid].state["meta"]["events"][-1]["id"] == inc["id"]


def test_retry_calls_service_override_once_and_audits_resolution() -> None:
    svc, store, services, iid, tid, _ = fail_action()
    (inc,) = svc.get(iid)["incidents"]
    runtime = svc.catalog.get("proposal_approval", 2)
    assert runtime is not None
    delivered: list[dict[str, Any]] = []
    runtime.on_event = delivered.append
    services["post_to_ledger"] = Services()["post_to_ledger"]
    svc.acts.clear()
    out = svc.retry_incident(iid, inc["id"], retried_by="user:admin")
    assert out["retried"] and out["status"] == "completed"
    assert svc.acts == [(iid, tid, "NV-0005", "approve", "Confirmed", {})]
    (resolved,) = svc.get(iid)["incidents"]
    assert resolved["status"] == "resolved" and resolved["attempts"] == 1
    assert resolved["retried_by"] == "user:admin"
    events = store.records[iid].state["meta"]["events"]
    assert [e["type"] for e in events].count("incident.retry") == 1
    assert [e["type"] for e in delivered].count("incident.retry") == 1
    assert not svc.retry_incident(iid, inc["id"], retried_by="user:admin")["retried"]
    assert len(svc.acts) == 1 and len(svc.get(iid)["history"]) == 1


def test_failed_retry_preserves_open_incident_and_workflow_snapshot() -> None:
    svc, store, _, iid, _, _ = fail_action()
    (inc,) = svc.get(iid)["incidents"]
    before = store.records[iid].state["wf"]
    with pytest.raises(EngineError) as caught:
        svc.retry_incident(iid, inc["id"], retried_by="admin")
    assert store.records[iid].state["wf"] == before
    original, retry_failure = svc.get(iid)["incidents"]
    assert original["status"] == retry_failure["status"] == "open"
    assert original["id"] == inc["id"] != retry_failure["id"]
    assert caught.value.incident == retry_failure
    assert svc.get(iid)["history"] == []


def test_retry_audit_delivery_failure_propagates_to_caller_unit_of_work() -> None:
    svc, _, services, iid, _, _ = fail_action()
    (inc,) = svc.get(iid)["incidents"]
    runtime = svc.catalog.get("proposal_approval", 2)
    assert runtime is not None

    def unavailable(event: dict[str, Any]) -> None:
        if event["type"] == "incident.retry":
            raise RuntimeError("audit unavailable")

    runtime.on_event = unavailable
    services["post_to_ledger"] = Services()["post_to_ledger"]
    # The composition unit of work must see this error and roll the attempt back.
    with pytest.raises(RuntimeError, match="audit unavailable"):
        svc.retry_incident(iid, inc["id"], retried_by="admin")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retry", None),
        ("retry", "tick"),
        ("retry", []),
        ("retry", {"operation": "act"}),
        ("actor", None),
        ("actor", ""),
        ("task_id", None),
        ("task_id", ""),
        ("outcome", None),
        ("outcome", ""),
        ("kwargs", None),
        ("kwargs", []),
        ("kwargs", {"unknown": True}),
        ("comment", 17),
        ("operation", "tick"),
        ("kwargs", {"updates": "bad"}),
        ("kwargs", {"ask": 17}),
        ("kwargs", {"delegate_to": 17}),
    ],
)
def test_malformed_replay_is_skipped_without_running_operations(field: str, value: Any) -> None:
    svc, store, _, iid, _, _ = fail_action()
    inc = store.records[iid].state["meta"]["incidents"][0]
    if field == "retry":
        inc["retry"] = value
    else:
        inc["retry"][field] = value
    svc.acts.clear()
    assert not svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]
    assert svc.acts == []


def test_successful_manual_action_resolves_old_incident() -> None:
    svc, _, services, iid, tid, _ = fail_action()
    (inc,) = svc.get(iid)["incidents"]
    services["post_to_ledger"] = Services()["post_to_ledger"]
    svc.act(iid, tid, "NV-0005", "approve")
    assert svc.get(iid)["incidents"][0]["status"] == "resolved"
    assert not svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]


@pytest.mark.parametrize("change", ["reason", "code", "stale_task", "withdrawn", "completed"])
def test_retry_never_replays_unrelated_or_stale_actions(change: str) -> None:
    svc, store, services, iid, _, _ = fail_action()
    inc = store.records[iid].state["meta"]["incidents"][0]
    if change in {"reason", "code"}:
        inc[change] = "unrelated"
    elif change == "stale_task":
        inc["retry"]["task_id"] = "past-task"
    elif change == "withdrawn":
        svc.withdraw(iid, "NV-0006", "Withdrawn")
    else:
        services["post_to_ledger"] = Services()["post_to_ledger"]
        svc.act(iid, inc["retry"]["task_id"], "NV-0005", "approve")
        # Even an old externally imported open incident cannot replay a finished process.
        store.records[iid].state["meta"]["incidents"][0]["status"] = "open"
    svc.acts.clear()
    assert not svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]
    assert svc.acts == []


def test_error_message_alone_does_not_qualify_for_app_recovery() -> None:
    svc, store, _, iid, _, _ = fail_action("text")
    inc = store.records[iid].state["meta"]["incidents"][0]
    assert inc["reason"] == "engine_error" and inc["code"] == "internal_error"
    assert not svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]


def test_timer_failure_has_retry_payload_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    services = Services()
    svc, store = _service(services)
    out = svc.start("proposal_approval", {"kind": "advance_request", "amount": 1}, "NV-0006")
    iid = out["instance"]
    original = SpiffWorkflowHandle.refresh_timers

    def fail(self: SpiffWorkflowHandle) -> None:
        raise disabled()

    monkeypatch.setattr(SpiffWorkflowHandle, "refresh_timers", fail)
    before = store.records[iid].state["wf"]
    with pytest.raises(EngineError) as caught:
        svc.tick(iid)
    assert store.records[iid].state["wf"] == before
    (inc,) = svc.get(iid)["incidents"]
    assert inc["retry"] == {"operation": "tick"}
    assert caught.value.incident == inc
    monkeypatch.setattr(SpiffWorkflowHandle, "refresh_timers", original)
    assert svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]
    assert svc.get(iid)["incidents"][0]["status"] == "resolved"


@pytest.mark.parametrize(
    ("outcome", "kwargs"),
    [
        ("delegate", {"delegate_to": "NV-0002"}),
        ("request_info", {"ask": "NV-0002"}),
        ("resubmit", {"updates": {"amount": 7}}),
    ],
)
def test_incident_replays_optional_action_arguments(
    make_rt: MakeRt, monkeypatch: pytest.MonkeyPatch, outcome: str, kwargs: dict[str, Any]
) -> None:
    rt = make_rt(
        {
            "id": "retry_inputs",
            "name": "Retry inputs",
            "version": 1,
            "roles": ["director"],
            "inputs": {"amount": {"type": "int", "editable_on_revision": True}},
            "steps": [
                {
                    "id": "approve",
                    "type": "approve",
                    "name": "Approve",
                    "assignee": "role(director)",
                    "outcomes": ["approve", "return_for_revision", "delegate", "request_info"],
                    "on_return": {"resume": "restart", "max_rounds": 2},
                }
            ],
        }
    )
    catalog = InMemoryProcessCatalog()
    catalog.add(rt)
    svc = TrackedService(ProcessService(catalog, InMemoryInstanceStore()))
    out = svc.start("retry_inputs", {"amount": 1}, "NV-0006")
    iid, actor = out["instance"], "NV-0001"
    if outcome == "resubmit":
        out = svc.act(iid, out["tasks"][0]["task_id"], actor, "return_for_revision", "Revise")
        actor = "NV-0006"
    tid = out["tasks"][0]["task_id"]
    original = SpiffWorkflowHandle.complete

    def fail(self: SpiffWorkflowHandle, *args: Any, **kw: Any) -> None:
        original(self, *args, **kw)
        raise disabled()

    monkeypatch.setattr(SpiffWorkflowHandle, "complete", fail)
    with pytest.raises(EngineError):
        svc.act(iid, tid, actor, outcome, "Details", **kwargs)
    (inc,) = svc.get(iid)["incidents"]
    assert inc["retry"]["kwargs"] == kwargs
    monkeypatch.setattr(SpiffWorkflowHandle, "complete", original)
    svc.acts.clear()
    assert svc.retry_incident(iid, inc["id"], retried_by="admin")["retried"]
    assert svc.acts == [(iid, tid, actor, outcome, "Details", kwargs)]
