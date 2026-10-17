// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { AppConfig, Graph, RunResult, Trace } from './contracts.ts';
import type { Store } from './store.ts';
import { HttpError } from './validation.ts';
import { generate } from './models.ts';
import { contextText, retrieve } from './knowledge.ts';
import { sourceCheck } from './xt-source.ts';
import type { XtTools } from './xt-tools.ts';
import { httpNodeUrl, runHttpNode } from './http-node.ts';
import { withNodePolicy } from './node-policy.ts';
export function validateGraph(graph: Graph, inputNames: string[] = []): string[] {
  const ids = new Set(graph.nodes.map((n) => n.id));
  if (ids.size !== graph.nodes.length) throw new HttpError(400, 'Node ID bị trùng.');
  if (inputNames.some((name) => ids.has(name))) throw new HttpError(400, 'Tên đầu vào trùng Node ID.');
  if (graph.nodes.filter((n) => n.type === 'start').length !== 1 || !graph.nodes.some((n) => n.type === 'output'))
    throw new HttpError(400, 'Workflow cần đúng một đầu vào và ít nhất một kết quả.');
  if (new Set(graph.edges.map((e) => e.id)).size !== graph.edges.length) throw new HttpError(400, 'Edge ID bị trùng.');
  const degree = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) throw new HttpError(400, 'Đường nối tham chiếu node không tồn tại.');
    const source = graph.nodes.find((n) => n.id === e.source)!;
    if (source.type === 'output' || graph.nodes.find((n) => n.id === e.target)!.type === 'start')
      throw new HttpError(400, 'Không nối sau kết quả hoặc trước đầu vào.');
    if (source.type === 'condition' && !['true', 'false'].includes(e.sourceHandle ?? ''))
      throw new HttpError(400, 'Điều kiện cần nhánh true hoặc false.');
    degree.set(e.target, degree.get(e.target)! + 1);
  }
  const queue = graph.nodes.filter((n) => !degree.get(n.id)).map((n) => n.id),
    ordered: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    ordered.push(id);
    for (const e of graph.edges.filter((e) => e.source === id)) {
      degree.set(e.target, degree.get(e.target)! - 1);
      if (!degree.get(e.target)) queue.push(e.target);
    }
  }
  if (ordered.length !== graph.nodes.length) throw new HttpError(400, 'Workflow có vòng lặp.');
  const guaranteed = new Map<string, Set<string>>();
  for (const id of ordered) {
    const incoming = graph.edges.filter((e) => e.target === id).map((e) => new Set([...(guaranteed.get(e.source) ?? []), e.source]));
    guaranteed.set(id, incoming.length ? new Set([...incoming[0]].filter((key) => incoming.every((s) => s.has(key)))) : new Set());
  }
  const reached = new Set<string>();
  const visit = (id: string) => {
    if (reached.has(id)) return;
    reached.add(id);
    for (const e of graph.edges.filter((e) => e.source === id)) visit(e.target);
  };
  visit(graph.nodes.find((n) => n.type === 'start')!.id);
  if (reached.size !== graph.nodes.length) throw new HttpError(400, 'Có node chưa được nối từ đầu vào.');
  for (const n of graph.nodes) {
    const outgoing = graph.edges.filter((e) => e.source === n.id);
    if (n.type !== 'output' && !outgoing.length) throw new HttpError(400, `Node ${n.data.label} chưa có đường ra.`);
    if (n.type === 'condition' && (!outgoing.some((e) => e.sourceHandle === 'true') || !outgoing.some((e) => e.sourceHandle === 'false')))
      throw new HttpError(400, 'Điều kiện cần đủ hai nhánh.');
    if (n.type === 'http') httpNodeUrl(n.data.url ?? '');
    if (n.type === 'aggregate') {
      const candidates = n.data.candidates ?? [];
      if (!candidates.length || candidates.length !== new Set(candidates).size)
        throw new HttpError(400, 'Node gộp cần các biến không trùng.');
      for (const candidate of candidates) {
        const upstream = new Set<string>();
        const walk = (id: string) => {
          for (const edge of graph.edges.filter((e) => e.target === id))
            if (!upstream.has(edge.source)) {
              upstream.add(edge.source);
              walk(edge.source);
            }
        };
        walk(n.id);
        if (!upstream.has(candidate)) throw new HttpError(400, `Biến gộp ${candidate} phải ở trước node ${n.id}.`);
      }
    }
    for (const value of [n.data.prompt, n.data.template, n.data.variable, n.data.url, n.data.body, n.data.fallback])
      if (value)
        for (const m of value.matchAll(/\{\{\s*([\w-]+)\s*\}\}/g)) {
          if (m[1] === 'input' || inputNames.includes(m[1])) continue;
          if (!ids.has(m[1])) throw new HttpError(400, `Biến ${m[1]} không tồn tại.`);
          const ancestors = new Set<string>();
          const walk = (id: string) => {
            for (const e of graph.edges.filter((e) => e.target === id))
              if (!ancestors.has(e.source)) {
                ancestors.add(e.source);
                walk(e.source);
              }
          };
          walk(n.id);
          if (m[1] !== 'input' && !ancestors.has(m[1])) throw new HttpError(400, `Biến ${m[1]} phải nằm trước node ${n.id}.`);
          // shortcut: require dominance at joins; add explicit aggregation nodes for alternative branch outputs.
          if (m[1] !== 'input' && !guaranteed.get(n.id)?.has(m[1]))
            throw new HttpError(400, `Biến ${m[1]} không được bảo đảm có giá trị trên mọi nhánh vào ${n.id}.`);
        }
  }
  return ordered;
}
function render(template: string, values: Map<string, string>): string {
  return template.replace(/\{\{\s*([\w-]+)\s*\}\}/g, (_, key: string) => {
    if (!values.has(key)) throw new HttpError(400, `Biến ${key} chưa có giá trị trên nhánh này.`);
    return values.get(key)!;
  });
}
export async function runWorkflow(
  store: Store,
  workspace: string,
  config: AppConfig,
  input: string,
  signal?: AbortSignal,
  inputs: Record<string, string | number | boolean> = {},
  coreTools?: XtTools,
  integrated = coreTools !== undefined,
): Promise<RunResult> {
  const check = sourceCheck(coreTools, integrated);
  const graph = config.graph,
    ordered = validateGraph(
      graph,
      (config.inputs ?? []).map((f) => f.name),
    ),
    values = new Map([['input', input], ...Object.entries(inputs).map(([key, value]) => [key, String(value)] as [string, string])]),
    trace: Trace[] = [],
    active = new Set<string>(),
    branches = new Map<string, string>();
  const citations: RunResult['citations'] = [];
  const outputs: string[] = [];
  for (const id of ordered) {
    if (signal?.aborted) throw new HttpError(499, 'Đã dừng workflow.');
    const n = graph.nodes.find((n) => n.id === id)!,
      started = Date.now();
    const incoming = graph.edges.filter((e) => e.target === id);
    const enabled =
      n.type === 'start' ||
      incoming.some((e) => active.has(e.source) && (!branches.has(e.source) || branches.get(e.source) === e.sourceHandle));
    if (!enabled) {
      trace.push({ node_id: id, label: n.data.label, type: n.type, status: 'skipped', input: '', output: '', duration_ms: 0 });
      continue;
    }
    let value = input,
      output = '';
    try {
      const policy = await withNodePolicy(
        n,
        async () => {
          if (n.type === 'start') output = input;
          if (n.type === 'llm') {
            value = render(n.data.prompt || '{{input}}', values);
            output =
              (
                await generate(
                  store,
                  workspace,
                  n.data.model_id || config.model_id,
                  [
                    { role: 'system', content: config.system_prompt },
                    { role: 'user', content: value },
                  ],
                  [],
                  signal,
                  config.generation,
                )
              ).content ?? '';
          }
          if (n.type === 'retrieval') {
            value = render(n.data.prompt || '{{input}}', values);
            if (!n.data.knowledge_id) throw new HttpError(400, 'Node truy xuất chưa chọn kho kiến thức.');
            const found = await retrieve(store, workspace, [n.data.knowledge_id], value, 5, signal, check);
            citations.push(...found);
            output = contextText(found);
          }
          if (n.type === 'condition') {
            value = render(n.data.variable || '{{input}}', values);
            const yes = n.data.operator === 'equals' ? value === (n.data.value ?? '') : value.includes(n.data.value ?? '');
            output = String(yes);
            branches.set(id, output);
          }
          if (n.type === 'aggregate') {
            const candidates = (n.data.candidates ?? []).filter((key) => values.has(key));
            if (candidates.length !== 1) throw new HttpError(400, `Node gộp cần đúng một biến có giá trị; nhận ${candidates.length}.`);
            output = values.get(candidates[0])!;
            value = candidates[0];
          }
          if (n.type === 'http') {
            value = render(n.data.url ?? '', values);
            output = await runHttpNode(n.data.url ?? '', value, n.data.method ?? 'GET', render(n.data.body ?? '', values), signal);
          }
          if (n.type === 'template' || n.type === 'output') {
            value = n.data.template || '{{input}}';
            output = render(value, values);
            if (n.type === 'output') outputs.push(output);
          }
        },
        () => {
          output = render(n.data.fallback ?? '', values);
        },
        signal,
      );
      values.set(id, output);
      active.add(id);
      trace.push({
        node_id: id,
        label: n.data.label,
        type: n.type,
        status: 'success',
        input: value,
        output,
        duration_ms: Date.now() - started,
        ...policy,
      });
    } catch (error) {
      trace.push({
        node_id: id,
        label: n.data.label,
        type: n.type,
        status: 'error',
        input: value,
        output: error instanceof Error ? error.message : 'Lỗi',
        duration_ms: Date.now() - started,
      });
      const failure = new HttpError(
        signal?.aborted ? 499 : error instanceof HttpError ? error.status : 500,
        signal?.aborted ? 'Đã dừng workflow.' : error instanceof Error ? error.message : 'Workflow thất bại.',
      );
      Object.assign(failure, { trace });
      throw failure;
    }
  }
  if (!outputs.length) throw new HttpError(400, 'Nhánh chạy không có node kết quả.');
  return { output: outputs.join('\n\n'), trace, citations };
}
