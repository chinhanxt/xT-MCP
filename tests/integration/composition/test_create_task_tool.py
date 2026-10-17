# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""The installed task pack must support the public create_task tool end to end."""

from __future__ import annotations

import uuid

import pytest

from tests.integration.composition.shared_tenants import Ops

pytestmark = pytest.mark.integration


def test_create_task_uses_pack_binding_and_policy(ops: Ops) -> None:
    kind = ops.create("NV-0001", "task_type", {"code": "test_kind", "name": "Test kind"})
    args = {
        "title": "Complete the MCP task test",
        "assignee": "me",
        "description": "Evidence from a real composition call",
        "due_at": "2099-12-31",
        "priority": "normal",
        "values": {
            "task_no": "CV-2099-9901",
            "kind": f"task_type:{kind['id']}",
            "assigner": "me",
            "done_criteria": "Test result is recorded",
        },
        "idempotency_key": f"create-task-{uuid.uuid4().hex}",
    }
    first = ops.tool("NV-0009", "create_task", args)
    assert first["ok"], first
    task = first["data"]["task"]
    assert task["priority"] == "normal"
    assert task["assignee"]["ref"] == f"person:{ops.pid('NV-0009')}"
    assert ops.tool("NV-0009", "create_task", args)["data"]["task"]["ref"] == task["ref"]
    stored = ops.get("task", task["ref"].split(":", 1)[1])
    assert stored["priority"] == "medium"
    assert stored["assigner"] == ops.pid("NV-0009")
    assert stored["kind"] == kind["id"]
    assert stored["done_criteria"] == "Test result is recorded"
    assert (
        len(ops.search("task", {"field": "task_no", "op": "=", "value": args["values"]["task_no"]}))
        == 1
    )
    denied = ops.tool("NV-0010", "create_task", {**args, "idempotency_key": "employee-no-task"})
    assert not denied["ok"] and denied["error"]["code"] == "access_denied"
    missing = ops.tool(
        "NV-0009",
        "create_task",
        {
            **args,
            "values": {},
            "idempotency_key": "missing-task-fields",
        },
    )
    assert not missing["ok"] and missing["error"]["code"] in {"invalid_request", "rule_violation"}
    for priority in ({"bad": "priority"}, ["normal"]):
        malformed_args = {k: v for k, v in args.items() if k != "priority"}
        malformed = ops.tool(
            "NV-0009",
            "create_task",
            {
                **malformed_args,
                "values": {**args["values"], "priority": priority},
                "idempotency_key": f"bad-priority-{uuid.uuid4().hex}",
            },
        )
        assert not malformed["ok"] and malformed["error"]["code"] in {
            "invalid_request",
            "rule_violation",
        }
    bad_date = ops.tool(
        "NV-0009",
        "create_task",
        {
            **args,
            "due_at": "2026-02-30",
            "idempotency_key": f"bad-date-{uuid.uuid4().hex}",
        },
    )
    assert not bad_date["ok"] and bad_date["error"]["code"] == "invalid_request"
    assert bad_date["error"]["details"]["reason"] == "bad_date"
    assert len(ops.search("task", {"field": "task_no", "op": "=", "value": "CV-2099-9901"})) == 1
