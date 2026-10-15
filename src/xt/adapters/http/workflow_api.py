# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""REST `/workflow/v1`: dịch vụ Workflow của Tầng 1 gọi được bằng curl, không qua MCP.

Mỗi endpoint là một lớp mỏng trên CÙNG `ToolDispatcher` của MCP (IAM -> idempotency -> plan/CAS
-> tự chủ/xác nhận -> commit -> làn PII), không có đường tắt:

| Endpoint | Tool |
|---|---|
| `GET /definitions` | `inspect_workspace(include=[processes])` |
| `GET /definitions/{id}` | `describe_process` |
| `GET /definitions/{id}/bpmn` | `describe_process` (IAM + revision) rồi `WorkflowBackend.bpmn` |
| `POST /instances` | `create_record` (nếu chưa có nháp) + `submit_request`; quy trình `manual` không gắn đề nghị: `start_process` |
| `GET /instances/{id}` | `WorkflowBackend.instance` (RLS tenant) rồi `get_record` của đề nghị |
| `GET /inbox` | `get_inbox` |
| `POST /tasks/{id}/decision` | `decide_request` (luôn `confirmation_required` + `confirm_url`) |

Người (client phiên Desk) không gọi được API này: người chỉ quyết định trên trang
`/desk/confirm/{id}` (giống MCP, M6). Envelope lỗi là envelope chung của tool (`ok:false`), lỗi do
adapter tự dựng cũng qua lưới `ErrorScrubber`. Tài liệu: `docs/workflow-api.md`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any, Protocol
from urllib.parse import quote

import anyio.to_thread
from jsonschema import Draft202012Validator
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from xt import __version__
from xt.application.tools import Actor, CallContext, ClientClass, ToolError, ToolOutcome
from xt.application.tools.pii_scrub import ErrorScrubber
from xt.application.tools.safe_log import log_failure
from xt.contracts.tools import (
    BY_NAME,
    CONTRACT_VERSION,
    ERROR_ENVELOPE,
    ErrorCode,
    find_duplicate_keys,
)
from xt.contracts.tools.envelope import META
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import (
    IdentityError,
    IdentityProviderUnavailableError,
    InsufficientScopeError,
    InvalidTokenError,
    Principal,
    PrincipalKind,
)

log = logging.getLogger("xt.workflow_api")

API_PREFIX = "/workflow/v1"
DEFAULT_MAX_BODY_BYTES = 64 * 1024
IDEMPOTENCY_HEADER = "Idempotency-Key"
# Khóa REST + hậu tố `.create`/`.submit` vẫn nằm trong mẫu IDEM của tool (8..128 ký tự).
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9_.:-]{8,120}$")
PROCESS_ID = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
TASK_ID = re.compile(
    r"^(xrn:[a-z0-9][a-z0-9_-]{0,63}:)?[a-z][a-z0-9_]{0,62}:[a-z0-9][a-z0-9_-]{0,63}$"
)
JSON_TYPES = ("application/json",)

# Tên quyết định của engine (`DecisionOutcome`) -> giá trị `decide_request`. `request_info` có trong
# engine nhưng chưa có trong ToolContract `decide_request` v0.1 -> từ chối rõ ràng, không giả.
DECISIONS: Mapping[str, str | None] = {
    "approve": "approve",
    "reject": "reject",
    "return_for_revision": "return",
    "delegate": "delegate",
    "request_info": None,
}

# Mã lỗi contract -> HTTP. `confirmation_required` = 428: hành động CHƯA chạy, cần người xác nhận
# trên confirm_url rồi gửi lại đúng request (cùng Idempotency-Key). Không dùng 2xx để client
# `curl -f` / SDK không coi là đã duyệt (docs/decisions.md "REST /workflow/v1").
HTTP_STATUS: Mapping[ErrorCode, int] = {
    ErrorCode.INVALID_REQUEST: 400,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.ACCESS_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.REVISION_CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.PRECONDITION_FAILED: 409,
    ErrorCode.CONFIRMATION_DECLINED: 409,
    ErrorCode.RULE_VIOLATION: 422,
    ErrorCode.CONFIRMATION_REQUIRED: 428,
    ErrorCode.RESOURCE_CONSTRAINT: 413,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.CAPABILITY_UNAVAILABLE: 501,
    ErrorCode.TIMEOUT: 503,
}


# ---- cổng (composition root nối vào) -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AuthChallenge:
    status: int
    headers: Mapping[str, str] = field(default_factory=dict)


class WorkflowAuth(Protocol):
    """Lớp danh tính cho audience của API workflow (composition nối `AccessTokenVerifier`)."""

    def bearer(self, authorization: str | None) -> str | None:
        """None nếu thiếu header; header sai -> `InvalidTokenError`."""
        ...

    def verify(self, token: str) -> Principal:
        """Ném `InvalidTokenError` (401), `InsufficientScopeError` (403),
        `IdentityProviderUnavailableError` (503)."""
        ...

    def challenge(self, error: IdentityError | None) -> AuthChallenge:
        """401/403 + `WWW-Authenticate` (RFC 6750)."""
        ...


class ToolCaller(Protocol):
    """Hình dạng public của `ToolDispatcher` mà API dùng."""

    def call(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        ctx: CallContext,
        *,
        duplicate_keys: Sequence[str] = (),
    ) -> ToolOutcome: ...


@dataclass(frozen=True, slots=True)
class InstanceTask:
    task_id: str
    step: str
    name: str
    kind: str
    outcomes: tuple[str, ...]
    assignees: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class InstanceView:
    """Trạng thái engine của một instance (không có comment/biến: chúng có thể mang PII)."""

    instance_id: str
    ref: str
    process_id: str
    version: int
    status: str
    tasks: tuple[InstanceTask, ...] = ()


class WorkflowBackend(Protocol):
    """Đọc phần engine mà 17 tool không lộ ra. Chạy trong transaction RLS của tenant `actor`;
    tenant khác/không tồn tại -> None (cùng một câu trả lời)."""

    def bpmn(self, actor: Actor, trace_id: str, process_id: str, revision: int) -> str | None:
        """BPMN 2.0 XML (có BPMNDI) đúng bản `(process_id, revision)` đã duyệt."""
        ...

    def instance(self, actor: Actor, trace_id: str, instance_id: str) -> InstanceView | None: ...

    def instance_ref(self, actor: Actor, trace_id: str, instance_id: str) -> str | None:
        """Chỉ ref đề nghị của instance (một truy vấn, không nạp engine)."""
        ...


def _cloud_unless_human(principal: Principal) -> ClientClass:
    return ClientClass.HUMAN if principal.kind is PrincipalKind.HUMAN else ClientClass.CLOUD


RevocationCheck = Callable[[Principal, str, RiskLevel], None]


# ---- envelope ------------------------------------------------------------------------------


def _mask(value: str) -> str:
    return "[PII]"


def error_envelope(trace_id: str, error: ToolError) -> dict[str, Any]:
    """Envelope lỗi chung cho lỗi do adapter dựng (không có ngữ cảnh tool), đã qua lưới PII."""
    scrubbed = ErrorScrubber(_mask).error(error.to_error())
    return {
        "ok": False,
        "untrusted": True,
        "error": scrubbed,
        "meta": {"contract_version": CONTRACT_VERSION, "trace_id": trace_id},
    }


def _json(
    status: int, body: Mapping[str, Any], headers: Mapping[str, str] | None = None
) -> Response:
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    hdrs = {"Cache-Control": "no-store", **(headers or {})}
    return Response(raw, status_code=status, media_type="application/json", headers=hdrs)


def _retry_after(error: Mapping[str, Any]) -> dict[str, str]:
    details = error.get("details")
    secs = details.get("retry_after_seconds") if isinstance(details, Mapping) else None
    return {"Retry-After": str(int(secs))} if isinstance(secs, int | float) else {}


def outcome_response(
    outcome: ToolOutcome, success_status: int = 200, headers: Mapping[str, str] | None = None
) -> Response:
    env = outcome.envelope
    if not outcome.is_error:
        return _json(success_status, env, headers)
    error = env.get("error") or {}
    code = ErrorCode(error.get("code", ErrorCode.INTERNAL_ERROR.value))
    extra = _retry_after(error)
    if code is ErrorCode.CONFIRMATION_REQUIRED:
        url = (error.get("details") or {}).get("confirm_url")
        if isinstance(url, str):
            extra["Link"] = f'<{url}>; rel="confirm"'
    return _json(HTTP_STATUS.get(code, 500), env, extra)


class ApiError(Exception):
    """Lỗi adapter (trước khi tới dispatcher). Thông điệp tĩnh, không echo dữ liệu nghiệp vụ."""

    def __init__(self, error: ToolError, headers: Mapping[str, str] | None = None) -> None:
        super().__init__(error.message)
        self.error = error
        self.headers = dict(headers or {})


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


# ---- schema REST (sinh OpenAPI và kiểm body từ cùng một nguồn) -----------------------------


def _strip(schema: Any) -> Any:
    """Bỏ `$schema` lồng (OpenAPI 3.1 dùng dialect mặc định của tài liệu)."""
    if isinstance(schema, Mapping):
        return {k: _strip(v) for k, v in schema.items() if k != "$schema"}
    if isinstance(schema, list):
        return [_strip(v) for v in schema]
    return schema


def _tool_prop(tool: str, name: str) -> dict[str, Any]:
    prop: dict[str, Any] = dict(BY_NAME[tool].input_schema["properties"][name])
    return prop


def _tool_out(tool: str) -> dict[str, Any]:
    out: dict[str, Any] = _strip(BY_NAME[tool].output_schema)
    return out


def _envelope(data: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["ok", "untrusted", "data", "meta"],
        "properties": {
            "ok": {"const": True},
            "untrusted": {"type": "boolean"},
            "data": dict(data),
            "meta": _strip(META),
        },
    }


LINKS = {
    "type": "object",
    "additionalProperties": {"type": "string"},
    "description": "Đường dẫn tương đối tới tài nguyên liên quan.",
}

CREATE_INSTANCE_BODY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "description": (
        "Tạo nháp đề nghị rồi gửi duyệt (definition_id hoặc model_id + values), "
        "gửi duyệt một nháp có sẵn (ref), hoặc khởi động quy trình thủ công không gắn đề nghị "
        "(definition_id + subject_ref/parameters, như tool start_process)."
    ),
    "properties": {
        "definition_id": {
            "type": "string",
            "pattern": PROCESS_ID.pattern,
            "description": "Quy trình, vd proposal_approval; model đề nghị lấy từ trigger.",
        },
        "model_id": _tool_prop("create_record", "model_id"),
        "values": _tool_prop("create_record", "values"),
        "ref": _tool_prop("submit_request", "ref"),
        "note": _tool_prop("submit_request", "note"),
        "expected_revision": _tool_prop("submit_request", "expected_revision"),
        "subject_ref": _tool_prop("start_process", "subject_ref"),
        "parameters": _tool_prop("start_process", "parameters"),
    },
    "oneOf": [
        {
            "required": ["ref"],
            "not": {
                "anyOf": [
                    {"required": ["values"]},
                    {"required": ["model_id"]},
                    {"required": ["definition_id"]},
                    {"required": ["subject_ref"]},
                    {"required": ["parameters"]},
                ]
            },
        },
        {
            "required": ["values"],
            # definition_id XOR model_id: gửi cả hai thì không rõ ý (WA-09).
            "not": {
                "anyOf": [
                    {"required": ["ref"]},
                    {"required": ["definition_id", "model_id"]},
                    {"required": ["subject_ref"]},
                    {"required": ["parameters"]},
                ]
            },
            "anyOf": [{"required": ["definition_id"]}, {"required": ["model_id"]}],
        },
        {
            # Quy trình trigger manual không gắn đề nghị -> start_process (cùng dispatcher).
            "required": ["definition_id"],
            "not": {
                "anyOf": [
                    {"required": ["values"]},
                    {"required": ["model_id"]},
                    {"required": ["ref"]},
                    {"required": ["note"]},
                    {"required": ["expected_revision"]},
                ]
            },
        },
    ],
}

DECISION_BODY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "expected_revision"],
    "properties": {
        "decision": {
            "enum": list(DECISIONS),
            "description": "request_info chưa có trong decide_request v0.1: trả 400.",
        },
        "reason": _tool_prop("decide_request", "reason"),
        "delegate_to": _tool_prop("decide_request", "delegate_to"),
        "expected_revision": _tool_prop("decide_request", "expected_revision"),
    },
}

WITHDRAW_BODY: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["expected_revision"],
    "properties": {
        "expected_revision": _tool_prop("transition_record", "expected_revision"),
        "reason": _tool_prop("transition_record", "reason"),
    },
}

_PROCESS_ITEM = _tool_out("inspect_workspace")["properties"]["processes"]["items"]
DEFINITION_LIST = _envelope(
    {
        "type": "object",
        "required": ["definitions"],
        "properties": {
            "definitions": {
                "type": "array",
                "items": {"allOf": [_PROCESS_ITEM, {"properties": {"links": LINKS}}]},
            }
        },
    }
)
DEFINITION = _envelope({"allOf": [_tool_out("describe_process"), {"properties": {"links": LINKS}}]})
INSTANCE_STATE = {
    "type": "object",
    "required": ["id", "process_id", "version", "status", "tasks"],
    "properties": {
        "id": {"type": "string", "format": "uuid"},
        "process_id": {"type": "string"},
        "version": {"type": "integer"},
        "status": {"enum": ["running", "completed", "rejected", "withdrawn"]},
        "tasks": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["task_id", "step", "name", "kind"],
                "properties": {
                    "task_id": {"type": "string"},
                    "step": {"type": "string"},
                    "name": {"type": "string"},
                    "kind": {"type": "string"},
                    "outcomes": {"type": "array", "items": {"type": "string"}},
                    "assignees": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "links": LINKS,
    },
}
INSTANCE = _envelope(
    {
        "type": "object",
        "required": ["instance", "request"],
        "properties": {
            "instance": INSTANCE_STATE,
            "request": _tool_out("get_record")["properties"]["record"],
        },
    }
)
STARTED = _envelope(
    {
        "allOf": [
            {"anyOf": [_tool_out("submit_request"), _tool_out("start_process")]},
            {
                "required": ["instance"],
                "properties": {
                    "instance": {
                        "type": "object",
                        "required": ["id", "href"],
                        "properties": {"id": {"type": "string"}, "href": {"type": "string"}},
                    }
                },
            },
        ]
    }
)
INBOX = _envelope(_tool_out("get_inbox"))
DECIDED = _envelope(_tool_out("decide_request"))
WITHDRAWN = _envelope(_tool_out("transition_record"))

_BODY_VALIDATORS = {
    "create_instance": Draft202012Validator(CREATE_INSTANCE_BODY),
    "decide_task": Draft202012Validator(DECISION_BODY),
    "withdraw_instance": Draft202012Validator(WITHDRAW_BODY),
}
for _v in _BODY_VALIDATORS.values():
    Draft202012Validator.check_schema(_v.schema)


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    where: str  # path | query | header
    schema: Mapping[str, Any]
    required: bool = False
    description: str = ""


@dataclass(frozen=True, slots=True)
class Operation:
    """Một endpoint. Route Starlette và tài liệu OpenAPI cùng sinh từ danh sách này."""

    op_id: str
    method: str
    path: str  # tương đối với API_PREFIX
    summary: str
    tools: tuple[str, ...]
    responses: Mapping[int, tuple[str, Mapping[str, Any] | None]]
    params: tuple[Param, ...] = ()
    body: Mapping[str, Any] | None = None
    public: bool = False


IDEM_PARAM = Param(
    IDEMPOTENCY_HEADER,
    "header",
    {"type": "string", "pattern": IDEMPOTENCY_KEY.pattern},
    required=True,
    description="Khóa do client tự sinh; gửi lại đúng khóa khi thử lại cùng hành động.",
)
_ERR = ("Envelope lỗi chung (`ok:false`).", None)
_COMMON_ERRORS: Mapping[int, tuple[str, Mapping[str, Any] | None]] = {
    400: _ERR,
    401: ("Thiếu/sai token (WWW-Authenticate: Bearer).", None),
    403: _ERR,
    404: _ERR,
    405: ("Method không hỗ trợ (header Allow).", None),
    429: ("Quá tải hoặc cùng Idempotency-Key đang chạy (Retry-After).", None),
    500: _ERR,
    501: ("Năng lực chưa có trong bản phát hành này.", None),
    503: _ERR,
}
_WRITE_ERRORS: Mapping[int, tuple[str, Mapping[str, Any] | None]] = {
    409: _ERR,
    413: ("Body vượt giới hạn (resource_constraint).", None),
    422: ("Vi phạm rule nghiệp vụ (rule_violation).", None),
    428: ("Cần người xác nhận trên confirm_url, rồi gửi lại y hệt.", None),
}
INBOX_QUERY = BY_NAME["get_inbox"].input_schema["properties"]

OPERATIONS: tuple[Operation, ...] = (
    Operation(
        "list_definitions",
        "GET",
        "/definitions",
        "Danh sách quy trình đang kích hoạt của tenant.",
        ("inspect_workspace",),
        {200: ("Định nghĩa + link BPMN.", DEFINITION_LIST), **_COMMON_ERRORS},
    ),
    Operation(
        "get_definition",
        "GET",
        "/definitions/{definition_id}",
        "Bước, người duyệt, SLA của một quy trình.",
        ("describe_process",),
        {200: ("Mô tả quy trình.", DEFINITION), **_COMMON_ERRORS},
        (Param("definition_id", "path", {"type": "string", "pattern": PROCESS_ID.pattern}, True),),
    ),
    Operation(
        "get_definition_bpmn",
        "GET",
        "/definitions/{definition_id}/bpmn",
        "BPMN 2.0 XML (có BPMNDI) của bản đang kích hoạt; mở được bằng bpmn.io/Camunda Modeler.",
        ("describe_process",),
        {
            200: ("BPMN XML.", {"type": "string"}),
            304: ("Không đổi (If-None-Match khớp ETag).", {"type": "string"}),
            **_COMMON_ERRORS,
        },
        (Param("definition_id", "path", {"type": "string", "pattern": PROCESS_ID.pattern}, True),),
    ),
    Operation(
        "create_instance",
        "POST",
        "/instances",
        "Khởi động quy trình: tạo nháp đề nghị (nếu cần) rồi gửi duyệt; quy trình thủ công không"
        " gắn đề nghị qua start_process.",
        ("describe_process", "create_record", "submit_request", "start_process"),
        {
            201: ("Đã gửi duyệt; Location = instance.", STARTED),
            200: ("Gửi lại cùng Idempotency-Key: trả lại kết quả cũ.", STARTED),
            **_WRITE_ERRORS,
            **_COMMON_ERRORS,
        },
        (IDEM_PARAM,),
        CREATE_INSTANCE_BODY,
    ),
    Operation(
        "get_instance",
        "GET",
        "/instances/{instance_id}",
        "Trạng thái instance + đề nghị gắn với nó.",
        ("get_record",),
        {200: ("Instance.", INSTANCE), **_COMMON_ERRORS},
        (Param("instance_id", "path", {"type": "string", "format": "uuid"}, True),),
    ),
    Operation(
        "withdraw_instance",
        "POST",
        "/instances/{instance_id}/withdraw",
        "Người đề nghị rút đề nghị đang chờ duyệt (kể cả bước bị kẹt); quy trình dừng ở withdrawn.",
        ("transition_record",),
        {200: ("Đã rút.", WITHDRAWN), **_WRITE_ERRORS, **_COMMON_ERRORS},
        (Param("instance_id", "path", {"type": "string", "format": "uuid"}, True), IDEM_PARAM),
        WITHDRAW_BODY,
    ),
    Operation(
        "get_inbox",
        "GET",
        "/inbox",
        "Việc đang chờ người được đại diện (approval -> POST /tasks/{ref}/decision).",
        ("get_inbox",),
        {200: ("Inbox.", INBOX), **_COMMON_ERRORS},
        (
            Param(
                "kinds",
                "query",
                {"type": "string", "description": "Danh sách cách nhau bởi dấu phẩy."},
                description="task, approval, notification, mention",
            ),
            Param("status", "query", INBOX_QUERY["status"]),
            Param("due_before", "query", INBOX_QUERY["due_before"]),
            Param("limit", "query", INBOX_QUERY["limit"]),
            Param("cursor", "query", INBOX_QUERY["cursor"]),
        ),
    ),
    Operation(
        "decide_task",
        "POST",
        "/tasks/{task_id}/decision",
        "Quyết định bước đang chờ của một đề nghị (task_id = ref trong inbox). Agent luôn nhận "
        "428 confirmation_required + confirm_url; người xác nhận trên Desk rồi agent gửi lại.",
        ("decide_request",),
        {
            200: ("Đã thực hiện (sau khi người xác nhận).", DECIDED),
            **_WRITE_ERRORS,
            **_COMMON_ERRORS,
        },
        (
            Param("task_id", "path", {"type": "string", "pattern": TASK_ID.pattern}, True),
            IDEM_PARAM,
        ),
        DECISION_BODY,
    ),
    Operation(
        "openapi",
        "GET",
        "/openapi.json",
        "Tài liệu OpenAPI 3.1 này (sinh từ code).",
        (),
        {200: ("OpenAPI 3.1.", {"type": "object"})},
        public=True,
    ),
)


def openapi_document(server_url: str | None = None) -> dict[str, Any]:
    """OpenAPI 3.1 sinh từ `OPERATIONS` + schema ToolContract (không viết tay)."""
    paths: dict[str, dict[str, Any]] = {}
    for op in OPERATIONS:
        responses: dict[str, Any] = {}
        for status, (desc, schema) in sorted(op.responses.items()):
            if status == 304:
                responses[str(status)] = {"description": desc}
                continue
            if op.op_id == "get_definition_bpmn" and status == 200:
                content = {"application/xml": {"schema": {"type": "string"}}}
            elif schema is None:
                content = {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}
            else:
                content = {"application/json": {"schema": _strip(schema)}}
            responses[str(status)] = {"description": desc, "content": content}
        entry: dict[str, Any] = {
            "operationId": op.op_id,
            "summary": op.summary,
            "responses": responses,
            "x-xt-tools": list(op.tools),
        }
        if op.public:
            entry["security"] = []
        if op.params:
            entry["parameters"] = [
                {
                    "name": p.name,
                    "in": p.where,
                    "required": p.required or p.where == "path",
                    "schema": _strip(p.schema),
                    **({"description": p.description} if p.description else {}),
                }
                for p in op.params
            ]
        if op.body is not None:
            entry["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _strip(op.body)}},
            }
        paths.setdefault(API_PREFIX + op.path, {})[op.method.lower()] = entry
    doc: dict[str, Any] = {
        "openapi": "3.1.0",
        "info": {
            "title": "xT Workflow API",
            "version": __version__,
            "description": (
                "Dịch vụ Workflow headless của xT (Tầng 1). Cùng pipeline với MCP: IAM, "
                "idempotency, tự chủ, xác nhận của người, làn PII. Contract tool "
                f"{CONTRACT_VERSION}."
            ),
            "license": {"name": "Apache-2.0", "identifier": "Apache-2.0"},
        },
        "paths": paths,
        "components": {
            "securitySchemes": {
                "bearer": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Access token Keycloak, aud = audience của workflow.",
                }
            },
            "schemas": {"Error": _strip(ERROR_ENVELOPE)},
        },
        "security": [{"bearer": []}],
    }
    if server_url:
        doc["servers"] = [{"url": server_url}]
    return doc


# ---- app -----------------------------------------------------------------------------------


Handler = Callable[[Request, CallContext], Awaitable[Response]]


_ALL_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")


def _path_rx(template: str) -> re.Pattern[str]:
    return re.compile(re.sub(r"\\\{[a-z_]+\\\}", "[^/]+", re.escape(template)))


def _etag_matches(header: str | None, etag: str) -> bool:
    """If-None-Match theo RFC 9110 §13.1.2: so sánh yếu, danh sách, `*`."""
    if not header:
        return False
    opaque = etag.removeprefix("W/")
    for raw in header.split(","):
        tag = raw.strip()
        if tag == "*" or tag.removeprefix("W/") == opaque:
            return True
    return False


def _href(*parts: str) -> str:
    return API_PREFIX + "".join("/" + quote(p, safe=":") for p in parts)


def _instance_id(ref: Any) -> str | None:
    if isinstance(ref, str) and ref.startswith("process_instance:"):
        return ref.split(":", 1)[1]
    return None


class WorkflowApi:
    def __init__(
        self,
        dispatcher: ToolCaller,
        auth: WorkflowAuth,
        backend: WorkflowBackend,
        *,
        classify: Callable[[Principal], ClientClass] = _cloud_unless_human,
        ensure_active: RevocationCheck | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
        server_url: str | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.auth = auth
        self.backend = backend
        self.classify = classify
        self.ensure_active = ensure_active
        self.clock = clock
        self.max_body_bytes = max_body_bytes
        self._openapi = json.dumps(openapi_document(server_url), ensure_ascii=False).encode()

    # ---- hạ tầng chung ---------------------------------------------------------------------

    def routes(self) -> list[Route]:
        handlers: dict[str, Handler] = {
            "list_definitions": self.list_definitions,
            "get_definition": self.get_definition,
            "get_definition_bpmn": self.get_definition_bpmn,
            "create_instance": self.create_instance,
            "get_instance": self.get_instance,
            "get_inbox": self.get_inbox,
            "decide_task": self.decide_task,
            "withdraw_instance": self.withdraw_instance,
        }
        out: list[Route] = []
        for op in OPERATIONS:
            if op.public:
                out.append(Route(op.path, self.openapi, methods=[op.method]))
            else:
                out.append(Route(op.path, self._guard(handlers[op.op_id]), methods=[op.method]))
        # Route/method lạ: envelope lỗi chung thay vì text/plain của router (WA-05).
        out.append(Route("/{rest:path}", self._fallback, methods=list(_ALL_METHODS)))
        return out

    async def _fallback(self, request: Request) -> Response:
        trace_id = "tr_" + uuid.uuid4().hex[:20]
        rel = "/" + str(request.path_params.get("rest", ""))
        allowed = sorted({op.method for op in OPERATIONS if _path_rx(op.path).fullmatch(rel)})
        if allowed:
            err = ToolError(
                ErrorCode.INVALID_REQUEST,
                "Method not allowed on this endpoint.",
                remediation=f"Use {', '.join(allowed)} (see /workflow/v1/openapi.json).",
                details={"reason": "method_not_allowed", "allowed": allowed},
            )
            return _json(405, error_envelope(trace_id, err), {"Allow": ", ".join(allowed)})
        return _json(404, error_envelope(trace_id, _not_found().error))

    async def openapi(self, request: Request) -> Response:
        return Response(
            self._openapi,
            media_type="application/json",
            headers={"Cache-Control": "public, max-age=300"},
        )

    def _guard(self, handler: Handler) -> Callable[[Request], Awaitable[Response]]:
        async def endpoint(request: Request) -> Response:
            trace_id = "tr_" + uuid.uuid4().hex[:20]
            try:
                ctx = await self._context(request, trace_id)
                return await handler(request, ctx)
            except ApiError as e:
                status = HTTP_STATUS.get(e.error.code, 500)
                return _json(status, error_envelope(trace_id, e.error), e.headers)
            except Exception as e:
                log_failure(log, "workflow api failure", e, trace_id=trace_id)
                err = ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure.")
                return _json(500, error_envelope(trace_id, err))

        return endpoint

    def _challenge(self, error: IdentityError | None, message: str) -> ApiError:
        c = self.auth.challenge(error)
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
            raise self._challenge(e, "The access token lacks the workflow scope.") from None
        except InvalidTokenError as e:
            raise self._challenge(e, "The access token is invalid or expired.") from None
        if principal.kind is PrincipalKind.HUMAN:
            # Như MCP (M6): người chỉ quyết định trên trang /desk/confirm, không gọi API trực tiếp.
            raise self._challenge(
                InvalidTokenError("client_not_allowed", "human session on workflow API"),
                "Human session tokens are not accepted; people confirm on the xT confirmation"
                " page.",
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

    async def _body(self, request: Request, op_id: str) -> dict[str, Any]:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if ctype not in JSON_TYPES:
            raise _invalid(
                "Body must be JSON.",
                "content_type",
                "Send Content-Type: application/json with a JSON object body.",
            )
        declared = request.headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_body_bytes:
            raise self._too_large()
        chunks: list[bytes] = []
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > self.max_body_bytes:
                raise self._too_large()
            chunks.append(chunk)
        try:
            value, dups = find_duplicate_keys(b"".join(chunks))
        except ValueError:
            raise _invalid(
                "Body is not valid JSON.", "invalid_json", "Send a valid JSON object."
            ) from None
        if dups:
            raise _invalid(
                "The JSON body repeats an object key.",
                "duplicate_key",
                "Send each field once, then call again.",
                keys=dups,
            )
        if not isinstance(value, dict):
            raise _invalid("Body must be a JSON object.", "schema", "Send a JSON object.")
        problems = [
            {"path": "/" + "/".join(map(str, e.absolute_path)), "keyword": str(e.validator)}
            for e in sorted(_BODY_VALIDATORS[op_id].iter_errors(value), key=lambda e: e.path)
        ]
        if problems:
            # Không trả message của jsonschema: nó echo giá trị (có thể là PII).
            raise _invalid(
                "Body does not match the schema (see /workflow/v1/openapi.json).",
                "schema",
                "Fix the listed fields (unknown fields are not allowed).",
                errors=problems[:20],
            )
        return value

    def _too_large(self) -> ApiError:
        return ApiError(
            ToolError(
                ErrorCode.RESOURCE_CONSTRAINT,
                "The request body is too large.",
                remediation="Send a smaller body.",
                details={"resource": "request_body", "limit_bytes": self.max_body_bytes},
            )
        )

    @staticmethod
    def _idempotency_key(request: Request) -> str:
        key = request.headers.get(IDEMPOTENCY_HEADER)
        if key is None:
            raise _invalid(
                "Idempotency-Key header is required.",
                "idempotency_key_required",
                "Send a unique Idempotency-Key (8-120 chars [A-Za-z0-9_.:-]); reuse it only to "
                "retry the same request.",
            )
        if not IDEMPOTENCY_KEY.fullmatch(key):
            raise _invalid(
                "Idempotency-Key has an invalid format.",
                "idempotency_key_format",
                "Use 8-120 chars from [A-Za-z0-9_.:-].",
            )
        return key

    # ---- definitions -----------------------------------------------------------------------

    async def list_definitions(self, request: Request, ctx: CallContext) -> Response:
        if request.query_params:
            raise _invalid("This endpoint takes no query parameters.", "schema", "Remove them.")
        out = await self._tool("inspect_workspace", {"include": ["processes"]}, ctx)
        if out.is_error:
            return outcome_response(out)
        env = out.envelope
        defs = []
        for p in env["data"].get("processes") or []:
            pid = str(p["id"])
            defs.append(
                {
                    **p,
                    "links": {
                        "self": _href("definitions", pid),
                        "bpmn": _href("definitions", pid, "bpmn"),
                    },
                }
            )
        return _json(200, {**env, "data": {"definitions": defs}})

    async def _describe(self, ctx: CallContext, pid: str) -> ToolOutcome:
        if not PROCESS_ID.fullmatch(pid):
            raise _not_found()
        return await self._tool("describe_process", {"process_id": pid}, ctx)

    async def get_definition(self, request: Request, ctx: CallContext) -> Response:
        pid = str(request.path_params["definition_id"])
        out = await self._describe(ctx, pid)
        if out.is_error:
            return outcome_response(out)
        env = out.envelope
        data = {
            **env["data"],
            "links": {"self": _href("definitions", pid), "bpmn": _href("definitions", pid, "bpmn")},
        }
        return _json(200, {**env, "data": data})

    async def get_definition_bpmn(self, request: Request, ctx: CallContext) -> Response:
        pid = str(request.path_params["definition_id"])
        out = await self._describe(ctx, pid)
        if out.is_error:
            return outcome_response(out)
        revision = int(out.envelope["data"]["process"]["revision"])
        xml = await anyio.to_thread.run_sync(
            self.backend.bpmn, ctx.actor, ctx.trace_id, pid, revision
        )
        if xml is None:
            raise _not_found()
        raw = xml.encode("utf-8")
        etag = '"sha256-' + hashlib.sha256(raw).hexdigest()[:32] + '"'
        if _etag_matches(request.headers.get("if-none-match"), etag):
            return Response(
                status_code=304, headers={"ETag": etag, "Cache-Control": "private, no-cache"}
            )
        return Response(
            raw,
            media_type="application/xml",
            headers={
                "Content-Disposition": f'attachment; filename="{pid}-v{revision}.bpmn"',
                "ETag": etag,
                "Cache-Control": "private, no-cache",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # ---- instances -------------------------------------------------------------------------

    async def create_instance(self, request: Request, ctx: CallContext) -> Response:
        key = self._idempotency_key(request)
        body = await self._body(request, "create_instance")
        if "values" not in body and "ref" not in body:
            return await self._start_process(body, key, ctx)
        ref = body.get("ref")
        if ref is None:
            model_id = body.get("model_id")
            if model_id is None:
                described = await self._describe(ctx, str(body["definition_id"]))
                if described.is_error:
                    return outcome_response(described)
                trigger = described.envelope["data"]["process"].get("trigger") or {}
                model_id = trigger.get("model_id")
                if not isinstance(model_id, str):
                    raise ApiError(
                        ToolError(
                            ErrorCode.PRECONDITION_FAILED,
                            "This process is not started by a request model.",
                            remediation="Start it from its trigger; see GET /definitions/{id}.",
                            details={"reason": "no_request_model"},
                        )
                    )
            created = await self._tool(
                "create_record",
                {
                    "model_id": model_id,
                    "values": body["values"],
                    "idempotency_key": key + ".create",
                },
                ctx,
            )
            if created.is_error:
                return outcome_response(created)
            ref = created.envelope["data"]["record"]["ref"]
        args: dict[str, Any] = {"ref": ref, "idempotency_key": key + ".submit"}
        for k in ("note", "expected_revision"):
            if k in body:
                args[k] = body[k]
        submitted = await self._tool("submit_request", args, ctx)
        if submitted.is_error:
            return outcome_response(submitted)
        env = submitted.envelope
        iid = _instance_id(env["data"].get("process_instance"))
        if iid is None:  # pragma: no cover - output contract đã kiểm process_instance
            raise RuntimeError("submit_request without process_instance")
        href = _href("instances", iid)
        data = {**env["data"], "instance": {"id": iid, "href": href}}
        replayed = bool((env["meta"].get("receipt") or {}).get("replayed"))
        return _json(200 if replayed else 201, {**env, "data": data}, {"Location": href})

    async def _start_process(self, body: Mapping[str, Any], key: str, ctx: CallContext) -> Response:
        """`{definition_id, subject_ref?, parameters?}` -> đúng tool `start_process` (MCP dùng
        chung): quy trình gắn đề nghị bị tool trả `wrong_tool`, định kỳ trả `not_manual_trigger`."""
        args: dict[str, Any] = {"process_id": body["definition_id"], "idempotency_key": key}
        for k in ("subject_ref", "parameters"):
            if k in body:
                args[k] = body[k]
        started = await self._tool("start_process", args, ctx)
        if started.is_error:
            return outcome_response(started)
        env = started.envelope
        iid = _instance_id(env["data"].get("process_instance"))
        if iid is None:  # pragma: no cover - output contract đã kiểm process_instance
            raise RuntimeError("start_process without process_instance")
        href = _href("instances", iid)
        data = {**env["data"], "instance": {"id": iid, "href": href}}
        replayed = bool((env["meta"].get("receipt") or {}).get("replayed"))
        return _json(200 if replayed else 201, {**env, "data": data}, {"Location": href})

    async def _view(self, request: Request, ctx: CallContext) -> InstanceView:
        raw = str(request.path_params["instance_id"])
        try:
            iid = str(uuid.UUID(raw))
        except ValueError:
            raise _not_found() from None
        view = await anyio.to_thread.run_sync(self.backend.instance, ctx.actor, ctx.trace_id, iid)
        if view is None:
            raise _not_found()
        return view

    async def get_instance(self, request: Request, ctx: CallContext) -> Response:
        view = await self._view(request, ctx)
        iid = view.instance_id
        # IAM + RLS bản ghi qua đúng tool đọc; chỉ sau khi được phép mới trả trạng thái engine.
        got = await self._tool("get_record", {"ref": view.ref}, ctx)
        if got.is_error:
            code = got.envelope.get("error", {}).get("code")
            if code in (ErrorCode.NOT_FOUND.value, ErrorCode.ACCESS_DENIED.value):
                # Cùng tenant, không được đọc: giống hệt id không tồn tại (WA-07).
                raise _not_found()
            return outcome_response(got)
        env = got.envelope
        tasks = []
        for t in view.tasks:
            item: dict[str, Any] = {
                # Khóa thao tác = ref đề nghị (decisions.md câu 4), không phải id task của engine.
                "task_id": view.ref,
                "step": t.step,
                "name": t.name,
                "kind": t.kind,
                "outcomes": list(t.outcomes),
                "assignees": list(t.assignees),
            }
            tasks.append(item)
        links = {
            "self": _href("instances", iid),
            "definition": _href("definitions", view.process_id),
        }
        if any(t.kind == "decision" for t in view.tasks):
            links["decision"] = _href("tasks", view.ref, "decision")
        if view.status == "running":
            links["withdraw"] = _href("instances", iid, "withdraw")
        instance = {
            "id": iid,
            "process_id": view.process_id,
            "version": view.version,
            "status": view.status,
            "tasks": tasks,
            "links": links,
        }
        return _json(200, {**env, "data": {"instance": instance, "request": env["data"]["record"]}})

    async def withdraw_instance(self, request: Request, ctx: CallContext) -> Response:
        key = self._idempotency_key(request)
        body = await self._body(request, "withdraw_instance")
        try:
            iid = str(uuid.UUID(str(request.path_params["instance_id"])))
        except ValueError:
            raise _not_found() from None
        ref = await anyio.to_thread.run_sync(
            self.backend.instance_ref, ctx.actor, ctx.trace_id, iid
        )
        # WA-12: id ma/tenant khác vẫn đi qua đúng một lời gọi tool (ref không thể tồn tại) để
        # chi phí gần như lời gọi bị từ chối trên instance thật; mọi từ chối đều ra cùng 404.
        args: dict[str, Any] = {
            "ref": ref if ref is not None else f"process_instance:{iid}",
            "to_state": "withdrawn",
            "expected_revision": body["expected_revision"],
            "idempotency_key": key,
        }
        if "reason" in body:
            args["reason"] = body["reason"]
        out = await self._tool("transition_record", args, ctx)
        code = out.envelope.get("error", {}).get("code") if out.is_error else None
        if ref is None or code in (ErrorCode.NOT_FOUND.value, ErrorCode.ACCESS_DENIED.value):
            raise _not_found()
        return outcome_response(out)

    # ---- inbox + quyết định ----------------------------------------------------------------

    async def get_inbox(self, request: Request, ctx: CallContext) -> Response:
        q = request.query_params
        unknown = sorted(set(q) - {"kinds", "status", "due_before", "limit", "cursor"})
        if unknown:
            raise _invalid(
                "Unknown query parameters.",
                "schema",
                "Use kinds, status, due_before, limit, cursor.",
                parameters=unknown[:10],
            )
        args: dict[str, Any] = {}
        if "kinds" in q:
            args["kinds"] = [k for v in q.getlist("kinds") for k in v.split(",") if k]
        for name in ("status", "due_before", "cursor"):
            if name in q:
                args[name] = q[name]
        if "limit" in q:
            if not q["limit"].isdigit():
                raise _invalid("limit must be an integer.", "schema", "Send limit=1..100.")
            args["limit"] = int(q["limit"])
        out = await self._tool("get_inbox", args, ctx)
        if out.is_error:
            return outcome_response(out)
        env = out.envelope
        items = []
        for it in env["data"].get("items") or []:
            it = dict(it)
            if it.get("kind") == "approval" and isinstance(it.get("ref"), str):
                it["task_id"] = it["ref"]
                it["links"] = {"decision": _href("tasks", it["ref"], "decision")}
            items.append(it)
        return _json(200, {**env, "data": {**env["data"], "items": items}})

    async def decide_task(self, request: Request, ctx: CallContext) -> Response:
        task_id = str(request.path_params["task_id"])
        if not TASK_ID.fullmatch(task_id):
            raise _not_found()
        key = self._idempotency_key(request)
        body = await self._body(request, "decide_task")
        decision = DECISIONS[str(body["decision"])]
        if decision is None:
            raise _invalid(
                "request_info is not available through the workflow API in this release.",
                "decision_not_supported",
                "Use return_for_revision with a reason, or ask the requester outside xT.",
                allowed=[k for k, v in DECISIONS.items() if v is not None],
            )
        args: dict[str, Any] = {
            "ref": task_id,
            "decision": decision,
            "expected_revision": body["expected_revision"],
            "idempotency_key": key,
        }
        for k in ("reason", "delegate_to"):
            if k in body:
                args[k] = body[k]
        return outcome_response(await self._tool("decide_request", args, ctx))


def workflow_routes(
    dispatcher: ToolCaller,
    auth: WorkflowAuth,
    backend: WorkflowBackend,
    **options: Any,
) -> Mount:
    """`Mount("/workflow/v1")` để composition root ghép vào app Starlette của `xt-api`:

    `Starlette(routes=[workflow_routes(...), *extra, *mcp_app.routes], lifespan=...)`.
    `options`: `classify`, `ensure_active`, `clock`, `max_body_bytes`, `server_url`."""
    api = WorkflowApi(dispatcher, auth, backend, **options)
    return Mount(API_PREFIX, routes=api.routes())


__all__ = [
    "API_PREFIX",
    "DECISIONS",
    "HTTP_STATUS",
    "OPERATIONS",
    "AuthChallenge",
    "InstanceTask",
    "InstanceView",
    "ToolCaller",
    "WorkflowApi",
    "WorkflowAuth",
    "WorkflowBackend",
    "error_envelope",
    "openapi_document",
    "outcome_response",
    "workflow_routes",
]
