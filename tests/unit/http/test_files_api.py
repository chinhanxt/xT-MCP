# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""REST `/files/v1` (adapter) với dispatcher, danh tính và backend giả: định tuyến, mã HTTP theo
lý do lỗi, Content-Digest, giới hạn kích thước, trang tải lên, OpenAPI."""

from __future__ import annotations

import base64
import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

import httpx2
import pytest
from jsonschema import Draft202012Validator
from starlette.applications import Starlette

from xt.adapters.http.files_api import (
    PAGE_CSP,
    Download,
    UploadInfo,
    content_disposition,
    files_routes,
    openapi_document,
)
from xt.adapters.http.workflow_api import AuthChallenge
from xt.application.files import Spool
from xt.application.tools import CallContext, ToolOutcome
from xt.application.tools.errors import invalid_request, not_found
from xt.domain.identity import IdentityError, InvalidTokenError, Principal, PrincipalKind

BASE = "https://xt-api.example.test"
FID = uuid.uuid4()
PDF = b"%PDF-1.7 test"


class Auth:
    def bearer(self, authorization: str | None) -> str | None:
        if authorization is None:
            return None
        return authorization.removeprefix("Bearer ")

    def verify(self, token: str) -> Principal:
        if token != "good":
            raise InvalidTokenError("audience")
        return Principal(
            subject="sub-1",
            kind=PrincipalKind.AGENT,
            client_id="xt-cli",
            on_behalf_of="sub-1",
            issuer="https://idp.test",
            username=None,
            organizations=("acme",),
            roles=frozenset(),
            scopes=frozenset({"xt-files"}),
            auth_time=None,
            acr=None,
            amr=(),
            token_id=None,
            issued_at=1,
            expires_at=2,
        )

    def challenge(self, error: IdentityError | None) -> AuthChallenge:
        return AuthChallenge(401, {"WWW-Authenticate": 'Bearer realm="xt-files"'})


class Dispatcher:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        ctx: CallContext,
        *,
        duplicate_keys: Sequence[str] = (),
    ) -> ToolOutcome:
        assert name == "upload_file" and arguments is not None
        self.calls.append(arguments)
        if arguments["name"].endswith(".html"):
            return ToolOutcome(True, {"ok": False, "error": {
                "code": "invalid_request", "message": "x",
                "details": {"reason": "content_type_not_allowed"}}})  # fmt: skip
        data = {
            "file": {"ref": f"file:{FID}", "name": arguments["name"], "status": "awaiting_upload"},
            "upload_url": f"{BASE}/files/v1/uploads/tok",
            "put_url": f"{BASE}/files/v1/uploads/tok",
            "expires_at": "2026-11-02T09:15:00Z",
        }
        return ToolOutcome(False, {"ok": True, "untrusted": True, "data": data,
                                   "meta": {"trace_id": ctx.trace_id}})  # fmt: skip


def _view(status: str = "uploaded", sha: str | None = None) -> dict[str, Any]:
    return {"id": str(FID), "ref": f"file:{FID}", "name": "a.pdf", "status": status,
            "declared_type": "application/pdf", "sha256": sha}  # fmt: skip


class Backend:
    max_bytes = 64

    def __init__(self) -> None:
        self.ingested: list[tuple[bytes, str | None]] = []

    def upload_info(self, token: str, trace_id: str) -> UploadInfo:
        if token != "tok":
            raise invalid_request("bad", "upload_url_invalid")
        return UploadInfo("<b>a</b>.pdf", "application/pdf", "awaiting_upload", None, 64)

    def ingest(self, token: str, spool: Spool, trace_id: str, sha256: str | None) -> dict[str, Any]:
        self.ingested.append((spool.rewind().read(), sha256))
        if not spool.head.startswith(b"%PDF"):
            raise invalid_request("mismatch", "content_type_mismatch")
        if sha256 is not None and sha256 != spool.sha256:
            raise invalid_request("bad sha", "checksum_mismatch")
        return _view("not_scanned" if sha256 else "uploaded", spool.sha256)

    def finalize(self, ctx: CallContext, file_id: uuid.UUID, sha256: str) -> dict[str, Any]:
        return _view("not_scanned", sha256)

    def metadata(self, ctx: CallContext, file_id: uuid.UUID) -> dict[str, Any]:
        if file_id != FID:
            raise not_found()
        return _view()

    def list_mine(self, ctx: CallContext, limit: int) -> list[dict[str, Any]]:
        return [_view()][:limit]

    def content(self, ctx: CallContext, file_id: uuid.UUID) -> Download:
        return Download("Hóa đơn.pdf", "application/pdf", len(PDF),
                        hashlib.sha256(PDF).hexdigest(), iter([PDF[:4], PDF[4:]]))  # fmt: skip

    def delete(self, ctx: CallContext, file_id: uuid.UUID) -> dict[str, Any]:
        return {**_view(), "deleted": True}


@pytest.fixture
def env() -> tuple[httpx2.AsyncClient, Dispatcher, Backend]:
    d, b = Dispatcher(), Backend()
    app = Starlette(routes=[files_routes(d, Auth(), b, server_url=BASE)])
    client = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=BASE)
    return client, d, b


H = {"Authorization": "Bearer good"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_auth_and_routing(env: Any) -> None:
    c, _, _ = env
    async with c:
        anon = await c.get("/files/v1")
        bad = await c.get("/files/v1", headers={"Authorization": "Bearer nope"})
        doc = await c.get("/files/v1/openapi.json")
        missing = await c.get(f"/files/v1/{uuid.uuid4()}", headers=H)
        not_uuid = await c.get("/files/v1/abc", headers=H)
        unknown = await c.get("/files/v1/a/b/c/d", headers=H)
        listed = await c.get("/files/v1?limit=5", headers=H)
        badq = await c.get("/files/v1?foo=1", headers=H)
    assert anon.status_code == 401 and bad.status_code == 401
    assert anon.json()["error"]["code"] == "unauthenticated"
    assert doc.status_code == 200 and doc.json()["openapi"] == "3.1.0"
    assert missing.status_code == not_uuid.status_code == unknown.status_code == 404
    assert missing.json()["error"]["message"] == not_uuid.json()["error"]["message"]
    assert listed.status_code == 200 and listed.json()["data"]["files"][0]["links"]["content"]
    assert badq.status_code == 400


@pytest.mark.anyio
async def test_create_intent_and_status_mapping(env: Any) -> None:
    c, d, _ = env
    async with c:
        r = await c.post("/files/v1", json={"name": "a.pdf"}, headers=H)
        html = await c.post("/files/v1", json={"name": "x.html"}, headers=H)
        extra = await c.post("/files/v1", json={"name": "a.pdf", "approved": True}, headers=H)
        plain = await c.post("/files/v1", content=b"x", headers={**H, "Content-Type": "text/plain"})
        badkey = await c.post("/files/v1", json={"name": "a.pdf"},
                              headers={**H, "Idempotency-Key": "bad key!"})  # fmt: skip
    assert r.status_code == 201 and r.headers["location"] == f"/files/v1/{FID}"
    assert r.json()["data"]["put_url"].endswith("/uploads/tok")
    assert d.calls[0]["source"] == "upload_link" and d.calls[0]["idempotency_key"]
    assert html.status_code == 415
    assert extra.status_code == 400 and plain.status_code == 400 and badkey.status_code == 400


@pytest.mark.anyio
async def test_presigned_put_digest_and_limits(env: Any) -> None:
    c, _, b = env
    dig = "sha-256=:" + base64.b64encode(hashlib.sha256(PDF).digest()).decode() + ":"
    async with c:
        ok = await c.put("/files/v1/uploads/tok", content=PDF)
        fin = await c.put("/files/v1/uploads/tok", content=PDF, headers={"Content-Digest": dig})
        wrong = await c.put("/files/v1/uploads/tok", content=PDF,
                            headers={"Content-Digest": "sha-256=:" + "A" * 43 + "=:"})  # fmt: skip
        junk = await c.put("/files/v1/uploads/tok", content=PDF,
                           headers={"Content-Digest": "md5=:abc:"})  # fmt: skip
        mismatch = await c.put("/files/v1/uploads/tok", content=b"text only")
        big = await c.put("/files/v1/uploads/tok", content=b"%PDF" + b"0" * 100)
        badtok = await c.put("/files/v1/uploads/zzz", content=PDF)
        page = await c.get("/files/v1/uploads/tok")
    assert ok.status_code == 200 and ok.json()["data"]["file"]["status"] == "uploaded"
    assert fin.status_code == 200 and fin.json()["data"]["file"]["status"] == "not_scanned"
    assert wrong.status_code == 422 and junk.status_code == 400
    assert mismatch.status_code == 415
    assert big.status_code == 413 and big.json()["error"]["details"]["limit_bytes"] == 64
    assert badtok.status_code == 400
    assert len(b.ingested) == 4  # quá cỡ / link sai: không tới backend
    assert page.status_code == 200 and page.headers["content-security-policy"] == PAGE_CSP
    assert "&lt;b&gt;a&lt;/b&gt;.pdf" in page.text and "<b>a</b>" not in page.text


@pytest.mark.anyio
async def test_multipart_download_delete(env: Any) -> None:
    c, d, b = env
    async with c:
        mp = await c.post("/files/v1", files={"file": ("a.pdf", PDF, "application/pdf")},
                          headers=H)  # fmt: skip
        nofile = await c.post("/files/v1", data={"x": "1"}, files={"y": ("", b"", "text/plain")},
                              headers=H)  # fmt: skip
        dl = await c.get(f"/files/v1/{FID}/content", headers=H)
        rm = await c.delete(f"/files/v1/{FID}", headers=H)
        fin = await c.post(f"/files/v1/{FID}/finalize", json={"sha256": "a" * 64}, headers=H)
        badfin = await c.post(f"/files/v1/{FID}/finalize", json={"sha256": "xyz"}, headers=H)
    assert mp.status_code == 201, mp.text
    assert b.ingested[-1] == (PDF, hashlib.sha256(PDF).hexdigest())
    assert d.calls[-1]["content_type"] == "application/pdf"
    assert nofile.status_code == 400
    assert dl.status_code == 200 and dl.content == PDF
    assert dl.headers["etag"] == f'"sha256-{hashlib.sha256(PDF).hexdigest()}"'
    assert "sandbox" in dl.headers["content-security-policy"]
    assert rm.status_code == 200 and rm.json()["data"]["file"]["deleted"] is True
    assert fin.status_code == 200 and badfin.status_code == 400


def test_openapi_is_valid_31_and_disposition_safe() -> None:
    doc = openapi_document(BASE, 1024)
    assert doc["openapi"] == "3.1.0" and doc["servers"] == [{"url": BASE}]
    for path in doc["paths"].values():
        for op in path.values():
            for resp in op["responses"].values():
                for media in (resp.get("content") or {}).values():
                    if "$ref" not in media["schema"]:
                        Draft202012Validator.check_schema(media["schema"])
    cd = content_disposition('a"\r\nX: y.pdf')
    assert "\r" not in cd and "\n" not in cd and 'filename="a___X: y.pdf"' in cd
