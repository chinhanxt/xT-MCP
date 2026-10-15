# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""Server MCP xT THẬT (XtMcpServer + MCP Apps) trong tiến trình, dữ liệu từ port giả của test.

Chỉ phục vụ e2e Desk: chấp nhận mọi bearer (đăng nhập thật do Keycloak giả của e2e lo), cộng ba
route điều khiển: `GET /__calls` (nhật ký tool), `POST /__confirm_base` (đổi origin của confirm_url,
để thử confirm_url ngoài origin xT), `GET /desk/confirm/{id}` (trang xác nhận giả).
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import uvicorn  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import HTMLResponse, JSONResponse, Response  # noqa: E402
from starlette.routing import Route  # noqa: E402

from tests.unit.mcp.apps_support import build_apps_harness  # noqa: E402
from tests.unit.mcp.conftest import principal  # noqa: E402
from xt.adapters.mcp import AppTool  # noqa: E402


class AnyToken(dict):  # type: ignore[type-arg]
    def get(self, key: Any, default: Any = None) -> Any:
        return principal("sub-mai", client="xt-desk-agent")


def main() -> None:
    port = int(os.environ["E2E_XT_PORT"])
    desk_origin = os.environ[
        "E2E_DESK_ORIGIN"
    ]  # origin của Desk e2e (Origin header của trình duyệt)
    origin = f"http://127.0.0.1:{port}"
    h = build_apps_harness(
        origin,
        allowed_hosts=[f"127.0.0.1:{port}"],
        allowed_origins=[desk_origin],
        tokens=AnyToken(),
    )
    calls: list[dict[str, Any]] = []
    d = h.server.dispatcher
    orig_call = d.call

    def logged_call(name: str, arguments: Any, ctx: Any, **kw: Any) -> Any:
        calls.append({"via": "dispatcher", "tool": name, "args": dict(arguments or {})})
        return orig_call(name, arguments, ctx, **kw)

    d.call = logged_call  # type: ignore[method-assign]
    for name, t in list(h.server._app_tools.items()):

        def wrap(n: str, run: Any) -> Any:
            def logged(ctx: Any, args: Any, dups: Any) -> Any:
                calls.append({"via": "app", "tool": n, "args": dict(args)})
                return run(ctx, args, dups)

            return logged

        h.server._app_tools[name] = AppTool(t.contract, wrap(name, t.run))

    async def get_calls(request: Request) -> Response:
        return JSONResponse(calls)

    async def set_confirm_base(request: Request) -> Response:
        url = (await request.json()).get("url") or origin
        d.settings = replace(d.settings, confirm_base_url=url)
        return JSONResponse({"confirm_base_url": url})

    async def confirm_page(request: Request) -> Response:
        return HTMLResponse(
            "<!doctype html><title>xT xác nhận</title><h1>Trang xác nhận xT (giả)</h1>"
        )

    h.app.router.routes[:0] = [
        Route("/__calls", get_calls),
        Route("/__confirm_base", set_confirm_base, methods=["POST"]),
        Route("/desk/confirm/{cid}", confirm_page),
    ]
    json.dump({"ready": True}, sys.stderr)
    uvicorn.run(h.app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
