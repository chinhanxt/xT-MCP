# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""IdP cục bộ cho test ghép: JWKS qua HTTP thật (thread), khóa RSA 2048, token ký thật.

Dùng đúng `AccessTokenVerifier`/`StepUpVerifier`/`HttpJwksFetcher` của sản phẩm (không giả).
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from xt.adapters.identity.access_token import AccessTokenVerifier
from xt.adapters.identity.config import IdpSettings, ResourceServerSettings, StepUpSettings
from xt.adapters.identity.jwks import HttpJwksFetcher, JwksCache
from xt.adapters.identity.step_up import StepUpVerifier

KID = "it-comp-1"
AGENT_CLIENT = "it-agent"
DESK_CLIENT = "xt-desk"
CONFIRM_ACR = "urn:xt:acr:otp"


@dataclass
class LocalIdp:
    """`base` = http://127.0.0.1:<port>; `issuer` = `base`/realms/xt; JWKS ở `base`/jwks."""

    resource: str
    base: str
    issuer: str
    jwks_url: str
    private: rsa.RSAPrivateKey
    server: ThreadingHTTPServer
    fetches: list[float] = field(default_factory=list)
    codes: dict[str, str] = field(default_factory=dict)
    token_requests: list[dict[str, str]] = field(default_factory=list)

    def issue_code(self, id_token: str) -> str:
        """Mã authorization code mà `/token` sẽ đổi lấy `id_token` (một lần)."""
        code = uuid.uuid4().hex
        self.codes[code] = id_token
        return code

    # ---- settings (http cho phép vì chỉ dev/test) -------------------------------------------
    def idp_settings(self, **over: Any) -> IdpSettings:
        base: dict[str, Any] = {
            "issuer": self.issuer,
            "jwks_url": self.jwks_url,
            "algorithms": frozenset({"RS256"}),
            "leeway_s": 30,
            "max_token_lifetime_s": 900,
            "jwks_ttl_s": 300,
            "jwks_min_refetch_interval_s": 10,
            "http_timeout_s": 5.0,
            "tenant_claim": "organization",
        }
        base.update(over)
        return IdpSettings(**base)

    def rs_settings(self, **over: Any) -> ResourceServerSettings:
        base: dict[str, Any] = {
            "idp": self.idp_settings(),
            "resource": self.resource,
            "allowed_clients": frozenset({AGENT_CLIENT}),
            "required_scopes": frozenset({"mcp-xt"}),
            "scopes_supported": ("openid", "mcp-xt"),
            "human_clients": frozenset(),
        }
        base.update(over)
        return ResourceServerSettings(**base)

    def stepup_settings(self, **over: Any) -> StepUpSettings:
        base: dict[str, Any] = {
            "idp": self.idp_settings(),
            "client_id": DESK_CLIENT,
            "acr": CONFIRM_ACR,
            "max_auth_age_s": 300,
            "required_amr": frozenset({"otp"}),
        }
        base.update(over)
        return StepUpSettings(**base)

    def jwks_cache(self) -> JwksCache:
        s = self.idp_settings()
        return JwksCache(
            HttpJwksFetcher(s.jwks_url, s.http_timeout_s),
            ttl_s=s.jwks_ttl_s,
            min_refetch_interval_s=s.jwks_min_refetch_interval_s,
        )

    def access_verifier(self, **over: Any) -> AccessTokenVerifier:
        return AccessTokenVerifier(self.rs_settings(**over), self.jwks_cache())

    def step_up_verifier(self, **over: Any) -> StepUpVerifier:
        return StepUpVerifier(self.stepup_settings(**over), self.jwks_cache())

    # ---- phát token -------------------------------------------------------------------------
    def _sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(
            {k: v for k, v in claims.items() if v is not None},
            self.private,
            algorithm="RS256",
            headers={"kid": KID, "typ": "JWT"},
        )

    def access_token(
        self,
        sub: str,
        *,
        org: str,
        azp: str = AGENT_CLIENT,
        scope: str = "openid mcp-xt",
        lifetime: int = 300,
        **over: Any,
    ) -> str:
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": sub,
            "aud": [self.resource, "account"],
            "azp": azp,
            "typ": "Bearer",
            "iat": now - 5,
            "exp": now - 5 + lifetime,
            "auth_time": now - 10,
            "jti": uuid.uuid4().hex,
            "scope": scope,
            "preferred_username": sub,
            "acr": "1",
            "organization": [org],
        }
        claims.update(over)
        return self._sign(claims)

    def id_token(
        self,
        sub: str,
        *,
        nonce: str,
        org: str = "acme",
        amr: tuple[str, ...] = ("pwd", "otp"),
        acr: str = CONFIRM_ACR,
        client: str = DESK_CLIENT,
        auth_time: int | None = None,
        **over: Any,
    ) -> str:
        """ID token của trang confirm; `jti` mới mỗi lần (StepUpVerifier tiêu một lần)."""
        now = int(time.time())
        claims: dict[str, Any] = {
            "iss": self.issuer,
            "sub": sub,
            "aud": client,
            "azp": client,
            "typ": "ID",
            "iat": now - 2,
            "exp": now - 2 + 300,
            "auth_time": auth_time if auth_time is not None else now - 3,
            "jti": uuid.uuid4().hex,
            "nonce": nonce,
            "acr": acr,
            "amr": list(amr),
            "preferred_username": sub,
            "sid": uuid.uuid4().hex,
            "organization": [org],
        }
        claims.update(over)
        return self._sign(claims)

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def start_idp(resource: str) -> LocalIdp:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk: dict[str, Any] = json.loads(RSAAlgorithm.to_jwk(private.public_key()))
    jwk.update(kid=KID, use="sig", alg="RS256")
    body = json.dumps({"keys": [jwk]}).encode()
    holder: dict[str, LocalIdp] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/jwks":
                self.send_error(404)
                return
            holder["idp"].fetches.append(time.monotonic())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            from urllib.parse import parse_qsl

            if self.path != "/token":
                self.send_error(404)
                return
            n = int(self.headers.get("Content-Length", "0"))
            form = dict(parse_qsl(self.rfile.read(n).decode()))
            idp_ = holder["idp"]
            idp_.token_requests.append(form)
            tok = idp_.codes.pop(form.get("code", ""), None)
            ok = tok is not None and form.get("grant_type") == "authorization_code"
            out = json.dumps(
                {"id_token": tok, "token_type": "Bearer"} if ok else {"error": "invalid_grant"}
            ).encode()
            self.send_response(200 if ok else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, format: str, *args: Any) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    base = f"http://127.0.0.1:{server.server_address[1]}"
    idp = LocalIdp(resource, base, f"{base}/realms/xt", f"{base}/jwks", private, server)
    holder["idp"] = idp
    threading.Thread(target=server.serve_forever, daemon=True, name="it-idp").start()
    return idp
