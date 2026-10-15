# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 xT contributors
"""MCP Apps của xT (`ui://xt/orgchart`, `approval-card`, `record-table`): HTML/SVG/JS thuần.

Composition root gọi `register_apps(...)` rồi truyền `bundle.ui_resources` / `bundle.app_tools`
vào `XtMcpServer(ui_resources=..., app_tools=...)`. Liên kết tool <-> view lấy từ contract
(`ToolContract.ui_resource`, `contracts.tools.apps`), không khai lại ở đây.
"""

from xt.adapters.mcp.apps.registry import AppsBundle, register_apps

__all__ = ["AppsBundle", "register_apps"]
