# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`docker compose` của stack e2e (project riêng `xt-e2e`, volume riêng) và lệnh `xt` thật.

Bí mật không bao giờ lên argv: biến được truyền bằng `-e TÊN` (giá trị lấy từ môi trường tiến trình).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from xte2e.config import DEPLOY, ROOT, settings

# Dịch vụ của profile minimal. postgrest khởi động sau khi có JWKS (demo-seed), xem seed.py.
CORE = ["postgres", "keycloak", "apisix", "xt-init", "xt-api", "xt-worker", "xt-desk", "studio"]


def override_file() -> Path:
    return settings().state_dir / "compose-config-files.yaml"


def base_cmd() -> list[str]:
    s = settings()
    files = ["-f", str(DEPLOY / "compose.yaml")]
    if (
        override_file().exists()
    ):  # chỉ khi stack.up phải dùng đường vòng (ghi vào stack-issues.json, E0 FAIL)
        files += ["-f", str(override_file())]
    return [
        "docker",
        "compose",
        "-p",
        s.project,
        *files,
        "--env-file",
        str(DEPLOY / ".env"),
        "--profile",
        "minimal",
    ]


def dc(
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    check: bool = True,
    capture: bool = True,
    timeout: float | None = 900,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    e = {**settings().compose_env(), **(env or {})}
    r = subprocess.run(
        [*base_cmd(), *args],
        cwd=DEPLOY,
        env=e,
        text=True,
        input=input,
        capture_output=capture,
        timeout=timeout,
        check=False,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"docker compose {' '.join(args[:3])} rc={r.returncode}\n{(r.stderr or '')[-2000:]}"
        )
    return r


def superuser_dsn() -> str:
    e = settings().env
    return f"postgresql://{e.get('POSTGRES_USER') or 'xt'}:{e['POSTGRES_PASSWORD']}@postgres:5432/{e.get('POSTGRES_DB') or 'xt'}"


def xt(
    args: Sequence[str],
    *,
    service: str = "xt-api",
    secret_env: Mapping[str, str] | None = None,
    mounts: Sequence[tuple[Path, str]] = (),
    timeout: float = 600,
) -> tuple[int, str]:
    """`xt <args>` trong ảnh xt/xt:dev, cùng mạng compose (DSN nội bộ `postgres:5432`)."""
    flags: list[str] = ["run", "--rm", "--no-deps", "-T", "--entrypoint", "xt"]
    for name in secret_env or {}:
        flags += ["-e", name]
    for src, dst in mounts:
        flags += ["-v", f"{src}:{dst}:ro"]
    r = dc([*flags, service, *args], env=secret_env, check=False, timeout=timeout)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def psql(sql: str, *, db: str | None = None) -> str:
    """Truy vấn bằng superuser trong container postgres (chỉ để kiểm chứng/tamper của kịch bản)."""
    e = settings().env
    r = dc(
        [
            "exec",
            "-T",
            "postgres",
            "psql",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            e.get("POSTGRES_USER") or "xt",
            "-d",
            db or e.get("POSTGRES_DB") or "xt",
            "-At",
            "-F",
            "\t",
            "-f",
            "-",
        ],
        input=sql,
        check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"psql lỗi: {(r.stderr or '')[-800:]}")
    return r.stdout.strip()


def ps() -> str:
    return dc(
        ["ps", "--all", "--format", "{{.Service}}\t{{.State}}\t{{.Status}}"], check=False
    ).stdout


def logs(service: str, tail: int = 80) -> str:
    return dc(["logs", "--no-color", "--tail", str(tail), service], check=False).stdout


def repo_mount(rel: str, dst: str) -> tuple[Path, str]:
    return (ROOT / rel, dst)


def env_with(**kv: str) -> dict[str, str]:
    return {**os.environ, **kv}
