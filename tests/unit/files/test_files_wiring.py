# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Cấu hình dịch vụ tệp, che tên tệp theo tier PII, client S3 (SigV4) trên transport giả."""

from __future__ import annotations

import hashlib
import io
from pathlib import Path

import httpx
import pytest

from xt.adapters.blob.s3 import S3FileStore
from xt.application.files import BlobError
from xt.entrypoints.files_http import FilesConfig, FilesConfigError, mask_name


def _env(tmp_path: Path, **over: str) -> dict[str, str]:
    key = tmp_path / "cursor.key"
    key.write_bytes(b"c" * 40)
    env = {
        "XT_FILES_S3_ENDPOINT": "http://seaweedfs:8333",
        "XT_FILES_S3_ACCESS_KEY": "xt",
        "XT_FILES_S3_SECRET_KEY": "s" * 24,
        "XT_FILES_PUBLIC_URL": "https://xt-api.example.test",
        "XT_CURSOR_KEY_FILE": str(key),
    }
    env.update(over)
    return env


def test_config_defaults_and_derived_url_key(tmp_path: Path) -> None:
    cfg = FilesConfig.from_env(_env(tmp_path))
    assert cfg.bucket == "xt-files" and cfg.clamd is None and not cfg.policy.scanning
    assert cfg.policy.unscanned_download == "uploader"
    assert cfg.url_key != b"c" * 40 and len(cfg.url_key) == 32  # nhãn riêng, không dùng lại khóa
    full = FilesConfig.from_env(_env(tmp_path, XT_FILES_CLAMD="clamav:3310"))
    assert full.clamd == ("clamav", 3310) and full.policy.scanning
    assert not FilesConfig.enabled({}) and FilesConfig.enabled(_env(tmp_path))


def test_config_errors(tmp_path: Path) -> None:
    with pytest.raises(FilesConfigError) as e:
        FilesConfig.from_env(
            _env(
                tmp_path,
                XT_FILES_S3_SECRET_KEY="short",
                XT_FILES_PUBLIC_URL="http://public.example",
                XT_FILES_UNSCANNED_DOWNLOAD="maybe",
                XT_FILES_CLAMD="clamav",
                XT_FILES_ALLOWED_TYPES="text/html",
                XT_FILES_MAX_BYTES="12",
            )
        )
    text = "; ".join(e.value.problems)
    for name in ("SECRET_KEY", "PUBLIC_URL", "UNSCANNED", "CLAMD", "ALLOWED_TYPES", "MAX_BYTES"):
        assert name in text, name


def test_mask_name() -> None:
    assert mask_name("CCCD 079123456789.pdf", "full") == "CCCD 079123456789.pdf"
    assert mask_name("CCCD 079123456789.pdf", "masked") == "CCCD [PII].pdf"
    assert mask_name("hop-dong-2026.pdf", "masked") == "hop-dong-2026.pdf"


def test_s3_client_signs_and_maps_errors() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if req.method == "HEAD":
            return httpx.Response(404)
        if req.url.path.endswith("/boom"):
            return httpx.Response(500, text="<Error><Code>InternalError</Code></Error>")
        return httpx.Response(200)

    st = S3FileStore(
        "http://s3.test:8333", "xt-files", "AK", "SK" * 10,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )  # fmt: skip
    data = b"%PDF-1.7"
    st.put("t/a/b c", io.BytesIO(data), len(data), hashlib.sha256(data).hexdigest(), "x/y")
    req = seen[-1]
    assert req.url.raw_path == b"/xt-files/t/a/b%20c"
    auth = req.headers["authorization"]
    assert (
        auth.startswith("AWS4-HMAC-SHA256 Credential=AK/") and "/us-east-1/s3/aws4_request" in auth
    )
    assert "SignedHeaders=host;x-amz-checksum-sha256;x-amz-content-sha256;x-amz-date;" in auth
    assert req.headers["x-amz-content-sha256"] == hashlib.sha256(data).hexdigest()
    assert req.content == data
    assert st.head("missing") is None
    with pytest.raises(BlobError, match="InternalError"):
        st.copy("a", "boom")
    with pytest.raises(ValueError):
        S3FileStore("ftp://x", "b", "a", "s")
