# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Caller-owned transaction enqueue, durable deduplication and timers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import procrastinate
from croniter import croniter
from psycopg import AsyncConnection, sql
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from xt.application.jobs import JobRequest, validate_schedule_at

from .cron import cron_to_utc
from .guard import guard_suspended

# AQ-07: jobs carry identifiers, not documents. Configurable per queue.
DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024


def request_fingerprint(job: JobRequest, when: datetime | None = None) -> str:
    value = {
        "tenant_id": str(job.tenant_id),
        "task_name": job.task_name,
        "payload": job.payload,
        "when": validate_schedule_at(when).astimezone(UTC).isoformat() if when else None,
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def envelope(job: JobRequest) -> dict[str, Any]:
    return {
        "tenant_id": str(job.tenant_id),
        "task_name": job.task_name,
        "idempotency_key": job.idempotency_key,
        "payload": job.payload,
    }


def _require_connection(transaction: object) -> None:
    if not isinstance(transaction, AsyncConnection):
        raise TypeError("queue transaction must be a psycopg AsyncConnection")


async def tenant_connection(transaction: object, tenant_id: UUID) -> AsyncConnection[Any]:
    _require_connection(transaction)
    assert isinstance(transaction, AsyncConnection)
    if transaction.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError("queue operation requires an active business transaction")
    if not transaction.autocommit:
        raise ValueError("queue connection requires autocommit=True and an explicit transaction")
    async with transaction.cursor(row_factory=dict_row) as cursor:
        await cursor.execute(
            "SELECT current_user AS role, current_setting('xt.tenant_id', true) AS tenant"
        )
        row = await cursor.fetchone()
    if row is None or row["role"] != "xt_app" or row["tenant"] != str(tenant_id):
        raise ValueError("queue transaction requires xt_app and matching SET LOCAL xt.tenant_id")
    return transaction


@asynccontextmanager
async def queue_connection(
    transaction: object, tenant_id: UUID
) -> AsyncIterator[AsyncConnection[Any]]:
    """Validate the caller transaction; a worker handler running as its tenant's
    bound job role (AQ-05) temporarily resumes xt_app for queue bookkeeping."""
    _require_connection(transaction)
    assert isinstance(transaction, AsyncConnection)
    # Trusted adapter code: the handler statement guard does not apply here.
    with guard_suspended(transaction):
        restore: str | None = None
        restore_path = ""
        try:
            if transaction.info.transaction_status == TransactionStatus.INTRANS:
                async with transaction.cursor(row_factory=dict_row) as cursor:
                    await cursor.execute(
                        "SELECT current_user AS role, session_user AS login, "
                        "current_setting('xt.tenant_id', true) AS tenant, "
                        "current_setting('search_path') AS search_path"
                    )
                    row = await cursor.fetchone()
                    if (
                        row is not None
                        and row["role"] != "xt_app"
                        and row["login"] == "xt_app"
                        and row["tenant"] == str(tenant_id)
                    ):
                        restore = row["role"]
                        restore_path = row["search_path"]
                        await cursor.execute("SET LOCAL ROLE xt_app")
                        # The handler's path is pinned to its tenant schema; upstream
                        # procrastinate functions live in public.
                        await cursor.execute("SET LOCAL search_path TO public")
                        await cursor.execute(
                            "SELECT tenant_role FROM xt_queue.job_binding(%s)", (tenant_id,)
                        )
                        binding = await cursor.fetchone()
                        if binding is None or binding["tenant_role"] != restore:
                            raise ValueError("queue transaction role is not the tenant's job role")
            yield await tenant_connection(transaction, tenant_id)
        finally:
            if (
                restore is not None
                and transaction.info.transaction_status == TransactionStatus.INTRANS
            ):
                await transaction.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(restore))
                )
                await transaction.execute(
                    "SELECT set_config('search_path', %s, true)", (restore_path,)
                )


def check_payload_size(job: JobRequest, maximum: int) -> None:
    size = len(json.dumps(job.payload, separators=(",", ":"), ensure_ascii=False).encode())
    if size > maximum:
        raise ValueError(f"job payload is {size} bytes; the limit is {maximum} bytes")


class ProcrastinateJobQueue:
    def __init__(
        self,
        app: procrastinate.App,
        task_names: set[str],
        *,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        if max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be positive")
        self.app = app
        self.task_names = task_names
        self.max_payload_bytes = max_payload_bytes

    async def enqueue(self, transaction: object, job: JobRequest) -> int:
        return await self._enqueue(transaction, job, None)

    async def schedule_at(self, transaction: object, job: JobRequest, when: datetime) -> int:
        return await self._enqueue(transaction, job, validate_schedule_at(when))

    async def _enqueue(
        self,
        transaction: object,
        job: JobRequest,
        when: datetime | None,
        *,
        occurrence_schedule_id: int | None = None,
    ) -> int:
        _require_connection(transaction)
        if occurrence_schedule_id is None and job.idempotency_key.startswith("periodic:"):
            raise ValueError(
                "idempotency_key prefix periodic: is reserved for schedule occurrences"
            )
        if job.task_name not in self.task_names:
            raise ValueError(f"No registered job handler: {job.task_name}")
        check_payload_size(job, self.max_payload_bytes)
        async with queue_connection(transaction, job.tenant_id) as connection:
            return await self._enqueue_locked(connection, job, when, occurrence_schedule_id)

    async def _enqueue_locked(
        self,
        connection: AsyncConnection[Any],
        job: JobRequest,
        when: datetime | None,
        occurrence_schedule_id: int | None,
    ) -> int:
        fingerprint = request_fingerprint(job, when)
        async with connection.cursor(row_factory=dict_row) as cursor:
            if occurrence_schedule_id is None:
                await cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (f"{job.tenant_id}:{job.idempotency_key}",),
                )
                await cursor.execute(
                    "SELECT 1 FROM xt_queue.schedules WHERE tenant_id=%s AND key=%s",
                    (job.tenant_id, job.idempotency_key),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError("idempotency_key already belongs to a periodic schedule")
            await cursor.execute(
                "INSERT INTO xt_queue.requests "
                "(tenant_id,idempotency_key,fingerprint,envelope_hash,schedule_id) "
                "VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    job.tenant_id,
                    job.idempotency_key,
                    fingerprint,
                    request_fingerprint(job),
                    occurrence_schedule_id,
                ),
            )
            await cursor.execute(
                "SELECT fingerprint,job_id FROM xt_queue.requests "
                "WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                (job.tenant_id, job.idempotency_key),
            )
            row = await cursor.fetchone()
            if row is None or row["fingerprint"] != fingerprint:
                raise ValueError(
                    "idempotency_key already used with different job content or schedule"
                )
            if row["job_id"] is not None:
                return int(row["job_id"])
            task = self.app.tasks["xt.dispatch"]
            job_id = await task.configure(connection=connection, schedule_at=when).defer_async(
                **envelope(job)
            )
            await cursor.execute(
                "UPDATE xt_queue.requests SET job_id=%s WHERE tenant_id=%s AND idempotency_key=%s",
                (job_id, job.tenant_id, job.idempotency_key),
            )
        return job_id

    async def cancel(self, transaction: object, *, tenant_id: UUID, idempotency_key: str) -> bool:
        # Reuse the port's boundary validation for direct cancel calls too.
        JobRequest(tenant_id, "cancel", idempotency_key, {})
        async with queue_connection(transaction, tenant_id) as connection:
            return await self._cancel_locked(connection, tenant_id, idempotency_key)

    async def _cancel_locked(
        self, connection: AsyncConnection[Any], tenant_id: UUID, idempotency_key: str
    ) -> bool:
        changed = False
        async with connection.cursor(row_factory=dict_row) as cursor:
            # Always lock schedules before requests, as the scheduler does.
            await cursor.execute(
                "UPDATE xt_queue.schedules SET active=false "
                "WHERE tenant_id=%s AND key=%s AND active RETURNING id",
                (tenant_id, idempotency_key),
            )
            schedule_ids = [row["id"] for row in await cursor.fetchall()]
            changed = bool(schedule_ids)
            await cursor.execute(
                "SELECT job_id,idempotency_key FROM xt_queue.requests "
                "WHERE tenant_id=%s AND (idempotency_key=%s OR schedule_id=ANY(%s)) "
                "AND NOT completed AND NOT cancelled ORDER BY idempotency_key FOR UPDATE",
                (tenant_id, idempotency_key, schedule_ids),
            )
            for row in await cursor.fetchall():
                await cursor.execute(
                    "UPDATE xt_queue.requests SET cancelled=true "
                    "WHERE tenant_id=%s AND idempotency_key=%s",
                    (tenant_id, row["idempotency_key"]),
                )
                await self.app.job_manager.cancel_job_by_id_async(
                    row["job_id"], connection=connection
                )
                changed = True
        return changed

    async def periodic(
        self, transaction: object, job: JobRequest, *, cron: str, timezone: str
    ) -> int:
        _require_connection(transaction)
        if job.idempotency_key.startswith("periodic:"):
            raise ValueError(
                "idempotency_key prefix periodic: is reserved for schedule occurrences"
            )
        if job.task_name not in self.task_names:
            raise ValueError(f"No registered job handler: {job.task_name}")
        check_payload_size(job, self.max_payload_bytes)
        async with queue_connection(transaction, job.tenant_id) as connection:
            return await self._periodic_locked(connection, job, cron, timezone)

    async def _periodic_locked(
        self, connection: AsyncConnection[Any], job: JobRequest, cron: str, timezone: str
    ) -> int:
        cron_utc = cron_to_utc(cron, timezone)
        fingerprint = hashlib.sha256(
            (request_fingerprint(job) + "\n" + cron_utc + "\n" + timezone).encode()
        ).hexdigest()
        next_at = croniter(cron_utc, datetime.now(UTC)).get_next(datetime)
        async with connection.cursor(row_factory=dict_row) as cursor:
            # Share the namespace lock with one-shot enqueue; checking two
            # separate tables without this lock would race on first insertion.
            await cursor.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                (f"{job.tenant_id}:{job.idempotency_key}",),
            )
            await cursor.execute(
                "SELECT 1 FROM xt_queue.requests WHERE tenant_id=%s AND idempotency_key=%s",
                (job.tenant_id, job.idempotency_key),
            )
            if await cursor.fetchone() is not None:
                raise ValueError("idempotency_key already belongs to a one-shot job")
            await cursor.execute(
                "INSERT INTO xt_queue.schedules "
                "(tenant_id,key,fingerprint,task_name,payload,cron_utc,timezone,"
                "local_cron,next_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    job.tenant_id,
                    job.idempotency_key,
                    fingerprint,
                    job.task_name,
                    Jsonb(job.payload),
                    cron_utc,
                    timezone,
                    cron,
                    next_at,
                ),
            )
            await cursor.execute(
                "SELECT id,fingerprint FROM xt_queue.schedules "
                "WHERE tenant_id=%s AND key=%s FOR UPDATE",
                (job.tenant_id, job.idempotency_key),
            )
            row = await cursor.fetchone()
            if row is None or row["fingerprint"] != fingerprint:
                raise ValueError("idempotency_key already used with different periodic schedule")
            return int(row["id"])
