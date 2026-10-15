# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Postgres 17 thật cho test ghép (composition): `postgres:17-alpine` có sẵn, cổng ngẫu nhiên.

Không pull image (đĩa `/` gần đầy): thiếu image thì lỗi rõ. Mật khẩu/pepper sinh ngẫu nhiên mỗi lần
chạy; container `--rm` và được xóa khi xong.
"""

from __future__ import annotations

import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any

import psycopg

IMAGE = "postgres:17-alpine"
# Tên + cổng riêng mỗi tiến trình test: nhiều session/agent chạy song song không dọn nhầm của nhau.
CONTAINER = f"xt-it-composition-pg-{secrets.token_hex(4)}"


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


PORT = _free_port()
DSN = f"host=127.0.0.1 port={PORT} dbname=xt"
MIN_FREE_BYTES = 512 * 1024 * 1024


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
    if shutil.disk_usage("/").free < MIN_FREE_BYTES:
        raise RuntimeError("đĩa / còn dưới 512 MiB: dọn trước khi chạy test tích hợp")
    have = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
    if have.returncode != 0:
        raise RuntimeError(f"thiếu image {IMAGE} (không tự pull; kiểm `df -h /` trước)")
    pg = Pg(
        secrets.token_urlsafe(16),
        secrets.token_urlsafe(16),
        secrets.token_urlsafe(16),
        secrets.token_hex(32).encode(),  # ASCII: dùng được làm tệp khóa XT_AUDIT_KEY_FILE
    )
    stop()
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            CONTAINER,
            "-p",
            f"127.0.0.1:{PORT}:5432",
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
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, check=False)
