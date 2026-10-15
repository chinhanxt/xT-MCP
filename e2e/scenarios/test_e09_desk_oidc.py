# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E9: đăng nhập OIDC trên Desk THẬT (container xt-desk sau APISIX, không serve build qua route),
Keycloak thật, rồi Desk nối MCP thật (tools/list có Bearer). Kèm `deploy/tests/desk_oidc_e2e.py check`
(cấu hình client xt-desk-agent qua Admin REST)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from xte2e import desk
from xte2e.config import DEPLOY
from xte2e.flows import A, REQUESTER
from xte2e.report import Checks, Evidence

SCENARIO = "E9"
pytestmark = pytest.mark.e2e


def test_desk_client_config(ev: Evidence) -> None:
    r = subprocess.run([sys.executable, str(DEPLOY / "tests" / "desk_oidc_e2e.py"), "check"], capture_output=True,
                       text=True, timeout=120, cwd=DEPLOY)
    lines = [ln.strip() for ln in r.stdout.splitlines() if "PASS" in ln or "FAIL" in ln]
    ev.step("desk_oidc_e2e.py check", lines)
    fails = [ln for ln in lines if ln.startswith("FAIL")]
    assert r.returncode == 0 and not fails, f"cấu hình client xt-desk-agent: {fails or r.stderr[-300:]}"


def test_desk_container_login(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    rc, res, log = desk.run("oidc", world.by_key(A, REQUESTER), timeout=240)
    ev.step("desk_real.mjs oidc", {k: res.get(k) for k in ("checks", "claims", "http", "mcp")})
    c = Checks(ev)
    for ch in res.get("checks", []):
        c.check(ch["ok"], ch["msg"])
    spa = [h for h in res.get("http", []) if h.startswith("404 GET") and "/auth/callback" in h]
    if spa:
        c.check(False, "xt-desk (nginx) thiếu SPA fallback: GET /auth/callback -> 404 (cần try_files ... /index.html)")
    if not res:
        c.check(False, f"desk_real.mjs không chạy được: {log[-400:]}")
    c.done()
