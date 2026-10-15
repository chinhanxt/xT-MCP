# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Fence acknowledgements using the delivery's worker id and attempt generation."""

from __future__ import annotations

from datetime import UTC, datetime

from procrastinate.connector import BaseConnector
from procrastinate.jobs import Job, Status
from procrastinate.manager import JobManager


class FencedJobManager(JobManager):
    def __init__(self, connector: BaseConnector, *, max_attempts: int) -> None:
        super().__init__(connector)
        self.max_attempts = max_attempts
        # Exception class of the latest failed delivery, recorded on dead letters.
        # Messages are not stored: driver/handler messages may carry payload data.
        self.failure_reasons: dict[int, str] = {}

    async def finish_job(self, job: Job, status: Status, delete_job: bool) -> None:
        if delete_job:
            raise ValueError("xT retains job history; delete_job must be false")
        await self.connector.execute_query_one_async(
            query="SELECT xt_queue.finish_owned("
            "%(id)s,%(worker)s,%(generation)s,%(status)s,%(reason)s) AS applied",
            id=job.id,
            worker=job.worker_id,
            generation=job.attempts,
            status=status.value,
            reason=self.failure_reasons.pop(job.id, None) if job.id is not None else None,
        )

    async def retry_job(
        self,
        job: Job,
        retry_at: datetime | None = None,
        priority: int | None = None,
        queue: str | None = None,
        lock: str | None = None,
    ) -> None:
        await self.connector.execute_query_one_async(
            query="SELECT xt_queue.retry_owned(%(id)s,%(worker)s,%(generation)s,%(max)s,"
            "%(at)s,%(priority)s,%(queue)s,%(lock)s,%(reason)s) AS applied",
            id=job.id,
            worker=job.worker_id,
            generation=job.attempts,
            max=self.max_attempts,
            at=retry_at or datetime.now(UTC),
            priority=priority,
            queue=queue,
            lock=lock,
            reason=self.failure_reasons.pop(job.id, None) if job.id is not None else None,
        )
