# SPDX-License-Identifier: Apache-2.0
"""Chạy server spike cho e2e trên basic-host (host tham chiếu, không có OAuth).

basic-host không làm OAuth nên harness gán CỐ ĐỊNH principal tier cloud (azp DCR giả lập) để kiểm đúng
đường khó nhất; production lấy principal từ JWT qua `principal_from_request`.
"""
import pathlib
import sqlite3
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tests"))

import uvicorn  # noqa: E402
from starlette.middleware.cors import CORSMiddleware  # noqa: E402

from conftest import REGISTRY, SEED  # noqa: E402
from xtpii.policy import Principal, TenantPolicy  # noqa: E402
from xtpii.server import App  # noqa: E402

SANDBOX_ORIGINS = frozenset({"http://localhost:18212"})
P = Principal(tenant="t1", azp="5c1e7a0e-dcr-claude", sub="u-hr-01", sid="sid-E2E", roles=frozenset({"hr"}),
              tier=REGISTRY.tier_of("5c1e7a0e-dcr-claude"))
conn = sqlite3.connect(pathlib.Path(__file__).with_name("e2e.db"), check_same_thread=False)
app = App(conn, TenantPolicy(), lambda: P, public_url="http://localhost:18210", sandbox_origins=SANDBOX_ORIGINS)
for r in SEED:
    app.store.create("nhan_vien", r)
conn.commit()
inner = app.mcp.streamable_http_app(streamable_http_path="/mcp")
# CORS cho /mcp chỉ cần vì basic-host gọi MCP từ trình duyệt (host thương mại gọi từ backend).
# /pii/* tự xử lý CORS theo allowlist sandbox của nó, không đi qua middleware chung.
mcp_cors = CORSMiddleware(inner, allow_origins=["http://localhost:18211"], allow_methods=["*"], allow_headers=["*"],
                          expose_headers=["Mcp-Session-Id", "Mcp-Protocol-Version"])


async def asgi(scope, receive, send):
    if scope["type"] == "http" and scope["path"].startswith("/pii/"):
        return await inner(scope, receive, send)
    return await mcp_cors(scope, receive, send)

uvicorn.run(asgi, host="127.0.0.1", port=18210, log_level="warning", lifespan="on")
