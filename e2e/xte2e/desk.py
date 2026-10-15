# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chạy `e2e/desk/desk_real.mjs` (Playwright Chromium headless của desk/node_modules)."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

from xte2e.config import E2E, ROOT


def run(mode: str, user: dict[str, Any], prompt: str = "", timeout: float = 600) -> tuple[int, dict[str, Any], str]:
    if not (ROOT / "desk" / "node_modules" / "@playwright" / "test").exists():
        return 2, {"fails": 1, "checks": [{"ok": False, "msg": "desk/node_modules chưa có (make desk-setup)"}]}, ""
    nvm = os.path.expanduser("~/.nvm/nvm.sh")
    # Mật khẩu/TOTP qua argv của tiến trình con cục bộ (không log, không vào báo cáo).
    cmd = f'. "{nvm}" >/dev/null 2>&1 || true; exec node "{E2E / "desk" / "desk_real.mjs"}" "$@"'
    args = [mode, user["username"], user["password"], user.get("totp") or ""]
    if mode == "chat":
        args.append(prompt)
    r = subprocess.run(["bash", "-c", cmd, "desk", *args], capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "SHOT": str(E2E / "reports" / f"desk-{mode}.png")})
    res: dict[str, Any] = {}
    for line in r.stdout.splitlines():
        if line.startswith("RESULT "):
            res = json.loads(line[7:])
    return r.returncode, res, (r.stdout + r.stderr)[-3000:]
