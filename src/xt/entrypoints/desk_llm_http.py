# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Authenticated, bounded Desk chat relay to a fixed operator-configured LLM endpoint."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import anyio
import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from xt.adapters.identity import (
    DpopContext,
    build_revocation_guard,
    extract_credentials,
    load_introspection_settings,
)
from xt.application.tools.context import Actor
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import IdentityError
from xt.entrypoints.composition.runtime import CallerError

MAX_BODY = 1048576
MAX_TOKENS = 4096
MAX_SECONDS = 420
HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _nonfinite(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _decode(body: bytes) -> Any:
    return json.loads(body, parse_constant=_nonfinite)


def error(code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": code}, status_code=status, headers=HEADERS)


class RelayResponse(StreamingResponse):
    def __init__(
        self, upstream: httpx.Response, slots: anyio.Semaphore, media: str, deadline: float
    ) -> None:
        super().__init__(upstream.aiter_bytes(), media_type=media, headers=HEADERS)
        self.upstream, self.slots = upstream, slots
        self.deadline = deadline

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            with anyio.fail_after(max(0, self.deadline - anyio.current_time())):
                await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    await self.upstream.aclose()
                finally:
                    self.slots.release()


class DeskLlmProxy:
    def __init__(
        self,
        authorize: Callable[[Request], None],
        upstream: str,
        model: str,
        client: httpx.AsyncClient,
    ) -> None:
        url = urlsplit(upstream)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("XT_DESK_LLM_URL must be an absolute HTTP(S) URL without credentials")
        if not model.strip():
            raise ValueError("XT_DESK_LLM_MODEL is required")
        self.authorize, self.model, self.client = authorize, model, client
        self.url = upstream.rstrip("/") + "/chat/completions"
        self.slots = anyio.Semaphore(4)

    async def endpoint(self, request: Request) -> Response:
        try:
            await anyio.to_thread.run_sync(self.authorize, request)
        except (IdentityError, CallerError, PermissionError):
            return error("unauthenticated", 401)
        if request.url.query:
            return error("invalid_request", 400)
        if request.headers.get("content-type", "").split(";", 1)[0].strip() != "application/json":
            return error("unsupported_media_type", 415)
        body = bytearray()
        try:
            with anyio.fail_after(10):
                async for chunk in request.stream():
                    if len(body) + len(chunk) > MAX_BODY:
                        return error("request_too_large", 413)
                    body.extend(chunk)
            payload = await anyio.to_thread.run_sync(_decode, bytes(body))
        except TimeoutError:
            return error("request_timeout", 408)
        except (ValueError, RecursionError):
            return error("invalid_request", 400)
        if (
            not isinstance(payload, dict)
            or payload.get("model") != self.model
            or not isinstance(payload.get("messages"), list)
            or not payload["messages"]
            or not isinstance(payload.get("stream", False), bool)
        ):
            return error("invalid_request", 400)
        # No URL, provider credential or arbitrary request headers can come from the browser.
        if set(payload) - {
            "model",
            "messages",
            "tools",
            "stream",
            "reasoning_effort",
            "temperature",
            "top_p",
            "max_tokens",
            "tool_choice",
        }:
            return error("invalid_request", 400)
        budget = payload.get("max_tokens", MAX_TOKENS)
        if isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= MAX_TOKENS:
            return error("invalid_request", 400)
        payload["max_tokens"] = budget
        try:
            self.slots.acquire_nowait()
        except anyio.WouldBlock:
            return error("busy", 429)
        upstream: httpx.Response | None = None
        transferred = False
        deadline = anyio.current_time() + MAX_SECONDS
        try:
            req = self.client.build_request(
                "POST",
                self.url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream, application/json",
                },
            )
            with anyio.fail_after(MAX_SECONDS):
                upstream = await self.client.send(req, stream=True, follow_redirects=False)
            media = upstream.headers.get("content-type", "").split(";", 1)[0].strip()
            if upstream.status_code != 200 or media not in (
                "text/event-stream",
                "application/json",
            ):
                return error("llm_unavailable", 502)
            response = RelayResponse(upstream, self.slots, media, deadline)
            transferred = True
            return response
        except (httpx.HTTPError, TimeoutError):
            return error("llm_unavailable", 502)
        finally:
            if not transferred:
                with anyio.CancelScope(shield=True):
                    try:
                        if upstream is not None:
                            await upstream.aclose()
                    finally:
                        self.slots.release()


def with_desk_llm(prepared: Any, env: Mapping[str, str], app: Starlette) -> Starlette:
    upstream = env.get("XT_DESK_LLM_URL", "").strip()
    if not upstream:
        return app
    revocation = build_revocation_guard(load_introspection_settings(env))
    desk_origin = env.get("XT_DESK_URL", "").rstrip("/")
    client_id = env.get("XT_DESK_LLM_CLIENT_ID", "xt-desk-agent")

    def authorize(request: Request) -> None:
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
        prepared.services.runtime.resolve(
            Actor.from_principal(principal, principal.tenant), uuid.uuid4().hex
        )

    client = httpx.AsyncClient(
        timeout=httpx.Timeout(420, connect=5),
        follow_redirects=False,
        trust_env=False,
    )
    proxy = DeskLlmProxy(authorize, upstream, env.get("XT_DESK_LLM_MODEL", ""), client)
    prior_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[Any]:
        try:
            async with prior_lifespan(application) as state:
                yield state
        finally:
            await client.aclose()

    app.router.lifespan_context = lifespan
    app.router.routes.insert(0, Route("/llm/chat/completions", proxy.endpoint, methods=["POST"]))
    return app
