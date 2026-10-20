// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { AppConfig, AppKind, Graph, Model, ToolName } from './contracts.ts';
import { nodeKinds, xtToolNames } from './contracts.ts';
import { nodeDataInput } from './workflow-nodes.ts';
import { fieldDefinitions, generationInput } from './input-fields.ts';
export class HttpError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}
export function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new HttpError(400, 'Cần một JSON object.');
  return value as Record<string, unknown>;
}
export function text(value: unknown, field: string, max = 1000, required = true): string {
  if (typeof value !== 'string' || value.length > max || (required && !value.trim()))
    throw new HttpError(400, `${field} không hợp lệ (tối đa ${max} ký tự).`);
  return value;
}
export function choice<T extends string>(value: unknown, values: readonly T[], field: string): T {
  if (typeof value !== 'string' || !values.includes(value as T)) throw new HttpError(400, `${field} không hợp lệ.`);
  return value as T;
}
export function strings(value: unknown, field: string, max = 30): string[] {
  if (!Array.isArray(value) || value.length > max) throw new HttpError(400, `${field} không hợp lệ.`);
  return value.map((x) => text(x, field, 128));
}
export const kind = (value: unknown): AppKind => choice(value, ['chat', 'workflow', 'agent'], 'Loại ứng dụng');
export function modelInput(value: unknown): Omit<Model, 'id' | 'has_key'> & { api_key?: string } {
  const v = object(value);
  const base = text(v.base_url, 'URL', 2000).replace(/\/+$/, '');
  let url: URL;
  try {
    url = new URL(base);
  } catch {
    throw new HttpError(400, 'URL model không hợp lệ.');
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new HttpError(400, 'URL model phải là HTTP(S), không chứa tài khoản hoặc query.');
  if (v.provider === 'anthropic' && v.kind === 'embedding') throw new HttpError(400, 'Anthropic native chỉ hỗ trợ chat.');
  return {
    name: text(v.name, 'Tên', 100),
    provider: choice(v.provider, ['openai', 'ollama', 'anthropic'], 'Provider'),
    base_url: base,
    model: text(v.model, 'Model', 200),
    kind: choice(v.kind, ['chat', 'embedding'], 'Loại model'),
    ...(v.api_key === undefined ? {} : { api_key: text(v.api_key, 'API key', 4000, false) }),
  };
}
export function configInput(value: unknown): AppConfig {
  const v = object(value);
  const xt_tools = strings(v.xt_tools ?? [], 'Công cụ xT', 11);
  if (xt_tools.some((name) => !xtToolNames.includes(name)) || new Set(xt_tools).size !== xt_tools.length)
    throw new HttpError(400, 'Công cụ xT không hợp lệ hoặc bị trùng.');
  const steps = Number(v.max_steps);
  if (!Number.isInteger(steps) || steps < 1 || steps > 8) throw new HttpError(400, 'Agent cần từ 1–8 bước.');
  const tools = strings(v.tools, 'Tools', 2).map((t) => choice<ToolName>(t, ['knowledge_search', 'calculator'], 'Tool'));
  const remote = v.mcp_tools ?? [];
  if (!Array.isArray(remote) || remote.length > 16) throw new HttpError(400, 'Tối đa 16 tool MCP cho mỗi ứng dụng.');
  const mcp_tools = remote.map((raw) => {
    const t = object(raw);
    const selected = {
      connector_id: text(t.connector_id, 'MCP connector', 128),
      tool_name: text(t.tool_name, 'MCP tool', 128),
      schema_hash: text(t.schema_hash, 'MCP schema', 64),
    };
    if (!/^[a-f0-9]{64}$/.test(selected.schema_hash)) throw new HttpError(400, 'MCP schema hash không hợp lệ.');
    return selected;
  });
  if (new Set(mcp_tools.map((t) => JSON.stringify([t.connector_id, t.tool_name]))).size !== mcp_tools.length)
    throw new HttpError(400, 'Tool MCP bị trùng.');
  const g = object(v.graph);
  if (!Array.isArray(g.nodes) || !Array.isArray(g.edges) || g.nodes.length > 50 || g.edges.length > 100)
    throw new HttpError(400, 'Workflow quá lớn hoặc không hợp lệ.');
  const graph: Graph = {
    nodes: g.nodes.map((raw) => {
      const n = object(raw),
        d = object(n.data),
        p = object(n.position);
      const node = {
        id: text(n.id, 'Node ID', 64),
        type: choice(n.type, nodeKinds, 'Node'),
        position: { x: Number(p.x), y: Number(p.y) },
        data: { label: text(d.label, 'Nhãn', 100) },
      } as Graph['nodes'][number];
      if (!/^[a-zA-Z0-9_-]+$/.test(node.id) || !Number.isFinite(node.position.x) || !Number.isFinite(node.position.y))
        throw new HttpError(400, 'ID hoặc vị trí node không hợp lệ.');
      for (const key of ['prompt', 'model_id', 'knowledge_id', 'variable', 'value', 'template', 'url', 'body'] as const)
        if (d[key] !== undefined) node.data[key] = text(d[key], key, 20000, false);
      if (d.operator !== undefined) node.data.operator = choice<'contains' | 'equals'>(d.operator, ['contains', 'equals'], 'Điều kiện');
      nodeDataInput(d, node.data);
      if (d.candidates !== undefined) node.data.candidates = strings(d.candidates, 'Biến gộp', 20);
      if (d.method !== undefined) node.data.method = choice<'GET' | 'POST'>(d.method, ['GET', 'POST'], 'HTTP method');
      if (d.retries !== undefined) {
        if (typeof d.retries !== 'number' || !Number.isInteger(d.retries) || d.retries < 0 || d.retries > 3)
          throw new HttpError(400, 'Retry cần từ 0–3 lần.');
        node.data.retries = d.retries;
      }
      if (d.error_mode !== undefined) node.data.error_mode = choice<'stop' | 'fallback'>(d.error_mode, ['stop', 'fallback'], 'Xử lý lỗi');
      if (d.fallback !== undefined) node.data.fallback = text(d.fallback, 'Giá trị thay thế', 20000, false);
      if ((node.data.retries || node.data.error_mode === 'fallback') && !['llm', 'retrieval', 'http'].includes(node.type))
        throw new HttpError(400, 'Retry/fallback chỉ áp dụng cho AI, truy xuất và HTTP.');
      return node;
    }),
    edges: g.edges.map((raw) => {
      const e = object(raw);
      return {
        id: text(e.id, 'Edge ID', 64),
        source: text(e.source, 'Source', 64),
        target: text(e.target, 'Target', 64),
        sourceHandle: e.sourceHandle == null ? null : text(e.sourceHandle, 'Nhánh', 20),
      };
    }),
  };
  return {
    xt_tools,
    mcp_tools,
    generation: generationInput(v.generation),
    inputs: fieldDefinitions(v.inputs),
    model_id: text(v.model_id, 'Model', 128, false),
    system_prompt: text(v.system_prompt, 'System prompt', 20000, false),
    knowledge_ids: strings(v.knowledge_ids, 'Kho kiến thức'),
    tools,
    max_steps: steps,
    graph,
  };
}
