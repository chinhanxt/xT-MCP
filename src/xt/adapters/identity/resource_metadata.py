# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Protected Resource Metadata (RFC 9728) và header `WWW-Authenticate` (RFC 6750, MCP 2026-07-28).

- 401 không có token: `Bearer resource_metadata="…", scope="…"` (không kèm `error`, RFC 6750 §3).
- 401 token sai: thêm `error="invalid_token"` + `error_description`.
- 403 thiếu scope: `error="insufficient_scope"`, `scope` = mọi scope thao tác cần (1 challenge).
- Không đưa `offline_access` vào PRM hay challenge (config đã chặn).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from xt.adapters.identity.config import ResourceServerSettings
from xt.adapters.identity.dpop import DpopError
from xt.domain.identity import IdentityError, InsufficientScopeError, InvalidTokenError

WELL_KNOWN = "/.well-known/oauth-protected-resource"

# Mô tả công khai theo `reason`; không lộ chi tiết nội bộ (kid, giá trị claim).
_DESCRIPTIONS = {
    "missing_token": "Authentication required",
    "expired": "The access token expired",
    "audience": "The access token was not issued for this resource",
    "client_not_allowed": "The client is not allowed to access this resource",
    "token_revoked": "The access token is no longer active",
    "dpop_missing": "A DPoP proof is required for this access token",
    "dpop_bearer_scheme": "This access token is DPoP-bound; use the DPoP authorization scheme",
    "dpop_unbound_token": "The DPoP scheme requires a DPoP-bound access token",
    "dpop_required": "This client must use DPoP-bound access tokens",
    "dpop_unsupported": "DPoP-bound access tokens are not accepted on this resource",
}


def protected_resource_metadata_url(resource: str) -> str:
    """RFC 9728 §3.1: chèn well-known giữa host và path của resource."""
    p = urlsplit(resource)
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme, p.netloc, WELL_KNOWN + path, "", ""))


def protected_resource_metadata_path(resource: str) -> str:
    """Đường dẫn route cần phục vụ PRM (APISIX phải forward nguyên đường này)."""
    return urlsplit(protected_resource_metadata_url(resource)).path


def build_protected_resource_metadata(settings: ResourceServerSettings) -> dict[str, Any]:
    return {
        "resource": settings.resource,
        "authorization_servers": [settings.idp.issuer],
        "scopes_supported": list(settings.scopes_supported),
        "bearer_methods_supported": ["header"],
    }


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _header(params: list[tuple[str, str]]) -> str:
    return "Bearer " + ", ".join(f"{k}={_quote(v)}" for k, v in params)


def _scope_value(scopes: Iterable[str]) -> str:
    return " ".join(sorted(set(scopes)))


def www_authenticate_unauthorized(
    settings: ResourceServerSettings, error: InvalidTokenError | None = None
) -> str:
    params: list[tuple[str, str]] = []
    if error is not None:
        params += [
            ("error", "invalid_token"),
            ("error_description", _DESCRIPTIONS.get(error.reason, "The access token is invalid")),
        ]
    params += [
        ("resource_metadata", protected_resource_metadata_url(settings.resource)),
        ("scope", _scope_value(settings.required_scopes)),
    ]
    return _header(params)


def www_authenticate_insufficient_scope(
    settings: ResourceServerSettings, required: Iterable[str]
) -> str:
    return _header(
        [
            ("error", "insufficient_scope"),
            ("scope", _scope_value(set(required) | settings.required_scopes)),
            ("resource_metadata", protected_resource_metadata_url(settings.resource)),
        ]
    )


@dataclass(frozen=True, slots=True)
class AuthChallenge:
    status: int
    headers: dict[str, str]
    body: dict[str, str] = field(default_factory=dict)


def www_authenticate_dpop(settings: ResourceServerSettings, error: DpopError) -> str:
    """RFC 9449 §7.1: challenge `DPoP` mang `error` + `algs`, kèm challenge Bearer có PRM (RFC 9728)
    để client MCP vẫn tìm được authorization server."""
    desc = _DESCRIPTIONS.get(error.reason, "The DPoP proof is invalid")
    dpop = "DPoP " + ", ".join(
        f"{k}={_quote(v)}"
        for k, v in (
            ("error", error.error),
            ("error_description", desc),
            ("algs", " ".join(error.algs)),
        )
    )
    bearer = _header(
        [
            ("resource_metadata", protected_resource_metadata_url(settings.resource)),
            ("scope", _scope_value(settings.required_scopes)),
        ]
    )
    return f"{dpop}, {bearer}"


def challenge_for(settings: ResourceServerSettings, exc: IdentityError | None) -> AuthChallenge:
    """None = không có token. Lỗi khác ngoài 401/403 (vd IdP không tới được) không thuộc hàm này."""
    if isinstance(exc, DpopError):
        return AuthChallenge(
            401,
            {"WWW-Authenticate": www_authenticate_dpop(settings, exc)},
            {
                "error": exc.error,
                "error_description": _DESCRIPTIONS.get(exc.reason, "The DPoP proof is invalid"),
            },
        )
    if isinstance(exc, InsufficientScopeError):
        return AuthChallenge(
            403,
            {"WWW-Authenticate": www_authenticate_insufficient_scope(settings, exc.required)},
            {"error": "insufficient_scope", "error_description": "Additional scope required"},
        )
    if exc is None:
        return AuthChallenge(
            401,
            {"WWW-Authenticate": www_authenticate_unauthorized(settings)},
            {"error": "invalid_token", "error_description": _DESCRIPTIONS["missing_token"]},
        )
    if isinstance(exc, InvalidTokenError):
        return AuthChallenge(
            401,
            {"WWW-Authenticate": www_authenticate_unauthorized(settings, exc)},
            {
                "error": "invalid_token",
                "error_description": _DESCRIPTIONS.get(exc.reason, "The access token is invalid"),
            },
        )
    raise TypeError(f"không ánh xạ {type(exc).__name__} sang challenge 401/403")


def extract_credentials(authorization: str | None) -> tuple[str, str] | None:
    """(scheme chữ thường `bearer`|`dpop`, token); None nếu thiếu header. Scheme khác -> lỗi."""
    if authorization is None or not authorization.strip():
        return None
    scheme, _, value = authorization.strip().partition(" ")
    low = scheme.lower()
    if low not in ("bearer", "dpop") or not value.strip() or " " in value.strip():
        raise InvalidTokenError("malformed_authorization")
    return low, value.strip()


def extract_bearer(authorization: str | None) -> str | None:
    """None nếu thiếu header; sai scheme/rỗng -> `InvalidTokenError`. Không nhận token ở query."""
    if authorization is None or not authorization.strip():
        return None
    scheme, _, value = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip() or " " in value.strip():
        raise InvalidTokenError("malformed_authorization")
    return value.strip()
