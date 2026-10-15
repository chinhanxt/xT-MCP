# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Adversarial queue regressions using the real acceptance-test database."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

import psycopg
import pytest
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job, Status
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from tests.integration.test_worker_queue import (
    TENANT_A,
    TENANT_B,
    WorkerDatabase,
    _enqueue,
    _request,
)
from tests.integration.test_worker_queue import (
    worker_database as worker_database,
)
from xt.application.jobs import JobRequest

pytestmark = pytest.mark.integration


def _claim_snapshot(database: WorkerDatabase, job_id: int, runtime: Any) -> JobContext:
    """Claim exactly this job with a deliberately expired worker heartbeat."""
    worker_id = database.execute(
        "INSERT INTO public.procrastinate_workers(last_heartbeat) "
        "VALUES (now()-interval '1 hour') RETURNING id"
    )[0][0]
    database.execute(
        "UPDATE public.procrastinate_jobs SET status='doing',worker_id=%s WHERE id=%s",
        (worker_id, job_id),
    )
    with psycopg.connect(database.dsn("postgres"), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
        ).fetchone()
    assert row is not None
    return JobContext(
        app=runtime.app,
        job=Job.from_row(row),
        start_timestamp=time.time(),
        abort_reason=lambda: None,
    )


def test_dispatch_rejects_payload_tampering_against_durable_request(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    original = _request(database, TENANT_A, "xt_t1")
    other = _request(database, TENANT_A, "xt_t1")
    asyncio.run(_enqueue(database, original))

    async def scenario() -> None:
        runtime = database.runtime()
        async with runtime.tenant_pool:
            with pytest.raises(ValueError, match=r"envelope|fingerprint|content"):
                await runtime.dispatch(
                    tenant_id=str(original.tenant_id),
                    task_name=original.task_name,
                    idempotency_key=original.idempotency_key,
                    payload=other.payload,
                )

    asyncio.run(scenario())
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id IN (%s,%s)",
        (original.payload["request_id"], other.payload["request_id"]),
    ) == [("pending",), ("pending",)]
    assert (
        database.execute(
            "SELECT 1 FROM xt_t1.effects WHERE job_key=%s", (original.idempotency_key,)
        )
        == []
    )


def test_periodic_cancel_also_cancels_already_materialized_pending_occurrence(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    original = _request(database, TENANT_A, "xt_t1")

    async def scenario() -> None:
        runtime = database.runtime()
        async with database.tenant_transaction(TENANT_A) as connection:
            schedule_id = await runtime.queue.periodic(
                connection, original, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
            )
        database.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '1 second' WHERE id=%s",
            (schedule_id,),
        )
        assert await runtime.dispatch_periodic() == 1
        occurrence = database.execute(
            "SELECT idempotency_key,job_id FROM xt_queue.requests "
            "WHERE tenant_id=%s AND idempotency_key LIKE %s",
            (TENANT_A, f"periodic:{schedule_id}:%"),
        )
        assert len(occurrence) == 1
        occurrence_key, job_id = occurrence[0]
        async with database.tenant_transaction(TENANT_A) as connection:
            assert await runtime.queue.cancel(
                connection, tenant_id=TENANT_A, idempotency_key=original.idempotency_key
            )
        assert database.execute(
            "SELECT cancelled FROM xt_queue.requests WHERE job_id=%s", (job_id,)
        ) == [(True,)]
        assert database.execute(
            "SELECT status FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
        ) == [("cancelled",)]
        async with runtime.tenant_pool:
            await runtime.dispatch(
                tenant_id=str(TENANT_A),
                task_name=original.task_name,
                idempotency_key=occurrence_key,
                payload=original.payload,
            )

    asyncio.run(scenario())
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (original.payload["request_id"],)
    ) == [("pending",)]


def test_app_queue_access_cannot_read_or_change_another_tenants_envelope(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    own = _request(database, TENANT_A, "xt_t1")
    other = _request(database, TENANT_B, "xt_t2")
    own_id = asyncio.run(_enqueue(database, own))
    other_id = asyncio.run(_enqueue(database, other))

    async def scenario() -> None:
        async with database.tenant_transaction(TENANT_A) as connection:
            cursor = await connection.execute(
                "SELECT id FROM public.procrastinate_jobs WHERE id IN (%s,%s)",
                (own_id, other_id),
            )
            assert await cursor.fetchall() == [(own_id,)]
            # AQ-09: xt_app may only touch the status column, never the envelope.
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with connection.transaction():
                    await connection.execute(
                        "UPDATE public.procrastinate_jobs SET args=%s WHERE id=%s RETURNING id",
                        ("{}", other_id),
                    )
            cursor = await connection.execute(
                "SELECT id FROM public.procrastinate_events WHERE job_id=%s", (other_id,)
            )
            assert await cursor.fetchall() == []

    asyncio.run(scenario())


def test_queue_still_operates_after_public_schema_default_access_is_revoked(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    database.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    job = _request(database, TENANT_A, "xt_t1")
    assert asyncio.run(_enqueue(database, job)) > 0


def test_job_fingerprint_prevents_reusing_existing_key_with_changed_task(
    worker_database: WorkerDatabase,
) -> None:
    from xt.application.jobs import JobRequest

    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    asyncio.run(_enqueue(database, job))
    changed = JobRequest(TENANT_A, "business.fail", job.idempotency_key, job.payload)
    with pytest.raises(ValueError, match="idempotency"):
        asyncio.run(_enqueue(database, changed))


def test_durable_request_lock_suppresses_concurrent_duplicate_handler_effects(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    asyncio.run(_enqueue(database, job))

    async def scenario() -> None:
        runtime = database.runtime(concurrency=2)
        envelope: dict[str, Any] = {
            "tenant_id": str(TENANT_A),
            "task_name": job.task_name,
            "idempotency_key": job.idempotency_key,
            "payload": job.payload,
        }
        async with runtime.tenant_pool:
            await asyncio.gather(runtime.dispatch(**envelope), runtime.dispatch(**envelope))

    asyncio.run(scenario())
    assert database.execute(
        "SELECT count(*) FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(1,)]


def test_old_worker_cannot_retry_dead_letter_or_acknowledge_new_delivery(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    job_id = asyncio.run(_enqueue(database, job))

    async def scenario() -> None:
        runtime = database.runtime(max_attempts=2)
        first = _claim_snapshot(database, job_id, runtime)
        assert await runtime.recover_stalled() == 1
        second = _claim_snapshot(database, job_id, runtime)
        assert second.job.attempts == 1
        async with runtime.app.open_async():
            # An acknowledgement from the old worker cannot finish this delivery.
            await runtime.app.job_manager.finish_job(
                first.job, status=Status.SUCCEEDED, delete_job=False
            )
            assert database.execute(
                "SELECT status,attempts FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
            ) == [("doing", 1)]
            assert await runtime.recover_stalled() == 1
            await runtime.app.job_manager.retry_job(first.job, retry_at=datetime.now(UTC))
            await runtime.app.job_manager.retry_job(second.job, retry_at=datetime.now(UTC))
        assert database.execute(
            "SELECT status,attempts FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
        ) == [("failed", 2)]

    asyncio.run(scenario())


def test_manager_checks_durable_attempt_budget_before_retrying_current_delivery(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    job_id = asyncio.run(_enqueue(database, job))

    async def scenario() -> None:
        runtime = database.runtime(max_attempts=2)
        first = _claim_snapshot(database, job_id, runtime)
        async with runtime.app.open_async():
            await runtime.app.job_manager.retry_job(first.job, retry_at=datetime.now(UTC))
            assert database.execute(
                "SELECT status,attempts FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
            ) == [("todo", 1)]
            second = _claim_snapshot(database, job_id, runtime)
            await runtime.app.job_manager.retry_job(second.job, retry_at=datetime.now(UTC))
        assert database.execute(
            "SELECT status,attempts FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
        ) == [("failed", 2)]

    asyncio.run(scenario())


def test_worker_losing_ownership_during_handler_rolls_back_all_business_effects(
    worker_database: WorkerDatabase,
) -> None:
    from tests.integration.worker_handlers import finish
    from xt.adapters.queue.runtime import StaleDeliveryError

    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    job_id = asyncio.run(_enqueue(database, job))

    async def scenario() -> None:
        async def lose_ownership_after_business_write(connection: Any, request: Any) -> None:
            await finish(connection, request)
            # No sleep: deterministically interleave recovery before final commit.
            assert await runtime.recover_stalled() == 1

        runtime = database.runtime(
            handlers={"business.finish": lose_ownership_after_business_write}
        )
        context = _claim_snapshot(database, job_id, runtime)
        async with runtime.tenant_pool:
            with pytest.raises(StaleDeliveryError):
                await runtime.dispatch(
                    context=context,
                    tenant_id=str(TENANT_A),
                    task_name=job.task_name,
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                )

    asyncio.run(scenario())
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (job.payload["request_id"],)
    ) == [("pending",)]
    assert (
        database.execute("SELECT 1 FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,))
        == []
    )


def test_caller_cannot_reserve_periodic_occurrence_key_namespace(
    worker_database: WorkerDatabase,
) -> None:
    original = _request(worker_database, TENANT_A, "xt_t1")
    reserved = JobRequest(TENANT_A, original.task_name, "periodic:1:1000", original.payload)

    async def scenario() -> None:
        runtime = worker_database.runtime()
        async with worker_database.tenant_transaction(TENANT_A) as connection:
            with pytest.raises(ValueError, match="reserved"):
                await runtime.queue.enqueue(connection, reserved)
            with pytest.raises(ValueError, match="reserved"):
                await runtime.queue.periodic(
                    connection, reserved, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
                )
        async with await AsyncConnection.connect(worker_database.dsn("xt_app")) as connection:
            await connection.execute("SELECT set_config('xt.tenant_id',%s,true)", (str(TENANT_A),))
            with pytest.raises(ValueError, match="autocommit"):
                await runtime.queue.enqueue(connection, original)

    asyncio.run(scenario())


def test_one_shot_and_periodic_cannot_reuse_same_tenant_key(
    worker_database: WorkerDatabase,
) -> None:
    first = _request(worker_database, TENANT_A, "xt_t1")
    second = _request(worker_database, TENANT_A, "xt_t1")

    async def scenario() -> None:
        runtime = worker_database.runtime()
        async with worker_database.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.enqueue(connection, first)
            with pytest.raises(ValueError, match="idempotency_key"):
                await runtime.queue.periodic(
                    connection, first, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
                )
        async with worker_database.tenant_transaction(TENANT_A) as connection:
            await runtime.queue.periodic(
                connection, second, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
            )
            with pytest.raises(ValueError, match="idempotency_key"):
                await runtime.queue.enqueue(connection, second)

    asyncio.run(scenario())


def test_occurrence_foreign_key_cannot_point_to_other_tenant_schedule(
    worker_database: WorkerDatabase,
) -> None:
    own = _request(worker_database, TENANT_A, "xt_t1")
    foreign = _request(worker_database, TENANT_B, "xt_t2")

    async def scenario() -> None:
        runtime = worker_database.runtime()
        own_id = await _enqueue(worker_database, own)
        async with worker_database.tenant_transaction(TENANT_B) as connection:
            foreign_schedule = await runtime.queue.periodic(
                connection, foreign, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            async with worker_database.tenant_transaction(TENANT_A) as connection:
                await connection.execute(
                    "UPDATE xt_queue.requests SET schedule_id=%s WHERE job_id=%s",
                    (foreign_schedule, own_id),
                )

    asyncio.run(scenario())


def test_delivery_cannot_execute_another_requests_key_with_identical_content(
    worker_database: WorkerDatabase,
) -> None:
    original = _request(worker_database, TENANT_A, "xt_t1")
    other = JobRequest(
        TENANT_A, original.task_name, original.idempotency_key + ":other", original.payload
    )

    async def scenario() -> None:
        runtime = worker_database.runtime()
        original_id = await _enqueue(worker_database, original)
        await _enqueue(worker_database, other)
        context = _claim_snapshot(worker_database, original_id, runtime)
        async with runtime.tenant_pool:
            with pytest.raises(ValueError, match=r"job id|delivery|request"):
                await runtime.dispatch(
                    context,
                    tenant_id=str(TENANT_A),
                    task_name=other.task_name,
                    idempotency_key=other.idempotency_key,
                    payload=other.payload,
                )

    asyncio.run(scenario())
    assert worker_database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (original.payload["request_id"],)
    ) == [("pending",)]
