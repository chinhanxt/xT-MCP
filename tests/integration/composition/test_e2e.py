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
# Nhân sự của tệp seed: người đề nghị (Kinh doanh), trưởng phòng Kinh doanh, giám đốc.
REQUESTER, DEPT_HEAD, DIRECTOR = "NV-0013", "NV-0010", "NV-0001"


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
        "XT_MCP_ALLOWED_CLIENTS": f"{AGENT_CLIENT},{LOCAL_AGENT}",
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
            "subjects": {k: sub(alias, k) for k in (REQUESTER, DEPT_HEAD, DIRECTOR)},
            "agent_grants": [
                {"person": k, "client_id": c, "autonomy": 1, "roles": ["agent"]}
                for k in (REQUESTER, DEPT_HEAD, DIRECTOR)
                for c in (AGENT_CLIENT, LOCAL_AGENT)
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
    assert "{'units': 7, 'people': 60, 'agent_grants': 6}" in stack.outputs["org-acme"]
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


@pytest.mark.anyio
async def test_worker_relays_process_timers_to_queue(stack: Stack, pg: pgfixture.Pg) -> None:
    """SLA của bước duyệt -> outbox (cùng transaction tool) -> relay -> JobQueue.schedule_at;
    job `workflow.tick` gọi ProcessService.tick; `xt worker` thật chạy với handler composition."""
    import subprocess
    import sys

    from psycopg import sql

    from xt.adapters.db.workflow_bridge import tenant_async_tx
    from xt.application.jobs import JobRequest
    from xt.entrypoints.composition.worker import RELAY, build_jobs

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
    with pg.su() as su:
        has_queue = su.execute("SELECT to_regclass('xt_queue.requests') IS NOT NULL").fetchone()
    if not (has_queue and has_queue[0]):
        inst = subprocess.run(
            [xt_bin, "worker", "--install-schema"], env=wenv, capture_output=True, timeout=120
        )
        assert inst.returncode == 0, inst.stderr.decode()
    import anyio.to_thread

    rc, out = await anyio.to_thread.run_sync(
        run_admin, ["tenant", "bind-queue", "--tenant", "acme"], wenv
    )
    assert rc == 0, out
    stack.outputs["bind-queue"] = out
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
