# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Bind each private Studio request to an active xT identity, never browser cookies."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response
from starlette.routing import Route

from xt.adapters.identity import (
    DpopContext,
    build_revocation_guard,
    extract_credentials,
    load_introspection_settings,
)
from xt.application.tools.context import Actor, CallContext, ClientClass
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import IdentityError, InvalidTokenError
from xt.entrypoints.composition.runtime import Caller, CallerError
from xt.entrypoints.desk_llm_http import MAX_SECONDS, RelayResponse, error
from xt.entrypoints.studio_tools import StudioTools

MAX_BODY = 8 * 1024 * 1024
PATH = re.compile(r"[a-zA-Z0-9_-]+(?:/[a-zA-Z0-9_-]+)*\Z")


def assertion(
    secret: bytes,
    caller: Caller,
    method: str,
    path: str,
    body: bytes,
    capability: str = "",
    request_id: str = "",
) -> dict[str, str]:
    payload = {
        "v": 1,
        "tenant": str(caller.tenant.id),
        "person": str(caller.person.id),
        "name": caller.person.name,
        "workspace_name": caller.alias,
        "roles": list(caller.person.roles),
        "method": method,
        "path": path,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": int(time.time()),
        "nonce": str(uuid.uuid4()),
        "capability": capability,
        "request_id": request_id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    ).rstrip(b"=")
    return {
        "x-xt-assertion": encoded.decode(),
        "x-xt-signature": hmac.new(secret, encoded, hashlib.sha256).hexdigest(),
    }


class StudioProxy:
    def __init__(
        self,
        authorize: Callable[[Request], Caller],
        upstream: str,
        secret: bytes,
        client: httpx.AsyncClient,
        tools: StudioTools | None = None,
    ) -> None:
        url = urlsplit(upstream)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.path not in ("", "/")
            or url.query
            or url.fragment
        ):
            raise ValueError("XT_STUDIO_URL must be a fixed HTTP(S) origin")
        if len(secret) < 32:
            raise ValueError("XT_STUDIO_SECRET_FILE requires at least 32 bytes")
        self.authorize, self.upstream = authorize, upstream.rstrip("/")
        self.secret, self.client = secret, client
        self.tools = tools
        self.slots = anyio.Semaphore(4)

    async def endpoint(self, request: Request) -> Response:
        # Generated here, never taken from the browser, so logs on both sides correlate.
        request_id = uuid.uuid4().hex
        request.state.request_id = request_id
        response = await self._relay(request, request_id)
        response.headers["x-request-id"] = request_id
        return response

    async def _relay(self, request: Request, request_id: str) -> Response:
        try:
            caller = await anyio.to_thread.run_sync(self.authorize, request)
        except (IdentityError, CallerError, PermissionError):
            return error("unauthenticated", 401)
        path = request.path_params["path"]
        if not PATH.fullmatch(path):
            return error("invalid_path", 400)
        query = request.scope.get("query_string", b"")
        if len(query) > 4096:
            return error("invalid_query", 400)
        try:
            target = "/api/" + path + ("?" + query.decode("ascii") if query else "")
        except UnicodeDecodeError:
            return error("invalid_query", 400)
        body = bytearray()
        try:
            with anyio.fail_after(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        return error("request_too_large", 413)
                    body.extend(chunk)
        except TimeoutError:
            return error("request_timeout", 408)
        except ClientDisconnect:
            return error("request_disconnected", 400)
        media = request.headers.get("content-type", "").split(";", 1)[0].strip()
        if body and media != "application/json":
            return error("unsupported_media_type", 415)
        try:
            self.slots.acquire_nowait()
        except anyio.WouldBlock:
            return error("busy", 429)
        upstream: httpx.Response | None = None
        transferred = False
        deadline = anyio.current_time() + MAX_SECONDS
        capability = (
            self.tools.register(request.state.studio_context) if self.tools is not None else ""
        )
        try:
            headers = assertion(
                self.secret, caller, request.method, target, bytes(body), capability, request_id
            )
            if media:
                headers["content-type"] = media
            req = self.client.build_request(
                request.method, self.upstream + target, headers=headers, content=bytes(body)
            )
            with anyio.fail_after(MAX_SECONDS):
                upstream = await self.client.send(req, stream=True, follow_redirects=False)
            content_type = upstream.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type not in (
                "application/json",
                "application/x-ndjson",
                "text/event-stream",
            ):
                return error("studio_unavailable", 502)
            if 300 <= upstream.status_code < 400:
                return error("studio_unavailable", 502)
            response = StudioResponse(
                upstream, self.slots, content_type, deadline, self.tools, capability
            )
            response.status_code = upstream.status_code
            transferred = True
            return response
        except (httpx.HTTPError, TimeoutError):
            return error("studio_unavailable", 502)
        finally:
            if not transferred:
                if self.tools is not None:
                    self.tools.release(capability)
                with anyio.CancelScope(shield=True):
                    try:
                        if upstream is not None:
                            await upstream.aclose()
                    finally:
                        self.slots.release()


class StudioResponse(RelayResponse):
    def __init__(
        self,
        upstream: Any,
        slots: Any,
        media: str,
        deadline: float,
        tools: StudioTools | None,
        capability: str,
    ) -> None:
        super().__init__(upstream, slots, media, deadline)
        self.tools, self.capability = tools, capability

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            if self.tools is not None:
                self.tools.release(self.capability)


def with_studio(prepared: Any, env: Mapping[str, str], app: Starlette) -> Starlette:
    upstream = env.get("XT_STUDIO_URL", "").strip()
    if not upstream:
        return app
    secret = Path(env["XT_STUDIO_SECRET_FILE"]).read_bytes()
    desk_origin = env.get("XT_DESK_URL", "").rstrip("/")
    if not desk_origin:
        raise ValueError("XT_DESK_URL is required for Studio")
    client_id = env.get("XT_STUDIO_CLIENT_ID", "xt-desk-agent")
    revocation = build_revocation_guard(load_introspection_settings(env))

    def authorize(request: Request) -> Caller:
        if request.headers.get("origin") not in (None, desk_origin):
            raise PermissionError("origin")
        if len(request.headers.getlist("authorization")) != 1:
            raise PermissionError("authorization")
        credentials = extract_credentials(request.headers.get("authorization"))
        if credentials is None:
            raise PermissionError("authorization")
        scheme, token = credentials
        principal = prepared.verifier.verify(
            token,
            dpop=DpopContext(
                scheme=scheme,
                proofs=tuple(request.headers.getlist("dpop")),
                method=request.method,
                htu=prepared.dpop.candidates(request.url.path) if prepared.dpop else frozenset(),
            ),
        )
        if principal.client_id != client_id or principal.tenant is None:
            raise PermissionError("client or tenant")
        revocation.ensure_active(principal, token, RiskLevel.L2)

        def active(level: RiskLevel) -> None:
            if principal.expires_at <= time.time():
                raise InvalidTokenError("expired Studio delegation")
            revocation.ensure_active(principal, token, level)

        request.state.studio_context = CallContext(
            actor=Actor.from_principal(principal, principal.tenant),
            client_class=ClientClass.CLOUD,
            trace_id=request.state.request_id,
            now=datetime.now(UTC),
            ensure_active=active,
        )
        return prepared.services.runtime.resolve(  # type: ignore[no-any-return]
            Actor.from_principal(principal, principal.tenant), request.state.request_id
        )

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(MAX_SECONDS, connect=5), follow_redirects=False, trust_env=False
    )
    tools = StudioTools(prepared.dispatcher, secret)
    proxy = StudioProxy(authorize, upstream, secret, client, tools)
    prior = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[Any]:
        try:
            async with prior(application) as state:
                yield state
        finally:
            await client.aclose()

    app.router.lifespan_context = lifespan
    app.router.routes.insert(
        0,
        Route(
            "/studio-api/{path:path}",
            proxy.endpoint,
            methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        ),
    )
    app.router.routes.insert(0, Route("/studio-internal/tools", tools.endpoint, methods=["POST"]))
    return app
