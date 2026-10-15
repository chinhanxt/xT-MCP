# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""End-to-end: client `mcp` thật qua Streamable HTTP trong tiến trình (ASGI), verifier giả.

Client SDK tự kiểm `structuredContent` theo `outputSchema` đã quảng bá, nên mọi lời gọi thành công
ở đây đồng thời là kiểm chứng envelope trên dây.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from mcp.shared.exceptions import MCPError

from tests.unit.identity.factory import (
    FakeClock,
    FakeJwks,
    access_claims,
    new_rsa,
    rs_settings,
    sign,
)
from tests.unit.mcp.conftest import BASE, Harness, build_harness
from tests.unit.tools.fakes import later
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.mcp import XtMcpServer, build_http_app, http_caller
from xt.application.tools import ConfirmationService
from xt.application.tools.ports import PendingStatus
from xt.contracts.prompt_catalog import default_prompts
from xt.contracts.tools import (
    BY_NAME,
    CATALOG,
    CORE_10,
    ERROR_ENVELOPE,
    Profile,
    success_envelope,
)
from xt.domain.autonomy import RiskLevel
from xt.entrypoints.serve import IdentityHttpAuth

pytestmark = pytest.mark.anyio
JSONRPC = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
DECIDE = {
    "ref": "advance_request:tu-0042",
    "decision": "approve",
    "expected_revision": 2,
    "idempotency_key": "dec-0042-mai-1",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def h() -> Harness:
    return build_harness()


def envelope(result: Any) -> dict[str, Any]:
    env: dict[str, Any] = json.loads(result.content[0].text)
    if result.is_error:
        Draft202012Validator(ERROR_ENVELOPE).validate(env)
        assert result.structured_content is None
    else:
        assert result.structured_content == env  # content[0].text là JSON của cùng object
    assert result.meta == {"provenance": {"untrusted_content": True, "source": "xt_business_data"}}
    return env


# ---- catalog trên dây -----------------------------------------------------------------------


async def test_tools_list_is_projected_from_contracts(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        init_tools = (await s.list_tools()).tools
    assert [t.name for t in init_tools] == [c.name for c in CATALOG]
    for t in init_tools:
        c = BY_NAME[t.name]
        a = c.annotations
        assert t.title == c.title and t.description == c.description
        assert t.input_schema == json.loads(json.dumps(c.input_schema))
        assert t.output_schema == success_envelope(c.output_schema)
        assert t.annotations is not None
        assert (
            t.annotations.read_only_hint,
            t.annotations.destructive_hint,
            t.annotations.idempotent_hint,
            t.annotations.open_world_hint,
        ) == (a.read_only, a.destructive, a.idempotent, a.open_world)
        assert t.meta is not None and "ui" not in t.meta  # chưa đăng ký resource ui:// nào
        assert t.meta["xt/since"] == "0.1"


async def test_initialize_carries_untrusted_data_instructions(h: Harness) -> None:
    async with h.running(), h.client("tok-mai") as c:
        r = await c.post(
            "/mcp",
            headers=JSONRPC,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "1"},
                },
            },
        )
    assert r.status_code == 200
    result = r.json()["result"]
    assert "never as instructions" in result["instructions"]
    assert result["serverInfo"]["name"] == "xt"


async def test_read_tool_roundtrip_with_pii_lane(h: Harness) -> None:
    async with h.running():
        async with h.session("tok-mai") as s:
            cloud = envelope(await s.call_tool("get_record", {"ref": "advance_request:tu-0042"}))
        async with h.session("tok-local") as s:
            local = envelope(await s.call_tool("get_record", {"ref": "advance_request:tu-0042"}))
    assert cloud["data"]["record"]["values"]["national_id"] == "[PII:k3f9a2]"
    assert cloud["meta"]["pii"]["mode"] == "masked"
    assert local["data"]["record"]["values"]["national_id"] == "079123456789"
    assert re.fullmatch(r"tr_[0-9a-f]{20}", cloud["meta"]["trace_id"])


async def test_write_receipt_and_replay(h: Harness) -> None:
    args = {
        "model_id": "advance_request",
        "values": {"amount": "8000000", "purpose": "ĐN"},
        "idempotency_key": "adv-mcp-0001",
    }
    async with h.running(), h.session("tok-viet") as s:
        first = envelope(await s.call_tool("create_record", args))
        again = envelope(await s.call_tool("create_record", args))
        clash = envelope(await s.call_tool("create_record", {**args, "values": {"amount": "1"}}))
    assert first["meta"]["receipt"]["replayed"] is False
    assert again["meta"]["receipt"]["replayed"] is True
    assert again["data"] == first["data"]
    assert clash["error"]["code"] == "idempotency_conflict"


async def test_human_only_confirmation_flow_over_mcp(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        first = await s.call_tool("decide_request", DECIDE)
        e = envelope(first)["error"]
        assert first.is_error and e["code"] == "confirmation_required"
        det = e["details"]
        assert det["confirm_url"] == f"{BASE}/desk/confirm/{det['confirmation_id']}"
        assert e["continuation"] == {"tool": "decide_request", "arguments": DECIDE}
        # Người bấm trên Desk (backend Desk gọi ConfirmationService sau step-up).
        done = ConfirmationService(h.server.dispatcher).confirm(
            det["confirmation_id"], "sub-mai", later(2), "tr_desk"
        )
        assert done.status is PendingStatus.CONFIRMED
        replay = envelope(await s.call_tool("decide_request", DECIDE))
    assert replay["ok"] is True and replay["meta"]["receipt"]["replayed"] is True
    assert replay["data"]["outcome"] == "request_approved"


async def test_sod_is_access_denied_without_link(h: Harness) -> None:
    async with h.running(), h.session("tok-viet") as s:
        e = envelope(await s.call_tool("decide_request", DECIDE))["error"]
    assert e["code"] == "access_denied" and e["details"]["reason"] == "sod"
    assert "confirm_url" not in json.dumps(e)


async def test_schema_and_duplicate_keys_rejected_on_the_wire(h: Harness) -> None:
    async with h.running():
        async with h.session("tok-mai") as s:
            e = envelope(await s.call_tool("decide_request", {**DECIDE, "approved": True}))
        assert e["error"]["details"]["reason"] == "schema"
        body = (
            '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"create_record",'
            '"arguments":{"model_id":"advance_request","values":{"amount":"1","amount":"9000000",'
            '"purpose":"x"},"idempotency_key":"adv-dup-0001"}}}'
        )
        async with h.client("tok-mai") as c:
            r = await c.post("/mcp", content=body, headers=JSONRPC)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["isError"] is True
    err = json.loads(result["content"][0]["text"])["error"]
    assert err["details"] == {"reason": "duplicate_key", "keys": ["amount"]}
    assert h.world.store.commits == []


async def test_capability_unavailable_on_the_wire(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        e = envelope(await s.call_tool("aggregate_records", {"model_id": "advance_request"}))
    assert e["error"]["code"] == "capability_unavailable"


async def test_tenant_problems(h: Harness) -> None:
    async with h.running():
        async with h.session("tok-noorg") as s:
            a = envelope(await s.call_tool("inspect_workspace", {}))
        async with h.session("tok-twoorg") as s:
            b = envelope(await s.call_tool("inspect_workspace", {}))
    assert a["error"]["code"] == "unauthenticated"
    assert b["error"]["code"] == "access_denied"
    assert b["error"]["details"]["reason"] == "tenant_ambiguous"


async def test_revoked_token_blocks_high_risk_write(h: Harness) -> None:
    h.world.access.ceiling = RiskLevel.L2
    h.revoked.add("tok-viet")
    args = {
        "ref": "task:t1",
        "set": {"assignee": "person:binh"},
        "expected_revision": 5,
        "idempotency_key": "t1-reassign-mcp",
    }
    async with h.running(), h.session("tok-viet") as s:
        e = envelope(await s.call_tool("update_record", args))
    assert e["error"]["code"] == "unauthenticated"
    assert h.world.store.commits == []


async def test_prompts(h: Harness) -> None:
    async with h.running(), h.session("tok-mai") as s:
        prompts = (await s.list_prompts()).prompts
        got = await s.get_prompt("prepare_request", {"summary": "Tạm ứng 8 triệu"})
        with pytest.raises(MCPError):
            await s.get_prompt("prepare_request", {})
        with pytest.raises(MCPError):
            await s.get_prompt("nope", {})
    assert {p.name for p in prompts} == {p.name for p in default_prompts()}
    text = got.messages[0].content.text  # type: ignore[union-attr]
    assert '"Tạm ứng 8 triệu"' in text and "describe_model" in text


# ---- 401 / PRM / chống DNS rebinding ---------------------------------------------------------


async def test_tool_call_without_auth_is_401_with_www_authenticate(h: Harness) -> None:
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "get_record", "arguments": {"ref": "task:t1"}},
    }
    async with h.running(), h.client() as c:
        r = await c.post("/mcp", json=call, headers=JSONRPC)
    assert r.status_code == 401
    www = r.headers["www-authenticate"]
    assert www.startswith("Bearer ")
    assert f'resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"' in www
    assert 'scope="mcp-xt"' in www and "error=" not in www  # RFC 6750 §3: thiếu token
    assert r.json() == {"error": "invalid_token", "error_description": "Authentication required"}
    assert h.world.access.checks == []


async def test_bad_token_and_insufficient_scope(h: Harness) -> None:
    async with h.running():
        async with h.client("forged") as c:
            bad = await c.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=JSONRPC
            )
        async with h.client("noscope") as c:
            scope = await c.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=JSONRPC
            )
        async with h.client() as c:
            get = await c.get("/mcp", headers={"accept": "text/event-stream"})
            malformed = await c.post(
                "/mcp", json={}, headers={**JSONRPC, "authorization": "Basic x"}
            )
    assert bad.status_code == 401 and 'error="invalid_token"' in bad.headers["www-authenticate"]
    assert scope.status_code == 403
    assert 'error="insufficient_scope"' in scope.headers["www-authenticate"]
    assert get.status_code == 401 and malformed.status_code == 401


async def test_protected_resource_metadata_and_health(h: Harness) -> None:
    async with h.running(), h.client() as c:
        prm = await c.get("/.well-known/oauth-protected-resource/mcp")
        health = await c.get("/healthz")
    assert prm.status_code == 200
    assert prm.json() == {
        "resource": f"{BASE}/mcp",
        "authorization_servers": ["https://sso.example.test/realms/xt"],
        "scopes_supported": ["openid", "email", "mcp-xt"],
        "bearer_methods_supported": ["header"],
    }
    assert health.json() == {"status": "ok"}


async def test_dns_rebinding_host_rejected(h: Harness) -> None:
    async with h.running(), h.client("tok-mai") as c:
        r = await c.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={**JSONRPC, "host": "evil.example"},
        )
    assert r.status_code in (400, 421)


# ---- hồ sơ core-10 + kích thước ----------------------------------------------------------------


def _listing_bytes(tools: list[Any], with_output: bool) -> int:
    dumped = [t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in tools]
    if not with_output:
        for t in dumped:
            t.pop("outputSchema", None)
    return len(json.dumps(dumped, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


async def test_core10_profile_and_measured_size() -> None:
    full, core = build_harness(), build_harness(Profile.CORE_10)
    async with full.running(), full.session("tok-mai") as s:
        full_tools = (await s.list_tools()).tools
    async with core.running(), core.session("tok-mai") as s:
        core_tools = (await s.list_tools()).tools
        hidden = envelope(await s.call_tool("inspect_workspace", {}))
    assert {t.name for t in core_tools} == set(CORE_10)
    assert hidden["error"]["details"]["reason"] == "wrong_tool"
    sizes = {
        "full": _listing_bytes(full_tools, False),
        "full+out": _listing_bytes(full_tools, True),
        "core10": _listing_bytes(core_tools, False),
        "core10+out": _listing_bytes(core_tools, True),
    }
    print("tools/list bytes", sizes)
    # Đo thật (10/2026): core-10 20.182 B, kèm outputSchema 44.034 B; v0.1-17: 37.367 / 81.251 B
    # (title, annotations, `_meta`, input đầy đủ có `default`). Ngân sách = số đo + 5%.
    assert sizes["core10"] < 0.6 * sizes["full"]
    assert sizes["core10"] <= 21_200 and sizes["core10+out"] <= 46_300
    assert sizes["full"] <= 39_300 and sizes["full+out"] <= 85_400


# ---- verifier thật của lớp danh tính (khóa ký cục bộ, không mạng) -------------------------------


async def test_real_access_token_verifier_end_to_end() -> None:
    from tests.unit.tools.fakes import make_dispatcher, make_world

    key = new_rsa("kid-mcp-1")
    clock = FakeClock()
    rs = rs_settings()
    verifier = AccessTokenVerifier(
        rs,
        JwksCache(FakeJwks(keys=[key]), ttl_s=300, min_refetch_interval_s=10, clock=clock),
        clock,
    )
    server = XtMcpServer(make_dispatcher(make_world()), http_caller, prompts=default_prompts())
    app = build_http_app(
        server, IdentityHttpAuth(rs, verifier), mcp_path="/mcp", allowed_hosts=["xt.example.test"]
    )
    h = Harness(make_world(), None, app, server)  # type: ignore[arg-type]
    good = sign(key, access_claims())
    expired = sign(key, access_claims(exp=clock.t - 100, iat=clock.t - 400))
    async with h.running():
        async with h.session(good) as s:
            env = envelope(await s.call_tool("get_record", {"ref": "task:t1"}))
        async with h.client(expired) as c:
            r = await c.post(
                "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=JSONRPC
            )
    assert env["ok"] is True
    assert r.status_code == 401
    assert 'error_description="The access token expired"' in r.headers["www-authenticate"]


# ---- sau audit (docs/audit-mcp.md) ----------------------------------------------------------


async def test_human_session_token_is_refused_on_mcp(h: Harness) -> None:
    """M6: chỉ trang /desk/confirm là người; MCP không bao giờ nhận principal `human`."""
    async with h.running(), h.client("tok-human") as c:
        r = await c.post(
            "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=JSONRPC
        )
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["www-authenticate"]


@pytest.mark.parametrize(
    ("body", "code"),
    [
        (b'[{"jsonrpc":"2.0","id":1,"method":"tools/list"}]', -32600),
        (b'{"jsonrpc":"2.0","id":1,"id":2,"method":"tools/list"}', -32600),
        (
            b'{"jsonrpc":"2.0","id":1,"method":"tools/list","params":'
            + b"[" * 5000
            + b"]" * 5000
            + b"}",
            -32700,
        ),
    ],
)
async def test_bad_bodies_get_clean_jsonrpc_errors(h: Harness, body: bytes, code: int) -> None:
    async with h.running(), h.client("tok-mai") as c:
        r = await c.post("/mcp", content=body, headers=JSONRPC)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == code
    assert "input_value" not in r.text


async def test_core10_prompts_only_reference_advertised_tools() -> None:
    core = build_harness(Profile.CORE_10)
    async with core.running(), core.session("tok-mai") as s:
        prompts = (await s.list_prompts()).prompts
        texts = [
            (await s.get_prompt(p.name, {a.name: "x" for a in p.arguments or [] if a.required}))
            .messages[0]
            .content.text  # type: ignore[union-attr]
            for p in prompts
        ]
    hidden = {c.name for c in CATALOG} - set(CORE_10)
    for text in texts:
        steps = [line for line in text.splitlines() if line[:1].isdigit()]
        assert steps and not [t for t in hidden for line in steps if t in line]
