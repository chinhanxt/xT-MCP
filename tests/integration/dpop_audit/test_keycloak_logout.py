# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit DPoP vòng 2, DPA-03 phía Keycloak 26.6.3 thật: client dựng bằng `desk_agent_client_rep`
(có `post.logout.redirect.uris`), RP-initiated logout như `desk/src/oidc.ts` (id_token_hint +
post_logout_redirect_uri + client_id) kết thúc phiên SSO: refresh token gắn khóa bị vô hiệu."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.keycloak import Admin, Realm
from tests.unit.identity.dpop_client import DpopKey

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[3]
DESK = "http://localhost:19403"
CLIENT = "it-desk-logout"


def _provision() -> Any:
    spec = importlib.util.spec_from_file_location(
        "provision_lo", ROOT / "deploy/keycloak/provision.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def client(realm: Realm, kc_admin: Admin) -> str:
    env = {"XT_DESK_AGENT_CLIENT_ID": CLIENT, "XT_DPOP_REQUIRED": "true"}
    rep = _provision().desk_agent_client_rep(env, DESK, [])
    rep["directAccessGrantsEnabled"] = True  # chỉ cho test headless
    kc_admin.ok(kc_admin.c.post(f"/admin/realms/{realm.name}/clients", json=rep))
    return CLIENT


def test_rp_initiated_logout_kills_bound_refresh_token(realm: Realm, client: str) -> None:
    token_url = f"{realm.oidc}/token"
    holder = DpopKey()
    r = httpx.post(
        token_url,
        data={
            "grant_type": "password",
            "client_id": client,
            "username": "bob",
            "password": "bob-pw",
            "scope": "openid",
        },
        headers={"DPoP": holder.proof("POST", token_url)},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "DPoP" and tok.get("id_token")

    end = httpx.get(
        f"{realm.oidc}/logout",
        params={
            "client_id": client,
            "id_token_hint": tok["id_token"],
            "post_logout_redirect_uri": f"{DESK}/",
        },
        timeout=30,
        follow_redirects=False,
    )
    assert end.status_code == 302, end.text
    assert end.headers["location"].startswith(f"{DESK}/")

    evil = httpx.get(
        f"{realm.oidc}/logout",
        params={
            "client_id": client,
            "id_token_hint": tok["id_token"],
            "post_logout_redirect_uri": "https://evil.example/",
        },
        timeout=30,
        follow_redirects=False,
    )
    assert evil.status_code != 302 or not evil.headers.get("location", "").startswith(
        "https://evil.example"
    )

    again = httpx.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "client_id": client,
            "refresh_token": tok["refresh_token"],
        },
        headers={"DPoP": holder.proof("POST", token_url)},
        timeout=30,
    )
    assert again.status_code == 400, again.text  # phiên SSO đã kết thúc, kể cả chủ khóa
