# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""REST `/workflow/v1` trên lớp ghép thật: Postgres 17 + composition + uvicorn, token ký bằng khóa
test qua JWKS tĩnh (audience `xt-workflow`), duyệt qua trang `/desk/confirm` có step-up thật.

Luồng W1 (tạm ứng 8 triệu) chạy hoàn toàn bằng HTTP, không dùng MCP. Tenant riêng `wfa`/`wfb`
để không đụng dữ liệu của `test_e2e.py` trong cùng session Postgres.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import serve
from tests.integration.composition.test_e2e import (
    DEPT_HEAD,
    DIRECTOR,
    ORG,
    PACK,
    REQUESTER,
    Stack,
    _write,
    advance,
    confirm_in_desk,
    run_admin,
    sub,
)
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.adapters.workflow.bpmn.spiff import load_specs
from xt.entrypoints.serve import prepare
from xt.entrypoints.workflow_http import with_workflow_api

pytestmark = pytest.mark.integration

A, B = "wfa", "wfb"
WF = "/workflow/v1"
BPMN_NS = {
    "bpmn": "http://www.omg.org/spec/BPMN/20100524/MODEL",
    "bpmndi": "http://www.omg.org/spec/BPMN/20100524/DI",
}


@pytest.fixture(scope="module")
def wstack(
    pg: pgfixture.Pg, idp: LocalIdp, mcp_port: int, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Stack]:
    d = tmp_path_factory.mktemp("xt-wf")
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
        "XT_MCP_ALLOWED_CLIENTS": AGENT_CLIENT,
        "XT_PII_REVEAL_ORIGINS": base,  # bắt buộc từ khi có /pii/reveal (integration-notes)
        "XT_PII_REVEAL_CLIENTS": AGENT_CLIENT,
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
        "XT_WORKFLOW_PUBLIC_URL": base,
    }
    tenants: dict[str, Any] = {}
    outputs: dict[str, str] = {}
    for alias, name in ((A, "Workflow A"), (B, "Workflow B")):
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
                {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
                for k in (REQUESTER, DEPT_HEAD, DIRECTOR)
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
        common = ["pack", "install", "--tenant", alias, str(PACK)]
        who = ["--proposed-by", "agent:it", "--approved-by", "person:admin"]
        rc, plan = run_admin([*common, *who], env)
        assert rc == 0, plan
        digest = re.search(r"Mã xác nhận \(digest\): (sha256:[0-9a-f]{64})", plan)
        assert digest, plan
        rc, out = run_admin([*common, *who, "--confirm", digest.group(1)], env)
        assert rc == 0, out
        with connect(pg.migrator_dsn) as c:
            tenants[alias] = tenant_of_alias(c, alias)
    ns = argparse.Namespace(transport="http", host="127.0.0.1", port=mcp_port, profile="full")
    prepared = prepare(env, ns)
    app = serve(with_workflow_api(prepared, env, prepared.http_app()), mcp_port)
    try:
        yield Stack(idp, app, prepared, env, tenants, outputs)
    finally:
        app.stop()
        prepared.close()


def wf_token(s: Stack, alias: str, key: str, **over: Any) -> str:
    claims: dict[str, Any] = {"aud": ["xt-workflow"], "scope": "openid xt-workflow"}
    claims.update(over)
    return s.idp.access_token(sub(alias, key), org=alias, **claims)


def http(s: Stack, token: str | None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=s.app.base, headers=headers, timeout=30)


def test_auth_boundaries(wstack: Stack) -> None:
    s = wstack
    with http(s, None) as c:
        r = c.get(f"{WF}/definitions")
        doc = c.get(f"{WF}/openapi.json")
    assert r.status_code == 401 and r.json()["error"]["code"] == "unauthenticated"
    assert r.headers["www-authenticate"].startswith('Bearer realm="xt-workflow"')
    assert doc.status_code == 200 and doc.json()["openapi"] == "3.1.0"
    assert doc.json()["servers"] == [{"url": s.app.base}]
    # Token MCP (aud = URL /mcp) không dùng được cho workflow, và ngược lại.
    with http(s, s.token(A, REQUESTER)) as c:
        r = c.get(f"{WF}/definitions")
    assert r.status_code == 401 and "workflow API" in r.headers["www-authenticate"]
    mcp = httpx.post(
        f"{s.app.base}/mcp",
        headers={
            "Authorization": f"Bearer {wf_token(s, A, REQUESTER)}",
            "Accept": "application/json, text/event-stream",
        },
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=30,
    )
    assert mcp.status_code == 401
    # Phiên người (client Desk) không gọi API: người chỉ xác nhận trên trang /desk/confirm.
    with http(s, wf_token(s, A, DEPT_HEAD, azp=DESK_CLIENT)) as c:
        r = c.get(f"{WF}/inbox")
    assert r.status_code == 401
    with http(s, wf_token(s, A, REQUESTER, scope="openid")) as c:
        r = c.get(f"{WF}/inbox")
    assert r.status_code == 403


def test_definitions_and_bpmn_download(wstack: Stack) -> None:
    s = wstack
    with http(s, wf_token(s, A, REQUESTER)) as c:
        lst = c.get(f"{WF}/definitions")
        assert lst.status_code == 200, lst.text
        ids = [d["id"] for d in lst.json()["data"]["definitions"]]
        assert "proposal_approval" in ids, ids
        one = c.get(f"{WF}/definitions/proposal_approval")
        x = c.get(f"{WF}/definitions/proposal_approval/bpmn")
        missing = c.get(f"{WF}/definitions/khong_co_quy_trinh/bpmn")
    assert one.status_code == 200, one.text
    rev = one.json()["data"]["process"]["revision"]
    assert x.status_code == 200, x.text
    assert x.headers["content-type"].startswith("application/xml")
    assert f'proposal_approval-v{rev}.bpmn"' in x.headers["content-disposition"]
    root = ET.fromstring(x.content)
    proc = root.find("bpmn:process", BPMN_NS)
    assert proc is not None and proc.get("id")
    # DI: mọi flow node có BPMNShape, mọi sequenceFlow có BPMNEdge (mở được trong bpmn.io).
    plane = root.find("bpmndi:BPMNDiagram/bpmndi:BPMNPlane", BPMN_NS)
    assert plane is not None
    drawn = {e.get("bpmnElement") for e in plane}
    flows = {e.get("id") for e in proc.iter() if e.tag.endswith("sequenceFlow")}
    nodes = {
        e.get("id")
        for e in proc
        if not e.tag.endswith(("sequenceFlow", "laneSet", "extensionElements"))
    }
    assert flows and nodes and (flows | nodes) <= drawn
    # Hợp lệ với chính parser BPMN của engine (đã cứng hóa XXE): nạp được thành spec chạy.
    spec, _ = load_specs(x.content, str(proc.get("id")))
    assert spec.name == proc.get("id")
    s.outputs["wf-bpmn-bytes"] = str(len(x.content))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_w1_advance_over_rest_without_mcp(wstack: Stack) -> None:
    s = wstack
    body = {
        "definition_id": "proposal_approval",
        "values": advance(8_000_000),
        "note": "Công tác Đà Nẵng",
    }
    with http(s, wf_token(s, A, REQUESTER)) as c:
        nokey = c.post(f"{WF}/instances", json=body)
        h = {"Idempotency-Key": "it-wf-w1-start"}
        started = c.post(f"{WF}/instances", json=body, headers=h)
        again = c.post(f"{WF}/instances", json=body, headers=h)
    assert nokey.status_code == 400
    assert started.status_code == 201, started.text
    data = started.json()["data"]
    iid = data["instance"]["id"]
    assert started.headers["location"] == f"{WF}/instances/{iid}"
    assert data["request"]["state"] == "in_review"
    assert [r["step"] for r in data["route"]] == ["dept_head", "director"]
    assert again.status_code == 200 and again.json()["data"] == data
    ref = data["request"]["ref"]

    with http(s, wf_token(s, A, REQUESTER)) as c:
        inst = c.get(f"{WF}/instances/{iid}")
    assert inst.status_code == 200, inst.text
    got = inst.json()["data"]
    assert got["instance"]["status"] == "running"
    assert got["instance"]["process_id"] == "proposal_approval"
    assert [t["step"] for t in got["instance"]["tasks"]] == ["dept_head"]
    assert got["request"]["ref"] == ref

    # Tenant khác: cùng câu trả lời như id không tồn tại (không lộ tồn tại).
    with http(s, wf_token(s, B, REQUESTER)) as c:
        foreign = c.get(f"{WF}/instances/{iid}")
        ghost = c.get(f"{WF}/instances/{uuid.uuid4()}")
        foreign_dec = c.post(
            f"{WF}/tasks/{quote(ref, safe=':')}/decision",
            json={"decision": "approve", "expected_revision": 1},
            headers={"Idempotency-Key": "it-wf-foreign-1"},
        )
    assert foreign.status_code == ghost.status_code == 404
    assert foreign.json()["error"] == ghost.json()["error"]
    assert "8000000" not in foreign.text
    assert foreign_dec.status_code in (403, 404) and "confirm_url" not in foreign_dec.text

    # Inbox trưởng phòng -> quyết định qua REST -> 428 + confirm_url (agent không tự duyệt được).
    with http(s, wf_token(s, A, DEPT_HEAD)) as c:
        inbox = c.get(f"{WF}/inbox", params={"kinds": "approval"})
        assert inbox.status_code == 200, inbox.text
        item = next(i for i in inbox.json()["data"]["items"] if i["ref"] == ref)
        assert item["task_id"] == ref and item["links"]["decision"].endswith("/decision")
        dec = {"decision": "approve", "expected_revision": item["revision"]}
        hk = {"Idempotency-Key": "it-wf-w1-dh"}
        pending = c.post(item["links"]["decision"], json=dec, headers=hk)
        assert pending.status_code == 428, pending.text
        err = pending.json()["error"]
        assert err["code"] == "confirmation_required"
        url = err["details"]["confirm_url"]
        assert url.startswith(f"{s.app.base}/desk/confirm/cf_")
        assert pending.headers["link"] == f'<{url}>; rel="confirm"'
        s.outputs["wf-w1-pending"] = json.dumps(pending.json(), ensure_ascii=False, indent=1)
        # Người duyệt bấm trên trang Desk (step-up OTP thật), rồi agent gửi lại y hệt.
        r = confirm_in_desk(s, url, sub(A, DEPT_HEAD))
        assert r.status_code == 200 and "Đã thực hiện" in r.text, r.text
        done = c.post(item["links"]["decision"], json=dec, headers=hk)
    assert done.status_code == 200, done.text
    assert done.json()["data"]["outcome"] == "step_completed"
    assert done.json()["data"]["next_step"]["label"] == "Giám đốc duyệt"

    with http(s, wf_token(s, A, REQUESTER)) as c:
        after = c.get(f"{WF}/instances/{iid}").json()["data"]
    assert [t["step"] for t in after["instance"]["tasks"]] == ["director"]


def test_headless_demo_script_workflow_section(
    wstack: Stack, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """`docs/headless-demo.sh` chạy curl thật vào stack: bước 4 (Workflow) phải OK."""
    import shutil
    import subprocess

    if shutil.which("curl") is None:
        pytest.skip("thiếu curl")
    s = wstack
    d = tmp_path_factory.mktemp("xt-wf-demo")
    start = d / "start.json"
    start.write_text(
        json.dumps({"definition_id": "proposal_approval", "values": advance(6_000_000)}),
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ["PATH"],
        "XT_AUTH_BASE": s.idp.base,
        "XT_API_BASE": s.app.base,
        "XT_WORKFLOW_TOKEN_FILE": _write(d / "wf.tok", wf_token(s, A, REQUESTER).encode()),
        "XT_WORKFLOW_APPROVER_TOKEN_FILE": _write(
            d / "wfa.tok", wf_token(s, A, DEPT_HEAD).encode()
        ),
        "XT_WORKFLOW_START_FILE": str(start),
        "XT_WORKFLOW_BPMN_OUT": str(d / "out.bpmn"),
        "XT_DEMO_MAX_BODY": "300",
    }
    script = Path(__file__).resolve().parents[3] / "docs" / "headless-demo.sh"
    run = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, timeout=120, check=False
    )
    out = run.stdout
    s.outputs["wf-demo"] = out
    report = os.environ.get("XT_IT_WF_DEMO")
    if report:  # docs/workflow-api.md lấy transcript thật từ đây
        Path(report).write_text(out, encoding="utf-8")
    assert "4a không token: 401 (đúng)" in out, out + run.stderr
    assert "4b definitions: 200, có proposal_approval" in out, out
    assert "4c BPMN: 200" in out and (d / "out.bpmn").read_bytes().startswith(b"<?xml"), out
    assert re.search(
        r"4d POST /instances \(Idempotency-Key [^)]+\): 201, instance [0-9a-f-]{36}", out
    )
    assert "4e GET /instances/" in out and "trạng thái running, bước dept_head" in out, out
    assert "4g quyết định qua REST: 428 confirmation_required" in out, out
    assert f"người duyệt mở: {s.app.base}/desk/confirm/cf_" in out, out
    assert re.search(r"4 Workflow\s+OK", out), out
    for secret in (env["XT_WORKFLOW_TOKEN_FILE"], env["XT_WORKFLOW_APPROVER_TOKEN_FILE"]):
        assert Path(secret).read_text() not in out + run.stderr


def test_requester_withdraws_running_request_rest_and_mcp(wstack: Stack) -> None:
    """Đường gỡ bước kẹt: người đề nghị rút đề nghị đang chờ duyệt (REST + MCP cùng service)."""
    import anyio

    s = wstack
    body = {"definition_id": "proposal_approval", "values": advance(3_000_000)}
    with http(s, wf_token(s, A, REQUESTER)) as c:
        started = c.post(f"{WF}/instances", json=body, headers={"Idempotency-Key": "it-wf-wd-01"})
        assert started.status_code == 201, started.text
        iid = started.json()["data"]["instance"]["id"]
        rev = started.json()["data"]["request"]["revision"]
        inst = c.get(f"{WF}/instances/{iid}").json()["data"]["instance"]
        assert inst["links"]["withdraw"] == f"{WF}/instances/{iid}/withdraw"
    with http(s, wf_token(s, A, DEPT_HEAD)) as c:  # không phải người đề nghị
        other = c.post(
            f"{WF}/instances/{iid}/withdraw",
            json={"expected_revision": rev},
            headers={"Idempotency-Key": "it-wf-wd-dh"},
        )
    assert other.status_code in (403, 404), other.text
    hk = {"Idempotency-Key": "it-wf-wd-02"}
    with http(s, wf_token(s, A, REQUESTER)) as c:
        done = c.post(
            f"{WF}/instances/{iid}/withdraw",
            json={"expected_revision": rev, "reason": "Đổi kế hoạch"},
            headers=hk,
        )
        after = c.get(f"{WF}/instances/{iid}").json()["data"]
        again = c.post(
            f"{WF}/instances/{iid}/withdraw", json={"expected_revision": rev + 1}, headers=hk
        )
    assert done.status_code == 200, done.text
    assert done.json()["data"]["record"]["state"] == "withdrawn"
    assert after["instance"]["status"] == "withdrawn" and after["instance"]["tasks"] == []
    assert again.status_code == 409  # cùng khóa, body khác
    with http(s, wf_token(s, A, DEPT_HEAD)) as c:
        items = c.get(f"{WF}/inbox", params={"kinds": "approval"}).json()["data"]["items"]
    assert all(i["ref"] != after["request"]["ref"] for i in items)

    # MCP: transition_record(to_state=withdrawn) trên đề nghị đang chờ duyệt.
    async def mcp() -> dict[str, Any]:
        created = await s.call(
            A, REQUESTER, "create_record",
            {"model_id": "advance_request", "values": advance(2_500_000),
             "idempotency_key": "it-wf-wd-mcp-c"},
        )  # fmt: skip
        ref = created["data"]["record"]["ref"]
        sub_ = await s.call(A, REQUESTER, "submit_request",
                            {"ref": ref, "idempotency_key": "it-wf-wd-mcp-s"})  # fmt: skip
        assert sub_["ok"], sub_
        return await s.call(
            A, REQUESTER, "transition_record",
            {"ref": ref, "to_state": "withdrawn", "reason": "Không đi nữa",
             "expected_revision": sub_["data"]["request"]["revision"],
             "idempotency_key": "it-wf-wd-mcp-w"},
        )  # fmt: skip

    out = anyio.run(mcp)
    assert out["ok"] and out["data"]["record"]["state"] == "withdrawn", out
