# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Client MCP thật (thư viện `mcp`, Streamable HTTP) tới `https://<xt-mcp>/mcp` qua APISIX."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from xte2e import net
from xte2e.config import settings


@asynccontextmanager
async def session(token: str, url: str | None = None) -> AsyncIterator[ClientSession]:
    async with (
        httpx2.AsyncClient(
            transport=net.AsyncMapped2(), headers={"Authorization": f"Bearer {token}"}, timeout=60
        ) as http,
        streamable_http_client(url or settings().mcp_url, http_client=http) as (read, write),
        ClientSession(read, write) as s,
    ):
        await s.initialize()
        yield s


def envelope(result: Any) -> dict[str, Any]:
    """Envelope JSON của xT trong `content[0].text` (kèm `_meta`/`isError` để làm bằng chứng)."""
    text = result.content[0].text if result.content else "{}"
    try:
        env: dict[str, Any] = json.loads(text)
    except json.JSONDecodeError:
        env = {"ok": False, "error": {"code": "non_json", "message": text[:500]}}
    if getattr(result, "meta", None):
        env.setdefault("_mcp_meta", result.meta)
    if getattr(result, "is_error", getattr(result, "isError", False)):
        env.setdefault("_mcp_is_error", True)
    return env


def call(
    token: str | Callable[[], str], tool: str, args: dict[str, Any], url: str | None = None
) -> dict[str, Any]:
    tok = token() if callable(token) else token

    async def go() -> dict[str, Any]:
        async with session(tok, url) as s:
            return envelope(await s.call_tool(tool, args))

    return anyio.run(go)


def call_raw(token: str, tool: str, args: dict[str, Any]) -> Any:
    async def go() -> Any:
        async with session(token) as s:
            return await s.call_tool(tool, args)

    return anyio.run(go)


def list_tools(token: str) -> list[Any]:
    async def go() -> list[Any]:
        async with session(token) as s:
            return list((await s.list_tools()).tools)

    return anyio.run(go)


def list_resources(token: str) -> list[Any]:
    async def go() -> list[Any]:
        async with session(token) as s:
            try:
                return list((await s.list_resources()).resources)
            except Exception as e:  # server không khai capability resources
                return [f"error: {type(e).__name__}: {e}"]

    return anyio.run(go)


def read_resource(token: str, uri: str) -> Any:
    async def go() -> Any:
        async with session(token) as s:
            return await s.read_resource(uri)  # type: ignore[arg-type]

    return anyio.run(go)
