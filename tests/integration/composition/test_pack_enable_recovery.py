# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Real PostgreSQL coverage for pack-enable recovery of disabled-app incidents."""

from __future__ import annotations

import argparse
import re
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from tests.integration.composition.fixtures import run_admin
from tests.integration.composition.shared_tenants import Warehouse, install_warehouse, movements
from xt.adapters.db.workflow_bridge import PgWorkflowStore
from xt.domain.process.errors import EngineError
from xt.entrypoints.composition.process import ProcessContext
from xt.entrypoints.composition.runtime import Caller
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def recovery_warehouses(
    stack_base: Any, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Warehouse, Warehouse, dict[str, str]]]:
    env = stack_base.env
    aliases = ("recoverstock", "recoverforeign")
    tenants = [install_warehouse(env, tmp_path_factory, alias) for alias in aliases]
    prepared = prepare(
        env, argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    )
    services: Any = prepared.services
    warehouses = [
        Warehouse(services.runtime, services.engine, tenant, {}, prepared.dispatcher, alias=alias)
        for tenant, alias in zip(tenants, aliases, strict=True)
    ]
    try:
        yield warehouses[0], warehouses[1], env
    finally:
        prepared.close()


def _argv(w: Warehouse, action: str) -> list[str]:
    return [
        "pack", action, "--tenant", w.alias, "kho-hang", "--proposed-by", "agent:recovery",
        "--approved-by", "person:admin",
    ]  # fmt: skip


def _plan(w: Warehouse, env: dict[str, str], action: str) -> tuple[str, str]:
    rc, output = run_admin(_argv(w, action), env)
    assert rc == 0, output
    digest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", output)
    assert digest is not None, output
    return output, digest.group(1)


def _apply(w: Warehouse, env: dict[str, str], action: str) -> str:
    _, digest = _plan(w, env, action)
    rc, output = run_admin([*_argv(w, action), "--confirm", digest], env)
    assert rc == 0, output
    return output


def _request(w: Warehouse, *, kind: str = "receive") -> tuple[dict[str, Any], str, str]:
    warehouse = w.create(
        "NV-0001", "warehouse", {"code": uuid.uuid4().hex, "name": "Recovery warehouse"}
    )
    location = w.create(
        "NV-0001",
        "warehouse_location",
        {"code": uuid.uuid4().hex, "name": "Shelf", "warehouse": warehouse["id"]},
    )
    item = w.create(
        "NV-0001", "stock_item", {"sku": uuid.uuid4().hex, "name": "Recovery item", "unit": "pc"}
    )
    request = w.request(
        {
            "warehouse": warehouse["id"],
            "kind": kind,
            "lines": [{"item": item["id"], "location": location["id"], "quantity_milli": 1000}],
        }
    )

    def identify(_: Caller, pc: ProcessContext) -> tuple[str, str]:
        instance = PgWorkflowStore(w.rt.uow).instance_for(request["ref"])
        assert instance is not None
        (task,) = pc.service().get(instance)["tasks"]
        return instance, str(task["task_id"])

    instance, task = w.run("NV-0001", identify)
    return request, instance, task


def _fail_while_disabled(w: Warehouse, instance: str, task: str) -> None:
    def fail(_: Caller, pc: ProcessContext) -> None:
        # Catch inside the transaction so ProcessService's incident survives its EngineError.
        # This models a process already running when the application was disabled.
        with pytest.raises(EngineError):
            pc.service().act(instance, task, w.pid("NV-0001"), "approve", "Approved before outage")

    w.run("NV-0001", fail)


def _state(pg: Any, w: Warehouse, instance: str) -> tuple[str, dict[str, Any]]:
    with pg.su() as connection:
        row = connection.execute(
            "SELECT status, state FROM xt_meta.workflow_instance WHERE tenant_id=%s AND id=%s",
            (w.tenant.id, instance),
        ).fetchone()
    assert row is not None
    return str(row[0]), dict(row[1])


def _change_incident(pg: Any, w: Warehouse, instance: str, **changes: Any) -> None:
    _, state = _state(pg, w, instance)
    (incident,) = state["meta"]["incidents"]
    incident.update(changes)
    with pg.su() as connection:
        connection.execute(
            "UPDATE xt_meta.workflow_instance SET state=%s WHERE tenant_id=%s AND id=%s",
            (Jsonb(state), w.tenant.id, instance),
        )


def _retries(pg: Any, w: Warehouse) -> list[tuple[str, dict[str, Any]]]:
    with pg.su() as connection:
        rows = connection.execute(
            "SELECT instance_id,payload FROM xt_meta.process_event WHERE tenant_id=%s"
            " AND type='incident.retry' ORDER BY created_at,id",
            (w.tenant.id,),
        ).fetchall()
    return [(str(row[0]), dict(row[1])) for row in rows]


def _lifecycle_audit_count(pg: Any, w: Warehouse) -> int:
    with pg.su() as connection:
        row = connection.execute(
            "SELECT count(*) FROM xt_audit.event WHERE tenant_id=%s"
            " AND model='xt_meta.pack_install' AND op='UPDATE'",
            (w.tenant.id,),
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_enable_plan_is_read_only_and_recovers_running_process_once(
    recovery_warehouses: tuple[Warehouse, Warehouse, dict[str, str]], pg: Any
) -> None:
    w, foreign, env = recovery_warehouses
    request, instance, task = _request(w)
    _, foreign_instance, foreign_task = _request(foreign)
    _apply(w, env, "disable")
    _apply(foreign, env, "disable")
    _fail_while_disabled(w, instance, task)
    _fail_while_disabled(foreign, foreign_instance, foreign_task)
    before = _state(pg, w, instance)
    assert before[0] == "running" and before[1]["meta"]["incidents"]
    (incident,) = before[1]["meta"]["incidents"]
    assert incident["reason"] == "app_disabled"
    assert incident["model"] == "stock_request" and incident["pack"] == "kho-hang"
    assert incident["status"] == "open"

    plan, confirmation = _plan(w, env, "enable")
    assert instance in plan
    assert incident["id"] in plan
    assert "eligible=1" in plan
    assert foreign_instance not in plan
    assert _state(pg, w, instance) == before
    assert _retries(pg, w) == []

    output = _apply(w, env, "enable")
    for count in ("eligible=1", "retried=1", "failed=0", "skipped=0"):
        assert count in output, output
    status, state = _state(pg, w, instance)
    assert status == "completed", state
    assert state["meta"]["incidents"][0]["status"] == "resolved"
    assert len(state["meta"]["history"]) == 1
    assert state["meta"]["history"][0]["actor"] == w.pid("NV-0001")
    assert len(movements(w, request["ref"])) == 1
    assert w.get("stock_request", request["ref"].split(":", 1)[1])["state"] == "approved"
    retries = _retries(pg, w)
    assert [row[0] for row in retries] == [instance]
    assert _state(pg, foreign, foreign_instance)[0] == "running"
    assert _retries(pg, foreign) == []

    # Replaying the exact successful CLI confirmation is a no-op, including lifecycle audit.
    audit_updates = _lifecycle_audit_count(pg, w)
    recovered = _state(pg, w, instance)
    rc, replay = run_admin([*_argv(w, "enable"), "--confirm", confirmation], env)
    assert rc == 0, replay
    for count in ("eligible=0", "retried=0", "failed=0", "skipped=0"):
        assert count in replay, replay
    assert _lifecycle_audit_count(pg, w) == audit_updates
    assert _state(pg, w, instance) == recovered
    assert _retries(pg, w) == retries
    assert len(movements(w, request["ref"])) == 1

    with pg.su() as connection:
        lifecycle = connection.execute(
            "SELECT diff->'new'->>'status' FROM xt_audit.event"
            " WHERE tenant_id=%s AND model='xt_meta.pack_install' AND op='UPDATE' ORDER BY seq",
            (w.tenant.id,),
        ).fetchall()
        audited_retry = connection.execute(
            "SELECT count(*) FROM xt_audit.event WHERE tenant_id=%s"
            " AND model='xt_meta.process_event' AND op='INSERT'"
            " AND diff->'new'->>'type'='incident.retry'",
            (w.tenant.id,),
        ).fetchone()
    assert [row[0] for row in lifecycle][-2:] == ["disabled", "enabled"]
    assert audited_retry is not None and audited_retry[0] == 1

    # Another complete disable/enable cycle must leave resolved incidents and postings alone.
    _apply(w, env, "disable")
    next_plan, _ = _plan(w, env, "enable")
    assert instance not in next_plan
    assert "eligible=0" in next_plan
    output = _apply(w, env, "enable")
    for count in ("eligible=0", "retried=0", "failed=0", "skipped=0"):
        assert count in output, output
    assert _state(pg, w, instance) == recovered
    assert _retries(pg, w) == retries
    assert len(movements(w, request["ref"])) == 1


def test_enable_ignores_other_models_reasons_packs_and_closed_incidents(
    recovery_warehouses: tuple[Warehouse, Warehouse, dict[str, str]], pg: Any
) -> None:
    w, _, env = recovery_warehouses
    cases = {name: _request(w) for name in ("eligible", "model", "reason", "pack", "closed")}
    _apply(w, env, "disable")
    for _, instance, task in cases.values():
        _fail_while_disabled(w, instance, task)

    # Derive every exclusion from a real captured incident; change one selection dimension.
    changes = {
        "model": {"model": "employee"},
        "reason": {"reason": "permission_denied"},
        "pack": {"pack": "unrelated-pack"},
        "closed": {"status": "resolved"},
    }
    for name, change in changes.items():
        _change_incident(pg, w, cases[name][1], **change)
    untouched = {name: _state(pg, w, cases[name][1]) for name in changes}
    retries_before = _retries(pg, w)
    eligible_request, eligible_instance, _ = cases["eligible"]

    plan, _ = _plan(w, env, "enable")
    assert eligible_instance in plan
    assert "eligible=1" in plan
    for name in changes:
        assert cases[name][1] not in plan
        assert _state(pg, w, cases[name][1]) == untouched[name]

    output = _apply(w, env, "enable")
    for count in ("eligible=1", "retried=1", "failed=0", "skipped=0"):
        assert count in output, output
    assert _state(pg, w, eligible_instance)[0] == "completed"
    assert len(movements(w, eligible_request["ref"])) == 1
    retries_after = _retries(pg, w)
    assert [row[0] for row in retries_after[len(retries_before) :]] == [eligible_instance]
    for name in changes:
        assert _state(pg, w, cases[name][1]) == untouched[name]
        assert movements(w, cases[name][0]["ref"]) == []


def test_enable_keeps_other_recoveries_when_one_retry_fails(
    recovery_warehouses: tuple[Warehouse, Warehouse, dict[str, str]], pg: Any
) -> None:
    w, _, env = recovery_warehouses
    good, good_instance, good_task = _request(w)
    # A fresh item has no stock: this service will fail with insufficient_stock after enabling.
    bad, bad_instance, bad_task = _request(w, kind="issue")
    _apply(w, env, "disable")
    _fail_while_disabled(w, good_instance, good_task)
    _fail_while_disabled(w, bad_instance, bad_task)
    retries_before = len(_retries(pg, w))

    plan, _ = _plan(w, env, "enable")
    assert "eligible=2" in plan
    assert good_instance in plan and bad_instance in plan
    output = _apply(w, env, "enable")
    for count in ("eligible=2", "retried=1", "failed=1", "skipped=0"):
        assert count in output, output

    assert _state(pg, w, good_instance)[0] == "completed"
    assert len(movements(w, good["ref"])) == 1
    assert w.get("stock_request", good["ref"].split(":", 1)[1])["state"] == "approved"
    bad_status, bad_state = _state(pg, w, bad_instance)
    assert bad_status == "running" and bad_state["meta"]["history"] == []
    (incident,) = bad_state["meta"]["incidents"]
    assert incident["status"] == "open" and incident["attempts"] == 1
    assert movements(w, bad["ref"]) == []
    retried_instances = {row[0] for row in _retries(pg, w)[retries_before:]}
    assert retried_instances == {good_instance, bad_instance}
    with pg.su() as connection:
        status = connection.execute(
            "SELECT status FROM xt_meta.pack_install WHERE tenant_id=%s AND pack='kho-hang'",
            (w.tenant.id,),
        ).fetchone()
    assert status is not None and status[0] == "enabled"

    # A new approved plan on the enabled app retries only its remaining failed incident.
    good_state = _state(pg, w, good_instance)
    audit_updates = _lifecycle_audit_count(pg, w)
    retry_plan, _ = _plan(w, env, "enable")
    assert "eligible=1" in retry_plan
    assert bad_instance in retry_plan and good_instance not in retry_plan
    output = _apply(w, env, "enable")
    for count in ("eligible=1", "retried=0", "failed=1", "skipped=0"):
        assert count in output, output
    bad_status, bad_state = _state(pg, w, bad_instance)
    assert bad_status == "running" and bad_state["meta"]["history"] == []
    (retried_incident,) = bad_state["meta"]["incidents"]
    assert retried_incident["id"] == incident["id"]
    assert retried_incident["status"] == "open" and retried_incident["attempts"] == 2
    assert _state(pg, w, good_instance) == good_state
    assert len(movements(w, good["ref"])) == 1
    assert movements(w, bad["ref"]) == []
    assert _lifecycle_audit_count(pg, w) == audit_updates
    assert len(_retries(pg, w)) == retries_before + 3
