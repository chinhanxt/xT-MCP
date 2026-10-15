# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Queue acceptance tests against real PostgreSQL roles, RLS, and worker processes."""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import AsyncConnection, sql

from tests.integration.worker_handlers import handlers
from xt.adapters.db.tenancy import bootstrap
from xt.application.jobs import JobRequest

if TYPE_CHECKING:
    from xt.adapters.queue.runtime import QueueRuntime

# Same statement as xt.adapters.queue.runtime.TENANT_CONTEXT_SQL (imported lazily there).
TENANT_CONTEXT_SQL = (
    "SELECT xt.set_context(%s::uuid, NULL, '{}'::text[], '{}'::uuid[], '{}'::uuid[],"
    " NULL, NULL, NULL)"
)

pytestmark = pytest.mark.integration

TENANT_A = UUID("10000000-0000-0000-0000-000000000001")
TENANT_B = UUID("20000000-0000-0000-0000-000000000002")
# Trusted tenant -> (schema, NOLOGIN job role) bindings, provisioned like production.
TENANT_BINDINGS = ((TENANT_A, "xt_t1", "xt_job_t1"), (TENANT_B, "xt_t2", "xt_job_t2"))
IMAGE = "postgres:17-alpine"
PORT = 19800


@dataclass(frozen=True)
class WorkerDatabase:
    password: str = field(repr=False)

    def dsn(self, role: str) -> str:
        return f"postgresql://{role}:{self.password}@127.0.0.1:{PORT}/xt_worker_it"

    def runtime(self, **settings: Any) -> QueueRuntime:
        from xt.adapters.queue.runtime import create_runtime

        options: dict[str, Any] = {
            "queue_dsn": self.dsn("xt_control"),
            "tenant_dsn": self.dsn("xt_app"),
            "handlers": handlers(),
            "concurrency": 1,
            "stalled_seconds": 6,
            "recover_seconds": 1,
            "max_attempts": 2,
            "retry_seconds": 1,
        }
        options.update(settings)
        return create_runtime(**options)

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        with psycopg.connect(self.dsn("postgres"), autocommit=True) as connection:
            cursor = connection.execute(query, parameters)
            return list(cursor.fetchall()) if cursor.description is not None else []

    @asynccontextmanager
    async def tenant_transaction(self, tenant_id: UUID) -> AsyncIterator[AsyncConnection[Any]]:
        async with (
            await AsyncConnection.connect(self.dsn("xt_app"), autocommit=True) as connection,
            connection.transaction(),
        ):
            await connection.execute(TENANT_CONTEXT_SQL, (str(tenant_id),))
            yield connection


@pytest.fixture(scope="module")
def worker_database() -> Iterator[WorkerDatabase]:
    if shutil.which("docker") is None:
        pytest.fail("queue integration tests require Docker and the local postgres:17-alpine image")
    subprocess.run(["df", "-h", "/"], check=True, capture_output=True)
    inspected = subprocess.run(
        ["docker", "image", "inspect", IMAGE], capture_output=True, check=False
    )
    if inspected.returncode:
        pytest.fail("postgres:17-alpine image is missing; integration tests never pull images")
    database = WorkerDatabase(secrets.token_hex(18))
    name = f"xt-it-worker-{secrets.token_hex(4)}"
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--pull",
                "never",
                "-d",
                "--name",
                name,
                "-p",
                f"127.0.0.1:{PORT}:5432",
                "-e",
                f"POSTGRES_PASSWORD={database.password}",
                "-e",
                "POSTGRES_DB=xt_worker_it",
                IMAGE,
            ],
            check=True,
            capture_output=True,
        )
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(database.dsn("postgres"), connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("PostgreSQL did not become ready in 30 seconds")
                time.sleep(0.1)
        _install_business_schema(database)
        asyncio.run(database.runtime().install_schema(database.dsn("postgres")))
        bind_tenants(database)
        yield database
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, check=False)


def _install_business_schema(database: WorkerDatabase) -> None:
    with psycopg.connect(database.dsn("postgres"), autocommit=True) as connection:
        # The real model-engine bootstrap: xt_app/xt_workflow roles and the supported
        # tenant context function xt.set_context (AUD-13) that the worker calls.
        bootstrap(
            connection,
            migrator_password=database.password,
            app_password=database.password,
            pepper=secrets.token_bytes(32),
        )
        connection.execute(
            sql.SQL("CREATE ROLE xt_control LOGIN NOBYPASSRLS PASSWORD {}").format(
                sql.Literal(database.password)
            )
        )
        connection.execute("GRANT CONNECT ON DATABASE xt_worker_it TO xt_control")
        # Group of all tenant job roles: tenant policies apply TO xt_app, xt_job.
        connection.execute("CREATE ROLE xt_job NOLOGIN NOBYPASSRLS")
        # Handlers read the model-engine context like any tenant code (provisioning grant).
        connection.execute("GRANT USAGE ON SCHEMA xt TO xt_job")
        connection.execute("GRANT EXECUTE ON FUNCTION xt.cur(), xt.tenant() TO xt_job")
        connection.execute(
            "CREATE TABLE public.worker_test_gates("
            "id integer PRIMARY KEY, released boolean NOT NULL)"
        )
        connection.execute("GRANT USAGE ON SCHEMA public TO xt_job")
        connection.execute("GRANT SELECT ON public.worker_test_gates TO xt_app, xt_job")
        for _, schema, job_role in TENANT_BINDINGS:
            identifier = sql.Identifier(schema)
            role = sql.Identifier(job_role)
            connection.execute(
                sql.SQL("CREATE ROLE {} NOLOGIN NOBYPASSRLS IN ROLE xt_job").format(role)
            )
            connection.execute(
                sql.SQL("GRANT {} TO xt_app WITH INHERIT FALSE, SET TRUE").format(role)
            )
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))
            connection.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO xt_app, {}").format(identifier, role)
            )
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {}.requests (id text PRIMARY KEY, tenant_id uuid NOT NULL, "
                    "status text NOT NULL DEFAULT 'pending')"
                ).format(identifier)
            )
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {}.effects (job_key text PRIMARY KEY, tenant_id uuid NOT NULL, "
                    "observed_tenant uuid NOT NULL, observed_role text NOT NULL)"
                ).format(identifier)
            )
            for table in ("requests", "effects"):
                qualified = sql.Identifier(schema, table)
                connection.execute(
                    sql.SQL("ALTER TABLE {} ENABLE ROW LEVEL SECURITY").format(qualified)
                )
                connection.execute(
                    sql.SQL("ALTER TABLE {} FORCE ROW LEVEL SECURITY").format(qualified)
                )
                connection.execute(
                    sql.SQL(
                        "CREATE POLICY tenant ON {} TO xt_app, xt_job "
                        "USING (tenant_id = nullif(current_setting('xt.tenant_id',true),'')::uuid) "
                        "WITH CHECK (tenant_id = "
                        "nullif(current_setting('xt.tenant_id',true),'')::uuid)"
                    ).format(qualified)
                )
                connection.execute(
                    sql.SQL("GRANT SELECT,INSERT,UPDATE,DELETE ON {} TO xt_app, {}").format(
                        qualified, role
                    )
                )


def bind_tenants(database: WorkerDatabase) -> None:
    """Provisioning step after the queue schema exists (operator/migrator role)."""
    for tenant_id, schema, job_role in TENANT_BINDINGS:
        database.execute(
            "INSERT INTO xt_queue.tenant_bindings(tenant_id,schema_name,job_role) "
            "VALUES (%s,%s,%s)",
            (tenant_id, schema, job_role),
        )


@contextmanager
def _worker(database: WorkerDatabase) -> Iterator[subprocess.Popen[bytes]]:
    environment = {
        **os.environ,
        "XT_QUEUE_DATABASE_URL": database.dsn("xt_control"),
        "XT_WORKER_DATABASE_URL": database.dsn("xt_app"),
        "XT_WORKER_CONCURRENCY": "1",
        "XT_WORKER_STALLED_SECONDS": "6",
        "XT_WORKER_RECOVER_SECONDS": "1",
        "XT_WORKER_MAX_ATTEMPTS": "2",
        "XT_WORKER_RETRY_SECONDS": "1",
        "XT_WORKER_HANDLERS": "tests.integration.worker_handlers:handlers",
        "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
    }
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [str(Path(sys.executable).with_name("xt")), "worker", "--name", f"it-{uuid4().hex}"],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            stdout=output,
            stderr=output,
        )
        try:
            yield process
        except BaseException:
            output.seek(0)
            # Exceptions contain logs only when the assertion fails; DSNs are
            # redacted even if an upstream driver included them in a traceback.
            logs = output.read().decode(errors="replace").replace(database.password, "[redacted]")
            print(logs)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _wait_for(
    database: WorkerDatabase,
    query: str,
    parameters: tuple[Any, ...] = (),
    *,
    worker: subprocess.Popen[bytes] | None = None,
    timeout: float = 25,
) -> list[tuple[Any, ...]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = database.execute(query, parameters)
        if rows:
            return rows
        if worker is not None and worker.poll() is not None:
            pytest.fail(f"worker exited before business outcome (status {worker.returncode})")
        time.sleep(0.1)
    pytest.fail("timed out waiting for the real worker/database outcome")


def _request(
    database: WorkerDatabase,
    tenant_id: UUID,
    schema: str,
    task: str = "business.finish",
    **payload: Any,
) -> JobRequest:
    request_id = uuid4().hex
    database.execute(
        f"INSERT INTO {schema}.requests(id,tenant_id) VALUES (%s,%s)", (request_id, tenant_id)
    )
    return JobRequest(
        tenant_id, task, request_id, {"schema": schema, "request_id": request_id, **payload}
    )


async def _enqueue(database: WorkerDatabase, job: JobRequest) -> int:
    async with database.tenant_transaction(job.tenant_id) as connection:
        return await database.runtime().queue.enqueue(connection, job)


def test_business_request_and_queue_job_commit_or_roll_back_together(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    committed = uuid4().hex
    rolled_back = uuid4().hex

    async def scenario() -> None:
        queue = database.runtime().queue
        async with database.tenant_transaction(TENANT_A) as connection:
            await connection.execute(
                "INSERT INTO xt_t1.requests(id,tenant_id) VALUES (%s,%s)", (committed, TENANT_A)
            )
            await queue.enqueue(
                connection,
                JobRequest(
                    TENANT_A,
                    "business.finish",
                    committed,
                    {"schema": "xt_t1", "request_id": committed},
                ),
            )
        with pytest.raises(RuntimeError, match="roll back"):
            async with database.tenant_transaction(TENANT_A) as connection:
                await connection.execute(
                    "INSERT INTO xt_t1.requests(id,tenant_id) VALUES (%s,%s)",
                    (rolled_back, TENANT_A),
                )
                await queue.enqueue(
                    connection,
                    JobRequest(
                        TENANT_A,
                        "business.finish",
                        rolled_back,
                        {"schema": "xt_t1", "request_id": rolled_back},
                    ),
                )
                raise RuntimeError("roll back the entire business operation")

    asyncio.run(scenario())
    assert database.execute("SELECT id FROM xt_t1.requests WHERE id = %s", (rolled_back,)) == []
    assert (
        database.execute(
            "SELECT job_id FROM xt_queue.requests WHERE idempotency_key = %s", (rolled_back,)
        )
        == []
    )
    assert (
        database.execute(
            "SELECT id FROM public.procrastinate_jobs WHERE args->>'idempotency_key'=%s",
            (rolled_back,),
        )
        == []
    )
    with _worker(database) as worker:
        rows = _wait_for(
            database,
            "SELECT observed_tenant,observed_role FROM xt_t1.effects WHERE job_key = %s",
            (committed,),
            worker=worker,
        )
    assert rows == [(TENANT_A, "xt_job_t1")]


def test_idempotency_survives_completion_and_rejects_changed_payload(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    first = asyncio.run(_enqueue(database, job))
    assert asyncio.run(_enqueue(database, job)) == first
    other_request = _request(database, TENANT_B, "xt_t2")
    same_key_other_tenant = JobRequest(
        TENANT_B, job.task_name, job.idempotency_key, other_request.payload
    )
    other_id = asyncio.run(_enqueue(database, same_key_other_tenant))
    assert other_id != first
    changed = JobRequest(
        TENANT_A, job.task_name, job.idempotency_key, {**job.payload, "changed": True}
    )
    with pytest.raises(ValueError):
        asyncio.run(_enqueue(database, changed))
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM xt_queue.requests WHERE job_id = %s AND completed",
            (first,),
            worker=worker,
        )
        _wait_for(
            database,
            "SELECT 1 FROM xt_queue.requests WHERE job_id=%s AND completed",
            (other_id,),
            worker=worker,
        )
        assert asyncio.run(_enqueue(database, job)) == first
        with pytest.raises(ValueError):
            asyncio.run(_enqueue(database, changed))
    assert database.execute(
        "SELECT count(*) FROM xt_t1.effects WHERE job_key = %s", (job.idempotency_key,)
    ) == [(1,)]
    assert database.execute(
        "SELECT observed_tenant FROM xt_t2.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(TENANT_B,)]


def test_worker_tenant_context_and_schema_rls_prevent_cross_tenant_access(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    own = _request(database, TENANT_B, "xt_t2")
    next_tenant = _request(database, TENANT_A, "xt_t1")
    foreign = JobRequest(TENANT_A, "business.finish", uuid4().hex, own.payload)
    own_id = asyncio.run(_enqueue(database, own))
    foreign_id = asyncio.run(_enqueue(database, foreign))
    next_id = asyncio.run(_enqueue(database, next_tenant))
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM xt_queue.requests WHERE job_id = %s AND completed",
            (own_id,),
            worker=worker,
        )
        _wait_for(
            database,
            "SELECT 1 FROM public.procrastinate_jobs WHERE id = %s AND status = 'failed'",
            (foreign_id,),
            worker=worker,
        )
        _wait_for(
            database,
            "SELECT 1 FROM xt_queue.requests WHERE job_id=%s AND completed",
            (next_id,),
            worker=worker,
        )
    assert database.execute(
        "SELECT observed_tenant,observed_role FROM xt_t2.effects WHERE job_key = %s",
        (own.idempotency_key,),
    ) == [(TENANT_B, "xt_job_t2")]
    assert (
        database.execute(
            "SELECT 1 FROM xt_t2.effects WHERE job_key = %s", (foreign.idempotency_key,)
        )
        == []
    )
    assert database.execute(
        "SELECT observed_tenant,observed_role FROM xt_t1.effects WHERE job_key=%s",
        (next_tenant.idempotency_key,),
    ) == [(TENANT_A, "xt_job_t1")]


def test_queue_storage_rls_blocks_foreign_jobs_and_events_and_control_business_access(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    own = _request(database, TENANT_A, "xt_t1")
    foreign = _request(database, TENANT_B, "xt_t2")
    own_id = asyncio.run(_enqueue(database, own))
    foreign_id = asyncio.run(_enqueue(database, foreign))

    async def scenario() -> None:
        async with database.tenant_transaction(TENANT_A) as connection:
            own_rows = await (
                await connection.execute(
                    "SELECT id FROM public.procrastinate_jobs WHERE id=%s", (own_id,)
                )
            ).fetchall()
            assert own_rows == [(own_id,)]
            for table, column, update in (
                ("procrastinate_jobs", "id", "status=status"),
                ("procrastinate_events", "job_id", None),
            ):
                cursor = await connection.execute(
                    sql.SQL("SELECT * FROM {} WHERE {}=%s").format(
                        sql.Identifier("public", table), sql.Identifier(column)
                    ),
                    (foreign_id,),
                )
                assert await cursor.fetchall() == []
                if update is None:
                    continue  # xt_app has no UPDATE on events at all (AQ-09).
                # Both table/update fragments are fixed test data, not caller input.
                cursor = await connection.execute(
                    f"UPDATE public.{table} SET {update} WHERE {column}=%s", (foreign_id,)
                )
                assert cursor.rowcount == 0
        async with await AsyncConnection.connect(database.dsn("xt_app"), autocommit=True) as conn:
            assert (
                await (await conn.execute("SELECT id FROM public.procrastinate_jobs")).fetchall()
                == []
            )
            assert (
                await (await conn.execute("SELECT id FROM public.procrastinate_events")).fetchall()
                == []
            )

    asyncio.run(scenario())
    assert database.execute(
        "SELECT 1 FROM public.procrastinate_events WHERE job_id=%s", (foreign_id,)
    )
    with psycopg.connect(database.dsn("xt_control"), autocommit=True) as connection:
        for table in ("xt_t1.requests", "xt_t1.effects", "xt_t2.requests", "xt_t2.effects"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(f"SELECT * FROM {table}")


def test_sigkill_recovery_replays_rolled_back_work_once(worker_database: WorkerDatabase) -> None:
    database = worker_database
    barrier = secrets.randbelow(1_000_000) + 100_000
    database.execute("INSERT INTO public.worker_test_gates VALUES (%s,false)", (barrier,))
    job = _request(database, TENANT_A, "xt_t1", "business.block_once", barrier=barrier)
    job_id = asyncio.run(_enqueue(database, job))
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM pg_locks WHERE locktype='advisory' AND objid=%s AND granted",
            (barrier,),
            worker=worker,
        )
        worker.kill()
        worker.wait(timeout=5)
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (job.payload["request_id"],)
    ) == [("pending",)]
    database.execute("UPDATE public.worker_test_gates SET released=true WHERE id=%s", (barrier,))
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM xt_queue.requests WHERE job_id=%s AND completed",
            (job_id,),
            worker=worker,
            timeout=30,
        )
    assert database.execute(
        "SELECT count(*) FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,)
    ) == [(1,)]


def test_retry_budget_dead_letters_without_committing_business_writes(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1", "business.fail")
    job_id = asyncio.run(_enqueue(database, job))
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM public.procrastinate_jobs WHERE id=%s AND status='failed'",
            (job_id,),
            worker=worker,
        )
    assert database.execute(
        "SELECT attempts FROM public.procrastinate_jobs WHERE id=%s", (job_id,)
    ) == [(2,)]
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (job.payload["request_id"],)
    ) == [("pending",)]
    assert (
        database.execute("SELECT 1 FROM xt_t1.effects WHERE job_key=%s", (job.idempotency_key,))
        == []
    )


def test_timer_cancellation_shares_task_completion_transaction(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    committed = _request(database, TENANT_A, "xt_t1")
    rolled_back = _request(database, TENANT_A, "xt_t1")
    runtime = database.runtime()

    async def scenario() -> None:
        for job in (committed, rolled_back):
            async with database.tenant_transaction(TENANT_A) as connection:
                await runtime.queue.schedule_at(
                    connection, job, datetime.now(UTC) + timedelta(seconds=2)
                )
        async with database.tenant_transaction(TENANT_A) as connection:
            await connection.execute(
                "UPDATE xt_t1.requests SET status='done' WHERE id=%s",
                (committed.payload["request_id"],),
            )
            assert await runtime.queue.cancel(
                connection, tenant_id=TENANT_A, idempotency_key=committed.idempotency_key
            )
        with pytest.raises(RuntimeError, match="undo"):
            async with database.tenant_transaction(TENANT_A) as connection:
                await connection.execute(
                    "UPDATE xt_t1.requests SET status='done' WHERE id=%s",
                    (rolled_back.payload["request_id"],),
                )
                assert await runtime.queue.cancel(
                    connection, tenant_id=TENANT_A, idempotency_key=rolled_back.idempotency_key
                )
                raise RuntimeError("undo cancellation and completion")

    asyncio.run(scenario())
    assert database.execute(
        "SELECT cancelled FROM xt_queue.requests WHERE idempotency_key=%s",
        (committed.idempotency_key,),
    ) == [(True,)]
    assert database.execute(
        "SELECT cancelled FROM xt_queue.requests WHERE idempotency_key=%s",
        (rolled_back.idempotency_key,),
    ) == [(False,)]
    assert database.execute(
        "SELECT status FROM xt_t1.requests WHERE id=%s", (rolled_back.payload["request_id"],)
    ) == [("pending",)]
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM xt_t1.effects WHERE job_key=%s",
            (rolled_back.idempotency_key,),
            worker=worker,
        )
    assert (
        database.execute(
            "SELECT 1 FROM xt_t1.effects WHERE job_key=%s", (committed.idempotency_key,)
        )
        == []
    )


def test_queue_requires_open_transaction_with_matching_tenant_and_registered_handler(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")

    async def scenario() -> None:
        queue = database.runtime().queue
        async with await AsyncConnection.connect(database.dsn("xt_app"), autocommit=True) as conn:
            with pytest.raises(ValueError, match="transaction"):
                await queue.enqueue(conn, job)
            async with conn.transaction():
                with pytest.raises(ValueError, match="tenant"):
                    await queue.enqueue(conn, job)
            async with conn.transaction():
                await conn.execute("SELECT set_config('xt.tenant_id', %s, true)", (str(TENANT_B),))
                with pytest.raises(ValueError, match="tenant"):
                    await queue.enqueue(conn, job)
            context = await (
                await conn.execute("SELECT current_setting('xt.tenant_id',true)")
            ).fetchone()
            assert context is not None and context[0] in (None, "")
        async with database.tenant_transaction(TENANT_A) as conn:
            unknown = JobRequest(TENANT_A, "unregistered", uuid4().hex, {})
            with pytest.raises(ValueError, match="registered"):
                await queue.enqueue(conn, unknown)
            with pytest.raises(ValueError, match="timezone"):
                await queue.schedule_at(conn, job, datetime(2026, 10, 15))

    asyncio.run(scenario())
    assert (
        database.execute(
            "SELECT 1 FROM xt_queue.requests WHERE idempotency_key=%s", (job.idempotency_key,)
        )
        == []
    )


def test_periodic_monday_eight_ho_chi_minh_has_real_utc_schedule_and_dispatch(
    worker_database: WorkerDatabase,
) -> None:
    database = worker_database
    job = _request(database, TENANT_A, "xt_t1")
    runtime = database.runtime()

    async def schedule() -> int:
        async with database.tenant_transaction(TENANT_A) as connection:
            return await runtime.queue.periodic(
                connection, job, cron="0 8 * * 1", timezone="Asia/Ho_Chi_Minh"
            )

    schedule_id = asyncio.run(schedule())
    rows = database.execute(
        "SELECT cron_utc,next_at FROM xt_queue.schedules WHERE id=%s", (schedule_id,)
    )
    cron_utc, next_at = rows[0]
    assert cron_utc == "0 1 * * 1"
    local = next_at.astimezone(ZoneInfo("Asia/Ho_Chi_Minh"))
    assert (local.weekday(), local.hour, local.minute) == (0, 8, 0)
    assert next_at > datetime.now(UTC)
    database.execute(
        "UPDATE xt_queue.schedules SET next_at=now()-interval '1 second' WHERE id=%s",
        (schedule_id,),
    )
    asyncio.run(runtime.maintenance())
    with _worker(database) as worker:
        _wait_for(
            database,
            "SELECT 1 FROM xt_t1.requests WHERE id=%s AND status='done'",
            (job.payload["request_id"],),
            worker=worker,
        )
