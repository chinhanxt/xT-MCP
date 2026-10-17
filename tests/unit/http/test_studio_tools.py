# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Delegations are bounded, short lived, signed and use the original actor/context."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.domain.identity import InvalidTokenError, PrincipalKind
from xt.entrypoints.studio_tools import StudioTools

SECRET = b"local test secret no production use"


def test_delegated_tool_calls_cannot_change_actor_or_escape_lifetime() -> None:
    dispatcher = Mock(call=Mock(return_value=SimpleNamespace(envelope={"ok": True, "data": {}})))
    guard = Mock()
    ctx = CallContext(
        actor=Actor("tenant-a", "user", PrincipalKind.AGENT, "xt-desk-agent", "user"),
        client_class=ClientClass.CLOUD,
        trace_id="request",
        now=datetime.now(UTC),
        ensure_active=guard,
    )
    tools = StudioTools(dispatcher, SECRET)
    key = tools.register(ctx)
    app = Starlette(routes=[Route("/studio-internal/tools", tools.endpoint, methods=["POST"])])

    def signed(client: TestClient, payload: object, token: str = key):
        raw = json.dumps(payload).encode()
        signature = hmac.new(SECRET, token.encode() + b"\n" + raw, hashlib.sha256).hexdigest()
        return client.post(
            "/studio-internal/tools",
            content=raw,
            headers={
                "x-xt-capability": token,
                "x-xt-signature": signature,
            },
        )

    with TestClient(app) as client:
        assert client.post("/studio-internal/tools", json={"action": "list"}).status_code == 401
        inventory = signed(client, {"action": "list"})
        assert inventory.status_code == 200
        assert "decide_request" not in {t["name"] for t in inventory.json()["tools"]}
        args = {"model_id": "stock_balance", "limit": 20}
        assert signed(client, {"name": "search_records", "arguments": args}).json()["ok"]
        actual = dispatcher.call.call_args.args
        assert actual[:2] == ("search_records", args)
        assert actual[2].actor == ctx.actor and actual[2].client_class == ClientClass.CLOUD
        assert signed(client, {"name": "decide_request", "arguments": {}}).status_code == 403
        assert signed(client, {"name": [], "arguments": {}}).status_code == 403
        assert (
            signed(client, {"name": "get_record", "arguments": {}, "tenant": "b"}).status_code
            == 403
        )
        assert signed(client, {"action": "list"}, "unknown").status_code == 401
        guard.side_effect = InvalidTokenError("revoked")
        assert signed(client, {"action": "list"}).status_code == 401
        guard.side_effect = None
        tools.release(key)
        assert signed(client, {"action": "list"}).status_code == 401
        dispatcher.call.assert_called_once()


def test_bad_signature_expiry_and_budget_are_enforced() -> None:
    ctx = Mock()
    tools = StudioTools(Mock(), SECRET)
    key = tools.register(ctx)
    app = Starlette(routes=[Route("/tools", tools.endpoint, methods=["POST"])])
    with TestClient(app) as client:
        assert (
            client.post(
                "/tools",
                content="{}",
                headers={
                    "x-xt-capability": key,
                    "x-xt-signature": "forged",
                },
            ).status_code
            == 401
        )
        context, expiry, _ = tools.active[key]
        tools.active[key] = context, expiry, 64
        assert (
            client.post("/tools", content="{}", headers={"x-xt-capability": key}).status_code == 429
        )
        tools.active[key] = context, 0, 0
        assert (
            client.post("/tools", content="{}", headers={"x-xt-capability": key}).status_code == 401
        )
        assert key not in tools.active
