# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Regression của audit lớp MCP (docs/archive/audit-mcp.md). Các lỗi đã sửa; test giữ làm hồi quy."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from decimal import Decimal
from typing import Any

import anyio
import pytest

from tests.unit.mcp.conftest import Harness, build_harness
from tests.unit.tools.fakes import agent_ctx, later, make_dispatcher, make_world
from xt.adapters.mcp import SERVER_INSTRUCTIONS, mark_duplicate_keys
from xt.application.tools import ConfirmationService
from xt.application.tools.errors import rule_violation
from xt.application.tools.ports import AccessDecision, PendingStatus
from xt.contracts.tools import BY_NAME, CATALOG, CORE_10, Profile, input_issues

JSONRPC = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
DECIDE = {
    "ref": "advance_request:tu-0042",
    "decision": "approve",
    "expected_revision": 2,
    "idempotency_key": "dec-audit-0001",
}
NATIONAL_ID = "079123456789"


def _text(result: Any) -> str:
    return str(result.content[0].text)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def h() -> Harness:
    return build_harness()


# ---- M1: JSON lồng sâu -> RecursionError thoát khỏi DuplicateKeyGuard (HTTP 500) -------------


def test_deeply_nested_body_does_not_raise() -> None:
    n = 100_000
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_record",'
        '"arguments":{"ref":' + "[" * n + "]" * n + "}}}"
    ).encode()
    assert mark_duplicate_keys(body) is None  # để SDK trả -32700 thay vì 500


# ---- M5: pattern `^...$` của Python `re.search` nhận chuỗi có "\n" cuối ----------------------


@pytest.mark.parametrize(
    ("tool", "args"),
    [
        ("get_record", {"ref": "task:t1\n"}),
        ("describe_model", {"model_id": "advance_request\n"}),
        (
            "create_record",
            {
                "model_id": "advance_request",
                "values": {"amount\n": "1", "purpose": "x"},
                "idempotency_key": "adv-audit-0001",
            },
        ),
        (
            "create_record",
            {
                "model_id": "advance_request",
                "values": {"amount": "1", "purpose": "x"},
                "idempotency_key": "adv-audit-0001\n",
            },
        ),
    ],
)
def test_identifier_patterns_reject_trailing_newline(tool: str, args: dict[str, Any]) -> None:
    assert input_issues(BY_NAME[tool], args) != []


# ---- H1: hai lời gọi đồng thời cùng key -> commit hai lần -----------------------------------


def test_concurrent_same_key_commits_once() -> None:
    w = make_world()
    d = make_dispatcher(w)
    plan_create = w.records.plan_create

    def slow(ctx: Any, args: Any) -> Any:
        time.sleep(0.2)
        return plan_create(ctx, args)

    w.records.plan_create = slow  # type: ignore[method-assign]
    args = {
        "model_id": "advance_request",
        "values": {"amount": "8000000", "purpose": "x"},
        "idempotency_key": "race-audit-0001",
    }
    out: list[Any] = []
    threads = [
        threading.Thread(target=lambda: out.append(d.call("create_record", args, agent_ctx())))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert w.store.commits == ["create"]
    assert sorted(o.envelope["meta"]["receipt"]["replayed"] for o in out) == [False, True]


# ---- M2: dữ liệu không phải JSON gốc (Decimal/date) -> MCPError thô, không envelope ----------


@pytest.mark.anyio
async def test_non_json_native_data_becomes_internal_error_envelope(h: Harness) -> None:
    def get(ctx: Any, args: Any) -> Any:
        return {
            "record": {
                "ref": "task:t1",
                "model_id": "task",
                "title": "x",
                "state": "todo",
                "revision": 1,
                "model_revision": 1,
                "values": {"amount": Decimal("1.5")},
                "authored_by_kind": "agent",
                "available_transitions": [],
            }
        }

    h.world.records.get = get  # type: ignore[method-assign]
    async with h.running(), h.session("tok-mai") as s:
        r = await s.call_tool("get_record", {"ref": "task:t1"})
    env = json.loads(_text(r))
    assert r.is_error and env["error"]["code"] == "internal_error"


# ---- M3: ngoại lệ ở tầng adapter (ngoài dispatcher) lộ str(exc) qua JSON-RPC error ----------


@pytest.mark.anyio
async def test_adapter_exception_does_not_leak_message(h: Harness) -> None:
    def boom(p: Any) -> Any:
        raise KeyError("secret-azp-map dsn=postgres://u:p@db")

    h.server._classify = boom
    async with h.running(), h.session("tok-mai") as s:
        r = await s.call_tool("get_record", {"ref": "task:t1"})
    text = _text(r)
    assert r.is_error and "secret" not in text and "postgres" not in text
    assert json.loads(text)["error"]["code"] == "internal_error"


# ---- H2: envelope lỗi không đi qua làn PII (spike-pii-lane §"Lỗi") -----------------------------


@pytest.mark.anyio
async def test_error_envelopes_go_through_pii_lane(h: Harness) -> None:
    procs = h.world.services.processes
    plan_decide = procs.plan_decide

    def leaky_plan(ctx: Any, args: Any) -> Any:
        return replace(plan_decide(ctx, args), summary=f"Duyệt (CCCD {NATIONAL_ID})")

    procs.plan_decide = leaky_plan  # type: ignore[method-assign]

    def leaky_rule(ctx: Any, args: Any) -> Any:
        raise rule_violation(
            [{"field": "national_id", "rule": "unique", "message": f"{NATIONAL_ID} đã tồn tại"}],
            "explain:audit",
        )

    h.world.services.records.plan_update = leaky_rule  # type: ignore[method-assign]
    async with h.running(), h.session("tok-mai") as s:  # client cloud
        a = await s.call_tool("decide_request", DECIDE)
        b = await s.call_tool(
            "update_record",
            {
                "ref": "advance_request:tu-0042",
                "set": {"purpose": "y"},
                "expected_revision": 2,
                "idempotency_key": "upd-audit-0001",
            },
        )
    assert NATIONAL_ID not in _text(a)
    assert NATIONAL_ID not in _text(b)


# ---- M4: ConfirmationService không kiểm lại IAM lúc thực thi --------------------------------


def test_confirm_rechecks_access_before_commit() -> None:
    w = make_world()
    d = make_dispatcher(w)
    out = d.call("decide_request", DECIDE, agent_ctx("sub-mai"))
    cid = out.envelope["error"]["details"]["confirmation_id"]
    # Quyền của agent bị thu hồi trong lúc chờ (TTL 10 phút).
    w.access.check = lambda ctx, check: AccessDecision(  # type: ignore[method-assign]
        False, "grant_revoked", "Revoked.", "explain:rev"
    )
    done = ConfirmationService(d).confirm(cid, "sub-mai", later(2), "tr_desk")
    assert done.status is not PendingStatus.CONFIRMED
    assert w.store.commits == []


# ---- L1: GET /mcp ở chế độ stateless giữ SSE rỗng vô hạn ------------------------------------


@pytest.mark.anyio
async def test_get_in_stateless_mode_is_405(h: Harness) -> None:
    status = None
    async with h.running(), h.client("tok-mai") as c:
        with anyio.move_on_after(3):
            r = await c.get("/mcp", headers={"accept": "text/event-stream"})
            status = r.status_code
    assert status == 405


# ---- L2: instructions của core-10 trỏ tới tool bị ẩn -------------------------------------------


@pytest.mark.anyio
async def test_core10_instructions_only_name_advertised_tools() -> None:
    core = build_harness(Profile.CORE_10)
    async with core.running(), core.session("tok-mai") as s:
        listed = {t.name for t in (await s.list_tools()).tools}
    assert listed == set(CORE_10)
    hidden = {c.name for c in CATALOG} - listed
    assert not [t for t in hidden if t in SERVER_INSTRUCTIONS]
