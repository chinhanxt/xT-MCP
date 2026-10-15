# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Adapter MCP: SDK `mcp` chính thức, Streamable HTTP (prod) và stdio (dev).

Chỉ module này được import `mcp`. Không import adapter khác: danh tính đi vào qua `HttpAuth`
và các callable do composition root (`xt.entrypoints.serve`) nối với `xt.adapters.identity`.
"""

from xt.adapters.mcp.http_app import (
    AuthGate,
    Challenge,
    DuplicateKeyGuard,
    HttpAuth,
    build_http_app,
    http_caller,
)
from xt.adapters.mcp.instructions import SERVER_INSTRUCTIONS, instructions_for
from xt.adapters.mcp.server import (
    Caller,
    ClientClassifier,
    UiResource,
    XtMcpServer,
    to_call_tool_result,
    tool_definition,
)
from xt.adapters.mcp.stdio import serve_stdio, stdio_caller, token_file_source
from xt.adapters.mcp.strict_body import DUPLICATE_KEYS_META, mark_duplicate_keys

__all__ = [
    "DUPLICATE_KEYS_META",
    "SERVER_INSTRUCTIONS",
    "AuthGate",
    "Caller",
    "Challenge",
    "ClientClassifier",
    "DuplicateKeyGuard",
    "HttpAuth",
    "UiResource",
    "XtMcpServer",
    "build_http_app",
    "http_caller",
    "instructions_for",
    "mark_duplicate_keys",
    "serve_stdio",
    "stdio_caller",
    "to_call_tool_result",
    "token_file_source",
    "tool_definition",
]
