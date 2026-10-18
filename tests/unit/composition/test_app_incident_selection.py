# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Selection excludes unrelated, closed, malformed and legacy process failures."""

from copy import deepcopy
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from xt.adapters.db.process_incidents import app_incidents_for, list_app_incidents
from xt.domain.model.context import TenantRef

INCIDENT: dict[str, Any] = {
    "id": "incident-1",
    "status": "open",
    "code": "capability_unavailable",
    "reason": "app_disabled",
    "pack": "crm",
    "model": "lead",
    "retry": {
        "operation": "act",
        "task_id": "task-1",
        "actor": str(uuid4()),
        "outcome": "approve",
        "comment": None,
        "kwargs": {},
    },
}


@pytest.mark.parametrize(
    "change",
    [
        {"code": "internal_error"},
        {"reason": "permission_denied"},
        {"status": "resolved"},
        {"pack": "hr"},
        {"model": "employee"},
        {"id": None},
        {"retry": None},
        {"retry": {"operation": "act"}},
        {"retry": "legacy text"},
    ],
)
def test_only_replayable_app_failures_are_listed(change: dict[str, Any]) -> None:
    state = {"meta": {"incidents": [{**INCIDENT, **change}]}}
    assert app_incidents_for("instance", state, "crm", ["lead"]) == []


def test_replay_hash_covers_original_action_without_exposing_it() -> None:
    incident = deepcopy(INCIDENT)
    state = {"meta": {"incidents": [incident]}}
    (before,) = app_incidents_for("instance", state, "crm", ["lead"])
    incident["retry"]["outcome"] = "reject"
    (after,) = app_incidents_for("instance", state, "crm", ["lead"])
    assert before["replay_digest"] != after["replay_digest"]
    assert "retry" not in before


def test_listing_is_explicitly_tenant_scoped_and_read_only() -> None:
    tenant = TenantRef(1, uuid4())
    conn = MagicMock()
    cursor = conn.cursor.return_value.__enter__.return_value
    cursor.execute.return_value.fetchall.return_value = [
        {"id": "instance", "state": {"meta": {"incidents": [INCIDENT]}}}
    ]
    found = list_app_incidents(conn, tenant, "crm", ["lead"])
    assert found[0]["instance"] == "instance"
    query, params = cursor.execute.call_args.args
    assert query.startswith("SELECT ")
    assert "WHERE tenant_id=%s" in query and "status='running'" in query
    assert params == (tenant.id,)
    cursor.execute.assert_called_once()
