# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Incident replay rechecks the person's current IAM and resource conditions."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import UUID

import pytest

from xt.adapters.db.integration import TenantProfile
from xt.domain.model.context import ModelActor, TenantRef
from xt.domain.model.errors import ModelError
from xt.entrypoints.composition import pack_recovery

ACTOR = ModelActor(TenantRef(1, UUID(int=1)), UUID(int=2), ("xt_workflow",))
PROFILE = TenantProfile(ACTOR.tenant, "Acme", "acme", "UTC", "masked", 0)
RID = UUID(int=7)
REF = f"approval:{RID}"


@pytest.fixture
def iam_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    person = SimpleNamespace(
        id=ACTOR.actor_id, active=True, roles=("approver",), unit_key=None, limit_amount=None
    )
    policy: dict[str, Any] = {
        "version": "1",
        "tenant": "acme",
        "statements": [
            {
                "id": "approve",
                "effect": "Allow",
                "subjects": ["role:approver"],
                "actions": ["request.decide", "record.transition"],
                "resources": ["xrn:acme:*"],
                "conditions": [{"left": "resource.amount", "op": "le", "right": 100}],
            }
        ],
    }
    rt = SimpleNamespace(uow=object(), store=Mock(), records=Mock())
    rt.store.person.return_value = person
    rt.store.active_policy.return_value = (1, policy)
    rt.records.get.return_value = {"record": {"amount": 50, "owner_id": UUID(int=3)}}
    workflow = Mock()
    workflow.ref_for.return_value = REF
    rt.store.bindings.return_value = {"approval": SimpleNamespace(primitive="request")}
    monkeypatch.setattr(pack_recovery, "PgWorkflowStore", lambda uow: workflow)
    return rt


@pytest.mark.parametrize("source", ["decision", "record_patch", "direct"])
def test_replay_denies_active_original_approver_after_role_revocation(
    iam_runtime: Any,
    source: str,
) -> None:
    replay: dict[str, Any] = {"operation": "act"}
    if source == "decision":
        replay["decision"] = {"ref": REF}
    elif source == "record_patch":
        replay["record_patch"] = {"model": "approval", "record_id": str(RID)}
    pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", replay)
    iam_runtime.store.person.return_value.roles = ()

    with pytest.raises(ModelError) as caught:
        pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", replay)

    assert caught.value.code == "access_denied"
    assert caught.value.details == {"reason": "retry_permission_denied"}


def test_replay_checks_current_record_attributes(iam_runtime: Any) -> None:
    replay = {"operation": "act", "decision": {"ref": REF}}
    pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", replay)
    iam_runtime.records.get.return_value["record"]["amount"] = 150

    with pytest.raises(ModelError, match="permission"):
        pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", replay)


def test_replay_requires_every_recorded_tool_permission(iam_runtime: Any) -> None:
    iam_runtime.store.active_policy.return_value[1]["statements"][0]["actions"] = ["request.decide"]
    replay = {
        "operation": "act",
        "decision": {"ref": REF},
        "record_patch": {"model": "approval", "record_id": str(RID)},
    }
    with pytest.raises(ModelError, match="permission"):
        pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", replay)


@pytest.mark.parametrize("state", ["inactive", "missing", "no_policy"])
def test_replay_fails_closed_without_active_person_or_policy(iam_runtime: Any, state: str) -> None:
    if state == "inactive":
        iam_runtime.store.person.return_value.active = False
    elif state == "missing":
        iam_runtime.store.person.return_value = None
    else:
        iam_runtime.store.active_policy.return_value = None
    with pytest.raises(ModelError) as caught:
        pack_recovery._authorize_replay(
            iam_runtime,
            ACTOR,
            PROFILE,
            "instance-a",
            {"operation": "act", "decision": {"ref": REF}},
        )
    assert caught.value.details == {"reason": "retry_permission_denied"}


def test_tick_needs_no_human_permission(iam_runtime: Any) -> None:
    pack_recovery._authorize_replay(
        iam_runtime, ACTOR, PROFILE, "instance-a", {"operation": "tick"}
    )
    iam_runtime.store.person.assert_not_called()
    iam_runtime.store.active_policy.assert_not_called()


def test_internal_unbound_act_keeps_runtime_assignment_checks(iam_runtime: Any) -> None:
    iam_runtime.store.bindings.return_value = {}
    iam_runtime.store.active_policy.return_value = None
    pack_recovery._authorize_replay(iam_runtime, ACTOR, PROFILE, "instance-a", {"operation": "act"})
    iam_runtime.store.person.assert_called_once_with(ACTOR, ACTOR.actor_id)
    iam_runtime.store.active_policy.assert_not_called()
