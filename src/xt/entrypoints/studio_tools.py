# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ephemeral per-request Studio tool delegation; xT tokens never leave this process."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse

from xt.application.tools.context import CallContext
from xt.contracts.tools import BY_NAME, loads_strict
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import IdentityError
from xt.entrypoints.desk_llm_http import error

TOOLS = frozenset(
    {
        "inspect_workspace",
        "describe_model",
        "describe_process",
        "search_records",
        "get_record",
        "aggregate_records",
        "get_history",
        "get_inbox",
        "create_record",
        "update_record",
        "submit_request",
    }
)


class StudioTools:
    def __init__(self, dispatcher: Any, secret: bytes) -> None:
        self.dispatcher, self.secret = dispatcher, secret
        self.active: dict[str, tuple[CallContext, float, int]] = {}

    def register(self, context: CallContext) -> str:
        key = secrets.token_urlsafe(32)
        self.active[key] = context, time.monotonic() + 420, 0
        return key

    def release(self, key: str) -> None:
        self.active.pop(key, None)

    async def endpoint(self, request: Request) -> JSONResponse:
        key = request.headers.get("x-xt-capability", "")
        active = self.active.get(key)
        if active is None or active[1] < time.monotonic():
            self.release(key)
            return error("unauthenticated", 401)
        context, expiry, calls = active
        if calls >= 64:
            return error("tool_limit", 429)
        body = bytearray()
        try:
            with anyio.fail_after(5):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > 32768:
                        return error("request_too_large", 413)
                    body.extend(chunk)
        except TimeoutError:
            return error("request_timeout", 408)
        expected = hmac.new(self.secret, key.encode() + b"\n" + body, hashlib.sha256).hexdigest()
        signature = request.headers.get("x-xt-signature", "")
        if not hmac.compare_digest(expected, signature):
            return error("unauthenticated", 401)
        # Reserve without awaiting, bounding parallel calls and expired request lifetimes.
        active = self.active.get(key)
        if active is None or active[1] < time.monotonic():
            return error("unauthenticated", 401)
        if active[2] >= 64:
            return error("tool_limit", 429)
        self.active[key] = context, expiry, active[2] + 1
        try:
            payload = loads_strict(bytes(body).decode())
            if not isinstance(payload, dict):
                return error("invalid_request", 400)
            await anyio.to_thread.run_sync(context.ensure_active, RiskLevel.L2)
            if key not in self.active or expiry < time.monotonic():
                return error("unauthenticated", 401)
            if payload == {"action": "list"}:
                return JSONResponse(
                    {
                        "tools": [
                            {
                                "name": name,
                                "description": BY_NAME[name].description,
                                "input_schema": BY_NAME[name].input_schema,
                            }
                            for name in sorted(TOOLS)
                            if name in BY_NAME
                        ]
                    },
                    headers={"Cache-Control": "no-store"},
                )
            if (
                set(payload) != {"name", "arguments"}
                or not isinstance(payload.get("name"), str)
                or payload["name"] not in TOOLS
            ):
                return error("access_denied", 403)
            if not isinstance(payload["arguments"], dict):
                return error("invalid_request", 400)
            current = replace(context, now=datetime.now(UTC))
            outcome = await anyio.to_thread.run_sync(
                lambda: self.dispatcher.call(payload["name"], payload["arguments"], current)
            )
            return JSONResponse(outcome.envelope, headers={"Cache-Control": "no-store"})
        except IdentityError:
            return error("unauthenticated", 401)
        except (ValueError, UnicodeError):
            return error("invalid_request", 400)
