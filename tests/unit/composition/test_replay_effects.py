# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Recovery preserves the surrounding confirmed action, including decision ledger and patch."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from xt.domain.model.errors import ModelError
from xt.entrypoints.composition import pack_recovery as recovery

RID, PERSON, REQUESTER = UUID(int=1), UUID(int=2), UUID(int=3)


def test_recovery_restores_confirmed_decision_and_step_patch() -> None:
    rt: Any = SimpleNamespace(store=Mock(), records=Mock())
    actor: Any = object()
    replay: dict[str, Any] = {
        "record_patch": {
            "model": "task",
            "record_id": str(RID),
            "expected_revision": 5,
            "state_field": "state",
            "to_state": "done",
            "values": {"note": "ok"},
        },
        "decision": {
            "kind": "request",
            "ref": f"task:{RID}",
            "step": "approve",
            "decision": "approve",
            "approver": str(PERSON),
            "requester": str(REQUESTER),
            "actor_kind": "agent",
            "actor_client": "desk",
            "confirmed_by": ["kc-person"],
            "instance_id": "i",
            "task_id": "t",
        },
    }
    rt.records.get.return_value = {"record": {"revision": 4}}
    with pytest.raises(ModelError, match="revision"):
        recovery._check_patch_revision(rt, actor, replay)
    rt.records.workflow_write.assert_not_called()
    rt.store.record_decision.assert_not_called()
    rt.records.get.return_value = {"record": {"revision": 5}}
    recovery._check_patch_revision(rt, actor, replay)
    # The service task can advance the record revision before the final confirmed patch.
    rt.records.get.return_value = {"record": {"revision": 6, "state": "open", "note": "old"}}
    recovery._finish_replay(rt, actor, replay)
    decision = {**replay["decision"], "approver": PERSON, "requester": REQUESTER}
    rt.store.record_decision.assert_called_once_with(actor, **decision)
    rt.records.workflow_write.assert_called_once_with(
        actor, "task", RID, 6, {"note": "ok", "state": "done"}
    )


def test_service_only_replay_needs_no_surrounding_action() -> None:
    rt: Any = SimpleNamespace(store=Mock(), records=Mock())
    actor: Any = object()
    recovery._check_patch_revision(rt, actor, {"operation": "tick"})
    recovery._finish_replay(rt, actor, {"operation": "tick"})
    rt.records.get.assert_not_called()
    rt.store.record_decision.assert_not_called()


def test_confirmed_request_replay_keeps_original_revision_guard() -> None:
    rt: Any = SimpleNamespace(records=Mock())
    actor: Any = object()
    replay = {
        "request_revision": {
            "model": "approval",
            "record_id": str(RID),
            "expected_revision": 5,
        }
    }
    rt.records.get.return_value = {"record": {"revision": 6}}
    with pytest.raises(ModelError, match="revision"):
        recovery._check_patch_revision(rt, actor, replay)
