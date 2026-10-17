# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng đời stack e2e: `python -m xte2e.stack up|down|status`.

`up` = volume MỚI (down -v trước), render APISIX, build, khởi động dịch vụ minimal.
postgrest bật sau khi demo-seed có JWKS. Chỉ chạy khi RAM available >= 4 GiB và
không có stack Keycloak/Ollama nặng khác đang chạy.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from xte2e import compose
from xte2e.config import DEPLOY, settings

MIN_AVAILABLE_GIB = 4.0


def available_gib() -> float:
    with open("/proc/meminfo", encoding="ascii") as meminfo:
        for line in meminfo:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    return 0.0


def other_heavy() -> list[str]:
    proj = settings().project
    result = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Image}}"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError("[stack] docker ps failed; cannot verify the heavy-stack slot")
    out = result.stdout
    return [
        ln.split("\t")[0]
        for ln in out.splitlines()
        if any(k in ln for k in ("keycloak", "ollama/ollama")) and not ln.startswith(f"{proj}-")
    ]


def preflight(wait: float = 600) -> None:
    end = time.time() + wait
    while True:
        avail, heavy = available_gib(), other_heavy()
        if avail >= MIN_AVAILABLE_GIB and not heavy:
            print(f"[stack] RAM available {avail:.1f} GiB, không có stack nặng khác")
            return
        if time.time() > end:
            sys.exit(
                f"[stack] không đủ điều kiện heavy-stack: available={avail:.1f} GiB, "
                f"đang chạy: {heavy}"
            )
        print(f"[stack] chờ: available={avail:.1f} GiB, stack nặng khác: {heavy}")
        time.sleep(30)


def up() -> None:
    preflight()
    # Compose also parses required secrets for `down`, including on a first run.
    # Render APISIX and generate any missing test secrets from .env.example.
    subprocess.run(["make", "-s", "render"], cwd=DEPLOY, check=True)
    # A failed cleanup means the next run cannot claim fresh volumes.
    compose.dc(["--profile", "*", "down", "--remove-orphans", "--volumes"])
    print("[stack] build + up (volume mới)")
    compose.dc(["build"], timeout=1800, capture=False)
    issues: list[str] = []
    compose.override_file().unlink(missing_ok=True)
    r = compose.dc(["up", "-d", *compose.CORE], timeout=900, check=False)
    print((r.stdout or "")[-1500:], (r.stderr or "")[-1500:])
    if r.returncode != 0:
        issues.append(f"docker compose up rc={r.returncode}")
    issues_file().write_text(json.dumps(issues, ensure_ascii=False), encoding="utf-8")
    if issues:
        # Keep diagnostics, but never seed or test against an incomplete stack.
        print(f"[stack] {issues[0]}\n{compose.ps()}")
        raise RuntimeError(f"[stack] {issues[0]}")


def issues_file() -> Path:
    settings().state_dir.mkdir(parents=True, exist_ok=True)
    return settings().state_dir / "stack-issues.json"


def down() -> None:
    compose.dc(["--profile", "*", "down", "--remove-orphans", "--volumes"], capture=False)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        up()
    elif cmd == "down":
        down()
    elif cmd == "preflight":
        preflight()
    else:
        print(compose.ps())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
