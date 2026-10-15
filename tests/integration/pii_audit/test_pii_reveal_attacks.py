# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Tấn công thật kênh người xem PII (`pii_reveal_ticket` + `POST /pii/reveal`).

Báo cáo: docs/audit-pii-reveal.md.

xT chạy trong tiến trình (uvicorn) + Postgres 17 thật (container tên/cổng ngẫu nhiên) + client `mcp`
thật qua Streamable HTTP + httpx cho /pii/reveal. Ca PASS là test thường; lỗi đã tìm thấy là
`xfail(strict=True)`: khi sửa xong test sẽ XPASS -> đỏ, nhắc bỏ marker.
"""

from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import json
import logging
import re
import time
from typing import Any

import httpx
import pytest

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT
from tests.integration.composition.mcphttp import envelope
from tests.integration.composition.test_e2e import (  # noqa: F401  (fixture `stack`)
    DESK_AGENT,
    DIRECTOR,
    LINKED,
    LOCAL_AGENT,
    ORG,
    REQUESTER,
    DeskFrame,
    Stack,
    advance,
    run_admin,
    stack,
    sub,
)
from xt.adapters.db.integration import person_id

pytestmark = pytest.mark.integration

HR = "NV-0026"  # vai trò hr: có pii.reveal (gói kenh-pii) nhưng không phải người duyệt/đọc đề nghị
OUTSIDER = "NV-0003"  # nhân viên phòng D01, không liên quan đề nghị của REQUESTER (D03)
EXTRA = (HR, OUTSIDER)
TOKEN_RE = re.compile(r"\[PII:[a-z2-7]{16}\]")
ACCOUNT = "9704221100223344"


# ------------------------------------------------------------------------- tiện ích ----
@pytest.fixture(scope="module")
def s(stack: Stack, tmp_path_factory: pytest.TempPathFactory) -> Stack:  # noqa: F811
    """Stack của test_e2e + liên kết thêm HR và một nhân viên ngoài cuộc (nhập lại tệp tổ chức)."""
    d = tmp_path_factory.mktemp("pii-audit")
    keys = (*LINKED, *EXTRA)
    links = {
        "subjects": {k: sub("acme", k) for k in keys},
        "agent_grants": [
            {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
            for k in keys
            for c in (AGENT_CLIENT, LOCAL_AGENT, DESK_AGENT)
        ],
    }
    lf = d / "links-acme.json"
    lf.write_text(json.dumps(links), encoding="utf-8")
    rc, out = run_admin(
        ["org", "import", "--tenant", "acme", str(ORG), "--links", str(lf),
         "--base-roles", "employee"],
        stack.env,
    )  # fmt: skip
    assert rc == 0, out
    return stack


async def mint(s: Stack, key: str, tokens: list[str], frame: DeskFrame, *, client: str = DESK_AGENT,
               alias: str = "acme") -> Any:  # fmt: skip
    async with s.app.session(s.idp.access_token(sub(alias, key), org=alias, azp=client)) as sess:
        return await sess.call_tool(
            "pii_reveal_ticket", {"tokens": tokens, "pubkey_jwk": frame.jwk}
        )


def post(s: Stack, body: Any, origin: str | None = "", **headers: str) -> httpx.Response:
    h = {"content-type": "application/json", **headers}
    if origin == "":
        origin = s.app.base
    if origin is not None:
        h["origin"] = origin
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    return httpx.post(f"{s.app.base}/pii/reveal", content=data, headers=h, timeout=15)


def signed(frame: DeskFrame, ticket: str, ts: int | None = None) -> dict[str, Any]:
    ts = int(time.time()) if ts is None else ts
    return {"ticket": ticket, "ts": ts, "sig": frame.sign(ticket, ts)}


async def ticket_for(s: Stack, key: str, tokens: list[str]) -> tuple[str, DeskFrame]:
    f = DeskFrame()
    r = await mint(s, key, tokens, f)
    assert not r.is_error, r.content[0].text
    return r.meta["xt/reveal"]["ticket"], f


_cache: dict[str, str] = {}


async def bank_token(s: Stack) -> str:
    """Đề nghị tạm ứng của REQUESTER (Desk chat = masked): token của field sensitive."""
    if "bank" not in _cache:
        c = await s.call(
            "acme",
            REQUESTER,
            "create_record",
            {
                "model_id": "advance_request",
                "values": advance(3_100_000, payout_method="bank_transfer", bank_account=ACCOUNT),
                "idempotency_key": "pii-audit-bank-1",
            },
            client=DESK_AGENT,
        )
        assert c["ok"], c
        _cache["bank"] = c["data"]["record"]["values"]["bank_account"]
        _cache["ref"] = c["data"]["record"]["ref"]
    return _cache["bank"]


async def email_token(s: Stack) -> str:
    """Email (basic) của REQUESTER qua org_person_details trên Desk chat."""
    if "email" not in _cache:
        async with s.app.session(s.token("acme", REQUESTER, DESK_AGENT)) as sess:
            me = envelope(await sess.call_tool("inspect_workspace", {}))["data"]["me"]["ref"]
            env = envelope(await sess.call_tool("org_person_details", {"ref": me}))
        assert env["ok"], env
        _cache["email"] = env["data"]["email"]
    return _cache["email"]


def egress(pg: pgfixture.Pg) -> list[tuple[Any, ...]]:
    with pg.su() as su:
        return su.execute(
            "SELECT trace_id, fields, person, client_class, mode FROM xt_meta.pii_egress"
            " WHERE tool = 'pii_reveal' ORDER BY at"
        ).fetchall()


# ------------------------------------------- 1. LLM/agent không lấy được vé / giá trị ----
@pytest.mark.anyio
async def test_ticket_only_in_meta_never_in_model_channels(
    s: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    tok = await bank_token(s)
    f = DeskFrame()
    r = await mint(s, DIRECTOR, [tok], f)
    assert not r.is_error
    ticket = r.meta["xt/reveal"]["ticket"]
    assert len(base64.urlsafe_b64decode(ticket + "=")) == 32  # 256 bit
    assert ticket not in r.content[0].text
    assert ticket not in json.dumps(r.structured_content)
    assert r.structured_content["data"] == {"tokens": 1, "expires_in_seconds": 60}
    # đổi vé thành giá trị; giá trị/vé không xuất hiện trong log của tiến trình xT
    ok = post(s, signed(f, ticket))
    assert ok.status_code == 200 and ok.json()["values"] == {tok: ACCOUNT}
    leaks = sorted(
        {r.name for r in caplog.records if ticket in r.getMessage() or ACCOUNT in r.getMessage()}
    )
    # chỉ logger phía client (SDK `mcp` client / httpx ở DEBUG) được phép thấy vé
    assert all(n.startswith(("mcp.client", "httpx", "httpcore")) for n in leaks), leaks
    # kết quả tool của chính chủ (Desk chat, masked) chỉ có token
    st = await s.call("acme", REQUESTER, "get_record", {"ref": _cache["ref"]}, client=DESK_AGENT)
    assert ACCOUNT not in json.dumps(st) and st["data"]["record"]["values"]["bank_account"] == tok


@pytest.mark.anyio
async def test_mint_denied_for_every_non_desk_client(s: Stack) -> None:
    tok = await bank_token(s)
    for client in (AGENT_CLIENT, LOCAL_AGENT):  # host cloud (claude.ai/ChatGPT) và BFF local
        r = await mint(s, DIRECTOR, [tok], DeskFrame(), client=client)
        text = r.content[0].text
        assert r.is_error and "client_not_allowed" in text
        assert "xt/reveal" not in (r.meta or {}) and ACCOUNT not in text
    # token phiên người (client xác nhận) không được vào /mcp
    try:
        r = await mint(s, DIRECTOR, [tok], DeskFrame(), client=DESK_CLIENT)
    except Exception as e:  # MCPError / ExceptionGroup: phiên bị từ chối trước tools/call
        assert "xt/reveal" not in repr(e)
    else:
        assert r.is_error and "xt/reveal" not in (r.meta or {})


@pytest.mark.anyio
async def test_error_envelopes_carry_no_ticket_or_token_list(s: Stack) -> None:
    tok = await bank_token(s)
    unknown = "[PII:aaaaaaaaaaaaaaaa]"
    r = await mint(s, DIRECTOR, [tok, unknown], DeskFrame())
    assert r.is_error and r.structured_content is None
    assert "xt/reveal" not in (r.meta or {})
    text = r.content[0].text
    assert "PII_TOKEN_UNKNOWN" in text and unknown not in text and tok not in text
    # sai schema / khóa sai
    bad = DeskFrame()
    bad.jwk = {**bad.jwk, "y": bad.jwk["x"]}  # điểm không nằm trên đường cong
    r = await mint(s, DIRECTOR, [tok], bad)
    assert r.is_error and "BAD_PUBKEY" in r.content[0].text
    r = await mint(s, DIRECTOR, [f"[PII:{'a' * 16}]"] * 2, DeskFrame())  # uniqueItems
    assert r.is_error


# --------------------------------------------------------- 2. vé: một lần, TTL, ts ----
@pytest.mark.anyio
async def test_ticket_single_use_under_concurrency(s: Stack) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    body = signed(f, ticket)
    with concurrent.futures.ThreadPoolExecutor(16) as ex:
        res = list(ex.map(lambda _: post(s, body), range(16)))
    codes = sorted(r.status_code for r in res)
    assert codes.count(200) == 1, [(r.status_code, r.text) for r in res]
    assert all(r.json() == {"error": "TICKET_INVALID"} for r in res if r.status_code != 200)


@pytest.mark.anyio
async def test_bad_signature_burns_ticket(s: Stack) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    other = DeskFrame()
    r = post(s, signed(other, ticket))  # thay khóa
    assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}
    r = post(s, signed(f, ticket))  # khóa đúng nhưng vé đã tiêu
    assert r.status_code == 403 and r.json() == {"error": "TICKET_INVALID"}


@pytest.mark.anyio
async def test_signature_bound_to_ticket_and_ts(s: Stack) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    ts = int(time.time())
    # chữ ký cho ts khác
    r = post(s, {"ticket": ticket, "ts": ts, "sig": f.sign(ticket, ts - 1)})
    assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    # chữ ký DER / độ dài sai / rỗng
    for sig in ("", "A" * 86, base64.urlsafe_b64encode(b"\x30" * 70).decode().rstrip("=")):
        ticket, f = await ticket_for(s, DIRECTOR, [tok])
        r = post(s, {"ticket": ticket, "ts": ts, "sig": sig})
        assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}, sig
    # r = s = 0 (chữ ký tầm thường)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    zero = base64.urlsafe_b64encode(b"\x00" * 64).decode().rstrip("=")
    r = post(s, {"ticket": ticket, "ts": ts, "sig": zero})
    assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}


@pytest.mark.anyio
async def test_ts_skew_window(s: Stack) -> None:
    tok = await bank_token(s)
    now = int(time.time())
    for delta in (-28, 28):
        ticket, f = await ticket_for(s, DIRECTOR, [tok])
        r = post(s, signed(f, ticket, now + delta))
        assert r.status_code == 200, (delta, r.text)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    for delta in (-35, 35, 10**12):
        r = post(s, signed(f, ticket, now + delta))
        assert r.status_code == 403 and r.json() == {"error": "STALE_REQUEST"}, delta
    for bad_ts in (True, 1.5e9, str(now), None):
        r = post(s, {"ticket": ticket, "ts": bad_ts, "sig": f.sign(ticket, now)})
        assert r.status_code == 400 and r.json() == {"error": "BAD_REQUEST"}, bad_ts
    # STALE không tiêu vé (chưa qua bước khóa) -> vẫn đổi được trong TTL
    r = post(s, signed(f, ticket))
    assert r.status_code == 200


@pytest.mark.anyio
async def test_ticket_expires_after_ttl(s: Stack, pg: pgfixture.Pg) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    h = hashlib.sha256(ticket.encode()).digest()
    with pg.su() as su:
        row = su.execute(
            "SELECT expires_at - created_at FROM xt_meta.pii_reveal_ticket WHERE ticket_hash=%s",
            (h,),
        ).fetchone()
        assert row is not None and 59 <= row[0].total_seconds() <= 61
        su.execute(
            "UPDATE xt_meta.pii_reveal_ticket SET expires_at = clock_timestamp() - interval '1 s'"
            " WHERE ticket_hash = %s",
            (h,),
        )
    r = post(s, signed(f, ticket))
    assert r.status_code == 403 and r.json() == {"error": "TICKET_INVALID"}


# --------------------------------------------------------------------- 3. CORS ----
@pytest.mark.anyio
async def test_cors_only_exact_desk_origin(s: Stack) -> None:
    tok = await bank_token(s)
    base = s.app.base
    evil = ("null", "*", "https://evil.example", base + ".evil.example", base.upper(), base + "/",
            base.replace("127.0.0.1", "localhost"), f"{base} https://evil.example")  # fmt: skip
    for o in evil:
        pre = httpx.options(
            f"{base}/pii/reveal", headers={"origin": o, "access-control-request-method": "POST"}
        )
        assert pre.status_code == 403 and "access-control-allow-origin" not in pre.headers, o
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    body = signed(f, ticket)
    for o in (*evil, None):
        r = post(s, body, o)
        assert r.status_code == 403 and r.json() == {"error": "BAD_ORIGIN"}, o
        assert "access-control-allow-origin" not in r.headers
    # simple request (text/plain, không preflight) cũng bị chặn trước khi chạm vé
    r = post(s, json.dumps(body), base, **{"content-type": "text/plain"})
    assert r.status_code == 400
    ok = post(s, body)
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == base
    assert "access-control-allow-credentials" not in ok.headers
    assert ok.headers["cache-control"] == "no-store" and ok.headers["vary"] == "Origin"


# ------------------------------------------------------ 4. tenant / người / quyền ----
@pytest.mark.anyio
async def test_cross_tenant_token_and_ticket(s: Stack) -> None:
    tok = await bank_token(s)  # token của tenant acme
    r = await mint(s, DIRECTOR, [tok], DeskFrame(), alias="beta")
    assert r.is_error and "PII_TOKEN_UNKNOWN" in r.content[0].text


@pytest.mark.anyio
async def test_sensitive_needs_pii_reveal_basic_for_any_member(s: Stack) -> None:
    bank, email = await bank_token(s), await email_token(s)
    ticket, f = await ticket_for(s, REQUESTER, [bank, email])  # nhân viên: không có pii.reveal
    r = post(s, signed(f, ticket))
    assert r.status_code == 200
    assert r.json()["denied"] == [bank] and set(r.json()["values"]) == {email}


@pytest.mark.anyio
async def test_unknown_level_token_treated_as_sensitive(s: Stack, pg: pgfixture.Pg) -> None:
    tid = s.tenants["acme"].id
    tok = "[PII:unknownlevel2345]"
    with pg.su() as su:
        su.execute(
            "INSERT INTO xt_meta.pii_token (tenant_id, token, value) VALUES (%s, %s, 'x-secret')",
            (tid, tok),
        )
    ticket, f = await ticket_for(s, REQUESTER, [tok])
    assert post(s, signed(f, ticket)).json() == {"values": {}, "denied": [tok]}
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    assert post(s, signed(f, ticket)).json()["values"] == {tok: "x-secret"}


@pytest.mark.anyio
async def test_deactivated_viewer_cannot_redeem(s: Stack, pg: pgfixture.Pg) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, OUTSIDER, [tok])
    pid = person_id(s.tenants["acme"].id, OUTSIDER)
    with pg.su() as su:
        su.execute("UPDATE xt_meta.person SET active = false WHERE id = %s", (pid,))
    try:
        r = post(s, signed(f, ticket))
        assert r.status_code == 403 and r.json() == {"error": "TICKET_INVALID"}
    finally:
        with pg.su() as su:
            su.execute("UPDATE xt_meta.person SET active = true WHERE id = %s", (pid,))


@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True,
    reason="PIIR-02: vé cấp cho mọi token có trong vault của tenant, không kiểm token thuộc phiên "
    "người gọi / bản ghi người đó đọc được (integration-notes: tenant|azp|sub|sid)",
)
async def test_token_of_unreadable_record_not_revealable(s: Stack) -> None:
    tok = await bank_token(s)
    # HR không đọc được đề nghị tạm ứng của nhân viên phòng khác...
    got = await s.call("acme", HR, "get_record", {"ref": _cache["ref"]}, client=DESK_AGENT)
    assert not got["ok"], got
    # ...nhưng có token (chép từ màn hình/chat của người khác) là đổi được giá trị thật.
    f = DeskFrame()
    r = await mint(s, HR, [tok], f)
    if r.is_error:
        return
    res = post(s, signed(f, r.meta["xt/reveal"]["ticket"]))
    assert res.status_code != 200 or tok not in res.json()["values"], res.text


# --------------------------------------------------------------------- 5. audit ----
@pytest.mark.anyio
async def test_successful_reveal_logged_without_value(s: Stack, pg: pgfixture.Pg) -> None:
    tok = await bank_token(s)
    before = len(egress(pg))
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    assert post(s, signed(f, ticket)).status_code == 200
    rows = egress(pg)
    assert len(rows) == before + 1
    trace, fields, person, cls, mode = rows[-1]
    assert cls == "human" and mode == "full"
    assert person == person_id(s.tenants["acme"].id, DIRECTOR)
    assert fields == ["advance_request.bank_account:reveal_human"]
    assert trace == f"reveal:{hashlib.sha256(ticket.encode()).hexdigest()[:16]}"
    with pg.su() as su:
        dump = json.dumps(su.execute("SELECT * FROM xt_meta.pii_egress").fetchall(), default=str)
        audit = json.dumps(su.execute("SELECT * FROM xt_audit.event").fetchall(), default=str)
    assert ACCOUNT not in dump and ACCOUNT not in audit


@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True,
    reason="PIIR-03: egress của reveal không ghi bản ghi/token nào bị xem (chỉ tập field)",
)
async def test_reveal_audit_identifies_record(s: Stack, pg: pgfixture.Pg) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    assert post(s, signed(f, ticket)).status_code == 200
    with pg.su() as su:
        row = su.execute(
            "SELECT to_jsonb(e) FROM xt_meta.pii_egress e WHERE tool='pii_reveal' ORDER BY at DESC"
            " LIMIT 1"
        ).fetchone()
    blob = json.dumps(row[0])
    assert _cache["ref"].split(":", 1)[1] in blob or tok in blob, blob


@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True, reason="PIIR-03: lần đổi vé thất bại (BAD_SIGNATURE/STALE/denied) không được ghi"
)
async def test_failed_reveal_attempts_are_audited(s: Stack, pg: pgfixture.Pg) -> None:
    tok = await bank_token(s)
    before = len(egress(pg))
    ticket, _ = await ticket_for(s, DIRECTOR, [tok])
    assert post(s, signed(DeskFrame(), ticket)).json() == {"error": "BAD_SIGNATURE"}
    assert len(egress(pg)) == before + 1


# ------------------------------------------------- 6. vault: field/mức của token ----
@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True,
    reason="PIIR-05: cùng giá trị ở field basic (ghi trước) rồi sensitive: vault giữ field cũ, "
    "kiểm pii.reveal + audit theo field basic thay vì field sensitive",
)
async def test_vault_field_follows_sensitive_source(s: Stack, pg: pgfixture.Pg) -> None:
    from xt.adapters.db.connection import Database
    from xt.domain.model.context import ModelActor
    from xt.entrypoints.composition.runtime import NIL, Runtime

    tid = s.tenants["acme"].id
    tok = "[PII:fieldconfus2345]"
    rt = Runtime(app_db=Database(pg.app_dsn, max_size=2), migrator_dsn=None)
    actor = ModelActor(s.tenants["acme"], NIL, (), client_azp="pii-audit")
    with rt.uow.scope(actor):
        rt.uow.run(
            lambda: rt.store.pii_put(actor, tok, "dup@x", field="person.email", level="basic")
        )
    with rt.uow.scope(actor):
        rt.uow.run(
            lambda: rt.store.pii_put(actor, tok, "dup@x", field="hr.salary", level="sensitive")
        )
    rt.app_db.close()
    with pg.su() as su:
        field, level = su.execute(
            "SELECT field, level FROM xt_meta.pii_token WHERE tenant_id=%s AND token=%s", (tid, tok)
        ).fetchone()
    assert level == "sensitive"
    assert field == "hr.salary", field


# --------------------------------------------------------- 7. giới hạn tần suất ----
@pytest.mark.anyio
@pytest.mark.xfail(
    strict=True, reason="PIIR-04: không có rate limit nào cho pii_reveal_ticket / POST /pii/reveal"
)
async def test_mint_and_reveal_are_rate_limited(s: Stack) -> None:
    tok = await bank_token(s)
    throttled = 0
    for _ in range(40):
        f = DeskFrame()
        r = await mint(s, DIRECTOR, [tok], f)
        if r.is_error:
            throttled += 1
            continue
        res = post(s, signed(f, r.meta["xt/reveal"]["ticket"]))
        throttled += res.status_code == 429
    assert throttled > 0


# --------------------------------------------- 8. thân request / định dạng token ----
@pytest.mark.anyio
async def test_request_body_strictness(s: Stack) -> None:
    tok = await bank_token(s)
    ticket, f = await ticket_for(s, DIRECTOR, [tok])
    body = signed(f, ticket)
    for bad in ({**body, "extra": 1}, {"ticket": ticket, "ts": body["ts"]}, [body], "x" * 5000,
                {**body, "ticket": "short"}, {**body, "sig": "A" * 200}):  # fmt: skip
        r = post(s, bad)
        assert r.status_code == 400 and r.json() == {"error": "BAD_REQUEST"}, str(bad)[:60]
    # vé vẫn nguyên (các request hỏng không chạm vé)
    assert post(s, body).status_code == 200


@pytest.mark.anyio
async def test_spike_token_format_rejected_by_server(s: Stack) -> None:
    """Desk nhận cả `⟨MÃ.trường⟩` (spike) nhưng server chỉ nhận `[PII:…]`: một token kiểu spike
    trong kết quả làm hỏng CẢ lần xin vé (Desk gom mọi token vào một lời gọi) — PIIR-06."""
    tok = await bank_token(s)
    r = await mint(s, DIRECTOR, [tok, "⟨NV0013.bank_account⟩"], DeskFrame())
    assert r.is_error
    r = await mint(s, DIRECTOR, [f"[PII:{'b' * 16}]"] * 1 + [tok], DeskFrame())
    assert r.is_error and "PII_TOKEN_UNKNOWN" in r.content[0].text


# --------------------------------------------- 9. agent cầm token Desk ngoài trình duyệt ----
@pytest.mark.anyio
async def test_desk_agent_token_outside_browser_reveals(s: Stack) -> None:
    """Rủi ro còn lại (PIIR-07): khóa do người gọi tự chọn, Origin chỉ là header. Ai cầm access
    token `xt-desk-agent` (vd profile dev có redirect loopback) là tự đổi được giá trị thật."""
    tok = await bank_token(s)
    f = DeskFrame()  # khóa của "agent", không phải khung Desk
    r = await mint(s, DIRECTOR, [tok], f)
    res = post(s, signed(f, r.meta["xt/reveal"]["ticket"]), origin=s.app.base)
    assert res.status_code == 200 and res.json()["values"] == {tok: ACCOUNT}
