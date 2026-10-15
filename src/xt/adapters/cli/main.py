# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""CLI `xt` (argparse): --version, doctor."""

from __future__ import annotations

import argparse
import os
import socket
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from xt import __version__

OK = "ok"
FAIL = "fail"
UNKNOWN = "unknown"

MIN_PYTHON = (3, 12)
DATABASE_ENV = "XT_DATABASE_URL"
# Dịch vụ thật dùng DSN theo vai trò: xt-api/xt-init `XT_DB_APP_URL`, xt-worker `XT_WORKER_DATABASE_URL`.
DATABASE_ENV_FALLBACKS = ("XT_DB_APP_URL", "XT_WORKER_DATABASE_URL", "XT_QUEUE_DATABASE_URL")
CONNECT_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def check_python() -> Check:
    cur = sys.version_info[:3]
    text = ".".join(map(str, cur))
    if cur >= MIN_PYTHON:
        return Check("python", OK, text)
    need = ".".join(map(str, MIN_PYTHON))
    return Check("python", FAIL, f"{text} < {need}")


def check_postgres(url: str | None) -> Check:
    """Chỉ kiểm tra TCP tới host:port trong URL; chưa xác thực/đăng nhập."""
    if not url:
        names = ", ".join((DATABASE_ENV, *DATABASE_ENV_FALLBACKS))
        return Check("postgres", UNKNOWN, f"chưa đặt biến DSN nào trong: {names}")
    parsed = urlparse(url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        return Check("postgres", FAIL, f"{DATABASE_ENV} không phải URL postgres hợp lệ")
    host, port = parsed.hostname, parsed.port or 5432
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S):
            pass
    except OSError as exc:
        return Check("postgres", FAIL, f"{host}:{port} không kết nối được ({exc})")
    return Check("postgres", OK, f"{host}:{port} TCP reachable (chưa xác thực đăng nhập)")


def database_url(environ: Mapping[str, str]) -> str | None:
    for name in (DATABASE_ENV, *DATABASE_ENV_FALLBACKS):
        if environ.get(name):
            return environ[name]
    return None


def run_doctor() -> int:
    checks = [check_python(), check_postgres(database_url(os.environ))]
    for c in checks:
        print(f"{c.name}: {c.status} - {c.detail}")
    return 1 if any(c.status == FAIL for c in checks) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="xt", description="xT - Agent-native DX-OS")
    parser.add_argument("--version", action="version", version=f"xt {__version__}")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("doctor", help="kiểm tra môi trường (chỉ đọc)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return run_doctor()
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
