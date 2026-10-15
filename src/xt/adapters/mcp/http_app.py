# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Streamable HTTP (stateless, JSON response) + cổng xác thực + Protected Resource Metadata.

401/403 và PRM do lớp danh tính dựng (`HttpAuth`, composition root nối với
`xt.adapters.identity`): `WWW-Authenticate: Bearer resource_metadata=…, scope=…` (RFC 9728).
Không dùng `RequireAuthMiddleware` của SDK để challenge giữ đúng một định dạng của xT.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import anyio.to_thread
from mcp.server.context import ServerRequestContext
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp, StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from xt.adapters.mcp.server import Caller, XtMcpServer
from xt.adapters.mcp.strict_body import BodyRejection, inspect_body
from xt.domain.identity import (
    IdentityError,
    IdentityProviderUnavailableError,
    InsufficientScopeError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
)

CALLER_SCOPE_KEY = "xt.caller"
DEFAULT_MAX_BODY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Challenge:
    status: int
    headers: Mapping[str, str]
    body: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RequestFacts:
    """Dữ kiện HTTP cho proof-of-possession (DPoP, RFC 9449): method, path, Authorization thô,
    mọi giá trị header `DPoP`."""

    method: str
    path: str
    authorization: str
    dpop: tuple[str, ...] = ()


class HttpAuth(Protocol):
    """Cầu nối tới lớp danh tính (`AccessTokenVerifier`, `challenge_for`, PRM)."""

    @property
    def prm_path(self) -> str: ...

    def prm_document(self) -> Mapping[str, Any]: ...

    def bearer(self, authorization: str | None) -> str | None:
        """Token từ `Bearer`/`DPoP`; None nếu thiếu header; header sai -> `InvalidTokenError`."""
        ...

    def verify(self, token: str, request: RequestFacts) -> Principal:
        """Ném `InvalidTokenError` (401), `InsufficientScopeError` (403),
        `IdentityProviderUnavailableError` (503)."""
        ...

    def challenge(self, error: IdentityError | None) -> Challenge: ...


async def _send_json(
    send: Send, status: int, body: Mapping[str, Any], headers: Mapping[str, str]
) -> None:
    raw = json.dumps(dict(body)).encode("utf-8")
    hdrs = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
    hdrs += [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()]
    hdrs.append((b"cache-control", b"no-store"))
    await send({"type": "http.response.start", "status": status, "headers": hdrs})
    await send({"type": "http.response.body", "body": raw})


class AuthGate:
    """Mọi request tới endpoint MCP (POST/GET/DELETE) phải mang Bearer hợp lệ."""

    def __init__(self, app: ASGIApp, auth: HttpAuth) -> None:
        self.app = app
        self.auth = auth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        auth = self.auth
        try:
            headers = Headers(scope=scope)
            authorization = headers.get("authorization")
            token = auth.bearer(authorization)
            if token is None:
                c = auth.challenge(None)
                await _send_json(send, c.status, c.body, c.headers)
                return
            facts = RequestFacts(
                str(scope.get("method", "")),
                str(scope.get("path", "")),
                authorization or "",
                tuple(headers.getlist("dpop")),
            )
            principal = await anyio.to_thread.run_sync(auth.verify, token, facts)
        except IdentityProviderUnavailableError:
            await _send_json(
                send,
                503,
                {
                    "error": "temporarily_unavailable",
                    "error_description": "Identity provider unavailable",
                },
                {"Retry-After": "5"},
            )
            return
        except (InvalidTokenError, InsufficientScopeError) as e:
            c = auth.challenge(e)
            await _send_json(send, c.status, c.body, c.headers)
            return
        if principal.kind is PrincipalKind.HUMAN:
            # M6: MCP là bề mặt của agent; phiên người chỉ đi qua trang /desk/confirm.
            c = auth.challenge(InvalidTokenError("client_not_allowed", "human session on MCP"))
            await _send_json(send, c.status, c.body, c.headers)
            return
        if scope.get("method") == "GET":
            # Stateless: không có session nên stream SSE GET không bao giờ mang thông điệp (L1).
            await _send_json(send, 405, {"error": "method_not_allowed"}, {"Allow": "POST, DELETE"})
            return
        inner = dict(scope)
        inner[CALLER_SCOPE_KEY] = Caller(principal, token)
        await self.app(inner, receive, send)


class DuplicateKeyGuard:
    """Đọc trọn body POST, đánh dấu `tools/call` có khóa JSON trùng, rồi phát lại cho SDK."""

    def __init__(self, app: ASGIApp, max_body_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > self.max_body_bytes:
                await _send_json(
                    send,
                    413,
                    {"error": "request_too_large", "limit_bytes": self.max_body_bytes},
                    {},
                )
                return
            chunks.append(chunk)
            more = bool(message.get("more_body", False))
        body = b"".join(chunks)
        # Parse/kiểm CPU-nặng chạy ngoài event loop (audit R2-H1).
        marked = await anyio.to_thread.run_sync(inspect_body, body)
        if isinstance(marked, BodyRejection):
            await _send_json(
                send,
                400,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": marked.code, "message": marked.message},
                },
                {},
            )
            return
        if isinstance(marked, bytes):
            body = marked
            headers = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"]
            headers.append((b"content-length", str(len(body)).encode()))
            scope = {**scope, "headers": headers}
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


async def http_caller(ctx: ServerRequestContext[Any, Any]) -> Caller | None:
    """Danh tính do `AuthGate` gắn vào scope của request HTTP hiện tại."""
    request = ctx.request
    scope = getattr(request, "scope", None)
    caller = scope.get(CALLER_SCOPE_KEY) if isinstance(scope, dict) else None
    return caller if isinstance(caller, Caller) else None


def build_http_app(
    server: XtMcpServer,
    auth: HttpAuth,
    *,
    mcp_path: str,
    allowed_hosts: list[str],
    allowed_origins: list[str] | None = None,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> Starlette:
    """App ASGI: `mcp_path` (Streamable HTTP, có auth), PRM (công khai), `/healthz`."""
    if not mcp_path.startswith("/"):
        raise ValueError("mcp_path phải bắt đầu bằng /")
    if not allowed_hosts:
        raise ValueError("allowed_hosts rỗng: phải khai Host công khai (chống DNS rebinding)")
    manager = StreamableHTTPSessionManager(
        app=server.server,
        stateless=True,  # nhiều replica sau APISIX, không cần session affinity
        json_response=True,  # không phụ thuộc buffering SSE của proxy
        security_settings=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=allowed_origins or [],
        ),
        max_request_body_size=max_body_bytes,
    )
    endpoint = AuthGate(DuplicateKeyGuard(StreamableHTTPASGIApp(manager), max_body_bytes), auth)
    prm = dict(auth.prm_document())

    async def protected_resource_metadata(request: Request) -> Response:
        return JSONResponse(prm, headers={"Cache-Control": "public, max-age=3600"})

    async def healthz(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    routes = [
        Route(mcp_path, endpoint=endpoint),
        Route(auth.prm_path, endpoint=protected_resource_metadata, methods=["GET"]),
        Route("/healthz", endpoint=healthz, methods=["GET"]),
    ]
    return Starlette(routes=routes, lifespan=lambda app: manager.run())
