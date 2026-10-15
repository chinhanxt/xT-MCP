# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit fixture: own postgres:17-alpine container on 127.0.0.1:20100 (never pulls)."""

from __future__ import annotations

import asyncio
import secrets
import shutil
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
import pytest

from tests.integration.test_worker_queue import (
    IMAGE,
    WorkerDatabase,
    _install_business_schema,
    bind_tenants,
)

AUDIT_PORT = 20100


@dataclass(frozen=True)
class AuditDatabase(WorkerDatabase):
    def dsn(self, role: str) -> str:
        return f"postgresql://{role}:{self.password}@127.0.0.1:{AUDIT_PORT}/xt_worker_it"


@pytest.fixture(scope="session")
def audit_db() -> Iterator[AuditDatabase]:
    if shutil.which("docker") is None:
        pytest.fail("queue audit tests require Docker and local postgres:17-alpine")
    if subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode:
        pytest.fail("postgres:17-alpine image is missing; audit never pulls images")
    database = AuditDatabase(secrets.token_hex(18))
    name = f"xt-audit-queue-{secrets.token_hex(4)}"
    try:
        subprocess.run(
            [
                "docker", "run", "--pull", "never", "-d", "--name", name,
                "-p", f"127.0.0.1:{AUDIT_PORT}:5432",
                "-e", f"POSTGRES_PASSWORD={database.password}",
                "-e", "POSTGRES_DB=xt_worker_it",
                IMAGE,
            ],
            check=True,
            capture_output=True,
        )  # fmt: skip
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(database.dsn("postgres"), connect_timeout=1):
                    break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    pytest.fail("PostgreSQL did not become ready in 30 seconds")
                time.sleep(0.2)
        _install_business_schema(database)
        # Worst case for the AQ-03 leak tests: the bootstrap revokes TEMP from
        # PUBLIC, the audit gives it back to handler roles so temp tables can leak.
        with psycopg.connect(database.dsn("postgres"), autocommit=True) as connection:
            connection.execute("GRANT TEMPORARY ON DATABASE xt_worker_it TO xt_job, xt_workflow")
        asyncio.run(database.runtime().install_schema(database.dsn("postgres")))
        bind_tenants(database)
        yield database
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, check=False)
