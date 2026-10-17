# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Mọi ca âm của VS B (`research/verification-spikes.md`) với khóa sinh cục bộ."""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.unit.identity.factory import (
    AGENT,
    ISSUER,
    NOW,
    RESOURCE,
    FakeClock,
    FakeJwks,
    SigningKey,
    access_claims,
    b64,
    new_rsa,
    raw_jws,
    rs_settings,
    sign,
)
from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.domain.identity import (
    IdentityProviderUnavailableError,
    InsufficientScopeError,
    InvalidTokenError,
    PrincipalKind,
)


@pytest.fixture
def verifier(jwks: JwksCache, clock: FakeClock) -> AccessTokenVerifier:
    return AccessTokenVerifier(rs_settings(), jwks, clock)


def reject(v: AccessTokenVerifier, token: str, reason: str) -> None:
    with pytest.raises(InvalidTokenError) as e:
        v.verify(token)
    assert e.value.reason == reason, e.value


def test_valid_rs256_maps_principal(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    p = verifier.verify(sign(rsa_key, access_claims()))
    assert p.subject == "user-alice"
    assert p.kind is PrincipalKind.AGENT and p.on_behalf_of == "user-alice"
    assert p.client_id == AGENT
    assert p.issuer == ISSUER
    assert p.username == "alice"
    assert p.tenant == "acme" and not p.tenant_ambiguous
    assert p.roles == {"employee", "default-roles-xt"}
    assert p.scopes == {"openid", "email", "mcp-xt"}
    assert p.auth_time == NOW - 20 and p.acr == "1" and p.amr == ()
    assert p.token_id == "tok-1" and p.issued_at == NOW - 10 and p.expires_at == NOW + 290
    assert p.has_scopes({"mcp-xt"}) and not p.has_scopes({"admin"})


def test_valid_es256(verifier: AccessTokenVerifier, ec_key: SigningKey) -> None:
    assert verifier.verify(sign(ec_key, access_claims())).subject == "user-alice"


def test_service_account_is_agent(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    c = access_claims(client_id=AGENT, sub="sa-1", auth_time=None, organization=None)
    p = verifier.verify(sign(rsa_key, c))
    assert p.kind is PrincipalKind.AGENT and p.tenant is None and p.auth_time is None
    assert p.on_behalf_of is None


def test_desk_session_client_is_human(
    jwks: JwksCache, clock: FakeClock, rsa_key: SigningKey
) -> None:
    v = AccessTokenVerifier(rs_settings(human_clients=frozenset({"xt-desk-web"})), jwks, clock)
    p = v.verify(sign(rsa_key, access_claims(azp="xt-desk-web")))
    assert p.kind is PrincipalKind.HUMAN and p.on_behalf_of is None
    # Client người không được dùng service account.
    sa = access_claims(azp="xt-desk-web", client_id="xt-desk-web")
    reject(v, sign(rsa_key, sa), "service_account_not_human")
    # Client ngoài hai danh sách: từ chối.
    reject(v, sign(rsa_key, access_claims(azp="xt-bot")), "client_not_allowed")


def test_org_object_form_and_ambiguous(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    p = verifier.verify(sign(rsa_key, access_claims(organization={"b": {}, "a": {"id": "1"}})))
    assert p.organizations == ("a", "b") and p.tenant is None and p.tenant_ambiguous
    reject(verifier, sign(rsa_key, access_claims(organization=5)), "bad_claim:organization")


def test_alg_none_rejected(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    tok = raw_jws({"alg": "none", "kid": rsa_key.kid, "typ": "JWT"}, access_claims(), None)
    reject(verifier, tok, "alg_not_allowed")


def test_hs256_with_rsa_public_key_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey
) -> None:
    pem = rsa_key.private.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    tok = raw_jws({"alg": "HS256", "kid": rsa_key.kid, "typ": "JWT"}, access_claims(), pem)
    reject(verifier, tok, "alg_not_allowed")


def test_alg_header_swapped_to_es256_with_rsa_kid(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, ec_key: SigningKey
) -> None:
    tok = sign(ec_key, access_claims(), kid=rsa_key.kid)
    reject(verifier, tok, "key_alg_mismatch")


def test_tampered_payload_rejected(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    h, _, s = sign(rsa_key, access_claims()).split(".")
    forged = b64(json.dumps(access_claims(sub="user-admin")).encode())
    reject(verifier, f"{h}.{forged}.{s}", "bad_signature")


def test_signed_by_unknown_key_with_known_kid(
    verifier: AccessTokenVerifier, rsa_key: SigningKey
) -> None:
    attacker = new_rsa("attacker")
    reject(verifier, sign(attacker, access_claims(), kid=rsa_key.kid), "bad_signature")


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"azp": "some-other-app"}, "client_not_allowed"),
        ({"azp": None}, "missing_claim:azp"),
        ({"aud": ["account"]}, "audience"),
        ({"aud": RESOURCE + "/"}, "audience"),
        ({"aud": None}, "audience"),
        ({"iss": ISSUER + "/"}, "issuer"),
        ({"iss": "https://sso.example.test/realms/master"}, "issuer"),
        ({"exp": NOW - 31}, "expired"),
        ({"exp": None}, "missing_claim:exp"),
        ({"iat": None}, "missing_claim:iat"),
        ({"exp": "soon"}, "bad_claim:exp"),
        ({"exp": True}, "bad_claim:exp"),
        ({"nbf": NOW + 60}, "not_yet_valid"),
        ({"iat": NOW + 60, "exp": NOW + 300}, "iat_in_future"),
        ({"exp": NOW + 10 * 365 * 86400}, "lifetime_exceeded"),
        ({"iat": NOW, "exp": NOW}, "lifetime_invalid"),
        ({"typ": "Refresh"}, "token_type"),
        ({"typ": "ID"}, "token_type"),
        ({"typ": None}, "token_type"),
        ({"sub": None}, "missing_claim:sub"),
        ({"sub": ""}, "missing_claim:sub"),
        ({"scope": 7}, "bad_claim:scope"),
    ],
)
def test_claim_rejections(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, over: dict[str, Any], reason: str
) -> None:
    reject(verifier, sign(rsa_key, access_claims(**over)), reason)


def test_leeway_accepts_small_skew(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, clock: FakeClock
) -> None:
    verifier.verify(sign(rsa_key, access_claims(iat=NOW - 100, exp=NOW - 29)))
    verifier.verify(sign(rsa_key, access_claims(nbf=NOW + 29)))


def test_refresh_token_as_bearer_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey
) -> None:
    # Hình dạng refresh token Keycloak: typ=Refresh, aud=issuer, không có scope MCP.
    refresh = access_claims(typ="Refresh", aud=ISSUER, azp=AGENT, exp=NOW + 1700)
    with pytest.raises(InvalidTokenError):
        verifier.verify(sign(rsa_key, refresh))


def test_id_token_as_bearer_rejected(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    reject(verifier, sign(rsa_key, access_claims(typ="ID", aud=AGENT)), "token_type")


@pytest.mark.parametrize(
    ("token", "reason"),
    [
        ("abc.def.ghi", "malformed"),
        ("not-a-jwt", "malformed"),
        ("a.b.c.d.e", "malformed"),
        ("", "malformed"),
    ],
)
def test_garbage(verifier: AccessTokenVerifier, token: str, reason: str) -> None:
    reject(verifier, token, reason)


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"kid": None}, "missing_kid"),
        ({"typ": "xt-approval+jwt"}, "header_typ"),
        ({"crit": ["exp"]}, "malformed"),  # PyJWT từ chối mọi `crit`
    ],
)
def test_header_rejections(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, headers: dict[str, Any], reason: str
) -> None:
    claims = json.dumps(access_claims()).encode()
    if headers.get("kid", 1) is None:
        h = {"alg": "RS256", "typ": "JWT"}
        tok = f"{b64(json.dumps(h).encode())}.{b64(claims)}.{b64(b'x')}"
    else:
        tok = sign(rsa_key, access_claims(), **headers)
    reject(verifier, tok, reason)


def test_header_typ_at_jwt_accepted(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    verifier.verify(sign(rsa_key, access_claims(), typ="at+jwt"))


def test_insufficient_scope_is_403(verifier: AccessTokenVerifier, rsa_key: SigningKey) -> None:
    with pytest.raises(InsufficientScopeError) as e:
        verifier.verify(sign(rsa_key, access_claims(scope="openid")))
    assert e.value.required == {"mcp-xt"}
    with pytest.raises(InsufficientScopeError) as e2:
        verifier.verify(sign(rsa_key, access_claims()), required_scopes=frozenset({"xt:admin"}))
    assert e2.value.required == {"mcp-xt", "xt:admin"}
    # `openidx` không phải `openid`: so khớp nguyên từ.
    with pytest.raises(InsufficientScopeError):
        verifier.verify(sign(rsa_key, access_claims(scope="mcp-xtx")))


def test_dynamic_organization_scope_satisfies_base_scope(
    verifier: AccessTokenVerifier, rsa_key: SigningKey
) -> None:
    """Desk đổi tenant xin `mcp-xt:<alias>` (Keycloak không kèm `mcp-xt` trơn): vẫn đủ scope MCP."""
    claims = access_claims(scope="openid mcp-xt:hoamai email", organization=["hoamai"])
    p = verifier.verify(sign(rsa_key, claims))
    assert p.has_scopes({"mcp-xt"})
    assert p.scopes == {"openid", "email", "mcp-xt:hoamai", "mcp-xt"}
    assert p.organizations == ("hoamai",)
    # Scope tham số của scope KHÁC không thay được `mcp-xt`; `mcp-xtx:a` cũng không.
    for scope in ("openid organization:hoamai", "openid mcp-xtx:hoamai", "openid xt:mcp-xt"):
        with pytest.raises(InsufficientScopeError):
            verifier.verify(sign(rsa_key, access_claims(scope=scope, organization=["hoamai"])))


@pytest.mark.parametrize(
    ("scope", "organization", "required"),
    [
        ("openid mcp-xt:", ["hoamai"], "mcp-xt"),  # hậu tố rỗng
        ("openid mcp-xt:other", ["hoamai"], "mcp-xt"),  # hậu tố khác claim tổ chức
        ("openid mcp-xt:hoamai", None, "mcp-xt"),  # không có claim tổ chức
        ("openid mcp-xt:hoamai", ["hoamai", "acme"], "mcp-xt"),  # nhiều tổ chức
        ("openid mcp-xt:*", ["hoamai"], "mcp-xt"),
        ("openid mcp-xt xt-data:hoamai", ["hoamai"], "xt-data"),  # ngoài allow-list
        ("openid mcp-xt admin:hoamai", ["hoamai"], "admin"),
    ],
)
def test_dynamic_scope_rejected_unless_allowlisted_and_matches_tenant(
    verifier: AccessTokenVerifier,
    rsa_key: SigningKey,
    scope: str,
    organization: list[str] | None,
    required: str,
) -> None:
    claims = access_claims(scope=scope, organization=organization)
    with pytest.raises(InsufficientScopeError):
        verifier.verify(sign(rsa_key, claims), required_scopes=frozenset({required}))


def test_algorithm_allowlist_from_settings(
    jwks: JwksCache, clock: FakeClock, ec_key: SigningKey
) -> None:
    from tests.unit.identity.factory import idp_settings

    only_rs = AccessTokenVerifier(
        rs_settings(idp=idp_settings(algorithms=frozenset({"RS256"}))), jwks, clock
    )
    reject(only_rs, sign(ec_key, access_claims()), "alg_not_allowed")


# ------------------------------------------------------------------ JWKS: xoay khóa, xóa khóa


def test_unknown_kid_refetch_is_rate_limited(clock: FakeClock, rsa_key: SigningKey) -> None:
    src = FakeJwks(keys=[rsa_key])
    v = AccessTokenVerifier(rs_settings(), JwksCache(src, 300, 10, clock), clock)
    v.verify(sign(rsa_key, access_claims()))
    assert src.calls == 1
    rogue = new_rsa("rogue")
    reject(v, sign(rogue, access_claims(), kid="random-1"), "unknown_kid")
    assert src.calls == 1, "lần fetch đầu < 10 s trước: chưa được refetch"
    clock.advance(10)
    for i in range(20):
        reject(v, sign(rogue, access_claims(), kid=f"random-{i}"), "unknown_kid")
    assert src.calls == 2, "20 kid lạ trong cùng cửa sổ chỉ gây đúng 1 lần refetch"


def test_rotated_in_key_accepted_after_refetch(clock: FakeClock, rsa_key: SigningKey) -> None:
    src = FakeJwks(keys=[rsa_key])
    v = AccessTokenVerifier(rs_settings(), JwksCache(src, 300, 10, clock), clock)
    v.verify(sign(rsa_key, access_claims()))
    new = new_rsa("kid-rsa-2")
    src.keys.append(new)
    clock.advance(10)
    assert v.verify(sign(new, access_claims())).subject == "user-alice"
    assert src.calls == 2


def test_deleted_key_not_trusted_after_ttl(clock: FakeClock, rsa_key: SigningKey) -> None:
    src = FakeJwks(keys=[rsa_key])
    v = AccessTokenVerifier(rs_settings(), JwksCache(src, 300, 10, clock), clock)
    tok = sign(rsa_key, access_claims(exp=NOW + 800))
    v.verify(tok)
    src.keys.clear()
    clock.advance(299)
    v.verify(tok)  # vẫn trong TTL: chấp nhận (độ trễ tối đa = TTL)
    clock.advance(1)
    reject(v, tok, "unknown_kid")


def test_idp_down_after_ttl_fails_closed(clock: FakeClock, rsa_key: SigningKey) -> None:
    src = FakeJwks(keys=[rsa_key])
    v = AccessTokenVerifier(rs_settings(), JwksCache(src, 300, 10, clock), clock)
    tok = sign(rsa_key, access_claims(exp=NOW + 800))
    v.verify(tok)
    src.fail = True
    clock.advance(300)
    with pytest.raises(IdentityProviderUnavailableError):
        v.verify(tok)
    calls = src.calls
    with pytest.raises(IdentityProviderUnavailableError):
        v.verify(tok)  # trong cửa sổ refetch: không gọi IdP nữa, không dùng khóa cũ
    assert src.calls == calls
    src.fail = False
    clock.advance(10)
    v.verify(tok)


def test_jwks_filters_unusable_keys(clock: FakeClock, rsa_key: SigningKey) -> None:
    good = rsa_key.jwk()
    enc = dict(good, kid="enc-1", use="enc", alg="RSA-OAEP")
    hs = {"kty": "oct", "kid": "hs-1", "k": "c2VjcmV0", "alg": "HS256"}
    broken = {"kty": "RSA", "kid": "broken", "n": "!!", "e": "AQAB"}
    p384 = {"kty": "EC", "kid": "p384", "crv": "P-384", "x": "a", "y": "b"}
    src = FakeJwks(extra=[good, enc, hs, broken, p384, "junk", {"kty": "RSA"}])  # type: ignore[list-item]
    cache = JwksCache(src, 300, 10, clock)
    v = AccessTokenVerifier(rs_settings(), cache, clock)
    v.verify(sign(rsa_key, access_claims()))
    for kid in ("enc-1", "hs-1", "broken", "p384"):
        with pytest.raises(InvalidTokenError):
            cache.get(kid)


def test_duplicate_kid_dropped(clock: FakeClock, rsa_key: SigningKey) -> None:
    other = new_rsa("other")
    dup = dict(other.jwk(), kid=rsa_key.kid)
    src = FakeJwks(keys=[rsa_key], extra=[dup])
    v = AccessTokenVerifier(rs_settings(), JwksCache(src, 300, 10, clock), clock)
    reject(v, sign(rsa_key, access_claims()), "unknown_kid")


def test_jwk_alg_mismatch(clock: FakeClock, rsa_key: SigningKey) -> None:
    jwk = dict(rsa_key.jwk(), alg="PS256")
    v = AccessTokenVerifier(rs_settings(), JwksCache(FakeJwks(extra=[jwk]), 300, 10, clock), clock)
    reject(v, sign(rsa_key, access_claims()), "key_alg_mismatch")


def test_mcp_rejects_tokens_also_issued_for_workflow_or_data() -> None:
    """WA-08: token có thêm aud `xt-workflow`/`xt-data` không dùng được ở /mcp."""
    from tests.unit.identity.factory import FakeClock, FakeJwks, access_claims, new_rsa, sign
    from xt.adapters.identity.config import load_resource_server_settings
    from xt.adapters.identity.jwks import JwksCache

    env = {
        "XT_IDP_ISSUER": ISSUER,
        "XT_IDP_JWKS_URL": "https://kc.internal.test/certs",
        "XT_MCP_RESOURCE": RESOURCE,
        "XT_MCP_ALLOWED_CLIENTS": AGENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
    }
    rs = load_resource_server_settings(env)
    assert rs.foreign_audiences == {"xt-workflow", "xt-data", "xt-files"}
    key = new_rsa("wa08")
    v = AccessTokenVerifier(rs, JwksCache(FakeJwks([key]), 300, 10, FakeClock()), FakeClock())
    assert v.verify(sign(key, access_claims())).client_id == AGENT
    for extra in ("xt-workflow", "xt-data", "xt-files"):  # FB-08: cả aud của /files
        with pytest.raises(InvalidTokenError) as e:
            v.verify(sign(key, access_claims(aud=[RESOURCE, extra])))
        assert e.value.reason == "audience"
