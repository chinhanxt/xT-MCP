# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Queue contracts that do not need a database."""

from datetime import UTC, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from procrastinate.jobs import Job

from xt.adapters.queue.backend import request_fingerprint
from xt.adapters.queue.runtime import BoundedRetry, create_runtime
from xt.application.jobs import JobRequest


def test_fingerprint_canonical_and_tenant_scoped() -> None:
    tenant = uuid4()
    a = JobRequest(tenant, "send", "k", {"b": 2, "a": {"c": 1}})
    b = JobRequest(tenant, "send", "k", {"a": {"c": 1}, "b": 2})
    assert request_fingerprint(a) == request_fingerprint(b)
    assert request_fingerprint(a) != request_fingerprint(
        JobRequest(uuid4(), "send", "k", a.payload)
    )
    assert request_fingerprint(a) != request_fingerprint(JobRequest(tenant, "send", "k", {"b": 3}))
    when = datetime(2026, 10, 19, 1, tzinfo=UTC)
    local = when.astimezone(timezone(timedelta(hours=7)))
    assert request_fingerprint(a, when) == request_fingerprint(a, local)
    assert request_fingerprint(a, when) != request_fingerprint(a)


def test_total_attempts_and_backoff() -> None:
    strategy = BoundedRetry(max_attempts=3, retry_seconds=2)

    def job(attempts: int) -> Job:
        return Job(
            queue="xt", lock=None, queueing_lock=None, task_name="xt.dispatch", attempts=attempts
        )

    assert strategy.delay(0) == 2
    assert strategy.delay(1) == 4
    assert strategy.delay(1000000) == 3600
    assert strategy.get_retry_decision(exception=RuntimeError(), job=job(0)) is not None
    assert strategy.get_retry_decision(exception=RuntimeError(), job=job(1)) is not None
    assert strategy.get_retry_decision(exception=RuntimeError(), job=job(2)) is None
    once = BoundedRetry(max_attempts=1, retry_seconds=2)
    assert once.get_retry_decision(exception=RuntimeError(), job=job(0)) is None


def test_enqueue_rejects_foreign_or_closed_transaction() -> None:
    import asyncio

    runtime = create_runtime(
        queue_dsn="postgresql://xt_control:p@localhost/db",
        tenant_dsn="postgresql://xt_app:p@localhost/db",
        handlers={},
        concurrency=1,
        stalled_seconds=6,
        recover_seconds=1,
        max_attempts=3,
        retry_seconds=1,
    )
    with pytest.raises(TypeError, match="AsyncConnection"):
        asyncio.run(runtime.queue.enqueue(object(), JobRequest(uuid4(), "send", "k", {})))


def _runtime(**settings: object) -> object:
    options: dict[str, object] = {
        "queue_dsn": "postgresql://xt_control:p@localhost/db?options=-c%20work_mem%3D8MB",
        "tenant_dsn": "postgresql://xt_app:p@localhost/db",
        "handlers": {},
        "concurrency": 1,
        "stalled_seconds": 6,
        "recover_seconds": 1,
        "max_attempts": 3,
        "retry_seconds": 1,
    }
    options.update(settings)
    return create_runtime(**options)  # type: ignore[arg-type]


def test_session_options_pin_utc_and_timeouts_and_keep_dsn_options() -> None:
    runtime = _runtime(statement_timeout_seconds=7, lock_timeout_seconds=6)
    assert runtime.queue_options == (  # type: ignore[attr-defined]
        "-c work_mem=8MB -c TimeZone=UTC -c statement_timeout=7000 -c lock_timeout=6000 "
        "-c idle_in_transaction_session_timeout=60000"
    )
    assert "-c lock_timeout=10000" in _runtime().tenant_options  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="shorter than the statement timeout"):
        _runtime(statement_timeout_seconds=10, lock_timeout_seconds=10)
    assert runtime.tenant_options.startswith("-c TimeZone=UTC")  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="timeouts must be positive"):
        _runtime(idle_in_transaction_timeout_seconds=0)
    with pytest.raises(ValueError, match="max_payload_bytes"):
        _runtime(max_payload_bytes=0)


def test_payload_cap_is_checked_before_any_database_access() -> None:
    import asyncio

    from psycopg import AsyncConnection

    runtime = _runtime(handlers={"send": lambda c, j: None}, max_payload_bytes=32)
    big = JobRequest(uuid4(), "send", "k", {"text": "ư" * 20})
    fake = AsyncConnection.__new__(AsyncConnection)
    with pytest.raises(ValueError, match="limit is 32 bytes"):
        asyncio.run(runtime.queue.enqueue(fake, big))  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("text", "ends"),
    [
        ("COMMIT", True),
        ("  commit work", True),
        ("SELECT 1; COMMIT", True),
        ("END", True),
        ("ABORT", True),
        ("ROLLBACK", True),
        ("ROLLBACK AND CHAIN", True),
        ("COMMIT PREPARED 'x'", True),
        ("PREPARE TRANSACTION 'x'", True),
        ("-- note\nCOMMIT", True),
        ("ROLLBACK TO SAVEPOINT s", False),
        ("ROLLBACK TO s", False),
        ("RELEASE SAVEPOINT s", False),
        ("SELECT 'COMMIT; END'", False),
        ('SELECT "commit"', False),
        ("SELECT $$; COMMIT$$", False),
        ("SELECT $q$; COMMIT$q$", False),
        ("/* COMMIT */ SELECT 1", False),
        ("UPDATE t SET endpoint=1", False),
        ("DO $$BEGIN PERFORM 1; END$$", False),
        ("SELECT e'it''s; COMMIT'", False),
        ("SELECT E'\\'; COMMIT'", False),
        ("SELECT name'x'; COMMIT", True),
        ("SELECT a$$b; COMMIT", True),
        ("SELECT $1; COMMIT", True),
        ("SELECT 1 AS a$b$; COMMIT; SELECT 1 AS c$b$", True),
        ("SELECT 1 AS a$$; COMMIT; SELECT 1 AS c$$", True),
    ],
)
def test_transaction_control_detection(text: str, ends: bool) -> None:
    from xt.adapters.queue.runtime import ends_transaction

    assert ends_transaction(text) is ends


def test_job_payload_is_redacted_from_procrastinate_log_records() -> None:
    import logging
    import sys

    from xt.adapters.queue.runtime import RedactJobPayload, _redact_payload

    call = (
        "xt.dispatch[19](payload={'note': 'PAYLOAD-MARKER', 'n': [1, {'x': \")}'\"}]}, "
        "task_name='t', tenant_id='u', idempotency_key='k')"
    )
    assert _redact_payload(f"Job {call} ended") == "Job xt.dispatch[19]([redacted]) ended"
    try:
        raise RuntimeError("DETAIL: Key (email)=(PAYLOAD-MARKER@x) already exists")
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "procrastinate.worker.w", logging.ERROR, __file__, 1, "Job %s failed", (call,), exc_info
    )
    record.job = {"task_kwargs": {"payload": {"note": "PAYLOAD-MARKER"}}, "call_string": call}
    record.jobs = [dict(record.job)]
    assert RedactJobPayload().filter(record)
    formatted = logging.Formatter().format(record)
    rendered = f"{formatted} {record.job} {record.jobs}"
    assert "PAYLOAD-MARKER" not in rendered
    assert formatted == "Job xt.dispatch[19]([redacted]) failed [RuntimeError]"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # Unbalanced quote/bracket characters inside the payload repr.
        (
            "Job xt.dispatch[3](payload={'n': \"it's ( [ {\"}, idempotency_key='k') ended",
            "Job xt.dispatch[3]([redacted]) ended",
        ),
        ("xt.dispatch[3](payload={'n': 'it\\'s'})", "xt.dispatch[3]([redacted])"),
        # Truncated call string: everything after the open paren is dropped.
        ("Loaded xt.dispatch[4](payload={'secret': 'MARK", "Loaded xt.dispatch[4]([redacted])"),
        ("Jobs xt.dispatch[](a='x') and xt.dispatch[2](b='y')", "Jobs xt.dispatch[]([redacted])"),
        ("no call string here", "no call string here"),
    ],
)
def test_redaction_regex_covers_the_whole_argument_list(text: str, expected: str) -> None:
    from xt.adapters.queue.runtime import _redact_payload

    assert _redact_payload(text) == expected


def test_redaction_keeps_only_exception_class_of_chained_psycopg_errors() -> None:
    import logging
    import sys

    from xt.adapters.queue.runtime import RedactJobPayload

    try:
        try:
            raise ValueError("handler saw PAYLOAD-MARKER")
        except ValueError as inner:
            raise LookupError("DETAIL:  Key (email)=(PAYLOAD-MARKER@x)") from inner
    except LookupError:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "procrastinate.worker", logging.ERROR, __file__, 1, "Job failed", None, exc_info
    )
    record.stack_info = "PAYLOAD-MARKER stack"
    assert RedactJobPayload().filter(record)
    assert logging.Formatter().format(record) == "Job failed [LookupError]"
    assert record.exc_info is None and record.exc_text is None


def test_installed_redaction_covers_lazily_imported_worker_logger(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    from xt.adapters.queue.runtime import install_log_redaction

    install_log_redaction("procrastinate.worker.unit")
    with caplog.at_level(logging.DEBUG):
        for name in ("procrastinate.worker", "procrastinate.worker.unit"):
            logging.getLogger(name).info("Job xt.dispatch[1](payload={'secret': 's3'}) ended")
    assert caplog.messages == ["Job xt.dispatch[1]([redacted]) ended"] * 2


@pytest.mark.parametrize(
    ("text", "blocked"),
    [
        ("SET ROLE xt_app", True),
        ("set local role xt_job_t2", True),
        ("SET SESSION ROLE xt_app", True),
        ("RESET ROLE", True),
        ("RESET ALL", True),
        ("SET SESSION AUTHORIZATION xt_app", True),
        ("SET LOCAL xt.tenant_id = 'x'", True),
        ("SELECT set_config('xt.tenant_id', '1', true)", True),
        ("SELECT set_config( 'role', 'xt_app', true)", True),
        ("SELECT set_config(%s, %s, true)", True),
        ("SELECT 1; SET ROLE xt_app", True),
        ("SET LOCAL ROLE xt_workflow", False),
        ('SET LOCAL ROLE "xt_workflow"', False),
        ("SET LOCAL statement_timeout = 1000", False),
        ("SELECT set_config('search_path', 'xt_t1', true)", True),
        ("SELECT set_config('Search_Path', 'xt_t1', false)", True),
        ("SELECT set_config(E'xt.tenant_id', '1', true)", True),
        ("SELECT set_config($$xt.tenant_id$$, '1', true)", True),
        ("SELECT \"set_config\"('xt.tenant_id', '1', true)", True),
        ("SELECT set_config(lower('XT.TENANT_ID'), '1', true)", True),
        ("SELECT xt.set_context(%s, NULL, '{}', '{}', '{}', NULL, NULL, NULL)", True),
        ("SELECT pg_catalog.set_config('statement_timeout', '5s', true)", False),
        ("SELECT current_setting('xt.tenant_id')", False),
        ("SELECT 'SET ROLE xt_app'", False),
    ],
)
def test_session_context_detection(text: str, blocked: bool) -> None:
    from xt.adapters.queue.runtime import changes_session_context

    assert changes_session_context(text) is blocked


def test_maintenance_steps_fail_independently() -> None:
    import asyncio

    runtime = _runtime()
    calls: list[str] = []

    async def recover() -> int:
        calls.append("recover")
        raise RuntimeError("recovery broken")

    async def periodic() -> int:
        calls.append("periodic")
        return 0

    runtime.recover_stalled = recover  # type: ignore[attr-defined]
    runtime.dispatch_periodic = periodic  # type: ignore[attr-defined]
    asyncio.run(runtime.maintenance())  # type: ignore[attr-defined]
    assert calls == ["recover", "periodic"]
    assert runtime.stats["maintenance_errors"] == 1  # type: ignore[attr-defined]


def test_maintenance_loop_survives_failing_ticks() -> None:
    import asyncio

    runtime = _runtime()
    calls = 0

    async def flaky() -> None:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise RuntimeError("database unavailable")
        raise asyncio.CancelledError

    runtime.maintenance = flaky  # type: ignore[attr-defined]
    runtime.recover_seconds = 0  # type: ignore[attr-defined]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime._maintenance_loop())  # type: ignore[attr-defined]
    assert calls == 3
    assert runtime.stats["maintenance_errors"] == 2  # type: ignore[attr-defined]


def test_stalled_recovery_is_per_job_so_one_failure_does_not_stop_the_batch() -> None:
    import asyncio

    runtime = _runtime()
    recovered: list[int] = []

    async def candidates() -> list[int]:
        return [1, 2, 3]

    async def recover_one(job_id: int) -> int:
        if job_id == 2:
            raise RuntimeError("request row locked / lock timeout")
        recovered.append(job_id)
        return 1

    runtime._stalled_candidates = candidates  # type: ignore[attr-defined]
    runtime._recover_one = recover_one  # type: ignore[attr-defined]
    assert asyncio.run(runtime.recover_stalled()) == 2  # type: ignore[attr-defined]
    assert recovered == [1, 3]
    assert runtime.stats["recover_errors"] == 1  # type: ignore[attr-defined]


def test_failing_schedule_backs_off_with_cap_and_others_still_fire() -> None:
    import asyncio
    from uuid import UUID

    from xt.adapters.queue.runtime import SCHEDULE_BACKOFF_CAP_SECONDS

    runtime = _runtime(recover_seconds=2, stalled_seconds=6)
    assert [runtime.schedule_backoff_seconds(n) for n in range(4)] == [2, 4, 8, 16]  # type: ignore[attr-defined]
    assert runtime.schedule_backoff_seconds(40) == SCHEDULE_BACKOFF_CAP_SECONDS  # type: ignore[attr-defined]
    tenant = UUID(int=1)
    fired: list[int] = []
    errors: list[tuple[int, str, int | None]] = []

    async def due() -> tuple[list[tuple[object, ...]], list[tuple[object, ...]]]:
        return [(1, tenant, 0), (2, tenant, 5), (3, tenant, 0)], []

    async def fire(schedule_id: int, tenant_id: UUID) -> int:
        if schedule_id == 2:
            raise ValueError("bad payload")
        fired.append(schedule_id)
        return 1

    async def record(
        schedule_id: int, tenant_id: UUID, error: str, *, failures: int | None = None
    ) -> None:
        errors.append((schedule_id, error, failures))

    runtime._due_schedules = due  # type: ignore[attr-defined]
    runtime._fire_schedule = fire  # type: ignore[attr-defined]
    runtime._record_schedule_error = record  # type: ignore[attr-defined]
    assert asyncio.run(runtime.dispatch_periodic()) == 2  # type: ignore[attr-defined]
    assert fired == [1, 3]
    assert errors == [(2, "ValueError", 5)]


def test_due_schedule_query_is_fair_and_skips_backoff() -> None:
    import inspect

    from xt.adapters.queue.runtime import QueueRuntime

    source = inspect.getsource(QueueRuntime._due_schedules)
    assert "PARTITION BY tenant_id ORDER BY failures" in source
    assert "retry_at IS NULL OR retry_at <= now()" in source
    assert "SCHEDULES_PER_TENANT_PER_TICK" in source


@pytest.mark.parametrize(
    ("argv", "command"),
    [
        (["list"], "list"),
        (["show", "7"], "show"),
        (["inspect", "7"], "inspect"),
        (["requeue", "7", "8"], "requeue"),
        (["purge", "7"], "purge"),
        (["purge", "--older-than-days", "30"], "purge"),
    ],
)
def test_dlq_cli_commands_parse(argv: list[str], command: str) -> None:
    from xt.adapters.queue.dlq import build_parser

    assert build_parser().parse_args(argv).command == command


def test_dlq_purge_requires_a_target(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from xt.adapters.queue.dlq import main

    monkeypatch.setenv("XT_QUEUE_DATABASE_URL", "postgresql://xt_control:p@localhost/db")
    assert main(["purge"]) == 2
    assert "purge needs job ids" in capsys.readouterr().err


def test_xt_worker_dlq_is_wired_to_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    import xt.adapters.queue.dlq as dlq
    from xt.entrypoints.worker import main

    seen: list[list[str]] = []
    monkeypatch.setattr(dlq, "main", lambda argv: seen.append(list(argv)) or 0)
    assert main(["dlq", "requeue", "5"]) == 0
    assert seen == [["requeue", "5"]]
