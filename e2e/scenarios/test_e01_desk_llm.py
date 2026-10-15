# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E1 (make e2e-llm): nhân viên đăng nhập Desk thật, chat "tạm ứng 8 triệu đi Đà Nẵng 3 ngày" với
qwen3.5:4b (Ollama local) -> create + submit, approval-card render; trưởng phòng /desk/confirm + TOTP,
giám đốc duyệt -> approved. Đạt khi >= 2/3 lần. Chỉ chạy khi slot heavy cho phép (Ollama)."""

from __future__ import annotations

import os

import httpx
import pytest

from xte2e import desk
from xte2e.flows import A, DEPT_HEAD, DIRECTOR, REQUESTER, err
from xte2e.report import Checks, Evidence

SCENARIO = "E1"
pytestmark = [pytest.mark.e2e, pytest.mark.llm]
PROMPT = "Tạo và gửi duyệt giấy đề nghị tạm ứng 8.000.000 đồng đi công tác Đà Nẵng 3 ngày, bắt đầu tuần sau, nhận tiền mặt."
RUNS = int(os.environ.get("XT_E2E_LLM_RUNS", "3"))


def _approve(world, ref: str, who: str, ev: Evidence) -> dict:  # type: ignore[no-untyped-def]
    u = world.by_key(A, who)
    got = world.call(u, "get_record", {"ref": ref})
    args = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": got["data"]["record"]["revision"],
        "idempotency_key": f"e2e-llm-{who}-{ref[-8:]}",
    }
    p = world.call(u, "decide_request", args)
    url = (err(p).get("details") or {}).get("confirm_url")
    if not url:
        return p
    ev.step(f"{who} xác nhận", world.confirm(url, u))
    return world.call(u, "decide_request", args)


def test_desk_llm_w1(ev: Evidence, world) -> None:  # type: ignore[no-untyped-def]
    try:
        tags = httpx.get("http://127.0.0.1:11434/api/tags", timeout=5).json()
    except httpx.HTTPError as e:
        pytest.fail(f"THIẾU Ollama local (127.0.0.1:11434): {e}", pytrace=False)
    assert any(m["name"] == "qwen3.5:4b" for m in tags.get("models", [])), (
        "THIẾU model qwen3.5:4b trong Ollama"
    )
    wins = 0
    why: list[str] = []

    def in_review() -> set[str]:
        f = world.call(
            world.by_key(A, REQUESTER),
            "search_records",
            {"model_id": "advance_request", "where": {"state": "in_review"}},
        )
        return {i["ref"] for i in (f.get("data") or {}).get("items", [])}

    for i in range(RUNS):
        before = in_review()
        rc, res, log = desk.run("chat", world.by_key(A, REQUESTER), PROMPT, timeout=900)
        ev.step(
            f"lần {i + 1}: desk_real.mjs chat",
            {k: res.get(k) for k in ("checks", "tools", "seconds", "answer", "http")},
        )
        if rc != 0:
            why = [ch["msg"] for ch in res.get("checks", []) if not ch["ok"]][:3]
            if any("/auth/callback" in h for h in res.get("http", [])):
                why.append("xt-desk thiếu SPA fallback cho /auth/callback (404)")
            continue
        new = sorted(in_review() - before)
        if not new:
            why = ["LLM không tạo được đề nghị mới ở trạng thái in_review"]
            continue
        ref = new[0]
        _approve(world, ref, DEPT_HEAD, ev)
        final = _approve(world, ref, DIRECTOR, ev)
        ok = (final.get("data") or {}).get("request", {}).get("state") == "approved"
        ev.step(f"lần {i + 1}: kết quả duyệt", final, ok=ok)
        wins += int(ok)
    c = Checks(ev)
    c.check(
        wins * 3 >= RUNS * 2,
        f"W1 qua Desk + qwen3.5:4b đạt {wins}/{RUNS} lần (cần >= 2/3)"
        + (f"; lỗi lần cuối: {'; '.join(why)}" if why else ""),
    )
    c.done()
