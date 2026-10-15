# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
from __future__ import annotations

import pytest

from tests.unit.identity.factory import ISSUER, rs_settings
from xt.adapters.identity.resource_metadata import (
    build_protected_resource_metadata,
    challenge_for,
    extract_bearer,
    protected_resource_metadata_path,
    protected_resource_metadata_url,
    www_authenticate_insufficient_scope,
)
from xt.domain.identity import (
    IdentityProviderUnavailableError,
    InsufficientScopeError,
    InvalidTokenError,
    TokenRevokedError,
)

PRM = "https://xt.example.test/.well-known/oauth-protected-resource/mcp"


@pytest.mark.parametrize(
    ("resource", "url"),
    [
        ("https://xt.example.test/mcp", PRM),
        ("https://xt.example.test/mcp/", PRM),
        ("https://xt.example.test", "https://xt.example.test/.well-known/oauth-protected-resource"),
        (
            "http://localhost:18000/t/acme/mcp",
            "http://localhost:18000/.well-known/oauth-protected-resource/t/acme/mcp",
        ),
    ],
)
def test_prm_url_rfc9728_insertion(resource: str, url: str) -> None:
    assert protected_resource_metadata_url(resource) == url


def test_prm_document() -> None:
    s = rs_settings()
    path = protected_resource_metadata_path(s.resource)
    assert path == "/.well-known/oauth-protected-resource/mcp"
    assert build_protected_resource_metadata(s) == {
        "resource": "https://xt.example.test/mcp",
        "authorization_servers": [ISSUER],
        "scopes_supported": ["openid", "email", "mcp-xt"],
        "bearer_methods_supported": ["header"],
    }


def test_401_without_token_has_no_error_code() -> None:
    ch = challenge_for(rs_settings(), None)
    assert ch.status == 401
    assert ch.headers["WWW-Authenticate"] == f'Bearer resource_metadata="{PRM}", scope="mcp-xt"'


def test_401_invalid_token() -> None:
    ch = challenge_for(rs_settings(), InvalidTokenError("expired"))
    assert ch.status == 401
    assert ch.headers["WWW-Authenticate"] == (
        'Bearer error="invalid_token", error_description="The access token expired", '
        f'resource_metadata="{PRM}", scope="mcp-xt"'
    )
    generic = challenge_for(rs_settings(), InvalidTokenError("unknown_kid", "kid-secret"))
    assert "kid-secret" not in generic.headers["WWW-Authenticate"]
    assert generic.body["error_description"] == "The access token is invalid"
    revoked = challenge_for(rs_settings(), TokenRevokedError("token_revoked"))
    assert "no longer active" in revoked.headers["WWW-Authenticate"]


def test_403_insufficient_scope_union() -> None:
    ch = challenge_for(rs_settings(), InsufficientScopeError({"xt:write", "mcp-xt"}, {"mcp-xt"}))
    assert ch.status == 403
    assert ch.headers["WWW-Authenticate"] == (
        f'Bearer error="insufficient_scope", scope="mcp-xt xt:write", resource_metadata="{PRM}"'
    )
    assert ch.body["error"] == "insufficient_scope"


def test_header_escaping() -> None:
    h = www_authenticate_insufficient_scope(rs_settings(), ['a"b\\c'])
    assert 'scope="a\\"b\\\\c mcp-xt"' in h


def test_challenge_rejects_non_auth_errors() -> None:
    with pytest.raises(TypeError):
        challenge_for(rs_settings(), IdentityProviderUnavailableError("jwks_unavailable"))


@pytest.mark.parametrize(
    ("header", "token"),
    [(None, None), ("", None), ("Bearer abc", "abc"), ("bearer  abc ", "abc")],
)
def test_extract_bearer(header: str | None, token: str | None) -> None:
    assert extract_bearer(header) == token


@pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer", "Bearer a b", "Token abc"])
def test_extract_bearer_malformed(header: str) -> None:
    with pytest.raises(InvalidTokenError) as e:
        extract_bearer(header)
    assert e.value.reason == "malformed_authorization"
