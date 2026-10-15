# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Server MCP của xT trên SDK chính thức `mcp` 2.x (lowlevel `Server`).

Dùng lowlevel thay vì `MCPServer` + decorator: catalog chiếu từ ToolContract (schema, outputSchema,
annotations, title), không suy từ chữ ký hàm Python; và tránh lỗi thứ tự của extension Apps
(tool khai sau `MCPServer(extensions=[apps])` bị bỏ qua im lặng, research/spike-mcp-apps.md).
`_meta.ui.resourceUri` chỉ gắn khi resource `ui://` tương ứng được đăng ký thật.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any

import anyio.to_thread
import mcp_types as types
from mcp.server.context import ServerRequestContext
from mcp.server.lowlevel import Server
from mcp.shared.exceptions import MCPError

from xt import __version__
from xt.adapters.mcp.instructions import instructions_for
from xt.adapters.mcp.strict_body import DUPLICATE_KEYS_META
from xt.application.tools import (
    Actor,
    CallContext,
    ClientClass,
    ToolDispatcher,
    ToolError,
    ToolOutcome,
)
from xt.application.tools.errors import invalid_request
from xt.application.tools.safe_log import log_failure
from xt.contracts.prompt_catalog import PromptContract, PromptError
from xt.contracts.tools import (
    APP_TOOLS_BY_NAME,
    CATALOG,
    AppToolContract,
    ErrorCode,
    Profile,
    ToolContract,
    profile_tools,
    profiles_of,
    success_envelope,
    title_from_name,
)
from xt.domain.autonomy import RiskLevel
from xt.domain.identity import IdentityError, Principal, PrincipalKind

log = logging.getLogger("xt.mcp")
APP_MIME_TYPE = "text/html;profile=mcp-app"  # MCP Apps (io.modelcontextprotocol/ui)
PROVENANCE = {"provenance": {"untrusted_content": True, "source": "xt_business_data"}}


@dataclass(frozen=True, slots=True)
class Caller:
    """Danh tính đã kiểm của một lời gọi. `token` chỉ để kiểm thu hồi (introspection)."""

    principal: Principal
    token: str


# Trả Caller, hoặc lỗi danh tính (token sai/hết hạn), hoặc None (không có token).
CallerResolver = Callable[
    [ServerRequestContext[Any, Any]], Awaitable[Caller | IdentityError | None]
]
RevocationCheck = Callable[[Principal, str, RiskLevel], None]


@dataclass(frozen=True, slots=True)
class UiResource:
    uri: str
    name: str
    html: str
    description: str = ""
    csp: Mapping[str, Sequence[str]] | None = (
        None  # _meta.ui.csp; None = CSP mặc định chặt của host
    )
    prefers_border: bool = False

    @property
    def meta(self) -> dict[str, Any]:
        ui: dict[str, Any] = {"prefersBorder": self.prefers_border}
        if self.csp is not None:
            ui["csp"] = {k: list(v) for k, v in self.csp.items()}
        return {"ui": ui}

    def __post_init__(self) -> None:
        if not self.uri.startswith("ui://"):
            raise ValueError(f"UI resource phải dùng scheme ui://: {self.uri}")


@dataclass(frozen=True, slots=True)
class AppTool:
    """Tool CHỈ DÀNH CHO APP (visibility = app). `run(ctx, args, dups)` chạy trong thread worker."""

    contract: AppToolContract
    run: Callable[[CallContext, Mapping[str, Any], Sequence[str]], ToolOutcome]


@dataclass(frozen=True, slots=True)
class ClientClassifier:
    """Lớp client cho làn PII (§3.8), suy từ `azp`; client lạ = `cloud` (fail-closed)."""

    local_clients: frozenset[str] = frozenset()

    def __call__(self, principal: Principal) -> ClientClass:
        if principal.kind is PrincipalKind.HUMAN:
            return ClientClass.HUMAN
        if principal.client_id in self.local_clients:
            return ClientClass.LOCAL
        return ClientClass.CLOUD


CLOUD_BY_DEFAULT = ClientClassifier()


def tool_definition(contract: ToolContract, ui_uris: frozenset[str]) -> types.Tool:
    """Chiếu một ToolContract thành entry `tools/list` (title + annotations sinh tự động)."""
    a = contract.annotations
    meta: dict[str, Any] = {
        "xt/since": contract.since,
        "xt/profiles": profiles_of(contract.name),
    }
    if contract.ui_resource and contract.ui_resource in ui_uris:
        meta["ui"] = {"resourceUri": contract.ui_resource}
    return types.Tool(
        name=contract.name,
        title=contract.title,
        description=contract.description,
        input_schema=json.loads(json.dumps(contract.input_schema)),
        output_schema=success_envelope(contract.output_schema),
        annotations=types.ToolAnnotations(
            title=contract.title,
            read_only_hint=a.read_only,
            destructive_hint=a.destructive,
            idempotent_hint=a.idempotent,
            open_world_hint=a.open_world,
        ),
        _meta=meta,
    )


def app_tool_definition(tool: AppTool) -> types.Tool:
    c = tool.contract
    return types.Tool(
        name=c.name,
        title=title_from_name(c.name),
        description=c.description,
        input_schema=json.loads(json.dumps(c.input_schema)),
        output_schema=success_envelope(c.output_schema),
        annotations=types.ToolAnnotations(
            title=title_from_name(c.name),
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        _meta={"ui": {"resourceUri": c.ui_resource, "visibility": ["app"]}},
    )


def to_call_tool_result(outcome: ToolOutcome) -> types.CallToolResult:
    text = json.dumps(outcome.envelope, ensure_ascii=False, separators=(",", ":"))
    content: list[types.ContentBlock] = [types.TextContent(type="text", text=text)]
    if outcome.is_error:
        # Lỗi: không structuredContent (outputSchema chỉ mô tả thành công, §3.6).
        return types.CallToolResult(content=content, is_error=True, _meta=PROVENANCE)
    return types.CallToolResult(
        content=content, structured_content=outcome.envelope, is_error=False, _meta=PROVENANCE
    )


class XtMcpServer:
    def __init__(
        self,
        dispatcher: ToolDispatcher,
        resolve_caller: CallerResolver,
        *,
        prompts: Sequence[PromptContract],
        profile: Profile = Profile.FULL,
        classify: Callable[[Principal], ClientClass] = CLOUD_BY_DEFAULT,
        ensure_active: RevocationCheck | None = None,
        ui_resources: Sequence[UiResource] = (),
        app_tools: Sequence[AppTool] = (),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.dispatcher = dispatcher
        self._resolve = resolve_caller
        self._classify = classify
        self._ensure_active = ensure_active
        self._clock = clock
        self._ui = {r.uri: r for r in ui_resources}
        known_ui = {c.ui_resource for c in CATALOG if c.ui_resource}
        known_ui |= {t.ui_resource for t in APP_TOOLS_BY_NAME.values()}
        stray = set(self._ui) - known_ui
        if stray:
            raise ValueError(f"UI resource không gắn với tool nào: {sorted(stray)}")
        self.tools = profile_tools(profile)
        self._tool_names = {c.name for c in self.tools}
        ui_uris = frozenset(self._ui)
        self._listed = [tool_definition(c, ui_uris) for c in self.tools]
        # Tool app-only chỉ khai khi App của nó đã đăng ký VÀ có tool của model dùng view đó
        # (hồ sơ core-10 không có view thì cũng không có tool app-only mồ côi).
        shown_ui = {c.ui_resource for c in self.tools if c.ui_resource} & set(self._ui)
        self._app_tools: dict[str, AppTool] = {}
        for at in app_tools:
            if APP_TOOLS_BY_NAME.get(at.contract.name) is not at.contract:
                raise ValueError(f"Tool app-only chưa có trong contract: {at.contract.name}")
            if at.contract.ui_resource not in self._ui:
                raise ValueError(
                    f"Tool app-only {at.contract.name} thiếu resource ui:// đã đăng ký"
                )
            if at.contract.name in self._app_tools or at.contract.name in {c.name for c in CATALOG}:
                raise ValueError(f"Tên tool app-only bị trùng: {at.contract.name}")
            if at.contract.ui_resource in shown_ui:
                self._app_tools[at.contract.name] = at
                self._listed.append(app_tool_definition(at))
        # Prompt chỉ giữ bước có tool đang quảng bá; prompt không còn bước nào thì bỏ (core-10).
        self._prompts = {}
        for p in prompts:
            narrowed = p.restricted_to(self._tool_names)
            if narrowed is not None:
                self._prompts[p.name] = narrowed
        self.server: Server[Any] = Server(
            "xt",
            version=__version__,
            title="xT",
            description="Agent-native DX-OS: một bộ tool cố định cho mọi doanh nghiệp.",
            instructions=instructions_for(self._tool_names),
            on_list_tools=self._list_tools,
            on_call_tool=self._call_tool,
            on_list_prompts=self._list_prompts,
            on_get_prompt=self._get_prompt,
            on_list_resources=self._list_resources if self._ui else None,
            on_read_resource=self._read_resource if self._ui else None,
        )

    # ---- tools -----------------------------------------------------------------------------

    async def _list_tools(
        self, ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        # Catalog cố định: không đổi theo người dùng hay dữ liệu (architecture-v2 §1.3).
        return types.ListToolsResult(tools=self._listed)

    async def _call_tool(
        self, ctx: ServerRequestContext[Any, Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        trace_id = "tr_" + uuid.uuid4().hex[:20]
        try:
            return await self._call_tool_inner(ctx, params, trace_id)
        except Exception as e:
            # M3: không để SDK đổi ngoại lệ thành MCPError(str(e)) (lộ chi tiết nội bộ).
            log_failure(log, "mcp adapter failure", e, trace_id=trace_id)
            return to_call_tool_result(
                self.dispatcher.reject(
                    None,
                    trace_id,
                    ToolError(ErrorCode.INTERNAL_ERROR, "Unexpected server failure."),
                )
            )

    async def _call_tool_inner(
        self,
        ctx: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
        trace_id: str,
    ) -> types.CallToolResult:
        name = params.name
        caller = await self._resolve(ctx)
        if not isinstance(caller, Caller):
            return to_call_tool_result(
                self.dispatcher.reject(
                    params.name,
                    trace_id,
                    ToolError(ErrorCode.UNAUTHENTICATED, "Sign-in required or token invalid."),
                )
            )
        principal = caller.principal
        if principal.kind is PrincipalKind.HUMAN:
            # M6: không có "người trực tiếp" trên MCP; phiên người chỉ qua trang xác nhận.
            return to_call_tool_result(
                self.dispatcher.reject(
                    params.name,
                    trace_id,
                    ToolError(
                        ErrorCode.UNAUTHENTICATED,
                        "Human session tokens are not accepted on the MCP endpoint.",
                        remediation="Connect through an agent client (e.g. xt-desk-agent).",
                    ),
                )
            )
        tenant = principal.tenant
        if tenant is None:
            err = (
                ToolError(
                    ErrorCode.ACCESS_DENIED,
                    "Your account belongs to several organizations; xT cannot pick one.",
                    details={"reason": "tenant_ambiguous"},
                )
                if principal.tenant_ambiguous
                else ToolError(
                    ErrorCode.UNAUTHENTICATED,
                    "Your account is not a member of any xT organization.",
                    remediation="Ask the xT administrator to add your account to the organization.",
                )
            )
            return to_call_tool_result(self.dispatcher.reject(params.name, trace_id, err))
        app_tool = self._app_tools.get(name)
        if app_tool is None and name not in self._tool_names:
            # Tool ngoài hồ sơ đang quảng bá: cùng câu trả lời như tool không tồn tại.
            err = invalid_request(
                f"Unknown tool {name[:64]!r}.",
                "wrong_tool",
                remediation="Use one of the tools from tools/list.",
                tools=sorted(self._tool_names),
            )
            return to_call_tool_result(self.dispatcher.reject(None, trace_id, err))
        call_ctx = CallContext(
            actor=Actor.from_principal(principal, tenant),
            client_class=self._classify(principal),
            trace_id=trace_id,
            now=self._clock(),
        )
        if self._ensure_active is not None:
            call_ctx = replace(
                call_ctx, ensure_active=partial(self._ensure_active, principal, caller.token)
            )
        meta = params.meta if isinstance(params.meta, Mapping) else {}
        raw_dups = meta.get(DUPLICATE_KEYS_META) or ()
        dups = [str(k) for k in raw_dups] if isinstance(raw_dups, list | tuple) else ["?"]
        arguments = dict(params.arguments or {})
        if app_tool is not None:
            run = app_tool.run
            return to_call_tool_result(
                await anyio.to_thread.run_sync(lambda: run(call_ctx, arguments, dups))
            )
        outcome = await anyio.to_thread.run_sync(
            lambda: self.dispatcher.call(name, arguments, call_ctx, duplicate_keys=dups)
        )
        return to_call_tool_result(outcome)

    # ---- prompts ---------------------------------------------------------------------------

    async def _list_prompts(
        self, ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
    ) -> types.ListPromptsResult:
        return types.ListPromptsResult(
            prompts=[
                types.Prompt(
                    name=p.name,
                    title=p.title,
                    description=p.description,
                    arguments=[
                        types.PromptArgument(
                            name=a.name, description=a.description, required=a.required
                        )
                        for a in p.arguments
                    ],
                )
                for p in self._prompts.values()
            ]
        )

    async def _get_prompt(
        self, ctx: ServerRequestContext[Any, Any], params: types.GetPromptRequestParams
    ) -> types.GetPromptResult:
        p = self._prompts.get(params.name)
        if p is None:
            raise MCPError(types.INVALID_PARAMS, f"unknown prompt: {params.name}")
        try:
            text = p.render(params.arguments)
        except PromptError as e:
            raise MCPError(types.INVALID_PARAMS, str(e)) from e
        except Exception as e:
            log_failure(log, "prompt render failure", e, prompt=params.name)
            raise MCPError(types.INTERNAL_ERROR, "Unexpected server failure.") from None
        return types.GetPromptResult(
            description=p.description,
            messages=[
                types.PromptMessage(role="user", content=types.TextContent(type="text", text=text))
            ],
        )

    # ---- UI resources (MCP Apps) -----------------------------------------------------------

    async def _list_resources(
        self, ctx: ServerRequestContext[Any, Any], params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        return types.ListResourcesResult(
            resources=[
                types.Resource(
                    uri=r.uri,
                    name=r.name,
                    description=r.description,
                    mime_type=APP_MIME_TYPE,
                    _meta=r.meta,
                )
                for r in self._ui.values()
            ]
        )

    async def _read_resource(
        self, ctx: ServerRequestContext[Any, Any], params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        r = self._ui.get(str(params.uri))
        if r is None:
            raise MCPError(types.INVALID_PARAMS, f"unknown resource: {params.uri}")
        return types.ReadResourceResult(
            contents=[
                types.TextResourceContents(
                    uri=r.uri, mime_type=APP_MIME_TYPE, text=r.html, _meta=r.meta
                )
            ]
        )
