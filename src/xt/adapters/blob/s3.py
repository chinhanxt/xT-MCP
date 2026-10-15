# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""`FileStore` trên API S3 (SeaweedFS `weed server -s3`), ký AWS Signature V4 bằng httpx.

Không dùng boto3/botocore (~90 MB cài đặt) cho 6 thao tác: PUT (payload ký sha256 + kèm
`x-amz-checksum-sha256`), HEAD, GET luồng, CopyObject, DELETE, CreateBucket. Địa chỉ kiểu path
(`/<bucket>/<key>`), không phụ thuộc DNS ảo theo bucket. Endpoint chỉ trong mạng nội bộ compose:
client bên ngoài không bao giờ thấy SeaweedFS (URL tải lên ký bởi xt-api, xem `docs/files-api.md`).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from typing import BinaryIO
from urllib.parse import quote, urlsplit

import httpx

from xt.application.files.ports import BlobError, BlobInfo

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
CHUNK = 64 * 1024
META_SHA = "x-amz-meta-sha256"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


class S3FileStore:
    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        *,
        region: str = "us-east-1",
        timeout_s: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        u = urlsplit(endpoint)
        if u.scheme not in ("http", "https") or not u.hostname or u.path not in ("", "/"):
            raise ValueError("endpoint S3 phải là http(s)://host[:port] không có path")
        if not bucket or bucket != bucket.lower() or "/" in bucket:
            raise ValueError("tên bucket S3 không hợp lệ")
        self.endpoint = f"{u.scheme}://{u.netloc}"
        self.host = u.netloc
        self.bucket = bucket
        self.region = region
        self._ak = access_key
        self._sk = secret_key
        self._client = client or httpx.Client(timeout=timeout_s)

    def close(self) -> None:
        self._client.close()

    # ---- SigV4 -----------------------------------------------------------------------------

    def _path(self, key: str | None) -> str:
        p = f"/{self.bucket}"
        if key is not None:
            p += "/" + quote(key, safe="/-_.~")
        return p

    def _signed(
        self,
        method: str,
        path: str,
        payload_sha256: str,
        headers: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> dict[str, str]:
        t = (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
        day = t[:8]
        hdrs = {k.lower(): v.strip() for k, v in (headers or {}).items()}
        hdrs["host"] = self.host
        hdrs["x-amz-date"] = t
        hdrs["x-amz-content-sha256"] = payload_sha256
        signed = sorted(k for k in hdrs if k == "host" or k.startswith("x-amz-"))
        canonical = "\n".join(
            [
                method,
                path,
                "",
                "".join(f"{k}:{hdrs[k]}\n" for k in signed),
                ";".join(signed),
                payload_sha256,
            ]
        )
        scope = f"{day}/{self.region}/s3/aws4_request"
        to_sign = "\n".join(
            ["AWS4-HMAC-SHA256", t, scope, hashlib.sha256(canonical.encode()).hexdigest()]
        )
        k = _hmac(("AWS4" + self._sk).encode(), day)
        for part in (self.region, "s3", "aws4_request"):
            k = _hmac(k, part)
        sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
        hdrs["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._ak}/{scope}, "
            f"SignedHeaders={';'.join(signed)}, Signature={sig}"
        )
        hdrs.pop("host")
        return hdrs

    def _send(
        self,
        method: str,
        key: str | None,
        *,
        payload_sha256: str = EMPTY_SHA256,
        headers: Mapping[str, str] | None = None,
        content: bytes | Iterator[bytes] | None = None,
    ) -> httpx.Response:
        path = self._path(key)
        try:
            return self._client.request(
                method,
                self.endpoint + path,
                headers=self._signed(method, path, payload_sha256, headers),
                content=content,
            )
        except httpx.HTTPError as e:
            raise BlobError(f"S3 {method}: {type(e).__name__}") from None

    @staticmethod
    def _fail(r: httpx.Response, op: str) -> BlobError:
        code = ""
        if "<Code>" in r.text:
            code = r.text.split("<Code>", 1)[1].split("</Code>", 1)[0][:64]
        return BlobError(f"S3 {op}: HTTP {r.status_code} {code}".strip())

    # ---- FileStore -------------------------------------------------------------------------

    def ensure_bucket(self) -> None:
        r = self._send("PUT", None)
        if r.status_code not in (200, 409):
            raise self._fail(r, "CreateBucket")

    def put(self, key: str, data: BinaryIO, size: int, sha256: str, content_type: str) -> None:
        digest = base64.b64encode(bytes.fromhex(sha256)).decode()

        def body() -> Iterator[bytes]:
            left = size
            while left > 0:
                chunk = data.read(min(CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

        r = self._send(
            "PUT",
            key,
            payload_sha256=sha256,
            headers={
                "content-type": content_type,
                "content-length": str(size),
                "x-amz-checksum-sha256": digest,
                META_SHA: sha256,
            },
            content=body(),
        )
        if r.status_code != 200:
            raise self._fail(r, "PutObject")

    def head(self, key: str) -> BlobInfo | None:
        r = self._send("HEAD", key)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise self._fail(r, "HeadObject")
        return BlobInfo(int(r.headers.get("content-length", "0")), r.headers.get(META_SHA))

    def stream(self, key: str) -> Iterator[bytes]:
        path = self._path(key)
        req = self._client.build_request(
            "GET", self.endpoint + path, headers=self._signed("GET", path, EMPTY_SHA256)
        )
        try:
            r = self._client.send(req, stream=True)
        except httpx.HTTPError as e:
            raise BlobError(f"S3 GET: {type(e).__name__}") from None
        if r.status_code != 200:
            r.read()
            r.close()
            raise self._fail(r, "GetObject")
        return self._chunks(r)

    @staticmethod
    def _chunks(r: httpx.Response) -> Iterator[bytes]:
        try:
            yield from r.iter_bytes(CHUNK)
        except httpx.HTTPError as e:
            raise BlobError(f"S3 GET: {type(e).__name__}") from None
        finally:
            r.close()

    def copy(self, src: str, dst: str) -> None:
        r = self._send("PUT", dst, headers={"x-amz-copy-source": self._path(src)})
        if r.status_code != 200 or "<Error>" in r.text:
            raise self._fail(r, "CopyObject")

    def delete(self, key: str) -> None:
        r = self._send("DELETE", key)
        if r.status_code not in (200, 204, 404):
            raise self._fail(r, "DeleteObject")
