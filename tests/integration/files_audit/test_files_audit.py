# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Audit tấn công REST /files/v1 (docs/audit-files.md).

Test thường = thuộc tính an toàn đã kiểm là ĐÚNG (giữ làm hồi quy).
`xfail(strict=True)` = lỗi đã tái hiện (FB-xx trong docs/audit-files.md): khi sửa xong test sẽ
XPASS -> strict làm đỏ -> bỏ marker.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import socket
import subprocess
import threading
import time
import uuid
from typing import Any, cast

import httpx
import pytest

from tests.integration.composition.fixtures import DEPT_HEAD, DIRECTOR, REQUESTER, Stack, sub
from tests.integration.files_audit.conftest import COLLEAGUE, MAX, TTL, A, B
from xt.adapters.db.files import PgFileRepository
from xt.domain.model.context import ModelActor
from xt.entrypoints.files_http import CompositionFilesBackend, FilesPep

pytestmark = pytest.mark.integration

F = "/files/v1"
PDF = b"%PDF-1.7\n" + b"1 0 obj << /Type /Catalog >> endobj\n" * 50 + b"%%EOF\n"


def ftoken(s: Stack, alias: str, key: str, **over: Any) -> str:
    claims: dict[str, Any] = {"aud": ["xt-files"], "scope": "openid xt-files"}
    claims.update(over)
    return s.idp.access_token(sub(alias, key), org=alias, **claims)


def http(s: Stack, token: str | None = None) -> httpx.Client:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.Client(base_url=s.app.base, headers=headers, timeout=60)


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def intent(c: httpx.Client, name: str, ctype: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"name": name}
    if ctype:
        body["content_type"] = ctype
    r = c.post(F, json=body)
    assert r.status_code == 201, r.text
    return dict(r.json()["data"])


def upload(s: Stack, c: httpx.Client, name: str, data: bytes, ctype: str | None = None) -> str:
    it = intent(c, name, ctype)
    r = httpx.put(it["put_url"], content=data, timeout=60)
    assert r.status_code == 200, r.text
    fid = str(it["file"]["id"])
    f = c.post(f"{F}/{fid}/finalize", json={"sha256": sha(data)})
    assert f.status_code == 200, f.text
    return fid


def raw_http(port: int, request: bytes, timeout: float = 20.0) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as so:
        so.sendall(request)
        out = b""
        try:
            while chunk := so.recv(65536):
                out += chunk
        except (TimeoutError, ConnectionResetError):
            pass  # server đóng sau khi trả lời (phần thân thừa) = bình thường
    return out


# ---- presigned link: giả mạo / sửa / dùng lại ---------------------------------------------------


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def test_upload_token_tampering_rejected(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "a.pdf")
        other = intent(c, "b.pdf")
    url = it["put_url"]
    token = url.rsplit("/", 1)[1]
    body, mac = token.split(".")
    payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
    tenant, fid, person, exp = payload.split("~")
    ofid = uuid.UUID(other["file"]["id"]).hex
    variants = {
        "mac_flip": body + "." + ("A" if mac[0] != "A" else "B") + mac[1:],
        "mac_empty": body + ".",
        "no_dot": body + mac,
        "other_file": _b64u(f"{tenant}~{ofid}~{person}~{exp}".encode()) + "." + mac,
        "other_tenant": _b64u(f"{B}~{fid}~{person}~{exp}".encode()) + "." + mac,
        "other_person": _b64u(f"{tenant}~{fid}~{uuid.uuid4().hex}~{exp}".encode()) + "." + mac,
        "later_expiry": _b64u(f"{tenant}~{fid}~{person}~{int(exp) + 3600}".encode()) + "." + mac,
        "field_reorder": _b64u(f"{fid}~{tenant}~{person}~{exp}".encode()) + "." + mac,
        "extra_field": _b64u(f"{tenant}~{fid}~{person}~{exp}~x".encode()) + "." + mac,
        "huge": "A" * 600 + "." + mac,
    }
    base = url[: -len(token)]
    for name, tok in variants.items():
        r = httpx.put(base + tok, content=PDF, timeout=30)
        assert r.status_code == 403, (name, r.status_code, r.text)
        assert r.json()["error"]["details"]["reason"] == "upload_url_invalid", name
    # Query string / dấu `/` thêm không đổi được đích; đường dẫn lồng là 404.
    assert httpx.put(url + "?file=" + ofid, content=PDF, timeout=30).status_code == 200
    again = httpx.put(url, content=PDF, timeout=30)
    assert again.status_code == 409  # một lần
    assert httpx.put(url + "/../" + token, content=PDF, timeout=30).status_code in (404, 409)
    # Link của tệp A không đụng tới tệp B (B vẫn awaiting_upload).
    with http(s, ftoken(s, A, REQUESTER)) as c:
        st = c.get(f"{F}/{other['file']['id']}").json()["data"]["file"]["status"]
    assert st == "awaiting_upload"


def test_upload_token_method_swap(xstack: Stack) -> None:
    """GET trên link tải lên chỉ trả trang HTML (không bao giờ là byte của tệp); POST/DELETE 405."""
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "m.pdf")
    url = it["put_url"]
    assert httpx.put(url, content=PDF, timeout=30).status_code == 200
    g = httpx.get(url, timeout=30)
    assert g.status_code == 409 and PDF not in g.content
    for m in ("POST", "DELETE", "PATCH"):
        assert httpx.request(m, url, content=PDF, timeout=30).status_code in (404, 405), m


def test_upload_token_non_ascii_is_403_not_500(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "u.pdf")
    url = it["put_url"]
    token = url.rsplit("/", 1)[1]
    body, mac = token.split(".")
    bad = url[: -len(token)] + body + "." + "%C3%A9" + mac[1:]
    r = httpx.put(bad, content=PDF, timeout=30)
    assert r.status_code == 403, (r.status_code, r.text)


# ---- tên tệp / khóa object ---------------------------------------------------------------------


def test_path_traversal_names_never_reach_object_key(xstack: Stack, pg: Any) -> None:
    s = xstack
    bad = ["../../etc/passwd.txt", "..\\x.txt", "a/b.txt", "..", ".", "x\x00.txt", "x‮.txt",
           "x\r\nSet-Cookie: a=b.txt", "", " ", "a" * 256 + ".txt"]  # fmt: skip
    with http(s, ftoken(s, A, REQUESTER)) as c:
        for n in bad:
            r = c.post(F, json={"name": n, "content_type": "text/plain"})
            assert r.status_code in (400, 415), (n, r.status_code, r.text)
        # %2e%2e và unicode dot-dot là tên thường, không phải đường dẫn: chấp nhận, khóa vẫn
        # theo sha256.
        fid = upload(s, c, "%2e%2e%2f%2e%2e%2fpasswd.txt", b"hello traversal\n")
        fid2 = upload(s, c, "\uff0e\uff0e\u2215x.txt", b"hello unicode\n")
        dl = c.get(f"{F}/{fid}/content")
    assert dl.status_code == 200
    cd = dl.headers["content-disposition"]
    assert cd.startswith("attachment; ") and "\r" not in cd and "\n" not in cd
    with pg.su() as conn:
        rows = conn.execute(
            "SELECT object_key FROM xt_meta.file_object WHERE id = ANY(%s)", ([fid, fid2],)
        ).fetchall()
    tid = s.tenants[A].id
    for (key,) in rows:
        assert key.startswith(f"t/{tid}/sha256/") and ".." not in key


# ---- kiểu tệp: polyglot / spoofing -------------------------------------------------------------


def test_markup_and_executables_rejected(xstack: Stack) -> None:
    s = xstack
    cases = [
        ("a.png", "image/png", b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>"),
        ("a.txt", "text/plain", b"\xef\xbb\xbf  <html><script>alert(1)</script>"),
        ("a.txt", "text/plain", b"<?xml version='1.0'?><svg/>"),
        ("a.jpg", "image/jpeg", b"<!doctype html><script>alert(1)</script>"),
        ("a.pdf", "application/pdf", b"\x7fELF\x02\x01\x01" + b"\x00" * 64),
        ("a.zip", "application/zip", b"\x1f\x8b\x08\x00" + b"\x00" * 64),
        ("a.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64),
        ("a.txt", "text/plain", b"#!/bin/sh\nrm -rf /\n"),
        ("a.png", "image/png", b"GIF89a" + b"\x00" * 64),  # GIF khai là PNG
    ]  # fmt: skip
    with http(s, ftoken(s, A, REQUESTER)) as c:
        for name, ctype, body in cases:
            r = c.post(F, files={"file": (name, body, ctype)})
            assert r.status_code == 415, (name, ctype, r.status_code, r.text)


def test_polyglots_served_inert(xstack: Stack) -> None:
    """Polyglot lọt qua magic byte (PDF+HTML, GIF+JS, text có HTML ở giữa, ZIP tùy ý) vẫn vô hại khi
    tải về: Content-Type = kiểu đã kiểm, nosniff, attachment, CSP sandbox."""
    s = xstack
    polys = [
        ("p.pdf", "application/pdf", b"%PDF-1.4\n<html><script>alert(document.domain)</script>"),
        ("g.gif", "image/gif", b"GIF89a/*<script>alert(1)</script>*/=1;"),
        ("t.txt", "text/plain", b"hello\n<script>alert(1)</script>\n"),
        ("s.txt", "text/plain", b"<!DOCTYPE svg><svg onload=alert(1)>"),
        ("z.zip", "application/zip", b"PK\x03\x04" + b"<html><script>alert(1)</script>" * 4),
        ("pz.pdf", "application/pdf", b"%PDF-1.4\n" + b"PK\x03\x04" + b"\x00" * 64),
    ]
    with http(s, ftoken(s, A, REQUESTER)) as c:
        for name, ctype, body in polys:
            r = c.post(F, files={"file": (name, body, ctype)})
            assert r.status_code == 201, (name, r.text)
            dl = c.get(f"{F}/{r.json()['data']['file']['id']}/content")
            assert dl.status_code == 200 and dl.content == body
            h = dl.headers
            assert h["content-type"].split(";")[0] == ctype, name
            assert h["x-content-type-options"] == "nosniff"
            assert h["content-disposition"].startswith("attachment;")
            assert "sandbox" in h["content-security-policy"]
            assert "default-src 'none'" in h["content-security-policy"]


def test_text_validation_covers_whole_file(xstack: Stack) -> None:
    s = xstack
    body = b"A" * 9000 + b"\x00\x7fELF\x02\x01\x01" + secrets.token_bytes(256)
    with http(s, ftoken(s, A, REQUESTER)) as c:
        r = c.post(F, files={"file": ("log.txt", body, "text/plain")})
    assert r.status_code == 415, (r.status_code, r.text[:200])


def test_zip_bomb_is_not_unpacked(xstack: Stack) -> None:
    """xT không giải nén gì: zip 'bom' (42 kiểu) chỉ là byte; nhận/trả nguyên văn, nhanh."""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("bomb.bin", b"\x00" * (64 * 1024 * 1024))  # 64 MiB -> ~64 KiB nén
    data = buf.getvalue()
    assert len(data) < MAX
    with http(s := xstack, ftoken(s, A, REQUESTER)) as c:
        t0 = time.monotonic()
        r = c.post(F, files={"file": ("bomb.zip", data, "application/zip")})
        took = time.monotonic() - t0
        assert r.status_code == 201, r.text
        dl = c.get(f"{F}/{r.json()['data']['file']['id']}/content")
    assert dl.content == data and took < 10


# ---- quá hạn mức / Content-Length nói dối --------------------------------------------------------


def test_oversize_put_lies(xstack: Stack) -> None:
    s = xstack
    port = s.app.port
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "big.txt", "text/plain")
    path = it["put_url"].split(str(port), 1)[1]
    # Content-Length nhỏ nhưng gửi nhiều hơn: chỉ CL byte được đọc (h11), phần thừa là request
    # rác -> không bao giờ vượt hạn mức.
    body = b"x" * (MAX * 2)
    req = (f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n"
           "Connection: close\r\n\r\n").encode() + body  # fmt: skip
    out = raw_http(port, req)
    assert out.startswith((b"HTTP/1.1 200", b"HTTP/1.1 400")), out[:300]
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it2 = intent(c, "big2.txt", "text/plain")
        meta = c.get(f"{F}/{it['file']['id']}").json()["data"]["file"]
    assert meta["size_bytes"] in (None, 10), meta
    # Chunked không có Content-Length -> 411; CL lớn hơn hạn mức -> 413 ngay.
    path2 = it2["put_url"].split(str(port), 1)[1]
    chunked = (f"PUT {path2} HTTP/1.1\r\nHost: 127.0.0.1\r\nTransfer-Encoding: chunked\r\n"
               "Connection: close\r\n\r\n5\r\nhello\r\n0\r\n\r\n").encode()  # fmt: skip
    assert raw_http(port, chunked).startswith(b"HTTP/1.1 411")
    huge = (f"PUT {path2} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {MAX + 1}\r\n"
            "Connection: close\r\n\r\n").encode()  # fmt: skip
    assert raw_http(port, huge).startswith(b"HTTP/1.1 413")
    # CL + TE: chunked thắng (h11) nhưng Spool vẫn chặn ở MAX.
    chunk = b"y" * 65536
    smug = (
        f"PUT {path2} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 10\r\n"
        "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n"
    ).encode()
    smug += (b"%x\r\n" % len(chunk) + chunk + b"\r\n") * ((MAX // len(chunk)) + 2) + b"0\r\n\r\n"
    out = raw_http(port, smug)
    assert out.startswith(b"HTTP/1.1 413"), out[:300]


def test_multipart_cl_te_does_not_spool_unbounded(xstack: Stack) -> None:
    """Content-Length nhỏ + Transfer-Encoding: chunked: `_length` tin CL, h11 đọc chunked, Starlette
    ghi phần `file` ra tệp tạm KHÔNG giới hạn rồi mới tới Spool. Kỳ vọng: từ chối trước khi nhận
    quá MAX + 64 KiB. Đo: server đọc hết bao nhiêu byte trước khi trả lời."""
    s = xstack
    port = s.app.port
    tok = ftoken(s, A, REQUESTER)
    boundary = "xtaudit"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.txt\"\r\n"
            "Content-Type: text/plain\r\n\r\n").encode()  # fmt: skip
    total_target = MAX * 128  # 32 MiB: lớn hơn hẳn bộ đệm socket loopback (~1-6 MiB)
    sent = 0
    with socket.create_connection(("127.0.0.1", port), timeout=30) as so:
        so.sendall(
            (f"POST {F} HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer {tok}\r\n"
             f"Content-Type: multipart/form-data; boundary={boundary}\r\nContent-Length: 100\r\n"
             "Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n").encode()
        )  # fmt: skip
        so.sendall(b"%x\r\n" % len(head) + head + b"\r\n")
        chunk = b"z" * 65536
        try:
            while sent < total_target:
                so.sendall(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
                sent += len(chunk)
            tail = f"\r\n--{boundary}--\r\n".encode()
            so.sendall(b"%x\r\n" % len(tail) + tail + b"\r\n0\r\n\r\n")
        except OSError:
            pass  # server đóng sớm = đúng
        resp = b""
        try:
            while c := so.recv(65536):
                resp += c
        except OSError:
            pass
    # An toàn: server từ chối (411/413/400) NGAY sau header rồi đóng kết nối, không đọc tiếp thân:
    # client bị reset trước khi gửi hết (phần đã "gửi" chỉ nằm trong bộ đệm socket của kernel,
    # không bao giờ tới Starlette/đĩa tạm). Lỗi cũ: server đọc + spool đủ 32 MiB rồi mới 413.
    assert resp.startswith((b"HTTP/1.1 411", b"HTTP/1.1 413", b"HTTP/1.1 400")), resp[:200]
    assert sent < total_target, (sent, resp[:200])


# ---- TOCTOU: finalize khi PUT thứ hai đang bay; PUT song song ------------------------------------


def test_parallel_puts_single_winner_and_finalize_vs_inflight_put(xstack: Stack) -> None:
    s = xstack
    port = s.app.port
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "race.txt", "text/plain")
        fid = it["file"]["id"]
        url = it["put_url"]
        bodies = [f"body-{i}\n".encode() * 100 for i in range(8)]
        res: list[httpx.Response] = []
        lock = threading.Lock()

        def go(b: bytes) -> None:
            r = httpx.put(url, content=b, timeout=60)
            with lock:
                res.append(r)

        ts = [threading.Thread(target=go, args=(b,)) for b in bodies]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        codes = sorted(r.status_code for r in res)
        assert codes.count(200) == 1 and codes.count(409) == 7, codes
        win = next(r for r in res if r.status_code == 200).json()["data"]["file"]["sha256"]
        # PUT chậm thứ hai (đang gửi thân) trong lúc finalize: PUT phải thua, byte = bản thắng.
        path = url.split(str(port), 1)[1]
        evil = b"EVIL\n" * 1000
        so = socket.create_connection(("127.0.0.1", port), timeout=30)
        so.sendall((f"PUT {path} HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: {len(evil)}\r\n"
                    "Connection: close\r\n\r\n").encode() + evil[:100])  # fmt: skip
        time.sleep(0.5)
        fin = c.post(f"{F}/{fid}/finalize", json={"sha256": win})
        assert fin.status_code == 200, fin.text
        so.sendall(evil[100:])
        resp = b""
        while chunk := so.recv(65536):
            resp += chunk
        so.close()
        assert resp.startswith(b"HTTP/1.1 409"), resp[:300]
        dl = c.get(f"{F}/{fid}/content")
    assert sha(dl.content) == win


# ---- tenant / S3 / RLS / FK ----------------------------------------------------------------------


def test_rls_and_cross_tenant_fk(xstack: Stack, pg: Any) -> None:
    s = xstack
    with http(s, ftoken(s, B, REQUESTER)) as c:
        b_fid = upload(s, c, "b-secret.txt", b"tenant B secret\n")
    with http(s, ftoken(s, A, REQUESTER)) as c:
        assert c.get(f"{F}/{b_fid}").status_code == 404
        assert c.get(f"{F}/{b_fid}/content").status_code == 404
        assert c.post(f"{F}/{b_fid}/finalize", json={"sha256": "0" * 64}).status_code == 404
        assert c.delete(f"{F}/{b_fid}").status_code == 404
    # RLS ở mức repo (xt_app + tenant A): không thấy hàng của B dù biết id.
    actor = ModelActor(s.tenants[A], uuid.uuid4(), (), trace_id="audit")
    rt = cast(Any, s.prepared.services).runtime
    with rt.uow.scope(actor):
        got = rt.uow.run(lambda: PgFileRepository(rt.uow, actor).get(uuid.UUID(b_fid)))
    assert got is None
    # xt_app không UPDATE được tenant_id/name/created_by (GRANT theo cột).
    with pg.su() as conn:
        cols = {
            r[0]
            for r in conn.execute(
                "SELECT column_name FROM information_schema.column_privileges WHERE"
                " table_schema='xt_meta' AND table_name='file_object' AND grantee='xt_app'"
                " AND privilege_type='UPDATE'"
            ).fetchall()
        }
        assert not {"tenant_id", "name", "created_by", "id"} & cols, cols
        rls = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid ="
            " 'xt_meta.file_object'::regclass"
        ).fetchone()
        assert tuple(rls) == (True, True)


@pytest.mark.anyio
async def test_record_cannot_reference_other_tenant_file(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, B, REQUESTER)) as c:
        b_fid = upload(s, c, "b.txt", b"tenant B file\n")
    rec = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Cheo tenant", "tep": f"file:{b_fid}"},
        "idempotency_key": "audit-fk-cross-1"})  # fmt: skip
    assert not rec["ok"], rec


@pytest.mark.anyio
async def test_record_cannot_reference_colleagues_file(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, A, COLLEAGUE)) as c:
        fid = upload(s, c, "luong-ca-nhan.txt", b"bang luong rieng\n")
    rec = await s.call(A, REQUESTER, "create_record", {
        "model_id": "tai_lieu", "values": {"tieu_de": "Muon tep", "tep": f"file:{fid}"},
        "idempotency_key": "audit-fk-colleague-1"})  # fmt: skip
    assert not rec["ok"], rec


def test_s3_requires_sigv4(xstack: Stack) -> None:
    """Cổng S3 đòi chữ ký: không chữ ký / chữ ký sai không đọc được object."""
    s = xstack
    ep = s.env["XT_FILES_S3_ENDPOINT"]
    bucket = s.env["XT_FILES_S3_BUCKET"]
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, "s3.txt", b"s3 secret\n")
    key = f"t/{s.tenants[A].id}/sha256/{sha(b's3 secret\n')[:2]}/{sha(b's3 secret\n')}"
    assert fid
    r = httpx.get(f"{ep}/{bucket}/{key}", timeout=10)
    assert r.status_code in (400, 403) and b"s3 secret" not in r.content
    r2 = httpx.get(f"{ep}/{bucket}/{key}", timeout=10, headers={
        "Authorization": "AWS4-HMAC-SHA256 Credential=x/20260101/us-east-1/s3/aws4_request, "
                         "SignedHeaders=host, Signature=" + "0" * 64})  # fmt: skip
    assert r2.status_code in (400, 403) and b"s3 secret" not in r2.content


def test_seaweed_filer_requires_auth(xstack: Stack) -> None:
    """Từ một tiến trình bất kỳ trong cùng mạng (ở đây: chính container), filer :8888 trả object
    của tenant mà không cần chữ ký. Kỳ vọng: 401/403."""
    s = xstack
    bucket = s.env["XT_FILES_S3_BUCKET"]
    secret = b"filer leak " + secrets.token_hex(8).encode() + b"\n"
    with http(s, ftoken(s, A, REQUESTER)) as c:
        upload(s, c, "leak.txt", secret)
    key = f"t/{s.tenants[A].id}/sha256/{sha(secret)[:2]}/{sha(secret)}"
    r = subprocess.run(
        ["docker", "exec", s.outputs["seaweed"], "wget", "-q", "-O", "-",
         f"http://127.0.0.1:8888/buckets/{bucket}/{key}"],
        capture_output=True, timeout=30, check=False,
    )  # fmt: skip
    assert secret not in r.stdout, "filer trả byte của tenant không cần xác thực"


# ---- tải về / xóa mềm / policy chưa quét ------------------------------------------------------


def test_soft_delete_blocks_download_and_upload_link(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, "del.txt", b"to be deleted\n")
        it = intent(c, "pending.txt", "text/plain")
        assert c.delete(f"{F}/{it['file']['id']}").status_code == 200
        assert c.delete(f"{F}/{fid}").status_code == 200
        assert c.get(f"{F}/{fid}/content").status_code == 404
        assert (
            c.post(f"{F}/{fid}/finalize", json={"sha256": sha(b"to be deleted\n")}).status_code
            == 404
        )
        assert fid not in [f["id"] for f in c.get(F).json()["data"]["files"]]
    late = httpx.put(it["put_url"], content=b"late\n", timeout=30)
    assert late.status_code == 403 and late.json()["error"]["details"]["reason"] == (
        "upload_url_invalid"
    )
    # Người duyệt cũng không thấy.
    with http(s, ftoken(s, A, DEPT_HEAD)) as c:
        assert c.get(f"{F}/{fid}").status_code == 404


def test_unscanned_policy_uploader_only(xstack: Stack) -> None:
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, "u.txt", b"unscanned\n")
        assert c.get(f"{F}/{fid}/content").status_code == 200
    for who in (DEPT_HEAD, COLLEAGUE):
        with http(s, ftoken(s, A, who)) as c:
            r = c.get(f"{F}/{fid}/content")
            assert r.status_code in (404, 409), (who, r.status_code)
            assert b"unscanned" not in r.content


def test_head_content_does_not_exhaust_s3_pool(xstack: Stack) -> None:
    """Giả thuyết đã bác: HEAD trên route GET /content (Starlette không lặp body) KHÔNG rò kết nối
    S3 tới mức cạn pool: sau 110 HEAD, GET vẫn chạy. Giữ làm hồi quy."""
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, "h.txt", b"head leak\n")
        for _ in range(110):
            h = c.head(f"{F}/{fid}/content")
            assert h.status_code == 200
    ok = True
    try:
        with httpx.Client(
            base_url=s.app.base,
            timeout=8,
            headers={"Authorization": f"Bearer {ftoken(s, A, REQUESTER)}"},
        ) as c:
            r = c.get(f"{F}/{fid}/content")
            ok = r.status_code == 200 and r.content == b"head leak\n"
    except httpx.TimeoutException:
        ok = False
    assert ok, "sau 110 HEAD, GET /content không còn chạy (pool S3 cạn)"


# ---- DoS lưu trữ: ý định vô hạn, quarantine không được dọn ------------------------------------


def test_abandoned_upload_bytes_are_reclaimed(xstack: Stack) -> None:
    s = xstack
    blob = b"A" * (MAX - 16)
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "abandon.txt", "text/plain")
        assert httpx.put(it["put_url"], content=blob, timeout=60).status_code == 200
    time.sleep(TTL + 2)
    # Không ai chạm vào hàng: job worker `files.maintain` (FilesMaintenance) dọn. Ở đây chạy đúng
    # thân job (worker không chạy trong stack test).
    svc = cast(Any, s.prepared.dispatcher.services.files)
    store = svc.stack.store
    key = f"t/{s.tenants[A].id}/quarantine/{it['file']['id']}"
    assert store.head(key) is not None
    backend = CompositionFilesBackend(svc.rt, svc.stack, FilesPep(svc.rt))
    done = backend.maintain(s.tenants[A])
    assert done["purged"] >= 1, done
    assert store.head(key) is None, "byte quarantine của lượt tải bỏ dở vẫn nằm trên S3"
    with http(s, ftoken(s, A, REQUESTER)) as c:
        st = c.get(f"{F}/{it['file']['id']}").json()["data"]["file"]["status"]
    assert st == "expired"


def test_per_user_pending_intent_quota(xstack: Stack) -> None:
    """FB-02: ý định đang mở có trần theo người (XT_FILES_MAX_PENDING_PER_USER, conftest: 40);
    vượt -> 429 resource_constraint `pending_uploads_quota`. Người khác không bị ảnh hưởng."""
    s = xstack
    cap = int(s.env["XT_FILES_MAX_PENDING_PER_USER"])
    with http(s, ftoken(s, A, DIRECTOR)) as c:
        codes = [c.post(F, json={"name": f"n{i}.pdf"}).status_code for i in range(cap + 5)]
        last = c.post(F, json={"name": "x.pdf"})
    assert codes.count(201) <= cap and 429 in codes, codes
    assert last.json()["error"]["details"]["reason"] == "pending_uploads_quota"
    with http(s, ftoken(s, A, REQUESTER)) as c:
        assert c.post(F, json={"name": "other-user.pdf"}).status_code == 201


# ---- audit ---------------------------------------------------------------------------------------


def test_audit_records_download_and_rejections(xstack: Stack, pg: Any) -> None:
    s = xstack
    with http(s, ftoken(s, A, REQUESTER)) as c:
        it = intent(c, "aud.pdf")
        fid = it["file"]["id"]
        assert httpx.put(it["put_url"], content=b"not a pdf", timeout=30).status_code == 415
        assert httpx.put(it["put_url"], content=PDF, timeout=30).status_code == 200
        assert c.post(f"{F}/{fid}/finalize", json={"sha256": sha(PDF)}).status_code == 200
        assert c.get(f"{F}/{fid}/content").status_code == 200
    with pg.su() as conn:
        acts = [r[0] for r in conn.execute(
            "SELECT action FROM xt_meta.file_event WHERE file_id = %s ORDER BY at, id", (fid,)
        ).fetchall()]  # fmt: skip
        ok = conn.execute("SELECT * FROM xt_audit.verify(%s)", (s.tenants[A].id,)).fetchone()
    assert acts == ["created", "rejected", "uploaded", "finalized", "downloaded"], acts
    assert ok is not None and ok[1] is None


def test_audit_filename_hash_is_keyed(xstack: Stack, pg: Any) -> None:
    s = xstack
    name = "CCCD_079203001234.pdf"
    with http(s, ftoken(s, A, REQUESTER)) as c:
        fid = upload(s, c, name, PDF)
    with pg.su() as conn:
        (diff,) = conn.execute(
            "SELECT diff::text FROM xt_audit.event WHERE model = 'xt_meta.file_object'"
            " AND record_id = %s AND op = 'INSERT'",
            (fid,),
        ).fetchone()
    unkeyed = hashlib.sha256(json.dumps(name).encode()).hexdigest()
    assert unkeyed not in diff, "dò ngược được tên tệp (CCCD) bằng sha256 không khóa"


# ---- audience: token mang aud của bề mặt khác ---------------------------------------------------


def test_files_rejects_token_also_for_data(xstack: Stack) -> None:
    """files kế thừa foreign_audiences của MCP ({xt-workflow, xt-data}): aud kép bị 401."""
    s = xstack
    tok = ftoken(s, A, REQUESTER, aud=["xt-files", "xt-data"])
    with http(s, tok) as c:
        assert c.get(F).status_code == 401


def test_workflow_rejects_token_also_for_files(xstack: Stack) -> None:
    s = xstack
    tok = s.idp.access_token(
        sub(A, REQUESTER), org=A, aud=["xt-workflow", "xt-files"],
        scope="openid xt-workflow xt-files",
    )  # fmt: skip
    ok = s.idp.access_token(
        sub(A, REQUESTER), org=A, aud=["xt-workflow"], scope="openid xt-workflow"
    )
    with httpx.Client(base_url=s.app.base, timeout=30) as c:
        good = c.get("/workflow/v1/inbox", headers={"Authorization": f"Bearer {ok}"})
        # Đối chứng: token workflow thuần qua được xác thực (403 IAM vì tenant không có quy trình).
        assert good.status_code != 401, good.text
        r = c.get("/workflow/v1/inbox", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401, (r.status_code, r.text[:200])


def test_files_rejects_token_also_for_mcp_or_workflow(xstack: Stack) -> None:
    s = xstack
    for extra in ("xt-workflow", f"{s.app.base}/mcp"):
        with http(s, ftoken(s, A, REQUESTER, aud=["xt-files", extra])) as c:
            assert c.get(F).status_code == 401, extra


def test_mcp_rejects_token_also_for_files(xstack: Stack) -> None:
    s = xstack
    mcp = f"{s.app.base}/mcp"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    hdr = {"Accept": "application/json, text/event-stream"}
    tok = s.idp.access_token(sub(A, REQUESTER), org=A, aud=[mcp, "xt-files"],
                             scope="openid mcp-xt xt-files")  # fmt: skip
    ctl = s.idp.access_token(sub(A, REQUESTER), org=A, aud=[mcp, "xt-workflow"],
                             scope="openid mcp-xt xt-workflow")  # fmt: skip
    r_ctl = httpx.post(
        mcp, json=body, headers={**hdr, "Authorization": f"Bearer {ctl}"}, timeout=30
    )
    assert r_ctl.status_code == 401  # đối chứng: aud workflow kèm theo bị chặn đúng
    r = httpx.post(mcp, json=body, headers={**hdr, "Authorization": f"Bearer {tok}"}, timeout=30)
    assert r.status_code == 401, r.status_code
