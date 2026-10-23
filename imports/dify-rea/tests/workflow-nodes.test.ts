// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { emptyConfig } from '../src/contracts.ts';
import type { AppConfig, Graph, GraphEdge, GraphNode, Trace } from '../src/contracts.ts';
import { configInput } from '../src/validation.ts';
import { runWorkflow, validateGraph } from '../src/workflow.ts';
import { compare, operateList, parseClass } from '../src/workflow-nodes.ts';
import type { XtTools } from '../src/xt-tools.ts';

const dir = mkdtempSync(join(tmpdir(), 'xt-wf-nodes-')),
    store = new Store(dir);
const replies: string[] = [];
const requests: any[] = [];
const model = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    requests.push(JSON.parse(Buffer.concat(chunks).toString()));
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ choices: [{ message: { role: 'assistant', content: replies.shift() ?? '' } }] }));
});
before(async () => {
    await new Promise<void>((resolve) => model.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${(model.address() as AddressInfo).port}`;
    store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run('w', 'W');
    store.db.prepare('INSERT INTO models VALUES(?,?,?,?,?,?,?,?)').run('m', 'w', 'Fixture', 'openai', origin, 'fixture', 'chat', '');
});
after(async () => {
    await new Promise<void>((resolve) => model.close(() => resolve()));
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
const traceOf = async (promise: Promise<unknown>) => {
    try {
        await promise;
    } catch (error) {
        return error as Error & { status: number; trace: Trace[] };
    }
    throw new Error('expected failure');
};

const toolGraph = (args: string, tool = 'search_records'): Graph => ({
    nodes: [node('s', 'start'), node('t', 'xt_tool', { tool, args }), node('o', 'output', { template: '{{t}}' })],
    edges: [edge('s', 't'), edge('t', 'o')],
});

test('xt_tool renders JSON-escaped args, calls the core tool and outputs its JSON result', async () => {
    const calls: { name: string; args: Record<string, unknown> }[] = [];
    const tools: XtTools = {
        async list() {
            return [];
        },
        async call(name, args) {
            calls.push({ name, args });
            return { ok: true, data: { records: [{ ref: 'stock_item:a' }] } };
        },
    };
    const input = 'kho "A"\nline\\2';
    const result = await runWorkflow(store, 'w', config(toolGraph('{"query": "{{input}}", "limit": 5}')), input, undefined, {}, tools);
    assert.deepEqual(calls, [{ name: 'search_records', args: { query: input, limit: 5 } }]);
    assert.deepEqual(JSON.parse(result.output), { ok: true, data: { records: [{ ref: 'stock_item:a' }] } });
    assert.equal(result.trace[1].type, 'xt_tool');
    assert.equal(result.trace[1].status, 'success');
    const offline = await traceOf(runWorkflow(store, 'w', config(toolGraph('{}')), 'x'));
    assert.equal(offline.status, 400);
    assert.match(offline.message, /tích hợp/);
    assert.equal(offline.trace.at(-1)!.status, 'error');
});

test('xt_tool rejects unknown tools and invalid JSON templates before running', () => {
    assert.throws(() => validateGraph(toolGraph('{}', 'decide_request')), /không được cấp/);
    assert.throws(() => validateGraph(toolGraph('{"query": {{input}}}')), /JSON hợp lệ/);
    assert.throws(() => validateGraph(toolGraph('["{{input}}"]')), /JSON object/);
    assert.throws(() => validateGraph(toolGraph('{"q": "{{missing}}"}')), /không tồn tại/);
    assert.throws(() => configInput(config(toolGraph('{}', 'decide_request'))), /không được cấp/);
    assert.equal(configInput(config(toolGraph('{"q": "{{input}}"}'))).graph.nodes[1].data.tool, 'search_records');
});

const casesGraph = (): Graph => ({
    nodes: [
        node('s', 'start'),
        node('if', 'condition', {
            cases: [
                {
                    id: 'big',
                    logical_operator: 'and',
                    conditions: [
                        { variable: '{{input}}', operator: 'ge', value: '100' },
                        { variable: '{{input}}', operator: 'not_contains', value: '.' },
                    ],
                },
                {
                    id: 'word',
                    logical_operator: 'or',
                    conditions: [
                        { variable: '{{input}}', operator: 'starts_with', value: 'xin' },
                        { variable: '{{input}}', operator: 'empty' },
                    ],
                },
            ],
        }),
        node('ob', 'output', { template: 'lớn' }),
        node('ow', 'output', { template: 'chữ' }),
        node('oe', 'output', { template: 'khác' }),
    ],
    edges: [edge('s', 'if'), edge('if', 'ob', 'big'), edge('if', 'ow', 'word'), edge('if', 'oe', 'else')],
});

test('multi-branch condition picks the first matching case, else otherwise, without crashing on non-numbers', async () => {
    const run = async (input: string) => (await runWorkflow(store, 'w', config(casesGraph()), input)).output;
    assert.equal(await run('250'), 'lớn');
    assert.equal(await run('250.5'), 'khác');
    assert.equal(await run('xin chào'), 'chữ');
    assert.equal(await run('   '), 'chữ');
    assert.equal(await run('abc'), 'khác');
    const result = await runWorkflow(store, 'w', config(casesGraph()), '99');
    assert.equal(result.trace.find((t) => t.node_id === 'if')!.output, 'else');
    assert.equal(result.trace.find((t) => t.node_id === 'ob')!.status, 'skipped');
    assert.equal(configInput(config(casesGraph())).graph.nodes[1].data.cases!.length, 2);
});

test('multi-branch condition requires every case and else connected and rejects bad handles', () => {
    const noElse = casesGraph();
    noElse.edges = noElse.edges.filter((e) => e.sourceHandle !== 'else');
    noElse.nodes = noElse.nodes.filter((n) => n.id !== 'oe');
    assert.throws(() => validateGraph(noElse), /chưa nối nhánh else/);
    const stray = casesGraph();
    stray.edges.push(edge('if', 'oe', 'true'));
    assert.throws(() => validateGraph(stray), /Nhánh true không có/);
    const reserved = casesGraph();
    reserved.nodes[1].data.cases![0].id = 'else';
    assert.throws(() => validateGraph(reserved), /dành cho nhánh còn lại/);
    const empty = casesGraph();
    empty.nodes[1].data.cases![0].conditions = [];
    assert.throws(() => validateGraph(empty), /ít nhất một điều kiện/);
    const unknown = casesGraph();
    unknown.nodes[1].data.cases![0].conditions[0].value = '{{nope}}';
    assert.throws(() => validateGraph(unknown), /không tồn tại/);
    assert.throws(
        () =>
            configInput(
                config({
                    ...casesGraph(),
                    nodes: casesGraph().nodes.map((n) =>
                        n.id === 'if' ? { ...n, data: { label: 'if', cases: [{ id: 'a', logical_operator: 'xor', conditions: [] }] } } : n,
                    ),
                } as Graph),
            ),
        /Toán tử logic/,
    );
});

test('comparison operators cover text and numeric semantics', () => {
    assert.equal(compare('contains', 'abc', 'b'), true);
    assert.equal(compare('ends_with', 'abc', 'bc'), true);
    assert.equal(compare('is', 'a', 'a'), true);
    assert.equal(compare('is_not', 'a', 'a'), false);
    assert.equal(compare('not_empty', ' x ', ''), true);
    assert.equal(compare('eq', '1.0', '1'), true);
    assert.equal(compare('ne', '2', '1'), true);
    assert.equal(compare('lt', '-3', '2'), true);
    assert.equal(compare('le', '2', '2'), true);
    assert.equal(compare('gt', '5', ''), false);
    assert.equal(compare('ne', 'abc', '1'), false);
});

const classifierGraph = (): Graph => ({
    nodes: [
        node('s', 'start'),
        node('qc', 'question_classifier', {
            query: '{{input}}',
            instruction: 'Ưu tiên hoàn tiền.',
            classes: [
                { id: 'refund', name: 'Hoàn tiền' },
                { id: 'ship', name: 'Giao hàng' },
            ],
        }),
        node('or', 'output', { template: 'R:{{qc}}' }),
        node('os', 'output', { template: 'S:{{qc}}' }),
    ],
    edges: [edge('s', 'qc'), edge('qc', 'or', 'refund'), edge('qc', 'os', 'ship')],
});

test('question classifier branches on the class the model returns and fails with trace on unknown classes', async () => {
    replies.push('{"class_id": "ship"}');
    const shipped = await runWorkflow(store, 'w', config(classifierGraph()), 'Bao giờ giao?');
    assert.equal(shipped.output, 'S:Giao hàng');
    assert.equal(shipped.trace.find((t) => t.node_id === 'or')!.status, 'skipped');
    const prompt = requests.at(-1).messages[0].content as string;
    assert.match(prompt, /refund/);
    assert.match(prompt, /Ưu tiên hoàn tiền/);
    assert.equal(requests.at(-1).messages[1].content, 'Bao giờ giao?');
    replies.push('Hoàn tiền.');
    assert.equal((await runWorkflow(store, 'w', config(classifierGraph()), 'Trả lại tiền')).output, 'R:Hoàn tiền');
    replies.push('Tôi không biết');
    const failed = await traceOf(runWorkflow(store, 'w', config(classifierGraph()), '???'));
    assert.equal(failed.status, 502);
    assert.match(failed.message, /nhóm không hợp lệ/);
    const entry = failed.trace.find((t) => t.node_id === 'qc')!;
    assert.equal(entry.status, 'error');
    assert.equal(entry.input, '???');
    const missing = classifierGraph();
    missing.edges = missing.edges.filter((e) => e.sourceHandle !== 'ship');
    missing.nodes = missing.nodes.filter((n) => n.id !== 'os');
    assert.throws(() => validateGraph(missing), /chưa nối nhánh ship/);
    const duplicate = classifierGraph();
    duplicate.nodes[1].data.classes![1].id = 'refund';
    assert.throws(() => validateGraph(duplicate), /trùng ID/);
});

test('class parsing accepts ids, names, fenced JSON and a single mention', () => {
    const classes = [
        { id: 'a1', name: 'Kỹ thuật' },
        { id: 'b2', name: 'Kế toán' },
    ];
    assert.equal(parseClass('```json\n{"name": "kế toán"}\n```', classes)?.id, 'b2');
    assert.equal(parseClass('"a1"', classes)?.id, 'a1');
    assert.equal(parseClass('Nhóm phù hợp là Kỹ thuật vì ...', classes)?.id, 'a1');
    assert.equal(parseClass('Kỹ thuật hoặc Kế toán', classes), undefined);
});

const extractorGraph = (template = '{{px.amount}}|{{px.urgent}}|{{px.tags}}|{{px.note}}'): Graph => ({
    nodes: [
        node('s', 'start'),
        node('px', 'parameter_extractor', {
            query: '{{input}}',
            parameters: [
                { name: 'amount', type: 'number', description: 'Số tiền', required: true },
                { name: 'urgent', type: 'boolean', description: '', required: false },
                { name: 'kind', type: 'select', description: '', required: true, options: ['tạm ứng', 'hoàn ứng'] },
                { name: 'tags', type: 'array[string]', description: '', required: false },
                { name: 'note', type: 'string', description: '', required: false },
            ],
        }),
        node('o', 'output', { template }),
    ],
    edges: [edge('s', 'px'), edge('px', 'o')],
});

test('parameter extractor uses JSON mode, coerces types and exposes fields via dotted variables', async () => {
    replies.push(JSON.stringify({ amount: '1500000', urgent: true, kind: 'tạm ứng', tags: ['a', 'b'], extra: 'drop' }));
    const result = await runWorkflow(store, 'w', config(extractorGraph()), 'Tạm ứng 1,5 triệu gấp');
    assert.ok(requests.at(-1).response_format);
    assert.equal(result.output, '1500000|true|["a","b"]|');
    assert.deepEqual(JSON.parse(result.trace.find((t) => t.node_id === 'px')!.output), {
        amount: 1500000,
        urgent: true,
        kind: 'tạm ứng',
        tags: ['a', 'b'],
        note: null,
    });
    replies.push(JSON.stringify({ urgent: false, kind: 'tạm ứng' }));
    assert.match((await traceOf(runWorkflow(store, 'w', config(extractorGraph()), 'x'))).message, /bắt buộc amount/);
    replies.push(JSON.stringify({ amount: 1, kind: 'khác' }));
    assert.match((await traceOf(runWorkflow(store, 'w', config(extractorGraph()), 'x'))).message, /danh sách lựa chọn/);
    replies.push(JSON.stringify({ amount: 'nhiều', kind: 'tạm ứng' }));
    assert.match((await traceOf(runWorkflow(store, 'w', config(extractorGraph()), 'x'))).message, /kiểu number/);
});

test('dotted variables are validated against extractor parameters and fail clearly at runtime on non-objects', async () => {
    assert.throws(() => validateGraph(extractorGraph('{{px.missing}}')), /không có trường missing/);
    assert.throws(() => validateGraph(extractorGraph('{{nope.amount}}')), /không tồn tại/);
    const select = extractorGraph();
    select.nodes[1].data.parameters![2].options = [];
    assert.throws(() => validateGraph(select), /danh sách giá trị/);
    const plain: Graph = {
        nodes: [node('s', 'start'), node('t', 'template', { template: 'text' }), node('o', 'output', { template: '{{t.field}}' })],
        edges: [edge('s', 't'), edge('t', 'o')],
    };
    assert.deepEqual(validateGraph(plain), ['s', 't', 'o']);
    assert.match((await traceOf(runWorkflow(store, 'w', config(plain), 'x'))).message, /không phải JSON object/);
    plain.nodes[1].data.template = '{"other": 1}';
    assert.match((await traceOf(runWorkflow(store, 'w', config(plain), 'x'))).message, /không có trường field/);
    plain.nodes[1].data.template = '{"field": {"n": 1}}';
    assert.equal((await runWorkflow(store, 'w', config(plain), 'x')).output, '{"n":1}');
});

test('list operator filters, orders, limits and extracts JSON arrays', async () => {
    const items = JSON.stringify([
        { name: 'b', qty: 5, kind: 'đồ uống' },
        { name: 'a', qty: 12, kind: 'đồ ăn' },
        { name: 'c', qty: 30, kind: 'đồ ăn' },
        { name: 'd', qty: 'n/a', kind: 'đồ ăn' },
    ]);
    const graph: Graph = {
        nodes: [
            node('s', 'start'),
            node('l', 'list_operator', {
                variable: '{{input}}',
                filter: { key: 'qty', operator: 'gt', value: '6' },
                order: { key: 'qty', dir: 'desc' },
                limit: 2,
            }),
            node('o', 'output', { template: '{{l}}' }),
        ],
        edges: [edge('s', 'l'), edge('l', 'o')],
    };
    assert.deepEqual(
        JSON.parse((await runWorkflow(store, 'w', config(graph), items)).output).map((x: any) => x.name),
        ['c', 'a'],
    );
    graph.nodes[1].data.extract = 'last';
    assert.equal(JSON.parse((await runWorkflow(store, 'w', config(graph), items)).output).name, 'a');
    graph.nodes[1].data.extract = 3;
    assert.match((await traceOf(runWorkflow(store, 'w', config(graph), items))).message, /không có phần tử 3/);
    assert.match((await traceOf(runWorkflow(store, 'w', config(graph), 'không phải mảng'))).message, /mảng JSON/);
    assert.deepEqual(
        operateList('["b","a","c"]', { label: '', order: { dir: 'asc' }, extract: 'first' }, (t) => t),
        'a',
    );
    assert.deepEqual(
        operateList('["xa","b","xc"]', { label: '', filter: { operator: 'starts_with', value: 'x' } }, (t) => t),
        ['xa', 'xc'],
    );
    const parsed = configInput(config(graph)).graph.nodes[1].data;
    assert.deepEqual([parsed.limit, parsed.extract, parsed.order?.dir], [2, 3, 'desc']);
    assert.throws(
        () =>
            configInput(config({ ...graph, nodes: graph.nodes.map((n) => (n.id === 'l' ? { ...n, data: { ...n.data, limit: 0 } } : n)) })),
        /1–1000/,
    );
});
