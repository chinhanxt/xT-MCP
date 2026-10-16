# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP (RFC 9449) phía client cho harness: khóa ES256 mỗi phiên, proof cho token endpoint và mọi
request tài nguyên (`ath` = base64url(SHA-256(access token hoặc vé PII))).

Bật khi `XT_DPOP_REQUIRED=true` (cùng cờ với xt-api/provision.py) cho các client trong
`XT_DPOP_CLIENTS` (mặc định `xt-desk-agent`). Token gắn khóa đi với scheme `DPoP`.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from xte2e.config import settings


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def enabled() -> bool:
    return settings().env.get("XT_DPOP_REQUIRED", "").strip().lower() in ("1", "true", "yes", "on")


def clients() -> set[str]:
    raw = (
        settings().env.get("XT_DPOP_CLIENTS")
        or settings().env.get("XT_DESK_AGENT_CLIENT_ID")
        or "xt-desk-agent"
    )
    return {c.strip() for c in raw.split(",") if c.strip()}


def for_client(client_id: str) -> DpopKey | None:
    return DpopKey() if enabled() and client_id in clients() else None


def ath(value: str) -> str:
    return _b64(hashlib.sha256(value.encode("ascii")).digest())


def htu(url: str) -> str:
    p = urlsplit(url)
    return urlunsplit((p.scheme.lower(), (p.hostname or "").lower(), p.path or "/", "", ""))


class DpopKey:
    def __init__(self) -> None:
        self.priv = ec.generate_private_key(ec.SECP256R1())
        n = self.priv.public_key().public_numbers()
        self.jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": _b64(n.x.to_bytes(32, "big")),
            "y": _b64(n.y.to_bytes(32, "big")),
        }
        canon = json.dumps(
            {k: self.jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":")
        )
        self.jkt = _b64(hashlib.sha256(canon.encode()).digest())

    def proof(self, method: str, url: str, bound: str | None = None) -> str:
        head = {"typ": "dpop+jwt", "alg": "ES256", "jwk": self.jwk}
        body: dict[str, Any] = {
            "jti": secrets.token_urlsafe(16),
            "htm": method.upper(),
            "htu": htu(url),
            "iat": int(time.time()),
        }
        if bound is not None:
            body["ath"] = ath(bound)
        signing = f"{_b64(json.dumps(head).encode())}.{_b64(json.dumps(body).encode())}"
        r, s = decode_dss_signature(self.priv.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        return f"{signing}.{_b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"

    def headers(self, method: str, url: str, token: str) -> dict[str, str]:
        return {"Authorization": f"DPoP {token}", "DPoP": self.proof(method, url, token)}


def auth_headers(key: DpopKey | None, method: str, url: str, token: str) -> dict[str, str]:
    return key.headers(method, url, token) if key else {"Authorization": f"Bearer {token}"}
