# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Desk relay authentication, fixed destination, credential stripping and failure paths."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from xt.domain.identity import InvalidTokenError, Principal, PrincipalKind
from xt.entrypoints.desk_llm_http import MAX_BODY, DeskLlmProxy, with_desk_llm

PAYLOAD = {
    "model": "test-model",
    "messages": [{"role": "user", "content": "hello"}],
    "stream": True,
}


@pytest.fixture
def relay() -> Any:
    requests: list[httpx.Request] = []
    state: dict[str, Any] = {"status": 200, "type": "text/event-stream"}

    def upstream(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state.get("unavailable"):
            raise httpx.ConnectError("secret provider details", request=request)
        return httpx.Response(
            state["status"],
            headers={"content-type": state["type"], "set-cookie": "provider=secret"},
            content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
        )

    def authorize(request: Request) -> None:
        if request.headers.get("authorization") != "DPoP private-token":
            raise PermissionError("auth")

    client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    proxy = DeskLlmProxy(authorize, "http://provider/v1", "test-model", client)
    app = Starlette(routes=[Route("/llm/chat/completions", proxy.endpoint, methods=["POST"])])
    with TestClient(app) as browser:
        yield SimpleNamespace(browser=browser, requests=requests, state=state, proxy=proxy)


def post(relay: Any, **kwargs: Any) -> httpx.Response:
    kwargs.setdefault("json", PAYLOAD)
    kwargs.setdefault("headers", {"authorization": "DPoP private-token", "DPoP": "private-proof"})
    return relay.browser.post("/llm/chat/completions", **kwargs)


def test_auth_required_before_body_or_upstream(relay: Any) -> None:
    result = post(relay, headers={})
    assert result.status_code == 401
    assert not relay.requests


def test_streams_to_fixed_provider_without_user_credentials(relay: Any) -> None:
    result = post(relay)
    assert result.status_code == 200
    assert result.content.endswith(b"data: [DONE]\n\n")
    assert result.headers["cache-control"] == "no-store"
    assert "set-cookie" not in result.headers
    request = relay.requests[0]
    assert str(request.url) == "http://provider/v1/chat/completions"
    assert "authorization" not in request.headers and "dpop" not in request.headers
    assert (
        request.read()
        == httpx.Request("POST", "http://provider", json={**PAYLOAD, "max_tokens": 4096}).read()
    )


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {},
        {**PAYLOAD, "model": "other"},
        {**PAYLOAD, "messages": []},
        {**PAYLOAD, "url": "http://evil"},
        {**PAYLOAD, "max_tokens": 1000000},
        {**PAYLOAD, "max_tokens": True},
    ],
)
def test_rejects_invalid_or_arbitrary_provider_requests(relay: Any, data: Any) -> None:
    assert post(relay, json=data).status_code in (400, 415)
    assert not relay.requests


def test_body_limit_and_nonfinite_json(relay: Any) -> None:
    headers = {"authorization": "DPoP private-token", "content-type": "application/json"}
    assert post(relay, content=b"x" * (MAX_BODY + 1), json=None, headers=headers).status_code == 413
    assert post(relay, content=b'{"model":NaN}', json=None, headers=headers).status_code == 400
    assert not relay.requests


@pytest.mark.parametrize(
    ("status", "media"), [(302, "application/json"), (500, "application/json"), (200, "text/html")]
)
def test_bad_provider_response_is_generic_and_releases_slot(
    relay: Any, status: int, media: str
) -> None:
    relay.state.update(status=status, type=media)
    for _ in range(6):
        result = post(relay)
        assert result.status_code == 502
        assert result.json() == {"error": "llm_unavailable"}


def test_provider_unavailable_is_generic_and_releases_slot(relay: Any) -> None:
    relay.state["unavailable"] = True
    for _ in range(6):
        result = post(relay)
        assert result.status_code == 502
        assert "secret" not in result.text


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "http://u:p@provider/v1", "https://provider/?x=1"]
)
def test_invalid_config_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        DeskLlmProxy(lambda request: None, url, "model", Mock())


def principal() -> Principal:
    return Principal(
        subject="person",
        kind=PrincipalKind.AGENT,
        client_id="xt-desk-agent",
        on_behalf_of="person",
        issuer="https://auth",
        username=None,
        organizations=("org",),
        roles=frozenset(),
        scopes=frozenset({"mcp-xt"}),
        auth_time=None,
        acr=None,
        amr=(),
        token_id=None,
        issued_at=1,
        expires_at=9999999999,
    )


@pytest.mark.parametrize(
    "failure", ["", "client", "tenant", "revoked", "disabled", "origin", "duplicate"]
)
def test_mounted_proxy_checks_identity_and_dpop_context(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    guard = Mock()
    if failure == "revoked":
        guard.ensure_active.side_effect = InvalidTokenError("revoked")
    monkeypatch.setattr("xt.entrypoints.desk_llm_http.build_revocation_guard", lambda _: guard)
    identity = principal()
    if failure == "client":
        identity = replace(identity, client_id="untrusted")
    if failure == "tenant":
        identity = replace(identity, organizations=("a", "b"))
    runtime = Mock()
    if failure == "disabled":
        runtime.resolve.side_effect = PermissionError("disabled")
    prepared = SimpleNamespace(
        verifier=Mock(verify=Mock(return_value=identity)),
        dpop=Mock(candidates=Mock(return_value=frozenset({"https://desk/llm/chat/completions"}))),
        services=SimpleNamespace(runtime=runtime),
    )
    app = with_desk_llm(
        prepared,
        {
            "XT_DESK_LLM_URL": "http://provider/v1",
            "XT_DESK_LLM_MODEL": "test-model",
            "XT_DESK_URL": "https://desk",
            "XT_IDP_INTROSPECTION": "off",
        },
        Starlette(),
    )
    proxy = app.routes[0].endpoint.__self__
    # Exercise real relay with an HTTP transport stub after real identity wiring.
    proxy.client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "application/json"}, json={"ok": True}
            )
        )
    )
    headers = [("authorization", "DPoP token"), ("dpop", "proof"), ("origin", "https://desk")]
    if failure == "origin":
        headers[-1] = ("origin", "https://evil")
    if failure == "duplicate":
        headers.append(("authorization", "Bearer other"))
    with TestClient(app) as browser:
        response = browser.post("/llm/chat/completions", json=PAYLOAD, headers=headers)
    assert response.status_code == (401 if failure else 200)
    if not failure:
        context = prepared.verifier.verify.call_args.kwargs["dpop"]
        assert context.scheme == "dpop" and context.proofs == ("proof",)
        assert context.method == "POST"
        assert context.htu == frozenset({"https://desk/llm/chat/completions"})
        guard.ensure_active.assert_called_once()
        runtime.resolve.assert_called_once()


def test_cancel_during_provider_connect_releases_slot() -> None:
    import anyio

    async def run() -> None:
        started = anyio.Event()

        async def provider(request: httpx.Request) -> httpx.Response:
            started.set()
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            proxy = DeskLlmProxy(lambda request: None, "http://provider/v1", "test-model", client)

            async def receive() -> dict[str, Any]:
                import json

                return {
                    "type": "http.request",
                    "body": json.dumps(PAYLOAD).encode(),
                    "more_body": False,
                }

            request = Request(
                {
                    "type": "http",
                    "method": "POST",
                    "path": "/llm/chat/completions",
                    "query_string": b"",
                    "headers": [(b"content-type", b"application/json")],
                },
                receive,
            )
            async with anyio.create_task_group() as group:
                group.start_soon(proxy.endpoint, request)
                await started.wait()
                group.cancel_scope.cancel()
            assert proxy.slots.value == 4

    anyio.run(run)


def test_disconnect_before_stream_body_releases_slot() -> None:
    import anyio
    from starlette.requests import ClientDisconnect

    from xt.entrypoints.desk_llm_http import RelayResponse

    async def run() -> None:
        slots = anyio.Semaphore(1)
        await slots.acquire()
        upstream = httpx.Response(200, content=b"data: [DONE]\n\n")
        response = RelayResponse(upstream, slots, "text/event-stream", anyio.current_time() + 10)

        async def receive() -> dict[str, Any]:
            return {"type": "http.disconnect"}

        async def send(message: Any) -> None:
            raise OSError("browser disconnected before headers")

        with pytest.raises(ClientDisconnect):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert slots.value == 1
        assert upstream.is_closed

    anyio.run(run)


def test_steady_heartbeats_cannot_extend_absolute_stream_deadline() -> None:
    import anyio

    from xt.entrypoints.desk_llm_http import RelayResponse

    async def run() -> None:
        class Heartbeats(httpx.AsyncByteStream):
            async def __aiter__(self) -> Any:
                while True:
                    yield b": heartbeat\n\n"
                    await anyio.sleep(0.001)

        slots = anyio.Semaphore(1)
        await slots.acquire()
        upstream = httpx.Response(200, stream=Heartbeats())
        response = RelayResponse(upstream, slots, "text/event-stream", anyio.current_time() + 0.02)
        chunks: list[bytes] = []

        async def receive() -> dict[str, Any]:
            await anyio.sleep_forever()
            raise AssertionError("unreachable")

        async def send(message: Any) -> None:
            if message["type"] == "http.response.body":
                chunks.append(message["body"])

        with pytest.raises(TimeoutError):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
        assert chunks
        assert slots.value == 1 and upstream.is_closed

    anyio.run(run)
