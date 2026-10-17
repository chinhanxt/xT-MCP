# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Postgres 17 thật cho test process: cổng ngẫu nhiên, tự dọn container sau phiên."""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from tests.unit.process.support import Clock, Services, clock_fixture, make_runtime, seed_org
from xt.application.process.memory import InMemoryOrgDirectory
from xt.application.process.runtime import ProcessRuntime

IMAGE = "postgres:17-alpine"
# Tên container có hậu tố ngẫu nhiên: nhiều lượt chạy song song không xóa container của nhau.
CONTAINER = f"xt-it-process-pg-{secrets.token_hex(4)}"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


PORT = int(os.environ.get("XT_IT_PROCESS_PG_PORT") or _free_port())


def _wait_ready(dsn: str, timeout: float = 60.0) -> None:
    import psycopg

    deadline = time.monotonic() + timeout
    last: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as c:
                c.execute("SELECT 1")
            return
        except psycopg.OperationalError as e:
            last = e
            time.sleep(0.5)
    raise RuntimeError(f"Postgres không sẵn sàng sau {timeout}s: {last}")


@pytest.fixture(scope="session")
def pg_dsn() -> Iterator[str]:
    if shutil.which("docker") is None:
        pytest.fail(f"test tích hợp process cần docker (image {IMAGE})")
    password = secrets.token_urlsafe(18)
    subprocess.run(
        ["docker", "run", "-d", "--rm", "--name", CONTAINER, "-p", f"127.0.0.1:{PORT}:5432",
         "-e", f"POSTGRES_PASSWORD={password}", "-e", "POSTGRES_DB=xt_process_it", IMAGE],
        check=True, capture_output=True,
    )  # fmt: skip
    dsn = f"postgresql://postgres:{password}@127.0.0.1:{PORT}/xt_process_it"
    try:
        _wait_ready(dsn)
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True, check=False)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Iterator[Clock]:
    yield from clock_fixture(monkeypatch)


@pytest.fixture
def org() -> InMemoryOrgDirectory:
    return seed_org()


@pytest.fixture
def services() -> Services:
    return Services()


@pytest.fixture
def make_rt(org: InMemoryOrgDirectory, services: Services) -> Callable[..., ProcessRuntime]:
    def mk(spec: str | dict[str, Any]) -> ProcessRuntime:
        return make_runtime(spec, services, org)

    return mk
