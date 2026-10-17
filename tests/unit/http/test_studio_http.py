# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""The Studio boundary strips credentials and signs only resolved xT identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.routing import Route
from starlette.testclient import TestClient

from xt.entrypoints.studio_http import MAX_BODY, StudioProxy, with_studio

SECRET = b"test-only-key-32-bytes-minimum!!!!"


@pytest.fixture
def relay() -> Any:
    caller = SimpleNamespace(
        tenant=SimpleNamespace(id=uuid4()),
        person=SimpleNamespace(id=uuid4(), name="Nhân viên", roles=["studio_editor"]),
        alias="company-a",
    )
    requests: list[httpx.Request] = []
    state: dict[str, Any] = {"status": 200, "media": "application/json"}

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if state.get("offline"):
            raise httpx.ConnectError("secret endpoint", request=request)
        return httpx.Response(
            state["status"],
            content=b'{"ok":true}',
            headers={"content-type": state["media"], "set-cookie": "studio_session=secret"},
        )

    def authorize(request: Request) -> Any:
        if request.headers.get("authorization") != "DPoP user-token":
            raise PermissionError("auth")
        return caller

    proxy = StudioProxy(
        authorize,
        "http://studio:4317",
        SECRET,
        httpx.AsyncClient(transport=httpx.MockTransport(provider)),
    )
    app = Starlette(
        routes=[
            Route(
                "/studio-api/{path:path}", proxy.endpoint, methods=["GET", "POST", "PUT", "DELETE"]
            )
        ]
    )
    with TestClient(app) as browser:
        browser.headers["authorization"] = "DPoP user-token"
        yield SimpleNamespace(
            browser=browser, requests=requests, caller=caller, state=state, proxy=proxy
        )


def test_signature_binds_identity_method_query_and_body(relay: Any) -> None:
    response = relay.browser.post(
        "/studio-api/apps?version=2",
        json={"name": "Kho"},
        headers={
            "x-xt-assertion": "forged",
            "x-xt-signature": "forged",
            "cookie": "secret=1",
            "dpop": "proof",
        },
    )
    assert response.status_code == 200
    req = relay.requests[0]
    assert str(req.url) == "http://studio:4317/api/apps?version=2"
    assert not {"authorization", "cookie", "dpop", "origin"}.intersection(req.headers)
    encoded = req.headers["x-xt-assertion"]
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert payload["tenant"] == str(relay.caller.tenant.id)
    assert payload["person"] == str(relay.caller.person.id)
    assert payload["roles"] == ["studio_editor"]
    assert payload["method"] == "POST" and payload["path"] == "/api/apps?version=2"
    assert payload["body_sha256"] == hashlib.sha256(req.content).hexdigest()
    assert (
        req.headers["x-xt-signature"]
        == hmac.new(SECRET, encoded.encode(), hashlib.sha256).hexdigest()
    )
    assert response.headers["cache-control"] == "no-store"
    assert "set-cookie" not in response.headers
    relay.browser.get("/studio-api/session")
    assert relay.requests[1].headers["x-xt-assertion"] != encoded


def test_unauthenticated_does_not_contact_upstream(relay: Any) -> None:
    assert (
        relay.browser.get("/studio-api/session", headers={"authorization": ""}).status_code == 401
    )
    assert not relay.requests


@pytest.mark.parametrize("path", ["apps//other", "apps%2F%2Fother", "apps:evil", "apps%5Cother"])
def test_rejects_noncanonical_path(relay: Any, path: str) -> None:
    assert relay.browser.get("/studio-api/" + path).status_code == 400
    assert not relay.requests


def test_bounded_body_and_media(relay: Any) -> None:
    assert relay.browser.post("/studio-api/apps", content=b"x").status_code == 415
    assert relay.browser.post("/studio-api/apps", content=b"x" * (MAX_BODY + 1)).status_code == 413
    assert not relay.requests


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 429, 500])
def test_preserves_application_errors(relay: Any, status: int) -> None:
    relay.state["status"] = status
    assert relay.browser.get("/studio-api/session").status_code == status
    assert relay.proxy.slots.value == 4


def test_fails_closed_on_redirect_html_and_network_error(relay: Any) -> None:
    relay.state["status"] = 302
    assert relay.browser.get("/studio-api/session").status_code == 502
    relay.state.update(status=200, media="text/html")
    assert relay.browser.get("/studio-api/session").status_code == 502
    relay.state["offline"] = True
    assert relay.browser.get("/studio-api/session").json() == {"error": "studio_unavailable"}
    assert relay.proxy.slots.value == 4


def test_request_id_is_returned_and_bound_into_the_assertion(relay: Any) -> None:
    ok = relay.browser.get("/studio-api/session", headers={"x-request-id": "client-chosen"})
    rid = ok.headers["x-request-id"]
    assert len(rid) == 32 and int(rid, 16) >= 0 and rid != "client-chosen"
    encoded = relay.requests[0].headers["x-xt-assertion"]
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert payload["request_id"] == rid
    denied = relay.browser.get("/studio-api/session", headers={"authorization": ""})
    assert denied.status_code == 401 and len(denied.headers["x-request-id"]) == 32
    relay.state["offline"] = True
    failed = relay.browser.get("/studio-api/session")
    assert failed.status_code == 502
    assert len({rid, denied.headers["x-request-id"], failed.headers["x-request-id"]}) == 3


def test_opt_in_and_invalid_configuration() -> None:
    app = Starlette()
    assert with_studio(None, {}, app) is app
    for url in (
        "http://user:pass@studio",
        "http://studio/api",
        "file:///tmp/a",
        "http://studio?x=1",
    ):
        with pytest.raises(ValueError):
            StudioProxy(Mock(), url, SECRET, Mock())
    with pytest.raises(ValueError):
        StudioProxy(Mock(), "http://studio", b"short", Mock())
