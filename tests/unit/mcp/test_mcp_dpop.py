# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP trên dây /mcp: app MCP thật + `AccessTokenVerifier` thật (JWKS tĩnh, khóa cục bộ) +
`IdentityHttpAuth`, chạy với `XT_DPOP_REQUIRED` tắt và bật."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.unit.identity.dpop_client import DpopKey
from tests.unit.identity.factory import (
    AGENT,
    FakeClock,
    FakeJwks,
    SigningKey,
    access_claims,
    new_rsa,
    rs_settings,
    sign,
)
from tests.unit.mcp.conftest import BASE, MCP_URL
from tests.unit.tools.fakes import make_dispatcher, make_world
from xt.adapters.identity import AccessTokenVerifier, DpopSettings, DpopVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.mcp import XtMcpServer, build_http_app, http_caller
from xt.contracts.prompt_catalog import default_prompts
from xt.entrypoints.serve import IdentityHttpAuth

DESK = "xt-desk-agent"
DESK_ORIGIN = "https://desk.example.test"  # Desk gọi /mcp cùng origin (APISIX passthrough)
JSONRPC = {"accept": "application/json, text/event-stream", "content-type": "application/json"}
LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Env:
    def __init__(self, required: bool) -> None:
        self.clock = FakeClock()
        self.key: SigningKey = new_rsa("kid-dpop-1")
        rs = rs_settings(allowed_clients=frozenset({AGENT, DESK}))
        self.dpop = DpopVerifier(
            DpopSettings(required=required, origins=("https://xt.example.test", DESK_ORIGIN)),
            clock=self.clock,
        )
        jwks = JwksCache(
            FakeJwks(keys=[self.key]), ttl_s=300, min_refetch_interval_s=10, clock=self.clock
        )
        verifier = AccessTokenVerifier(rs, jwks, self.clock, dpop=self.dpop)
        server = XtMcpServer(make_dispatcher(make_world()), http_caller, prompts=default_prompts())
        self.app = build_http_app(
            server,
            IdentityHttpAuth(rs, verifier, self.dpop),
            mcp_path="/mcp",
            allowed_hosts=["xt.example.test"],
        )

    def bound(self, holder: DpopKey) -> str:
        return sign(self.key, access_claims(azp=DESK, typ="DPoP", cnf={"jkt": holder.jkt}))

    def bearer(self, azp: str = DESK) -> str:
        return sign(self.key, access_claims(azp=azp))

    def http(self, **kw: Any) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app), base_url=BASE, timeout=30, **kw
        )

    @asynccontextmanager
    async def dpop_session(
        self, token: str, holder: DpopKey, origin: str = BASE
    ) -> AsyncIterator[ClientSession]:
        """Như `desk/src/mcp.ts`: mỗi request một proof mới (jti mới), `Authorization: DPoP`."""

        async def add_proof(request: httpx2.Request) -> None:
            htu = origin + request.url.path
            request.headers["Authorization"] = f"DPoP {token}"
            request.headers["DPoP"] = holder.proof(request.method, htu, token=token)

        async with (
            self.http(event_hooks={"request": [add_proof]}) as http,
            streamable_http_client(MCP_URL, http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            yield s


@pytest.fixture(params=[False, True], ids=["flag-off", "flag-on"])
def env(request: pytest.FixtureRequest) -> Env:
    return Env(bool(request.param))


@asynccontextmanager
async def running(env: Env) -> AsyncIterator[None]:
    async with env.app.router.lifespan_context(env.app):
        yield


async def test_bound_token_session_calls_tools(env: Env) -> None:
    holder = DpopKey(env.clock)
    async with running(env), env.dpop_session(env.bound(holder), holder) as s:
        r = await s.call_tool("get_record", {"ref": "task:t1"})
    assert json.loads(r.content[0].text)["ok"] is True  # type: ignore[union-attr]


async def test_proof_htu_on_desk_origin_accepted(env: Env) -> None:
    holder = DpopKey(env.clock)
    async with running(env), env.dpop_session(env.bound(holder), holder, DESK_ORIGIN) as s:
        assert (await s.list_tools()).tools


async def _post(env: Env, headers: dict[str, str]) -> httpx2.Response:
    async with env.http() as c:
        return await c.post("/mcp", json=LIST, headers={**JSONRPC, **headers})


async def test_replayed_proof_gets_dpop_challenge(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    hdr = {"Authorization": f"DPoP {tok}", "DPoP": holder.proof("POST", MCP_URL, token=tok)}
    async with running(env):
        first = await _post(env, hdr)
        again = await _post(env, hdr)
    assert first.status_code == 200
    assert again.status_code == 401
    www = again.headers["www-authenticate"]
    assert www.startswith('DPoP error="invalid_dpop_proof"') and 'algs="ES256"' in www
    assert again.json()["error"] == "invalid_dpop_proof"


async def test_stolen_bound_token_is_useless_without_the_key(env: Env) -> None:
    holder, thief = DpopKey(env.clock), DpopKey(env.clock)
    tok = env.bound(holder)
    async with running(env):
        as_bearer = await _post(env, {"Authorization": f"Bearer {tok}"})
        own_key = await _post(
            env,
            {"Authorization": f"DPoP {tok}", "DPoP": thief.proof("POST", MCP_URL, token=tok)},
        )
        no_proof = await _post(env, {"Authorization": f"DPoP {tok}"})
    for r in (as_bearer, own_key, no_proof):
        assert r.status_code == 401
        assert r.headers["www-authenticate"].startswith("DPoP ")


async def test_proof_for_other_method_or_path_rejected(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    async with running(env):
        wrong_m = await _post(
            env, {"Authorization": f"DPoP {tok}", "DPoP": holder.proof("GET", MCP_URL, token=tok)}
        )
        wrong_u = await _post(
            env,
            {
                "Authorization": f"DPoP {tok}",
                "DPoP": holder.proof("POST", f"{BASE}/pii/reveal", token=tok),
            },
        )
    assert wrong_m.status_code == wrong_u.status_code == 401


async def test_flag_decides_unbound_desk_bearer(env: Env) -> None:
    async with running(env):
        desk = await _post(env, {"Authorization": f"Bearer {env.bearer()}"})
        other = await _post(env, {"Authorization": f"Bearer {env.bearer(AGENT)}"})
    assert other.status_code == 200
    if env.dpop.settings.required:
        assert desk.status_code == 401
        assert desk.headers["www-authenticate"].startswith('DPoP error="invalid_token"')
    else:
        assert desk.status_code == 200
