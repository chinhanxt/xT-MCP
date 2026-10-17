# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Create PEP uses the proposed tenant department and owner."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from xt.application.tools.ports import AccessCheck
from xt.entrypoints.composition.runtime import iam_person
from xt.entrypoints.composition.security import ToolAccessGuard


def test_create_record_and_task_use_proposed_department_and_owner() -> None:
    tenant, owner_id, other_id = uuid4(), uuid4(), uuid4()
    own_dept, other_dept, foreign_dept = uuid4(), uuid4(), uuid4()
    owner = SimpleNamespace(
        id=owner_id, roles=("dept_head",), active=True, unit_key="sales", limit_amount=None
    )
    other = SimpleNamespace(
        id=other_id, roles=("employee",), active=True, unit_key="other", limit_amount=None
    )
    units = {own_dept: SimpleNamespace(key="sales"), other_dept: SimpleNamespace(key="other")}
    policy = {
        "version": "1",
        "tenant": "acme",
        "statements": [
            {
                "id": "same-dept-create",
                "effect": "Allow",
                "subjects": ["role:dept_head"],
                "actions": ["record.create", "task.create"],
                "resources": ["xrn:acme:request:*", "xrn:acme:task:*"],
                "conditions": [
                    {
                        "left": "resource.department",
                        "op": "eq",
                        "right": {"ref": "subject.department"},
                    }
                ],
            }
        ],
    }
    store = SimpleNamespace(
        active_policy=lambda actor: (1, policy),
        unit=lambda actor, *, uid: units.get(uid),
        person=lambda actor, uid: {owner_id: owner, other_id: other}.get(uid),
    )
    caller = SimpleNamespace(
        actor=object(),
        tenant=SimpleNamespace(id=tenant),
        person=owner,
        alias="acme",
        iam=lambda: iam_person("acme", owner),
    )
    rt = SimpleNamespace(store=store, caller=lambda ctx: caller)
    guard = ToolAccessGuard(rt)
    ctx = SimpleNamespace(tenant="acme", now=datetime.now(UTC), confirmed_by=(), trace_id="test")

    for tool, action, attrs in (
        ("create_record", "record.create", {"model_id": "request", "values": {}}),
        ("create_task", "task.create", {"task_type": "task", "values": {}}),
    ):

        def decision(values, tool=tool, action=action, attrs=attrs):
            return guard.check(ctx, AccessCheck(tool, action, None, {**attrs, "values": values}))

        assert decision({"department_id": str(own_dept)}).allowed
        assert not decision({"department_id": str(other_dept)}).allowed
        assert not decision({"department_id": str(foreign_dept)}).allowed
        assert not decision({"department_id": str(uuid4())}).allowed
        assert not decision({"owner_id": str(other_id)}).allowed
        assert not decision({"owner_id": str(uuid4())}).allowed
        assert decision({"owner_id": "me"}).allowed
        assert decision({}).allowed  # valid default self-owned creation
    assert guard.check(ctx, AccessCheck("create_task", "task.create", None, {"values": {}})).allowed
