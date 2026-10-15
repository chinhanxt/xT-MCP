# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit DPoP trên dây: hạ cấp Bearer ở mọi bề mặt xác thực bằng `AccessTokenVerifier`
(/mcp, /workflow/v1, /files/v1), Host header giả, và lỗi 500 với iat=NaN (DPA-01)."""

from __future__ import annotations

import json
from typing import Any

import httpx2
import jwt
import pytest

from tests.unit.identity.dpop_client import DpopKey, ath
from tests.unit.identity.factory import access_claims, rs_settings, sign
from tests.unit.mcp.conftest import MCP_URL
from tests.unit.mcp.test_mcp_dpop import DESK, DESK_ORIGIN, JSONRPC, LIST, Env, running
from xt.adapters.identity import AccessTokenVerifier, DpopError, DpopSettings, DpopVerifier
from xt.adapters.identity.jwks import JwksCache
from xt.entrypoints.files_http import IdentityFilesAuth
from xt.entrypoints.workflow_http import IdentityWorkflowAuth

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(params=[False, True], ids=["flag-off", "flag-on"])
def env(request: pytest.FixtureRequest) -> Env:
    return Env(bool(request.param))


async def _post(env: Env, headers: dict[str, str], **kw: Any) -> httpx2.Response:
    async with env.http(**kw) as c:
        return await c.post("/mcp", json=LIST, headers={**JSONRPC, **headers})


async def test_bound_token_as_bearer_with_valid_proof_rejected(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    async with running(env):
        r = await _post(
            env,
            {"Authorization": f"Bearer {tok}", "DPoP": holder.proof("POST", MCP_URL, token=tok)},
        )
    assert r.status_code == 401
    assert r.headers["www-authenticate"].startswith('DPoP error="invalid_token"')


async def test_unbound_token_with_dpop_scheme_rejected(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bearer(DESK)
    async with running(env):
        r = await _post(
            env, {"Authorization": f"DPoP {tok}", "DPoP": holder.proof("POST", MCP_URL, token=tok)}
        )
    assert r.status_code == 401


async def test_spoofed_host_header_does_not_change_htu(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    proof = holder.proof("POST", "https://evil.example/mcp", token=tok)
    async with running(env):
        r = await _post(
            env, {"Authorization": f"DPoP {tok}", "DPoP": proof, "Host": "evil.example"}
        )
        r2 = await _post(env, {"Authorization": f"DPoP {tok}", "DPoP": proof})
    assert r.status_code in (400, 401, 421)
    assert r2.status_code == 401


async def test_desk_origin_proof_replay_on_mcp_origin_blocked(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    hdr = {
        "Authorization": f"DPoP {tok}",
        "DPoP": holder.proof("POST", f"{DESK_ORIGIN}/mcp", token=tok),
    }
    async with running(env):
        first = await _post(env, hdr)
        again = await _post(env, hdr)
    assert first.status_code == 200 and again.status_code == 401


async def test_nan_iat_gets_401_not_500(env: Env) -> None:
    holder = DpopKey(env.clock)
    tok = env.bound(holder)
    claims = {
        "jti": "nan-1",
        "htm": "POST",
        "htu": MCP_URL,
        "iat": float("nan"),
        "ath": ath(tok),
    }
    proof = jwt.encode(
        claims, holder.private, algorithm="ES256", headers={"typ": "dpop+jwt", "jwk": holder.jwk}
    )
    assert '"iat": NaN' in json.dumps(claims)
    async with running(env):
        try:
            r = await _post(env, {"Authorization": f"DPoP {tok}", "DPoP": proof})
            status = r.status_code
        except Exception:  # ASGITransport ném lại lỗi ứng dụng (= 500 thật)
            status = 500
    assert status == 401


# ----------------------------------------------- REST: token gắn khóa luôn bị từ chối ----
@pytest.mark.parametrize("surface", ["workflow", "files"])
def test_rest_surfaces_reject_bound_token(
    surface: str, jwks: JwksCache, rsa_key: Any, clock: Any
) -> None:
    holder = DpopKey(clock)
    rs = rs_settings(allowed_clients=frozenset({DESK}))
    dv = DpopVerifier(DpopSettings(origins=("https://xt.example.test",)), clock=clock)
    for v in (AccessTokenVerifier(rs, jwks, clock), AccessTokenVerifier(rs, jwks, clock, dpop=dv)):
        auth = IdentityWorkflowAuth(rs, v) if surface == "workflow" else IdentityFilesAuth(rs, v)
        tok = sign(rsa_key, access_claims(azp=DESK, typ="DPoP", cnf={"jkt": holder.jkt}))
        with pytest.raises(DpopError) as ei:
            auth.verify(tok)
        assert ei.value.reason == "dpop_unsupported"
