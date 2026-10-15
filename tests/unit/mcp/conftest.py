# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dựng app MCP thật (SDK `mcp`, Streamable HTTP) trong tiến trình, với port giả và verifier giả.

401/PRM đi qua `IdentityHttpAuth` thật của composition root (API public của lớp danh tính).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from tests.unit.identity.factory import NOW as TOKEN_NOW
from tests.unit.identity.factory import RESOURCE, rs_settings
from tests.unit.tools.fakes import NOW, World, make_dispatcher, make_world
from xt.adapters.mcp import ClientClassifier, XtMcpServer, build_http_app, http_caller
from xt.contracts.prompt_catalog import default_prompts
from xt.contracts.tools import Profile
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import (
    InsufficientScopeError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
)
from xt.entrypoints.serve import IdentityHttpAuth

BASE = "https://xt.example.test"
MCP_URL = f"{BASE}/mcp"
assert RESOURCE == MCP_URL


def principal(sub: str = "sub-mai", client: str = "claude-connector", **over: Any) -> Principal:
    base: dict[str, Any] = {
        "subject": sub,
        "kind": PrincipalKind.AGENT,
        "client_id": client,
        "on_behalf_of": sub,
        "issuer": "https://sso.example.test/realms/xt",
        "username": sub,
        "organizations": ("acme",),
        "roles": frozenset(),
        "scopes": frozenset({"openid", "mcp-xt"}),
        "auth_time": TOKEN_NOW - 20,
        "acr": "1",
        "amr": (),
        "token_id": "tok-1",
        "issued_at": TOKEN_NOW - 10,
        "expires_at": TOKEN_NOW + 290,
    }
    base.update(over)
    return Principal(**base)


@dataclass
class FakeVerifier:
    """Token -> Principal; token lạ = 401; `noscope` = 403 insufficient_scope."""

    tokens: dict[str, Principal] = field(default_factory=dict)
    seen: list[str] = field(default_factory=list)

    def verify(self, token: str, required_scopes: frozenset[str] = frozenset()) -> Principal:
        self.seen.append(token)
        if token == "noscope":
            raise InsufficientScopeError({"mcp-xt"}, {"openid"})
        p = self.tokens.get(token)
        if p is None:
            raise InvalidTokenError("bad_signature")
        return p


@dataclass
class Harness:
    world: World
    verifier: FakeVerifier
    app: Starlette
    server: XtMcpServer
    revoked: set[str] = field(default_factory=set)

    def client(self, token: str | None = None) -> httpx2.AsyncClient:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=self.app),
            base_url=BASE,
            headers=headers,
            timeout=30,
        )

    @asynccontextmanager
    async def running(self) -> AsyncIterator[None]:
        async with self.app.router.lifespan_context(self.app):
            yield

    @asynccontextmanager
    async def session(self, token: str) -> AsyncIterator[ClientSession]:
        async with (
            self.client(token) as http,
            streamable_http_client(MCP_URL, http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            yield s


def build_harness(
    profile: Profile = Profile.FULL, clock: Callable[[], Any] = lambda: NOW
) -> Harness:
    world = make_world()
    world.access.ceiling = RiskLevel.L1
    verifier = FakeVerifier(
        {
            "tok-mai": principal("sub-mai"),
            "tok-viet": principal("sub-viet"),
            "tok-local": principal("sub-mai", client="goose-local"),
            "tok-noorg": principal("sub-mai", organizations=()),
            "tok-twoorg": principal("sub-mai", organizations=("a", "b")),
            "tok-human": principal(
                "sub-mai", client="xt-desk", kind=PrincipalKind.HUMAN, on_behalf_of=None
            ),
        }
    )
    revoked: set[str] = set()

    def ensure_active(p: Principal, token: str, level: RiskLevel) -> None:
        if token in revoked:
            raise InvalidTokenError("token_revoked")

    server = XtMcpServer(
        make_dispatcher(world),
        http_caller,
        prompts=default_prompts(),
        profile=profile,
        classify=ClientClassifier(frozenset({"goose-local"})),
        ensure_active=ensure_active,
        clock=clock,
    )
    app = build_http_app(
        server,
        IdentityHttpAuth(rs_settings(), verifier),
        mcp_path="/mcp",
        allowed_hosts=["xt.example.test"],
    )
    return Harness(world, verifier, app, server, revoked)
