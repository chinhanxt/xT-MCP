# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Allowlist `azp`: tĩnh cho client first-party, động cho connector DCR.

Connector DCR (Claude, ChatGPT) có `client_id` ngẫu nhiên nên không khai tĩnh được. Một `azp`
lạ chỉ được nhận là connector MCP khi đăng ký của nó tại Keycloak (Admin REST, service account
chỉ có `view-clients`) khớp chính sách DCR đã gia cố (`connector_violation`). Kết quả được cache có
TTL (thu hồi bằng cách disable client có hiệu lực trong <= TTL), cache âm, giới hạn số lần tra
cứu/phút; mọi lỗi tra cứu đều fail-closed.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from xt.adapters.identity.config import ConnectorRegistrySettings, ResourceServerSettings
from xt.application.ports import ClientAllowlist, ClientDirectory
from xt.domain.identity import (
    ClientRegistration,
    ConnectorPolicy,
    IdentityProviderUnavailableError,
    InvalidTokenError,
    PrincipalKind,
    connector_violation,
)

log = logging.getLogger(__name__)

MAX_CLIENT_ID_LEN = 128
MAX_CACHE_ENTRIES = 10_000
Clock = Callable[[], float]


class StaticClientAllowlist:
    """`XT_MCP_ALLOWED_CLIENTS` -> agent, `XT_MCP_HUMAN_CLIENTS` -> human, còn lại từ chối."""

    def __init__(self, agents: frozenset[str], humans: frozenset[str]) -> None:
        if agents & humans:
            raise ValueError(f"client vừa agent vừa human: {sorted(agents & humans)}")
        self._agents = agents
        self._humans = humans

    def lookup(self, client_id: str) -> PrincipalKind | None:
        if client_id in self._agents:
            return PrincipalKind.AGENT
        if client_id in self._humans:
            return PrincipalKind.HUMAN
        return None

    def classify(self, client_id: str) -> PrincipalKind:
        kind = self.lookup(client_id)
        if kind is None:
            raise InvalidTokenError("client_not_allowed", client_id)
        return kind


def _bool(rep: Mapping[str, Any], key: str) -> bool:
    return rep.get(key) is True


def registration_from_keycloak(rep: Mapping[str, Any]) -> ClientRegistration:
    attrs = rep.get("attributes") or {}
    if not isinstance(attrs, dict):
        attrs = {}
    extra = tuple(
        sorted(
            k
            for k, v in attrs.items()
            if str(v).lower() == "true" and ("grant.enabled" in k or "token.exchange" in k)
        )
    )
    mappers = rep.get("protocolMappers") or []
    pkce = attrs.get("pkce.code.challenge.method")
    uris = rep.get("redirectUris") or []
    return ClientRegistration(
        client_id=str(rep.get("clientId", "")),
        enabled=_bool(rep, "enabled"),
        public=_bool(rep, "publicClient"),
        protocol=rep.get("protocol") if isinstance(rep.get("protocol"), str) else None,
        standard_flow=_bool(rep, "standardFlowEnabled"),
        implicit_flow=_bool(rep, "implicitFlowEnabled"),
        direct_access_grants=_bool(rep, "directAccessGrantsEnabled"),
        service_accounts=_bool(rep, "serviceAccountsEnabled"),
        bearer_only=_bool(rep, "bearerOnly"),
        # Thiếu trường = mặc định Keycloak (true): coi như vi phạm.
        full_scope_allowed=rep.get("fullScopeAllowed") is not False,
        pkce_method=pkce if isinstance(pkce, str) and pkce else None,
        redirect_uris=tuple(str(u) for u in uris) if isinstance(uris, list) else ("*",),
        protocol_mappers=tuple(
            str(m.get("protocolMapper", "?")) if isinstance(m, dict) else "?" for m in mappers
        )
        if isinstance(mappers, list)
        else ("?",),
        extra_grants=extra,
    )


class KeycloakClientDirectory:
    """`GET {admin_api_url}/clients?clientId=…` bằng token client_credentials của service account
    chỉ có role `realm-management/view-clients`."""

    def __init__(
        self,
        settings: ConnectorRegistrySettings,
        client: httpx.Client | None = None,
        clock: Clock = time.monotonic,
    ) -> None:
        self._s = settings
        self._http = client or httpx.Client(timeout=settings.http_timeout_s, follow_redirects=False)
        self._clock = clock
        self._token: tuple[str, float] | None = None
        self._lock = threading.Lock()

    def _access_token(self, force: bool = False) -> str:
        with self._lock:
            now = self._clock()
            if not force and self._token is not None and now < self._token[1]:
                return self._token[0]
        r = self._http.post(
            self._s.token_url,
            data={"grant_type": "client_credentials"},
            auth=(self._s.client_id, self._s.client_secret),
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        body = r.json()
        tok, ttl = body.get("access_token"), body.get("expires_in", 60)
        if not isinstance(tok, str) or not isinstance(ttl, int):
            raise ValueError("phản hồi token admin sai dạng")
        with self._lock:
            self._token = (tok, self._clock() + max(1, ttl - 10))
        return tok

    def find(self, client_id: str) -> ClientRegistration | None:
        try:
            for attempt in (0, 1):
                tok = self._access_token(force=attempt == 1)
                r = self._http.get(
                    f"{self._s.admin_api_url}/clients",
                    params={"clientId": client_id, "search": "false"},
                    headers={"Authorization": f"Bearer {tok}", "Accept": "application/json"},
                )
                if r.status_code == 401 and attempt == 0:
                    continue
                r.raise_for_status()
                found = r.json()
                break
        except (httpx.HTTPError, ValueError) as exc:
            log.error("tra cứu client tại Keycloak lỗi: %s", exc)
            raise IdentityProviderUnavailableError("client_registry_unavailable", str(exc)) from exc
        if not isinstance(found, list):
            raise IdentityProviderUnavailableError("client_registry_unavailable", "phản hồi sai")
        exact = [c for c in found if isinstance(c, dict) and c.get("clientId") == client_id]
        if len(exact) != 1:
            return None
        return registration_from_keycloak(exact[0])


class _RateLimiter:
    """Token bucket: `per_minute` lần tra cứu, nạp đều."""

    def __init__(self, per_minute: int, clock: Clock) -> None:
        self._cap = float(per_minute)
        self._tokens = float(per_minute)
        self._rate = per_minute / 60.0
        self._clock = clock
        self._at = clock()

    def take(self) -> bool:
        now = self._clock()
        self._tokens = min(self._cap, self._tokens + (now - self._at) * self._rate)
        self._at = now
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False


class ConnectorAllowlist:
    """Tĩnh trước (first-party), sau đó tra registry. Cache dương `cache_ttl_s`, cache âm
    `negative_cache_ttl_s`; lỗi tra cứu không cache và fail-closed."""

    def __init__(
        self,
        static: StaticClientAllowlist,
        directory: ClientDirectory,
        policy: ConnectorPolicy,
        *,
        cache_ttl_s: float,
        negative_cache_ttl_s: float,
        lookups_per_minute: int,
        clock: Clock = time.monotonic,
    ) -> None:
        self._static = static
        self._dir = directory
        self._policy = policy
        self._ttl = cache_ttl_s
        self._neg_ttl = negative_cache_ttl_s
        self._clock = clock
        self._limiter = _RateLimiter(lookups_per_minute, clock)
        self._cache: dict[str, tuple[float, str | None]] = {}  # client_id -> (hạn, lý do từ chối)
        self._lock = threading.Lock()

    def classify(self, client_id: str) -> PrincipalKind:
        kind = self._static.lookup(client_id)
        if kind is not None:
            return kind
        if not client_id or len(client_id) > MAX_CLIENT_ID_LEN or not client_id.isprintable():
            raise InvalidTokenError("client_not_allowed", "client_id sai dạng")
        with self._lock:
            now = self._clock()
            hit = self._cache.get(client_id)
            if hit is not None and now < hit[0]:
                return self._decide(client_id, hit[1])
            if not self._limiter.take():
                raise IdentityProviderUnavailableError("client_registry_rate_limited")
        reg = self._dir.find(client_id)  # gọi mạng ngoài lock; lỗi -> fail-closed, không cache
        reason = "not_found" if reg is None else connector_violation(reg, self._policy)
        with self._lock:
            now = self._clock()
            if len(self._cache) >= MAX_CACHE_ENTRIES:
                self._cache = {k: v for k, v in self._cache.items() if now < v[0]}
                if len(self._cache) >= MAX_CACHE_ENTRIES:
                    self._cache.clear()
            ttl = self._ttl if reason is None else self._neg_ttl
            self._cache[client_id] = (now + ttl, reason)
        if reason is not None:
            log.warning("từ chối connector %s: %s", client_id, reason)
        return self._decide(client_id, reason)

    @staticmethod
    def _decide(client_id: str, reason: str | None) -> PrincipalKind:
        if reason is not None:
            raise InvalidTokenError("client_not_allowed", f"{client_id}: {reason}")
        return PrincipalKind.AGENT


def build_client_allowlist(
    settings: ResourceServerSettings,
    registry: ConnectorRegistrySettings | None = None,
    client: httpx.Client | None = None,
) -> ClientAllowlist:
    """`settings.dynamic_connectors` bật thì bắt buộc truyền `registry`."""
    static = StaticClientAllowlist(settings.allowed_clients, settings.human_clients)
    if not settings.dynamic_connectors:
        if registry is not None:
            raise ValueError("có registry nhưng XT_MCP_CLIENT_REGISTRY không phải 'keycloak'")
        return static
    if registry is None:
        raise ValueError("XT_MCP_CLIENT_REGISTRY=keycloak cần ConnectorRegistrySettings")
    return ConnectorAllowlist(
        static,
        KeycloakClientDirectory(registry, client),
        ConnectorPolicy(redirect_hosts=registry.redirect_hosts),
        cache_ttl_s=registry.cache_ttl_s,
        negative_cache_ttl_s=registry.negative_cache_ttl_s,
        lookups_per_minute=registry.lookups_per_minute,
    )
