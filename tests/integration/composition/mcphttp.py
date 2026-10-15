# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Chạy app Starlette của MCP trên uvicorn thật (thread, cổng ngẫu nhiên) + client `mcp`."""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class RunningApp:
    server: uvicorn.Server
    thread: threading.Thread
    port: int

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def mcp_url(self) -> str:
        return f"{self.base}/mcp"

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    @asynccontextmanager
    async def session(self, token: str) -> AsyncIterator[ClientSession]:
        async with (
            httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=30) as http,
            streamable_http_client(self.mcp_url, http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            yield s


def serve(app: Starlette, port: int) -> RunningApp:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    th = threading.Thread(target=server.run, daemon=True, name="it-mcp")
    th.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not th.is_alive():
            raise RuntimeError("uvicorn không lên")
        time.sleep(0.05)
    return RunningApp(server, th, port)


def envelope(result: Any) -> dict[str, Any]:
    """Envelope JSON của một kết quả tool (`content[0].text`)."""
    env: dict[str, Any] = json.loads(result.content[0].text)
    return env
