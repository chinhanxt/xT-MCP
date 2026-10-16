# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Local signed ID tokens exercise the user-only Telegram linking boundary without network."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import pytest
from starlette.requests import Request

from tests.unit.identity.factory import FakeClock, FakeJwks, SigningKey, new_rsa, sign, su_settings
from tests.unit.identity.test_step_up import id_claims
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.step_up import StepUpVerifier
from xt.domain.model.context import TenantRef
from xt.entrypoints import telegram_link as module
from xt.entrypoints.composition.confirm_web import ConfirmSettings


class Sessions:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, Any]] = {}

    def save(self, state: str, data: dict[str, Any], expiry: datetime) -> None:
        self.data[state] = dict(data)

    def get(self, state: str) -> dict[str, Any] | None:
        return self.data.get(state)

    def delete(self, state: str) -> None:
        self.data.pop(state, None)


def request(query: dict[str, str] | None = None, cookie: str | None = None) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/desk/telegram/callback",
            "query_string": urlencode(query or {}).encode(),
            "headers": [(b"cookie", f"{module.COOKIE}={cookie}".encode())] if cookie else [],
            "server": ("desk.test", 443),
            "scheme": "https",
        }
    )


@dataclass
class Flow:
    pages: module.TelegramLinkPages
    sessions: Sessions
    caller: Any
    repository: Mock
    runtime: Mock
    key: SigningKey
    clock: FakeClock

    def begin(self) -> tuple[str, str, dict[str, Any]]:
        response = self.pages.begin(self.caller, request())
        assert response.status_code == 200
        payload = json.loads(response.body)
        assert set(payload) == {"authorize_url", "expires_in"}
        assert "code" not in payload and "command" not in payload
        start_url = urlsplit(payload["authorize_url"])
        assert start_url.hostname == "desk.test"
        assert start_url.path.startswith("/desk/telegram/start/")
        state = start_url.path.rsplit("/", 1)[1]
        start_request = request()
        start_request.scope["path_params"] = {"state": state}
        started = self.pages.start(start_request)
        assert started.status_code == 302
        query = parse_qs(urlsplit(started.headers["location"]).query)
        assert query["prompt"] == ["login"]
        assert query["max_age"] == ["0"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["redirect_uri"] == ["https://desk.test/desk/telegram/callback"]
        cookies = SimpleCookie()
        cookies.load(started.headers["set-cookie"])
        cookie = cookies[module.COOKIE]
        assert cookie["httponly"] and cookie["secure"] and cookie["samesite"] == "lax"
        state = query["state"][0]
        return state, cookie.value, self.sessions.data[state]


@pytest.fixture
def flow(monkeypatch: pytest.MonkeyPatch) -> Flow:
    clock, key = FakeClock(), new_rsa("telegram-link-test")
    sessions, runtime, repository = Sessions(), Mock(), Mock()
    tenant = TenantRef(1, uuid4())
    owner = SimpleNamespace(id=uuid4(), subject="user-alice", active=True)
    caller = SimpleNamespace(person=owner, tenant=tenant)
    runtime.uow.scope.side_effect = lambda actor: nullcontext()
    runtime.uow.run.side_effect = lambda fn: fn()
    runtime.store.person.return_value = owner
    repository.mint_link_code.return_value = "secret-link-code"
    monkeypatch.setattr(module, "PgConfirmationSessionStore", lambda uow: sessions)
    monkeypatch.setattr(module, "PgNotificationRepository", lambda uow, actor: repository)
    monkeypatch.setattr(
        module, "tenant_by_id", lambda db, value: tenant if value == tenant.id else None
    )
    settings = su_settings()
    jwks = JwksCache(FakeJwks([key]), ttl_s=300, min_refetch_interval_s=10, clock=clock)
    verifier = StepUpVerifier(settings, jwks, clock)
    pages = module.TelegramLinkPages(
        runtime,
        ConfirmSettings(
            "https://desk.test", "https://idp.test/auth", "https://idp.test/token", settings
        ),
        verifier_for=lambda _: verifier,
        clock=lambda: datetime.fromtimestamp(clock(), UTC),
    )
    monkeypatch.setattr(
        module.httpx, "post", lambda *a, **kw: pytest.fail("unexpected live network")
    )
    return Flow(pages, sessions, caller, repository, runtime, key, clock)


def token(flow: Flow, data: dict[str, Any], **changes: Any) -> str:
    return sign(
        flow.key,
        id_claims(nonce=data["nonce"], auth_time=int(flow.clock()), **changes),
    )


def test_delegated_initiation_returns_no_link_capability(flow: Flow) -> None:
    _, _, data = flow.begin()
    flow.repository.mint_link_code.assert_not_called()
    assert data["owner_subject"] == "user-alice"
    assert data["purpose"] == "telegram-link"
    assert len(data["nonce"]) >= 32 and len(data["verifier"]) >= 43


def test_only_fresh_owner_otp_mints_html_code_once(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, cookie, data = flow.begin()
    monkeypatch.setattr(flow.pages, "_exchange", lambda code, verifier: token(flow, data))
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert response.status_code == 200
    assert b"/link secret-link-code" in response.body
    assert "no-store" in response.headers["cache-control"]
    assert flow.repository.mint_link_code.call_count == 1
    assert state not in flow.sessions.data
    replay = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert replay.status_code == 400
    assert flow.repository.mint_link_code.call_count == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"sub": "other-user"},
        {"azp": "xt-desk-agent", "aud": "xt-desk-agent"},
        {"acr": "0"},
        {"amr": ["pwd"]},
        {"nonce": "wrong-nonce"},
        {"auth_time": 1},
    ],
)
def test_delegated_wrong_owner_or_stale_proof_cannot_link(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any]
) -> None:
    state, cookie, data = flow.begin()
    claims = id_claims(nonce=data["nonce"], auth_time=int(flow.clock()))
    claims.update(changes)
    signed = sign(flow.key, claims)
    monkeypatch.setattr(flow.pages, "_exchange", lambda code, verifier: signed)
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert response.status_code == 403
    flow.repository.mint_link_code.assert_not_called()


@pytest.mark.parametrize("cookie", [None, "another-browser"])
def test_cookie_binding_failure_never_exchanges_oidc_code(
    flow: Flow, monkeypatch: pytest.MonkeyPatch, cookie: str | None
) -> None:
    state, _, _ = flow.begin()
    exchange = Mock()
    monkeypatch.setattr(flow.pages, "_exchange", exchange)
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert response.status_code == 400
    exchange.assert_not_called()
    flow.repository.mint_link_code.assert_not_called()


def test_expired_or_deactivated_owner_cannot_link(
    flow: Flow, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, cookie, _ = flow.begin()
    flow.clock.advance(601)
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert response.status_code == 400
    state, cookie, data = flow.begin()
    monkeypatch.setattr(flow.pages, "_exchange", lambda code, verifier: token(flow, data))
    flow.runtime.store.person.return_value = None
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert response.status_code == 403
    flow.repository.mint_link_code.assert_not_called()


def test_link_capability_is_html_escaped(flow: Flow, monkeypatch: pytest.MonkeyPatch) -> None:
    state, cookie, data = flow.begin()
    flow.repository.mint_link_code.return_value = "<script>alert(1)</script>"
    monkeypatch.setattr(flow.pages, "_exchange", lambda code, verifier: token(flow, data))
    response = flow.pages.callback(request({"state": state, "code": "oidc-code"}, cookie))
    assert b"<script>" not in response.body
    assert b"&lt;script&gt;" in response.body
