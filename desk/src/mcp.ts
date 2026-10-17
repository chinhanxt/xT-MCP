// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import { CfWorkerJsonSchemaValidator } from "@modelcontextprotocol/sdk/validation/cfworker";
import type { McpTool } from "./types";

/**
 * Seam xác thực: v0.1 Keycloak login sẽ cung cấp hàm này (trả header Authorization của người dùng).
 * Không có bearer tĩnh trong cấu hình/proxy; xem src/auth.ts (fail closed khi chưa đăng nhập).
 */
export type AuthHeaders = (req?: { method: string; url: string }) => Promise<Record<string, string>> | Record<string, string>;

export interface McpConnection {
  client: Client;
  tools: McpTool[];
}

export async function connectMcp(url: URL, authHeaders: AuthHeaders): Promise<McpConnection> {
  const client = new Client({ name: "xt-desk", version: "0.1.0" }, {
    jsonSchemaValidator: new CfWorkerJsonSchemaValidator(),
  });
  const authedFetch: typeof fetch = async (input, init) => {
    // mỗi request HTTP một proof DPoP mới (htm/htu của chính request); ném lỗi (chưa đăng nhập) => request không được gửi
    const raw = input instanceof Request ? input.url : String(input);
    const extra = await authHeaders({
      method: init?.method ?? (input instanceof Request ? input.method : "GET"),
      url: new URL(raw, globalThis.location?.href).href,
    });
    const headers = new Headers(init?.headers);
    for (const [k, v] of Object.entries(extra)) headers.set(k, v);
    return fetch(input, { ...init, headers });
  };
  await client.connect(new StreamableHTTPClientTransport(url, { fetch: authedFetch }));
  const { tools } = await client.listTools();
  return { client, tools: tools as unknown as McpTool[] };
}
