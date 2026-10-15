# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Dịch vụ tệp trên lớp ghép thật: Postgres 17 + SeaweedFS 4.48 (S3, chữ ký V4) + composition +
uvicorn; token ký bằng khóa test qua JWKS tĩnh (audience `xt-files`).

Phủ: ý định -> presigned PUT -> finalize -> tải về (roundtrip, sha256 khớp, khóa theo nội dung),
multipart một bước, tenant khác / người không được đọc -> 404 như id bịa, checksum lệch, vượt hạn
mức, kiểu bị cấm (magic byte vs khai báo), link hết hạn / bị sửa, IAM từ chối (tenant không có
policy tệp), policy tệp chưa quét, xóa mềm, audit hash-chain (tên tệp chỉ lưu sha256), tool MCP
`upload_file` thật + field `file` của model `tai_lieu` (FK ghép chặn tệp tenant khác).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import secrets
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.integration import seaweed
from tests.integration.composition import pgfixture
from tests.integration.composition.idp import AGENT_CLIENT, DESK_CLIENT, LocalIdp
from tests.integration.composition.mcphttp import serve
from tests.integration.composition.test_e2e import (
    DEPT_HEAD,
    DIRECTOR,
    ORG,
    REQUESTER,
    ROOT,
    Stack,
    _write,
    run_admin,
    sub,
)
from xt.adapters.db.integration import connect, tenant_of_alias
from xt.adapters.db.migrations import migrate
from xt.application.files import UploadTokens
from xt.entrypoints.files_http import with_files_api
from xt.entrypoints.serve import prepare
from xt.entrypoints.workflow_http import with_workflow_api

pytestmark = pytest.mark.integration

A, B = "fla", "flb"
F = "/files/v1"
COLLEAGUE = "NV-0014"  # cùng phòng với người đề nghị, chỉ có vai trò employee
FILES_PACK = ROOT / "packages" / "tep-dinh-kem"
PDF = b"%PDF-1.7\n" + b"1 0 obj << /Type /Catalog >> endobj\n" * 200 + b"%%EOF\n"
TTL = 10
MAX = 64 * 1024


@pytest.fixture(scope="module")
def sw() -> Iterator[seaweed.Seaweed]:
    s = seaweed.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="module")
def fstack(
    pg: pgfixture.Pg,
    idp: LocalIdp,
    mcp_port: int,
    sw: seaweed.Seaweed,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[Stack]:
    d = tmp_path_factory.mktemp("xt-files")
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
        "XT_PII_REVEAL_ORIGINS": base,
        "XT_PII_REVEAL_CLIENTS": AGENT_CLIENT,
        "XT_PII_REVEAL_TICKETS_PER_MINUTE": "60",
        "XT_MCP_HUMAN_CLIENTS": DESK_CLIENT,
        "XT_MCP_REQUIRED_SCOPES": "mcp-xt",
        "XT_MCP_SCOPES_SUPPORTED": "openid mcp-xt",
        "XT_MCP_SERVICES": "xt.entrypoints.composition:build",
        "XT_MCP_CAPABILITIES": "upload_file",
        "XT_WORKFLOW_PUBLIC_URL": base,
        # Dịch vụ tệp
        "XT_FILES_S3_ENDPOINT": sw.endpoint,
        "XT_FILES_S3_BUCKET": f"xt-it-{secrets.token_hex(3)}",
        "XT_FILES_S3_ACCESS_KEY": sw.access_key,
        "XT_FILES_S3_SECRET_KEY": sw.secret_key,
        "XT_FILES_PUBLIC_URL": base,
        "XT_FILES_MAX_BYTES": str(MAX),
        "XT_FILES_UPLOAD_TTL_SECONDS": str(TTL),
        "XT_FILES_UNSCANNED_DOWNLOAD": "uploader",  # kiểm riêng policy chặt nhất
    }
    tenants: dict[str, Any] = {}
    for alias, name, packs in ((A, "Files A", (FILES_PACK,)), (B, "Files B", ())):
        rc, out = run_admin(
            [
                "tenant", "create", "--alias", alias, "--name", name,
                "--timezone", "Asia/Ho_Chi_Minh", "--llm-egress", "masked",
            ],
            env,
        )  # fmt: skip
        assert rc == 0, out
        keys = (REQUESTER, DEPT_HEAD, DIRECTOR, COLLEAGUE)
        links = {
            "subjects": {k: sub(alias, k) for k in keys},
            "agent_grants": [
                {"person": k, "client_id": AGENT_CLIENT, "autonomy": 1, "roles": ["agent"]}
                for k in keys
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
        for pack in packs:
            common = ["pack", "install", "--tenant", alias, str(pack)]
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
    app = with_files_api(prepared, env, with_workflow_api(prepared, env, prepared.http_app()))
    running = serve(app, mcp_port)
    try:
        yield Stack(idp, running, prepared, env, tenants, {})
    finally:
        running.stop()
        prepared.close()


def ftoken(s: Stack, alias: str, key: str, **over: Any) -> str:
    claims: dict[str, Any] = {"aud": ["xt-files"], "scope": "openid xt-files"}
    claims.update(over)
    return s.idp.access_token(sub(alias, key), org=alias, **claims)


def http(s: Stack, token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=s.app.base, headers=headers, timeout=30)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def intent(c: httpx.Client, name: str, ctype: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if ctype:
        body["content_type"] = ctype
    r = c.post(F, json=body)
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


def put(s: Stack, url: str, data: bytes, **headers: str) -> httpx.Response:
    assert url.startswith(s.app.base + F + "/uploads/"), url
    return httpx.put(url, content=data, headers=headers, timeout=30)


def upload(s: Stack, c: httpx.Client, name: str, data: bytes, ctype: str | None = None) -> str:
    it = intent(c, name, ctype)
    r = put(s, it["put_url"], data)
    assert r.status_code == 200, r.text
    fid = str(it["file"]["id"])
    f = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(data)})
    assert f.status_code == 200, f.text
    return fid


def su_rows(pg: pgfixture.Pg, q: str, *args: Any) -> list[tuple[Any, ...]]:
    with pg.su() as c:
        return list(c.execute(q, args).fetchall())


# ---- danh tính / gateway -------------------------------------------------------------------


def test_auth_boundaries_and_openapi(fstack: Stack) -> None:
    s = fstack
    with http(s) as c:
        anon = c.get(F)
        anon_post = c.post(F, json={"name": "a.pdf"})
        doc = c.get(f"{F}/openapi.json")
    assert anon.status_code == 401 and anon.json()["error"]["code"] == "unauthenticated"
    assert anon.headers["www-authenticate"].startswith('Bearer realm="xt-files"')
    assert anon_post.status_code == 401
    assert doc.status_code == 200 and doc.json()["openapi"] == "3.1.0"
    paths = doc.json()["paths"]
    assert {F, f"{F}/{{file_id}}", f"{F}/{{file_id}}/content", f"{F}/uploads/{{token}}"} <= set(
        paths
    )
    # Token MCP / workflow không dùng được cho files; token files không mở được /mcp.
    for tok in (
        s.token(A, REQUESTER),
        s.idp.access_token(
            sub(A, REQUESTER), org=A, aud=["xt-workflow"], scope="openid xt-workflow"
        ),
    ):
        with http(s, tok) as c:
            assert c.get(F).status_code == 401
    mcp = httpx.post(
        f"{s.app.base}/mcp",
        headers={"Authorization": f"Bearer {ftoken(s, A, REQUESTER)}",
                 "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=30,
    )  # fmt: skip
    assert mcp.status_code == 401
    with http(s, ftoken(s, A, REQUESTER, scope="openid")) as c:
        assert c.get(F).status_code == 403
    with http(s, ftoken(s, A, REQUESTER, azp=DESK_CLIENT)) as c:
        assert c.get(F).status_code == 401  # phiên người không gọi API


# ---- roundtrip ---------------------------------------------------------------------------------


def test_intent_put_finalize_download_roundtrip(fstack: Stack, pg: pgfixture.Pg) -> None:
    s = fstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "Hóa đơn khách sạn.pdf")
        f = it["file"]
        assert f["status"] == "awaiting_upload" and f["declared_type"] == "application/pdf"
        assert it["put_url"] == it["upload_url"] and it["expires_at"]
        fid = f["id"]
        assert c.get(f"{F}/{fid}/content").status_code == 409  # chưa có byte
        r = put(s, it["put_url"], PDF, **{"Content-Type": "application/pdf"})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["file"]["status"] == "uploaded"
        assert r.json()["data"]["file"]["sha256"] == sha(PDF)
        again = put(s, it["put_url"], PDF)  # link một lần
        assert again.status_code == 409 and again.json()["error"]["details"]["reason"] == (
            "already_uploaded"
        )
        fin = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(PDF)})
        assert fin.status_code == 200, fin.text
        meta = fin.json()["data"]["file"]
        assert meta["status"] == "not_scanned" and meta["size_bytes"] == len(PDF)
        assert meta["ref"] == f"file:{fid}"
        got = c.get(f"{F}/{fid}")
        dl = c.get(f"{F}/{fid}/content")
        mine = c.get(F)
    assert got.status_code == 200 and got.json()["data"]["file"]["sha256"] == sha(PDF)
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.headers["etag"] == f'"sha256-{sha(PDF)}"'
    assert "filename*=UTF-8''H%C3%B3a%20%C4%91%C6%A1n" in dl.headers["content-disposition"]
    assert dl.headers["x-content-type-options"] == "nosniff"
    assert fid in [x["id"] for x in mine.json()["data"]["files"]]
    # Object storage: khóa theo nội dung, có tiền tố tenant; quarantine đã dọn.
    (key,) = su_rows(pg, "SELECT object_key FROM xt_meta.file_object WHERE id = %s", fid)[0]
    assert key == f"t/{s.tenants[A].id}/sha256/{sha(PDF)[:2]}/{sha(PDF)}"
    s.outputs["files-roundtrip"] = fid


def test_multipart_one_step_and_content_digest_put(fstack: Stack) -> None:
    s = fstack
    body = "Biên bản họp giao ban\nNgày 02/11\n".encode()
    with http(s, ftoken(s, A, REQUESTER)) as c:
        r = c.post(F, files={"file": ("bien-ban.txt", body, "text/plain")})
        assert r.status_code == 201, r.text
        f = r.json()["data"]["file"]
        assert f["status"] == "not_scanned" and f["sha256"] == sha(body)
        assert c.get(f"{F}/{f['id']}/content").content == body
        bad = c.post(F, files={"file": ("x.txt", body, "text/plain")}, data={"sha256": "0" * 64})
        assert bad.status_code == 422 and bad.json()["error"]["details"]["reason"] == (
            "checksum_mismatch"
        )
        # Idempotency-Key: gửi lại cùng khóa -> cùng tệp, không tạo mới.
        h = {"Idempotency-Key": "it-files-mp-0001"}
        one = c.post(F, files={"file": ("dup.txt", body, "text/plain")}, headers=h)
        two = c.post(F, files={"file": ("dup.txt", body, "text/plain")}, headers=h)
        assert one.status_code == 201 and two.status_code == 200
        assert one.json()["data"]["file"]["id"] == two.json()["data"]["file"]["id"]
        # PUT có Content-Digest (RFC 9530): finalize luôn, không cần bước 3.
        it = intent(c, "anh.png")
        png = b"\x89PNG\r\n\x1a\n" + secrets.token_bytes(500)
        dig = "sha-256=:" + base64.b64encode(hashlib.sha256(png).digest()).decode() + ":"
        r = put(s, it["put_url"], png, **{"Content-Digest": dig})
        assert r.status_code == 200 and r.json()["data"]["file"]["status"] == "not_scanned"
        page = httpx.get(intent(c, "khac.pdf")["upload_url"], timeout=30)
    assert page.status_code == 200 and "<input id=f type=file>" in page.text
    assert "script-src 'sha256-" in page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]


# ---- từ chối ----------------------------------------------------------------------------------


def test_checksum_mismatch_rejected_then_reupload(fstack: Stack, pg: pgfixture.Pg) -> None:
    s = fstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "hd.pdf")
        fid = it["file"]["id"]
        assert put(s, it["put_url"], PDF).status_code == 200
        bad = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(b"other")})
        assert bad.status_code == 422, bad.text
        assert bad.json()["error"]["details"]["reason"] == "checksum_mismatch"
        assert c.get(f"{F}/{fid}").json()["data"]["file"]["status"] == "awaiting_upload"
        assert put(s, it["put_url"], PDF).status_code == 200  # PUT lại trong hạn
        ok = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(PDF)})
    assert ok.status_code == 200
    acts = [
        r[0]
        for r in su_rows(
            pg, "SELECT action FROM xt_meta.file_event WHERE file_id = %s ORDER BY at, id", fid
        )
    ]
    assert acts == ["created", "uploaded", "rejected", "uploaded", "finalized"]


def test_oversize_rejected(fstack: Stack) -> None:
    s = fstack
    big = b"%PDF-1.7\n" + b"0" * MAX
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "lon.pdf")
        r = put(s, it["put_url"], big)
        mp = c.post(F, files={"file": ("lon.pdf", big + b"x" * 70_000, "application/pdf")})
        status = c.get(f"{F}/{it['file']['id']}").json()["data"]["file"]["status"]
    assert r.status_code == 413 and r.json()["error"]["code"] == "resource_constraint"
    assert r.json()["error"]["details"]["limit_bytes"] == MAX
    assert mp.status_code == 413
    assert status == "awaiting_upload"


def test_disallowed_and_mismatched_types_rejected(fstack: Stack) -> None:
    s = fstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        html = c.post(F, json={"name": "trang.html", "content_type": "text/html"})
        exe = c.post(F, json={"name": "setup.exe"})
        it = intent(c, "hoa-don.pdf")
        fake = put(s, it["put_url"], b"MZ\x90\x00\x03" + b"\x00" * 100)  # PE khai là PDF
        it2 = intent(c, "ghi-chu.pdf", "application/pdf")
        text = put(s, it2["put_url"], b"khong phai pdf")
        mp = c.post(F, files={"file": ("x.png", b"<svg onload=alert(1)>", "image/png")})
    assert html.status_code == 415 and html.json()["error"]["details"]["reason"] == (
        "content_type_not_allowed"
    )
    assert exe.status_code == 415
    assert fake.status_code == 415
    assert fake.json()["error"]["details"]["reason"] == "content_type_not_allowed"
    assert text.status_code == 415
    assert text.json()["error"]["details"] == {
        "reason": "content_type_mismatch", "declared": "application/pdf", "detected": "text"
    }  # fmt: skip
    assert mp.status_code == 415


def test_presigned_url_expiry_and_tampering(fstack: Stack) -> None:
    s = fstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "het-han.pdf")
        url = it["put_url"]
        token = url.rsplit("/", 1)[1]
        body, mac = token.split(".")
        forged = put(s, url.replace(token, body + "." + mac[::-1]), PDF)
        assert forged.status_code == 403
        assert forged.json()["error"]["details"]["reason"] == "upload_url_invalid"
        # Đổi tenant trong payload mà giữ chữ ký: bị từ chối.
        other = UploadTokens(b"y" * 32).issue(B, uuid.UUID(it["file"]["id"]), uuid.uuid4(), 2**40)
        swapped = put(s, url.replace(token, other.split(".")[0] + "." + mac), PDF)
        assert swapped.status_code == 403
        time.sleep(TTL + 1)
        late = put(s, url, PDF)
        status = c.get(f"{F}/{it['file']['id']}").json()["data"]["file"]["status"]
    assert late.status_code == 403, late.text
    assert late.json()["error"]["details"]["reason"] == "upload_url_expired"
    assert status == "expired"


# ---- ranh giới tenant / IAM / policy quét ---------------------------------------------------


def test_cross_tenant_and_non_reader_get_indistinguishable_404(fstack: Stack) -> None:
    s = fstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, "hop-dong.pdf", PDF)
    bogus = str(uuid.uuid4())
    answers = []
    for alias, key, target in ((B, REQUESTER, fid), (A, COLLEAGUE, fid), (A, REQUESTER, bogus)):
        with http(s, ftoken(s, alias, key)) as c:
            for path in (f"{F}/{target}", f"{F}/{target}/content"):
                r = c.get(path)
                answers.append((r.status_code, r.json()["error"]["code"],
                                r.json()["error"]["message"]))  # fmt: skip
            r = c.delete(f"{F}/{target}")
            answers.append((r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]))
    assert len(set(answers)) == 1 and answers[0][0] == 404, answers
    # Người duyệt (dept_head) đọc được metadata, nhưng tệp chưa quét chỉ người tải lên tải về.
    with http(s, ftoken(s, A, DEPT_HEAD)) as c:
        meta = c.get(f"{F}/{fid}")
        dl = c.get(f"{F}/{fid}/content")
        rm = c.delete(f"{F}/{fid}")
    assert meta.status_code == 200
    assert dl.status_code == 409 and dl.json()["error"]["details"]["reason"] == "file_not_scanned"
    assert rm.status_code == 403  # đọc được nhưng không có file.delete


def test_iam_denial_without_file_policy(fstack: Stack) -> None:
    s = fstack
    with http(s, ftoken(s, B, REQUESTER)) as c:
        r = c.post(F, json={"name": "a.pdf"})
        mp = c.post(F, files={"file": ("a.txt", b"hello", "text/plain")})
    assert r.status_code == 403 and r.json()["error"]["code"] == "access_denied"
    assert mp.status_code == 403


def test_soft_delete_and_audit_chain(fstack: Stack, pg: pgfixture.Pg) -> None:
    s = fstack
    name = "CCCD 079123456789.pdf"
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, name, PDF)
        meta = c.get(f"{F}/{fid}").json()["data"]["file"]
        assert c.get(f"{F}/{fid}/content").status_code == 200
        rm = c.delete(f"{F}/{fid}")
        after = c.get(f"{F}/{fid}")
        again = c.delete(f"{F}/{fid}")
    # Tier masked (client cloud): số CCCD trong tên tệp bị che.
    assert "079123456789" not in meta["name"] and "[PII]" in meta["name"]
    assert rm.status_code == 200 and rm.json()["data"]["file"]["deleted"] is True
    assert after.status_code == 404 and again.status_code == 404
    (deleted_at,) = su_rows(pg, "SELECT deleted_at FROM xt_meta.file_object WHERE id = %s", fid)[0]
    assert deleted_at is not None
    events = su_rows(
        pg,
        "SELECT model, op, diff::text, actor_id::text FROM xt_audit.event"
        " WHERE record_id = %s OR (model = 'xt_meta.file_event' AND diff::text LIKE %s)"
        " ORDER BY seq",
        fid,
        f"%{fid}%",
    )
    models = [(e[0], e[1]) for e in events]
    assert ("xt_meta.file_object", "INSERT") in models
    assert ("xt_meta.file_object", "UPDATE") in models
    file_events = [e for e in events if e[0] == "xt_meta.file_event"]
    acts = {json.loads(e[2])["new"]["action"] for e in file_events if json.loads(e[2]).get("new")}
    assert {"created", "uploaded", "finalized", "downloaded", "deleted"} <= acts
    blob = " ".join(e[2] for e in events)
    leaked = [e for e in events if "079123456789" in e[2]]
    assert not leaked, leaked
    assert "redacted:hmac:" in blob  # FB-04: HMAC có khóa, không phải sha256 trần
    with pg.su() as c:
        ok = c.execute("SELECT * FROM xt_audit.verify(%s)", (s.tenants[A].id,)).fetchone()
    assert ok is not None and ok[0] > 0 and ok[1] is None, ok  # chuỗi nguyên vẹn


# ---- MCP upload_file + field `file` ----------------------------------------------------------


@pytest.mark.anyio
async def test_mcp_upload_file_and_record_file_field(fstack: Stack) -> None:
    s = fstack
    txt = await s.call(A, REQUESTER, "upload_file", {
        "name": "bien-ban.md", "source": "text", "text": "# Biên bản\nĐã chốt.\n",
        "idempotency_key": "it-files-mcp-text-1"})  # fmt: skip
    assert txt["ok"], txt
    f = txt["data"]["file"]
    assert f["status"] == "not_scanned" and f["content_type"] == "text/markdown"
    link = await s.call(A, REQUESTER, "upload_file", {
        "name": "hoadon.pdf", "source": "upload_link", "content_type": "application/pdf",
        "idempotency_key": "it-files-mcp-link-1"})  # fmt: skip
    assert link["ok"], link
    assert link["data"]["file"]["status"] == "awaiting_upload"
    assert link["data"]["put_url"].startswith(s.app.base + F + "/uploads/")
    assert put(s, link["data"]["put_url"], PDF).status_code == 200
    bad = await s.call(A, REQUESTER, "upload_file", {
        "name": "x.pdf", "source": "base64", "base64": base64.b64encode(b"MZ\x90").decode(),
        "idempotency_key": "it-files-mcp-b64-1"})  # fmt: skip
    assert not bad["ok"] and bad["error"]["details"]["reason"] == "content_type_not_allowed"
    denied = await s.call(
        B,
        REQUESTER,
        "upload_file",
        {"name": "a.md", "source": "text", "text": "x", "idempotency_key": "it-files-mcp-b-1"},
    )
    assert not denied["ok"] and denied["error"]["code"] == "access_denied"
    # Field kiểu `file` của model tai_lieu nhận ref của tệp vừa tải.
    rec = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Biên bản giao ban", "tep": f["ref"],
                                           "loai": "bien_ban"},
        "idempotency_key": "it-files-mcp-rec-1"})  # fmt: skip
    assert rec["ok"], rec
    assert rec["data"]["record"]["values"]["tep"] == f["ref"]
    # Tệp của tenant khác / id bịa: FK ghép (tenant_id, id) chặn ở DB.
    with http(s, ftoken(s, B, REQUESTER)) as c:
        b_file = c.post(F, files={"file": ("b.txt", b"tenant B", "text/plain")})
    assert b_file.status_code == 403  # tenant B không có policy tệp
    foreign = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Sai", "tep": f"file:{uuid.uuid4()}"},
        "idempotency_key": "it-files-mcp-rec-2"})  # fmt: skip
    assert not foreign["ok"], foreign
    assert foreign["error"]["code"] == "invalid_request", foreign
    assert foreign["error"]["details"]["reason"] == "file_not_attachable"  # FB-09


def test_upload_file_capability_listed(fstack: Stack) -> None:
    assert fstack.prepared.dispatcher.settings.available("upload_file")
    assert Path(FILES_PACK / "pack.json").exists()


def test_headless_demo_files_step(fstack: Stack, tmp_path: Path) -> None:
    """Bước 5 của docs/headless-demo.sh (curl thật) trên stack này: ý định -> PUT -> finalize ->
    tải về -> multipart. Các bước 1-4 cần Keycloak/PostgREST thật (e2e E8), ở đây bỏ qua."""
    import os
    import subprocess

    s = fstack
    tok = tmp_path / "files_token"
    tok.write_text(ftoken(s, A, REQUESTER), encoding="utf-8")
    env = {
        **os.environ,
        "XT_AUTH_BASE": "http://127.0.0.1:9",  # SSO không có ở đây: bước 1 "chưa triển khai"
        "XT_API_BASE": s.app.base,
        "XT_FILES_TOKEN_FILE": str(tok),
    }
    r = subprocess.run(
        ["bash", str(ROOT / "docs" / "headless-demo.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    out = r.stdout
    assert "5a không token: 401" in out, out
    assert "5e GET .../content: 200" in out and "sha256 khớp" in out, out
    assert "5f multipart một bước: 201" in out, out
    assert re.search(r"5 Files\s+OK", out), out
