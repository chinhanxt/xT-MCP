# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Postgres 17 thật cho test tích hợp model engine (docker `postgres:17-alpine`, cổng 19700).

Image phải có sẵn trên máy (không pull khi đĩa gần đầy). Mật khẩu/khóa HMAC sinh ngẫu nhiên mỗi
lần chạy; container tự xóa khi xong.
"""

from __future__ import annotations

import secrets
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import psycopg

IMAGE = "postgres:17-alpine"
CONTAINER = "xt-it-model-pg"
PORT = 19700
DSN = f"host=localhost port={PORT} dbname=xt"


@dataclass(frozen=True)
class Pg:
    owner_password: str
    migrator_password: str
    app_password: str
    pepper: bytes

    def su(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            f"{DSN} user=xt_owner password={self.owner_password}", autocommit=True
        )

    def migrator(self) -> psycopg.Connection[Any]:
        return psycopg.connect(
            f"{DSN} user=xt_migrator password={self.migrator_password}", autocommit=True
        )

    def app(self) -> psycopg.Connection[Any]:
        return psycopg.connect(f"{DSN} user=xt_app password={self.app_password}", autocommit=True)

    @property
    def app_dsn(self) -> str:
        return f"{DSN} user=xt_app password={self.app_password}"

    @property
    def migrator_dsn(self) -> str:
        return f"{DSN} user=xt_migrator password={self.migrator_password}"


def start() -> Pg:
    have = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    if have.returncode != 0:
        raise RuntimeError(f"thiếu image {IMAGE} (kiểm `df -h /` trước khi pull)")
    pg = Pg(
        secrets.token_urlsafe(16),
        secrets.token_urlsafe(16),
        secrets.token_urlsafe(16),
        secrets.token_bytes(32),
    )
    subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True, check=False)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER,
            "-p",
            f"{PORT}:5432",
            "-e",
            "POSTGRES_USER=xt_owner",
            "-e",
            f"POSTGRES_PASSWORD={pg.owner_password}",
            "-e",
            "POSTGRES_DB=xt",
            IMAGE,
            "-c",
            "deadlock_timeout=1s",
        ],
        check=True,
        capture_output=True,
    )
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            with pg.su() as c:
                c.execute("SELECT 1")
            return pg
        except psycopg.OperationalError:
            time.sleep(0.5)
    stop()
    raise RuntimeError("Postgres không lên trong 60 s")


def stop() -> None:
    subprocess.run(["docker", "rm", "-f", "-v", CONTAINER], capture_output=True, check=False)
