# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tenant-scoped process persistence and transactional timer outbox.

All synchronous writes require PgUnitOfWork.run's active transaction. The async
relay uses the queue's caller-owned transaction for enqueue and acknowledgement.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Json

from xt.application.jobs import JobQueue, JobRequest
from xt.application.process.ports import InstanceRecord
from xt.domain.model.context import ModelActor


class WorkflowUnitOfWork(Protocol):
    @property
    def actor(self) -> ModelActor: ...
    @property
    def current_cursor(self) -> Any: ...
    def tenant_tx(self, actor: ModelActor, *, workflow: bool = False) -> Any: ...


class PgWorkflowStore:
    def __init__(self, uow: WorkflowUnitOfWork):
        self.uow = uow

    def insert(self, instance_id: str, record: InstanceRecord) -> None:
        self.uow.current_cursor.execute(
            "INSERT INTO xt_meta.workflow_instance "
            "(tenant_id,id,process_id,version,status,state,candidates,next_due) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                self.uow.actor.tenant.id,
                instance_id,
                record.process_id,
                record.version,
                record.status,
                Json(record.state),
                record.candidates,
                record.next_due,
            ),
        )

    def get_for_update(self, instance_id: str) -> InstanceRecord | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "SELECT * FROM xt_meta.workflow_instance WHERE tenant_id=%s AND id=%s FOR UPDATE",
                (self.uow.actor.tenant.id, instance_id),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return InstanceRecord(
            row["process_id"],
            row["version"],
            row["status"],
            row["state"],
            row["candidates"],
            row["next_due"],
        )

    def save(self, instance_id: str, record: InstanceRecord) -> None:
        cur = self.uow.current_cursor
        cur.execute(
            "UPDATE xt_meta.workflow_instance SET status=%s,state=%s,candidates=%s,"
            "next_due=%s WHERE tenant_id=%s AND id=%s AND process_id=%s AND version=%s",
            (
                record.status,
                Json(record.state),
                record.candidates,
                record.next_due,
                self.uow.actor.tenant.id,
                instance_id,
                record.process_id,
                record.version,
            ),
        )
        if cur.rowcount != 1:
            raise KeyError(instance_id)

    def open_for(self, user: str) -> list[str]:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "SELECT id FROM xt_meta.workflow_instance WHERE tenant_id=%s "
                "AND status='running' AND candidates @> ARRAY[%s]::text[] ORDER BY id",
                (self.uow.actor.tenant.id, user),
            )
            return [row["id"] for row in cur.fetchall()]

    def instance_for(self, ref: str) -> str | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "SELECT instance_id FROM xt_meta.workflow_request WHERE tenant_id=%s AND ref=%s",
                (self.uow.actor.tenant.id, ref),
            )
            row = cur.fetchone()
            return row["instance_id"] if row else None

    def ref_for(self, instance_id: str) -> str | None:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            cur.execute(
                "SELECT ref FROM xt_meta.workflow_request WHERE tenant_id=%s AND instance_id=%s",
                (self.uow.actor.tenant.id, instance_id),
            )
            row = cur.fetchone()
            return row["ref"] if row else None

    def link(self, ref: str, instance_id: str) -> None:
        self.uow.current_cursor.execute(
            "INSERT INTO xt_meta.workflow_request(tenant_id,ref,instance_id) VALUES (%s,%s,%s)",
            (self.uow.actor.tenant.id, ref, instance_id),
        )


class PgTimerScheduler:
    """Persist intent in the business transaction; no second connection or commit."""

    def __init__(self, uow: WorkflowUnitOfWork):
        self.uow = uow

    def wake_at(self, *, process_id: str, instance_id: str, due: datetime) -> None:
        if due.tzinfo is None or due.utcoffset() is None:
            raise ValueError("timer due must include a timezone")
        self.uow.current_cursor.execute(
            "INSERT INTO xt_meta.workflow_timer_outbox(tenant_id,instance_id,process_id,due) "
            "VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (self.uow.actor.tenant.id, instance_id, process_id, due),
        )


async def relay_timers(
    connection: AsyncConnection[Any],
    queue: JobQueue,
    tenant_id: UUID,
    *,
    limit: int = 100,
    task_name: str = "workflow.tick",
) -> int:
    """Call inside a matching tenant transaction; queue validates its boundary.

    The caller commits enqueue + dispatched_at together. SKIP LOCKED permits
    concurrent relays. The queue dedupes even if an operator resets an outbox row.
    """
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    async with connection.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT instance_id,process_id,due FROM xt_meta.workflow_timer_outbox "
            "WHERE tenant_id=%s AND dispatched_at IS NULL ORDER BY due "
            "LIMIT %s FOR UPDATE SKIP LOCKED",
            (tenant_id, limit),
        )
        rows = await cur.fetchall()
        for row in rows:
            due = row["due"].astimezone(UTC)
            digest = hashlib.sha256(f"{row['instance_id']}:{due.isoformat()}".encode()).hexdigest()
            job = JobRequest(
                tenant_id,
                task_name,
                f"workflow.timer:{digest}",
                {
                    "process_id": row["process_id"],
                    "instance_id": row["instance_id"],
                    "due": due.isoformat(),
                },
            )
            await queue.schedule_at(connection, job, due)
            await cur.execute(
                "UPDATE xt_meta.workflow_timer_outbox SET dispatched_at=clock_timestamp() "
                "WHERE tenant_id=%s AND instance_id=%s AND due=%s",
                (tenant_id, row["instance_id"], due),
            )
    return len(rows)


@asynccontextmanager
async def tenant_async_tx(dsn: str, tenant_id: UUID) -> AsyncIterator[AsyncConnection[Any]]:
    """Transaction async xt_app với ngữ cảnh tenant (cho JobQueue: enqueue/periodic)."""
    conn = await AsyncConnection.connect(dsn, autocommit=True)
    async with conn, conn.transaction():
        await conn.execute(
            "SELECT xt.set_context(%s::uuid, NULL, '{}'::text[], '{}'::uuid[], '{}'::uuid[],"
            " NULL, NULL, NULL)",
            (tenant_id,),
        )
        yield conn
