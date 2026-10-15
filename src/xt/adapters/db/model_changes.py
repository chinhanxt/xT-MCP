# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Relay outbox `xt_meta.model_change_request` (đề xuất model đã duyệt) sang hàng đợi.

Gọi trong transaction tenant của job relay (`tenant_async_tx` / connection của handler): enqueue
`model.apply` và đánh dấu `dispatched_at` commit cùng nhau; khóa idempotency theo proposal nên
relay chạy lại (hay hai relay song song, SKIP LOCKED) không nhân đôi job.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from xt.application.jobs import JobQueue, JobRequest

APPLY_TASK = "model.apply"


async def relay_model_changes(
    connection: AsyncConnection[Any],
    queue: JobQueue,
    tenant_id: UUID,
    *,
    limit: int = 50,
    task_name: str = APPLY_TASK,
) -> int:
    if not 1 <= limit <= 1000:
        raise ValueError("limit must be between 1 and 1000")
    async with connection.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "SELECT proposal_id FROM xt_meta.model_change_request WHERE tenant_id=%s"
            " AND state='approved' AND dispatched_at IS NULL ORDER BY decided_at"
            " LIMIT %s FOR UPDATE SKIP LOCKED",
            (tenant_id, limit),
        )
        rows = await cur.fetchall()
        for row in rows:
            pid = row["proposal_id"]
            await queue.enqueue(
                connection,
                JobRequest(tenant_id, task_name, f"model.apply:{pid}", {"proposal_id": str(pid)}),
            )
            await cur.execute(
                "UPDATE xt_meta.model_change_request SET dispatched_at=clock_timestamp()"
                " WHERE tenant_id=%s AND proposal_id=%s",
                (tenant_id, pid),
            )
    return len(rows)
