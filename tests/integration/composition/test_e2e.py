# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""E2E lớp ghép thật: Postgres 17 + `xt serve --transport http` (uvicorn) + client `mcp` qua
Streamable HTTP, token ký bằng khóa test qua JWKS tĩnh, trang `/desk/confirm` với step-up thật.

Dữ liệu: tổ chức hư cấu `seed/binh-minh/org.json` (nạp bằng `xt org import`), gói
`packages/de-xuat-phe-duyet` (cài bằng `xt pack install` hai bước có mã xác nhận).
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import re
import secrets
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import RunningApp, envelope, serve
from xt.adapters.db.integration import AdminError, connect, person_id, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.entrypoints import admin
from xt.entrypoints.serve import Prepared, prepare

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
ORG = ROOT / "seed" / "binh-minh" / "org.json"
PACK = ROOT / "packages" / "de-xuat-phe-duyet"
# Tier `local` chỉ cho client confidential phía server (decisions.md "Tier PII của Desk chat");
# `xt-desk-agent` (public) luôn ở tier masked.
LOCAL_AGENT = "it-bff-confidential"
DESK_AGENT = "xt-desk-agent"  # client public của Desk chat: tier masked, kênh người /pii/reveal
PII_PACK = ROOT / "packages" / "kenh-pii"
# Nhân sự của tệp seed: người đề nghị (Kinh doanh), trưởng phòng Kinh doanh, giám đốc.
REQUESTER, DEPT_HEAD, DIRECTOR = "NV-0013", "NV-0010", "NV-0001"
IT_ADMIN = "NV-0031"  # vai trò `it`: người duyệt thứ hai của thay đổi model PHÁ HỦY
LINKED = (REQUESTER, DEPT_HEAD, DIRECTOR, IT_ADMIN)
MODEL_PACK = ROOT / "packages" / "quan-tri-mo-hinh"


def sub(alias: str, key: str) -> str:
    return f"kc-{alias}-{key}"


def run_admin(argv: list[str], env: dict[str, str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        try:
            rc = admin.run(argv, env)
        except AdminError as e:  # như `xt` (admin.main): rc 2 + thông điệp
            return 2, str(e)
    return rc, buf.getvalue()


@dataclass
class Stack:
    idp: LocalIdp
    app: RunningApp
    prepared: Prepared
    env: dict[str, str]
    tenants: dict[str, Any]
    outputs: dict[str, str]

    def token(self, alias: str, key: str, client: str = AGENT_CLIENT) -> str:
        return self.idp.access_token(sub(alias, key), org=alias, azp=client)

    async def call(
        self, alias: str, key: str, tool: str, args: dict[str, Any], client: str = AGENT_CLIENT
    ) -> dict[str, Any]:
        async with self.app.session(self.token(alias, key, client)) as s:
            return envelope(await s.call_tool(tool, args))


def _write(path: Path, data: bytes) -> str:
    path.write_bytes(data)
    path.chmod(0o600)
    return str(path)


@pytest.fixture(scope="module")
def stack(
    pg: pgfixture.Pg, idp: LocalIdp, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Stack]:
    d = tmp_path_factory.mktemp("xt-e2e")
    with pg.migrator() as m:
        migrate(m)
    receipt_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    base = f"http://127.0.0.1:{mcp_port}"
    env = {
        "XT_DB_APP_URL": pg.app_dsn,
        "XT_DB_MIGRATOR_URL": pg.migrator_dsn,
        "XT_AUDIT_KEY_FILE": _write(d / "audit.key", pg.pepper),
        "XT_AUDIT_KEY_ID": "1",
        "XT_CURSOR_KEY_FILE": _write(d / "cursor.key", secrets.token_hex(32).encode()),
        "XT_PII_KEY_FILE": _write(d / "pii.key", secrets.token_hex(32).encode()),
        "XT_DESK_URL": base,
        "XT_CONFIRM_AUTHORIZATION_URL": f"{idp.base}/auth",
        "XT_CONFIRM_TOKEN_URL": f"{idp.base}/token",
        "XT_CONFIRM_CLIENT_ID": DESK_CLIENT,
        "XT_CONFIRM_ACR": "urn:xt:acr:otp",
        "XT_CONFIRM_REQUIRED_AMR": "otp",
        "XT_CONFIRM_MAX_AUTH_AGE_SECONDS": "300",
        "XT_RECEIPT_ISSUER": f"{base}/desk",
        "XT_RECEIPT_SIGNING_KEY_FILE": _write(d / "receipt.pem", receipt_key),
        "XT_IDP_ISSUER": idp.issuer,
        "XT_IDP_JWKS_URL": idp.jwks_url,
        "XT_IDP_ALLOW_HTTP": "1",
        "XT_IDP_INTROSPECTION": "off",
        "XT_MCP_RESOURCE": f"{base}/mcp",
        "XT_MCP_ALLOWED_CLIENTS": f"{AGENT_CLIENT},{LOCAL_AGENT},{DESK_AGENT}",
        "XT_PII_REVEAL_ORIGINS": base,
        "XT_PII_REVEAL_CLIENTS": DESK_AGENT,
        "XT_PII_REVEAL_TICKETS_PER_MINUTE": "60",
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_LOCAL_CLIENTS": LOCAL_AGENT,
        "XT_MCP_CONFIDENTIAL_CLIENTS": LOCAL_AGENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
    }
    outputs: dict[str, str] = {}
    tenants: dict[str, Any] = {}
    for alias, name in (("acme", "Bình Minh (A)"), ("beta", "Beta (B)")):
        rc, out = run_admin(
            [
                "tenant", "create", "--alias", alias, "--name", name,
                "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked",
            ],
            env,
        )  # fmt: skip
        assert rc == 0, out
        links = {
            "subjects": {k: sub(alias, k) for k in LINKED},
            "agent_grants": [
                {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
                for k in LINKED
                for c in (AGENT_CLIENT, LOCAL_AGENT, DESK_AGENT)
            ],
        }
        lf = d / f"links-{alias}.json"
        lf.write_text(json.dumps(links), encoding="utf-8")
        rc, out = run_admin(
            ["org", "import", "--tenant", alias, str(ORG), "--links", str(lf),
             "--base-roles", "employee"],
            env,
        )  # fmt: skip
        assert rc == 0, out
        outputs[f"org-{alias}"] = out
        common = ["pack", "install", "--tenant", alias, str(PACK)]
        who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, bad = run_admin([*common, *who, "--confirm", "sha256:" + "0" * 64], env)
        assert rc == 2 and "mã xác nhận không khớp" in bad
        rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, out
        outputs[f"plan-{alias}"] = plan
        outputs[f"install-{alias}"] = out
        for extra_pack in (MODEL_PACK, PII_PACK):
            mp = ["pack", "install", "--tenant", alias, str(extra_pack)]
            rc, mplan = run_admin([*mp, *who], env)
            assert rc == 0, mplan
            mdigest = re.search(r"\(digest\): (sha256:[0-9a-f]{64})", mplan)
            assert mdigest, mplan
            rc, out = run_admin([*mp, *who, "--confirm", mdigest.group(1)], env)
            assert rc == 0, out
        with connect(pg.migrator_dsn) as c:
            tenants[alias] = tenant_of_alias(c, alias)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    app = serve(prepared.http_app(), mcp_port)
    try:
        yield Stack(idp, app, prepared, env, tenants, outputs)
    finally:
        app.stop()
        prepared.close()
        report = os.environ.get("XT_IT_REPORT")
        if report:  # docs/integration.md lấy output thật từ đây
            Path(report).write_text(
                json.dumps(outputs, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )


# ---------------------------------------------------------------------- tiện ích ----
def advance(amount: int, **over: Any) -> dict[str, Any]:
    v: dict[str, Any] = {
        "prepared_on": "2026-10-16",
        "amount": amount,
        "trip_start": "2026-10-20",
        "trip_end": "2026-10-22",
        "destination": "Đà Nẵng",
        "settlement_due": "2026-10-31",
        "payout_method": "cash",
        "settled": False,
    }
    v.update(over)
    return v


class _Form(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hidden: dict[str, str] = {}
        self.action = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "form":
            self.action = a.get("action") or ""
        if tag == "input" and a.get("type") == "hidden":
            self.hidden[a.get("name") or ""] = a.get("value") or ""


def confirm_in_desk(
    s: Stack, confirm_url: str, approver_sub: str, decision: str = "approve", **id_over: Any
) -> httpx.Response:
    """Người duyệt mở confirm_url -> IdP (step-up OTP) -> callback -> bấm Đồng ý / Từ chối."""
    with httpx.Client(base_url=s.app.base, follow_redirects=False, timeout=30) as http:
        r = http.get(urlsplit(confirm_url).path)
        assert r.status_code == 302, r.text
        loc = urlsplit(r.headers["location"])
        q = {k: v[0] for k, v in parse_qs(loc.query).items()}
        assert f"{loc.scheme}://{loc.netloc}{loc.path}" == f"{s.idp.base}/auth"
        assert q["client_id"] == DESK_CLIENT and q["code_challenge_method"] == "S256"
        assert q["acr_values"] == "urn:xt:acr:otp" and q.get("prompt") == "login"
        code = s.idp.issue_code(s.idp.id_token(approver_sub, nonce=q["nonce"], **id_over))
        r = http.get("/desk/confirm/callback", params={"state": q["state"], "code": code})
        if r.status_code != 200:
            return r
        assert "Đồng ý" in r.text and "do agent" in r.text
        form = _Form()
        form.feed(r.text)
        return http.post(form.action, data={**form.hidden, "decision": decision})


# ---------------------------------------------------------------------- kiểm thử ----
def test_admin_outputs(stack: Stack) -> None:
    assert "{'units': 7, 'people': 60, 'agent_grants': 12}" in stack.outputs["org-acme"]
    plan = stack.outputs["plan-acme"]
    assert "Gói de-xuat-phe-duyet 0.2.0" in plan and "proposal_approval v3" in plan
    assert "đã cài de-xuat-phe-duyet 0.2.0 vào acme" in stack.outputs["install-acme"]


@pytest.mark.anyio
async def test_w1_advance_8m_end_to_end(stack: Stack) -> None:
    s = stack
    ws = await s.call("acme", REQUESTER, "inspect_workspace", {})
    assert ws["ok"], ws
    me = ws["data"]["me"]
    assert me["kind"] == "agent" and me["acting_for"]["name"] == "Phan Tiến Dũng"
    assert ws["data"]["pii_mode"] == "masked"

    # 1. Agent của người đề nghị tạo nháp "tạm ứng 8 triệu".
    created = await s.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(8_000_000),
            "idempotency_key": "it-key-w1-c",
        },
    )
    assert created["ok"], json.dumps(created, ensure_ascii=False)
    rec = created["data"]["record"]
    ref = rec["ref"]
    assert rec["state"] == "draft" and rec["values"]["amount"] in (8_000_000, "8000000")

    # 2. Gửi duyệt (L1, đề nghị của chính người được đại diện): chạy ngay.
    sub_args = {"ref": ref, "idempotency_key": "it-key-w1-submit", "note": "Công tác Đà Nẵng"}
    submitted = await s.call("acme", REQUESTER, "submit_request", sub_args)
    assert submitted["ok"], submitted
    assert submitted["data"]["request"]["state"] == "in_review"
    route = [r["step"] for r in submitted["data"]["route"]]
    assert route == ["dept_head", "director"], route

    # Idempotency: gửi lại đúng key -> cùng kết quả (replay), không chạy lần hai.
    again = await s.call("acme", REQUESTER, "submit_request", sub_args)
    assert again["ok"] and again["data"] == submitted["data"]
    # Khác key trên đề nghị đã gửi -> lỗi tiếng Việt có hướng dẫn (Poka-yoke).
    dup = await s.call(
        "acme", REQUESTER, "submit_request", {"ref": ref, "idempotency_key": "it-key-x"}
    )
    assert not dup["ok"] and dup["error"]["code"] == "precondition_failed"
    assert "chỉ gửi được khi là nháp" in dup["error"]["message"]
    s.outputs["w1-submit"] = json.dumps(submitted, ensure_ascii=False, indent=1)
    s.outputs["w1-dup"] = json.dumps(dup, ensure_ascii=False, indent=1)

    # 3. Inbox của trưởng phòng có việc duyệt.
    inbox = await s.call("acme", DEPT_HEAD, "get_inbox", {"kinds": ["approval"]})
    assert inbox["ok"], inbox
    item = next(i for i in inbox["data"]["items"] if i["ref"] == ref)
    revision = item["revision"]

    # 4. Agent của trưởng phòng quyết định -> luôn phải xác nhận (HUMAN_ONLY).
    dec_args = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": revision,
        "idempotency_key": "it-key-w1-dh",
    }
    pending = await s.call("acme", DEPT_HEAD, "decide_request", dec_args)
    assert not pending["ok"] and pending["error"]["code"] == "confirmation_required", pending
    url = pending["error"]["details"]["confirm_url"]
    s.outputs["w1-pending"] = json.dumps(pending, ensure_ascii=False, indent=1)
    assert url.startswith(f"{s.app.base}/desk/confirm/cf_")

    # Step-up sai: thiếu OTP -> 403, không thực thi.
    r = confirm_in_desk(s, url, sub("acme", DEPT_HEAD), amr=("pwd",))
    assert r.status_code == 403 and "Bằng chứng xác thực không đạt" in r.text
    # Người đề nghị tự duyệt (SoD) -> 403.
    r = confirm_in_desk(s, url, sub("acme", REQUESTER))
    assert r.status_code == 403
    # Đúng người, OTP, auth_time tươi -> thực thi.
    r = confirm_in_desk(s, url, sub("acme", DEPT_HEAD))
    assert r.status_code == 200 and "Đã thực hiện" in r.text, r.text

    done = await s.call("acme", DEPT_HEAD, "decide_request", dec_args)
    assert done["ok"], done
    s.outputs["w1-done"] = json.dumps(done, ensure_ascii=False, indent=1)
    assert done["data"]["outcome"] == "step_completed"
    assert done["data"]["next_step"]["label"] == "Giám đốc duyệt"

    # 5. Giám đốc duyệt cuối -> approved.
    got = await s.call("acme", DIRECTOR, "get_record", {"ref": ref})
    assert got["ok"], got
    dir_args = {
        "ref": ref,
        "decision": "approve",
        "expected_revision": got["data"]["record"]["revision"],
        "idempotency_key": "it-key-w1-dir",
    }
    pending = await s.call("acme", DIRECTOR, "decide_request", dir_args)
    assert pending["error"]["code"] == "confirmation_required", pending
    r = confirm_in_desk(s, pending["error"]["details"]["confirm_url"], sub("acme", DIRECTOR))
    assert r.status_code == 200, r.text
    final = await s.call("acme", DIRECTOR, "decide_request", dir_args)
    assert final["ok"] and final["data"]["outcome"] == "request_approved", final
    assert final["data"]["request"]["state"] == "approved"
    s.outputs["w1-final"] = json.dumps(final, ensure_ascii=False, indent=1)

    # 6. Audit hash-chain kiểm OK, dòng thời gian có sự kiện quy trình.
    hist = await s.call("acme", REQUESTER, "get_history", {"ref": ref, "view": "audit"})
    assert hist["ok"] and hist["data"]["chain_verified"] is True, hist
    s.outputs["w1-audit"] = json.dumps(hist["data"]["chain_verified"])
    tl = await s.call("acme", REQUESTER, "get_history", {"ref": ref})
    actions = {e["action"] for e in tl["data"]["events"]}
    assert "advance_request.created" in actions and "advance_request.updated" in actions


@pytest.mark.anyio
async def test_poka_yoke_vietnamese_rule(stack: Stack) -> None:
    bad = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(2_000_000, trip_end="2026-10-19"),
            "idempotency_key": "it-key-pk-1",
        },
    )
    assert not bad["ok"], bad
    assert bad["error"]["code"] in ("rule_violation", "validation_failed", "invalid_request")
    assert "'Ngày về' phải sau hoặc bằng 'Ngày đi'" in json.dumps(bad, ensure_ascii=False)
    stack.outputs["poka-yoke"] = json.dumps(bad, ensure_ascii=False, indent=1)


@pytest.mark.anyio
async def test_rls_two_tenants(stack: Stack) -> None:
    a = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(1_000_000),
            "idempotency_key": "it-key-rls-a",
        },
    )
    assert a["ok"], a
    ref = a["data"]["record"]["ref"]
    # Cùng nhân sự (cùng key seed) ở tenant B không thấy bản ghi của A.
    other = await stack.call("beta", REQUESTER, "get_record", {"ref": ref})
    ghost = await stack.call(
        "beta", REQUESTER, "get_record", {"ref": f"advance_request:{uuid.uuid4()}"}
    )
    xrn = await stack.call("beta", REQUESTER, "get_record", {"ref": f"xrn:acme:{ref}"})
    # Không lộ tồn tại: bản ghi của tenant A trả đúng lỗi như một id không tồn tại ở tenant B.
    for out in (other, ghost, xrn):
        assert not out["ok"], out
        assert "data" not in out and "1000000" not in json.dumps(out)
    assert other["error"]["code"] == ghost["error"]["code"]
    stack.outputs["rls"] = json.dumps(other, ensure_ascii=False, indent=1)
    assert other["error"]["details"] == ghost["error"]["details"] | {
        "principal": other["error"]["details"]["principal"]
    }
    found = await stack.call("beta", REQUESTER, "search_records", {"model_id": "advance_request"})
    assert found["ok"] and all(i["ref"] != ref for i in found["data"]["items"])
    # Id người khác nhau theo tenant (RLS + id ổn định theo tenant).
    ta, tb = stack.tenants["acme"], stack.tenants["beta"]
    assert person_id(ta.id, REQUESTER) != person_id(tb.id, REQUESTER)


@pytest.mark.anyio
async def test_idempotency_concurrent_double_submit(stack: Stack) -> None:
    import anyio

    c = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(3_000_000),
            "idempotency_key": "it-key-dd-c",
        },
    )
    ref = c["data"]["record"]["ref"]
    args = {"ref": ref, "idempotency_key": "it-key-dd-submit"}
    results: list[dict[str, Any]] = []

    async def one() -> None:
        results.append(await stack.call("acme", REQUESTER, "submit_request", args))

    async with anyio.create_task_group() as tg:
        tg.start_soon(one)
        tg.start_soon(one)
    assert all(r["ok"] for r in results), results
    assert results[0]["data"] == results[1]["data"]
    stack.outputs["idem"] = json.dumps([r["data"]["process_instance"] for r in results])
    # Chỉ một instance quy trình được tạo.
    assert results[0]["data"]["process_instance"] == results[1]["data"]["process_instance"]


LONG_ACCT = "0123456789012345678901"  # 22 chữ số: sai regex, engine có thể echo trong lỗi


@pytest.mark.anyio
async def test_pii_masked_for_cloud_full_for_local(stack: Stack) -> None:
    values = advance(4_000_000, payout_method="bank_transfer", bank_account="0123456789012")
    cloud = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {"model_id": "advance_request", "values": values, "idempotency_key": "it-key-pii-1"},
    )
    assert cloud["ok"], cloud
    acct = cloud["data"]["record"]["values"]["bank_account"]
    assert re.fullmatch(r"\[PII:[a-z2-7]{16}\]", acct), acct
    assert "0123456789012" not in json.dumps(cloud)
    stack.outputs["pii-cloud"] = json.dumps(cloud["data"]["record"]["values"], ensure_ascii=False)
    stack.outputs["pii-meta"] = json.dumps(cloud.get("meta"), ensure_ascii=False)
    ref = cloud["data"]["record"]["ref"]
    local = await stack.call("acme", REQUESTER, "get_record", {"ref": ref}, client=LOCAL_AGENT)
    assert local["ok"] and local["data"]["record"]["values"]["bank_account"] == "0123456789012"
    # Token dùng được trong where (so bằng) mà không lộ giá trị.
    hit = await stack.call(
        "acme",
        REQUESTER,
        "search_records",
        {"model_id": "advance_request", "where": {"bank_account": acct}},
    )
    assert hit["ok"] and [i["ref"] for i in hit["data"]["items"]] == [ref], hit
    # Lỗi echo số tài khoản cũng bị che với client cloud.
    bad = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(1, payout_method="bank_transfer", bank_account=LONG_ACCT),
            "idempotency_key": "it-key-pii-2",
        },
    )
    assert not bad["ok"], bad
    assert LONG_ACCT not in json.dumps(bad, ensure_ascii=False), bad


@pytest.mark.anyio
async def test_human_token_rejected_on_mcp(stack: Stack) -> None:
    tok = stack.idp.access_token(sub("acme", REQUESTER), org="acme", azp=DESK_CLIENT)
    r = httpx.post(
        stack.app.mcp_url,
        headers={"Authorization": f"Bearer {tok}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code == 401


@pytest.mark.anyio
async def test_unknown_org_is_rejected(stack: Stack) -> None:
    out = await stack.call("chua-dang-ky", REQUESTER, "inspect_workspace", {})
    assert not out["ok"] and out["error"]["code"] == "unauthenticated", out


# ------------------------------------------------------------------ worker / hẹn giờ ----
def _url(role: str, password: str) -> str:
    return f"postgresql://{role}:{password}@127.0.0.1:{pgfixture.PORT}/xt"


@dataclass
class Worker:
    env: dict[str, str]
    xt_bin: str


@pytest.fixture(scope="module")
def worker(stack: Stack, pg: pgfixture.Pg) -> Worker:
    """Hàng đợi thật: role xt_control/xt_job, `xt worker --install-schema`, `xt tenant
    bind-queue` (job role + chuỗi relay) cho tenant acme."""
    import subprocess
    import sys

    from psycopg import sql

    control_pw = secrets.token_urlsafe(16)
    with pg.su() as su:
        if not su.execute("SELECT 1 FROM pg_roles WHERE rolname='xt_control'").fetchone():
            su.execute(
                sql.SQL("CREATE ROLE xt_control LOGIN NOBYPASSRLS PASSWORD {}").format(
                    sql.Literal(control_pw)
                )
            )
            su.execute("GRANT CONNECT ON DATABASE xt TO xt_control")
            su.execute("CREATE ROLE xt_job NOLOGIN NOBYPASSRLS")
        else:
            su.execute(sql.SQL("ALTER ROLE xt_control PASSWORD {}").format(sql.Literal(control_pw)))
    owner = _url("xt_owner", pg.owner_password)
    wenv = {
        **stack.env,
        "XT_WORKER_DATABASE_URL": _url("xt_app", pg.app_password),
        "XT_QUEUE_DATABASE_URL": _url("xt_control", control_pw),
        "XT_QUEUE_MIGRATOR_DATABASE_URL": owner,
        "XT_DB_OWNER_URL": owner,
        "XT_WORKER_CONCURRENCY": "1",
        "XT_WORKER_STALLED_SECONDS": "6",
        "XT_WORKER_RECOVER_SECONDS": "1",
        "XT_WORKER_MAX_ATTEMPTS": "2",
        "XT_WORKER_RETRY_SECONDS": "1",
        "XT_WORKER_HANDLERS": "xt.entrypoints.composition.worker:handlers",
        "XT_WORKFLOW_RELAY_SECONDS": "60",
    }
    xt_bin = str(Path(sys.executable).with_name("xt"))
    inst = subprocess.run(
        [xt_bin, "worker", "--install-schema"], env=wenv, capture_output=True, timeout=120
    )
    assert inst.returncode == 0, inst.stderr.decode()
    rc, out = run_admin(["tenant", "bind-queue", "--tenant", "acme"], wenv)
    assert rc == 0, out
    stack.outputs["bind-queue"] = out
    return Worker(wenv, xt_bin)


@pytest.mark.anyio
async def test_worker_relays_process_timers_to_queue(
    stack: Stack, pg: pgfixture.Pg, worker: Worker
) -> None:
    """SLA của bước duyệt -> outbox (cùng transaction tool) -> relay -> JobQueue.schedule_at;
    job `workflow.tick` gọi ProcessService.tick; `xt worker` thật chạy với handler composition."""
    import subprocess

    from xt.adapters.db.workflow_bridge import tenant_async_tx
    from xt.application.jobs import JobRequest
    from xt.entrypoints.composition.worker import RELAY, build_jobs

    wenv, xt_bin = worker.env, worker.xt_bin
    tid = stack.tenants["acme"].id

    c = await stack.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(6_000_000),
            "idempotency_key": "it-key-wk",
        },
    )
    ref = c["data"]["record"]["ref"]
    s = await stack.call(
        "acme", REQUESTER, "submit_request", {"ref": ref, "idempotency_key": "it-key-wk-s"}
    )
    assert s["ok"], s
    iid = s["data"]["process_instance"].split(":", 1)[1]
    with pg.su() as su:
        due = su.execute(
            "SELECT due FROM xt_meta.workflow_timer_outbox WHERE tenant_id=%s AND instance_id=%s"
            " AND dispatched_at IS NULL",
            (tid, iid),
        ).fetchone()
        relays = su.execute(
            "SELECT count(*) FROM xt_queue.requests WHERE tenant_id=%s"
            " AND idempotency_key LIKE 'workflow.relay:%%'",
            (tid,),
        ).fetchone()
    assert due is not None, "SLA PT24H của bước trưởng phòng phải ghi outbox hẹn giờ"
    assert relays is not None and relays[0] >= 1  # bind-queue đã khởi động chuỗi relay

    jobs = build_jobs(wenv)
    async with tenant_async_tx(wenv["XT_WORKER_DATABASE_URL"], tid) as conn:
        await jobs.relay(conn, JobRequest(tid, RELAY, "it.relay.once", {}))
    with pg.su() as su:
        row = su.execute(
            "SELECT o.dispatched_at IS NOT NULL, j.scheduled_at, j.status"
            " FROM xt_meta.workflow_timer_outbox o"
            " JOIN xt_queue.requests r ON r.tenant_id = o.tenant_id"
            "  AND r.idempotency_key LIKE 'workflow.timer:%%'"
            " JOIN procrastinate_jobs j ON j.id = r.job_id"
            " WHERE o.tenant_id=%s AND o.instance_id=%s AND j.scheduled_at = o.due",
            (tid, iid),
        ).fetchone()
    assert row is not None and row[0] is True and row[2] == "todo", row
    with pg.su() as su:
        chain = su.execute(
            "SELECT count(*) FROM xt_queue.requests WHERE tenant_id=%s"
            " AND idempotency_key LIKE 'workflow.relay:%%'",
            (tid,),
        ).fetchone()
    assert chain is not None and chain[0] >= 2  # relay tự hẹn lần kế tiếp
    stack.outputs["timer"] = f"due={due[0].isoformat()} scheduled_at={row[1].isoformat()}"

    # Tick trước hạn: không leo thang, instance vẫn chờ trưởng phòng.
    tick = jobs.tick_sync(tid, iid)
    assert tick["status"] == "running" and tick["tasks"][0]["step"] == "dept_head", tick

    # `xt worker` thật nạp handler composition và chạy (không lỗi cấu hình) rồi dừng sạch.
    proc = subprocess.Popen(
        [xt_bin, "worker", "--name", f"it-{uuid.uuid4().hex[:8]}"],
        env={**wenv, "PYTHONPATH": str(ROOT)},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        time.sleep(4)
        assert proc.poll() is None, proc.stdout.read().decode() if proc.stdout else ""
    finally:
        proc.terminate()
        log_out = proc.communicate(timeout=30)[0].decode()
    assert "XT_WORKER_HANDLERS could not load" not in log_out


# ------------------------------------------------------- hợp đồng OperationStore (Postgres) ----
def test_pg_operation_store_contract(stack: Stack, pg: pgfixture.Pg) -> None:
    """`tests/unit/tools/operation_store_contract.py` trên Postgres thật, pool ≥ 2 connection,
    mỗi thread một transaction (claim chờ khóa hàng, update_pending có điều kiện)."""
    from tests.unit.tools.operation_store_contract import run_operation_store_contract
    from xt.adapters.db.confirmations import PgOperationStore, PgUnitOfWork
    from xt.adapters.db.connection import Database
    from xt.domain.model.context import ModelActor

    tenant = stack.tenants["acme"]
    actor = ModelActor(tenant, uuid.UUID(int=0), ())
    db = Database(pg.app_dsn, min_size=2, max_size=4)
    scopes: list[Any] = []

    class Tx:
        def __init__(self, uow: PgUnitOfWork) -> None:
            self.uow = uow

        def run(self, fn: Any) -> Any:
            with self.uow.scope(actor):
                return self.uow.run(fn)

    def new_pair() -> Any:
        with pg.su() as su:
            su.execute(
                "DELETE FROM xt_meta.tool_operation WHERE tenant_id=%s AND actor_key=%s",
                (tenant.id, "agent|c|sub-viet|sub-viet"),
            )
        uow = PgUnitOfWork(db)
        # Thread chính đọc ngoài transaction (get_pending): ràng actor cho uow mới này.
        scope = uow.scope(actor)
        scope.__enter__()
        scopes.append(scope)  # giữ tham chiếu: GC generator sẽ reset ContextVar
        return PgOperationStore(uow, lambda alias: tenant.id, lock_timeout_ms=3000), Tx(uow)

    try:
        run_operation_store_contract(new_pair)
    finally:
        db.close()


# ------------------------------------------------- W2: đề xuất model -> duyệt -> worker áp ----
def _column_exists(pg: pgfixture.Pg, seq: int, table: str, column: str) -> bool:
    with pg.su() as su:
        row = su.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema=%s AND table_name=%s"
            " AND column_name=%s",
            (f"xt_t{seq}", table, column),
        ).fetchone()
    return row is not None


@pytest.mark.anyio
async def test_w2_model_change_approved_then_applied_by_worker(
    stack: Stack, pg: pgfixture.Pg, worker: Worker
) -> None:
    """E6: agent đề xuất thêm `ma_du_an` -> agent không tự áp được -> giám đốc duyệt ở
    /desk/confirm -> worker `model.apply` (pha 1 + 2) -> cột có trong information_schema -> tạo
    bản ghi với field mới; xóa field (PHÁ HỦY) cần hai người duyệt."""
    import anyio.to_thread

    from xt.adapters.db.workflow_bridge import tenant_async_tx
    from xt.application.jobs import JobRequest
    from xt.entrypoints.composition.worker import RELAY, build_jobs

    s = stack
    tenant = s.tenants["acme"]
    jobs = build_jobs(worker.env)

    async def relay_and_apply() -> list[dict[str, Any]]:
        async with tenant_async_tx(worker.env["XT_WORKER_DATABASE_URL"], tenant.id) as conn:
            await jobs.relay(conn, JobRequest(tenant.id, RELAY, f"it.relay.{uuid.uuid4()}", {}))
        with pg.su() as su:
            rows = su.execute(
                "SELECT r.idempotency_key FROM xt_queue.requests r WHERE r.tenant_id=%s"
                " AND r.idempotency_key LIKE 'model.apply:%%'",
                (tenant.id,),
            ).fetchall()
        outs = []
        for (key,) in rows:
            pid = uuid.UUID(key.split(":", 1)[1])
            outs.append(await anyio.to_thread.run_sync(jobs.apply_sync, tenant.id, pid))
        return outs

    add = {
        "model_id": "advance_request",
        "operations": [
            {
                "op": "add_field",
                "field": {"id": "ma_du_an", "label": "Mã dự án", "type": "string"},
            }
        ],
        "rationale": "Theo dõi tạm ứng theo dự án",
        "idempotency_key": "it-key-w2-add",
    }
    preview = await s.call("acme", DEPT_HEAD, "propose_model_change", {**add, "dry_run": True})
    assert preview["ok"], preview
    proposed = await s.call("acme", DEPT_HEAD, "propose_model_change", {**add, "dry_run": False})
    assert proposed["ok"], proposed
    pref = proposed["data"]["proposal"]
    assert pref.startswith("model_proposal:")
    s.outputs["w2-propose"] = json.dumps(proposed["data"], ensure_ascii=False, indent=1)

    st = await s.call("acme", DIRECTOR, "get_record", {"ref": pref})
    assert st["ok"] and st["data"]["record"]["state"] == "pending_approval", st
    # Người đề xuất không tự duyệt (SoD).
    own = await s.call(
        "acme",
        DEPT_HEAD,
        "decide_request",
        {
            "ref": pref,
            "decision": "approve",
            "expected_revision": 1,
            "idempotency_key": "it-key-w2-own",
        },
    )
    assert not own["ok"] and own["error"]["code"] == "access_denied", own
    # Chưa duyệt -> worker không áp gì.
    assert await relay_and_apply() == []
    assert not _column_exists(pg, tenant.seq, "advance_request", "ma_du_an")

    args = {
        "ref": pref,
        "decision": "approve",
        "expected_revision": 1,
        "idempotency_key": "it-key-w2-approve",
    }
    pending = await s.call("acme", DIRECTOR, "decide_request", args)
    assert pending["error"]["code"] == "confirmation_required", json.dumps(
        pending, ensure_ascii=False
    )
    r = await anyio.to_thread.run_sync(
        lambda: confirm_in_desk(
            s, pending["error"]["details"]["confirm_url"], sub("acme", DIRECTOR)
        )
    )
    assert r.status_code == 200 and "Đã thực hiện" in r.text, r.text
    done = await s.call("acme", DIRECTOR, "decide_request", args)
    assert done["ok"] and done["data"]["outcome"] == "request_approved", done
    st = await s.call("acme", DIRECTOR, "get_record", {"ref": pref})
    assert st["data"]["record"]["state"] == "approved"

    applied = await relay_and_apply()
    assert [a["state"] for a in applied] == ["applied"], applied
    s.outputs["w2-applied"] = json.dumps(applied, ensure_ascii=False)
    assert _column_exists(pg, tenant.seq, "advance_request", "ma_du_an")
    st = await s.call("acme", DIRECTOR, "get_record", {"ref": pref})
    rec = st["data"]["record"]
    assert rec["state"] == "applied" and rec["values"]["applied_version"] >= 2, rec
    c = await s.call(
        "acme",
        REQUESTER,
        "create_record",
        {
            "model_id": "advance_request",
            "values": advance(1_500_000, ma_du_an="DA-2026-07"),
            "idempotency_key": "it-key-w2-rec",
        },
    )
    assert c["ok"] and c["data"]["record"]["values"]["ma_du_an"] == "DA-2026-07", c

    # PHÁ HỦY: xóa field đã có dữ liệu -> hai người duyệt khác nhau.
    rm = {
        "model_id": "advance_request",
        "operations": [{"op": "remove_field", "field_id": "ma_du_an"}],
        "rationale": "Bỏ mã dự án",
        "idempotency_key": "it-key-w2-rm",
        "dry_run": False,
    }
    proposed = await s.call("acme", DEPT_HEAD, "propose_model_change", rm)
    assert proposed["ok"], proposed
    rref = proposed["data"]["proposal"]
    rargs = {
        "ref": rref,
        "decision": "approve",
        "expected_revision": 1,
        "idempotency_key": "it-key-w2-rm-ok",
    }
    pending = await s.call("acme", DIRECTOR, "decide_request", rargs)
    assert pending["error"]["code"] == "confirmation_required", pending
    url = pending["error"]["details"]["confirm_url"]
    r = await anyio.to_thread.run_sync(lambda: confirm_in_desk(s, url, sub("acme", DIRECTOR)))
    assert r.status_code == 200 and "người xác nhận thứ hai" in r.text, r.text
    # Một người duyệt chưa đủ: agent gọi lại vẫn chưa thực thi, worker không áp.
    again = await s.call("acme", DIRECTOR, "decide_request", rargs)
    assert not again["ok"], again
    assert await relay_and_apply() == [
        {"proposal": str(applied[0]["proposal"]), "state": "applied"}
    ]
    assert _column_exists(pg, tenant.seq, "advance_request", "ma_du_an")
    r = await anyio.to_thread.run_sync(lambda: confirm_in_desk(s, url, sub("acme", IT_ADMIN)))
    assert r.status_code == 200 and "Đã thực hiện" in r.text, r.text
    final = await s.call("acme", DIRECTOR, "decide_request", rargs)
    assert final["ok"] and final["data"]["outcome"] == "request_approved", final
    outs = await relay_and_apply()
    removed = [o for o in outs if o["proposal"] == rref.split(":", 1)[1]]
    assert removed and removed[0]["state"] == "applied", outs
    assert not _column_exists(pg, tenant.seq, "advance_request", "ma_du_an")
    st = await s.call("acme", DIRECTOR, "get_record", {"ref": rref})
    assert st["data"]["record"]["values"]["destructive_confirmed"], st
    assert len(st["data"]["record"]["values"]["decided_by"]) == 2


@pytest.mark.anyio
async def test_w2_desk_agent_on_behalf_proposes_but_never_approves(stack: Stack) -> None:
    """Desk agent (kind=agent, thay mặt người dùng của tenant): người có vai trò đề xuất (dept_head)
    gửi được đề xuất L1 không cần cổng người; người không có vai trò (nhân viên) bị từ chối;
    agent không bao giờ tự duyệt, kể cả thay mặt giám đốc (policy Deny + HUMAN_ONLY)."""
    s = stack
    me = await s.call("acme", DEPT_HEAD, "inspect_workspace", {}, client=DESK_AGENT)
    assert me["ok"], me
    body = {
        "model_id": "advance_request",
        "operations": [
            {
                "op": "add_field",
                "field": {"id": "ghi_chu_desk", "label": "Ghi chú", "type": "string"},
            }
        ],
        "rationale": "Desk agent đề xuất",
    }
    ok = await s.call(
        "acme",
        DEPT_HEAD,
        "propose_model_change",
        {**body, "dry_run": False, "idempotency_key": "it-key-w2-desk"},
        client=DESK_AGENT,
    )
    assert ok["ok"], ok
    ref = ok["data"]["proposal"]
    denied = await s.call(
        "acme",
        REQUESTER,
        "propose_model_change",
        {**body, "dry_run": False, "idempotency_key": "it-key-w2-desk-no"},
        client=DESK_AGENT,
    )
    assert not denied["ok"] and denied["error"]["code"] == "access_denied", denied
    for who in (DIRECTOR, DEPT_HEAD):
        dec = await s.call(
            "acme",
            who,
            "decide_request",
            {
                "ref": ref,
                "decision": "approve",
                "expected_revision": 1,
                "idempotency_key": f"it-key-w2-desk-dec-{who}",
            },
            client=DESK_AGENT,
        )
        assert dec["error"]["code"] in ("access_denied", "confirmation_required"), dec
        st = await s.call("acme", DIRECTOR, "get_record", {"ref": ref})
        assert st["data"]["record"]["state"] == "pending_approval", st


# ---------------------------------------------- kênh người xem PII: Desk <-> /pii/reveal ----
class DeskFrame:
    """Phía Desk theo hợp đồng `desk/src/pii.ts`: khóa P-256 riêng mỗi lần xem, vé chỉ ở _meta,
    POST thẳng tới origin xT, chữ ký P1363 trên "{ticket}.{ts}"."""

    def __init__(self) -> None:
        from cryptography.hazmat.primitives.asymmetric import ec

        self.key = ec.generate_private_key(ec.SECP256R1())
        n = self.key.public_key().public_numbers()

        def b64u(i: int) -> str:
            import base64

            return base64.urlsafe_b64encode(i.to_bytes(32, "big")).decode().rstrip("=")

        self.jwk = {"kty": "EC", "crv": "P-256", "x": b64u(n.x), "y": b64u(n.y), "ext": True,
                    "key_ops": ["verify"]}  # fmt: skip

    def sign(self, ticket: str, ts: int) -> str:
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        der = self.key.sign(f"{ticket}.{ts}".encode(), ec.ECDSA(hashes.SHA256()))
        r, sv = decode_dss_signature(der)
        raw = r.to_bytes(32, "big") + sv.to_bytes(32, "big")
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")


async def _mint(stack: Stack, key: str, tokens: list[str], frame: DeskFrame) -> Any:
    async with stack.app.session(stack.token("acme", key, DESK_AGENT)) as sess:
        return await sess.call_tool(
            "pii_reveal_ticket", {"tokens": tokens, "pubkey_jwk": frame.jwk}
        )


def _post(stack: Stack, body: dict[str, Any], origin: str | None) -> httpx.Response:
    headers = {"content-type": "application/json"}
    if origin:
        headers["origin"] = origin
    return httpx.post(f"{stack.app.base}/pii/reveal", json=body, headers=headers, timeout=10)


@pytest.mark.anyio
async def test_pii_reveal_human_channel(stack: Stack, pg: pgfixture.Pg) -> None:
    import anyio.to_thread

    s = stack
    values = advance(2_500_000, payout_method="bank_transfer", bank_account="9704229912345678")
    c = await s.call(
        "acme",
        REQUESTER,
        "create_record",
        {"model_id": "advance_request", "values": values, "idempotency_key": "it-key-rv-1"},
        client=DESK_AGENT,
    )
    assert c["ok"], c
    tok = c["data"]["record"]["values"]["bank_account"]
    assert re.fullmatch(r"\[PII:[a-z2-7]{16}\]", tok), tok  # Desk chat (public) = masked
    # Token gắn phiên đã nhận nó (PIIR-02): giám đốc tự mở bản ghi trên Desk chat trước.
    seen = await s.call(
        "acme", DIRECTOR, "get_record", {"ref": c["data"]["record"]["ref"]}, client=DESK_AGENT
    )
    assert seen["ok"] and seen["data"]["record"]["values"]["bank_account"] == tok

    # App-only tool: có trong tools/list với visibility app; MCP App đã đăng ký.
    async with s.app.session(s.token("acme", REQUESTER, DESK_AGENT)) as sess:
        listed = {t.name: t for t in (await sess.list_tools()).tools}
        resources = {str(r.uri) for r in (await sess.list_resources()).resources}
    assert (listed["pii_reveal_ticket"].meta or {})["ui"]["visibility"] == ["app"]
    assert {"ui://xt/orgchart", "ui://xt/approval-card", "ui://xt/record-table"} <= resources

    origin = s.app.base
    frame = DeskFrame()
    minted = await _mint(s, DIRECTOR, [tok], frame)
    assert not minted.is_error, minted
    meta = minted.meta["xt/reveal"]
    ticket = meta["ticket"]
    assert meta["url"] == f"{origin}/pii/reveal"
    # Vé chỉ ở _meta, không trong content/structuredContent.
    assert ticket not in json.dumps(minted.structured_content)
    assert ticket not in minted.content[0].text

    pre = httpx.options(
        f"{origin}/pii/reveal",
        headers={"origin": origin, "access-control-request-method": "POST"},
    )
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == origin
    ts = int(time.time())
    body = {"ticket": ticket, "ts": ts, "sig": frame.sign(ticket, ts)}
    bad_origin = await anyio.to_thread.run_sync(_post, s, body, "https://evil.example")
    assert bad_origin.status_code == 403 and bad_origin.json() == {"error": "BAD_ORIGIN"}
    assert "access-control-allow-origin" not in bad_origin.headers
    ok = await anyio.to_thread.run_sync(_post, s, body, origin)
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"values": {tok: "9704229912345678"}, "denied": []}
    assert ok.headers["cache-control"] == "no-store"
    assert ok.headers["access-control-allow-origin"] == origin
    s.outputs["reveal-ok"] = ok.text
    # Dùng một lần.
    again = await anyio.to_thread.run_sync(_post, s, body, origin)
    assert again.status_code == 403 and again.json() == {"error": "TICKET_INVALID"}

    # Vé bị chép nhưng không có khóa của khung -> BAD_SIGNATURE (và vé bị tiêu).
    m2 = await _mint(s, DIRECTOR, [tok], DeskFrame())
    t2 = m2.meta["xt/reveal"]["ticket"]
    forged = {"ticket": t2, "ts": ts, "sig": frame.sign(t2, ts)}
    r = await anyio.to_thread.run_sync(_post, s, forged, origin)
    assert r.status_code == 403 and r.json() == {"error": "BAD_SIGNATURE"}
    # ts lệch quá 30 s.
    f3 = DeskFrame()
    t3 = (await _mint(s, DIRECTOR, [tok], f3)).meta["xt/reveal"]["ticket"]
    old = ts - 120
    r = await anyio.to_thread.run_sync(
        _post, s, {"ticket": t3, "ts": old, "sig": f3.sign(t3, old)}, origin
    )
    assert r.status_code == 403 and r.json() == {"error": "STALE_REQUEST"}

    # sensitive: người không có `pii.reveal` (nhân viên) bị từ chối từng token.
    f4 = DeskFrame()
    t4 = (await _mint(s, REQUESTER, [tok], f4)).meta["xt/reveal"]["ticket"]
    ts4 = int(time.time())
    r = await anyio.to_thread.run_sync(
        _post, s, {"ticket": t4, "ts": ts4, "sig": f4.sign(t4, ts4)}, origin
    )
    assert r.status_code == 200 and r.json() == {"values": {}, "denied": [tok]}

    # Client không phải Desk (agent cloud) không xin được vé; token lạ bị từ chối.
    async with s.app.session(s.token("acme", DIRECTOR)) as sess:
        nope = await sess.call_tool("pii_reveal_ticket", {"tokens": [tok], "pubkey_jwk": frame.jwk})
    assert nope.is_error and "client_not_allowed" in json.dumps(envelope(nope))
    unknown = await _mint(s, DIRECTOR, ["[PII:aaaaaaaaaaaaaaaa]"], DeskFrame())
    assert unknown.is_error and "PII_TOKEN_UNKNOWN" in unknown.content[0].text

    # Mọi lần xem đều được ghi (tier human, người xem, field đã mở); không có giá trị thật.
    with pg.su() as su:
        rows = su.execute(
            "SELECT fields, person FROM xt_meta.pii_egress WHERE tool='pii_reveal'"
            " AND client_class='human' ORDER BY at"
        ).fetchall()
    assert any("advance_request.bank_account:reveal_human" in r[0] for r in rows), rows
    assert all(r[1] is not None for r in rows)
    assert "9704229912345678" not in json.dumps([list(r[0]) for r in rows])


@pytest.mark.anyio
async def test_org_person_details_masks_contact_for_desk_chat(stack: Stack) -> None:
    async with stack.app.session(stack.token("acme", REQUESTER, DESK_AGENT)) as sess:
        me = await sess.call_tool("inspect_workspace", {})
        pref = envelope(me)["data"]["me"]["ref"]
        res = await sess.call_tool("org_person_details", {"ref": pref})
    env = envelope(res)
    assert env["ok"], env
    assert re.fullmatch(r"\[PII:[a-z2-7]{16}\]", env["data"]["email"]), env
    assert env["meta"]["pii"]["mode"] == "masked"


# --------------------------------------------- migration từ số 0 (CSDL compose mới) ----
def test_fresh_db_has_full_pii_schema(stack: Stack, pg: pgfixture.Pg) -> None:
    """Container PG mới -> bootstrap -> `xt db migrate` (fixture `stack`): đủ schema làn PII
    (PIIR-08: 0007_pii_reveal sau 0006_audit_meta, không trùng số)."""
    from xt.adapters.db.migrations import available

    names = [n for n, _ in available()]
    assert len({n[:4] for n in names}) == len(names), names  # không trùng số thứ tự
    with pg.su() as su:
        applied = {r[0] for r in su.execute("SELECT name FROM xt_meta.schema_migration")}
        cols = {
            r[0]
            for r in su.execute(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_schema='xt_meta' AND table_name='pii_egress'"
            )
        }
        tables = {
            r[0]
            for r in su.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema='xt_meta'"
                " AND table_name LIKE 'pii_%'"
            )
        }
    assert set(names) <= applied, sorted(set(names) - applied)
    assert {"client_class", "mode", "fields", "person", "outcome", "tokens", "records",
            "denied"} <= cols, cols  # fmt: skip
    assert {"pii_token", "pii_token_source", "pii_token_seen", "pii_reveal_ticket",
            "pii_egress"} <= tables, tables  # fmt: skip
