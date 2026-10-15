# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Transport stdio (dev). Không có danh tính người cục bộ (CRIT 17): mỗi `tools/call` kiểm lại
access token client-credentials của một agent, đọc từ tệp (xoay token không cần restart).
Xác nhận vẫn qua `confirm_url` của Desk; không có kênh xác nhận CLI (D8)."""

from __future__ import annotations

import io
import sys
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any, cast

import anyio
import anyio.to_thread
from mcp.server.context import ServerRequestContext
from mcp.server.stdio import stdio_server

from xt.adapters.mcp.server import Caller, CallerResolver, XtMcpServer
from xt.adapters.mcp.strict_body import mark_duplicate_keys
from xt.domain.identity import IdentityError, InvalidTokenError, Principal, PrincipalKind


class _GuardedLines:
    """Dòng JSON-RPC từ stdin, đã đánh dấu khóa trùng (như `DuplicateKeyGuard` của HTTP)."""

    def __init__(self, source: anyio.AsyncFile[str]) -> None:
        self._source = source

    def __aiter__(self) -> AsyncIterator[str]:
        return self._lines()

    async def _lines(self) -> AsyncIterator[str]:
        async for line in self._source:
            marked = mark_duplicate_keys(line)
            yield marked if isinstance(marked, str) else line


def token_file_source(path: Path) -> Callable[[], str]:
    def read() -> str:
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise InvalidTokenError("token_file_unreadable", str(path)) from exc
        if not token:
            raise InvalidTokenError("missing_token")
        return token

    return read


def stdio_caller(
    token_source: Callable[[], str], verify: Callable[[str], Principal]
) -> CallerResolver:
    async def resolve(ctx: ServerRequestContext[Any, Any]) -> Caller | IdentityError | None:
        try:
            token = token_source()
            principal = await anyio.to_thread.run_sync(verify, token)
        except IdentityError as e:
            return e
        if principal.kind is PrincipalKind.HUMAN:
            return InvalidTokenError("client_not_allowed", "human session on MCP")
        return Caller(principal, token)

    return resolve


async def serve_stdio(server: XtMcpServer) -> None:
    raw = anyio.wrap_file(io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace"))
    stdin = cast("anyio.AsyncFile[str]", _GuardedLines(raw))
    async with stdio_server(stdin=stdin) as (read_stream, write_stream):
        lowlevel = server.server
        await lowlevel.run(read_stream, write_stream, lowlevel.create_initialization_options())
