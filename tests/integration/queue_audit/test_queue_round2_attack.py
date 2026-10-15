# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Round-2 tester attacks on real PG17: pooled-context leakage after handler
errors, lock_timeout under contention, log redaction through a real worker,
recovery liveness, schedule fairness with 100+ failing schedules, DLQ fencing."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from procrastinate.jobs import Status
from psycopg import AsyncConnection

from tests.integration.queue_audit.conftest import AuditDatabase
from tests.integration.test_worker_queue import TENANT_A, TENANT_B, _request
from tests.integration.test_worker_queue_review import _claim_snapshot
from xt.adapters.queue.dlq import purge_dead, requeue_dead
from xt.adapters.queue.runtime import SCHEDULES_PER_TENANT_PER_TICK
from xt.application.jobs import JobRequest

pytestmark = pytest.mark.integration


def _enqueue(db: AuditDatabase, runtime: Any, job: JobRequest) -> int:
    async def scenario() -> int:
        async with db.tenant_transaction(job.tenant_id) as connection:
            return int(await runtime.queue.enqueue(connection, job))

    return asyncio.run(scenario())


async def _dispatch(runtime: Any, job: JobRequest, context: Any = None) -> None:
    await runtime.dispatch(
        context,
        tenant_id=str(job.tenant_id),
        task_name=job.task_name,
        idempotency_key=job.idempotency_key,
        payload=job.payload,
    )


def test_r2_handler_errors_and_savepoints_do_not_leak_context_across_tenants(
    audit_db: AuditDatabase,
) -> None:
    """Pool size 1: A fails mid-tx, A swallows a failed savepoint, then B runs."""
    db = audit_db
    seen: dict[str, Any] = {}

    async def handler(connection: AsyncConnection[Any], job: JobRequest) -> None:
        pid = (await (await connection.execute("SELECT pg_backend_pid()")).fetchone())[0]
        mode = job.payload.get("mode")
        insert = (
            "INSERT INTO effects(job_key,tenant_id,observed_tenant,observed_role) "
            "VALUES (%s,%s,xt.tenant(),current_user)"
        )
        if mode == "raise":
            seen["pid_raise"] = pid
            async with connection.transaction():
                await connection.execute(insert, (job.idempotency_key, job.tenant_id))
                raise RuntimeError("handler failure inside savepoint")
        if mode == "swallow":
            seen["pid_swallow"] = pid
            with contextlib.suppress(psycopg.errors.DivisionByZero):
                async with connection.transaction():
                    await connection.execute("SELECT 1/0")
            await connection.execute(insert, (job.idempotency_key, job.tenant_id))
            return
        seen["pid_b"] = pid
        cursor = await connection.execute(
            "SELECT xt.tenant(), current_setting('xt.tenant_id'), current_user, "
            "current_setting('search_path')"
        )
        seen["b"] = await cursor.fetchone()
        seen["b_rows"] = (await (await connection.execute("SELECT count(*) FROM effects")).fetchone())[0]

    runtime = db.runtime(handlers={"business.finish": handler}, max_attempts=1)
    runtime.tenant_pool._max_size = runtime.tenant_pool._min_size = 1
    raising = _request(db, TENANT_A, "xt_t1", mode="raise")
    swallowing = _request(db, TENANT_A, "xt_t1", mode="swallow")
    other = _request(db, TENANT_B, "xt_t2")
    for job in (raising, swallowing, other):
        _enqueue(db, runtime, job)

    async def scenario() -> None:
        async with runtime.tenant_pool:
            with pytest.raises(RuntimeError):
                await _dispatch(runtime, raising)
            await _dispatch(runtime, swallowing)
            await _dispatch(runtime, other)

    asyncio.run(scenario())
    assert seen["pid_raise"] == seen["pid_swallow"] == seen["pid_b"]
    assert seen["b"] == (TENANT_B, str(TENANT_B), "xt_job_t2", "xt_t2, pg_temp")
    assert seen["b_rows"] == 0  # B sees no effects (its own schema, its own RLS)
    effects = db.execute(
        "SELECT job_key, tenant_id, observed_tenant, observed_role FROM xt_t1.effects "
        "WHERE job_key = ANY(%s)",
        ([raising.idempotency_key, swallowing.idempotency_key],),
    )
    assert effects == [(swallowing.idempotency_key, TENANT_A, TENANT_A, "xt_job_t1")]
    assert db.execute(
        "SELECT completed FROM xt_queue.requests WHERE idempotency_key=%s",
        (raising.idempotency_key,),
    ) == [(False,)]


def test_r2_lock_timeout_beats_statement_timeout_inside_handler(audit_db: AuditDatabase) -> None:
    """Handler blocked on a business row held by another session: LockNotAvailable at ~1 s."""
    db = audit_db
    runtime = db.runtime(lock_timeout_seconds=1, statement_timeout_seconds=4)
    job = _request(db, TENANT_A, "xt_t1")
    _enqueue(db, runtime, job)

    async def scenario() -> float:
        async with db.tenant_transaction(TENANT_A) as holder, runtime.tenant_pool:
            await holder.execute(
                "SELECT 1 FROM xt_t1.requests WHERE id=%s FOR UPDATE", (job.payload["request_id"],)
            )
            started = time.monotonic()
            with pytest.raises(psycopg.errors.LockNotAvailable):
                await _dispatch(runtime, job)
            return time.monotonic() - started

    elapsed = asyncio.run(asyncio.wait_for(scenario(), 20))
    assert 0.8 <= elapsed < 3.0
    assert db.execute(
        "SELECT count(*) FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(0,)]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))
        self.lines.append(repr({k: v for k, v in record.__dict__.items() if k != "msg"}))


def test_r2_real_worker_logs_hide_payload_exception_text_and_psycopg_detail(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    marker_payload = "PAYLOAD-R2-7f3a"
    marker_message = "MESSAGE-R2-c91e"
    marker_detail = "DETAIL-R2-55b0"

    async def handler(connection: AsyncConnection[Any], job: JobRequest) -> None:
        insert = (
            "INSERT INTO effects(job_key,tenant_id,observed_tenant,observed_role) "
            "VALUES (%s,%s,%s,current_user)"
        )
        key = f"{marker_detail}-{job.idempotency_key}"
        await connection.execute(insert, (key, job.tenant_id, job.tenant_id))
        try:
            await connection.execute(insert, (key, job.tenant_id, job.tenant_id))
        except psycopg.errors.UniqueViolation as exc:
            raise RuntimeError(f"customer {marker_message} rejected") from exc

    runtime = db.runtime(handlers={"business.finish": handler}, max_attempts=1)
    job = _request(
        db,
        TENANT_A,
        "xt_t1",
        note=f"x) ('{marker_payload}'\n\"q\" (",
        nested={"a": [f"{marker_payload})", "(("]},
    )
    job_id = _enqueue(db, runtime, job)
    capture = _Capture()
    root = logging.getLogger()
    previous = root.level
    root.addHandler(capture)
    root.setLevel(logging.DEBUG)

    async def scenario() -> None:
        task = asyncio.create_task(runtime.run(name="r2-redaction"))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status = db.execute("SELECT status::text FROM procrastinate_jobs WHERE id=%s", (job_id,))
            if status == [("failed",)]:
                break
            await asyncio.sleep(0.2)
        await asyncio.sleep(0.5)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    try:
        asyncio.run(scenario())
    finally:
        root.removeHandler(capture)
        root.setLevel(previous)
    text = "\n".join(capture.lines)
    assert db.execute("SELECT status::text FROM procrastinate_jobs WHERE id=%s", (job_id,)) == [
        ("failed",)
    ]
    assert f"xt.dispatch[{job_id}]" in text  # the worker did log the job
    for marker in (marker_payload, marker_message, marker_detail, job.payload["request_id"]):
        assert marker not in text, marker


def test_r2_recovery_tick_survives_locked_job_locked_request_and_retries_the_rest(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    runtime = db.runtime(max_attempts=2)
    jobs = [_request(db, TENANT_A, "xt_t1") for _ in range(3)]
    ids = [_enqueue(db, runtime, job) for job in jobs]
    for job_id in ids:
        _claim_snapshot(db, job_id, runtime)
    locked_job, exhausted, retryable = ids
    db.execute("UPDATE procrastinate_jobs SET attempts=1 WHERE id=%s", (exhausted,))
    with psycopg.connect(db.dsn("postgres")) as holder:
        holder.execute("SELECT 1 FROM procrastinate_jobs WHERE id=%s FOR UPDATE", (locked_job,))
        holder.execute("SELECT 1 FROM xt_queue.requests WHERE job_id=%s FOR UPDATE", (exhausted,))
        started = time.monotonic()
        recovered = asyncio.run(runtime.recover_stalled())
        elapsed = time.monotonic() - started
        status = dict(
            db.execute(
                "SELECT id, status::text FROM procrastinate_jobs WHERE id = ANY(%s)", (ids,)
            )
        )
        holder.rollback()
    assert recovered == 1
    assert elapsed < 1.0  # SKIP LOCKED: no waiting on the held rows
    assert status == {locked_job: "doing", exhausted: "doing", retryable: "todo"}
    assert runtime.stats["recover_errors"] == 0
    asyncio.run(runtime.recover_stalled())
    status = dict(
        db.execute("SELECT id, status::text FROM procrastinate_jobs WHERE id = ANY(%s)", (ids,))
    )
    assert status == {locked_job: "todo", exhausted: "failed", retryable: "todo"}


def _register(db: AuditDatabase, runtime: Any, job: JobRequest) -> int:
    async def scenario() -> int:
        async with db.tenant_transaction(job.tenant_id) as connection:
            return int(
                await runtime.queue.periodic(connection, job, cron="0 1 * * *", timezone="UTC")
            )

    return asyncio.run(scenario())


def test_r2_fairness_with_120_failing_schedules_across_12_tenants(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    full = db.runtime()
    pad = "x" * 600
    failing: list[int] = []
    created: list[int] = []
    try:
        for _ in range(12):
            tenant = UUID(int=uuid4().int)
            for i in range(10):
                failing.append(
                    _register(db, full, JobRequest(tenant, "business.finish", f"f{i}", {"p": pad}))
                )
        # Tenant A: 10 failing schedules older than its single healthy one.
        for i in range(10):
            failing.append(
                _register(db, full, JobRequest(TENANT_A, "business.finish", f"fa{i}", {"p": pad}))
            )
        healthy_a = _register(db, full, _request(db, TENANT_A, "xt_t1"))
        healthy_b = _register(db, full, _request(db, TENANT_B, "xt_t2"))
        created = [*failing, healthy_a, healthy_b]
        db.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '10 minutes' WHERE id = ANY(%s)",
            (failing,),
        )
        db.execute(
            "UPDATE xt_queue.schedules SET next_at=now()-interval '1 minute' WHERE id = ANY(%s)",
            ([healthy_a, healthy_b],),
        )
        worker = db.runtime(max_payload_bytes=300)  # the padded payloads always fail
        fired_at: dict[int, int] = {}
        errors_per_tick: list[int] = []
        for tick in range(1, 6):
            before = worker.stats["schedule_errors"]
            asyncio.run(worker.dispatch_periodic())
            errors_per_tick.append(worker.stats["schedule_errors"] - before)
            for sid, advanced in db.execute(
                "SELECT id, next_at > now() FROM xt_queue.schedules WHERE id = ANY(%s)",
                ([healthy_a, healthy_b],),
            ):
                if advanced and sid not in fired_at:
                    fired_at[sid] = tick
        assert fired_at == {healthy_a: 1, healthy_b: 1}
        assert all(n <= 100 for n in errors_per_tick)
        backoff = db.execute(
            "SELECT count(*) FILTER (WHERE failures >= 1 AND retry_at > now()), "
            "max(failures) FROM xt_queue.schedules WHERE id = ANY(%s)",
            (failing,),
        )
        assert backoff[0][0] == len(failing)  # every failing schedule ended in backoff
        assert backoff[0][1] == 1  # none hot-looped while in backoff
        per_tenant = db.execute(
            "SELECT max(n) FROM (SELECT count(*) n FROM xt_queue.schedules "
            "WHERE id = ANY(%s) AND failures >= 1 GROUP BY tenant_id) s",
            (failing,),
        )
        assert per_tenant[0][0] <= SCHEDULES_PER_TENANT_PER_TICK * 5
    finally:
        db.execute("UPDATE xt_queue.schedules SET active=false WHERE id = ANY(%s)", (created,))


def test_r2_dlq_requeue_rejects_old_delivery_and_purge_only_takes_dead_letters(
    audit_db: AuditDatabase,
) -> None:
    db = audit_db
    dsn = db.dsn("xt_control")
    runtime = db.runtime(max_attempts=1)
    job = _request(db, TENANT_B, "xt_t2", "business.fail")
    old_id = _enqueue(db, runtime, job)
    old_context = _claim_snapshot(db, old_id, runtime)

    async def fail_once() -> None:
        async with runtime.app.open_async(), runtime.tenant_pool:
            with pytest.raises(RuntimeError):
                await _dispatch(runtime, job, old_context)
            await runtime.job_manager.finish_job(
                old_context.job, status=Status.FAILED, delete_job=False
            )

    asyncio.run(fail_once())
    new_id = asyncio.run(requeue_dead(dsn, [old_id]))["requeued"][str(old_id)]
    assert new_id is not None

    finish = db.runtime(max_attempts=1)  # business.finish handler for the same request

    async def stale_then_fresh() -> tuple[bool, bool]:
        async with finish.app.open_async(), finish.tenant_pool:
            fresh_job = JobRequest(job.tenant_id, job.task_name, job.idempotency_key, job.payload)
            # The stale delivery of the superseded job id is refused before effects.
            with pytest.raises(ValueError, match="job id does not match"):
                await _dispatch(runtime, fresh_job, old_context)
            stale_ack = await finish.job_manager.finish_job(
                old_context.job, status=Status.SUCCEEDED, delete_job=False
            )
            return stale_ack is None, True

    asyncio.run(stale_then_fresh())
    assert db.execute(
        "SELECT status::text FROM procrastinate_jobs WHERE id=%s", (new_id,)
    ) == [("todo",)]
    assert db.execute(
        "SELECT status::text FROM procrastinate_jobs WHERE id=%s", (old_id,)
    ) == [("failed",)]
    assert db.execute(
        "SELECT count(*) FROM xt_t2.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(0,)]
    # Purging the superseded id must not cancel the live request.
    assert asyncio.run(purge_dead(dsn, [old_id]))["purged"][str(old_id)] is True
    assert db.execute(
        "SELECT job_id, cancelled, completed FROM xt_queue.requests WHERE idempotency_key=%s",
        (job.idempotency_key,),
    ) == [(new_id, False, False)]
    # The live requeued job is not a dead letter.
    assert asyncio.run(purge_dead(dsn, [new_id]))["purged"][str(new_id)] is False
    assert asyncio.run(requeue_dead(dsn, [new_id]))["requeued"][str(new_id)] is None
