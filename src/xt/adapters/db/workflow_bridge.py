# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tenant-scoped process persistence and transactional timer outbox.

All synchronous writes require PgUnitOfWork.run's active transaction. The async
relay uses the queue's caller-owned transaction for enqueue and acknowledgement.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.types.json import Json

from xt.adapters.db.audit import lock_audit_chain
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
            lock_audit_chain(cur, self.uow.actor.tenant.id)  # thứ tự khóa: chuỗi audit -> dòng
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

    def link(self, ref: str, instance_id: str, *, if_absent: bool = False) -> bool:
        """Nối bản ghi <-> instance. `if_absent`: bản ghi đã gắn instance khác thì giữ nguyên
        (`start_process` trên subject đã có quy trình), trả False."""
        cur = self.uow.current_cursor
        cur.execute(
            "INSERT INTO xt_meta.workflow_request(tenant_id,ref,instance_id) VALUES (%s,%s,%s)"
            + (" ON CONFLICT (tenant_id,ref) DO NOTHING" if if_absent else ""),
            (self.uow.actor.tenant.id, ref, instance_id),
        )
        return bool(cur.rowcount == 1)


class PgTimerFireStore:
    """`TimerFireStore` của `TimerPoller` trên `xt_meta.workflow_timer_fire` (0012).

    Mỗi `transaction()` là MỘT transaction tenant của `uow` (actor dịch vụ xt-worker): giành kỳ
    (`INSERT ... ON CONFLICT DO NOTHING` trên khóa `(tenant, process_id, period_key)`), tạo instance
    qua `ProcessService` (`PgWorkflowStore` dùng chung cursor) rồi nối instance vào kỳ. Worker khác
    chạy cùng kỳ chờ khóa của dòng kỳ rồi thấy `rowcount=0` -> `already`. Lỗi -> rollback cả kỳ."""

    def __init__(self, uow: WorkflowUnitOfWork):
        self.uow = uow

    @contextmanager
    def transaction(self) -> Iterator[_PgTimerFireTx]:
        with self.uow.tenant_tx(self.uow.actor) as cur:
            yield _PgTimerFireTx(self.uow.actor.tenant.id, cur)


class _PgTimerFireTx:
    def __init__(self, tenant_id: UUID, cur: Any):
        self.tenant_id, self.cur = tenant_id, cur

    def claim_period(
        self, *, process_key: str, period_key: str, status: str, version: int, fired_at: datetime
    ) -> bool:
        self.cur.execute(
            "INSERT INTO xt_meta.workflow_timer_fire"
            "(tenant_id,process_id,period_key,status,version,fired_at) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (tenant_id,process_id,period_key) DO NOTHING",
            (self.tenant_id, process_key, period_key, status, version, fired_at),
        )
        return bool(self.cur.rowcount == 1)

    def save_instance(
        self, *, instance_id: str, process_key: str, version: int, state: Any
    ) -> None:  # pragma: no cover - lớp ghép luôn tạo instance qua ProcessService
        raise RuntimeError("PgTimerFireStore cần TimerPoller(processes=ProcessService)")

    def link_instance(self, *, process_key: str, period_key: str, instance_id: str) -> None:
        self.cur.execute(
            "UPDATE xt_meta.workflow_timer_fire SET instance_id=%s "
            "WHERE tenant_id=%s AND process_id=%s AND period_key=%s",
            (instance_id, self.tenant_id, process_key, period_key),
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
