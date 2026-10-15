# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Trusted integration plugins that exercise business writes under real tenant RLS."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection, sql

from xt.application.jobs import JobRequest

Handler = Callable[[AsyncConnection[Any], JobRequest], Awaitable[None]]


def _schema(job: JobRequest) -> sql.Identifier:
    schema = job.payload["schema"]
    if schema not in {"xt_t1", "xt_t2"}:
        raise ValueError("unknown integration tenant schema")
    return sql.Identifier(schema)


async def finish(connection: AsyncConnection[Any], job: JobRequest) -> None:
    schema = _schema(job)
    cursor = await connection.execute(
        sql.SQL("UPDATE {}.requests SET status = 'done' WHERE id = %s RETURNING id").format(schema),
        (job.payload["request_id"],),
    )
    if await cursor.fetchone() is None:
        raise LookupError("request is not visible in worker tenant context")
    await connection.execute(
        sql.SQL(
            "INSERT INTO {}.effects(job_key,tenant_id,observed_tenant,observed_role) "
            "VALUES (%s,%s,current_setting('xt.tenant_id')::uuid,current_user)"
        ).format(schema),
        (job.idempotency_key, job.tenant_id),
    )


async def fail(connection: AsyncConnection[Any], job: JobRequest) -> None:
    await finish(connection, job)
    raise RuntimeError("integration failure after business write must roll back")


async def block_once(connection: AsyncConnection[Any], job: JobRequest) -> None:
    # A transaction lock is observable from a different session without adding
    # a committed side effect to the transaction whose rollback we are testing.
    barrier = int(job.payload["barrier"])
    await connection.execute("SELECT pg_advisory_xact_lock(%s)", (barrier,))
    while True:
        cursor = await connection.execute(
            "SELECT released FROM public.worker_test_gates WHERE id = %s", (barrier,)
        )
        row = await cursor.fetchone()
        if row is not None and row[0]:
            break
        await asyncio.sleep(0.1)
    await finish(connection, job)


def handlers() -> dict[str, Handler]:
    """Factory imported by the real ``xt worker`` subprocess."""
    return {"business.finish": finish, "business.fail": fail, "business.block_once": block_once}
