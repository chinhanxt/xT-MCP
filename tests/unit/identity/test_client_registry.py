# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Allowlist động cho connector DCR: chính sách, cache, cache âm, rate limit, fail-closed."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tests.unit.identity.factory import FakeClock, SigningKey, access_claims, rs_settings, sign
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.client_registry import (
    ConnectorAllowlist,
    KeycloakClientDirectory,
    StaticClientAllowlist,
    build_client_allowlist,
    registration_from_keycloak,
)
from xt.adapters.identity.config import (
    ConnectorRegistrySettings,
    IdentityConfigError,
    load_connector_registry_settings,
    load_resource_server_settings,
)
from xt.adapters.identity.jwks import JwksCache
from xt.domain.identity import (
    ClientRegistration,
    ConnectorPolicy,
    IdentityProviderUnavailableError,
    InvalidTokenError,
    PrincipalKind,
    connector_violation,
)

POLICY = ConnectorPolicy(redirect_hosts=frozenset({"claude.ai", "chatgpt.com", "localhost"}))
DCR = "3f1c0e7a-dcr"


def kc_rep(**over: Any) -> dict[str, Any]:
    """Hình dạng đúng của client DCR ẩn danh trên Keycloak 26.6.3 (đã đo bằng probe)."""
    rep: dict[str, Any] = {
        "clientId": DCR,
        "enabled": True,
        "publicClient": True,
        "protocol": "openid-connect",
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "bearerOnly": False,
        "fullScopeAllowed": False,
        "consentRequired": True,
        "redirectUris": ["https://claude.ai/api/mcp/auth_callback"],
        "attributes": {
            "pkce.code.challenge.method": "S256",
            "oidc.ciba.grant.enabled": "false",
            "oauth2.device.authorization.grant.enabled": "false",
            "oauth2.jwt.authorization.grant.enabled": "false",
        },
    }
    rep.update(over)
    return rep


def reg(**over: Any) -> ClientRegistration:
    return registration_from_keycloak(kc_rep(**over))


def test_compliant_dcr_client() -> None:
    assert connector_violation(reg(), POLICY) is None
    loop = reg(redirectUris=["http://localhost:33418/callback"])
    assert connector_violation(loop, POLICY) is None


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"enabled": False}, "disabled"),
        ({"protocol": "saml"}, "protocol"),
        ({"publicClient": False}, "not_public"),
        ({"bearerOnly": True}, "bearer_only"),
        ({"standardFlowEnabled": False}, "no_authorization_code"),
        ({"implicitFlowEnabled": True}, "implicit_flow"),
        ({"directAccessGrantsEnabled": True}, "password_grant"),
        ({"serviceAccountsEnabled": True}, "service_account"),
        ({"attributes": {"pkce.code.challenge.method": "S256",
                         "oauth2.device.authorization.grant.enabled": "true"}}, "extra_grant"),
        ({"attributes": {"pkce.code.challenge.method": "S256",
                         "standard.token.exchange.enabled": "true"}}, "extra_grant"),
        ({"attributes": {"pkce.code.challenge.method": "plain"}}, "pkce_not_s256"),
        ({"attributes": {}}, "pkce_not_s256"),
        ({"fullScopeAllowed": True}, "full_scope"),
        ({"fullScopeAllowed": None}, "full_scope"),
        ({"protocolMappers": [{"protocolMapper": "oidc-hardcoded-role-mapper"}]},
         "protocol_mappers"),
        ({"redirectUris": []}, "no_redirect_uri"),
        ({"redirectUris": ["https://claude.ai/*"]}, "redirect_wildcard"),
        ({"redirectUris": ["https://claude.ai/cb", "https://evil.example/cb"]}, "redirect_host"),
        ({"redirectUris": ["https://claude.ai.evil.example/cb"]}, "redirect_host"),
        ({"redirectUris": ["https://u:p@claude.ai/cb"]}, "redirect_host"),
        ({"redirectUris": ["http://claude.ai/cb"]}, "redirect_scheme"),
        ({"redirectUris": ["myapp://claude.ai/cb"]}, "redirect_scheme"),
        ({"redirectUris": "https://claude.ai/cb"}, "redirect_wildcard"),
    ],
)  # fmt: skip
def test_policy_violations(over: dict[str, Any], reason: str) -> None:
    assert connector_violation(reg(**over), POLICY) == reason


class FakeDirectory:
    def __init__(self) -> None:
        self.reps: dict[str, dict[str, Any]] = {DCR: kc_rep()}
        self.calls = 0
        self.fail = False

    def find(self, client_id: str) -> ClientRegistration | None:
        self.calls += 1
        if self.fail:
            raise IdentityProviderUnavailableError("client_registry_unavailable")
        rep = self.reps.get(client_id)
        return None if rep is None else registration_from_keycloak(rep)


def allowlist(
    d: FakeDirectory, clock: FakeClock, *, rate: int = 60, ttl: int = 60, neg: int = 30
) -> ConnectorAllowlist:
    static = StaticClientAllowlist(frozenset({"xt-desk-agent"}), frozenset({"xt-desk-web"}))
    return ConnectorAllowlist(
        static,
        d,
        POLICY,
        cache_ttl_s=ttl,
        negative_cache_ttl_s=neg,
        lookups_per_minute=rate,
        clock=clock,
    )


def no(a: ConnectorAllowlist, cid: str) -> None:
    with pytest.raises(InvalidTokenError) as e:
        a.classify(cid)
    assert e.value.reason == "client_not_allowed"


def test_static_first_party_never_hits_directory(clock: FakeClock) -> None:
    d = FakeDirectory()
    a = allowlist(d, clock)
    assert a.classify("xt-desk-agent") is PrincipalKind.AGENT
    assert a.classify("xt-desk-web") is PrincipalKind.HUMAN
    assert d.calls == 0


def test_positive_cache_and_revocation_within_ttl(clock: FakeClock) -> None:
    d = FakeDirectory()
    a = allowlist(d, clock, ttl=60)
    assert a.classify(DCR) is PrincipalKind.AGENT
    assert a.classify(DCR) is PrincipalKind.AGENT
    assert d.calls == 1
    d.reps[DCR]["enabled"] = False  # admin disable client
    clock.advance(59)
    assert a.classify(DCR) is PrincipalKind.AGENT  # còn trong TTL
    clock.advance(1)
    no(a, DCR)
    assert d.calls == 2


def test_negative_cache(clock: FakeClock) -> None:
    d = FakeDirectory()
    a = allowlist(d, clock, neg=30)
    no(a, "unknown-client")
    no(a, "unknown-client")
    assert d.calls == 1
    d.reps["unknown-client"] = kc_rep(clientId="unknown-client")
    clock.advance(30)
    assert a.classify("unknown-client") is PrincipalKind.AGENT
    assert d.calls == 2


def test_noncompliant_rejected_and_cached(clock: FakeClock) -> None:
    d = FakeDirectory()
    d.reps["cc"] = kc_rep(clientId="cc", publicClient=False, serviceAccountsEnabled=True)
    a = allowlist(d, clock)
    no(a, "cc")
    no(a, "cc")
    assert d.calls == 1


def test_rate_limit_fails_closed(clock: FakeClock) -> None:
    d = FakeDirectory()
    a = allowlist(d, clock, rate=3)
    for i in range(3):
        no(a, f"x{i}")
    with pytest.raises(IdentityProviderUnavailableError) as e:
        a.classify("x3")
    assert e.value.reason == "client_registry_rate_limited"
    clock.advance(20)  # nạp 1 lượt (3/phút)
    assert a.classify(DCR) is PrincipalKind.AGENT
    assert d.calls == 4


def test_directory_error_fails_closed_not_cached(clock: FakeClock) -> None:
    d = FakeDirectory()
    a = allowlist(d, clock, ttl=1)
    assert a.classify(DCR) is PrincipalKind.AGENT
    clock.advance(1)
    d.fail = True
    with pytest.raises(IdentityProviderUnavailableError):
        a.classify(DCR)  # hết TTL + IdP lỗi: không dùng kết quả cũ
    d.fail = False
    assert a.classify(DCR) is PrincipalKind.AGENT


@pytest.mark.parametrize("cid", ["", "x" * 129, "a\nb"])
def test_malformed_client_id(clock: FakeClock, cid: str) -> None:
    d = FakeDirectory()
    no(allowlist(d, clock), cid)
    assert d.calls == 0


def test_verifier_uses_dynamic_allowlist(
    jwks: JwksCache, clock: FakeClock, rsa_key: SigningKey
) -> None:
    d = FakeDirectory()
    settings = rs_settings(allowed_clients=frozenset(), dynamic_connectors=True)
    with pytest.raises(ValueError):
        AccessTokenVerifier(settings, jwks, clock)  # bật động mà không truyền allowlist
    v = AccessTokenVerifier(settings, jwks, clock, clients=allowlist(d, clock))
    p = v.verify(sign(rsa_key, access_claims(azp=DCR)))
    assert p.kind is PrincipalKind.AGENT and p.on_behalf_of == "user-alice"
    with pytest.raises(InvalidTokenError) as e:
        v.verify(sign(rsa_key, access_claims(azp="rogue", scope="openid")))
    assert e.value.reason == "client_not_allowed"  # 401 trước kiểm scope


# ------------------------------------------------------------------ Keycloak Admin REST (giả)

SETTINGS = ConnectorRegistrySettings(
    admin_api_url="https://kc.internal.test/admin/realms/xt",
    token_url="https://kc.internal.test/realms/xt/protocol/openid-connect/token",
    client_id="xt-client-registry",
    client_secret="s3cret",
    redirect_hosts=frozenset({"claude.ai"}),
)


class AdminApi:
    def __init__(self) -> None:
        self.tokens = 0
        self.lookups: list[httpx.Request] = []
        self.expire_next = False
        self.status = 200
        self.body: Any = [kc_rep()]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/token"):
            self.tokens += 1
            assert req.headers["Authorization"].startswith("Basic ")
            return httpx.Response(200, json={"access_token": f"t{self.tokens}", "expires_in": 300})
        self.lookups.append(req)
        if self.expire_next:
            self.expire_next = False
            return httpx.Response(401)
        return httpx.Response(self.status, content=json.dumps(self.body).encode())


def directory(api: AdminApi, clock: FakeClock) -> KeycloakClientDirectory:
    return KeycloakClientDirectory(
        SETTINGS, httpx.Client(transport=httpx.MockTransport(api)), clock=clock
    )


def test_keycloak_directory_exact_lookup(clock: FakeClock) -> None:
    api = AdminApi()
    d = directory(api, clock)
    r = d.find(DCR)
    assert r is not None and r.client_id == DCR and r.pkce_method == "S256"
    q = api.lookups[0].url.params
    assert q["clientId"] == DCR and q["search"] == "false"
    assert api.lookups[0].headers["Authorization"] == "Bearer t1"
    d.find(DCR)
    assert api.tokens == 1, "token service account được cache"
    api.body = [kc_rep(clientId=DCR + "-other")]
    assert d.find(DCR) is None, "chỉ nhận khớp chính xác"
    api.body = []
    assert d.find(DCR) is None


def test_keycloak_directory_refreshes_token_on_401(clock: FakeClock) -> None:
    api = AdminApi()
    d = directory(api, clock)
    d.find(DCR)
    api.expire_next = True
    assert d.find(DCR) is not None
    assert api.tokens == 2


@pytest.mark.parametrize(("status", "body"), [(500, {}), (403, {}), (200, {"not": "list"})])
def test_keycloak_directory_errors_fail_closed(clock: FakeClock, status: int, body: Any) -> None:
    api = AdminApi()
    api.status, api.body = status, body
    with pytest.raises(IdentityProviderUnavailableError):
        directory(api, clock).find(DCR)


# ------------------------------------------------------------------ cấu hình

REG_ENV = {
    "XT_IDP_ALLOW_HTTP": "true",
    "XT_IDP_ADMIN_API_URL": "http://keycloak:8080/admin/realms/xt/",
    "XT_IDP_ADMIN_TOKEN_URL": "http://keycloak:8080/realms/xt/protocol/openid-connect/token",
    "XT_IDP_ADMIN_CLIENT_ID": "xt-client-registry",
    "XT_IDP_ADMIN_CLIENT_SECRET": "TOPSECRET",
    "XT_MCP_CONNECTOR_REDIRECT_HOSTS": "Claude.ai, chatgpt.com",
}


def test_registry_settings() -> None:
    s = load_connector_registry_settings(REG_ENV)
    assert s.admin_api_url.endswith("/admin/realms/xt")
    assert s.redirect_hosts == {"claude.ai", "chatgpt.com"}
    assert (s.cache_ttl_s, s.negative_cache_ttl_s, s.lookups_per_minute) == (60, 30, 60)
    assert "TOPSECRET" not in repr(s)
    with pytest.raises(IdentityConfigError) as e:
        load_connector_registry_settings({**REG_ENV, "XT_MCP_CONNECTOR_REDIRECT_HOSTS": "*.ai"})
    assert any("chỉ tên host" in x for x in e.value.problems)
    with pytest.raises(IdentityConfigError) as e:
        load_connector_registry_settings({})
    assert len(e.value.problems) == 5


def test_resource_server_registry_mode() -> None:
    base = {
        "XT_IDP_ISSUER": "https://sso.test/realms/xt",
        "XT_IDP_JWKS_URL": "https://sso.test/certs",
        "XT_MCP_RESOURCE": "https://xt.test/mcp",
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "mcp-xt",
    }
    with pytest.raises(IdentityConfigError):
        load_resource_server_settings(base)  # static mà thiếu allowlist
    s = load_resource_server_settings({**base, "XT_MCP_CLIENT_REGISTRY": "keycloak"})
    assert s.dynamic_connectors and s.allowed_clients == frozenset()
    with pytest.raises(IdentityConfigError):
        load_resource_server_settings({**base, "XT_MCP_CLIENT_REGISTRY": "ldap"})
    with pytest.raises(ValueError):
        build_client_allowlist(s)
    assert isinstance(build_client_allowlist(s, SETTINGS), ConnectorAllowlist)
    static = load_resource_server_settings({**base, "XT_MCP_ALLOWED_CLIENTS": "a"})
    assert isinstance(build_client_allowlist(static), StaticClientAllowlist)
    with pytest.raises(ValueError):
        build_client_allowlist(static, SETTINGS)
    with pytest.raises(ValueError):
        StaticClientAllowlist(frozenset({"a"}), frozenset({"a"}))
