# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Allowlist động connector DCR trên Keycloak 26.6.3 thật: client DCR đúng chính sách được nhận,
client sai chính sách bị từ chối, client bị disable bị từ chối sau TTL."""

from __future__ import annotations

import time

import httpx
import pytest

from tests.integration.keycloak import (
    BASE,
    Admin,
    create_registry_reader,
    harden_dcr,
    register_dcr,
    set_client_enabled,
)
from xt.adapters.identity import (
    build_client_allowlist,
    load_connector_registry_settings,
    load_resource_server_settings,
)
from xt.domain.identity import InvalidTokenError, PrincipalKind

pytestmark = pytest.mark.integration

REALM = "xt-it-dcr"
CLAUDE_CB = "https://claude.ai/api/mcp/auth_callback"


def rejected(allow: object, cid: str) -> str:
    with pytest.raises(InvalidTokenError) as e:
        allow.classify(cid)  # type: ignore[attr-defined]
    assert e.value.reason == "client_not_allowed"
    return e.value.detail


def test_dynamic_connector_allowlist(kc_admin: Admin) -> None:
    realm = kc_admin.create_realm(REALM, stepup=False)
    # Keycloak tin cả example.org; xT chỉ nhận claude.ai/chatgpt.com: kiểm hai lớp độc lập.
    harden_dcr(kc_admin, REALM, ["claude.ai", "chatgpt.com", "example.org"])
    reader, secret = create_registry_reader(kc_admin, REALM)

    ok = register_dcr(
        REALM,
        {
            "client_name": "Claude",
            "redirect_uris": [CLAUDE_CB],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )["client_id"]
    cc = register_dcr(
        REALM,
        {"client_name": "cc", "redirect_uris": [CLAUDE_CB], "grant_types": ["client_credentials"]},
    )["client_id"]
    extra = register_dcr(
        REALM,
        {
            "client_name": "extra-host",
            "redirect_uris": [CLAUDE_CB, "https://example.org/cb"],
            "grant_types": ["authorization_code"],
            "token_endpoint_auth_method": "none",
        },
    )["client_id"]

    env = realm.env(
        XT_MCP_CLIENT_REGISTRY="keycloak",
        XT_MCP_ALLOWED_CLIENTS="it-agent",
        XT_MCP_HUMAN_CLIENTS="xt-desk-web",
        XT_IDP_ADMIN_API_URL=f"{BASE}/admin/realms/{REALM}",
        XT_IDP_ADMIN_TOKEN_URL=f"{realm.oidc}/token",
        XT_IDP_ADMIN_CLIENT_ID=reader,
        XT_IDP_ADMIN_CLIENT_SECRET=secret,
        XT_MCP_CONNECTOR_REDIRECT_HOSTS="claude.ai,chatgpt.com",
        XT_MCP_CONNECTOR_CACHE_SECONDS="2",
        XT_MCP_CONNECTOR_NEGATIVE_CACHE_SECONDS="2",
    )
    allow = build_client_allowlist(
        load_resource_server_settings(env), load_connector_registry_settings(env)
    )

    assert allow.classify("it-agent") is PrincipalKind.AGENT  # first-party tĩnh
    assert allow.classify("xt-desk-web") is PrincipalKind.HUMAN
    assert allow.classify(ok) is PrincipalKind.AGENT  # DCR đúng chính sách
    assert rejected(allow, cc).endswith("not_public")  # BUG-D1: client_credentials qua DCR
    assert rejected(allow, extra).endswith("redirect_host")
    assert rejected(allow, "does-not-exist").endswith("not_found")
    assert rejected(allow, "it-plain").endswith("not_public")  # client nội bộ khác

    # Service account đọc client được nhưng không làm gì khác (least privilege).
    tok = httpx.post(
        f"{realm.oidc}/token", data={"grant_type": "client_credentials"}, auth=(reader, secret)
    ).json()["access_token"]
    hdr = {"Authorization": f"Bearer {tok}"}
    assert httpx.get(f"{BASE}/admin/realms/{REALM}/users", headers=hdr).status_code == 403
    uid = kc_admin.client_uuid(REALM, ok)
    url = f"{BASE}/admin/realms/{REALM}/clients/{uid}"
    put = httpx.put(url, headers=hdr, json={"enabled": False})
    assert put.status_code == 403

    # Admin thu hồi connector: còn nhận trong TTL, bị từ chối sau TTL.
    set_client_enabled(kc_admin, REALM, ok, False)
    assert allow.classify(ok) is PrincipalKind.AGENT
    time.sleep(2.5)
    assert rejected(allow, ok).endswith("disabled")
