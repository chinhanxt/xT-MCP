# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.unit.identity.factory import AGENT, NOW, FakeClock
from xt.adapters.identity.config import IntrospectionMode, IntrospectionSettings
from xt.adapters.identity.introspection import (
    KeycloakIntrospector,
    RevocationGuard,
    build_revocation_guard,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import (
    IdentityProviderUnavailableError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
    TokenRevokedError,
)

URL = "https://kc.internal.test/realms/xt/protocol/openid-connect/token/introspect"
P = Principal(
    subject="user-alice",
    kind=PrincipalKind.AGENT,
    client_id=AGENT,
    on_behalf_of="user-alice",
    issuer="iss",
    username="alice",
    organizations=("acme",),
    roles=frozenset(),
    scopes=frozenset({"mcp-xt"}),
    auth_time=None,
    acr=None,
    amr=(),
    token_id=None,
    issued_at=NOW,
    expires_at=NOW + 300,
)


class Idp:
    def __init__(self) -> None:
        self.calls = 0
        self.reply: Any = {"active": True, "sub": "user-alice", "client_id": AGENT}
        self.status = 200
        self.seen: list[httpx.Request] = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.seen.append(req)
        if isinstance(self.reply, Exception):
            raise self.reply
        return httpx.Response(self.status, content=json.dumps(self.reply).encode())


def make(idp: Idp, clock: FakeClock, ttl: float = 30) -> KeycloakIntrospector:
    client = httpx.Client(transport=httpx.MockTransport(idp))
    return KeycloakIntrospector(URL, "xt-api", "s3cret", ttl, client=client, clock=clock)


def test_active_token_passes_and_is_cached(clock: FakeClock) -> None:
    idp = Idp()
    guard = RevocationGuard(IntrospectionMode.HIGH_RISK, make(idp, clock))
    guard.ensure_active(P, "tok", RiskLevel.L2)
    guard.ensure_active(P, "tok", RiskLevel.L3)
    assert idp.calls == 1
    req = idp.seen[0]
    assert req.headers["Authorization"].startswith("Basic ")
    assert b"token=tok" in req.content and b"token_type_hint=access_token" in req.content
    clock.advance(30)
    guard.ensure_active(P, "tok", RiskLevel.HUMAN_ONLY)
    assert idp.calls == 2


def test_low_risk_skips_introspection(clock: FakeClock) -> None:
    idp = Idp()
    guard = RevocationGuard(IntrospectionMode.HIGH_RISK, make(idp, clock))
    guard.ensure_active(P, "tok", RiskLevel.L0)
    guard.ensure_active(P, "tok", RiskLevel.L1)
    assert idp.calls == 0


def test_revoked_user_rejected(clock: FakeClock) -> None:
    idp = Idp()
    idp.reply = {"active": False}
    guard = RevocationGuard(IntrospectionMode.HIGH_RISK, make(idp, clock))
    with pytest.raises(TokenRevokedError) as e:
        guard.ensure_active(P, "tok", RiskLevel.L2)
    assert e.value.reason == "token_revoked"


@pytest.mark.parametrize(
    "reply", [{"active": True, "sub": "user-mallory"}, {"active": True, "azp": "other"}]
)
def test_introspection_mismatch(clock: FakeClock, reply: dict[str, Any]) -> None:
    idp = Idp()
    idp.reply = reply
    guard = RevocationGuard(IntrospectionMode.HIGH_RISK, make(idp, clock))
    with pytest.raises(InvalidTokenError) as e:
        guard.ensure_active(P, "tok", RiskLevel.L3)
    assert e.value.reason == "introspection_mismatch"


@pytest.mark.parametrize(
    ("status", "reply"),
    [(500, {}), (200, {"active": "yes"}), (200, ["x"]), (200, httpx.ConnectError("down"))],
)
def test_idp_failure_fails_closed(clock: FakeClock, status: int, reply: Any) -> None:
    idp = Idp()
    idp.status, idp.reply = status, reply
    guard = RevocationGuard(IntrospectionMode.HIGH_RISK, make(idp, clock))
    with pytest.raises(IdentityProviderUnavailableError):
        guard.ensure_active(P, "tok", RiskLevel.L2)


def test_mode_off_never_calls() -> None:
    guard = build_revocation_guard(
        IntrospectionSettings(IntrospectionMode.OFF, None, None, None, 30, 5.0)
    )
    assert guard.mode is IntrospectionMode.OFF
    guard.ensure_active(P, "tok", RiskLevel.L3)


def test_build_high_risk(clock: FakeClock) -> None:
    idp = Idp()
    s = IntrospectionSettings(IntrospectionMode.HIGH_RISK, URL, "xt-api", "s", 0, 5.0)
    guard = build_revocation_guard(s, client=httpx.Client(transport=httpx.MockTransport(idp)))
    guard.ensure_active(P, "tok", RiskLevel.L2)
    guard.ensure_active(P, "tok", RiskLevel.L2)
    assert idp.calls == 2, "cache_ttl=0: không cache"
    with pytest.raises(ValueError):
        RevocationGuard(IntrospectionMode.HIGH_RISK, None)
