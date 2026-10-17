// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { AppConfig, ChatMessage, RunResult, Trace } from './contracts.ts';
import type { Store } from './store.ts';
import type { XtTools } from './xt-tools.ts';
import { generate } from './models.ts';
import type { FunctionTool } from './models.ts';
import { callMcp, selectedTool } from './mcp.ts';
import { hashToken } from './http.ts';
import { retrieve } from './knowledge.ts';
import { HttpError, object, text } from './validation.ts';
export function calculate(expression: string): number {
  const tokens = expression.match(/(?:\d+(?:\.\d*)?|\.\d+)|[()+\-*/]/g) ?? [];
  if (tokens.join('') !== expression.replace(/\s/g, '') || tokens.length > 100 || !tokens.length)
    throw new HttpError(400, 'Chỉ hỗ trợ số và các phép + − × ÷, dấu ngoặc.');
  let at = 0;
  const factor = (): number => {
    const token = tokens[at++];
    if (token === '+' || token === '-') return (token === '-' ? -1 : 1) * factor();
    if (token === '(') {
      const v = sum();
      if (tokens[at++] !== ')') throw new HttpError(400, 'Thiếu dấu đóng ngoặc.');
      return v;
    }
    const n = Number(token);
    if (token === undefined || !Number.isFinite(n)) throw new HttpError(400, 'Biểu thức không hợp lệ.');
    return n;
  };
  const product = (): number => {
    let n = factor();
    while (tokens[at] === '*' || tokens[at] === '/') {
      const op = tokens[at++],
        v = factor();
      if (op === '/' && v === 0) throw new HttpError(400, 'Không chia được cho 0.');
      n = op === '*' ? n * v : n / v;
    }
    return n;
  };
  const sum = (): number => {
    let n = product();
    while (tokens[at] === '+' || tokens[at] === '-') {
      const op = tokens[at++],
        v = product();
      n = op === '+' ? n + v : n - v;
    }
    return n;
  };
  const result = sum();
  if (at !== tokens.length || !Number.isFinite(result)) throw new HttpError(400, 'Biểu thức không hợp lệ.');
  return result;
}
const definitions: FunctionTool[] = [
  {
    type: 'function',
    function: {
      name: 'knowledge_search',
      description: 'Search the knowledge bases attached to this app. Return source citations.',
      parameters: { type: 'object', properties: { query: { type: 'string' } }, required: ['query'], additionalProperties: false },
    },
  },
  {
    type: 'function',
    function: {
      name: 'calculator',
      description: 'Calculate a numeric expression using + - * / and parentheses.',
      parameters: { type: 'object', properties: { expression: { type: 'string' } }, required: ['expression'], additionalProperties: false },
    },
  },
];
export async function runAgent(
  store: Store,
  workspace: string,
  config: AppConfig,
  messages: ChatMessage[],
  signal?: AbortSignal,
  coreTools?: XtTools,
): Promise<RunResult> {
  const selected = config.xt_tools ?? [];
  if (selected.length && !coreTools) throw new HttpError(503, 'Kết nối công cụ xT chưa sẵn sàng.');
  const core = selected.length ? (await coreTools!.list(signal)).filter((tool) => selected.includes(tool.name)) : [];
  if (core.length !== selected.length) throw new HttpError(403, 'Công cụ xT đã thay đổi; hãy kiểm tra cấu hình.');
  const remote = new Map(
    (config.mcp_tools ?? []).map((selection) => {
      const { tool } = selectedTool(store, workspace, selection);
      const name = 'mcp_' + hashToken(JSON.stringify([selection.connector_id, selection.tool_name])).slice(0, 32);
      return [name, { selection, tool }];
    }),
  );
  const tools: FunctionTool[] = [
    ...core.map((tool) => ({ type: 'function' as const, function: {
      name: 'xt_' + tool.name, description: tool.description, parameters: tool.input_schema,
    } })),
    ...definitions.filter((t) => config.tools.includes(t.function.name as AppConfig['tools'][number])),
    ...[...remote].map(([name, { tool }]) => ({
      type: 'function' as const,
      function: { name, description: tool.name + ': ' + tool.description, parameters: tool.input_schema },
    })),
  ];
  const history = [...messages],
    trace: Trace[] = [],
    citations: RunResult['citations'] = [];
  for (let step = 0; step < config.max_steps; step++) {
    const response = await generate(store, workspace, config.model_id, history, tools, signal, config.generation);
    history.push(response);
    if (!response.tool_calls?.length) return { output: response.content ?? '', trace, citations };
    if (response.tool_calls.length > 8) throw new HttpError(502, 'Model yêu cầu quá nhiều công cụ trong một bước.');
    for (const call of response.tool_calls) {
      const started = Date.now();
      let output = '',
        status: Trace['status'] = 'success';
      try {
        if (!tools.some((t) => t.function.name === call.function.name))
          throw new HttpError(400, 'Công cụ không nằm trong danh sách được phép.');
        const args = object(JSON.parse(call.function.arguments));
        if (call.function.name.startsWith('xt_')) {
          const value = await coreTools!.call(call.function.name.slice(3), args, signal);
          output = JSON.stringify(value);
          if ((value as { ok?: boolean }).ok === false) status = 'error';
        }
        if (call.function.name === 'knowledge_search') {
          const found = await retrieve(store, workspace, config.knowledge_ids, text(args.query, 'Truy vấn', 2000), 5, signal);
          citations.push(...found);
          output = JSON.stringify(found);
        }
        if (call.function.name === 'calculator') output = String(calculate(text(args.expression, 'Biểu thức', 500)));
        const binding = remote.get(call.function.name);
        if (binding) {
          const result = await callMcp(store, workspace, binding.selection, args, signal);
          output = result.output;
          if (result.isError) status = 'error';
        }
      } catch (error) {
        if (signal?.aborted) throw new HttpError(499, 'Đã dừng yêu cầu.');
        status = 'error';
        output = error instanceof Error ? error.message : 'Tool thất bại.';
      }
      trace.push({
        node_id: call.id,
        label: remote.get(call.function.name)?.tool.name ?? call.function.name,
        type: 'tool',
        status,
        input: call.function.arguments,
        output,
        duration_ms: Date.now() - started,
      });
      history.push({ role: 'tool', tool_call_id: call.id, content: output });
    }
  }
  const error = new HttpError(422, `Agent đã đạt giới hạn ${config.max_steps} bước mà chưa trả lời.`);
  Object.assign(error, { trace });
  throw error;
}
