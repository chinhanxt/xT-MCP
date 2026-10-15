# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng 2b — attack the worker GuardedCursor on real PG17 through the real
handler path (``QueueRuntime._dispatch`` -> ``handler_guard`` -> tenant role).

The guard is documented defense-in-depth, not a sandbox: handlers are
first-party and the dispatcher re-pins role/tenant/search_path and the pool
runs ``DISCARD ALL`` afterwards. These tests probe how far the *statement
inspection* itself reaches. Each case is classified as:

* blocked-by-guard  -> ``SessionControlError`` / ``TransactionControlError``
  raised before the statement ever reaches PostgreSQL;
* blocked-by-privilege -> PostgreSQL rejects it (InsufficientPrivilege, feature
  error, invalid-transaction-termination) for the pinned NOLOGIN job role;
* NOT-blocked -> the statement runs; the test then reads ``current_user`` /
  ``current_setting('xt.tenant_id')`` / ``current_setting('search_path')``
  inside the same handler to prove whether context actually changed.

The real bypasses B12-B15 were first recorded as ``xfail(strict=True)``; after
the Vòng 2b guard fix (DO/routine definitions rejected, search_path in the
session-control set, one-pass tokenizer) they pass and the markers are gone.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import AsyncConnection

from tests.integration.queue_audit.conftest import AuditDatabase
from tests.integration.test_worker_queue import TENANT_A, TENANT_B, _request
from xt.adapters.queue.guard import SessionControlError, TransactionControlError
from xt.application.jobs import JobRequest

pytestmark = pytest.mark.integration

PINNED_ROLE = "xt_job_t1"
PINNED_SCHEMA = "xt_t1"


def _enqueue(db: AuditDatabase, runtime: Any, job: JobRequest) -> int:
    async def scenario() -> int:
        async with db.tenant_transaction(job.tenant_id) as connection:
            return int(await runtime.queue.enqueue(connection, job))

    return asyncio.run(scenario())


def _attack(
    db: AuditDatabase, statements: list[str], *, probe: bool = False
) -> tuple[BaseException | None, dict[str, Any]]:
    """Run ``statements`` through a real handler on the pinned tenant connection.

    When ``probe`` is set the handler reads its own session context *after* the
    statements (before the dispatcher re-pins it) so a silent change is visible.
    Returns the exception that escaped the dispatch (or ``None``) and the probe.
    """
    captured: dict[str, Any] = {}

    async def handler(connection: AsyncConnection[Any], job: JobRequest) -> None:
        for statement in statements:
            await connection.execute(statement)
        if probe:
            row = await (
                await connection.execute(
                    "SELECT current_user, current_setting('xt.tenant_id', true), "
                    "current_setting('search_path')"
                )
            ).fetchone()
            captured["after"] = row

    runtime = db.runtime(handlers={"business.finish": handler}, max_attempts=1)
    job = _request(db, TENANT_A, PINNED_SCHEMA)
    _enqueue(db, runtime, job)

    async def scenario() -> None:
        async with runtime.tenant_pool:
            await runtime.dispatch(
                None,
                tenant_id=str(job.tenant_id),
                task_name=job.task_name,
                idempotency_key=job.idempotency_key,
                payload=job.payload,
            )

    error: BaseException | None = None
    try:
        asyncio.run(scenario())
    except BaseException as exc:
        error = exc
    return error, captured


# --------------------------------------------------------------------------
# Comment / whitespace / case obfuscation of SET ROLE and SET SESSION AUTH.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SET ROLE xt_app",
        "set role xt_app",
        "SeT   RoLe   xt_app",
        "SET/**/ROLE/**/xt_app",
        "SET ROLE xt_app -- trailing comment\n",
        "SET\n\tROLE\n\txt_app",
        "SET\tLOCAL\tROLE\txt_app",
        "/* lead */ SET ROLE xt_app",
        "SET SESSION AUTHORIZATION xt_app",
        "SET SESSION ROLE xt_app",
        'SET ROLE "xt_app"',
        '"SET" ROLE xt_app',  # quoted identifier normalised back to SET by the guard
        "RESET ROLE",
        "RESET SESSION AUTHORIZATION",
        "RESET ALL",
    ],
)
def test_role_switch_variants_blocked_by_guard(audit_db: AuditDatabase, statement: str) -> None:
    error, _ = _attack(audit_db, [statement])
    assert isinstance(error, SessionControlError), f"not blocked: {statement!r} -> {error!r}"


@pytest.mark.parametrize(
    "statement",
    [
        "SET xt.tenant_id = '00000000-0000-0000-0000-000000000009'",
        "SET LOCAL xt.tenant_id = '00000000-0000-0000-0000-000000000009'",
        "set XT.TENANT_ID = '00000000-0000-0000-0000-000000000009'",
        "SET/**/xt.tenant_id/**/=/**/'00000000-0000-0000-0000-000000000009'",
    ],
)
def test_xt_guc_set_blocked_by_guard(audit_db: AuditDatabase, statement: str) -> None:
    error, _ = _attack(audit_db, [statement])
    assert isinstance(error, SessionControlError), f"not blocked: {statement!r} -> {error!r}"


# --------------------------------------------------------------------------
# set_config() obfuscation: E-string, unicode escape, quoted, schema-qualified,
# inside a CTE/subquery, multi-statement. All hit the whole-text _SET_CONFIG.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "SELECT set_config('role', 'xt_app', false)",
        "SELECT set_config('xt.tenant_id', 'x', false)",
        "SELECT set_config('search_path', 'public', false)",
        "SELECT set_config(E'role', 'xt_app', false)",
        "SELECT set_config(E'r\\157le', 'xt_app', false)",
        "SELECT set_config(U&'r\\006fle', 'xt_app', false)",
        "SELECT \"set_config\"('role', 'xt_app', false)",
        "SELECT pg_catalog.set_config('role', 'xt_app', false)",
        "SELECT set_config('xt.tenant_id', 'x', false) WHERE 1=1",
        "WITH c AS (SELECT set_config('xt.tenant_id','x',false) v) SELECT v FROM c",
        "SELECT (SELECT set_config('role','xt_app',false))",
        "SELECT 1; SELECT set_config('role','xt_app',false)",
        "SELECT xt.set_context('00000000-0000-0000-0000-000000000009'::uuid, NULL, "
        "'{}'::text[], '{}'::uuid[], '{}'::uuid[], NULL, NULL, NULL)",
    ],
)
def test_set_config_and_set_context_variants_blocked_by_guard(
    audit_db: AuditDatabase, statement: str
) -> None:
    error, _ = _attack(audit_db, [statement])
    assert isinstance(error, SessionControlError), f"not blocked: {statement!r} -> {error!r}"


# --------------------------------------------------------------------------
# Transaction control obfuscation.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "COMMIT",
        "commit",
        "COMMIT/**/",
        "END",
        "ABORT",
        "ROLLBACK",
        "ROLLBACK WORK",
        "PREPARE TRANSACTION 'gid1'",
        "SELECT 1; COMMIT",
    ],
)
def test_transaction_end_variants_blocked_by_guard(audit_db: AuditDatabase, statement: str) -> None:
    error, _ = _attack(audit_db, [statement])
    assert isinstance(error, TransactionControlError), f"not blocked: {statement!r} -> {error!r}"


def test_savepoint_and_rollback_to_savepoint_allowed(audit_db: AuditDatabase) -> None:
    """Savepoints are intentionally usable by handlers; guard must not block them."""
    error, _ = _attack(
        audit_db,
        ["SAVEPOINT s1", "ROLLBACK TO SAVEPOINT s1", "RELEASE SAVEPOINT s1"],
    )
    assert error is None, f"savepoint flow wrongly blocked: {error!r}"


def test_workflow_elevation_still_allowed(audit_db: AuditDatabase) -> None:
    """The single documented elevation (SET LOCAL ROLE xt_workflow) must pass the guard."""
    error, probe = _attack(audit_db, ["SET LOCAL ROLE xt_workflow"], probe=True)
    # xt_job_t1 is not a member of xt_workflow, so PG rejects it -- but the GUARD
    # must let it through (defense-in-depth allows the elevation statement shape).
    assert not isinstance(error, SessionControlError), "guard blocked the allowed elevation"


# --------------------------------------------------------------------------
# PL wrappers, DO blocks, PREPARE/EXECUTE, CALL -- dynamic SQL the guard does
# not parse. Privilege of the NOLOGIN job role is the real line of defense.
# --------------------------------------------------------------------------


def test_set_config_inside_do_and_function_bodies_still_caught(
    audit_db: AuditDatabase,
) -> None:
    """Whole-text _SET_CONFIG search sees ``set_config(`` even in a dollar body.

    The guard blanks literals only for the per-statement role/tx checks; the
    set_config/set_context scan runs over the raw text, so a DO or a CREATE
    FUNCTION whose body calls set_config is rejected before it executes.
    """
    body_do = "DO $$ BEGIN PERFORM set_config('xt.tenant_id', 'x', false); END $$"
    body_fn = (
        f"CREATE FUNCTION pg_temp.f_{uuid4().hex}() RETURNS void LANGUAGE plpgsql AS "
        "$$ BEGIN PERFORM set_config('xt.tenant_id', 'x', false); END $$"
    )
    for statement in (body_do, body_fn):
        error, _ = _attack(audit_db, [statement])
        assert isinstance(error, SessionControlError), f"not blocked: {statement!r} -> {error!r}"


def test_handler_role_cannot_create_pg_temp_functions(audit_db: AuditDatabase) -> None:
    """After the Vòng 2b fix: routine definitions are rejected by the guard, and job
    roles hold no TEMPORARY privilege (``xt_queue.job_binding`` fails closed otherwise),
    so the pg_temp routine prerequisite of B12/B13 is gone on both layers."""
    name = f"pg_temp.ok_{uuid4().hex}"
    error, _ = _attack(
        audit_db, [f"CREATE FUNCTION {name}() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"]
    )
    assert isinstance(error, SessionControlError), f"not blocked: {error!r}"
    assert audit_db.execute(
        "SELECT has_database_privilege(%s, current_database(), 'TEMPORARY')", (PINNED_ROLE,)
    ) == [(False,)]


@pytest.mark.parametrize(
    "statement",
    [
        "DO $$ BEGIN PERFORM 1; END $$",
        "/**/ do $x$ begin null; end $x$",
        "SELECT 1; DO 'BEGIN NULL; END'",
        "CREATE OR REPLACE PROCEDURE pg_temp.p() LANGUAGE sql AS 'SELECT 1'",
        "CREATE TABLE pg_temp.t(a int)",
        "SELECT '/*'; SET ROLE xt_app; SELECT '*/'",  # comment opener inside a literal
        "SET \"xt.tenant_id\" = 'x'",
        "SET SCHEMA 'pg_catalog'",
    ],
)
def test_dynamic_code_and_tokenizer_evasions_blocked_by_guard(
    audit_db: AuditDatabase, statement: str
) -> None:
    error, _ = _attack(audit_db, [statement])
    assert isinstance(error, SessionControlError), f"not blocked: {statement!r} -> {error!r}"


def test_do_keyword_set_role_escalates_to_app_role(audit_db: AuditDatabase) -> None:
    error, probe = _attack(audit_db, ["DO $$ BEGIN SET ROLE xt_app; END $$"], probe=True)
    assert isinstance(error, SessionControlError), (
        f"NOT blocked; current_user after handler = {probe.get('after')!r}"
    )


def test_do_keyword_set_rewrites_tenant_context_guc(audit_db: AuditDatabase) -> None:
    other = str(TENANT_B)
    error, probe = _attack(
        audit_db,
        [f"DO $$ BEGIN SET xt.tenant_id = '{other}'; END $$"],
        probe=True,
    )
    assert isinstance(error, SessionControlError), (
        f"NOT blocked; tenant GUC after handler = {probe.get('after')!r}"
    )


def test_function_wrapper_keyword_set_rewrites_tenant_context_guc(
    audit_db: AuditDatabase,
) -> None:
    other = str(TENANT_B)
    name = f"pg_temp.w_{uuid4().hex}"
    error, probe = _attack(
        audit_db,
        [
            f"CREATE FUNCTION {name}() RETURNS void LANGUAGE plpgsql AS "
            f"$$ BEGIN SET xt.tenant_id = '{other}'; END $$",
            f"SELECT {name}()",
        ],
        probe=True,
    )
    assert isinstance(error, SessionControlError), (
        f"NOT blocked; tenant GUC after handler = {probe.get('after')!r}"
    )


def test_set_search_path_not_inspected(audit_db: AuditDatabase) -> None:
    error, probe = _attack(audit_db, ["SET LOCAL search_path TO pg_catalog"], probe=True)
    assert isinstance(error, SessionControlError), (
        f"NOT blocked; search_path after handler = {probe.get('after')!r}"
    )


def test_call_procedure_commit_blocked_by_postgres_runtime(audit_db: AuditDatabase) -> None:
    """After the Vòng 2b fix the pg_temp procedure can no longer be defined at all: the
    guard rejects CREATE PROCEDURE before PostgreSQL would refuse the inner COMMIT."""
    name = f"pg_temp.p_{uuid4().hex}"
    error, _ = _attack(
        audit_db,
        [
            f"CREATE PROCEDURE {name}() LANGUAGE plpgsql AS $$ BEGIN COMMIT; END $$",
            f"CALL {name}()",
        ],
    )
    assert isinstance(error, SessionControlError), f"unexpected: {error!r}"


def test_prepare_execute_of_set_config_blocked_by_guard(audit_db: AuditDatabase) -> None:
    """PREPARE text carries the set_config literal, so the whole-text guard catches it."""
    error, _ = _attack(
        audit_db,
        ["PREPARE p AS SELECT set_config('xt.tenant_id', $1, false)"],
    )
    assert isinstance(error, SessionControlError), f"not blocked: {error!r}"


# --------------------------------------------------------------------------
# DISCARD / RESET family and COPY TO PROGRAM.
# --------------------------------------------------------------------------


def test_discard_all_rejected_by_postgres_inside_transaction(audit_db: AuditDatabase) -> None:
    """DISCARD ALL would reset role+search_path+GUCs, but PG forbids it in a tx block."""
    error, _ = _attack(audit_db, ["DISCARD ALL"])
    assert not isinstance(error, (SessionControlError, TransactionControlError))
    assert isinstance(error, psycopg.errors.ActiveSqlTransaction), f"unexpected: {error!r}"


def test_reset_search_path_not_inspected(audit_db: AuditDatabase) -> None:
    error, _ = _attack(audit_db, ["RESET search_path"], probe=True)
    assert isinstance(error, SessionControlError), f"NOT blocked: {error!r}"


def test_copy_to_program_rejected_by_privilege(audit_db: AuditDatabase) -> None:
    """COPY ... TO PROGRAM needs pg_execute_server_program; the job role lacks it."""
    error, _ = _attack(audit_db, ["COPY (SELECT 1) TO PROGRAM 'id'"])
    assert not isinstance(error, (SessionControlError, TransactionControlError))
    assert isinstance(error, psycopg.errors.InsufficientPrivilege), f"unexpected: {error!r}"
