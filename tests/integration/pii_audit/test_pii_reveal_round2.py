# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Vòng 2 audit kênh người xem PII (docs/audit-pii-reveal.md "Vòng 2"): thử vượt các ràng buộc mới
(token gắn nguồn + người/client đã nhận, kiểm lại IAM+RLS, quota vé, khóa khung mới mỗi lần).

Chạy riêng (fixture `stack` tạo tenant `acme`, mỗi phiên pytest chỉ dựng một lần):
    uv run pytest -m integration --no-cov tests/integration/pii_audit/test_pii_reveal_round2.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any

import httpx
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT
from tests.integration.composition.test_e2e import (  # noqa: F401  (fixture `stack`)
    DESK_AGENT,
    DIRECTOR,
    IT_ADMIN,
    LINKED,
    LOCAL_AGENT,
    ORG,
    REQUESTER,
    DeskFrame,
    Stack,
    advance,
    run_admin,
    stack,
    stack_base,
    sub,
)
from xt.adapters.db.integration import person_id

pytestmark = pytest.mark.integration

HR, OUTSIDER = "NV-0026", "NV-0003"
ACCOUNT = "9704225566778899"


@pytest.fixture(scope="module")
def s(stack: Stack, tmp_path_factory: pytest.TempPathFactory) -> Stack:  # noqa: F811
    d = tmp_path_factory.mktemp("pii-r2")
    keys = (*LINKED, HR, OUTSIDER)
    links = {
        "subjects": {k: sub("acme", k) for k in keys},
        "agent_grants": [
            {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
            for k in keys
            for c in (AGENT_CLIENT, LOCAL_AGENT, DESK_AGENT)
        ],
    }
    lf = d / "links.json"
    lf.write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", "acme", str(ORG), "--links", str(lf),
         "--base-roles", "employee"],
        stack.env,
    )  # fmt: skip
    assert rc == 0, out
    return stack


async def mint(
    s: Stack, key: str, tokens: list[str], f: DeskFrame, client: str = DESK_AGENT
) -> Any:
    token = s.idp.access_token(sub("acme", key), org="acme", azp=client)
    async with s.app.session(token) as sess:
        return await sess.call_tool("pii_reveal_ticket", {"tokens": tokens, "pubkey_jwk": f.jwk})


def post(s: Stack, body: dict[str, Any]) -> httpx.Response:
    return httpx.post(
        f"{s.app.base}/pii/reveal",
        content=json.dumps(body),
        headers={"content-type": "application/json", "origin": s.app.base},
        timeout=15,
    )


def signed(f: DeskFrame, ticket: str) -> dict[str, Any]:
    ts = int(time.time())
    return {"ticket": ticket, "ts": ts, "sig": f.sign(ticket, ts)}


_c: dict[str, str] = {}


async def requester_record(s: Stack) -> tuple[str, str]:
    """Đề nghị của REQUESTER; chính REQUESTER nhận token trên Desk chat."""
    if "tok" not in _c:
        c = await s.call(
            "acme",
            REQUESTER,
            "create_record",
            {
                "model_id": "advance_request",
                "values": advance(1_900_000, payout_method="bank_transfer", bank_account=ACCOUNT),
                "idempotency_key": "pii-r2-1",
            },
            client=DESK_AGENT,
        )
        assert c["ok"], c
        _c["tok"] = c["data"]["record"]["values"]["bank_account"]
        _c["ref"] = c["data"]["record"]["ref"]
    return _c["tok"], _c["ref"]


# ------------------------------------------------ 1. token A nhận, B dùng (cùng tenant) ----
@pytest.mark.anyio
async def test_token_received_by_a_unusable_by_b(s: Stack) -> None:
    tok, _ = await requester_record(s)
    for b in (HR, OUTSIDER, DIRECTOR):  # DIRECTOR đọc được bản ghi nhưng CHƯA nhận token
        r = await mint(s, b, [tok], DeskFrame())
        assert r.is_error and "PII_TOKEN_UNKNOWN" in r.content[0].text, b


# ---------------------------------------------- 2. đổi client: nhận ở cloud, xin ở Desk ----
@pytest.mark.anyio
async def test_token_seen_via_cloud_client_not_mintable_on_desk(s: Stack) -> None:
    tok, ref = await requester_record(s)
    got = await s.call("acme", DIRECTOR, "get_record", {"ref": ref}, client=AGENT_CLIENT)
    assert got["ok"] and got["data"]["record"]["values"]["bank_account"] == tok
    r = await mint(s, DIRECTOR, [tok], DeskFrame())
    assert r.is_error and "PII_TOKEN_UNKNOWN" in r.content[0].text
    # và token Desk của REQUESTER không dùng được qua client local/cloud
    for client in (AGENT_CLIENT, LOCAL_AGENT):
        r = await mint(s, REQUESTER, [tok], DeskFrame(), client=client)
        assert r.is_error and "client_not_allowed" in r.content[0].text


# ------------------------------------------ 3. mất quyền đọc bản ghi sau khi đã nhận token ----
@pytest.mark.anyio
async def test_record_access_revoked_after_token_issued(s: Stack, pg: pgfixture.Pg) -> None:
    tok, ref = await requester_record(s)
    got = await s.call("acme", DIRECTOR, "get_record", {"ref": ref}, client=DESK_AGENT)
    assert got["ok"]
    tid = s.tenants["acme"].id
    pid = person_id(tid, DIRECTOR)
    with pg.su() as su:
        roles = su.execute("SELECT roles FROM xt_meta.person WHERE id=%s", (pid,)).fetchone()[0]
    # mint TRƯỚC khi mất quyền, đổi SAU
    f = DeskFrame()
    r = await mint(s, DIRECTOR, [tok], f)
    assert not r.is_error, r.content[0].text
    with pg.su() as su:
        su.execute("UPDATE xt_meta.person SET roles = '{employee}' WHERE id = %s", (pid,))
    try:
        res = post(s, signed(f, r.meta["xt/reveal"]["ticket"]))
        assert res.json() == {"values": {}, "denied": [tok]}, res.text
        # mint SAU khi mất quyền: vẫn cấp vé (đã thấy) nhưng đổi ra denied
        f2 = DeskFrame()
        r2 = await mint(s, DIRECTOR, [tok], f2)
        if not r2.is_error:
            res = post(s, signed(f2, r2.meta["xt/reveal"]["ticket"]))
            assert res.json()["values"] == {}
    finally:
        with pg.su() as su:
            su.execute("UPDATE xt_meta.person SET roles = %s WHERE id = %s", (roles, pid))


@pytest.mark.anyio
async def test_owner_without_pii_reveal_cannot_open_own_sensitive(s: Stack) -> None:
    """Chính chủ (nhân viên) đọc được bản ghi nhưng field sensitive cần `pii.reveal`."""
    tok, _ = await requester_record(s)
    f = DeskFrame()
    r = await mint(s, REQUESTER, [tok], f)
    assert not r.is_error
    assert post(s, signed(f, r.meta["xt/reveal"]["ticket"])).json() == {
        "values": {},
        "denied": [tok],
    }


# ---------------------------------------------------- 4. phát lại vé với khóa mới ----
@pytest.mark.anyio
async def test_replay_used_ticket_with_fresh_key(s: Stack) -> None:
    tok, ref = await requester_record(s)
    await s.call("acme", DIRECTOR, "get_record", {"ref": ref}, client=DESK_AGENT)
    f = DeskFrame()
    r = await mint(s, DIRECTOR, [tok], f)
    ticket = r.meta["xt/reveal"]["ticket"]
    assert post(s, signed(f, ticket)).status_code == 200
    for frame in (DeskFrame(), f):
        res = post(s, signed(frame, ticket))
        assert res.status_code == 403 and res.json() == {"error": "TICKET_INVALID"}


def _noncanonical(b64u: str) -> str:
    """Cùng 32 byte, ký tự cuối khác (2 bit đệm của base64url 43 ký tự khác 0)."""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    i = alphabet.index(b64u[-1])
    return b64u[:-1] + alphabet[(i & ~3) | ((i + 1) & 3)]


@pytest.mark.anyio
async def test_key_reuse_check_not_bypassed_by_noncanonical_base64(s: Stack) -> None:
    tok, ref = await requester_record(s)
    await s.call("acme", DIRECTOR, "get_record", {"ref": ref}, client=DESK_AGENT)
    f = DeskFrame()
    assert not (await mint(s, DIRECTOR, [tok], f)).is_error
    assert base64.urlsafe_b64decode(_noncanonical(f.jwk["x"]) + "=") == base64.urlsafe_b64decode(
        f.jwk["x"] + "="
    )
    f.jwk = {**f.jwk, "x": _noncanonical(f.jwk["x"])}
    r = await mint(s, DIRECTOR, [tok], f)
    # Sửa R2-02: base64url không chuẩn tắc bị từ chối (BAD_PUBKEY); thumbprint RFC 7638 trên toạ độ
    # đã giải mã nên cùng điểm ở dạng chuẩn tắc là KEY_REUSED.
    text = r.content[0].text
    assert r.is_error and ("BAD_PUBKEY" in text or "KEY_REUSED" in text), "khóa cũ được nhận lại"


# --------------------------------------------- 5. quota vé: song song / nhiều client ----
@pytest.mark.anyio
async def test_ticket_quota_holds_under_concurrency(s: Stack) -> None:
    pref = f"person:{person_id(s.tenants['acme'].id, REQUESTER)}"
    async with s.app.session(s.token("acme", IT_ADMIN, DESK_AGENT)) as sess:
        res = await sess.call_tool("org_person_details", {"ref": pref})
    email = json.loads(res.content[0].text)["data"]["email"]
    limit = int(s.env.get("XT_PII_REVEAL_TICKETS_PER_MINUTE", "60"))

    async def one() -> bool:
        try:
            return not (await mint(s, IT_ADMIN, [email], DeskFrame())).is_error
        except Exception:
            return False

    ok = 0
    for _ in range(3):  # 3 đợt x 40 lời gọi song song
        ok += sum(await asyncio.gather(*(one() for _ in range(40))))
    assert ok <= limit, f"{ok} vé > quota {limit}/phút"


# ------------------------------------------------------------- 6. migration 0007 ----
def test_migration_0007_reapply_is_idempotent(s: Stack, pg: pgfixture.Pg) -> None:
    from importlib.resources import files

    body = files("xt.adapters.db").joinpath("sql/migrations/0007_pii_reveal.sql").read_text("utf-8")
    with pg.migrator() as m, m.transaction():
        m.execute(body.encode())
