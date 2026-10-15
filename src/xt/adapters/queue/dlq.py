# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dead letters (AQ-08): list/show failed jobs and failing schedules, requeue, purge.

Runs through the xt_control login only. Payload values are never printed:
show (alias inspect) gives payload keys and size, enough to locate the business
row. requeue/purge go through ``xt_queue.requeue_dead``/``purge_dead``.
Wired as ``xt worker dlq``; ``python -m xt.adapters.queue.dlq`` is the same command.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from .config import validate_database_url

_DEAD_JOBS = """
SELECT j.id AS job_id, j.args->>'tenant_id' AS tenant_id,
       j.args->>'task_name' AS task_name, j.args->>'idempotency_key' AS idempotency_key,
       j.attempts, r.dead_at, r.last_error,
       (SELECT max(e.at) FROM procrastinate_events e WHERE e.job_id = j.id) AS failed_at
FROM procrastinate_jobs j
LEFT JOIN xt_queue.requests r ON r.job_id = j.id
WHERE j.queue_name = 'xt' AND j.task_name = 'xt.dispatch' AND j.status = 'failed'
  AND r.job_id IS NOT NULL  -- a requeued job is superseded by its new job id
ORDER BY j.id DESC LIMIT %s
"""
_FAILING_SCHEDULES = """
SELECT id AS schedule_id, tenant_id, key, task_name, next_at, last_error, last_error_at,
       failures, retry_at
FROM xt_queue.schedules WHERE active AND last_error IS NOT NULL
ORDER BY last_error_at DESC LIMIT %s
"""
_JOB = """
SELECT j.id AS job_id, j.status, j.attempts, j.scheduled_at, j.worker_id,
       j.args->>'tenant_id' AS tenant_id, j.args->>'task_name' AS task_name,
       j.args->>'idempotency_key' AS idempotency_key,
       (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(j.args->'payload') k)
           AS payload_keys,
       octet_length((j.args->'payload')::text) AS payload_bytes,
       r.completed, r.cancelled, r.dead_at, r.last_error, r.schedule_id
FROM procrastinate_jobs j LEFT JOIN xt_queue.requests r ON r.job_id = j.id
WHERE j.id = %s AND j.queue_name = 'xt'
"""
_EVENTS = "SELECT type, at FROM procrastinate_events WHERE job_id = %s ORDER BY id"


async def list_dead(queue_dsn: str, *, limit: int = 50) -> dict[str, list[dict[str, Any]]]:
    async with await AsyncConnection.connect(
        queue_dsn, autocommit=True, row_factory=dict_row
    ) as connection:
        jobs = await (await connection.execute(_DEAD_JOBS, (limit,))).fetchall()
        schedules = await (await connection.execute(_FAILING_SCHEDULES, (limit,))).fetchall()
    return {"dead_jobs": list(jobs), "failing_schedules": list(schedules)}


async def inspect_dead(queue_dsn: str, job_id: int) -> dict[str, Any] | None:
    async with await AsyncConnection.connect(
        queue_dsn, autocommit=True, row_factory=dict_row
    ) as connection:
        job = await (await connection.execute(_JOB, (job_id,))).fetchone()
        if job is None:
            return None
        events = await (await connection.execute(_EVENTS, (job_id,))).fetchall()
    return {**job, "events": list(events)}


_OLDER_THAN = """
SELECT j.id FROM procrastinate_jobs j JOIN xt_queue.requests r ON r.job_id = j.id
WHERE j.queue_name = 'xt' AND j.task_name = 'xt.dispatch' AND j.status = 'failed'
  AND r.dead_at < now() - make_interval(days => %s)
ORDER BY j.id
"""


async def requeue_dead(queue_dsn: str, job_ids: Sequence[int]) -> dict[str, Any]:
    """Each job in its own transaction; ``None`` = not a requeueable dead letter."""
    result: dict[str, Any] = {}
    async with await AsyncConnection.connect(queue_dsn, autocommit=True) as connection:
        for job_id in job_ids:
            row = await (
                await connection.execute("SELECT xt_queue.requeue_dead(%s)", (job_id,))
            ).fetchone()
            result[str(job_id)] = row[0] if row else None
    return {"requeued": result}


async def purge_dead(
    queue_dsn: str, job_ids: Sequence[int], *, older_than_days: int | None = None
) -> dict[str, Any]:
    async with await AsyncConnection.connect(queue_dsn, autocommit=True) as connection:
        targets = list(job_ids)
        if older_than_days is not None:
            rows = await (await connection.execute(_OLDER_THAN, (older_than_days,))).fetchall()
            targets += [int(row[0]) for row in rows if int(row[0]) not in targets]
        result: dict[str, bool] = {}
        for job_id in targets:
            row = await (
                await connection.execute("SELECT xt_queue.purge_dead(%s)", (job_id,))
            ).fetchone()
            result[str(job_id)] = bool(row and row[0])
    return {"purged": result}


def _json(value: Any) -> str:
    def default(item: Any) -> str:
        return item.isoformat() if isinstance(item, datetime) else str(item)

    return json.dumps(value, default=default, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xt worker dlq", description="Inspect dead-lettered jobs and failing schedules"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="list failed jobs and schedules with errors")
    listing.add_argument("--limit", type=int, default=50)
    show = commands.add_parser(
        "show", aliases=["inspect"], help="show one job, its request and events"
    )
    show.add_argument("job_id", type=int)
    requeue = commands.add_parser("requeue", help="defer a dead job again with a fresh budget")
    requeue.add_argument("job_ids", type=int, nargs="+")
    purge = commands.add_parser("purge", help="cancel the request and delete the dead job")
    purge.add_argument("job_ids", type=int, nargs="*")
    purge.add_argument("--older-than-days", type=int, dest="older_than_days")
    return parser


async def _run(dsn: str, args: argparse.Namespace) -> Any:
    if args.command == "list":
        return await list_dead(dsn, limit=args.limit)
    if args.command == "requeue":
        return await requeue_dead(dsn, args.job_ids)
    if args.command == "purge":
        return await purge_dead(dsn, args.job_ids, older_than_days=args.older_than_days)
    return await inspect_dead(dsn, args.job_id)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        dsn = validate_database_url(
            os.environ.get("XT_QUEUE_DATABASE_URL", "").strip(),
            setting="XT_QUEUE_DATABASE_URL",
            role="xt_control",
        )
        if args.command == "list" and args.limit < 1:
            raise ValueError("--limit must be >= 1")
        if args.command == "purge":
            if not args.job_ids and args.older_than_days is None:
                raise ValueError("purge needs job ids or --older-than-days")
            if args.older_than_days is not None and args.older_than_days < 0:
                raise ValueError("--older-than-days must be >= 0")
    except ValueError as exc:
        print(f"xt worker dlq: {exc}", file=sys.stderr)
        return 2
    try:
        result = asyncio.run(_run(dsn, args))
    except Exception as exc:
        # Driver messages may contain DSNs; report only the class.
        print(f"xt worker dlq: operation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
    if result is None:
        print(f"xt worker dlq: job {args.job_id} not found", file=sys.stderr)
        return 1
    print(_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
