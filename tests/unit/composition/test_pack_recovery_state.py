# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Recovered workflows mirror bound request state in the same unit of work."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from xt.domain.model.context import ModelActor, TenantRef
from xt.entrypoints.composition import pack_recovery

RID = UUID(int=7)
ACTOR = ModelActor(TenantRef(1, UUID(int=1)), UUID(int=2), ("xt_workflow",))


@pytest.mark.parametrize(
    ("status", "tasks", "expected"),
    [
        ("completed", [], "approved"),
        ("rejected", [], "rejected"),
        ("withdrawn", [], "withdrawn"),
        ("running", [{"kind": "revise"}], "returned"),
        ("running", [{"kind": "decision"}], "in_review"),
    ],
)
@pytest.mark.parametrize("field", ["state", "phase"])
def test_recovery_syncs_configured_request_state_without_duplicate_revision(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    tasks: list[dict[str, str]],
    expected: str,
    field: str,
) -> None:
    actor = ACTOR
    rt: Any = SimpleNamespace(uow=object(), store=Mock(), records=Mock())
    workflow = Mock()
    workflow.ref_for.return_value = f"approval:{RID}"
    monkeypatch.setattr(pack_recovery, "PgWorkflowStore", lambda uow: workflow)
    config = {} if field == "state" else {"state_field": field}
    rt.store.bindings.return_value = {
        "approval": SimpleNamespace(primitive="request", config=config)
    }
    rt.records.get.return_value = {"record": {"revision": 7, field: "draft"}}
    result = {"status": status, "tasks": tasks}

    pack_recovery._sync_request(rt, actor, "instance-a", result)

    workflow.ref_for.assert_called_once_with("instance-a")
    rt.store.bindings.assert_called_once_with(actor)
    rt.records.get.assert_called_once_with(actor, "approval", RID)
    rt.records.workflow_write.assert_called_once_with(actor, "approval", RID, 7, {field: expected})

    # Matching state is a successful no-op, so an idempotent replay cannot bump revision.
    rt.records.workflow_write.reset_mock()
    rt.records.get.return_value = {"record": {"revision": 8, field: expected}}
    pack_recovery._sync_request(rt, actor, "instance-a", result)
    rt.records.workflow_write.assert_not_called()


@pytest.mark.parametrize("primitive", [None, "record", "task"])
def test_recovery_does_not_write_unbound_or_non_request_subjects(
    monkeypatch: pytest.MonkeyPatch,
    primitive: str | None,
) -> None:
    rt: Any = SimpleNamespace(uow=object(), store=Mock(), records=Mock())
    workflow = Mock()
    workflow.ref_for.return_value = f"ordinary:{RID}"
    monkeypatch.setattr(pack_recovery, "PgWorkflowStore", lambda uow: workflow)
    rt.store.bindings.return_value = (
        {} if primitive is None else {"ordinary": SimpleNamespace(primitive=primitive)}
    )

    pack_recovery._sync_request(rt, ACTOR, "instance-a", {"status": "completed", "tasks": []})

    rt.records.get.assert_not_called()
    rt.records.workflow_write.assert_not_called()


def test_recovery_without_subject_needs_no_model_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    rt: Any = SimpleNamespace(uow=object(), store=Mock(), records=Mock())
    workflow = Mock()
    workflow.ref_for.return_value = None
    monkeypatch.setattr(pack_recovery, "PgWorkflowStore", lambda uow: workflow)

    pack_recovery._sync_request(rt, ACTOR, "instance-a", {"status": "completed", "tasks": []})

    rt.store.bindings.assert_not_called()
    rt.records.get.assert_not_called()
    rt.records.workflow_write.assert_not_called()


def test_confirmed_request_state_write_keeps_cas_if_record_changes_during_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actor: Any = object()
    rt: Any = SimpleNamespace(uow=object(), store=Mock(), records=Mock())
    workflow = Mock()
    workflow.ref_for.return_value = f"approval:{RID}"
    monkeypatch.setattr(pack_recovery, "PgWorkflowStore", lambda uow: workflow)
    rt.store.bindings.return_value = {"approval": SimpleNamespace(primitive="request", config={})}
    rt.records.get.return_value = {"record": {"revision": 8, "state": "in_review"}}

    pack_recovery._sync_request(
        rt, actor, "instance-a", {"status": "completed", "tasks": []}, expected_revision=7
    )

    rt.records.workflow_write.assert_called_once_with(
        actor, "approval", RID, 7, {"state": "approved"}
    )
