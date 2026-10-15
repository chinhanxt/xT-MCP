// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Cấu hình đến từ vite.config.ts (đọc env lúc build/serve). Không có giá trị mặc định cứng ở đây.
export const config = __DESK_CONFIG__;
export const MCP_PATH = "/mcp"; // proxy cùng origin tới DESK_MCP_URL (gắn bearer phía server)
export const LLM_PATH = "/llm"; // proxy cùng origin tới DESK_LLM_URL
