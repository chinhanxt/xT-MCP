# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP (RFC 9449) với Keycloak 26.6.3 thật: client dựng bằng CHÍNH builder của
`deploy/keycloak/provision.py` (`desk_agent_client_rep`, attr `dpop.bound.access.tokens`), token
gắn `cnf.jkt`, refresh token gắn khóa (client public), và xt kiểm proof bằng `AccessTokenVerifier`.

Chỉ khác production ở `directAccessGrantsEnabled` (lấy token headless không cần trình duyệt); ràng
buộc DPoP ở token endpoint là cùng một nhánh mã cho mọi grant. Chạy: `make test-integration`.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests.integration.keycloak import RESOURCE, Admin, Realm
from tests.unit.identity.dpop_client import DpopKey
from xt.adapters.identity import (
    AccessTokenVerifier,
    DpopContext,
    DpopError,
    build_dpop_verifier,
    build_jwks_cache,
    load_resource_server_settings,
)

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]
DESK = "http://localhost:19403"


def _provision() -> Any:
    spec = importlib.util.spec_from_file_location(
        "provision_it", ROOT / "deploy/keycloak/provision.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _claims(jwt: str) -> dict[str, Any]:
    part = jwt.split(".")[1]
    out: dict[str, Any] = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    return out


@pytest.fixture(scope="module", params=[False, True], ids=["flag-off", "flag-on"])
def desk(request: pytest.FixtureRequest, realm: Realm, kc_admin: Admin) -> tuple[str, bool]:
    required = bool(request.param)
    client_id = f"it-desk-dpop-{'on' if required else 'off'}"
    env = {"XT_DESK_AGENT_CLIENT_ID": client_id, "XT_DPOP_REQUIRED": str(required).lower()}
    rep = _provision().desk_agent_client_rep(env, DESK, [])
    assert rep["attributes"]["dpop.bound.access.tokens"] == str(required).lower()
    rep["directAccessGrantsEnabled"] = True  # chỉ cho test headless
    a = f"/admin/realms/{realm.name}"
    kc_admin.ok(kc_admin.c.post(f"{a}/clients", json=rep))
    uid = kc_admin.client_uuid(realm.name, client_id)
    got = kc_admin.ok(kc_admin.c.get(f"{a}/clients/{uid}")).json()
    assert got["attributes"]["dpop.bound.access.tokens"] == str(required).lower()  # KC nhận attr
    scopes = kc_admin.ok(kc_admin.c.get(f"{a}/client-scopes")).json()
    mcp = next(s["id"] for s in scopes if s["name"] == "mcp-xt")
    kc_admin.ok(kc_admin.c.put(f"{a}/clients/{uid}/default-client-scopes/{mcp}"))
    return client_id, required


def token_request(realm: Realm, data: dict[str, str], proof: str | None) -> httpx.Response:
    headers = {"DPoP": proof} if proof else {}
    return httpx.post(f"{realm.oidc}/token", data=data, headers=headers, timeout=30)


def kc_dpop_error(r: httpx.Response) -> bool:
    """Keycloak 26.6 trả `invalid_request` (mô tả "DPoP …") cho proof thiếu/sai ở token endpoint."""
    j = r.json()
    return j.get("error") in ("invalid_dpop_proof", "invalid_request") and "DPoP" in str(
        j.get("error_description", "")
    )


def password(client: str) -> dict[str, str]:
    return {
        "grant_type": "password",
        "client_id": client,
        "username": "bob",  # alice có OTP: direct grant đòi OTP
        "password": "bob-pw",
        "scope": "openid",
    }


def verifier(realm: Realm, client: str, required: bool) -> tuple[AccessTokenVerifier, Any]:
    env = realm.env(
        XT_MCP_ALLOWED_CLIENTS=f"it-agent,{client}",
        XT_DPOP_REQUIRED=str(required).lower(),
        XT_DPOP_CLIENTS=client,
    )
    rs = load_resource_server_settings(env)
    dv = build_dpop_verifier(env)
    return AccessTokenVerifier(rs, build_jwks_cache(rs.idp), dpop=dv), dv


def ctx(proof: str | None, scheme: str = "dpop") -> DpopContext:
    return DpopContext(scheme, (proof,) if proof else (), "POST", frozenset({RESOURCE}))


def test_keycloak_binds_tokens_and_xt_verifies_proof(realm: Realm, desk: tuple[str, bool]) -> None:
    client, required = desk
    token_url = f"{realm.oidc}/token"
    holder, thief = DpopKey(), DpopKey()
    v, _ = verifier(realm, client, required)

    # Không proof: cờ bật -> Keycloak từ chối; cờ tắt -> token Bearer không gắn khóa.
    plain = token_request(realm, password(client), None)
    if required:
        assert plain.status_code == 400, plain.text
        assert kc_dpop_error(plain), plain.text
    else:
        assert plain.status_code == 200, plain.text
        at0 = plain.json()["access_token"]
        assert plain.json()["token_type"].lower() == "bearer" and "cnf" not in _claims(at0)
        assert v.verify(at0, dpop=ctx(None, "bearer")).dpop_jkt is None

    # Có proof: token_type DPoP, typ=DPoP, cnf.jkt = thumbprint RFC 7638 của khóa trình duyệt.
    r = token_request(realm, password(client), holder.proof("POST", token_url))
    assert r.status_code == 200, r.text
    tok = r.json()
    assert tok["token_type"] == "DPoP"
    at = tok["access_token"]
    claims = _claims(at)
    assert claims["typ"] == "DPoP" and claims["cnf"]["jkt"] == holder.jkt

    p = v.verify(at, dpop=ctx(holder.proof("POST", RESOURCE, token=at)))
    assert p.dpop_jkt == holder.jkt and p.client_id == client
    for bad in (
        ctx(holder.proof("POST", RESOURCE, token=at), "bearer"),  # hạ cấp về Bearer
        ctx(thief.proof("POST", RESOURCE, token=at)),  # token bị lộ, khóa khác
        ctx(None),  # thiếu proof
        None,  # bề mặt không DPoP (REST)
    ):
        with pytest.raises(DpopError):
            v.verify(at, dpop=bad)

    # Refresh token (client public) gắn khóa: kẻ trộm không làm mới được; chủ khóa thì được.
    refresh = {
        "grant_type": "refresh_token",
        "client_id": client,
        "refresh_token": tok["refresh_token"],
    }
    stolen = token_request(realm, refresh, thief.proof("POST", token_url))
    assert stolen.status_code == 400, stolen.text
    no_proof = token_request(realm, refresh, None)
    assert no_proof.status_code == 400, no_proof.text
    ok = token_request(realm, refresh, holder.proof("POST", token_url))
    assert ok.status_code == 200, ok.text
    at2 = ok.json()["access_token"]
    assert _claims(at2)["cnf"]["jkt"] == holder.jkt
    assert v.verify(at2, dpop=ctx(holder.proof("POST", RESOURCE, token=at2))).dpop_jkt == holder.jkt


def test_keycloak_rejects_bad_token_endpoint_proofs(realm: Realm, desk: tuple[str, bool]) -> None:
    client, _ = desk
    token_url = f"{realm.oidc}/token"
    holder = DpopKey()
    for proof in (
        holder.proof("POST", "http://localhost:19400/realms/other/protocol/openid-connect/token"),
        holder.proof("GET", token_url),
        holder.proof("POST", token_url, header={"typ": "JWT"}),
    ):
        r = token_request(realm, password(client), proof)
        assert r.status_code == 400, r.text
        assert kc_dpop_error(r), r.text
    replay = holder.proof("POST", token_url)
    assert token_request(realm, password(client), replay).status_code == 200
    assert token_request(realm, password(client), replay).status_code == 400  # jti dùng một lần
