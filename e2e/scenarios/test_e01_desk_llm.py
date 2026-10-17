# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E1 (make e2e-llm): nhân viên đăng nhập Desk thật, chat "tạm ứng 8 triệu đi Đà Nẵng 3 ngày" với
qwen3.5:4b (Ollama local) -> create + submit, approval-card render;
trưởng phòng /desk/confirm + TOTP,
giám đốc duyệt -> approved. Đạt khi >= 2/3 lần. Chỉ chạy khi slot heavy cho phép (Ollama)."""

from __future__ import annotations

import os
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from xte2e import desk
from xte2e.flows import DEPT_HEAD, DIRECTOR, REQUESTER, A, err
from xte2e.llm import ollama_address, relay_for_stack
from xte2e.report import Checks, Evidence

SCENARIO = "E1"
pytestmark = [pytest.mark.e2e, pytest.mark.llm]
PREPARED = date.today()
TRIP_START = PREPARED + timedelta(days=7 - PREPARED.weekday())
TRIP_END = TRIP_START + timedelta(days=2)
SETTLEMENT = TRIP_END + timedelta(days=7)
PROMPT = (
    "Tạo và gửi duyệt giấy đề nghị tạm ứng 8.000.000 đồng đi công tác Đà Nẵng 3 ngày, "
    f"từ {TRIP_START} đến {TRIP_END}, nhận tiền mặt. "
    f"Ngày lập {PREPARED}; thời hạn thanh toán/hoàn ứng {SETTLEMENT}."
)
RUNS = int(os.environ.get("XT_E2E_LLM_RUNS", "3"))


def _approve(world, ref: str, who: str, ev: Evidence) -> dict:  # type: ignore[no-untyped-def]
    u = world.by_key(A, who)
    inbox = world.call(u, "get_inbox", {"kinds": ["approval"]})
    ev.step(f"{who} inbox duyệt", inbox)
    item = next(
        (i for i in (inbox.get("data") or {}).get("items", []) if i.get("ref") == ref), None
    )
    assert item, f"Đề nghị {ref} không có trong inbox người duyệt {who}"
    args = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": item["revision"],
        "idempotency_key": f"e2e-llm-{who}-{ref[-8:]}",
    }
    p = world.call(u, "decide_request", args)
    ev.step(f"{who} yêu cầu duyệt", p)
    url = (err(p).get("details") or {}).get("confirm_url")
    if not url:
        assert p.get("ok"), f"Duyệt {who} bị chặn: {err(p).get('code')}"
        return p
    confirmed = world.confirm(url, u)
    ev.step(f"{who} xác nhận", confirmed)
    assert confirmed.get("stage") == "decided" and confirmed.get("status") == 200
    result = world.call(u, "decide_request", args)
    assert result.get("ok"), f"Duyệt {who} chưa hoàn tất: {err(result).get('code')}"
    return result


@pytest.fixture
def ollama_relay(ev: Evidence):  # type: ignore[no-untyped-def]
    host, port = ollama_address()
    base = f"http://{host}:{port}"
    try:
        tags = httpx.get(f"{base}/api/tags", timeout=5).json()
    except httpx.HTTPError as e:
        pytest.fail(f"THIẾU Ollama local ({host}:{port}): {e}", pytrace=False)
    assert any(m["name"] == "qwen3.5:4b" for m in tags.get("models", [])), (
        "THIẾU model qwen3.5:4b trong Ollama"
    )
    warm = httpx.post(
        f"{base}/api/generate", json={"model": "qwen3.5:4b", "keep_alive": "15m"}, timeout=120
    )
    warm.raise_for_status()
    running = httpx.get(f"{base}/api/ps", timeout=5).json()
    contexts = [
        m.get("context_length", 0) for m in running.get("models", []) if m["name"] == "qwen3.5:4b"
    ]
    assert contexts and min(contexts) >= 16384, (
        "core-10 cần Ollama context >=16384; cấu hình OLLAMA_CONTEXT_LENGTH cho runtime kiểm thử"
    )
    ev.step("Ollama thật có ngữ cảnh đủ cho core-10", {"contexts": contexts, "port": port})
    with relay_for_stack() as url:
        ev.step("relay mạng Docker riêng tới Ollama thật trên host", {"url": url})
        yield


def test_desk_llm_w1(ev: Evidence, world, ollama_relay) -> None:  # type: ignore[no-untyped-def]
    assert RUNS >= 3, "E1 cần ít nhất 3 lần chạy thật để đánh giá ngưỡng >= 2/3"
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
        rc, res, _log = desk.run("chat", world.by_key(A, REQUESTER), PROMPT, timeout=900)
        print(
            f"[E1] lượt {i + 1}/{RUNS}: rc={rc}, seconds={res.get('seconds')}, "
            f"tools={res.get('tools', [])}",
            flush=True,
        )
        ev.step(
            f"lần {i + 1}: desk_real.mjs chat",
            {
                k: res.get(k)
                for k in (
                    "checks",
                    "tools",
                    "tool_outcomes",
                    "llm",
                    "seconds",
                    "answer",
                    "http",
                    "notes",
                )
            },
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
        record = world.call(world.by_key(A, REQUESTER), "get_record", {"ref": ref})["data"][
            "record"
        ]
        values = record["values"]
        correct = (
            Decimal(str(values["amount"])) == Decimal("8000000")
            and values["destination"] == "Đà Nẵng"
            and values["prepared_on"] == str(PREPARED)
            and values["trip_start"] == str(TRIP_START)
            and values["trip_end"] == str(TRIP_END)
            and values["settlement_due"] == str(SETTLEMENT)
            and values["payout_method"] == "cash"
        )
        ev.step(f"lần {i + 1}: dữ liệu khớp yêu cầu người dùng", record, ok=correct)
        if not correct:
            why = ["Bản ghi không khớp số tiền, địa điểm, ngày hoặc hình thức nhận tiền đã yêu cầu"]
            continue
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
