# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""REST `/files/v1`: dịch vụ dữ liệu phi cấu trúc của Tầng 1 (SeaweedFS S3 phía sau xt-api).

| Endpoint | Việc |
|---|---|
| `POST /files/v1` (JSON) | ý định = tool `upload_file(source=upload_link)` -> `put_url` hạn ngắn |
| `POST /files/v1` (multipart) | một bước: ý định + byte + finalize (sha256 do server tính) |
| `PUT /files/v1/uploads/{token}` | presigned PUT (không Bearer): đếm, băm, nhận dạng magic byte |
| `GET /files/v1/uploads/{token}` | trang tải lên cho người (link `upload_url` của `upload_file`) |
| `POST /files/v1/{id}/finalize` | so sha256 của client với byte đã nhận -> quét / sẵn sàng |
| `GET /files/v1`, `GET /files/v1/{id}` | danh sách tệp của tôi / metadata (IAM `file.read`) |
| `GET /files/v1/{id}/content` | tải về dạng luồng (IAM `file.read` + policy quét) |
| `DELETE /files/v1/{id}` | xóa mềm (IAM `file.delete`) |

Danh tính, audience riêng, envelope lỗi, lưới PII của lỗi: như `/workflow/v1`
(`workflow_api.py`). Tệp tenant khác, hoặc cùng tenant nhưng không được đọc: 404 giống hệt id
không tồn tại. Byte không bao giờ đi qua LLM: agent chỉ nhận link. Tài liệu: `docs/files-api.md`.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import html
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol
from urllib.parse import quote

import anyio.to_thread
from jsonschema import Draft202012Validator
from starlette.datastructures import UploadFile
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Mount, Route

from xt import __version__
from xt.adapters.http.workflow_api import (
    HTTP_STATUS,
    ApiError,
    AuthChallenge,
    RevocationCheck,
    ToolCaller,
    WorkflowAuth,
    error_envelope,
)
from xt.application.files import Spool
from xt.application.tools import Actor, CallContext, ClientClass, ToolError, ToolOutcome
from xt.application.tools.safe_log import log_failure
from xt.contracts.tools import (
    BY_NAME,
    CONTRACT_VERSION,
    ERROR_ENVELOPE,
    ErrorCode,
    find_duplicate_keys,
)
from xt.domain.identity import (
    IdentityError,
    IdentityProviderUnavailableError,
    InsufficientScopeError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
)

log = logging.getLogger("xt.files_api")

MOUNT = "/files"
API_PREFIX = "/files/v1"
MAX_JSON_BYTES = 4096
MULTIPART_OVERHEAD = 64 * 1024
IDEMPOTENCY_HEADER = "Idempotency-Key"
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,128}$")
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
# RFC 9530: `Content-Digest: sha-256=:<base64>:` trên PUT -> finalize luôn trong cùng request.
CONTENT_DIGEST = re.compile(r"(?:^|,)\s*sha-256=:([A-Za-z0-9+/]{43}=):\s*(?:,|$)")
# Mã lỗi có lý do riêng -> HTTP chính xác hơn bảng chung.
REASON_STATUS: Mapping[str, int] = {
    "content_type_not_allowed": 415,
    "content_type_mismatch": 415,
    "content_type_required": 415,
    "checksum_mismatch": 422,
    "length_required": 411,
}

FilesAuth = WorkflowAuth  # cùng hình dạng: bearer / verify / challenge (realm riêng)


# ---- cổng (composition root nối vào) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Download:
    name: str
    content_type: str
    size: int
    sha256: str
    chunks: Iterator[bytes]


@dataclass(frozen=True, slots=True)
class UploadInfo:
    name: str
    declared_type: str
    status: str
    expires_at: str | None
    max_bytes: int


class FilesBackend(Protocol):
    """Phần không phải tool: chạy trong transaction RLS của tenant; lỗi = `ToolError`.
    Không thấy / không được đọc -> `not_found` (cùng một câu trả lời)."""

    max_bytes: int

    def upload_info(self, token: str, trace_id: str) -> UploadInfo: ...

    def ingest(
        self, token: str, spool: Spool, trace_id: str, sha256: str | None
    ) -> dict[str, Any]: ...

    def finalize(self, ctx: CallContext, file_id: uuid.UUID, sha256: str) -> dict[str, Any]: ...

    def metadata(self, ctx: CallContext, file_id: uuid.UUID) -> dict[str, Any]: ...

    def list_mine(self, ctx: CallContext, limit: int) -> list[dict[str, Any]]: ...

    def content(self, ctx: CallContext, file_id: uuid.UUID) -> Download: ...

    def delete(self, ctx: CallContext, file_id: uuid.UUID) -> dict[str, Any]: ...


def _cloud_unless_human(principal: Principal) -> ClientClass:
    return ClientClass.HUMAN if principal.kind is PrincipalKind.HUMAN else ClientClass.CLOUD


# ---- envelope ------------------------------------------------------------------------------


def _json(
    status: int, body: Mapping[str, Any], headers: Mapping[str, str] | None = None
) -> Response:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = {"Cache-Control": "no-store", **(headers or {})}
    return Response(raw, status_code=status, media_type="application/json", headers=hdrs)


def _ok(trace_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "ok": True,
        "untrusted": True,
        "data": dict(data),
        "meta": {"contract_version": CONTRACT_VERSION, "trace_id": trace_id},
    }


def status_for(code: ErrorCode, details: Mapping[str, Any] | None) -> int:
    reason = (details or {}).get("reason")
    if isinstance(reason, str) and reason in REASON_STATUS:
        return REASON_STATUS[reason]
    return HTTP_STATUS.get(code, 500)


def _error_response(
    trace_id: str, error: ToolError, headers: Mapping[str, str] | None = None
) -> Response:
    return _json(status_for(error.code, error.details), error_envelope(trace_id, error), headers)


def outcome_response(outcome: ToolOutcome, success_status: int = 200) -> Response:
    env = outcome.envelope
    if not outcome.is_error:
        return _json(success_status, env)
    error = env.get("error") or {}
    code = ErrorCode(error.get("code", ErrorCode.INTERNAL_ERROR.value))
    extra: dict[str, str] = {}
    secs = (error.get("details") or {}).get("retry_after_seconds")
    if isinstance(secs, int | float):
        extra["Retry-After"] = str(int(secs))
    return _json(status_for(code, error.get("details")), env, extra)


def _invalid(message: str, reason: str, remediation: str, **details: Any) -> ApiError:
    return ApiError(
        ToolError(
            ErrorCode.INVALID_REQUEST,
            message,
            remediation=remediation,
            details={"reason": reason, **details},
        )
    )


def _not_found() -> ApiError:
    return ApiError(
        ToolError(ErrorCode.NOT_FOUND, "Not found, or you are not allowed to know it exists.")
    )


# ---- schema (OpenAPI và kiểm body từ cùng một nguồn) --------------------------------------


def _tool_prop(name: str) -> dict[str, Any]:
    prop: dict[str, Any] = dict(BY_NAME["upload_file"].input_schema["properties"][name])
    return prop


CREATE_BODY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name"],
    "properties": {
        "name": _tool_prop("name"),
        "content_type": _tool_prop("content_type"),
    },
}
FINALIZE_BODY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sha256"],
    "properties": {
        "sha256": {
            "type": "string",
            "pattern": SHA256_HEX.pattern,
            "description": "sha256 (hex thường) của đúng các byte đã PUT.",
        }
    },
}
_VALIDATORS = {
    "create": Draft202012Validator(CREATE_BODY),
    "finalize": Draft202012Validator(FINALIZE_BODY),
}
FILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["id", "ref", "name", "declared_type", "status", "created_at", "created_by"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "ref": {"type": "string", "description": "Giá trị đặt vào field kiểu `file` của bản ghi."},
        "name": {"type": "string"},
        "declared_type": {"type": "string"},
        "content_type": {"type": ["string", "null"]},
        "size_bytes": {"type": ["integer", "null"]},
        "sha256": {"type": ["string", "null"]},
        "status": {
            "enum": [
                "awaiting_upload",
                "uploaded",
                "scanning",
                "clean",
                "not_scanned",
                "infected",
                "expired",
            ]
        },
        "created_at": {"type": "string", "format": "date-time"},
        "created_by": {"type": "string"},
        "upload_expires_at": {"type": ["string", "null"]},
        "finalized_at": {"type": ["string", "null"]},
        "scan": {"type": "object"},
        "links": {"type": "object", "additionalProperties": {"type": "string"}},
    },
}


def _env(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["ok", "untrusted", "data", "meta"],
        "properties": {"ok": {"const": True}, "untrusted": {"type": "boolean"}, "data": data},
    }


FILE_ENV = _env({"type": "object", "required": ["file"], "properties": {"file": FILE_SCHEMA}})
INTENT_ENV = _env(
    {
        "type": "object",
        "required": ["file", "put_url", "expires_at"],
        "properties": {
            "file": FILE_SCHEMA,
            "put_url": {"type": "string", "description": "PUT byte tới đây (không Bearer)."},
            "upload_url": {"type": "string", "description": "Trang tải lên cho người."},
            "expires_at": {"type": "string"},
        },
    }
)
LIST_ENV = _env(
    {
        "type": "object",
        "required": ["files"],
        "properties": {"files": {"type": "array", "items": FILE_SCHEMA}},
    }
)
_ERR = "Envelope lỗi chung (`ok:false`)."


def openapi_document(server_url: str | None, max_bytes: int) -> dict[str, Any]:
    err = {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}

    def r(status: int, desc: str, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
        content = {"application/json": {"schema": dict(schema)}} if schema is not None else err
        return {str(status): {"description": desc, "content": content}}

    common = {
        **r(400, _ERR),
        **r(401, "Thiếu/sai token (WWW-Authenticate: Bearer)."),
        **r(403, _ERR),
        **r(404, "Không có, hoặc không được biết là có (tenant khác)."),
        **r(500, _ERR),
        **r(503, _ERR),
    }
    fid = {
        "name": "file_id",
        "in": "path",
        "required": True,
        "schema": {"type": "string", "format": "uuid"},
    }
    token = {"name": "token", "in": "path", "required": True, "schema": {"type": "string"}}
    idem = {
        "name": IDEMPOTENCY_HEADER,
        "in": "header",
        "required": False,
        "schema": {"type": "string", "pattern": IDEMPOTENCY_KEY.pattern},
        "description": "Tùy chọn: gửi lại đúng khóa khi thử lại để không tạo tệp mới.",
    }
    multipart = {
        "type": "object",
        "required": ["file"],
        "properties": {
            "file": {"type": "string", "contentMediaType": "application/octet-stream"},
            "sha256": FINALIZE_BODY["properties"]["sha256"],
        },
    }
    paths: dict[str, Any] = {
        API_PREFIX: {
            "get": {
                "operationId": "list_files",
                "summary": "Tệp tôi đã tải lên (mới nhất trước).",
                "parameters": [{"name": "limit", "in": "query",
                                "schema": {"type": "integer", "minimum": 1, "maximum": 100}}],
                "responses": {**r(200, "Danh sách.", LIST_ENV), **common},
            },
            "post": {
                "operationId": "create_file",
                "summary": "JSON: tạo ý định tải lên (put_url hạn ngắn). multipart/form-data: tải "
                "lên một bước (finalize ngay).",
                "x-xt-tools": ["upload_file"],
                "parameters": [idem],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": CREATE_BODY},
                        "multipart/form-data": {"schema": multipart},
                    },
                },
                "responses": {
                    **r(201, "JSON: ý định (put_url). multipart: tệp đã finalize.", INTENT_ENV),
                    **r(409, _ERR),
                    **r(411, "Thiếu Content-Length (multipart)."),
                    **r(413, f"Vượt hạn mức {max_bytes} byte (resource_constraint)."),
                    **r(415, "Kiểu tệp không được phép hoặc byte không khớp kiểu khai báo."),
                    **r(422, "sha256 không khớp (checksum_mismatch)."),
                    **common,
                },
            },
        },
        API_PREFIX + "/uploads/{token}": {
            "put": {
                "operationId": "put_file_bytes",
                "summary": "Presigned PUT: byte của tệp (thân request nguyên văn). Kèm "
                "`Content-Digest: sha-256=:<base64>:` (RFC 9530) để finalize luôn.",
                "security": [],
                "parameters": [token],
                "requestBody": {"required": True, "content": {"*/*": {"schema": {
                    "type": "string", "contentMediaType": "application/octet-stream"}}}},
                "responses": {
                    **r(200, "Đã nhận (uploaded) hoặc đã finalize nếu có Content-Digest.",
                        FILE_ENV),
                    **r(403, "Link sai chữ ký (upload_url_invalid) hoặc hết hạn "
                        "(upload_url_expired)."),
                    **r(409, "Link đã dùng (already_uploaded)."),
                    **r(411, "Thiếu Content-Length."),
                    **r(413, f"Vượt hạn mức {max_bytes} byte."),
                    **r(415, "Kiểu tệp không được phép / không khớp magic byte."),
                    **r(422, "Content-Digest không khớp."),
                },
            },
            "get": {
                "operationId": "upload_page",
                "summary": "Trang HTML để người chọn tệp và tải lên (link upload_url).",
                "security": [],
                "parameters": [token],
                "responses": {"200": {"description": "HTML.",
                                      "content": {"text/html": {"schema": {"type": "string"}}}},
                              **r(403, _ERR)},
            },
        },
        API_PREFIX + "/{file_id}": {
            "get": {
                "operationId": "get_file",
                "summary": "Metadata tệp.",
                "parameters": [fid],
                "responses": {**r(200, "Metadata.", FILE_ENV), **common},
            },
            "delete": {
                "operationId": "delete_file",
                "summary": "Xóa mềm (IAM file.delete).",
                "parameters": [fid],
                "responses": {**r(200, "Đã xóa mềm.", FILE_ENV), **common},
            },
        },
        API_PREFIX + "/{file_id}/finalize": {
            "post": {
                "operationId": "finalize_file",
                "summary": "So sha256 với byte đã nhận; khớp -> quét (full) / not_scanned.",
                "parameters": [fid],
                "requestBody": {"required": True,
                                "content": {"application/json": {"schema": FINALIZE_BODY}}},
                "responses": {**r(200, "Đã finalize.", FILE_ENV), **r(409, _ERR),
                              **r(422, "sha256 không khớp; byte đã bỏ, PUT lại."), **common},
            }
        },
        API_PREFIX + "/{file_id}/content": {
            "get": {
                "operationId": "get_file_content",
                "summary": "Tải về (luồng). Tệp chưa quét: theo XT_FILES_UNSCANNED_DOWNLOAD.",
                "parameters": [fid],
                "responses": {
                    "200": {"description": "Byte của tệp (ETag = sha256).",
                            "content": {"application/octet-stream": {"schema": {
                                "type": "string", "contentMediaType": "application/octet-stream"
                            }}}},
                    **r(409, "Chưa sẵn sàng / đang quét / nhiễm / chưa quét (file_not_scanned)."),
                    **common,
                },
            }
        },
        API_PREFIX + "/openapi.json": {
            "get": {"operationId": "openapi", "summary": "Tài liệu này.", "security": [],
                    "responses": {"200": {"description": "OpenAPI 3.1."}}}
        },
    }  # fmt: skip
    doc: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "xT Files API",
            "version": __version__,
            "description": "Dữ liệu phi cấu trúc của xT (Tầng 1): tệp trên SeaweedFS (S3) sau "
            "xt-api, metadata Postgres RLS + audit hash-chain, ClamAV ở profile full. Contract "
            f"tool {CONTRACT_VERSION}.",
            "license": {"name": "Apache-2.0", "identifier": "Apache-2.0"},
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Access token Keycloak, aud = audience của files (xt-files).",
                }
            },
            "schemas": {"Error": _strip(ERROR_ENVELOPE)},
        },
        "security": [{"bearer": []}],
    }
    if server_url:
        doc["servers"] = [{"url": server_url}]
    return doc


def _strip(schema: Any) -> Any:
    if isinstance(schema, Mapping):
        return {k: _strip(v) for k, v in schema.items() if k != "$schema"}
    if isinstance(schema, list):
        return [_strip(v) for v in schema]
    return schema


# ---- trang tải lên cho người ----------------------------------------------------------------

_PAGE_SCRIPT = """
const f = document.getElementById('f'), out = document.getElementById('out');
const max = Number(document.body.dataset.max), type = document.body.dataset.type;
f.addEventListener('change', async () => {
  const file = f.files[0]; if (!file) return;
  if (file.size > max) { out.textContent = 'Tệp vượt hạn mức ' + max + ' byte.'; return; }
  out.textContent = 'Đang tính sha256…';
  const buf = await file.arrayBuffer();
  const d = new Uint8Array(await crypto.subtle.digest('SHA-256', buf));
  const b64 = btoa(String.fromCharCode(...d));
  out.textContent = 'Đang tải lên…';
  const r = await fetch(location.href, {method: 'PUT', body: buf,
    headers: {'Content-Type': type, 'Content-Digest': 'sha-256=:' + b64 + ':'}});
  const j = await r.json().catch(() => ({}));
  out.textContent = r.ok ? 'Đã tải lên: ' + j.data.file.ref + ' (' + j.data.file.status + ')'
    : 'Lỗi: ' + ((j.error && j.error.message) || r.status);
  if (r.ok) f.disabled = true;
});
"""
_PAGE_STYLE = "body{font:16px system-ui,sans-serif;max-width:40rem;margin:3rem auto;padding:0 1rem}"


def _csp_hash(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


PAGE_CSP = (
    f"default-src 'none'; script-src {_csp_hash(_PAGE_SCRIPT)}; style-src "
    f"{_csp_hash(_PAGE_STYLE)}; connect-src 'self'; form-action 'none'; base-uri 'none'; "
    "frame-ancestors 'none'"
)


def upload_page(info: UploadInfo) -> bytes:
    e = html.escape
    return (
        "<!doctype html><html lang=vi><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width'><meta name=referrer content=no-referrer>"
        f"<title>xT - Tải tệp lên</title><style>{_PAGE_STYLE}</style>"
        f"<body data-max='{info.max_bytes}' data-type='{e(info.declared_type)}'>"
        f"<h1>Tải tệp lên xT</h1><p>Tệp: <b>{e(info.name)}</b> ({e(info.declared_type)})</p>"
        f"<p>Link hết hạn: {e(info.expires_at or '')}</p>"
        "<input id=f type=file><p id=out role=status></p>"
        f"<script>{_PAGE_SCRIPT}</script></body></html>"
    ).encode()


# ---- app -----------------------------------------------------------------------------------


Handler = Callable[[Request, CallContext], Awaitable[Response]]


def _trace() -> str:
    return "tr_" + uuid.uuid4().hex[:20]


def _file_id(request: Request) -> uuid.UUID:
    try:
        return uuid.UUID(str(request.path_params["file_id"]))
    except ValueError:
        raise _not_found() from None


def _links(file_id: str) -> dict[str, str]:
    base = f"{API_PREFIX}/{quote(file_id)}"
    return {"self": base, "content": base + "/content", "finalize": base + "/finalize"}


def _with_links(view: Mapping[str, Any]) -> dict[str, Any]:
    return {**view, "links": _links(str(view["id"]))}


def content_disposition(name: str) -> str:
    ascii_name = "".join(c if 32 <= ord(c) < 127 and c not in '"\\' else "_" for c in name)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}"


@dataclass
class FilesApi:
    dispatcher: ToolCaller
    auth: FilesAuth
    backend: FilesBackend
    classify: Callable[[Principal], ClientClass] = _cloud_unless_human
    ensure_active: RevocationCheck | None = None
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    server_url: str | None = None

    def __post_init__(self) -> None:
        self._openapi = json.dumps(
            openapi_document(self.server_url, self.backend.max_bytes), ensure_ascii=False
        ).encode()

    def routes(self) -> list[Route]:
        g = self._guard
        return [
            Route("/v1/openapi.json", self.openapi, methods=["GET"]),
            Route("/v1/uploads/{token}", self._public(self.put_bytes), methods=["PUT"]),
            Route("/v1/uploads/{token}", self._public(self.page), methods=["GET"]),
            Route("/v1", g(self.list_files), methods=["GET"]),
            Route("/v1", g(self.create), methods=["POST"]),
            Route("/v1/{file_id}", g(self.get_file), methods=["GET"]),
            Route("/v1/{file_id}", g(self.delete_file), methods=["DELETE"]),
            Route("/v1/{file_id}/finalize", g(self.finalize), methods=["POST"]),
            Route("/v1/{file_id}/content", g(self.content), methods=["GET"]),
            Route("/{rest:path}", self._fallback),
        ]

    # ---- hạ tầng ---------------------------------------------------------------------------

    async def _fallback(self, request: Request) -> Response:
        return _json(404, error_envelope(_trace(), _not_found().error))

    async def openapi(self, request: Request) -> Response:
        return Response(
            self._openapi,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300"},
        )

    def _public(
        self, handler: Callable[[Request, str], Awaitable[Response]]
    ) -> Callable[[Request], Awaitable[Response]]:
        async def endpoint(request: Request) -> Response:
            trace_id = _trace()
            try:
                return await handler(request, trace_id)
            except ApiError as e:
                return _error_response(trace_id, e.error, e.headers)
            except ToolError as e:
                return _error_response(trace_id, e)
            except Exception as e:
                log_failure(log, "files api failure", e, trace_id=trace_id)
                err = ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure.")
                return _json(500, error_envelope(trace_id, err))

        return endpoint

    def _guard(self, handler: Handler) -> Callable[[Request], Awaitable[Response]]:
        async def endpoint(request: Request) -> Response:
            trace_id = _trace()
            try:
                ctx = await self._context(request, trace_id)
                return await handler(request, ctx)
            except ApiError as e:
                return _error_response(trace_id, e.error, e.headers)
            except ToolError as e:
                return _error_response(trace_id, e)
            except Exception as e:
                log_failure(log, "files api failure", e, trace_id=trace_id)
                err = ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure.")
                return _json(500, error_envelope(trace_id, err))

        return endpoint

    def _challenge(self, error: IdentityError | None, message: str) -> ApiError:
        c: AuthChallenge = self.auth.challenge(error)
        code = ErrorCode.ACCESS_DENIED if c.status == 403 else ErrorCode.UNAUTHENTICATED
        details = {"reason": "insufficient_scope"} if c.status == 403 else {}
        return ApiError(ToolError(code, message, details=details), c.headers)

    async def _context(self, request: Request, trace_id: str) -> CallContext:
        try:
            token = self.auth.bearer(request.headers.get("authorization"))
            if token is None:
                raise self._challenge(None, "Bearer access token required.")
            principal = await anyio.to_thread.run_sync(self.auth.verify, token)
        except IdentityProviderUnavailableError:
            raise ApiError(
                ToolError(
                    ErrorCode.TIMEOUT,
                    "Could not verify the token with the identity provider; nothing was done.",
                    details={"commit_state": "none", "retry_after_seconds": 5},
                ),
                {"Retry-After": "5"},
            ) from None
        except InsufficientScopeError as e:
            raise self._challenge(e, "The access token lacks the files scope.") from None
        except InvalidTokenError as e:
            raise self._challenge(e, "The access token is invalid or expired.") from None
        if principal.kind is PrincipalKind.HUMAN:
            raise self._challenge(
                InvalidTokenError("client_not_allowed", "human session on files API"),
                "Human session tokens are not accepted; people upload through the upload_url page.",
            )
        tenant = principal.tenant
        if tenant is None:
            if principal.tenant_ambiguous:
                raise ApiError(
                    ToolError(
                        ErrorCode.ACCESS_DENIED,
                        "Your account belongs to several organizations; xT cannot pick one.",
                        details={"reason": "tenant_ambiguous"},
                    )
                )
            raise ApiError(
                ToolError(
                    ErrorCode.UNAUTHENTICATED,
                    "Your account is not a member of any xT organization.",
                    remediation="Ask the xT administrator to add your account to the organization.",
                )
            )
        ctx = CallContext(
            actor=Actor.from_principal(principal, tenant),
            client_class=self.classify(principal),
            trace_id=trace_id,
            now=self.clock(),
        )
        if self.ensure_active is not None:
            ctx = replace(ctx, ensure_active=partial(self.ensure_active, principal, token))
        return ctx

    async def _tool(self, name: str, args: Mapping[str, Any], ctx: CallContext) -> ToolOutcome:
        return await anyio.to_thread.run_sync(lambda: self.dispatcher.call(name, args, ctx))

    async def _json_body(self, request: Request, kind: str) -> dict[str, Any]:
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > MAX_JSON_BYTES:
            raise self._too_large(MAX_JSON_BYTES, "request_body")
        raw = b""
        async for chunk in request.stream():
            raw += chunk
            if len(raw) > MAX_JSON_BYTES:
                raise self._too_large(MAX_JSON_BYTES, "request_body")
        try:
            value, dups = find_duplicate_keys(raw)
        except ValueError:
            raise _invalid(
                "Body is not valid JSON.", "invalid_json", "Send a JSON object."
            ) from None
        if dups:
            raise _invalid(
                "The JSON body repeats an object key.",
                "duplicate_key",
                "Send each field once.",
                keys=dups,
            )
        if not isinstance(value, dict):
            raise _invalid("Body must be a JSON object.", "schema", "Send a JSON object.")
        problems = [
            {"path": "/" + "/".join(map(str, e.absolute_path)), "keyword": str(e.validator)}
            for e in sorted(_VALIDATORS[kind].iter_errors(value), key=lambda e: e.path)
        ]
        if problems:
            raise _invalid(
                "Body does not match the schema (see /files/v1/openapi.json).",
                "schema",
                "Fix the listed fields (unknown fields are not allowed).",
                errors=problems[:20],
            )
        return value

    @staticmethod
    def _too_large(limit: int, resource: str = "file_size") -> ApiError:
        return ApiError(
            ToolError(
                ErrorCode.RESOURCE_CONSTRAINT,
                "The request body is too large.",
                remediation="Send a smaller file / body.",
                details={"resource": resource, "limit_bytes": limit},
            )
        )

    def _length(self, request: Request, limit: int) -> None:
        declared = request.headers.get("content-length")
        if declared is None or not declared.isdigit():
            raise _invalid(
                "Content-Length is required for uploads.",
                "length_required",
                "Send the file with a Content-Length header (no chunked encoding).",
            )
        if int(declared) > limit:
            raise self._too_large(self.backend.max_bytes)

    @staticmethod
    def _idempotency_key(request: Request) -> str:
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if key is None:
            return "files-" + uuid.uuid4().hex
        if not IDEMPOTENCY_KEY.fullmatch(key):
            raise _invalid(
                "Idempotency-Key has an invalid format.",
                "idempotency_key_format",
                "Use 8-128 chars from [A-Za-z0-9_.:-].",
            )
        return key

    async def _run(self, fn: Callable[[], Any]) -> Any:
        return await anyio.to_thread.run_sync(fn)

    # ---- tạo ------------------------------------------------------------------------------

    async def create(self, request: Request, ctx: CallContext) -> Response:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype == "multipart/form-data":
            return await self._create_multipart(request, ctx)
        if ctype != "application/json":
            raise _invalid(
                "Send JSON (upload intent) or multipart/form-data (one-step upload).",
                "content_type",
                "Use Content-Type: application/json or multipart/form-data.",
            )
        key = self._idempotency_key(request)
        body = await self._json_body(request, "create")
        out = await self._intent(ctx, key, body["name"], body.get("content_type"))
        if out.is_error:
            return outcome_response(out)
        env = out.envelope
        data = dict(env["data"])
        fid = str(data["file"]["ref"]).split(":", 1)[1]
        try:
            meta = await self._run(lambda: self.backend.metadata(ctx, uuid.UUID(fid)))
        except ToolError:  # policy cho tạo nhưng không cho đọc: trả đúng output của tool
            meta = {"id": fid, **data["file"]}
        data["file"] = _with_links(meta)
        replayed = bool((env["meta"].get("receipt") or {}).get("replayed"))
        return _json(
            200 if replayed else 201,
            {**env, "data": data},
            {"Location": _links(fid)["self"]},
        )

    async def _intent(
        self, ctx: CallContext, key: str, name: str, content_type: str | None
    ) -> ToolOutcome:
        args: dict[str, Any] = {"name": name, "source": "upload_link", "idempotency_key": key}
        if content_type:
            args["content_type"] = content_type
        return await self._tool("upload_file", args, ctx)

    async def _create_multipart(self, request: Request, ctx: CallContext) -> Response:
        limit = self.backend.max_bytes
        self._length(request, limit + MULTIPART_OVERHEAD)
        key = self._idempotency_key(request)
        try:
            form = await request.form(max_files=1, max_fields=4, max_part_size=4096)
        except Exception:
            raise _invalid(
                "The multipart body could not be parsed.",
                "invalid_multipart",
                "Send one file part named 'file' (curl -F file=@path).",
            ) from None
        try:
            part = form.get("file")
            if not isinstance(part, UploadFile) or not part.filename:
                raise _invalid(
                    "The multipart body needs a file part named 'file'.",
                    "missing_parameter",
                    "Use curl -F 'file=@path;type=application/pdf'.",
                    parameter="file",
                )
            sha = form.get("sha256")
            declared_sha = sha.strip().lower() if isinstance(sha, str) and sha.strip() else None
            if declared_sha is not None and not SHA256_HEX.fullmatch(declared_sha):
                raise _invalid("sha256 must be 64 hex characters.", "schema", "Fix sha256.")
            spool = Spool(limit)
            try:
                while chunk := await part.read(64 * 1024):
                    spool.write(chunk)
                part_type = part.content_type
                ctype = (
                    None if part_type in (None, "", "application/octet-stream") else str(part_type)
                )
                out = await self._intent(ctx, key, part.filename, ctype)
                if out.is_error:
                    return outcome_response(out)
                env = out.envelope
                fid = str(env["data"]["file"]["ref"]).split(":", 1)[1]
                if (env["meta"].get("receipt") or {}).get("replayed"):
                    meta = await self._run(lambda: self.backend.metadata(ctx, uuid.UUID(fid)))
                    return _json(200, _ok(ctx.trace_id, {"file": _with_links(meta)}))
                token = str(env["data"]["put_url"]).rsplit("/", 1)[1]
                final_sha = declared_sha or spool.sha256
                view = await self._run(
                    lambda: self.backend.ingest(token, spool, ctx.trace_id, final_sha)
                )
            finally:
                spool.close()
        finally:
            await form.close()
        return _json(
            201, _ok(ctx.trace_id, {"file": _with_links(view)}), {"Location": _links(fid)["self"]}
        )

    # ---- presigned PUT + trang -------------------------------------------------------------

    async def put_bytes(self, request: Request, trace_id: str) -> Response:
        token = str(request.path_params["token"])
        limit = self.backend.max_bytes
        self._length(request, limit)
        digest = None
        raw_digest = request.headers.get("content-digest")
        if raw_digest is not None:
            m = CONTENT_DIGEST.search(raw_digest)
            if m is None:
                raise _invalid(
                    "Content-Digest must carry sha-256 (RFC 9530).",
                    "content_digest",
                    "Send Content-Digest: sha-256=:<base64 of the 32-byte digest>:",
                )
            try:
                digest = base64.b64decode(m.group(1), validate=True).hex()
            except binascii.Error:
                raise _invalid(
                    "Content-Digest is not valid base64.", "content_digest", "Fix the digest."
                ) from None
        # Chữ ký + hạn kiểm TRƯỚC khi nhận byte (không ghi đĩa tạm cho link sai).
        await self._run(lambda: self.backend.upload_info(token, trace_id))
        with Spool(limit) as spool:
            async for chunk in request.stream():
                spool.write(chunk)
            view = await self._run(lambda: self.backend.ingest(token, spool, trace_id, digest))
        return _json(200, _ok(trace_id, {"file": _with_links(view)}))

    async def page(self, request: Request, trace_id: str) -> Response:
        token = str(request.path_params["token"])
        info = await self._run(lambda: self.backend.upload_info(token, trace_id))
        return Response(
            upload_page(info),
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Security-Policy": PAGE_CSP,
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ---- đọc / finalize / xóa --------------------------------------------------------------

    async def list_files(self, request: Request, ctx: CallContext) -> Response:
        q = request.query_params
        unknown = sorted(set(q) - {"limit"})
        if unknown:
            raise _invalid("Unknown query parameters.", "schema", "Use limit.", parameters=unknown)
        raw = q.get("limit", "50")
        if not raw.isdigit() or not 1 <= int(raw) <= 100:
            raise _invalid("limit must be 1..100.", "schema", "Send limit=1..100.")
        files = await self._run(lambda: self.backend.list_mine(ctx, int(raw)))
        return _json(200, _ok(ctx.trace_id, {"files": [_with_links(f) for f in files]}))

    async def get_file(self, request: Request, ctx: CallContext) -> Response:
        fid = _file_id(request)
        meta = await self._run(lambda: self.backend.metadata(ctx, fid))
        return _json(200, _ok(ctx.trace_id, {"file": _with_links(meta)}))

    async def finalize(self, request: Request, ctx: CallContext) -> Response:
        fid = _file_id(request)
        body = await self._json_body(request, "finalize")
        meta = await self._run(lambda: self.backend.finalize(ctx, fid, str(body["sha256"])))
        return _json(200, _ok(ctx.trace_id, {"file": _with_links(meta)}))

    async def delete_file(self, request: Request, ctx: CallContext) -> Response:
        fid = _file_id(request)
        meta = await self._run(lambda: self.backend.delete(ctx, fid))
        return _json(200, _ok(ctx.trace_id, {"file": meta}))

    async def content(self, request: Request, ctx: CallContext) -> Response:
        fid = _file_id(request)
        d = await self._run(lambda: self.backend.content(ctx, fid))
        etag = f'"sha256-{d.sha256}"'
        headers = {
            "Content-Disposition": content_disposition(d.name),
            "Content-Length": str(d.size),
            "ETag": etag,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "Repr-Digest": "sha-256=:" + base64.b64encode(bytes.fromhex(d.sha256)).decode() + ":",
        }
        return StreamingResponse(d.chunks, media_type=d.content_type, headers=headers)


def files_routes(
    dispatcher: ToolCaller, auth: FilesAuth, backend: FilesBackend, **options: Any
) -> Mount:
    """`Mount("/files")` (gồm `/files/v1` không có dấu `/` cuối) để ghép vào app `xt-api`.
    `options`: `classify`, `ensure_active`, `clock`, `server_url`."""
    api = FilesApi(dispatcher, auth, backend, **options)
    return Mount(MOUNT, routes=api.routes())


__all__ = [
    "API_PREFIX",
    "Download",
    "FilesApi",
    "FilesAuth",
    "FilesBackend",
    "UploadInfo",
    "content_disposition",
    "files_routes",
    "openapi_document",
    "status_for",
]
