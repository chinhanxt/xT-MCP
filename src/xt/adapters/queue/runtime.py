# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Control-plane worker and isolated tenant transactions.

Procrastinate is at-least-once. The request row lock serializes duplicate
handlers; completion commits with business writes, before queue acknowledgement.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import procrastinate
from croniter import croniter
from procrastinate.job_context import JobContext
from procrastinate.jobs import Job
from procrastinate.retry import BaseRetryStrategy, RetryDecision
from psycopg import AsyncConnection, sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from xt.application.jobs import JobRequest

from .backend import DEFAULT_MAX_PAYLOAD_BYTES, ProcrastinateJobQueue, request_fingerprint
from .guard import (
    GuardedCursor,
    SessionControlError,
    TransactionControlError,
    changes_session_context,
    ends_transaction,
    handler_guard,
)
from .manager import FencedJobManager
from .schema import SCHEMA

logger = logging.getLogger(__name__)
# Supported tenant context (model engine AUD-13): SECURITY DEFINER, xt_app only.
# Writes xt_meta.session_ctx for model-engine RLS (``xt.cur()``) and the
# transaction-local GUC ``xt.tenant_id`` that the xt_queue policies read.
TENANT_CONTEXT_SQL = (
    "SELECT xt.set_context(%s::uuid, NULL, '{}'::text[], '{}'::uuid[], '{}'::uuid[],"
    " NULL, NULL, NULL)"
)
_SET_CONTEXT_SIGNATURE = "xt.set_context(uuid,uuid,text[],uuid[],uuid[],uuid,text,text)"
JobHandler = Callable[[AsyncConnection[Any], JobRequest], Awaitable[None]]

# AQ-04 documented defaults (seconds); override explicitly per deployment.
DEFAULT_STATEMENT_TIMEOUT_SECONDS = 30
DEFAULT_LOCK_TIMEOUT_SECONDS = 10
DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS = 60
# A schedule without a local handler is reported once it is this many
# maintenance ticks overdue.
SCHEDULE_ERROR_GRACE_TICKS = 3
# Stalled deliveries examined per maintenance tick (each in its own transaction).
RECOVER_BATCH = 100
# Schedules materialized per tick, overall and per tenant (fairness), and the
# backoff cap for a schedule whose materialization keeps failing.
SCHEDULES_PER_TICK = 100
SCHEDULES_PER_TENANT_PER_TICK = 10
SCHEDULE_BACKOFF_CAP_SECONDS = 3600
__all__ = [
    "GuardedCursor",
    "SessionControlError",
    "TransactionControlError",
    "changes_session_context",
    "ends_transaction",
]


class StaleDeliveryError(RuntimeError):
    """Ownership was lost during the handler; its database effects roll back."""


# M1: every ``xt.dispatch[<id>](...)`` call string loses its whole argument
# list (payload, idempotency key, tenant). Greedy to the last ``)`` of the
# message, or to its end when the call string is truncated: quotes or brackets
# inside the payload repr cannot desynchronise a regex that never parses them.
_CALL_STRING = re.compile(r"(xt\.dispatch\[\d*\]\()(?:.*\)|.*)", re.S)


def _redact_payload(text: str) -> str:
    """Replace the argument list of every dispatch call string with a marker."""
    return _CALL_STRING.sub(r"\1[redacted])", text)


class RedactJobPayload(logging.Filter):
    """AQ-06: procrastinate logs job kwargs and handler tracebacks.

    Call strings lose their arguments, extras lose ``task_kwargs`` and
    exceptions are reduced to their class name: handler messages and psycopg
    DETAIL lines can carry row values.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage() if record.args else str(record.msg)
        record.msg, record.args = _redact_payload(message), ()
        if record.exc_info:
            error = record.exc_info[1]
            if error is not None:
                record.msg = f"{record.msg} [{type(error).__name__}]"
            record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        # R10: procrastinate passes ``extra={"exception": repr(error)}`` too.
        if "exception" in record.__dict__:
            record.__dict__["exception"] = "[redacted]"
        for name in ("job", "jobs"):
            value = record.__dict__.get(name)
            if isinstance(value, dict):
                record.__dict__[name] = self._job(value)
            elif isinstance(value, list):
                record.__dict__[name] = [self._job(i) if isinstance(i, dict) else i for i in value]
        return True

    @staticmethod
    def _job(job: dict[str, Any]) -> dict[str, Any]:
        clean = dict(job)
        if "task_kwargs" in clean:
            clean["task_kwargs"] = "[redacted]"
        if isinstance(clean.get("call_string"), str):
            clean["call_string"] = _redact_payload(clean["call_string"])
        return clean


_REDACTOR = RedactJobPayload()


def install_log_redaction(*logger_names: str) -> None:
    """Attach the redactor to every procrastinate logger (filters do not propagate)."""
    names = {n for n in logging.root.manager.loggerDict if n.startswith("procrastinate")}
    # procrastinate.worker is imported lazily by run_worker_async; pre-create it.
    for name in names | {"procrastinate", "procrastinate.worker", *logger_names}:
        target = logging.getLogger(name)
        if _REDACTOR not in target.filters:
            target.addFilter(_REDACTOR)


def session_options(
    dsn: str, *, statement_timeout: int, lock_timeout: int, idle_in_transaction_timeout: int
) -> str:
    """libpq startup options: survive ``DISCARD ALL`` and override role/database defaults."""
    pinned = (
        "-c TimeZone=UTC"
        f" -c statement_timeout={statement_timeout * 1000}"
        f" -c lock_timeout={lock_timeout * 1000}"
        f" -c idle_in_transaction_session_timeout={idle_in_transaction_timeout * 1000}"
    )
    existing = conninfo_to_dict(dsn).get("options")
    return f"{existing} {pinned}" if existing else pinned


async def _reset_session(connection: AsyncConnection[Any]) -> None:
    """AQ-03: pooled tenant connections return with no session state.

    DISCARD ALL = RESET ALL (search_path, role, GUCs incl. custom ``xt.*``) +
    SET SESSION AUTHORIZATION DEFAULT + DISCARD TEMP/PLANS/SEQUENCES +
    DEALLOCATE ALL + UNLISTEN * + pg_advisory_unlock_all(). Startup options
    (TimeZone/timeouts) remain because they are the session defaults.
    """
    await connection.execute("DISCARD ALL")


class BoundedRetry(BaseRetryStrategy):
    """max_attempts includes the initial execution, including crash recovery."""

    def __init__(self, *, max_attempts: int, retry_seconds: int) -> None:
        if max_attempts < 1 or retry_seconds < 1:
            raise ValueError("max_attempts and retry_seconds must be positive")
        self.max_attempts = max_attempts
        self.retry_seconds = retry_seconds

    def delay(self, attempts: int) -> int:
        return min(3600, self.retry_seconds * (1 << min(attempts, 12)))

    def get_retry_decision(self, *, exception: BaseException, job: Job) -> RetryDecision | None:
        if isinstance(exception, StaleDeliveryError):
            return None
        if job.attempts + 1 >= self.max_attempts:
            return None
        return RetryDecision(retry_in={"seconds": self.delay(job.attempts)})


class QueueRuntime:
    def __init__(
        self,
        *,
        queue_dsn: str,
        tenant_dsn: str,
        handlers: Mapping[str, JobHandler],
        concurrency: int,
        stalled_seconds: int,
        recover_seconds: int,
        max_attempts: int,
        retry_seconds: int,
        statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
        lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
        idle_in_transaction_timeout_seconds: int = DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        if concurrency < 1 or stalled_seconds < 3 or not 0 < recover_seconds < stalled_seconds / 2:
            raise ValueError("invalid concurrency or stalled/recover interval")
        if (
            min(
                statement_timeout_seconds, lock_timeout_seconds, idle_in_transaction_timeout_seconds
            )
            < 1
        ):
            raise ValueError("statement/lock/idle-in-transaction timeouts must be positive")
        if lock_timeout_seconds >= statement_timeout_seconds:
            raise ValueError("lock timeout must be shorter than the statement timeout")
        if any(not name or not callable(handler) for name, handler in handlers.items()):
            raise ValueError("handlers must map task names to callables")
        self.queue_dsn = queue_dsn
        self.tenant_dsn = tenant_dsn
        self.handlers = dict(handlers)
        self.concurrency = concurrency
        self.stalled_seconds = stalled_seconds
        self.recover_seconds = recover_seconds
        self.retry = BoundedRetry(max_attempts=max_attempts, retry_seconds=retry_seconds)
        timeouts = {
            "statement_timeout": statement_timeout_seconds,
            "lock_timeout": lock_timeout_seconds,
            "idle_in_transaction_timeout": idle_in_transaction_timeout_seconds,
        }
        self.queue_options = session_options(queue_dsn, **timeouts)
        self.tenant_options = session_options(tenant_dsn, **timeouts)
        # Counters for operators/metrics exporters; also surfaced by ``xt worker dlq``.
        self.stats: dict[str, int] = {
            "schedules_skipped": 0,
            "schedule_errors": 0,
            "maintenance_errors": 0,
            "recover_errors": 0,
            "stalled_dead_lettered": 0,
        }
        self._reported_schedule_errors: dict[int, str] = {}
        self.app = procrastinate.App(
            connector=procrastinate.PsycopgConnector(
                conninfo=queue_dsn,
                kwargs={"options": self.queue_options},
                min_size=1,
                max_size=concurrency + 2,
                max_lifetime=300,
            ),
            worker_defaults={
                "update_heartbeat_interval": stalled_seconds / 3,
                "stalled_worker_timeout": stalled_seconds,
            },
        )
        self.tenant_pool = AsyncConnectionPool[Any](
            conninfo=tenant_dsn,
            kwargs={
                "autocommit": True,
                "options": self.tenant_options,
                "cursor_factory": GuardedCursor,
                # DISCARD ALL (reset) xóa prepared statement phía server; tắt auto-prepare để
                # cache phía psycopg không trỏ tới `_pg3_N` đã mất sau khi trả kết nối về pool.
                "prepare_threshold": None,
            },
            reset=_reset_session,
            min_size=1,
            max_size=concurrency + 1,
            max_lifetime=300,
            open=False,
        )
        self.job_manager = FencedJobManager(self.app.connector, max_attempts=max_attempts)
        self.app.job_manager = self.job_manager
        self.app.task(name="xt.dispatch", queue="xt", retry=self.retry, pass_context=True)(
            self.dispatch
        )
        self.queue = ProcrastinateJobQueue(
            self.app, set(self.handlers), max_payload_bytes=max_payload_bytes
        )
        install_log_redaction()

    async def _connect(self, dsn: str, options: str, **kwargs: Any) -> AsyncConnection[Any]:
        return await AsyncConnection.connect(dsn, autocommit=True, options=options, **kwargs)

    async def install_schema(self, migrator_dsn: str) -> None:
        """Explicit fresh install only; fail rather than silently mask schema drift."""
        async with (
            await AsyncConnection.connect(migrator_dsn, autocommit=True) as connection,
            connection.transaction(),
        ):
            # SECURITY DEFINER helpers (job_binding, lock_delivery) read FORCE RLS
            # tables as their owner, so the owner must bypass RLS (L8).
            cursor = await connection.execute(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            owner = await cursor.fetchone()
            if owner is None or not owner[0]:
                raise ValueError("queue schema owner must be SUPERUSER or BYPASSRLS")
            await connection.execute("SET LOCAL search_path TO public, pg_catalog")
            await connection.execute(procrastinate.schema.SchemaManager.get_schema())
            await connection.execute(SCHEMA)

    async def _check_role(self, dsn: str, options: str, expected: str) -> tuple[Any, ...]:
        async with await self._connect(dsn, options) as connection:
            row = await (
                await connection.execute(
                    "SELECT current_user, rolsuper, rolbypassrls, current_database(), "
                    "pg_postmaster_start_time() FROM pg_roles "
                    "WHERE rolname=current_user"
                )
            ).fetchone()
            if row is None or row[:3] != (expected, False, False):
                raise ValueError(f"worker requires {expected} NOSUPERUSER NOBYPASSRLS connection")
            if expected == "xt_app":
                cursor = await connection.execute(
                    "SELECT coalesce("
                    "has_function_privilege(to_regprocedure(%s), 'EXECUTE'), false)",
                    (_SET_CONTEXT_SIGNATURE,),
                )
                usable = await cursor.fetchone()
                if usable is None or not usable[0]:
                    raise ValueError(f"worker requires EXECUTE on {_SET_CONTEXT_SIGNATURE}")
            return tuple(row[3:])

    async def dispatch(
        self,
        context: JobContext | None = None,
        *,
        tenant_id: str,
        task_name: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            await self._dispatch(
                context,
                JobRequest(UUID(tenant_id), task_name, idempotency_key, payload),
            )
        except BaseException as exc:
            if context is not None and context.job.id is not None:
                self.job_manager.failure_reasons[context.job.id] = type(exc).__name__
            raise

    async def _dispatch(self, context: JobContext | None, job: JobRequest) -> None:
        handler = self.handlers.get(job.task_name)
        if handler is None:
            raise ValueError(f"No registered job handler: {job.task_name}")
        tenant = str(job.tenant_id)
        async with self.tenant_pool.connection() as connection:
            if connection.info.transaction_status != TransactionStatus.IDLE:
                raise RuntimeError("worker tenant connection must be IDLE before transaction")
            async with connection.transaction():
                # The pool authenticates as xt_app, never as control/migrator.
                await connection.execute("SET LOCAL ROLE xt_app")
                await connection.execute(TENANT_CONTEXT_SQL, (tenant,))
                async with connection.cursor(row_factory=dict_row) as cursor:
                    # AQ-05: schema and role come from the trusted catalog, never payload.
                    await cursor.execute(
                        "SELECT tenant_schema, tenant_role FROM xt_queue.job_binding(%s)",
                        (job.tenant_id,),
                    )
                    binding = await cursor.fetchone()
                    if binding is None:
                        raise ValueError("tenant has no queue job binding")
                    await cursor.execute(
                        "SELECT job_id,completed,cancelled,envelope_hash FROM xt_queue.requests "
                        "WHERE tenant_id=%s AND idempotency_key=%s FOR UPDATE",
                        (job.tenant_id, job.idempotency_key),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise ValueError("job has no matching durable enqueue request")
                    if context is not None and row["job_id"] != context.job.id:
                        raise ValueError("job id does not match durable enqueue request")
                    if row["envelope_hash"] != request_fingerprint(job):
                        raise ValueError("job envelope does not match durable enqueue request")
                    if row["completed"] or row["cancelled"]:
                        return
                    if context is not None:
                        await cursor.execute(
                            "SELECT 1 FROM public.procrastinate_jobs WHERE id=%s "
                            "AND status='doing' AND worker_id=%s AND attempts=%s",
                            (context.job.id, context.job.worker_id, context.job.attempts),
                        )
                        if await cursor.fetchone() is None:
                            return  # The delivery was recovered while waiting for its lock.
                    await cursor.execute(
                        sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(binding["tenant_role"]))
                    )
                    await cursor.execute(
                        sql.SQL("SET LOCAL search_path TO {}, pg_temp").format(
                            sql.Identifier(binding["tenant_schema"])
                        )
                    )
                    # Authorization and SLA state must be rechecked by the trusted
                    # business handler using this transaction before producing effects.
                    with handler_guard(connection):
                        await handler(connection, job)
                    if connection.info.transaction_status != TransactionStatus.INTRANS:
                        raise RuntimeError("job handler must leave its transaction open")
                    # Bookkeeping re-pins role, tenant GUC and a trusted search_path.
                    # L3: right after role/tenant, before any unqualified lookup.
                    await cursor.execute("SET LOCAL ROLE xt_app")
                    await cursor.execute(TENANT_CONTEXT_SQL, (tenant,))
                    await cursor.execute("SET LOCAL search_path TO pg_catalog, public")
                    if context is not None:
                        await cursor.execute(
                            "SELECT xt_queue.lock_delivery(%s,%s,%s) AS owned",
                            (context.job.id, context.job.worker_id, context.job.attempts),
                        )
                        owned = await cursor.fetchone()
                        if owned is None or not owned["owned"]:
                            raise StaleDeliveryError("job delivery lost ownership during handler")
                    await cursor.execute(
                        "UPDATE xt_queue.requests SET completed=true "
                        "WHERE tenant_id=%s AND idempotency_key=%s",
                        (job.tenant_id, job.idempotency_key),
                    )
                    if cursor.rowcount != 1:
                        raise RuntimeError("job completion did not match its durable request")
        if context is not None and context.job.id is not None:
            self.job_manager.failure_reasons.pop(context.job.id, None)

    async def recover_stalled(self) -> int:
        """Recover each stalled delivery in its own transaction (round 2).

        Candidates are read without locks; every job is then re-checked and
        locked ``SKIP LOCKED`` in a separate transaction, so one job that is
        locked, exhausted or failing cannot roll back or block the batch.
        """
        count = 0
        for job_id in await self._stalled_candidates():
            try:
                count += await self._recover_one(job_id)
            except Exception as exc:
                self.stats["recover_errors"] += 1
                logger.error(
                    "stalled job recovery failed job_id=%s (%s)", job_id, type(exc).__name__
                )
        return count

    async def _stalled_candidates(self) -> list[int]:
        async with await self._connect(self.queue_dsn, self.queue_options) as connection:
            candidates = await (
                await connection.execute(
                    "SELECT j.id FROM procrastinate_jobs j "
                    "LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
                    "WHERE j.status='doing' AND j.queue_name='xt' "
                    "AND j.task_name='xt.dispatch' "
                    "AND (w.id IS NULL OR w.last_heartbeat < clock_timestamp() "
                    "- make_interval(secs => %s)) ORDER BY j.id LIMIT %s",
                    (self.stalled_seconds, RECOVER_BATCH),
                )
            ).fetchall()
        return [int(row[0]) for row in candidates]

    async def _recover_one(self, job_id: int) -> int:
        async with (
            await self._connect(
                self.queue_dsn, self.queue_options, row_factory=dict_row
            ) as connection,
            connection.transaction(),
        ):
            cursor = await connection.execute(
                "SELECT j.id,j.attempts FROM procrastinate_jobs j "
                "LEFT JOIN procrastinate_workers w ON w.id=j.worker_id "
                "WHERE j.id=%s AND j.status='doing' "
                "AND (w.id IS NULL OR w.last_heartbeat < clock_timestamp() "
                "- make_interval(secs => %s)) FOR UPDATE OF j SKIP LOCKED",
                (job_id, self.stalled_seconds),
            )
            row = await cursor.fetchone()
            if row is None:
                return 0  # Finished, recovered elsewhere, or locked by its owner.
            if row["attempts"] + 1 >= self.retry.max_attempts:
                # Dead-lettering writes the request row; a live (zombie) handler
                # still holding it is skipped and retried next tick.
                cursor = await connection.execute(
                    "SELECT 1 FROM xt_queue.requests WHERE job_id=%s FOR UPDATE SKIP LOCKED",
                    (job_id,),
                )
                if await cursor.fetchone() is None and await self._request_exists(
                    connection, job_id
                ):
                    return 0
                await connection.execute(
                    "SELECT procrastinate_finish_job_v1(%s,'failed',false)", (job_id,)
                )
                await connection.execute(
                    "SELECT xt_queue.mark_dead(%s,'stalled: attempts exhausted')", (job_id,)
                )
                self.stats["stalled_dead_lettered"] += 1
                logger.warning("stalled job dead-letter job_id=%s", job_id)
            else:
                retry_at = datetime.now(UTC) + timedelta(seconds=self.retry.delay(row["attempts"]))
                await connection.execute(
                    "SELECT procrastinate_retry_job_v2(%s,%s,NULL,NULL,NULL)", (job_id, retry_at)
                )
                logger.info("recovered stalled job_id=%s", job_id)
        return 1

    @staticmethod
    async def _request_exists(connection: AsyncConnection[Any], job_id: int) -> bool:
        cursor = await connection.execute(
            "SELECT 1 FROM xt_queue.requests WHERE job_id=%s", (job_id,)
        )
        return await cursor.fetchone() is not None

    async def _tenant_transaction_connection(self) -> AsyncConnection[Any]:
        return await self._connect(self.tenant_dsn, self.tenant_options, row_factory=dict_row)

    def schedule_backoff_seconds(self, failures: int) -> int:
        """Delay before retrying a schedule that failed ``failures`` times in a row."""
        return min(SCHEDULE_BACKOFF_CAP_SECONDS, self.recover_seconds * (1 << min(failures, 20)))

    async def _record_schedule_error(
        self, schedule_id: int, tenant_id: UUID, error: str, *, failures: int | None = None
    ) -> None:
        """Surface a schedule failure on its row (``xt worker dlq``) and log it once.

        With ``failures`` (a materialization error, not a missing handler) the
        schedule also backs off: ``retry_at`` moves forward exponentially, capped,
        so a schedule that keeps failing neither hot-loops nor fills the batch.
        """
        if self._reported_schedule_errors.get(schedule_id) != error:
            self._reported_schedule_errors[schedule_id] = error
            logger.error(
                "periodic schedule skipped schedule_id=%s tenant_id=%s error=%s",
                schedule_id,
                tenant_id,
                error,
            )
        try:
            async with (
                await self._tenant_transaction_connection() as connection,
                connection.transaction(),
            ):
                await connection.execute("SET LOCAL ROLE xt_app")
                await connection.execute(TENANT_CONTEXT_SQL, (str(tenant_id),))
                if failures is None:
                    await connection.execute(
                        "UPDATE xt_queue.schedules SET last_error=%s, last_error_at=now() "
                        "WHERE id=%s AND last_error IS DISTINCT FROM %s",
                        (error, schedule_id, error),
                    )
                else:
                    await connection.execute(
                        "UPDATE xt_queue.schedules SET last_error=%s, last_error_at=now(), "
                        "failures=%s, retry_at=now() + make_interval(secs => %s) WHERE id=%s",
                        (
                            error,
                            failures + 1,
                            self.schedule_backoff_seconds(failures),
                            schedule_id,
                        ),
                    )
        except Exception as exc:  # The log line above is the remaining signal.
            logger.warning(
                "could not record schedule error schedule_id=%s (%s)",
                schedule_id,
                type(exc).__name__,
            )

    async def _due_schedules(self) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        """(due, unhandled) rows read by the control login (identifiers only)."""
        handled = sorted(self.handlers)
        async with await self._connect(self.queue_dsn, self.queue_options) as control:
            # Fairness: schedules in backoff are not due; healthy ones sort first;
            # each tenant contributes at most SCHEDULES_PER_TENANT_PER_TICK rows.
            due = await (
                await control.execute(
                    "SELECT id,tenant_id,failures FROM ("
                    "SELECT id,tenant_id,failures,next_at,row_number() OVER ("
                    "PARTITION BY tenant_id ORDER BY failures, next_at, id) AS rank "
                    "FROM xt_queue.schedules WHERE active AND next_at <= now() "
                    "AND (retry_at IS NULL OR retry_at <= now()) AND task_name = ANY(%s)"
                    ") s WHERE rank <= %s ORDER BY failures, rank, next_at, id LIMIT %s",
                    (handled, SCHEDULES_PER_TENANT_PER_TICK, SCHEDULES_PER_TICK),
                )
            ).fetchall()
            # AQ-02: a schedule this worker cannot run (version skew, removed handler)
            # stays due for a worker that can, and is surfaced instead of crashing.
            # Only overdue by SCHEDULE_ERROR_GRACE_TICKS: during a rolling deploy a
            # worker that has the handler normally fires it first.
            unhandled = await (
                await control.execute(
                    "SELECT id,tenant_id,task_name,last_error FROM xt_queue.schedules "
                    "WHERE active AND NOT task_name = ANY(%s) "
                    "AND next_at < now() - make_interval(secs => %s) "
                    "ORDER BY next_at LIMIT %s",
                    (
                        handled,
                        SCHEDULE_ERROR_GRACE_TICKS * self.recover_seconds,
                        SCHEDULES_PER_TICK,
                    ),
                )
            ).fetchall()
        return list(due), list(unhandled)

    async def dispatch_periodic(self) -> int:
        # Control reads identifiers only; each schedule advances in its own tenant tx.
        due, unhandled = await self._due_schedules()
        for schedule_id, tenant_id, task_name, last_error in unhandled:
            self.stats["schedules_skipped"] += 1
            error = f"no registered job handler: {task_name}"[:200]
            if last_error != error or self._reported_schedule_errors.get(schedule_id) != error:
                await self._record_schedule_error(schedule_id, tenant_id, error)
        count = 0
        for schedule_id, tenant_id, failures in due:
            try:
                count += await self._fire_schedule(schedule_id, tenant_id)
            except Exception as exc:
                self.stats["schedule_errors"] += 1
                await self._record_schedule_error(
                    schedule_id, tenant_id, type(exc).__name__, failures=int(failures)
                )
        return count

    async def _fire_schedule(self, schedule_id: int, tenant_id: UUID) -> int:
        async with (
            await self._tenant_transaction_connection() as connection,
            connection.transaction(),
        ):
            await connection.execute("SET LOCAL ROLE xt_app")
            await connection.execute(TENANT_CONTEXT_SQL, (str(tenant_id),))
            cursor = await connection.execute(
                "SELECT * FROM xt_queue.schedules WHERE id=%s "
                "AND active AND next_at<=now() AND (retry_at IS NULL OR retry_at<=now()) "
                "FOR UPDATE SKIP LOCKED",
                (schedule_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return 0
            # AQ-01: psycopg returns timestamptz in the session TimeZone; croniter
            # evaluates the UTC cron in the tzinfo of its start, so normalize first.
            occurrence = row["next_at"].astimezone(UTC)
            job = JobRequest(
                tenant_id,
                row["task_name"],
                f"periodic:{schedule_id}:{int(occurrence.timestamp())}",
                row["payload"],
            )
            await self.queue._enqueue(
                connection,
                job,
                occurrence,
                occurrence_schedule_id=schedule_id,
            )
            await connection.execute(
                "UPDATE xt_queue.schedules SET next_at=%s, last_error=NULL, last_error_at=NULL, "
                "failures=0, retry_at=NULL WHERE id=%s",
                (croniter(row["cron_utc"], occurrence).get_next(datetime), schedule_id),
            )
        self._reported_schedule_errors.pop(schedule_id, None)
        return 1

    async def maintenance(self) -> None:
        # L5: one failing step (e.g. recovery) must not starve the other.
        for step in (self.recover_stalled, self.dispatch_periodic):
            try:
                await step()
            except Exception as exc:
                self.stats["maintenance_errors"] += 1
                logger.error("worker maintenance %s failed (%s)", step.__name__, type(exc).__name__)

    async def _maintenance_loop(self) -> None:
        # AQ-02: transient maintenance failures are logged and retried; recovery and
        # schedules of every tenant must not stop because one tick failed.
        while True:
            try:
                await self.maintenance()
            except Exception as exc:
                self.stats["maintenance_errors"] += 1
                logger.error("worker maintenance tick failed (%s)", type(exc).__name__)
            await asyncio.sleep(self.recover_seconds)

    async def run(self, name: str | None = None) -> None:
        control_database = await self._check_role(self.queue_dsn, self.queue_options, "xt_control")
        tenant_database = await self._check_role(self.tenant_dsn, self.tenant_options, "xt_app")
        if control_database != tenant_database:
            raise ValueError(
                "queue and tenant connections must address the same PostgreSQL database"
            )
        worker_name = name or "xt-worker"
        install_log_redaction(f"procrastinate.worker.{worker_name}")
        async with self.app.open_async(), self.tenant_pool:
            maintenance = asyncio.create_task(self._maintenance_loop())
            worker = asyncio.create_task(
                self.app.run_worker_async(
                    name=worker_name, concurrency=self.concurrency, queues=["xt"], wait=True
                )
            )
            try:
                done, _ = await asyncio.wait(
                    (maintenance, worker), return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                maintenance.cancel()
                worker.cancel()
                await asyncio.gather(maintenance, worker, return_exceptions=True)


def create_runtime(
    *,
    queue_dsn: str,
    tenant_dsn: str,
    handlers: Mapping[str, JobHandler],
    concurrency: int,
    stalled_seconds: int,
    recover_seconds: int,
    max_attempts: int,
    retry_seconds: int,
    statement_timeout_seconds: int = DEFAULT_STATEMENT_TIMEOUT_SECONDS,
    lock_timeout_seconds: int = DEFAULT_LOCK_TIMEOUT_SECONDS,
    idle_in_transaction_timeout_seconds: int = DEFAULT_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> QueueRuntime:
    return QueueRuntime(
        queue_dsn=queue_dsn,
        tenant_dsn=tenant_dsn,
        handlers=handlers,
        concurrency=concurrency,
        stalled_seconds=stalled_seconds,
        recover_seconds=recover_seconds,
        max_attempts=max_attempts,
        retry_seconds=retry_seconds,
        statement_timeout_seconds=statement_timeout_seconds,
        lock_timeout_seconds=lock_timeout_seconds,
        idle_in_transaction_timeout_seconds=idle_in_transaction_timeout_seconds,
        max_payload_bytes=max_payload_bytes,
    )
