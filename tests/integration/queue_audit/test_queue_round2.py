# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Round-2 audit: per-job stalled recovery, schedule backoff/fairness, DLQ requeue/purge."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import psycopg
import pytest
from procrastinate.jobs import Status

from tests.integration.queue_audit.conftest import AuditDatabase
from tests.integration.test_worker_queue import TENANT_A, TENANT_B, _request
from tests.integration.test_worker_queue_review import _claim_snapshot
from xt.adapters.queue.dlq import list_dead, purge_dead, requeue_dead
from xt.adapters.queue.runtime import SCHEDULES_PER_TENANT_PER_TICK

pytestmark = pytest.mark.integration


def _enqueue(db: AuditDatabase, runtime: Any, job: Any) -> int:
    async def scenario() -> int:
        async with db.tenant_transaction(job.tenant_id) as connection:
            return int(await runtime.queue.enqueue(connection, job))

    return asyncio.run(scenario())


def _dead_job(db: AuditDatabase) -> tuple[int, Any]:
    runtime = db.runtime(max_attempts=1)
    job = _request(db, TENANT_B, "xt_t2", "business.fail")
    job_id = _enqueue(db, runtime, job)
    context = _claim_snapshot(db, job_id, runtime)

    async def scenario() -> None:
        async with runtime.app.open_async(), runtime.tenant_pool:
            with pytest.raises(RuntimeError):
                await runtime.dispatch(
                    context,
                    tenant_id=str(TENANT_B),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )
            await runtime.job_manager.finish_job(
                context.job, status=Status.FAILED, delete_job=False
            )

    asyncio.run(scenario())
    return job_id, job


def test_stalled_recovery_skips_a_locked_request_and_recovers_the_rest(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    runtime = db.runtime(max_attempts=1)
    locked_job = _request(db, TENANT_A, "xt_t1")
    free_job = _request(db, TENANT_A, "xt_t1")
    locked_id = _enqueue(db, runtime, locked_job)
    free_id = _enqueue(db, runtime, free_job)
    _claim_snapshot(db, locked_id, runtime)
    _claim_snapshot(db, free_id, runtime)
    # A zombie handler still holds the durable request row of one job.
    with psycopg.connect(db.dsn("postgres")) as zombie:
        zombie.execute("SELECT 1 FROM xt_queue.requests WHERE job_id=%s FOR UPDATE", (locked_id,))
        asyncio.run(runtime.recover_stalled())
        status = dict(
            db.execute(
                "SELECT id, status::text FROM procrastinate_jobs WHERE id = ANY(%s)",
                ([locked_id, free_id],),
            )
        )
        assert status == {locked_id: "doing", free_id: "failed"}
        zombie.rollback()
    asyncio.run(runtime.recover_stalled())
    assert db.execute("SELECT status::text FROM procrastinate_jobs WHERE id=%s", (locked_id,)) == [
        ("failed",)
    ]
    assert db.execute(
        "SELECT count(*) FROM xt_queue.requests WHERE job_id = ANY(%s) AND dead_at IS NOT NULL",
        ([locked_id, free_id],),
    ) == [(2,)]


def test_failing_schedule_backs_off_and_does_not_starve_other_tenants(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    full = db.runtime()
    created: list[int] = []

    async def register(job: Any) -> int:
        async with db.tenant_transaction(job.tenant_id) as connection:
            return int(await full.queue.periodic(connection, job, cron="0 1 * * *", timezone="UTC"))

    try:
        # Tenant A floods the tick; tenant B has one schedule.
        flood = [
            asyncio.run(register(_request(db, TENANT_A, "xt_t1")))
            for _ in range(SCHEDULES_PER_TENANT_PER_TICK + 3)
        ]
        lone = asyncio.run(register(_request(db, TENANT_B, "xt_t2")))
        created += [*flood, lone]
        db.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '1 minute' WHERE id = ANY(%s)",
            (created,),
        )
        # A worker that can never materialize them (payload over its cap).
        failing = db.runtime(max_payload_bytes=8)
        asyncio.run(failing.dispatch_periodic())
        rows = db.execute(
            "SELECT id, failures, retry_at > now(), last_error FROM xt_queue.schedules "
            "WHERE id = ANY(%s) ORDER BY id",
            (created,),
        )
        attempted = [row for row in rows if row[1] == 1]
        assert any(row[0] == lone for row in attempted)  # per-tenant cap: B is not starved
        assert len([r for r in attempted if r[0] in flood]) == SCHEDULES_PER_TENANT_PER_TICK
        assert all(row[2] and row[3] == "ValueError" for row in attempted)
        # In backoff: the next tick skips them and picks the remaining A schedules.
        before = failing.stats["schedule_errors"]
        asyncio.run(failing.dispatch_periodic())
        assert failing.stats["schedule_errors"] - before == 3
        # A healthy worker fires them once their backoff has elapsed.
        db.execute("UPDATE xt_queue.schedules SET retry_at=now() WHERE id = ANY(%s)", (created,))
        fired = 0
        for _ in range(2):
            fired += asyncio.run(full.dispatch_periodic())
        assert fired >= len(created)
        assert db.execute(
            "SELECT count(*) FROM xt_queue.schedules WHERE id = ANY(%s) "
            "AND failures=0 AND retry_at IS NULL AND last_error IS NULL AND next_at > now()",
            (created,),
        ) == [(len(created),)]
    finally:
        db.execute("UPDATE xt_queue.schedules SET active=false WHERE id = ANY(%s)", (created,))


def test_dlq_requeue_defers_a_new_fenced_job_and_purge_abandons(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    dsn = db.dsn("xt_control")
    old_id, job = _dead_job(db)
    result = asyncio.run(requeue_dead(dsn, [old_id]))
    new_id = result["requeued"][str(old_id)]
    assert new_id is not None and new_id != old_id
    assert db.execute(
        "SELECT job_id, dead_at, last_error FROM xt_queue.requests WHERE idempotency_key=%s",
        (job.idempotency_key,),
    ) == [(new_id, None, None)]
    assert db.execute(
        "SELECT status::text, attempts FROM procrastinate_jobs WHERE id=%s", (new_id,)
    ) == [("todo", 0)]
    listing = asyncio.run(list_dead(dsn))
    assert old_id not in [row["job_id"] for row in listing["dead_jobs"]]
    # A second requeue of the superseded id is refused (fencing by job id).
    assert asyncio.run(requeue_dead(dsn, [old_id]))["requeued"][str(old_id)] is None

    purge_id, purge_job = _dead_job(db)
    env = {
        **os.environ,
        "XT_QUEUE_DATABASE_URL": dsn,
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
    }
    cli = subprocess.run(
        [sys.executable, "-m", "xt.entrypoints.worker", "dlq", "purge", str(purge_id)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr
    assert f'"{purge_id}": true' in cli.stdout
    assert db.execute("SELECT count(*) FROM procrastinate_jobs WHERE id=%s", (purge_id,)) == [(0,)]
    assert db.execute(
        "SELECT cancelled FROM xt_queue.requests WHERE idempotency_key=%s",
        (purge_job.idempotency_key,),
    ) == [(True,)]
    # Only dead letters can be purged.
    assert asyncio.run(purge_dead(dsn, [new_id]))["purged"][str(new_id)] is False
