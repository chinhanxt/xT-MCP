# SPDX-License-Identifier: Apache-2.0
"""MCP server (SDK `mcp` 2.x) với mọi tool đi qua làn PII + MCP App thẻ bản ghi + REST /pii/reveal."""
from __future__ import annotations

import json
import pathlib
import sqlite3
from typing import Any, Callable

from mcp.server.apps import Apps, ResourceCsp
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver import MCPServer
from mcp_types import CallToolResult, TextContent
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .lane import Audit, LaneError, PiiLane, ToolShape
from .policy import ClientRegistry, Principal, TenantPolicy, principal_from_claims
from .reveal import RevealService
from .store import Store
from .vault import Vault

CARD_URI = "ui://xt/pii-card.html"
UI_DIR = pathlib.Path(__file__).parent / "ui"


def principal_from_request(registry: ClientRegistry) -> Callable[[], Principal]:
    """Production: claim của access token đã được TokenVerifier (Keycloak JWKS) kiểm."""
    def get() -> Principal:
        at = get_access_token()
        if at is None:
            raise LaneError("UNAUTHENTICATED", "Thiếu access token")
        return principal_from_claims({**(at.claims or {}), "azp": at.client_id, "sub": at.subject}, registry)
    return get


class App:
    def __init__(self, conn: sqlite3.Connection, policy: TenantPolicy, principal: Callable[[], Principal],
                 public_url: str = "https://xt.example", sandbox_origins: frozenset[str] = frozenset()):
        self.conn, self.policy, self.principal = conn, policy, principal
        self.store, self.vault, self.audit = Store(conn), Vault(conn), Audit(conn)
        self.lane = PiiLane(self.store, self.vault, self.audit, policy)
        self.reveal = RevealService(conn, self.vault, self.audit, policy, sandbox_origins)
        self.mcp = self._build(public_url, sandbox_origins)

    def _result(self, env: dict, is_error: bool) -> CallToolResult:
        # content (cho model) và structuredContent (Claude cũng đưa vào model) đều là bản ĐÃ chiếu
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(env, ensure_ascii=False))],
                              structured_content=env, is_error=is_error)

    def _run(self, tool: str, shape: ToolShape, fn: Callable[..., Any], args: dict) -> CallToolResult:
        try:
            p = self.principal()
        except LaneError as e:
            return self._result({"ok": False, "error": {"code": e.code, "message": e.message}}, True)
        return self._result(*self.lane.call(p, tool, shape, fn, args))

    def _build(self, public_url: str, sandbox_origins: frozenset[str]) -> MCPServer:
        apps, st = Apps(), self.store
        html = (UI_DIR / "pii-card.html").read_text().replace("__XT_PUBLIC_URL__", public_url)
        apps.add_html_resource(CARD_URI, html, name="pii_card", description="Thẻ bản ghi; giá trị PII chỉ hiện cho người",
                               csp=ResourceCsp(connect_domains=[public_url]), prefers_border=True)

        @apps.tool(resource_uri=CARD_URI, description="Đọc một bản ghi theo mã. Field PII trả về dạng token ⟨MÃ.trường⟩.")
        def get_record(model: str, key: str) -> CallToolResult:
            return self._run("get_record", ToolShape(), lambda model, key: {"model": model, "record": st.get(model, key)},
                             {"model": model, "key": key})

        @apps.tool(resource_uri=CARD_URI, visibility=["app"], description="(UI) Xin vé xem giá trị thật")
        def pii_reveal_ticket(tokens: list[str], pubkey_jwk: dict[str, Any]) -> CallToolResult:
            try:
                r = self.reveal.mint(self.principal(), tokens, pubkey_jwk)
                return CallToolResult(content=[TextContent(type="text", text=json.dumps(
                                          {"ok": True, "expires_in": r["expires_in"]}))],
                                      structured_content={"ok": True, "expires_in": r["expires_in"]},
                                      meta={"xt/reveal": {"ticket": r["ticket"], "url": f"{public_url}/pii/reveal"}})
            except LaneError as e:
                return self._result({"ok": False, "error": {"code": e.code, "message": e.message}}, True)

        # Lưu ý: mọi @apps.tool phải khai báo TRƯỚC MCPServer(extensions=[apps]), khai sau sẽ không được đăng ký
        mcp = MCPServer("xt-pii-spike", instructions=(
            "Dữ liệu cá nhân trả về dạng token ⟨MÃ.trường⟩. Truyền nguyên token vào tool khi cần dùng giá trị; "
            "không yêu cầu người dùng đọc giá trị thật vào chat."), extensions=[apps])

        @mcp.tool(description="Tìm bản ghi. filter {field: value | {op, value}}; field nhạy cảm chỉ lọc eq bằng token.")
        def search_records(model: str, filter: dict[str, Any] | None = None, sort: list[str] | None = None,
                           limit: int = 20) -> CallToolResult:
            return self._run("search_records", ToolShape(filter_args=("filter",), sort_args=("sort",)),
                             lambda model, filter, sort, limit: {"model": model, "records": st.search(model, filter, sort, limit)},
                             {"model": model, "filter": filter, "sort": sort, "limit": limit})

        @mcp.tool(description="Tạo bản ghi. Giá trị có thể là token ⟨MÃ.trường⟩.")
        def create_record(model: str, data: dict[str, Any]) -> CallToolResult:
            return self._run("create_record", ToolShape(data_args=("data",)),
                             lambda model, data: {"model": model, "record": st.create(model, data)},
                             {"model": model, "data": data})

        @mcp.tool(description="Cập nhật bản ghi theo mã. Giá trị có thể là token ⟨MÃ.trường⟩.")
        def update_record(model: str, key: str, patch: dict[str, Any]) -> CallToolResult:
            return self._run("update_record", ToolShape(data_args=("patch",)),
                             lambda model, key, patch: {"model": model, "record": st.update(model, key, patch)},
                             {"model": model, "key": key, "patch": patch})

        @mcp.custom_route("/pii/reveal", methods=["POST", "OPTIONS"])
        async def reveal(request: Request) -> Response:
            origin = request.headers.get("origin")
            cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
                    "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "content-type"} \
                if origin in sandbox_origins else {}
            hdr = {**cors, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
            if request.method == "OPTIONS":
                return Response(status_code=204 if cors else 403, headers=hdr)
            try:
                b = await request.json()
                out = self.reveal.redeem(str(b["ticket"]), int(b["ts"]), str(b["sig"]), origin)
                return JSONResponse(out, headers=hdr)
            except LaneError as e:
                return JSONResponse({"error": e.code, "message": e.message}, status_code=403, headers=hdr)
            except (KeyError, ValueError, TypeError):
                return JSONResponse({"error": "BAD_REQUEST"}, status_code=400, headers=hdr)

        return mcp
