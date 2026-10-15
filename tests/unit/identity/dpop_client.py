# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Phía client DPoP cho test (như `desk/src/dpop.ts`): khóa P-256, JWK công khai, proof."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from collections.abc import Callable
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import ec


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def ath(value: str) -> str:
    return b64u(hashlib.sha256(value.encode()).digest())


class DpopKey:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.private = ec.generate_private_key(ec.SECP256R1())
        n = self.private.public_key().public_numbers()
        self.jwk = {
            "crv": "P-256",
            "kty": "EC",
            "x": b64u(n.x.to_bytes(32, "big")),
            "y": b64u(n.y.to_bytes(32, "big")),
        }
        self.clock = clock

    @property
    def jkt(self) -> str:
        doc = json.dumps(self.jwk, separators=(",", ":"), sort_keys=True)
        return b64u(hashlib.sha256(doc.encode()).digest())

    def proof(
        self,
        htm: str,
        htu: str,
        *,
        token: str | None = None,
        header: dict[str, Any] | None = None,
        drop: tuple[str, ...] = (),
        key: ec.EllipticCurvePrivateKey | None = None,
        **claims: Any,
    ) -> str:
        c: dict[str, Any] = {
            "jti": secrets.token_urlsafe(16),
            "htm": htm,
            "htu": htu,
            "iat": int(self.clock()),
        }
        if token is not None:
            c["ath"] = ath(token)
        c.update(claims)
        for k in drop:
            c.pop(k, None)
        h: dict[str, Any] = {"typ": "dpop+jwt", "jwk": dict(self.jwk)}
        h.update(header or {})
        return jwt.encode(c, key or self.private, algorithm="ES256", headers=h)
