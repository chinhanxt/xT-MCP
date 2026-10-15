# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP (RFC 9449) ở resource server: §4.3 (mọi ca âm), §7.1 (token gắn khóa + scheme), cờ
`XT_DPOP_REQUIRED` bật/tắt, và challenge `WWW-Authenticate: DPoP`."""

from __future__ import annotations

import json
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from tests.unit.identity.dpop_client import DpopKey, ath, b64u
from tests.unit.identity.factory import (
    AGENT,
    FakeClock,
    SigningKey,
    access_claims,
    raw_jws,
    rs_settings,
    sign,
)
from xt.adapters.identity import (
    AccessTokenVerifier,
    DpopContext,
    DpopError,
    DpopSettings,
    DpopVerifier,
    IdentityConfigError,
    challenge_for,
    extract_credentials,
    load_dpop_settings,
)
from xt.adapters.identity.dpop import jwk_thumbprint, normalize_htu
from xt.adapters.identity.jwks import JwksCache
from xt.domain.identity import InvalidTokenError

HTU = "https://xt.example.test/mcp"
DESK = "xt-desk-agent"
TOKEN = "at-value"


@pytest.fixture
def key(clock: FakeClock) -> DpopKey:
    return DpopKey(clock)


@pytest.fixture
def dv(clock: FakeClock) -> DpopVerifier:
    return DpopVerifier(DpopSettings(origins=("https://xt.example.test",)), clock=clock)


def check(dv: DpopVerifier, proof: str | list[str], **over: Any) -> str:
    kw: dict[str, Any] = {
        "htm": "POST",
        "htu": {HTU},
        "bound_to": TOKEN,
        "expected_jkt": None,
    }
    kw.update(over)
    return dv.verify([proof] if isinstance(proof, str) else proof, **kw)


def reason(dv: DpopVerifier, proof: str | list[str], **over: Any) -> str:
    with pytest.raises(DpopError) as ei:
        check(dv, proof, **over)
    return ei.value.reason


def _segments(token: str) -> tuple[dict[str, Any], dict[str, Any], str]:
    import base64

    h, p, s = token.split(".")
    dec = lambda x: json.loads(base64.urlsafe_b64decode(x + "=" * (-len(x) % 4)))  # noqa: E731
    return dec(h), dec(p), s


def _resign(key: DpopKey, header: dict[str, Any], claims: dict[str, Any]) -> str:
    import jwt

    return jwt.encode(claims, key.private, algorithm="ES256", headers=header)


# ------------------------------------------------------------------ hợp lệ + chuẩn hóa ----
def test_valid_proof_returns_rfc7638_thumbprint(dv: DpopVerifier, key: DpopKey) -> None:
    jkt = check(dv, key.proof("POST", HTU, token=TOKEN), expected_jkt=key.jkt)
    assert jkt == key.jkt == jwk_thumbprint(key.jwk)


def test_rfc7638_known_vector() -> None:
    """RFC 7638 §3.1."""
    jwk = {
        "kty": "RSA",
        "n": (
            "0vx7agoebGcQSuuPiLJXZptN9nndrQmbXEps2aiAFbWhM78LhWx4cbbfAAtVT86zwu1RK7aPFFxuhDR1"
            "L6tSoc_BJECPebWKRXjBZCiFV4n3oknjhMstn64tZ_2W-5JsGY4Hc5n9yBXArwl93lqt7_RN5w6Cf0h4"
            "QyQ5v-65YGjQR0_FDW2QvzqY368QQMicAtaSqzs8KJZgnYb9c7d0zgdAZHzu6qMQvRL5hajrn1n91CbO"
            "pbISD08qNLyrdkt-bFTWhAI4vMQFh6WeZu0fM4lFd2NcRwr3XPksINHaQ-G_xBniIqbw0Ls1jF44-csF"
            "Cur-kEgU8awapJzKnqDKgw"
        ),
        "e": "AQAB",
        "alg": "RS256",
        "kid": "2011-04-29",
    }
    assert jwk_thumbprint(jwk) == "NzbLsXh8uDCcd-6MNwXF4W_7noWXFZAfHkxZsRGC9Xs"


@pytest.mark.parametrize(
    "htu",
    [
        "https://xt.example.test/mcp?x=1",
        "https://xt.example.test/mcp#frag",
        "HTTPS://XT.Example.Test/mcp",
        "https://xt.example.test:443/mcp",
    ],
)
def test_htu_normalization_accepts_equivalent_uri(dv: DpopVerifier, key: DpopKey, htu: str) -> None:
    assert check(dv, key.proof("POST", htu, token=TOKEN)) == key.jkt


def test_normalize_htu_rules() -> None:
    assert normalize_htu("http://h:80") == "http://h/"
    assert normalize_htu("https://h:8443/a/b?q#f") == "https://h:8443/a/b"
    with pytest.raises(ValueError):
        normalize_htu("ftp://h/x")
    with pytest.raises(ValueError):
        normalize_htu("https://u:p@h/x")


def test_candidates_cover_every_public_origin(clock: FakeClock) -> None:
    v = DpopVerifier(DpopSettings(origins=("https://a.test", "https://b.test:8443")), clock=clock)
    assert v.candidates("/mcp") == {"https://a.test/mcp", "https://b.test:8443/mcp"}


# ------------------------------------------------------------------ RFC 9449 §4.3: ca âm ----
def test_missing_proof_is_invalid_token(dv: DpopVerifier) -> None:
    with pytest.raises(DpopError) as ei:
        dv.verify([], htm="POST", htu={HTU}, bound_to=TOKEN, expected_jkt=None)
    assert ei.value.error == "invalid_token"


def test_two_dpop_headers_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    assert reason(dv, [p, key.proof("POST", HTU, token=TOKEN)]) == "dpop_multiple"


@pytest.mark.parametrize("bad", ["", "a.b", "a.b.c.d", "a..c", "x" * 9000])
def test_not_a_compact_jws(dv: DpopVerifier, bad: str) -> None:
    assert reason(dv, bad) in ("dpop_malformed", "dpop_too_large")


@pytest.mark.parametrize("typ", ["JWT", "at+jwt", None, 7])
def test_typ_must_be_dpop_jwt(dv: DpopVerifier, key: DpopKey, typ: Any) -> None:
    h: dict[str, Any] = {"typ": typ}
    p = key.proof("POST", HTU, token=TOKEN, header=h)
    if typ is None:
        hd, cl, _ = _segments(p)
        hd.pop("typ", None)
        p = _resign(key, hd, cl)
    assert reason(dv, p) == "dpop_typ"


def test_alg_none_rejected(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    claims = {"jti": "j", "htm": "POST", "htu": HTU, "iat": int(clock()), "ath": ath(TOKEN)}
    p = raw_jws({"typ": "dpop+jwt", "alg": "none", "jwk": key.jwk}, claims, None)
    assert reason(dv, p) == "dpop_alg"


def test_alg_hs256_rejected(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    claims = {"jti": "j", "htm": "POST", "htu": HTU, "iat": int(clock()), "ath": ath(TOKEN)}
    p = raw_jws({"typ": "dpop+jwt", "alg": "HS256", "jwk": key.jwk}, claims, b"k" * 32)
    assert reason(dv, p) == "dpop_alg"


def test_alg_outside_allowlist_rejected(dv: DpopVerifier, rsa_key: SigningKey) -> None:
    """RS256 hợp lệ về mật mã nhưng ngoài `XT_DPOP_ALGORITHMS` (mặc định ES256)."""
    import jwt
    from jwt.algorithms import RSAAlgorithm

    jwk = json.loads(RSAAlgorithm.to_jwk(rsa_key.private.public_key()))
    claims = {"jti": "j", "htm": "POST", "htu": HTU, "iat": 1_800_000_000, "ath": ath(TOKEN)}
    p = jwt.encode(claims, rsa_key.private, "RS256", headers={"typ": "dpop+jwt", "jwk": jwk})
    assert reason(dv, p) == "dpop_alg"


def test_crit_header_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN, header={"crit": ["exp"]})
    assert reason(dv, p) == "dpop_crit"


@pytest.mark.parametrize(
    "jwk",
    [
        None,
        "not-an-object",
        {"kty": "RSA", "n": "AQAB", "e": "AQAB"},  # kty không khớp ES256
        {"kty": "EC", "crv": "P-384", "x": "AA", "y": "AA"},
    ],
)
def test_bad_or_missing_jwk(dv: DpopVerifier, key: DpopKey, jwk: Any) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    hd, cl, _ = _segments(p)
    if jwk is None:
        hd.pop("jwk")
    else:
        hd["jwk"] = jwk
    assert reason(dv, _resign(key, hd, cl)) == "dpop_jwk"


def test_private_key_in_jwk_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    d = key.private.private_numbers().private_value.to_bytes(32, "big")
    p = key.proof("POST", HTU, token=TOKEN, header={"jwk": {**key.jwk, "d": b64u(d)}})
    assert reason(dv, p) == "dpop_jwk"


def test_noncanonical_coordinate_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    """Như R2-02: ký tự cuối base64url có bit thừa -> cùng điểm, chuỗi khác -> phải từ chối."""
    x = key.jwk["x"]
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    alt = x[:-1] + alphabet[alphabet.index(x[-1]) ^ 1]
    p = key.proof("POST", HTU, token=TOKEN, header={"jwk": {**key.jwk, "x": alt}})
    assert reason(dv, p) == "dpop_jwk"


def test_signature_by_other_key_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, key=other)) == "dpop_signature"


def test_tampered_payload_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    h, _, s = p.split(".")
    _, cl, _ = _segments(p)
    cl["htm"] = "GET"
    forged = f"{h}.{b64u(json.dumps(cl).encode())}.{s}"
    assert reason(dv, forged, htm="GET") == "dpop_signature"


@pytest.mark.parametrize("jti", [None, "", 5, "x" * 257])
def test_jti_required_and_bounded(dv: DpopVerifier, key: DpopKey, jti: Any) -> None:
    p = (
        key.proof("POST", HTU, token=TOKEN, jti=jti)
        if jti is not None
        else key.proof("POST", HTU, token=TOKEN, drop=("jti",))
    )
    assert reason(dv, p) == "dpop_jti"


@pytest.mark.parametrize("htm", ["GET", "post", None])
def test_htm_must_match_exactly(dv: DpopVerifier, key: DpopKey, htm: Any) -> None:
    p = (
        key.proof("POST", HTU, token=TOKEN, drop=("htm",))
        if htm is None
        else key.proof(htm, HTU, token=TOKEN)
    )
    assert reason(dv, p) == "dpop_htm"


@pytest.mark.parametrize(
    "htu",
    [
        "https://evil.example/mcp",
        "https://xt.example.test/mcp/other",
        "http://xt.example.test/mcp",
        "https://xt.example.test:8443/mcp",
        "/mcp",
        "not a url",
    ],
)
def test_htu_mismatch(dv: DpopVerifier, key: DpopKey, htu: str) -> None:
    assert reason(dv, key.proof("POST", htu, token=TOKEN)) == "dpop_htu"


def test_htu_missing(dv: DpopVerifier, key: DpopKey) -> None:
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, drop=("htu",))) == "dpop_htu"


@pytest.mark.parametrize(
    ("delta", "code"),
    [(31, "dpop_iat_future"), (-91, "dpop_iat_stale"), (-3600, "dpop_iat_stale")],
)
def test_iat_window(
    dv: DpopVerifier, key: DpopKey, clock: FakeClock, delta: int, code: str
) -> None:
    """Cửa sổ: tương lai ≤ leeway (30 s), quá khứ ≤ max_age + leeway (60 + 30 s)."""
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, iat=int(clock()) + delta)) == code


@pytest.mark.parametrize("delta", [30, -90, 0])
def test_iat_window_edges_accepted(
    dv: DpopVerifier, key: DpopKey, clock: FakeClock, delta: int
) -> None:
    assert check(dv, key.proof("POST", HTU, token=TOKEN, iat=int(clock()) + delta))


@pytest.mark.parametrize("iat", [True, "1800000000", None])
def test_iat_must_be_number(dv: DpopVerifier, key: DpopKey, iat: Any) -> None:
    p = (
        key.proof("POST", HTU, token=TOKEN, drop=("iat",))
        if iat is None
        else key.proof("POST", HTU, token=TOKEN, iat=iat)
    )
    assert reason(dv, p) == "dpop_iat"


def test_ath_missing(dv: DpopVerifier, key: DpopKey) -> None:
    assert reason(dv, key.proof("POST", HTU)) == "dpop_ath"


def test_ath_for_other_token(dv: DpopVerifier, key: DpopKey) -> None:
    assert reason(dv, key.proof("POST", HTU, token="another-token")) == "dpop_ath"


def test_ath_unexpected_without_bound_value(dv: DpopVerifier, key: DpopKey) -> None:
    assert reason(dv, key.proof("POST", HTU, token=TOKEN), bound_to=None) == "dpop_ath_unexpected"


def test_jwk_thumbprint_must_equal_cnf_jkt(dv: DpopVerifier, key: DpopKey) -> None:
    other = DpopKey()
    p = key.proof("POST", HTU, token=TOKEN)
    assert reason(dv, p, expected_jkt=other.jkt) == "dpop_key_mismatch"


def test_jti_replay_rejected(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    assert check(dv, p)
    assert reason(dv, p) == "dpop_replay"


def test_invalid_attempt_does_not_burn_jti(dv: DpopVerifier, key: DpopKey) -> None:
    p = key.proof("POST", HTU, token=TOKEN, jti="same")
    assert reason(dv, p, expected_jkt=DpopKey().jkt) == "dpop_key_mismatch"
    assert check(dv, p)


def test_replay_cache_expires_with_window(dv: DpopVerifier, key: DpopKey, clock: FakeClock) -> None:
    p = key.proof("POST", HTU, token=TOKEN)
    assert check(dv, p)
    clock.advance(200)  # proof cũ giờ hết cửa sổ iat: vẫn bị chặn (stale), không lọt
    assert reason(dv, p) == "dpop_iat_stale"


def test_es384_when_allowed(clock: FakeClock) -> None:
    import jwt

    v = DpopVerifier(DpopSettings(algorithms=frozenset({"ES384"})), clock=clock)
    priv = ec.generate_private_key(ec.SECP384R1())
    n = priv.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-384",
        "x": b64u(n.x.to_bytes(48, "big")),
        "y": b64u(n.y.to_bytes(48, "big")),
    }
    claims = {"jti": "j", "htm": "GET", "htu": HTU, "iat": int(clock())}
    p = jwt.encode(claims, priv, "ES384", headers={"typ": "dpop+jwt", "jwk": jwk})
    assert v.verify([p], htm="GET", htu={HTU}, bound_to=None, expected_jkt=None)


# ------------------------------------------------------------------ cấu hình ----
def test_settings_defaults_and_origins() -> None:
    s = load_dpop_settings(
        {"XT_MCP_RESOURCE": "https://xt-mcp.example/mcp", "XT_DESK_URL": "https://xt.example"}
    )
    assert s.required is False and s.clients == {DESK} and s.algorithms == {"ES256"}
    assert s.origins == ("https://xt-mcp.example", "https://xt.example")


def test_settings_flag_and_errors() -> None:
    s = load_dpop_settings({"XT_DPOP_REQUIRED": "true", "XT_DPOP_CLIENTS": "a,b"})
    assert s.required and s.clients == {"a", "b"}
    with pytest.raises(IdentityConfigError) as ei:
        load_dpop_settings(
            {
                "XT_DPOP_REQUIRED": "maybe",
                "XT_DPOP_ALGORITHMS": "HS256,none",
                "XT_DPOP_ORIGINS": "http://localhost:5173",
            }
        )
    text = "\n".join(ei.value.problems)
    assert "XT_DPOP_REQUIRED" in text and "HS256" in text and "http" in text
    ok = load_dpop_settings({"XT_DPOP_ORIGINS": "http://localhost:5173", "XT_IDP_ALLOW_HTTP": "1"})
    assert ok.origins == ("http://localhost:5173",)


# ------------------------------------------------------- AccessTokenVerifier (RFC 9449 §7) ----
@pytest.fixture(params=[False, True], ids=["flag-off", "flag-on"])
def required(request: pytest.FixtureRequest) -> bool:
    return bool(request.param)


@pytest.fixture
def verifier(jwks: JwksCache, clock: FakeClock, required: bool) -> AccessTokenVerifier:
    rs = rs_settings(allowed_clients=frozenset({AGENT, DESK}))
    dv = DpopVerifier(
        DpopSettings(required=required, origins=("https://xt.example.test",)), clock=clock
    )
    return AccessTokenVerifier(rs, jwks, clock, dpop=dv)


def bound_token(rsa_key: SigningKey, key: DpopKey, azp: str = DESK, **over: Any) -> str:
    return sign(rsa_key, access_claims(azp=azp, typ="DPoP", cnf={"jkt": key.jkt}, **over))


def ctx(proofs: tuple[str, ...], scheme: str = "dpop", method: str = "POST") -> DpopContext:
    return DpopContext(scheme, proofs, method, frozenset({HTU}))


def test_bound_token_with_valid_proof(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    tok = bound_token(rsa_key, key)
    p = verifier.verify(tok, dpop=ctx((key.proof("POST", HTU, token=tok),)))
    assert p.dpop_jkt == key.jkt and p.client_id == DESK


def test_bound_token_via_bearer_scheme_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    tok = bound_token(rsa_key, key)
    with pytest.raises(DpopError) as ei:
        verifier.verify(tok, dpop=ctx((key.proof("POST", HTU, token=tok),), scheme="bearer"))
    assert ei.value.reason == "dpop_bearer_scheme" and ei.value.error == "invalid_token"


def test_bound_token_without_request_context_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    """Bề mặt không hỗ trợ DPoP (REST workflow/files, stdio): token gắn khóa luôn bị từ chối."""
    with pytest.raises(DpopError):
        verifier.verify(bound_token(rsa_key, key))
    plain = AccessTokenVerifier(verifier._s, verifier._jwks, verifier._clock)
    with pytest.raises(DpopError):
        plain.verify(bound_token(rsa_key, key), dpop=ctx(()))


def test_bound_token_without_proof_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    with pytest.raises(DpopError) as ei:
        verifier.verify(bound_token(rsa_key, key), dpop=ctx(()))
    assert ei.value.reason == "dpop_missing"


def test_stolen_bound_token_with_attacker_key_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    tok = bound_token(rsa_key, key)
    thief = DpopKey(key.clock)
    with pytest.raises(DpopError) as ei:
        verifier.verify(tok, dpop=ctx((thief.proof("POST", HTU, token=tok),)))
    assert ei.value.reason == "dpop_key_mismatch"


def test_dpop_scheme_with_unbound_token_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    tok = sign(rsa_key, access_claims(azp=DESK))
    with pytest.raises(DpopError) as ei:
        verifier.verify(tok, dpop=ctx((key.proof("POST", HTU, token=tok),)))
    assert ei.value.reason == "dpop_unbound_token"


def test_flag_governs_unbound_desk_token(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, required: bool
) -> None:
    tok = sign(rsa_key, access_claims(azp=DESK))
    if required:
        with pytest.raises(DpopError) as ei:
            verifier.verify(tok, dpop=ctx((), scheme="bearer"))
        assert ei.value.reason == "dpop_required"
    else:
        assert verifier.verify(tok, dpop=ctx((), scheme="bearer")).dpop_jkt is None


def test_other_clients_keep_bearer_under_both_flags(
    verifier: AccessTokenVerifier, rsa_key: SigningKey
) -> None:
    tok = sign(rsa_key, access_claims())
    assert verifier.verify(tok, dpop=ctx((), scheme="bearer")).client_id == AGENT
    assert verifier.verify(tok).dpop_jkt is None


@pytest.mark.parametrize("cnf", [{"jkt": ""}, {"x5t#S256": "abc"}, "jkt", {"jkt": 5}])
def test_malformed_cnf_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey, cnf: Any
) -> None:
    tok = sign(rsa_key, access_claims(azp=DESK, typ="DPoP", cnf=cnf))
    with pytest.raises(InvalidTokenError):
        verifier.verify(tok, dpop=ctx((key.proof("POST", HTU, token=tok),)))


def test_typ_dpop_without_cnf_rejected(
    verifier: AccessTokenVerifier, rsa_key: SigningKey, key: DpopKey
) -> None:
    tok = sign(rsa_key, access_claims(azp=DESK, typ="DPoP"))
    with pytest.raises(InvalidTokenError):
        verifier.verify(tok, dpop=ctx((key.proof("POST", HTU, token=tok),)))


# ------------------------------------------------------------------ challenge + header ----
def test_challenge_is_dpop_scheme_with_prm(key: DpopKey) -> None:
    c = challenge_for(rs_settings(), DpopError("dpop_htu"))
    h = c.headers["WWW-Authenticate"]
    assert c.status == 401 and h.startswith('DPoP error="invalid_dpop_proof"')
    assert 'algs="ES256"' in h and 'Bearer resource_metadata="' in h
    assert c.body["error"] == "invalid_dpop_proof"
    c2 = challenge_for(rs_settings(), DpopError("dpop_bearer_scheme", error="invalid_token"))
    assert c2.headers["WWW-Authenticate"].startswith('DPoP error="invalid_token"')


def test_extract_credentials_schemes() -> None:
    assert extract_credentials("DPoP abc") == ("dpop", "abc")
    assert extract_credentials("bearer abc") == ("bearer", "abc")
    assert extract_credentials(None) is None
    for bad in ("Basic abc", "DPoP", "DPoP a b"):
        with pytest.raises(InvalidTokenError):
            extract_credentials(bad)


def test_stale_reported_only_for_the_bound_key(
    dv: DpopVerifier, key: DpopKey, clock: FakeClock
) -> None:
    """Kẻ có vé mà không có khóa không được 'stale' (không tiêu vé) bằng proof cũ khóa lạ."""
    old = int(clock()) - 600
    thief = DpopKey(clock)
    p = thief.proof("POST", HTU, token=TOKEN, iat=old)
    assert reason(dv, p, expected_jkt=key.jkt) == "dpop_key_mismatch"
    assert reason(dv, key.proof("POST", HTU, token="x", iat=old)) == "dpop_ath"
    assert reason(dv, key.proof("POST", HTU, token=TOKEN, iat=old)) == "dpop_iat_stale"
