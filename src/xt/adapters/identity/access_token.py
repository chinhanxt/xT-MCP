# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Kiểm access token Keycloak cho MCP resource server -> `Principal`.

Đóng các lỗ VS B (`research/verification-spikes.md`): `azp` theo allowlist (#1), JWKS có TTL (#3),
`sub`/`iat`/`typ=Bearer` bắt buộc và trần `exp - iat` (#4), alg allowlist RS256/ES256.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from xt.adapters.identity.client_registry import StaticClientAllowlist
from xt.adapters.identity.config import ResourceServerSettings
from xt.adapters.identity.dpop import DpopError, DpopVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.adapters.identity.jwt_core import (
    check_standard_claims,
    numeric,
    str_list,
    string,
    verify_signed_jwt,
)
from xt.application.ports import ClientAllowlist
from xt.domain.identity import (
    InsufficientScopeError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
)

ACCESS_TOKEN_TYP = "Bearer"
DPOP_TOKEN_TYP = "DPoP"  # Keycloak: token gắn DPoP có claim `typ=DPoP` + `cnf.jkt`


@dataclass(frozen=True, slots=True)
class DpopContext:
    """Dữ kiện HTTP để kiểm DPoP (RFC 9449 §7): scheme của Authorization, mọi header `DPoP`,
    method và các URI (đã gồm origin công khai) mà request này được coi là gửi tới."""

    scheme: str  # "bearer" | "dpop" (chữ thường)
    proofs: tuple[str, ...]
    method: str
    htu: frozenset[str]


def _cnf_jkt(claims: Mapping[str, Any]) -> str | None:
    cnf = claims.get("cnf")
    if cnf is None:
        return None
    jkt = cnf.get("jkt") if isinstance(cnf, dict) else None
    if not isinstance(jkt, str) or not jkt:
        raise InvalidTokenError("bad_claim:cnf")
    return jkt


def _organizations(claims: Mapping[str, Any], claim: str) -> tuple[str, ...]:
    """Mapper Organization của Keycloak trả list alias, hoặc object {alias: {...}}."""
    v = claims.get(claim)
    if v is None:
        return ()
    if isinstance(v, dict):
        return tuple(sorted(str(k) for k in v))
    if isinstance(v, str):
        return (v,)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return tuple(sorted(set(v)))
    raise InvalidTokenError(f"bad_claim:{claim}")


def _realm_roles(claims: Mapping[str, Any]) -> frozenset[str]:
    ra = claims.get("realm_access")
    if not isinstance(ra, dict):
        return frozenset()
    roles = ra.get("roles")
    if not isinstance(roles, list):
        return frozenset()
    return frozenset(r for r in roles if isinstance(r, str))


class AccessTokenVerifier:
    def __init__(
        self,
        settings: ResourceServerSettings,
        jwks: JwksCache,
        clock: Callable[[], float] = time.time,
        clients: ClientAllowlist | None = None,
        dpop: DpopVerifier | None = None,
    ) -> None:
        """`clients` mặc định là allowlist tĩnh từ settings; registry động thì truyền
        `build_client_allowlist(...)` (bắt buộc khi `dynamic_connectors`)."""
        if clients is None:
            if settings.dynamic_connectors:
                raise ValueError("XT_MCP_CLIENT_REGISTRY=keycloak cần truyền `clients`")
            clients = StaticClientAllowlist(settings.allowed_clients, settings.human_clients)
        self._s = settings
        self._jwks = jwks
        self._clock = clock
        self._clients = clients
        self._dpop = dpop

    def _check_dpop(
        self, token: str, claims: Mapping[str, Any], azp: str, ctx: DpopContext | None
    ) -> str | None:
        """RFC 9449 §7.1: token có `cnf.jkt` CHỈ hợp lệ với scheme DPoP + proof đúng khóa đó;
        scheme DPoP với token không gắn khóa bị từ chối; `XT_DPOP_REQUIRED` buộc client trong
        `XT_DPOP_CLIENTS` phải có token gắn khóa. Không có `ctx` (bề mặt không hỗ trợ DPoP: REST
        workflow/files) thì token gắn khóa luôn bị từ chối (fail-closed)."""
        jkt = _cnf_jkt(claims)
        typ_dpop = claims.get("typ") == DPOP_TOKEN_TYP
        algs: Iterable[str] = self._dpop.settings.algorithms if self._dpop else ("ES256",)
        if typ_dpop and jkt is None:
            raise InvalidTokenError("bad_claim:cnf", "typ=DPoP without cnf.jkt")
        if jkt is None:
            if ctx is not None and ctx.scheme == "dpop":
                raise DpopError("dpop_unbound_token", error="invalid_token", algs=algs)
            if self._dpop is not None and self._dpop.required_for(azp):
                raise DpopError("dpop_required", azp, error="invalid_token", algs=algs)
            return None
        if self._dpop is None or ctx is None:
            raise DpopError("dpop_unsupported", error="invalid_token", algs=algs)
        if ctx.scheme != "dpop":
            raise DpopError("dpop_bearer_scheme", error="invalid_token", algs=algs)
        return self._dpop.verify(
            ctx.proofs, htm=ctx.method, htu=ctx.htu, bound_to=token, expected_jkt=jkt
        )

    def verify(
        self,
        token: str,
        required_scopes: frozenset[str] = frozenset(),
        *,
        dpop: DpopContext | None = None,
    ) -> Principal:
        """Ném `InvalidTokenError` (401; `DpopError` -> challenge DPoP) hoặc
        `InsufficientScopeError` (403).

        `required_scopes` cộng thêm vào scope chung (`XT_MCP_REQUIRED_SCOPES`) cho thao tác cụ thể.
        `dpop`: dữ kiện request cho bề mặt hỗ trợ DPoP (/mcp).
        """
        s = self._s
        _, claims = verify_signed_jwt(token, self._jwks, s.idp.algorithms, s.idp.max_token_bytes)
        iat, exp = check_standard_claims(
            claims,
            issuer=s.idp.issuer,
            audience=s.resource,
            token_type=(
                DPOP_TOKEN_TYP if claims.get("typ") == DPOP_TOKEN_TYP else ACCESS_TOKEN_TYP
            ),
            now=self._clock(),
            leeway_s=s.idp.leeway_s,
            max_lifetime_s=s.idp.max_token_lifetime_s,
        )
        if s.foreign_audiences & set(str_list(claims, "aud")):
            raise InvalidTokenError("audience", "token also issued for another resource")
        azp = string(claims, "azp", required=True)
        assert azp is not None
        kind = self._clients.classify(azp)  # trước kiểm scope: client lạ luôn 401
        jkt = self._check_dpop(token, claims, azp, dpop)
        scope = string(claims, "scope", required=False) or ""
        scopes = frozenset(scope.split())
        need = s.required_scopes | required_scopes
        if not need <= scopes:
            raise InsufficientScopeError(need, scopes)
        sub = string(claims, "sub", required=True)
        assert sub is not None
        # Keycloak >= 25: token client_credentials có claim `client_id` (scope service_account).
        service_account = claims.get("client_id") == azp
        if kind is PrincipalKind.AGENT:
            # Client MCP: luôn là agent; token auth-code thì agent đại diện cho `sub`.
            behalf = None if service_account else sub
        else:
            if service_account:
                raise InvalidTokenError("service_account_not_human", azp)
            behalf = None
        return Principal(
            subject=sub,
            kind=kind,
            client_id=azp,
            on_behalf_of=behalf,
            issuer=s.idp.issuer,
            username=string(claims, "preferred_username", required=False),
            organizations=_organizations(claims, s.idp.tenant_claim),
            roles=_realm_roles(claims),
            scopes=scopes,
            auth_time=numeric(claims, "auth_time", required=False),
            acr=string(claims, "acr", required=False),
            amr=str_list(claims, "amr"),
            token_id=string(claims, "jti", required=False),
            issued_at=iat,
            expires_at=exp,
            dpop_jkt=jkt,
        )
