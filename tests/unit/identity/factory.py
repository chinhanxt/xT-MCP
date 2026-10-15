# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tiện ích test danh tính: khóa sinh cục bộ, JWKS giả, đồng hồ giả (không mạng)."""

from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from xt.adapters.identity.config import (
    IdpSettings,
    ResourceServerSettings,
    StepUpSettings,
)

ISSUER = "https://sso.example.test/realms/xt"
RESOURCE = "https://xt.example.test/mcp"
AGENT = "claude-connector"
NOW = 1_800_000_000


class FakeClock:
    def __init__(self, t: float = NOW) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@dataclass
class SigningKey:
    kid: str
    alg: str
    private: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey

    def jwk(self) -> dict[str, Any]:
        pub = self.private.public_key()
        raw = (
            RSAAlgorithm.to_jwk(pub)
            if isinstance(pub, rsa.RSAPublicKey)
            else ECAlgorithm.to_jwk(pub)
        )
        d: dict[str, Any] = json.loads(raw)
        d.update(kid=self.kid, use="sig", alg=self.alg)
        return d


@functools.cache
def _rsa_private(kid: str) -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def new_rsa(kid: str) -> SigningKey:
    """Mỗi kid một khóa RSA riêng (cache theo kid để test nhanh)."""
    return SigningKey(kid, "RS256", _rsa_private(kid))


def new_ec(kid: str) -> SigningKey:
    return SigningKey(kid, "ES256", ec.generate_private_key(ec.SECP256R1()))


@dataclass
class FakeJwks:
    keys: list[SigningKey] = field(default_factory=list)
    calls: int = 0
    fail: bool = False
    extra: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self) -> Mapping[str, Any]:
        self.calls += 1
        if self.fail:
            raise ConnectionError("IdP down")
        return {"keys": [k.jwk() for k in self.keys] + self.extra}


def sign(key: SigningKey, claims: Mapping[str, Any], **headers: Any) -> str:
    h = {"kid": key.kid, "typ": "JWT"}
    h.update(headers)
    return jwt.encode(dict(claims), key.private, algorithm=key.alg, headers=h)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def raw_jws(header: Mapping[str, Any], claims: Mapping[str, Any], secret: bytes | None) -> str:
    """JWS ghép tay (để dựng alg=none, HS256 bằng public key...)."""
    signing_input = f"{b64(json.dumps(header).encode())}.{b64(json.dumps(claims).encode())}"
    data = signing_input.encode()
    sig = b"" if secret is None else hmac.new(secret, data, hashlib.sha256).digest()
    return f"{signing_input}.{b64(sig)}"


def access_claims(**over: Any) -> dict[str, Any]:
    c: dict[str, Any] = {
        "iss": ISSUER,
        "sub": "user-alice",
        "aud": [RESOURCE, "account"],
        "azp": AGENT,
        "typ": "Bearer",
        "iat": NOW - 10,
        "exp": NOW + 290,
        "auth_time": NOW - 20,
        "jti": "tok-1",
        "scope": "openid email mcp-xt",
        "preferred_username": "alice",
        "acr": "1",
        "realm_access": {"roles": ["employee", "default-roles-xt"]},
        "organization": ["acme"],
    }
    c.update(over)
    return {k: v for k, v in c.items() if v is not None}


def idp_settings(**over: Any) -> IdpSettings:
    base: dict[str, Any] = {
        "issuer": ISSUER,
        "jwks_url": "https://kc.internal.test/realms/xt/protocol/openid-connect/certs",
        "algorithms": frozenset({"RS256", "ES256"}),
        "leeway_s": 30,
        "max_token_lifetime_s": 900,
        "jwks_ttl_s": 300,
        "jwks_min_refetch_interval_s": 10,
        "http_timeout_s": 5.0,
        "tenant_claim": "organization",
    }
    base.update(over)
    return IdpSettings(**base)


def rs_settings(**over: Any) -> ResourceServerSettings:
    base: dict[str, Any] = {
        "idp": idp_settings(),
        "resource": RESOURCE,
        "allowed_clients": frozenset({AGENT, "chatgpt-connector"}),
        "required_scopes": frozenset({"mcp-xt"}),
        "scopes_supported": ("openid", "email", "mcp-xt"),
    }
    base.update(over)
    return ResourceServerSettings(**base)


def su_settings(**over: Any) -> StepUpSettings:
    base: dict[str, Any] = {
        "idp": idp_settings(),
        "client_id": "xt-desk",
        "acr": "urn:xt:acr:otp",
        "max_auth_age_s": 300,
        "required_amr": frozenset({"otp"}),
    }
    base.update(over)
    return StepUpSettings(**base)
