# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Regressions from docs/audit-queue.md; each test failed before its fix."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from procrastinate.jobs import Status
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from tests.integration.queue_audit.conftest import AuditDatabase
from tests.integration.test_worker_queue import TENANT_A, TENANT_B, _request
from tests.integration.test_worker_queue_review import _claim_snapshot
from xt.adapters.queue.dlq import inspect_dead, list_dead
from xt.adapters.queue.guard import guard_suspended
from xt.adapters.queue.runtime import SessionControlError, TransactionControlError
from xt.application.jobs import JobRequest

pytestmark = pytest.mark.integration


async def _periodic(db: AuditDatabase, runtime: Any, job: Any, cron: str, tz: str) -> int:
    async with db.tenant_transaction(job.tenant_id) as connection:
        return int(await runtime.queue.periodic(connection, job, cron=cron, timezone=tz))


@pytest.mark.parametrize("pinned_session_utc", [True, False])
def test_aq01_periodic_next_fire_is_independent_of_session_timezone(
    audit_db: AuditDatabase, pinned_session_utc: bool
) -> None:
    db = audit_db
    runtime = db.runtime()
    if not pinned_session_utc:
        # The croniter fix alone must hold even if the session TimeZone is not UTC.
        runtime.tenant_options = runtime.tenant_options.replace("-c TimeZone=UTC", "")
    job = _request(db, TENANT_A, "xt_t1")
    schedule_id = asyncio.run(_periodic(db, runtime, job, "0 8 * * 1", "Asia/Ho_Chi_Minh"))
    # Pretend the Monday 08:00 VN occurrence of 12/10/2026 is due now.
    db.execute(
        "UPDATE xt_queue.schedules SET next_at='2026-10-12 01:00Z' WHERE id=%s", (schedule_id,)
    )
    db.execute("ALTER ROLE xt_app SET timezone='Asia/Ho_Chi_Minh'")
    try:
        asyncio.run(runtime.dispatch_periodic())
    finally:
        db.execute("ALTER ROLE xt_app RESET timezone")
        next_at = db.execute("SELECT next_at FROM xt_queue.schedules WHERE id=%s", (schedule_id,))[
            0
        ][0]
        db.execute("UPDATE xt_queue.schedules SET active=false WHERE id=%s", (schedule_id,))
    # Expected: next Monday 08:00 Asia/Ho_Chi_Minh == 2026-10-19T01:00Z.
    assert next_at == datetime(2026, 10, 19, 1, 0, tzinfo=UTC)


def test_aq02_poison_schedule_does_not_stop_other_tenants_maintenance(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    full = db.runtime()
    poison_job = _request(db, TENANT_A, "xt_t1")
    healthy_job = _request(db, TENANT_B, "xt_t2", "business.fail")
    poison = asyncio.run(_periodic(db, full, poison_job, "0 1 * * *", "UTC"))
    healthy = asyncio.run(_periodic(db, full, healthy_job, "0 1 * * *", "UTC"))
    db.execute(
        "UPDATE xt_queue.schedules SET next_at=now()-interval '2 minutes' WHERE id=%s", (poison,)
    )
    db.execute(
        "UPDATE xt_queue.schedules SET next_at=now()-interval '1 minute' WHERE id=%s", (healthy,)
    )
    # A worker deployed without business.finish (version skew / removed handler).
    partial = db.runtime(handlers={"business.fail": full.handlers["business.fail"]})
    try:
        asyncio.run(partial.maintenance())
        materialized = db.execute(
            "SELECT count(*) FROM xt_queue.requests WHERE schedule_id=%s", (healthy,)
        )
    finally:
        db.execute(
            "UPDATE xt_queue.schedules SET active=false WHERE id = ANY(%s)", ([poison, healthy],)
        )
    assert materialized == [(1,)]


def test_aq03_pooled_session_state_does_not_leak_between_tenants(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    seen: dict[str, Any] = {}

    async def handler(connection: Any, job: Any) -> None:
        cursor = await connection.execute("SELECT pg_backend_pid()")
        pid = (await cursor.fetchone())[0]
        if job.tenant_id == TENANT_A:
            seen["pid_a"] = pid
            # Non-LOCAL settings / temp tables made by a handler of tenant A.
            await connection.execute("SET SESSION search_path TO xt_t1, public")
            await connection.execute("CREATE TEMP TABLE audit_leak(v text) ON COMMIT PRESERVE ROWS")
            await connection.execute("INSERT INTO audit_leak VALUES ('secret-of-A')")
        elif pid == seen.get("pid_a"):
            cursor = await connection.execute(
                "SELECT current_setting('search_path'), "
                "to_regclass('pg_temp.audit_leak') IS NOT NULL"
            )
            seen["b_state"] = await cursor.fetchone()

    runtime = db.runtime(handlers={"business.finish": handler})
    jobs = [_request(db, TENANT_A, "xt_t1")] + [_request(db, TENANT_B, "xt_t2") for _ in range(4)]

    async def scenario() -> None:
        for job in jobs:
            async with db.tenant_transaction(job.tenant_id) as connection:
                await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            for job in jobs:
                await runtime.dispatch(
                    None,
                    tenant_id=str(job.tenant_id),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )
                if "b_state" in seen:
                    break

    asyncio.run(scenario())
    assert "b_state" in seen, "tenant B never reused tenant A's pooled connection"
    search_path, sees_temp_table = seen["b_state"]
    assert "xt_t1" not in search_path
    assert not sees_temp_table


def test_aq04_tenant_sessions_bound_zombie_lock_holding(audit_db: AuditDatabase) -> None:
    db = audit_db
    seen: list[tuple[str, str]] = []

    async def handler(connection: Any, job: Any) -> None:
        cursor = await connection.execute(
            "SELECT current_setting('idle_in_transaction_session_timeout'), "
            "current_setting('lock_timeout')"
        )
        seen.append(await cursor.fetchone())

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    idle_timeout, lock_timeout = seen[0]
    assert idle_timeout != "0" or lock_timeout != "0"


def test_aq03_tenant_a_then_b_on_one_connection_resets_all_session_state(
    audit_db: AuditDatabase,
) -> None:
    """Pool of size 1: tenant B's job necessarily reuses tenant A's backend."""
    db = audit_db
    seen: dict[str, Any] = {}

    async def handler(connection: Any, job: Any) -> None:
        pid = (await (await connection.execute("SELECT pg_backend_pid()")).fetchone())[0]
        if job.tenant_id == TENANT_A:
            seen["pid_a"] = pid
            # Bypass the handler statement guard (as connection.pgconn could) to
            # prove the pool reset is an independent backstop.
            with guard_suspended(connection):
                await connection.execute("SELECT set_config('xt.leak','secret-of-A',false)")
                await connection.execute(
                    "SELECT set_config('xt.tenant_id',%s,false)", (str(TENANT_A),)
                )
                await connection.execute("SET SESSION ROLE xt_workflow")
            await connection.execute("SET SESSION search_path TO xt_t1")
            await connection.execute("SELECT pg_advisory_lock(424242)")
            await connection.execute("PREPARE leak_stmt AS SELECT 1")
            await connection.execute("CREATE TEMP TABLE leak_a(v text) ON COMMIT PRESERVE ROWS")
            await connection.execute("LISTEN leak_channel")
            return
        seen["pid_b"] = pid
        cursor = await connection.execute(
            "SELECT current_setting('xt.leak', true), current_setting('xt.tenant_id'), "
            "current_setting('search_path'), current_user, "
            "(SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=424242 "
            " AND pid=pg_backend_pid()), "
            "(SELECT count(*) FROM pg_prepared_statements WHERE name='leak_stmt'), "
            "to_regclass('pg_temp.leak_a') IS NOT NULL, "
            "(SELECT count(*) FROM pg_listening_channels()), "
            "current_setting('TimeZone'), current_setting('lock_timeout'), "
            "current_setting('idle_in_transaction_session_timeout'), "
            "current_setting('statement_timeout')"
        )
        seen["b"] = await cursor.fetchone()

    runtime = db.runtime(handlers={"business.finish": handler})
    runtime.tenant_pool._max_size = runtime.tenant_pool._min_size = 1
    jobs = [_request(db, TENANT_A, "xt_t1"), _request(db, TENANT_B, "xt_t2")]

    async def scenario() -> None:
        for job in jobs:
            async with db.tenant_transaction(job.tenant_id) as connection:
                await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            for job in jobs:
                await runtime.dispatch(
                    None,
                    tenant_id=str(job.tenant_id),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )

    asyncio.run(scenario())
    assert seen["pid_a"] == seen["pid_b"]
    assert seen["b"] == (
        "",
        str(TENANT_B),
        "xt_t2, pg_temp",
        "xt_job_t2",
        0,
        0,
        False,
        0,
        "UTC",
        "10s",
        "1min",
        "30s",
    )


def test_aq04_timeouts_are_explicitly_configurable(audit_db: AuditDatabase) -> None:
    db = audit_db
    seen: list[tuple[str, ...]] = []

    async def handler(connection: Any, job: Any) -> None:
        cursor = await connection.execute(
            "SELECT current_setting('statement_timeout'), current_setting('lock_timeout'), "
            "current_setting('idle_in_transaction_session_timeout')"
        )
        seen.append(await cursor.fetchone())

    runtime = db.runtime(
        handlers={"business.finish": handler},
        statement_timeout_seconds=17,
        lock_timeout_seconds=11,
        idle_in_transaction_timeout_seconds=13,
    )
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    assert seen == [("17s", "11s", "13s")]
    with pytest.raises(ValueError, match="timeouts must be positive"):
        db.runtime(lock_timeout_seconds=0)


def test_aq04_lock_timeout_bounds_wait_behind_a_zombie_lock_holder(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    job = _request(db, TENANT_A, "xt_t1")
    runtime = db.runtime(lock_timeout_seconds=1)

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        # A paused zombie: holds the request row lock and never finishes.
        async with db.tenant_transaction(TENANT_A) as zombie, runtime.tenant_pool:
            await zombie.execute(
                "SELECT 1 FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s "
                "FOR UPDATE",
                (TENANT_A, job.idempotency_key),
            )
            with pytest.raises(psycopg.errors.LockNotAvailable):
                await runtime.dispatch(
                    None,
                    tenant_id=str(TENANT_A),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )

    asyncio.run(asyncio.wait_for(scenario(), 20))


def test_aq05_job_of_tenant_a_cannot_write_tenant_b_schema(audit_db: AuditDatabase) -> None:
    db = audit_db
    seen: dict[str, Any] = {}

    async def probe(connection: Any, job: Any) -> None:
        cursor = await connection.execute("SELECT current_user, current_setting('search_path')")
        seen["context"] = await cursor.fetchone()
        await connection.execute(
            "INSERT INTO xt_t2.effects(job_key,tenant_id,observed_tenant,observed_role) "
            "VALUES (%s,%s,%s,current_user)",
            (job.idempotency_key, job.tenant_id, job.tenant_id),
        )

    runtime = db.runtime(handlers={"business.finish": probe})
    # Valid tenant A request whose payload points at tenant B's schema.
    job = JobRequest(TENANT_A, "business.finish", uuid4().hex, {"schema": "xt_t2"})

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await runtime.dispatch(
                    None,
                    tenant_id=str(TENANT_A),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )

    asyncio.run(scenario())
    assert seen["context"] == ("xt_job_t1", "xt_t1, pg_temp")
    assert db.execute(
        "SELECT count(*) FROM xt_t2.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(0,)]
    assert db.execute(
        "SELECT completed FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s",
        (TENANT_A, job.idempotency_key),
    ) == [(False,)]


def test_aq05_unbound_tenant_and_misconfigured_role_fail_closed(audit_db: AuditDatabase) -> None:
    db = audit_db
    stranger = UUID("30000000-0000-0000-0000-000000000003")
    calls: list[str] = []

    async def handler(connection: Any, job: Any) -> None:
        calls.append(job.idempotency_key)

    runtime = db.runtime(handlers={"business.finish": handler})
    unbound = JobRequest(stranger, "business.finish", uuid4().hex, {})
    bound = _request(db, TENANT_B, "xt_t2")

    async def dispatch(job: JobRequest) -> None:
        await runtime.dispatch(
            None,
            tenant_id=str(job.tenant_id),
            task_name=job.task_name,
            idempotency_key=job.idempotency_key,
            payload=job.payload,
        )

    async def scenario() -> None:
        for job in (unbound, bound):
            async with db.tenant_transaction(job.tenant_id) as connection:
                await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            with pytest.raises(psycopg.errors.InsufficientPrivilege, match="no queue job binding"):
                await dispatch(unbound)
            db.execute("GRANT USAGE ON SCHEMA xt_t1 TO xt_job_t2")
            try:
                with pytest.raises(
                    psycopg.errors.InsufficientPrivilege, match="another tenant schema"
                ):
                    await dispatch(bound)
            finally:
                db.execute("REVOKE USAGE ON SCHEMA xt_t1 FROM xt_job_t2")

    asyncio.run(scenario())
    assert calls == []


def test_aq05_handler_can_enqueue_follow_up_work_as_its_job_role(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    follow_up = uuid4().hex

    async def handler(connection: Any, job: Any) -> None:
        await runtime.queue.enqueue(
            connection, JobRequest(job.tenant_id, "business.finish", follow_up, {"n": 1})
        )
        cursor = await connection.execute("SELECT current_user")
        assert (await cursor.fetchone())[0] == "xt_job_t1"

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    assert db.execute(
        "SELECT count(*) FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s "
        "AND job_id IS NOT NULL",
        (TENANT_A, follow_up),
    ) == [(1,)]


def test_aq07_payload_size_cap_is_configurable(audit_db: AuditDatabase) -> None:
    db = audit_db
    runtime = db.runtime(max_payload_bytes=128)
    small = _request(db, TENANT_A, "xt_t1")
    big = JobRequest(TENANT_A, "business.finish", uuid4().hex, {"blob": "x" * 200})

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            assert await runtime.queue.enqueue(connection, small) > 0
            with pytest.raises(ValueError, match="limit is 128 bytes"):
                await runtime.queue.enqueue(connection, big)
            with pytest.raises(ValueError, match="limit is 128 bytes"):
                await runtime.queue.periodic(connection, big, cron="0 1 * * *", timezone="UTC")

    asyncio.run(scenario())
    assert db.runtime().queue.max_payload_bytes == 64 * 1024


def test_aq08_dead_letter_is_marked_and_listed(audit_db: AuditDatabase) -> None:
    db = audit_db
    runtime = db.runtime(max_attempts=1)
    job = _request(db, TENANT_B, "xt_t2", "business.fail", note="PAYLOAD-MARKER-xyz")

    async def scenario() -> int:
        async with db.tenant_transaction(TENANT_B) as connection:
            job_id = await runtime.queue.enqueue(connection, job)
        context = _claim_snapshot(db, job_id, runtime)
        async with runtime.app.open_async(), runtime.tenant_pool:
            with pytest.raises(RuntimeError, match="roll back"):
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
        return int(job_id)

    job_id = asyncio.run(scenario())
    assert db.execute(
        "SELECT dead_at IS NOT NULL, last_error FROM xt_queue.requests WHERE job_id=%s", (job_id,)
    ) == [(True, "RuntimeError")]
    listing = asyncio.run(list_dead(db.dsn("xt_control")))
    dead = [row for row in listing["dead_jobs"] if row["job_id"] == job_id]
    assert dead and dead[0]["last_error"] == "RuntimeError"
    assert dead[0]["tenant_id"] == str(TENANT_B)
    detail = asyncio.run(inspect_dead(db.dsn("xt_control"), job_id))
    assert detail is not None and detail["status"] == "failed"
    assert detail["payload_keys"] == ["note", "request_id", "schema"]
    assert "payload" not in detail
    env = {
        **os.environ,
        "XT_QUEUE_DATABASE_URL": db.dsn("xt_control"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
    }
    result = subprocess.run(
        [sys.executable, "-m", "xt.adapters.queue.dlq", "inspect", str(job_id)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"last_error": "RuntimeError"' in result.stdout
    assert "PAYLOAD-MARKER" not in result.stdout


def test_aq02_failing_schedule_is_surfaced_in_dlq(audit_db: AuditDatabase) -> None:
    db = audit_db
    full = db.runtime()
    poison_job = _request(db, TENANT_A, "xt_t1")
    poison = asyncio.run(_periodic(db, full, poison_job, "0 1 * * *", "UTC"))
    db.execute(
        "UPDATE xt_queue.schedules SET next_at=now()-interval '1 minute' WHERE id=%s", (poison,)
    )
    # Grace = SCHEDULE_ERROR_GRACE_TICKS x recover_seconds = 3 x 2 s.
    partial = db.runtime(
        handlers={"business.fail": full.handlers["business.fail"]}, recover_seconds=2
    )
    try:
        # L7: during a rolling deploy a barely overdue schedule is not an error.
        db.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '1 second' WHERE id=%s",
            (poison,),
        )
        asyncio.run(partial.maintenance())
        assert db.execute("SELECT last_error FROM xt_queue.schedules WHERE id=%s", (poison,)) == [
            (None,)
        ]
        assert partial.stats["schedules_skipped"] == 0
        db.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '1 minute' WHERE id=%s",
            (poison,),
        )
        asyncio.run(partial.maintenance())
        asyncio.run(partial.maintenance())
        listing = asyncio.run(list_dead(db.dsn("xt_control")))
    finally:
        db.execute("UPDATE xt_queue.schedules SET active=false WHERE id=%s", (poison,))
    failing = [row for row in listing["failing_schedules"] if row["schedule_id"] == poison]
    assert failing and failing[0]["last_error"] == "no registered job handler: business.finish"
    assert partial.stats["schedules_skipped"] == 2


def test_aq09_xt_app_cannot_change_priority_status_or_defer_foreign_tasks(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    job = _request(db, TENANT_A, "xt_t1")
    runtime = db.runtime()

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            job_id = await runtime.queue.enqueue(connection, job)
        for statement, params, error in (
            (
                "UPDATE procrastinate_jobs SET priority=2147483647 WHERE id=%s",
                (job_id,),
                psycopg.errors.InsufficientPrivilege,
            ),
            (
                "UPDATE procrastinate_jobs SET status='succeeded' WHERE id=%s",
                (job_id,),
                psycopg.errors.InsufficientPrivilege,  # WITH CHECK violation
            ),
            (
                "INSERT INTO procrastinate_jobs(queue_name,task_name,args) "
                "VALUES ('xt','procrastinate.builtin_tasks.remove_old_jobs',%s)",
                (Jsonb({"tenant_id": str(TENANT_A)}),),
                psycopg.errors.InsufficientPrivilege,
            ),
            (
                "INSERT INTO procrastinate_jobs(queue_name,task_name,priority,args) "
                "VALUES ('xt','xt.dispatch',100,%s)",
                (Jsonb({"tenant_id": str(TENANT_A)}),),
                psycopg.errors.InsufficientPrivilege,
            ),
            (
                "UPDATE procrastinate_events SET type=type WHERE job_id=%s",
                (job_id,),
                psycopg.errors.InsufficientPrivilege,
            ),
        ):
            with pytest.raises(error):
                async with db.tenant_transaction(TENANT_A) as connection:
                    await connection.execute(statement, params)
        # The one legitimate mutation, cancel, still works.
        async with db.tenant_transaction(TENANT_A) as connection:
            assert await runtime.queue.cancel(
                connection, tenant_id=TENANT_A, idempotency_key=job.idempotency_key
            )

    asyncio.run(scenario())
    assert db.execute(
        "SELECT status::text, priority FROM procrastinate_jobs WHERE args->>'idempotency_key'=%s",
        (job.idempotency_key,),
    ) == [("cancelled", 0)]


@pytest.mark.parametrize(
    "statement",
    [
        "COMMIT",
        "SELECT 1; commit",
        "/* x */ END",
        "ROLLBACK",
        "ROLLBACK AND CHAIN",
        "ABORT",
        "PREPARE TRANSACTION 'x'",
    ],
)
def test_aq10_handler_cannot_end_the_worker_transaction(
    audit_db: AuditDatabase, statement: str
) -> None:
    db = audit_db

    async def handler(connection: Any, job: Any) -> None:
        await connection.execute(
            "INSERT INTO xt_t1.effects(job_key,tenant_id,observed_tenant,observed_role) "
            "VALUES (%s,%s,%s,current_user)",
            (job.idempotency_key, job.tenant_id, job.tenant_id),
        )
        await connection.execute(statement)

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            for _ in range(2):
                with pytest.raises(TransactionControlError):
                    await runtime.dispatch(
                        None,
                        tenant_id=str(TENANT_A),
                        task_name=job.task_name,
                        idempotency_key=job.idempotency_key,
                        payload=job.payload,
                    )

    asyncio.run(scenario())
    assert db.execute(
        "SELECT count(*) FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(0,)]


def test_aq10_savepoints_remain_available_to_handlers(audit_db: AuditDatabase) -> None:
    db = audit_db

    async def handler(connection: AsyncConnection[Any], job: Any) -> None:
        try:
            async with connection.transaction():
                await connection.execute("SELECT 1/0")
        except psycopg.errors.DivisionByZero:
            pass
        await connection.execute("SAVEPOINT manual")
        await connection.execute("ROLLBACK TO SAVEPOINT manual")
        await connection.execute("SELECT 'COMMIT; END'")

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    assert db.execute(
        "SELECT completed FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s",
        (TENANT_A, job.idempotency_key),
    ) == [(True,)]


@pytest.mark.parametrize(
    "statement",
    [
        "SET LOCAL ROLE xt_app",
        "RESET ROLE",
        "SET ROLE xt_job_t2",
        "SELECT set_config('xt.tenant_id', '20000000-0000-0000-0000-000000000002', true)",
        "SELECT set_config('search_path', 'xt_t2, public', true)",
        "SELECT xt.set_context('20000000-0000-0000-0000-000000000002', NULL, '{}', '{}', '{}',"
        " NULL, NULL, NULL)",
    ],
)
def test_m2_handler_cannot_change_pinned_role_or_tenant(
    audit_db: AuditDatabase, statement: str
) -> None:
    db = audit_db

    async def handler(connection: Any, job: Any) -> None:
        await connection.execute(statement)

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            with pytest.raises(SessionControlError):
                await runtime.dispatch(
                    None,
                    tenant_id=str(TENANT_A),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )

    asyncio.run(scenario())


def test_m2_workflow_elevation_remains_allowed(audit_db: AuditDatabase) -> None:
    db = audit_db
    seen: list[str] = []

    async def handler(connection: Any, job: Any) -> None:
        await connection.execute("SET LOCAL ROLE xt_workflow")
        seen.append((await (await connection.execute("SELECT current_user")).fetchone())[0])

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    assert seen == ["xt_workflow"]
    assert db.execute(
        "SELECT completed FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s",
        (TENANT_A, job.idempotency_key),
    ) == [(True,)]


def test_l8_install_schema_requires_rls_bypassing_owner(audit_db: AuditDatabase) -> None:
    with pytest.raises(ValueError, match="SUPERUSER or BYPASSRLS"):
        asyncio.run(audit_db.runtime().install_schema(audit_db.dsn("xt_control")))


def test_worker_sets_model_engine_context_via_set_context(audit_db: AuditDatabase) -> None:
    """AUD-13: xt.cur() (model-engine RLS) and the xt_queue GUC both carry the job tenant."""
    db = audit_db
    seen: list[tuple[Any, ...]] = []

    async def handler(connection: AsyncConnection[Any], job: Any) -> None:
        cursor = await connection.execute(
            "SELECT xt.tenant(), (xt.cur()).actor_id, current_setting('xt.tenant_id'), current_user"
        )
        seen.append(tuple(await cursor.fetchone() or ()))

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    asyncio.run(scenario())
    assert seen == [(TENANT_A, None, str(TENANT_A), "xt_job_t1")]
    assert db.execute(
        "SELECT completed FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s",
        (TENANT_A, job.idempotency_key),
    ) == [(True,)]


def test_worker_startup_requires_set_context(audit_db: AuditDatabase) -> None:
    db = audit_db
    runtime = db.runtime()
    signature = "xt.set_context(uuid,uuid,text[],uuid[],uuid[],uuid,text,text)"
    db.execute(f"REVOKE EXECUTE ON FUNCTION {signature} FROM xt_app")
    try:
        with pytest.raises(ValueError, match=r"requires EXECUTE on xt\.set_context"):
            asyncio.run(runtime._check_role(db.dsn("xt_app"), runtime.tenant_options, "xt_app"))
    finally:
        db.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO xt_app")
    asyncio.run(runtime._check_role(db.dsn("xt_app"), runtime.tenant_options, "xt_app"))


def test_l3_bookkeeping_runs_with_repinned_role_tenant_and_search_path(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    db.execute("CREATE TABLE public.l3_probe(role text, tenant text, search_path text, ctx uuid)")
    db.execute("GRANT INSERT ON public.l3_probe TO xt_app")
    db.execute(
        "CREATE FUNCTION public.l3_probe_trigger() RETURNS trigger LANGUAGE plpgsql AS $$"
        "BEGIN INSERT INTO public.l3_probe VALUES (current_user, "
        "current_setting('xt.tenant_id', true), current_setting('search_path'), "
        "(xt.cur()).tenant_id); RETURN NEW; END $$"
    )
    db.execute(
        "CREATE TRIGGER l3_probe AFTER UPDATE OF completed ON xt_queue.requests "
        "FOR EACH ROW EXECUTE FUNCTION public.l3_probe_trigger()"
    )

    async def handler(connection: AsyncConnection[Any], job: Any) -> None:
        # Allowed for handlers; must not leak into the dispatcher's bookkeeping.
        await connection.execute("SET LOCAL search_path TO xt_t1, public, pg_temp")
        await connection.execute("SET LOCAL ROLE xt_workflow")

    runtime = db.runtime(handlers={"business.finish": handler})
    job = _request(db, TENANT_A, "xt_t1")

    async def scenario() -> None:
        async with db.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, job)
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(TENANT_A),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    try:
        asyncio.run(scenario())
        probe = db.execute("SELECT role, tenant, search_path, ctx FROM public.l3_probe")
    finally:
        db.execute("DROP TRIGGER l3_probe ON xt_queue.requests")
        db.execute("DROP FUNCTION public.l3_probe_trigger()")
        db.execute("DROP TABLE public.l3_probe")
    assert probe == [("xt_app", str(TENANT_A), "pg_catalog, public", TENANT_A)]
