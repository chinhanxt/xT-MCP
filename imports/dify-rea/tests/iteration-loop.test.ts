// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Store } from '../src/store.ts';
import { allNodes, emptyConfig, emptyGraph } from '../src/contracts.ts';
import type { AppConfig, Graph, GraphEdge, GraphNode, Trace } from '../src/contracts.ts';
import { configInput } from '../src/validation.ts';
import { runWorkflow, validateGraph } from '../src/workflow.ts';
import type { XtTools } from '../src/xt-tools.ts';

const dir = mkdtempSync(join(tmpdir(), 'xt-wf-iter-')),
  store = new Store(dir);
after(() => {
  store.close();
  rmSync(dir, { recursive: true, force: true });
});

const node = (id: string, type: GraphNode['type'], data: Partial<GraphNode['data']> = {}): GraphNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { label: id, ...data },
});
const edge = (source: string, target: string, sourceHandle: string | null = null): GraphEdge => ({
  id: `${source}-${target}-${sourceHandle ?? ''}`,
  source,
  target,
  sourceHandle,
});
const config = (graph: Graph): AppConfig => ({ ...emptyConfig(), model_id: 'm', graph });
const failure = async (promise: Promise<unknown>) => {
  try {
    await promise;
  } catch (error) {
    return error as Error & { status: number; trace: Trace[] };
  }
  throw new Error('expected failure');
};
// Sub-graph: start -> template -> output.
const inner = (p: string, template: string): Graph => ({
  nodes: [node(`${p}s`, 'start'), node(`${p}t`, 'template', { template }), node(`${p}o`, 'output', { template: `{{${p}t}}` })],
  edges: [edge(`${p}s`, `${p}t`), edge(`${p}t`, `${p}o`)],
});
const iterGraph = (data: Partial<GraphNode['data']>, sub = inner('i', '{{index}}:{{item}}')): Graph => ({
  nodes: [node('s', 'start'), node('iter', 'iteration', { variable: '{{input}}', graph: sub, ...data }), node('o', 'output', { template: '{{iter}}' })],
  edges: [edge('s', 'iter'), edge('iter', 'o')],
});
const loopGraph = (data: Partial<GraphNode['data']>, sub = inner('l', '{{acc}}x')): Graph => ({
  nodes: [
    node('s', 'start'),
    node('lp', 'loop', {
      graph: sub,
      loop_vars: [{ name: 'acc', init: '{{input}}', update: '{{lt}}' }],
      until: { id: 'stop', logical_operator: 'and', conditions: [{ variable: '{{lt}}', operator: 'contains', value: 'xxx' }] },
      ...data,
    }),
    node('ok', 'output', { template: 'done {{lp.output}} after {{lp.rounds}}' }),
    node('bad', 'output', { template: 'exhausted {{lp.output}} after {{lp.rounds}}' }),
  ],
  edges: [edge('s', 'lp'), edge('lp', 'ok', 'done'), edge('lp', 'bad', 'exhausted')],
});

test('iteration runs the sub-graph per item with item/index and outputs a JSON array; object items expose fields', async () => {
  const result = await runWorkflow(store, 'w', config(iterGraph({})), JSON.stringify(['a', 'b', 'c']));
  assert.deepEqual(JSON.parse(result.output), ['0:a', '1:b', '2:c']);
  assert.deepEqual(
    result.trace.filter((t) => t.node_id === 'iter').map((t) => t.status),
    ['success'],
  );
  assert.deepEqual(
    result.trace.filter((t) => t.node_id === 'it').map((t) => [t.parent_id, t.item_index, t.output]),
    [
      ['iter', 0, '0:a'],
      ['iter', 1, '1:b'],
      ['iter', 2, '2:c'],
    ],
  );
  const objects = await runWorkflow(store, 'w', config(iterGraph({}, inner('i', '{{item.name}}'))), '[{"name":"An"},{"name":"Bình"}]');
  assert.deepEqual(JSON.parse(objects.output), ['An', 'Bình']);
  const inside = await runWorkflow(store, 'w', config(iterGraph({}, inner('i', '{{input}}|{{is}}'))), '["x"]');
  assert.deepEqual(JSON.parse(inside.output), ['["x"]|x'], 'inner start outputs the item; {{input}} stays the run input');
});

test('iteration error_mode: stop fails the run, skip keeps going and collects errors', async () => {
  const sub = inner('i', '{{item.name}}');
  const items = '[{"name":"a"},"plain",{"name":"c"}]';
  const stop = await failure(runWorkflow(store, 'w', config(iterGraph({}, sub)), items));
  assert.equal(stop.status, 400);
  assert.match(stop.message, /không phải JSON object/);
  assert.deepEqual(
    stop.trace.filter((t) => t.status === 'error').map((t) => [t.node_id === 'iter' ? 'iter' : t.parent_id, t.item_index]),
    [
      ['iter', 1],
      ['iter', undefined],
    ],
  );
  const skip = await runWorkflow(store, 'w', config(iterGraph({ error_mode: 'skip' }, sub)), items);
  assert.deepEqual(JSON.parse(skip.output), ['a', 'c']);
  const summary = skip.trace.find((t) => t.node_id === 'iter')!;
  assert.equal(summary.status, 'success');
  assert.equal(summary.errors?.length, 1);
  assert.equal(summary.errors![0].index, 1);
  assert.match(summary.errors![0].error, /không phải JSON object/);
});

test('iteration enforces max_items as a hard cap and rejects non-array input at runtime', async () => {
  const many = JSON.stringify(Array.from({ length: 51 }, (_, i) => i));
  const capped = await failure(runWorkflow(store, 'w', config(iterGraph({})), many));
  assert.match(capped.message, /51 mục, vượt giới hạn 50/);
  const ok = await runWorkflow(store, 'w', config(iterGraph({ max_items: 60 })), many);
  assert.equal(JSON.parse(ok.output).length, 51);
  assert.match((await failure(runWorkflow(store, 'w', config(iterGraph({ max_items: 2 })), '[1,2,3]'))).message, /vượt giới hạn 2/);
  const notArray = await failure(runWorkflow(store, 'w', config(iterGraph({})), 'không phải mảng'));
  assert.equal(notArray.status, 400);
  assert.match(notArray.message, /phải là mảng JSON/);
  assert.throws(
    () => configInput({ ...emptyConfig(), graph: iterGraph({ max_items: 201 }) }),
    /Tối đa mục cần từ 1–200/,
  );
});

test('nested iteration runs at depth 2 and a third level is rejected', async () => {
  const deep: Graph = {
    nodes: [
      node('is', 'start'),
      node('rows', 'iteration', { variable: '{{item}}', graph: inner('j', '{{index}}{{item}}') }),
      node('io', 'output', { template: '{{rows}}' }),
    ],
    edges: [edge('is', 'rows'), edge('rows', 'io')],
  };
  const result = await runWorkflow(store, 'w', config(iterGraph({}, deep)), '[["a","b"],["c"]]');
  assert.deepEqual(JSON.parse(result.output).map((x: string) => JSON.parse(x)), [['0a', '1b'], ['0c']]);
  assert.ok(result.trace.some((t) => t.node_id === 'jt' && t.parent_id === 'rows' && t.item_index === 1));
  const third: Graph = {
    nodes: [
      node('ks', 'start'),
      node('k3', 'iteration', { variable: '{{item}}', graph: inner('k', '{{item}}') }),
      node('ko', 'output', { template: '{{k3}}' }),
    ],
    edges: [edge('ks', 'k3'), edge('k3', 'ko')],
  };
  const tooDeep = iterGraph({}, { ...deep, nodes: deep.nodes.map((n) => (n.id === 'rows' ? { ...n, data: { ...n.data, graph: third } } : n)) });
  assert.throws(() => validateGraph(tooDeep), /lồng nhau tối đa 2 cấp/);
  assert.throws(() => configInput({ ...emptyConfig(), graph: tooDeep }), /lồng nhau tối đa 2 cấp/);
});

test('loop stops when the condition holds, carrying loop variables between rounds', async () => {
  const result = await runWorkflow(store, 'w', config(loopGraph({ max_rounds: 5 })), '');
  assert.equal(result.output, 'done xxx after 3');
  assert.deepEqual(
    result.trace.filter((t) => t.node_id === 'lt').map((t) => [t.round, t.output]),
    [
      [1, 'x'],
      [2, 'xx'],
      [3, 'xxx'],
    ],
  );
  assert.equal(result.trace.find((t) => t.node_id === 'bad')?.status, 'skipped');
  const withLast = await runWorkflow(
    store,
    'w',
    config(loopGraph({ max_rounds: 3, loop_vars: [], until: { id: 'c', logical_operator: 'and', conditions: [{ variable: '{{round}}', operator: 'ge', value: '2' }] } }, inner('l', 'r{{round}} after [{{last}}]'))),
    '',
  );
  assert.equal(withLast.output, 'done r2 after [r1 after []] after 2');
});

test('loop takes the exhausted branch at max_rounds ("rewrite at most 2 rounds, then tell a human")', async () => {
  const result = await runWorkflow(store, 'w', config(loopGraph({ max_rounds: 2 })), '');
  assert.equal(result.output, 'exhausted xx after 2');
  assert.equal(result.trace.find((t) => t.node_id === 'ok')?.status, 'skipped');
  assert.deepEqual(JSON.parse(result.trace.find((t) => t.node_id === 'lp')!.output), { output: 'xx', rounds: 2 });
  const defaults = await runWorkflow(store, 'w', config(loopGraph({ until: { id: 'c', logical_operator: 'and', conditions: [{ variable: '{{lt}}', operator: 'is', value: 'never' }] } })), '');
  assert.equal(defaults.output, 'exhausted xxx after 3', 'default max_rounds is 3');
  assert.throws(() => configInput({ ...emptyConfig(), graph: loopGraph({ max_rounds: 11 }) }), /Tối đa vòng cần từ 1–10/);
});

test('total step budget stops runaway runs, even in skip mode', async () => {
  const items = JSON.stringify(Array.from({ length: 200 }, (_, i) => i));
  const result = await failure(runWorkflow(store, 'w', config(iterGraph({ max_items: 200, error_mode: 'skip' })), items));
  assert.match(result.message, /Vượt ngân sách 500 bước/);
  assert.ok(result.trace.length <= 520);
  const fits = await runWorkflow(store, 'w', config(iterGraph({ max_items: 200 })), JSON.stringify(Array.from({ length: 150 }, (_, i) => i)));
  assert.equal(JSON.parse(fits.output).length, 150);
});

test('abort inside an iteration stops the run with 499, not collected by skip mode', async () => {
  const controller = new AbortController();
  let calls = 0;
  const tools: XtTools = {
    async list() {
      return [];
    },
    async call() {
      if (++calls === 2) controller.abort();
      return { ok: true };
    },
  };
  const sub: Graph = {
    nodes: [node('as', 'start'), node('at', 'xt_tool', { tool: 'get_record', args: '{"ref": "{{item}}"}' }), node('ao', 'output', { template: '{{at}}' })],
    edges: [edge('as', 'at'), edge('at', 'ao')],
  };
  const error = await failure(runWorkflow(store, 'w', config(iterGraph({ error_mode: 'skip' }, sub)), '["a","b","c","d"]', controller.signal, {}, tools));
  assert.equal(error.status, 499);
  assert.equal(calls, 2);
});

test('validation: human_approval inside, non-array variable, missing exhausted edge, scope and collisions', () => {
  const approval = node('ha', 'human_approval', { title: 't', summary: 's', request_model: 'approval_ticket' });
  const withApproval: Graph = {
    nodes: [node('hs', 'start'), approval, node('ho', 'output', { template: 'ok' }), node('hr', 'output', { template: 'no' })],
    edges: [edge('hs', 'ha'), edge('ha', 'ho', 'approved'), edge('ha', 'hr', 'rejected')],
  };
  assert.throws(() => validateGraph(iterGraph({}, withApproval)), /Không dùng node Người duyệt bên trong Lặp/);
  assert.throws(() => validateGraph(loopGraph({}, { ...withApproval })), /Không dùng node Người duyệt bên trong Vòng lặp/);

  const extractor: Graph = {
    nodes: [
      node('s', 'start'),
      node('p', 'parameter_extractor', {
        query: '{{input}}',
        parameters: [
          { name: 'ten', type: 'string', description: '', required: true },
          { name: 'ds', type: 'array[string]', description: '', required: true },
        ],
      }),
      node('iter', 'iteration', { variable: '{{p.ten}}', graph: inner('i', '{{item}}') }),
      node('o', 'output', { template: '{{iter}}' }),
    ],
    edges: [edge('s', 'p'), edge('p', 'iter'), edge('iter', 'o')],
  };
  assert.throws(() => validateGraph(extractor), /phải là mảng JSON; p.ten không phải danh sách/);
  extractor.nodes[2].data.variable = '{{p.ds}}';
  assert.deepEqual(validateGraph(extractor), ['s', 'p', 'iter', 'o']);
  assert.throws(() => validateGraph(iterGraph({ variable: 'các mục: {{input}}' })), /đúng một biến/);

  const noExhausted = loopGraph({});
  noExhausted.edges = noExhausted.edges.filter((e) => e.sourceHandle !== 'exhausted');
  noExhausted.nodes = noExhausted.nodes.filter((n) => n.id !== 'bad');
  assert.throws(() => validateGraph(noExhausted), /chưa nối nhánh exhausted/);
  assert.throws(() => validateGraph(loopGraph({ until: undefined })), /cần điều kiện dừng/);
  assert.throws(
    () => validateGraph(loopGraph({ until: { id: 'c', logical_operator: 'and', conditions: [{ variable: '{{nope}}', operator: 'empty' }] } })),
    /Biến nope trong điều kiện dừng/,
  );

  // Inner nodes may read guaranteed parent ancestors, not later or sibling parent nodes, and may not reuse their IDs.
  const scoped: Graph = {
    nodes: [
      node('s', 'start'),
      node('pre', 'template', { template: 'P' }),
      node('iter', 'iteration', { variable: '{{input}}', graph: inner('i', '{{pre}}{{item}}') }),
      node('o', 'output', { template: '{{iter}}' }),
    ],
    edges: [edge('s', 'pre'), edge('pre', 'iter'), edge('iter', 'o')],
  };
  assert.deepEqual(validateGraph(scoped), ['s', 'pre', 'iter', 'o']);
  scoped.nodes[2].data.graph = inner('i', '{{o}}');
  assert.throws(() => validateGraph(scoped), /Biến o không tồn tại/);
  scoped.nodes[2].data.graph = inner('i', '{{item}}');
  scoped.nodes[2].data.graph.nodes[1] = node('pre', 'template', { template: 'x' });
  assert.throws(() => validateGraph(scoped), /trùng với bước/);
  assert.throws(() => validateGraph(iterGraph({}, { nodes: [node('xs', 'start')], edges: [] })), /bên trong Lặp iter cần đúng một đầu vào/);
  assert.throws(() => configInput({ ...emptyConfig(), graph: iterGraph({ graph: undefined }) }), /cần các bước bên trong/);
  assert.throws(
    () => configInput({ ...emptyConfig(), graph: { ...iterGraph({}), nodes: iterGraph({}).nodes.map((n) => (n.id === 'o' ? { ...n, data: { ...n.data, error_mode: 'skip' } } : n)) } }),
    /chỉ áp dụng cho node Lặp/,
  );
});

test('configInput keeps sub-graphs and options; old graphs and helpers are unchanged', async () => {
  const graph = loopGraph({ max_rounds: 2 });
  const parsed = configInput({ ...emptyConfig(), graph });
  assert.deepEqual(parsed.graph, graph);
  const iter = configInput({ ...emptyConfig(), graph: iterGraph({ max_items: 7, error_mode: 'skip' }) });
  assert.deepEqual(iter.graph, iterGraph({ max_items: 7, error_mode: 'skip' }));
  assert.deepEqual(
    allNodes(parsed.graph).map((n) => n.id),
    ['s', 'lp', 'ls', 'lt', 'lo', 'ok', 'bad'],
  );
  const old = configInput(emptyConfig());
  assert.deepEqual(old.graph, emptyGraph());
  assert.deepEqual(validateGraph(old.graph), ['start', 'llm', 'output']);
  const plain: Graph = {
    nodes: [node('s', 'start'), node('t', 'template', { template: '<{{input}}>' }), node('o', 'output', { template: '{{t}}' })],
    edges: [edge('s', 't'), edge('t', 'o')],
  };
  const result = await runWorkflow(store, 'w', config(plain), 'hi');
  assert.equal(result.output, '<hi>');
  assert.ok(result.trace.every((t) => t.parent_id === undefined && t.item_index === undefined && t.round === undefined));
});
