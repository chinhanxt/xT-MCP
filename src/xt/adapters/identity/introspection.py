# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Thu hồi token: JWT không thu hồi được trước `exp`, nên thao tác rủi ro cao (L2/L3/human_only)
hỏi lại Keycloak qua token introspection (RFC 7662), cache ngắn (<= 60 s).

Độ trễ thu hồi tối đa: `cache_ttl_s` cho thao tác rủi ro cao khi bật `high_risk`; với thao tác
L0/L1 (hoặc khi `off`) là thời hạn token (`accessTokenLifespan`, khuyến nghị 5 phút).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Callable

import httpx

from xt.adapters.identity.config import IntrospectionMode, IntrospectionSettings
from xt.application.ports import IntrospectionResult, TokenIntrospector
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import (
    IdentityProviderUnavailableError,
    InvalidTokenError,
    Principal,
    TokenRevokedError,
)

log = logging.getLogger(__name__)

MAX_CACHE_ENTRIES = 10_000


class KeycloakIntrospector:
    def __init__(
        self,
        endpoint: str,
        client_id: str,
        client_secret: str,
        cache_ttl_s: float,
        *,
        timeout_s: float = 5.0,
        client: httpx.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._endpoint = endpoint
        self._auth = (client_id, client_secret)
        self._ttl = cache_ttl_s
        self._http = client or httpx.Client(timeout=timeout_s, follow_redirects=False)
        self._clock = clock
        self._cache: dict[str, tuple[float, IntrospectionResult]] = {}
        self._lock = threading.Lock()

    def introspect(self, token: str) -> IntrospectionResult:
        key = hashlib.sha256(token.encode()).hexdigest()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None and now - hit[0] < self._ttl:
                return hit[1]
        try:
            r = self._http.post(
                self._endpoint,
                data={"token": token, "token_type_hint": "access_token"},
                auth=self._auth,
                headers={"Accept": "application/json"},
            )
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            log.error("introspection lỗi: %s", exc)
            raise IdentityProviderUnavailableError("introspection_unavailable", str(exc)) from exc
        if not isinstance(body, dict) or not isinstance(body.get("active"), bool):
            raise IdentityProviderUnavailableError("introspection_unavailable", "phản hồi sai")
        sub, cid = body.get("sub"), body.get("client_id") or body.get("azp")
        result = IntrospectionResult(
            active=body["active"],
            subject=sub if isinstance(sub, str) else None,
            client_id=cid if isinstance(cid, str) else None,
        )
        if self._ttl > 0:
            with self._lock:
                if len(self._cache) >= MAX_CACHE_ENTRIES:
                    self._cache = {k: v for k, v in self._cache.items() if now - v[0] < self._ttl}
                    if len(self._cache) >= MAX_CACHE_ENTRIES:
                        self._cache.clear()
                self._cache[key] = (now, result)
        return result


class RevocationGuard:
    """Gọi trước khi thực thi thao tác: L0/L1 bỏ qua; rủi ro cao hỏi introspection nếu bật."""

    def __init__(self, mode: IntrospectionMode, introspector: TokenIntrospector | None) -> None:
        if mode is IntrospectionMode.HIGH_RISK and introspector is None:
            raise ValueError("mode high_risk cần introspector")
        self._mode = mode
        self._introspector = introspector

    @property
    def mode(self) -> IntrospectionMode:
        return self._mode

    def ensure_active(self, principal: Principal, token: str, level: RiskLevel) -> None:
        if not level.high_risk or self._mode is IntrospectionMode.OFF:
            return
        assert self._introspector is not None
        res = self._introspector.introspect(token)
        if not res.active:
            raise TokenRevokedError("token_revoked")
        if res.subject is not None and res.subject != principal.subject:
            raise InvalidTokenError("introspection_mismatch", "sub")
        if res.client_id is not None and res.client_id != principal.client_id:
            raise InvalidTokenError("introspection_mismatch", "client_id")


def build_revocation_guard(
    settings: IntrospectionSettings, client: httpx.Client | None = None
) -> RevocationGuard:
    if settings.mode is IntrospectionMode.OFF:
        return RevocationGuard(IntrospectionMode.OFF, None)
    assert settings.endpoint and settings.client_id and settings.client_secret
    return RevocationGuard(
        IntrospectionMode.HIGH_RISK,
        KeycloakIntrospector(
            settings.endpoint,
            settings.client_id,
            settings.client_secret,
            settings.cache_ttl_s,
            timeout_s=settings.http_timeout_s,
            client=client,
        ),
    )
