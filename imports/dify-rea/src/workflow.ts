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
import { randomUUID } from 'node:crypto';
import {
  approvalNote,
  humanApprovalNeedsXt,
  requestApproval,
  type ApprovalDecision,
  type WaitSnapshot,
  type WorkflowOutcome,
} from './human-approval-node.ts';
import { withNodePolicy } from './node-policy.ts';
import { defaultGeneration, xtToolNames } from './contracts.ts';
import {
  branchHandles,
  classifierPrompt,
  extractParameters,
  extractorPrompt,
  fieldValue,
  hasCases,
  jsonEscape,
  matchCase,
  nodeTemplates,
  operateList,
  parseArgs,
  parseClass,
  refPattern,
  validateNodeData,
} from './workflow-nodes.ts';
export function validateGraph(graph: Graph, inputNames: string[] = []): string[] {
  const ids = new Set(graph.nodes.map((n) => n.id));
  if (ids.size !== graph.nodes.length) throw new HttpError(400, 'Node ID bị trùng.');
  if (inputNames.some((name) => ids.has(name))) throw new HttpError(400, 'Tên đầu vào trùng Node ID.');
  if (graph.nodes.filter((n) => n.type === 'start').length !== 1 || !graph.nodes.some((n) => n.type === 'output'))
    throw new HttpError(400, 'Workflow cần đúng một đầu vào và ít nhất một kết quả.');
  if (new Set(graph.edges.map((e) => e.id)).size !== graph.edges.length) throw new HttpError(400, 'Edge ID bị trùng.');
  for (const n of graph.nodes) validateNodeData(n);
  const degree = new Map(graph.nodes.map((n) => [n.id, 0]));
  for (const e of graph.edges) {
    if (!ids.has(e.source) || !ids.has(e.target)) throw new HttpError(400, 'Đường nối tham chiếu node không tồn tại.');
    const source = graph.nodes.find((n) => n.id === e.source)!;
    if (source.type === 'output' || graph.nodes.find((n) => n.id === e.target)!.type === 'start')
      throw new HttpError(400, 'Không nối sau kết quả hoặc trước đầu vào.');
    const handles = branchHandles(source);
    if (handles && !handles.includes(e.sourceHandle ?? ''))
      throw new HttpError(
        400,
        source.type === 'condition' && !hasCases(source)
          ? 'Điều kiện cần nhánh true hoặc false.'
          : `Nhánh ${e.sourceHandle ?? ''} không có trong node ${source.data.label}.`,
      );
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
    const handles = branchHandles(n);
    for (const handle of handles ?? [])
      if (!outgoing.some((e) => e.sourceHandle === handle))
        throw new HttpError(
          400,
          n.type === 'condition' && !hasCases(n) ? 'Điều kiện cần đủ hai nhánh.' : `Node ${n.data.label} chưa nối nhánh ${handle}.`,
        );
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
    for (const value of nodeTemplates(n))
      if (value)
        for (const m of value.matchAll(refPattern)) {
          if (m[1] === 'input' || inputNames.includes(m[1])) continue;
          if (!ids.has(m[1])) throw new HttpError(400, `Biến ${m[1]} không tồn tại.`);
          const base = graph.nodes.find((x) => x.id === m[1])!;
          if (m[2] !== undefined && base.type === 'parameter_extractor' && !base.data.parameters?.some((p) => p.name === m[2]))
            throw new HttpError(400, `Node ${m[1]} không có trường ${m[2]}.`);
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
function lookup(values: Map<string, string>, key: string, field?: string): string {
  if (!values.has(key)) throw new HttpError(400, `Biến ${key} chưa có giá trị trên nhánh này.`);
  return field === undefined ? values.get(key)! : fieldValue(key, values.get(key)!, field);
}
function render(template: string, values: Map<string, string>): string {
  return template.replace(refPattern, (_, key: string, field?: string) => lookup(values, key, field));
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
  run: { id?: string; resume?: { snapshot: WaitSnapshot; decision: ApprovalDecision } } = {},
): Promise<WorkflowOutcome> {
  const check = sourceCheck(coreTools, integrated);
  const resume = run.resume,
    saved = resume?.snapshot;
  const graph = config.graph,
    ordered = validateGraph(
      graph,
      (config.inputs ?? []).map((f) => f.name),
    ),
    values = new Map(
      saved?.values ?? [['input', input], ...Object.entries(inputs).map(([key, value]) => [key, String(value)] as [string, string])],
    ),
    trace: Trace[] = saved ? saved.trace.filter((t) => !(t.node_id === saved.node_id && t.status === 'waiting')) : [],
    active = new Set<string>(saved?.active ?? []),
    branches = new Map<string, string>(saved?.branches ?? []);
  const citations: RunResult['citations'] = saved ? [...saved.citations] : [];
  const outputs: string[] = saved ? [...saved.outputs] : [];
  if (saved && ordered[saved.index] !== saved.node_id) throw new HttpError(409, 'Workflow đã thay đổi; không chạy tiếp được.');
  for (let index = saved?.index ?? 0; index < ordered.length; index++) {
    const id = ordered[index];
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
      output = '',
      waitingRef = '';
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
          if (n.type === 'condition' && hasCases(n)) {
            value = (n.data.cases ?? []).map((c) => c.id).join(', ');
            output = matchCase(n.data.cases!, (template) => render(template, values));
            branches.set(id, output);
          } else if (n.type === 'condition') {
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
          if (n.type === 'xt_tool') {
            const tool = n.data.tool ?? '';
            if (!xtToolNames.includes(tool)) throw new HttpError(403, 'Công cụ xT không được cấp.');
            if (!coreTools) throw new HttpError(400, 'Node Công cụ xT chỉ chạy khi Studio được mở từ xT (chế độ tích hợp).');
            const args = parseArgs(n.data.args || '{}', (key, field) => jsonEscape(lookup(values, key, field)));
            value = `${tool} ${JSON.stringify(args)}`;
            output = JSON.stringify(await coreTools.call(tool, args, signal));
          }
          if (n.type === 'question_classifier' || n.type === 'parameter_extractor') {
            value = render(n.data.query || '{{input}}', values);
            const classify = n.type === 'question_classifier';
            const instruction = render(n.data.instruction ?? '', values);
            const content =
              (
                await generate(
                  store,
                  workspace,
                  n.data.model_id || config.model_id,
                  [
                    {
                      role: 'system',
                      content: classify
                        ? classifierPrompt(n.data.classes ?? [], instruction)
                        : extractorPrompt(n.data.parameters ?? [], instruction),
                    },
                    { role: 'user', content: value },
                  ],
                  [],
                  signal,
                  { ...(config.generation ?? defaultGeneration()), json_mode: !classify },
                )
              ).content ?? '';
            if (classify) {
              const hit = parseClass(content, n.data.classes ?? []);
              if (!hit) throw new HttpError(502, `Mô hình trả về nhóm không hợp lệ: ${content.slice(0, 200)}`);
              output = hit.name;
              branches.set(id, hit.id);
            } else output = JSON.stringify(extractParameters(content, n.data.parameters ?? []));
          }
          if (n.type === 'human_approval') {
            if (resume && saved!.node_id === id) {
              value = saved!.request_ref;
              output = JSON.stringify({ request_ref: saved!.request_ref, state: resume.decision });
              branches.set(id, resume.decision);
            } else {
              if (!coreTools) throw new HttpError(400, humanApprovalNeedsXt);
              const title = render(n.data.title ?? '', values),
                content = render(n.data.summary ?? '', values);
              const note = approvalNote(title, content, n.data.approver_hint);
              value = note;
              waitingRef = await requestApproval(
                coreTools,
                {
                  run_id: run.id ?? randomUUID(),
                  node_id: id,
                  model: n.data.request_model ?? '',
                  values: parseArgs(n.data.request_values || '{}', (key, field) => jsonEscape(lookup(values, key, field))),
                  note,
                  title,
                  content,
                },
                signal,
              );
              output = `Đang chờ duyệt: ${waitingRef}`;
            }
          }
          if (n.type === 'list_operator') {
            value = render(n.data.variable ?? '', values);
            output = JSON.stringify(operateList(value, n.data, (template) => render(template, values)) ?? null);
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
      if (waitingRef) {
        trace.push({ node_id: id, label: n.data.label, type: n.type, status: 'waiting', input: value, output, duration_ms: Date.now() - started });
        const snapshot: WaitSnapshot = {
          node_id: id,
          index,
          request_ref: waitingRef,
          ...(n.data.timeout_hours ? { timeout_hours: n.data.timeout_hours } : {}),
          paused_at: Date.now(),
          values: [...values],
          active: [...active],
          branches: [...branches],
          citations,
          outputs,
          trace,
        };
        return { output: `Đang chờ duyệt. Yêu cầu xT: ${waitingRef}`, trace, citations, status: 'waiting', request_ref: waitingRef, waiting: snapshot };
      }
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
