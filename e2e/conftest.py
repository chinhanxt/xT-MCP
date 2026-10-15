# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Fixture chung của e2e: dữ liệu demo-seed, phiên Keycloak thật, MCP thật, bằng chứng mỗi kịch bản."""

from __future__ import annotations

import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from xte2e import mcpc, oidc  # noqa: E402
from xte2e.config import load_users, settings  # noqa: E402
from xte2e.report import SCENARIOS, Evidence, run_dir, write_scenario, write_summary  # noqa: E402

AGENT_CLIENT = "xt-desk-agent"
_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
_selected: list[str] = []


def scenario_of(item: pytest.Item) -> str:
    sid = getattr(item.module, "SCENARIO", None)
    return str(sid) if sid else "E?"


def pytest_runtest_setup(item: pytest.Item) -> None:
    sid = scenario_of(item)
    if sid not in _selected:
        _selected.append(sid)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):  # type: ignore[no-untyped-def]
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" or (rep.when == "setup" and rep.outcome != "passed"):
        ev: Evidence | None = getattr(item, "_xt_ev", None)
        msg = ""
        if rep.outcome != "passed":
            msg = str(
                getattr(rep.longrepr, "reprcrash", None)
                and rep.longrepr.reprcrash.message
                or rep.longreprtext
            )  # type: ignore[union-attr]
        _results[scenario_of(item)].append(
            {
                "test": item.name,
                "outcome": rep.outcome,
                "duration": rep.duration,
                "message": msg,
                "steps": ev.steps if ev else [],
                "gaps": ev.gaps if ev else [],
            }
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    d = run_dir()
    for sid, res in _results.items():
        write_scenario(d, sid, res)
    text = write_summary(d, _results, _selected)
    print("\n" + text)


@pytest.fixture
def ev(request: pytest.FixtureRequest) -> Evidence:
    e = Evidence(scenario_of(request.node), request.node.name)
    request.node._xt_ev = e
    return e


# ---------------------------------------------------------------------------- world ----
class World:
    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state
        self.users: dict[str, dict[str, Any]] = state["users"]
        self.tenants: dict[str, dict[str, Any]] = state["tenants"]
        self.s = settings()
        self._sessions: dict[tuple[str, str, str], oidc.Session] = {}

    # Tập người dùng đại diện (khóa nhân sự trong tệp HR)
    def by_key(self, tenant: str, key: str) -> dict[str, Any]:
        for u in self.users.values():
            if u.get("tenant") == tenant and u.get("key") == key:
                return u
        raise KeyError(f"demo-seed không có {tenant}/{key}")

    def session(
        self,
        user: dict[str, Any],
        client: str = AGENT_CLIENT,
        scope: str = "openid mcp-xt",
        redirect: str | None = None,
    ) -> oidc.Session:
        k = (user["username"], client, scope)
        if k not in self._sessions:
            self._sessions[k] = oidc.Session(
                user, client, redirect or f"{self.s.desk}/auth/callback", scope
            )
        return self._sessions[k]

    def token(self, user: dict[str, Any], **kw: Any) -> str:
        return self.session(user, **kw).access()

    def call(
        self, user: dict[str, Any], tool: str, args: dict[str, Any], **kw: Any
    ) -> dict[str, Any]:
        return mcpc.call(self.token(user, **kw), tool, args)

    # ------------------------------------------------------ /desk/confirm ----
    def confirm(
        self,
        url: str,
        approver: dict[str, Any],
        decision: str = "approve",
        *,
        with_totp: bool = True,
    ) -> dict[str, Any]:
        """Người duyệt mở confirm_url trên host Desk -> Keycloak step-up (mật khẩu + TOTP) -> trang
        xác nhận server-render -> bấm Đồng ý/Từ chối. Trả bằng chứng (không có token)."""
        b = oidc.Browser()
        cb = f"{self.s.desk}/desk/confirm/callback"
        out: dict[str, Any] = {"approver": approver["username"]}
        try:
            step = b.get(url, stop=cb)
            if (
                step.kind == "page"
                and "kc-form-login" not in step.page
                and "kc-otp-login-form" not in step.page
            ):
                out.update(stage="start", status=step.status, body=_text(step.page)[:600])
                return out
            totp_raw = approver.get("totp") if with_totp else None
            try:
                step = b.login_pages(
                    step,
                    approver["username"],
                    approver["password"],
                    totp_raw,
                    stop=cb,
                    consent=False,
                )
            except oidc.LoginError as e:
                out.update(stage="idp", error=str(e))
                return out
            r = b.c.get(step.url)
            out.update(stage="callback", status=r.status_code, body=_text(r.text)[:800])
            if r.status_code != 200:
                return out
            fs = [f for f in oidc.forms(r.text) if "/desk/confirm" in f["action"]]
            if not fs:
                out.update(stage="no_form")
                return out
            f = fs[0]
            action = (
                f["action"] if f["action"].startswith("http") else f"{self.s.desk}{f['action']}"
            )
            r2 = b.c.post(action, data={**f["inputs"], "decision": decision})
            out.update(stage="decided", status=r2.status_code, body=_text(r2.text)[:800])
            return out
        finally:
            out["trail"] = b.trail[-12:]
            b.close()


def _text(page: str) -> str:
    import re

    t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)).strip()


@pytest.fixture(scope="session")
def world() -> World:
    state = load_users()
    if not state.get("tenants") or not state.get("users"):
        pytest.fail(
            "THIẾU demo-seed: chạy `make demo-seed` trên stack minimal trước (E0)", pytrace=False
        )
    w = World(state)
    # xt-api phải trả lời qua gateway; nếu không, mọi kịch bản MCP FAIL với đúng lý do (không lỗi mơ hồ).
    from xte2e import compose, net

    with net.client() as h:
        try:
            r = h.post(
                w.s.mcp_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                headers={"Accept": "application/json, text/event-stream"},
            )
            code = r.status_code
        except Exception as e:  # noqa: BLE001
            code = f"lỗi kết nối {e}"
    if code != 401:
        last = compose.logs("xt-api", 6).strip().splitlines() or ["(không có log)"]
        pytest.fail(
            f"THIẾU xt-api chạy được trên stack (POST /mcp không token -> {code}, mong đợi 401). "
            f"Log xt-api: {' / '.join(x[-200:] for x in last[-3:])}",
            pytrace=False,
        )
    return w


@pytest.fixture(scope="session")
def uniq() -> str:
    return time.strftime("%H%M%S")


def pytest_report_header(config: pytest.Config) -> list[str]:
    s = settings()
    return [
        f"xT e2e: project={s.project} gateway={s.gateway[0]}:{s.gateway[1]} report={run_dir()}",
        f"kịch bản: {', '.join(SCENARIOS)}",
    ]
