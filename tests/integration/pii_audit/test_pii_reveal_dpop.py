# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""DPoP cho Desk (docs/decisions.md "DPoP cho Desk trước v1.0"): `xt serve` thật + PG17 + IdP JWKS
tĩnh, chạy với `XT_DPOP_REQUIRED` tắt và bật. Token gắn `cnf.jkt`, /mcp đòi proof, vé PII gắn jkt
và `/pii/reveal` phải ký bằng chính khóa DPoP đó (audit-pii-reveal.md "Vòng 2", rủi ro tồn dư).

    uv run pytest -m integration --no-cov tests/integration/pii_audit/test_pii_reveal_dpop.py
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import anyio.to_thread
import httpx
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from tests.integration.composition.fixtures import (
    DESK_AGENT,
    DIRECTOR,
    REQUESTER,
    StackBase,
    sub,
)
from tests.integration.composition.idp import LocalIdp
from tests.integration.composition.mcphttp import RunningApp, envelope, serve
from tests.integration.composition.test_e2e import DeskFrame, advance
from tests.unit.identity.dpop_client import DpopKey
from xt.entrypoints.serve import prepare

pytestmark = pytest.mark.integration
ACCOUNT = "9704221122334455"


@dataclass
class DStack:
    idp: LocalIdp
    app: RunningApp
    required: bool
    prepared: Any = None

    @property
    def reveal_url(self) -> str:
        return f"{self.app.base}/pii/reveal"

    def bound(self, key: str, holder: DpopKey) -> str:
        return self.idp.access_token(
            sub("acme", key), org="acme", azp=DESK_AGENT, typ="DPoP", cnf={"jkt": holder.jkt}
        )

    def bearer(self, key: str) -> str:
        return self.idp.access_token(sub("acme", key), org="acme", azp=DESK_AGENT)

    @asynccontextmanager
    async def session(self, token: str, holder: DpopKey | None) -> AsyncIterator[ClientSession]:
        async def add_proof(request: httpx2.Request) -> None:
            assert holder is not None
            request.headers["Authorization"] = f"DPoP {token}"
            request.headers["DPoP"] = holder.proof(
                request.method, f"{self.app.base}{request.url.path}", token=token
            )

        kw: dict[str, Any] = (
            {"event_hooks": {"request": [add_proof]}}
            if holder
            else {"headers": {"Authorization": f"Bearer {token}"}}
        )
        async with (
            httpx2.AsyncClient(timeout=30, **kw) as http,
            streamable_http_client(self.app.mcp_url, http_client=http) as (read, write),
            ClientSession(read, write) as s,
        ):
            await s.initialize()
            yield s


@pytest.fixture(scope="module", params=[False, True], ids=["flag-off", "flag-on"])
def ds(
    request: pytest.FixtureRequest, stack_base: StackBase, idp: LocalIdp, mcp_port: int
) -> Iterator[DStack]:
    required = bool(request.param)
    env = {**stack_base.env, "XT_DPOP_REQUIRED": "true" if required else "false"}
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    app = serve(prepared.http_app(), mcp_port)
    try:
        yield DStack(idp, app, required, prepared)
    finally:
        app.stop()
        prepared.close()


def post(ds: DStack, body: dict[str, Any], proof: str | None) -> httpx.Response:
    headers = {"content-type": "application/json", "origin": ds.app.base}
    if proof is not None:
        headers["DPoP"] = proof
    return httpx.post(ds.reveal_url, content=json.dumps(body), headers=headers, timeout=15)


async def seen_token(ds: DStack, holder: DpopKey, n: int) -> str:
    """REQUESTER tạo đề nghị trên Desk (gắn DPoP); DIRECTOR mở bản ghi trên Desk (đã thấy token)."""
    rq = DpopKey()
    async with ds.session(ds.bound(REQUESTER, rq), rq) as s:
        c = envelope(
            await s.call_tool(
                "create_record",
                {
                    "model_id": "advance_request",
                    "values": advance(
                        1_700_000 + n, payout_method="bank_transfer", bank_account=ACCOUNT
                    ),
                    "idempotency_key": f"dpop-{int(ds.required)}-{n}",
                },
            )
        )
    assert c["ok"], c
    tok = str(c["data"]["record"]["values"]["bank_account"])
    async with ds.session(ds.bound(DIRECTOR, holder), holder) as s:
        got = envelope(await s.call_tool("get_record", {"ref": c["data"]["record"]["ref"]}))
    assert got["ok"] and got["data"]["record"]["values"]["bank_account"] == tok
    return tok


async def mint(ds: DStack, holder: DpopKey, tokens: list[str], **extra: Any) -> Any:
    async with ds.session(ds.bound(DIRECTOR, holder), holder) as s:
        return await s.call_tool("pii_reveal_ticket", {"tokens": tokens, **extra})


@pytest.mark.anyio
async def test_dpop_bound_reveal_roundtrip_and_attacks(ds: DStack) -> None:
    holder = DpopKey()
    tok = await seen_token(ds, holder, 1)

    # Vé gắn khóa DPoP của phiên: gửi kèm khóa khung tùy ý bị từ chối.
    bad = await mint(ds, holder, [tok], pubkey_jwk=DeskFrame().jwk)
    assert bad.is_error and "DPOP_BOUND" in bad.content[0].text

    pre = httpx.options(
        ds.reveal_url,
        headers={
            "origin": ds.app.base,
            "access-control-request-method": "POST",
            "access-control-request-headers": "content-type, dpop",
        },
    )
    assert pre.status_code == 204 and "dpop" in pre.headers["access-control-allow-headers"]

    async def ticket() -> str:
        m = await mint(ds, holder, [tok])
        assert not m.is_error, m
        assert "pubkey_jwk" not in json.dumps(m.structured_content)
        return str(m.meta["xt/reveal"]["ticket"])

    # 1. Hợp lệ: proof bằng đúng khóa, ath = hash(vé), htu = URL /pii/reveal.
    t1 = await ticket()
    ok = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t1}, holder.proof("POST", ds.reveal_url, token=t1)
    )
    assert ok.status_code == 200 and ok.json() == {"values": {tok: ACCOUNT}, "denied": []}

    # 2. Vé bị lộ + access token bị lộ nhưng không có khóa riêng: proof bằng khóa kẻ trộm.
    t2 = await ticket()
    thief = DpopKey()
    r = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t2}, thief.proof("POST", ds.reveal_url, token=t2)
    )
    assert r.status_code == 403 and r.json() == {"error": "BAD_DPOP_PROOF"}
    retry = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t2}, holder.proof("POST", ds.reveal_url, token=t2)
    )
    assert retry.status_code == 403 and retry.json() == {"error": "TICKET_INVALID"}  # một lần thử

    # 3. Kẻ trộm tự xin vé bằng access token lấy được: /mcp đòi proof đúng cnf.jkt -> 401.
    stolen = ds.bound(DIRECTOR, holder)
    with pytest.raises(Exception):  # noqa: B017 - client mcp báo lỗi HTTP 401
        async with ds.session(stolen, thief) as s:
            await s.list_tools()

    # 4. Proof sai ràng buộc: ath của vé khác, htu khác, htm khác, thiếu proof, dạng cũ ts/sig.
    for make in (
        lambda t: holder.proof("POST", ds.reveal_url, token=t + "x"),
        lambda t: holder.proof("POST", f"{ds.app.base}/mcp", token=t),
        lambda t: holder.proof("PUT", ds.reveal_url, token=t),
    ):
        t = await ticket()
        r = await anyio.to_thread.run_sync(post, ds, {"ticket": t}, make(t))
        assert r.status_code == 403 and r.json() == {"error": "BAD_DPOP_PROOF"}
    t = await ticket()
    r = await anyio.to_thread.run_sync(post, ds, {"ticket": t}, None)
    assert r.status_code == 400
    f = DeskFrame()
    ts = int(time.time())
    r = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t, "ts": ts, "sig": f.sign(t, ts)}, None
    )
    assert r.status_code == 403 and r.json() == {"error": "DPOP_REQUIRED"}

    # 5. Proof cũ (iat quá cửa sổ): STALE, vé chưa bị tiêu, proof mới thì được.
    t5 = await ticket()
    old = holder.proof("POST", ds.reveal_url, token=t5, iat=int(time.time()) - 600)
    r = await anyio.to_thread.run_sync(post, ds, {"ticket": t5}, old)
    assert r.status_code == 403 and r.json() == {"error": "STALE_REQUEST"}
    r = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t5}, holder.proof("POST", ds.reveal_url, token=t5)
    )
    assert r.status_code == 200 and r.json()["values"] == {tok: ACCOUNT}

    # 6. Phát lại nguyên request hợp lệ (cùng jti) -> vé đã dùng.
    t6 = await ticket()
    p6 = holder.proof("POST", ds.reveal_url, token=t6, jti="replay-jti")
    assert (await anyio.to_thread.run_sync(post, ds, {"ticket": t6}, p6)).status_code == 200
    again = await anyio.to_thread.run_sync(post, ds, {"ticket": t6}, p6)
    assert again.status_code == 403


@pytest.mark.anyio
async def test_unbound_desk_token_follows_flag(ds: DStack) -> None:
    """Tắt cờ: token Bearer cũ của Desk + khóa khung vẫn chạy (tương thích ngược). Bật cờ: /mcp từ
    chối token không gắn khóa của xt-desk-agent ngay ở cổng (401 DPoP)."""
    holder = DpopKey()
    tok = await seen_token(ds, holder, 2)
    plain = ds.bearer(DIRECTOR)
    probe = httpx.post(
        ds.app.mcp_url,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={
            "authorization": f"Bearer {plain}",
            "accept": "application/json, text/event-stream",
        },
        timeout=15,
    )
    if ds.required:
        assert probe.status_code == 401
        assert probe.headers["www-authenticate"].startswith('DPoP error="invalid_token"')
        return
    assert probe.status_code == 200
    # Token PII gắn (người + client): DIRECTOR đã thấy nó ở trên, cùng client xt-desk-agent.
    f = DeskFrame()
    async with ds.session(plain, None) as s:
        m = await s.call_tool("pii_reveal_ticket", {"tokens": [tok], "pubkey_jwk": f.jwk})
    assert not m.is_error, m
    t = m.meta["xt/reveal"]["ticket"]
    ts = int(time.time())
    r = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t, "ts": ts, "sig": f.sign(t, ts)}, None
    )
    assert r.status_code == 200 and r.json()["values"] == {tok: ACCOUNT}
    # Vé khung không bao giờ đổi được bằng proof DPoP (khóa khác loại ràng buộc).
    async with ds.session(plain, None) as s:
        m2 = await s.call_tool(
            "pii_reveal_ticket", {"tokens": [tok], "pubkey_jwk": DeskFrame().jwk}
        )
    t2 = m2.meta["xt/reveal"]["ticket"]
    r = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t2}, holder.proof("POST", ds.reveal_url, token=t2)
    )
    assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}


class _DownStore:
    def consume(self, key: str, expires_at: int | None) -> bool:
        raise ConnectionError("db down")


@pytest.mark.anyio
async def test_replay_store_outage_is_503_and_ticket_survives(ds: DStack) -> None:
    """DPA-08 (ghi chú): store jti lỗi -> /pii/reveal 503 + Retry-After, vé KHÔNG bị tiêu."""
    holder = DpopKey()
    tok = await seen_token(ds, holder, 3)
    m = await mint(ds, holder, [tok])
    assert not m.is_error, m
    t = str(m.meta["xt/reveal"]["ticket"])
    reveal = ds.prepared.services.reveal()
    real = reveal.dpop._replay
    reveal.dpop._replay = _DownStore()
    try:
        r = await anyio.to_thread.run_sync(
            post, ds, {"ticket": t}, holder.proof("POST", ds.reveal_url, token=t)
        )
    finally:
        reveal.dpop._replay = real
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert r.json() == {"error": "TEMPORARILY_UNAVAILABLE"}
    ok = await anyio.to_thread.run_sync(
        post, ds, {"ticket": t}, holder.proof("POST", ds.reveal_url, token=t)
    )
    assert ok.status_code == 200 and ok.json()["values"] == {tok: ACCOUNT}
