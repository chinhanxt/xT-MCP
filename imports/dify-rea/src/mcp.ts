// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import { Ajv } from 'ajv';
import type { Store } from './store.ts';
import type { McpConnector, McpTool, McpToolSelection } from './contracts.ts';
import { HttpError, object, text } from './validation.ts';
import { hashToken } from './http.ts';
export function connectorInput(value: unknown) {
  const v = object(value),
    url = new URL(text(v.url, 'URL MCP', 2000));
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new HttpError(400, 'URL MCP phải là HTTP(S), không chứa tài khoản hoặc query.');
  return {
    name: text(v.name, 'Tên MCP', 100),
    url: url.href,
    ...(v.token === undefined ? {} : { token: text(v.token, 'Bearer token', 4000, false) }),
  };
}
export function connector(store: Store, workspace: string, id: string): McpConnector & { token: string } {
  const row = store.db.prepare('SELECT * FROM mcp_connectors WHERE id=? AND workspace_id=?').get(id, workspace);
  if (!row) throw new HttpError(404, 'Không tìm thấy MCP connector.');
  return {
    id: String(row.id),
    name: String(row.name),
    url: String(row.url),
    enabled: !!row.enabled,
    has_key: !!row.secret,
    tools: JSON.parse(String(row.tools)),
    token: store.decrypt(String(row.secret)),
  };
}
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object')
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => [k, canonical(v)]),
    );
  return value;
}
function validator(schema: Record<string, unknown>) {
  const encoded = JSON.stringify(schema);
  if (encoded.length > 32000) throw new Error('Schema too large');
  let count = 0;
  function visit(value: unknown, depth: number) {
    if (++count > 5000 || depth > 24) throw new Error('Schema too complex');
    if (value && typeof value === 'object') for (const v of Object.values(value)) visit(v, depth + 1);
  }
  visit(schema, 0);
  return new Ajv({ strict: false, allErrors: false, validateFormats: false }).compile(schema);
}
async function session<T>(
  endpoint: { url: string; token?: string },
  signal: AbortSignal | undefined,
  action: (client: Client) => Promise<T>,
): Promise<T> {
  const deadline = AbortSignal.any([AbortSignal.timeout(30000), ...(signal ? [signal] : [])]);
  const client = new Client({ name: 'xt-studio', version: '0.1.0' });
  const transport = new StreamableHTTPClientTransport(new URL(endpoint.url), {
    requestInit: { headers: endpoint.token ? { Authorization: `Bearer ${endpoint.token}` } : {} },
    fetch: async (url, init) => {
      const response = await fetch(url, { ...init, redirect: 'error', signal: deadline });
      if (!response.body) return response;
      let bytes = 0;
      const body = response.body.pipeThrough(
        new TransformStream<Uint8Array, Uint8Array>({
          transform(chunk, controller) {
            bytes += chunk.length;
            if (bytes > 1024 * 1024) throw new Error('MCP response too large');
            controller.enqueue(chunk);
          },
        }),
      );
      return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
    },
  });
  try {
    await client.connect(transport, { signal: deadline, timeout: 30000 });
    return await action(client);
  } catch (error) {
    if (signal?.aborted) throw new HttpError(499, 'Đã dừng yêu cầu MCP.');
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, 'MCP không phản hồi hợp lệ; kiểm tra URL, token, schema và trạng thái server.');
  } finally {
    try {
      await transport.terminateSession();
    } catch {
      /* Server may not support session termination. */
    }
    await client.close().catch(() => {});
  }
}
async function inventory(client: Client, signal?: AbortSignal): Promise<McpTool[]> {
  const tools: McpTool[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < 5; page++) {
    const result = await client.listTools(cursor ? { cursor } : {}, { signal, timeout: 15000 });
    for (const tool of result.tools) {
      const name = text(tool.name, 'Tên tool MCP', 128);
      if (tools.some((t) => t.name === name) || tools.length >= 100) throw new HttpError(502, 'MCP có tool trùng tên hoặc quá 100 tool.');
      validator(tool.inputSchema);
      tools.push({
        name,
        description: text(tool.description ?? '', 'Mô tả tool', 8000, false),
        input_schema: tool.inputSchema,
        schema_hash: hashToken(JSON.stringify(canonical(tool.inputSchema))),
      });
    }
    if (!result.nextCursor) return tools;
    cursor = result.nextCursor;
  }
  throw new HttpError(502, 'MCP có quá nhiều trang công cụ.');
}
export async function discoverTools(endpoint: { url: string; token?: string }, signal?: AbortSignal) {
  return session(endpoint, signal, (client) => inventory(client, signal));
}
export function selectedTool(store: Store, workspace: string, selection: McpToolSelection) {
  const source = connector(store, workspace, selection.connector_id);
  if (!source.enabled) throw new HttpError(409, 'MCP connector đã tắt.');
  const tool = source.tools.find((t) => t.name === selection.tool_name && t.schema_hash === selection.schema_hash);
  if (!tool) throw new HttpError(409, 'Tool MCP đã thay đổi; cập nhật danh sách và chọn lại tool trong ứng dụng.');
  return { source, tool };
}
export async function callMcp(
  store: Store,
  workspace: string,
  selection: McpToolSelection,
  args: Record<string, unknown>,
  signal?: AbortSignal,
) {
  const { source, tool } = selectedTool(store, workspace, selection);
  if (JSON.stringify(args).length > 32000 || !validator(tool.input_schema)(args))
    throw new HttpError(400, 'Tham số tool MCP không đúng schema.');
  return session(source, signal, async (client) => {
    const live = (await inventory(client, signal)).find((t) => t.name === tool.name);
    if (!live || live.schema_hash !== selection.schema_hash)
      throw new HttpError(409, 'Schema tool MCP đã đổi; cập nhật connector và chọn lại tool.');
    const result = await client.callTool({ name: tool.name, arguments: args }, undefined, { signal, timeout: 15000 });
    const output = JSON.stringify({
      content: result.content,
      structuredContent: result.structuredContent,
      isError: result.isError ?? false,
    });
    if (output.length > 64000) throw new HttpError(502, 'Kết quả tool MCP vượt quá 64.000 ký tự.');
    return { output, isError: result.isError === true };
  });
}
