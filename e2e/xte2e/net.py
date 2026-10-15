# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Ánh xạ hostname công khai -> APISIX 127.0.0.1:9080 ở tầng transport của httpx.

URL logic giữ nguyên `https://<host>/...` (cookie jar, redirect, issuer đều đúng như production);
chỉ socket được nối tới gateway với `Host` gốc. Không sửa /etc/hosts. Host không thuộc danh sách
và không phải loopback bị chặn (harness không bao giờ ra mạng thật).
"""

from __future__ import annotations

import random
from typing import Any

import httpx
import httpx2

from xte2e.config import settings

LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def run_ip() -> str:
    """IP client giả lập Cloudflare (`CF-Connecting-IP`, APISIX chỉ tin từ mạng nội bộ/loopback).

    Dùng TEST-NET-3 (203.0.113.0/24, RFC 5737) như deploy/tests; mỗi lần chạy một IP để rate-limit
    theo IP (DCR 20/giờ) của các lần chạy trước không ảnh hưởng.
    """
    return f"203.0.113.{random.randint(1, 254)}"


_IP = run_ip()


def _rewrite(mod: Any, request: Any, ip: str) -> Any:
    s = settings()
    host = request.url.host
    if host in s.hosts.values():
        gw_host, gw_port = s.gateway
        url = request.url.copy_with(scheme="http", host=gw_host, port=gw_port)
        headers = request.headers.copy()
        headers["host"] = host
        headers["cf-connecting-ip"] = ip
        headers["x-forwarded-proto"] = "https"
        return mod.Request(
            request.method, url, headers=headers, stream=request.stream, extensions=request.extensions
        )
    if host in LOOPBACK:
        return request
    raise mod.ConnectError(f"e2e: host {host!r} không được ánh xạ (chặn ra mạng thật)", request=request)


class Mapped(httpx.BaseTransport):
    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx.HTTPTransport()
        self.ip = ip or _IP

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.inner.handle_request(_rewrite(httpx, request, self.ip))

    def close(self) -> None:
        self.inner.close()


class AsyncMapped(httpx.AsyncBaseTransport):
    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx.AsyncHTTPTransport()
        self.ip = ip or _IP

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self.inner.handle_async_request(_rewrite(httpx, request, self.ip))

    async def aclose(self) -> None:
        await self.inner.aclose()


class AsyncMapped2(httpx2.AsyncBaseTransport):
    """Bản cho `httpx2` (thư viện `mcp` 2.x dùng httpx2)."""

    def __init__(self, ip: str | None = None) -> None:
        self.inner = httpx2.AsyncHTTPTransport()
        self.ip = ip or _IP

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        return await self.inner.handle_async_request(_rewrite(httpx2, request, self.ip))

    async def aclose(self) -> None:
        await self.inner.aclose()


def client(ip: str | None = None, **kw: Any) -> httpx.Client:
    kw.setdefault("timeout", 30)
    kw.setdefault("follow_redirects", False)
    return httpx.Client(transport=Mapped(ip), **kw)
