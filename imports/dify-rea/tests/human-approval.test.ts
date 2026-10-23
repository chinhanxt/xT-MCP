// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Node "Người duyệt": pauses on a real xT request (create_record + submit_request) and resumes only after the request
// was decided in xT; Studio itself never calls decide_request.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { Store } from '../src/store.ts';
import { emptyConfig, xtToolNames } from '../src/contracts.ts';
import type { AppConfig, Graph, GraphNode } from '../src/contracts.ts';
import { configInput } from '../src/validation.ts';
import { runWorkflow, validateGraph } from '../src/workflow.ts';
import { xtTools, type XtTools } from '../src/xt-tools.ts';
import { integratedStudio, type CoreCall } from './xt-fixture.ts';
import { createTriggerRunner, xtTriggerDenied } from '../src/triggers.ts';
import { scheduleInput } from '../src/schedule.ts';

const node = (id: string, type: GraphNode['type'], data: Partial<GraphNode['data']> = {}): GraphNode => ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: { label: id, ...data },
});
const approvalGraph = (extra: Partial<GraphNode['data']> = {}): Graph => ({
    nodes: [
        node('start', 'start'),
        node('approval', 'human_approval', {
            title: 'Duyệt chi {{input}}',
            summary: 'Nội dung: {{input}}',
            approver_hint: 'Trưởng phòng',
            request_model: 'advance_request',
            request_values: '{"amount":"{{input}}","destination":"Đà Nẵng"}',
            ...extra,
        }),
        node('ok', 'output', { template: 'Đã duyệt {{input}} ({{approval.state}})' }),
        node('no', 'output', { template: 'Bị từ chối {{input}}' }),
    ],
    edges: [
        { id: 'e1', source: 'start', target: 'approval', sourceHandle: null },
        { id: 'e2', source: 'approval', target: 'ok', sourceHandle: 'approved' },
        { id: 'e3', source: 'approval', target: 'no', sourceHandle: 'rejected' },
    ],
});
const config = (graph: Graph): AppConfig => ({ ...emptyConfig(), graph });

// Programmable xT: one request per create_record; `states` is what a human decided on Desk.
function fakeXt() {
    const states = new Map<string, string>();
    let count = 0,
        gate: Promise<void> | undefined;
    return {
        states,
        hold(promise: Promise<void>) {
            gate = promise;
        },
        async handle(call: CoreCall): Promise<unknown> {
            const a = call.arguments;
            if (call.name === 'describe_model')
                return {
                    ok: true,
                    data: {
                        model: {
                            id: a.model_id,
                            primitive: ['advance_request', 'approval_ticket'].includes(String(a.model_id)) ? 'request' : 'record',
                            fields:
                                a.model_id === 'approval_ticket'
                                    ? [
                                          { id: 'title', type: 'text', max_length: 20 },
                                          { id: 'content', type: 'text', max_length: 4000 },
                                          { id: 'amount', type: 'money' },
                                      ]
                                    : [{ id: 'amount', type: 'money' }],
                        },
                    },
                };
            if (call.name === 'create_record') {
                const ref = `${String(a.model_id)}:r${++count}`;
                states.set(ref, 'draft');
                return { ok: true, data: { record: { ref, state: 'draft', revision: 1 } } };
            }
            if (call.name === 'submit_request') {
                states.set(String(a.ref), 'in_review');
                return {
                    ok: true,
                    data: { request: { ref: a.ref, state: 'in_review', revision: 2 }, route: [], process_instance: 'process_instance:p1' },
                };
            }
            if (call.name === 'get_record') {
                if (gate) await gate;
                const state = states.get(String(a.ref));
                return state
                    ? { ok: true, data: { record: { ref: a.ref, state, revision: 3 } } }
                    : { ok: false, error: { code: 'not_found' } };
            }
            return { ok: false, error: { code: 'access_denied' } };
        },
    };
}
const toolsOf = (xt: ReturnType<typeof fakeXt>, calls: CoreCall[] = []): XtTools => ({
    async list() {
        return [];
    },
    async call(name, args) {
        calls.push({ name, arguments: args });
        return xt.handle({ name, arguments: args });
    },
});

async function withStore<T>(work: (store: Store) => Promise<T>) {
    const dir = mkdtempSync(join(tmpdir(), 'xt-approval-'));
    const store = new Store(dir);
    try {
        return await work(store);
    } finally {
        store.close();
        rmSync(dir, { recursive: true, force: true });
    }
}

test('graph rules: both branches required, title/summary required, old graphs unaffected', () => {
    const graph = approvalGraph();
    assert.deepEqual(validateGraph(graph), ['start', 'approval', 'ok', 'no']);
    assert.throws(
        () => validateGraph({ ...graph, nodes: graph.nodes.slice(0, 3), edges: graph.edges.slice(0, 2) }),
        /chưa nối nhánh rejected/,
    );
    assert.throws(
        () => validateGraph({ ...graph, edges: [...graph.edges.slice(0, 2), { ...graph.edges[2], sourceHandle: 'maybe' }] }),
        /Nhánh maybe/,
    );
    assert.throws(() => validateGraph(approvalGraph({ title: ' ' })), /cần tiêu đề và nội dung/);
    assert.throws(() => validateGraph(approvalGraph({ summary: '{{missing}}' })), /Biến missing không tồn tại/);
    assert.throws(
        () =>
            configInput({
                ...config(graph),
                graph: { ...graph, nodes: [graph.nodes[0], { ...graph.nodes[1], data: { ...graph.nodes[1].data, timeout_hours: -1 } }] },
            }),
        /Hạn duyệt/,
    );
    assert.throws(
        () =>
            configInput({
                ...config(graph),
                graph: { ...graph, nodes: [graph.nodes[0], { ...graph.nodes[1], data: { ...graph.nodes[1].data, retries: 1 } }] },
            }),
        /Retry\/fallback/,
    );
    const parsed = configInput(config(approvalGraph({ timeout_hours: 24 })));
    assert.equal(parsed.graph.nodes[1].data.timeout_hours, 24);
    assert.equal(parsed.graph.nodes[1].data.request_model, 'advance_request');
    assert.deepEqual(validateGraph(emptyConfig().graph), ['start', 'llm', 'output']);
});

test('decide_request is never callable from Studio', () => {
    assert.ok(!xtToolNames.includes('decide_request'));
    const graph: Graph = {
        nodes: [node('start', 'start'), node('t', 'xt_tool', { tool: 'decide_request', args: '{}' }), node('out', 'output')],
        edges: [
            { id: 'a', source: 'start', target: 't' },
            { id: 'b', source: 't', target: 'out' },
        ],
    };
    assert.throws(() => configInput(config(graph)), /decide_request/);
    const client = xtTools('http://127.0.0.1:9', Buffer.alloc(32), 'a'.repeat(43));
    assert.throws(() => client.call('decide_request', { ref: 'advance_request:r1', decision: 'approve' }), /không được cấp/);
});

test('not integrated: node errors clearly and creates nothing', () =>
    withStore(async (store) => {
        await assert.rejects(
            runWorkflow(store, 'w', config(approvalGraph()), '5000000'),
            /Node Người duyệt chỉ chạy khi Studio được mở từ xT\./,
        );
    }));

test('pause creates and submits a real request, then resume continues from the saved position', () =>
    withStore(async (store) => {
        const xt = fakeXt(),
            calls: CoreCall[] = [],
            tools = toolsOf(xt, calls);
        const paused = await runWorkflow(store, 'w', config(approvalGraph({ timeout_hours: 2 })), '5000000', undefined, {}, tools, true, {
            id: 'run-1',
        });
        assert.equal(paused.status, 'waiting');
        assert.equal(paused.request_ref, 'advance_request:r1');
        assert.match(paused.output, /Đang chờ duyệt/);
        assert.deepEqual(
            calls.map((c) => c.name),
            ['describe_model', 'create_record', 'submit_request'],
        );
        assert.deepEqual(calls[1].arguments.values, { amount: '5000000', destination: 'Đà Nẵng' });
        assert.match(String(calls[1].arguments.idempotency_key), /^[A-Za-z0-9_.:-]{8,128}$/);
        assert.match(String(calls[2].arguments.note), /Duyệt chi 5000000[\s\S]*Nội dung: 5000000[\s\S]*Người duyệt đề xuất: Trưởng phòng/);
        const snapshot = paused.waiting!;
        assert.equal(snapshot.node_id, 'approval');
        assert.equal(snapshot.index, 1);
        assert.equal(snapshot.timeout_hours, 2);
        assert.deepEqual(snapshot.active, ['start']);
        assert.equal(snapshot.trace.at(-1)!.status, 'waiting');
        // A retry of the same run reuses the same idempotency keys, so xT never gets a second draft.
        const again: CoreCall[] = [];
        await runWorkflow(store, 'w', config(approvalGraph()), '5000000', undefined, {}, toolsOf(fakeXt(), again), true, { id: 'run-1' });
        assert.equal(again[1].arguments.idempotency_key, calls[1].arguments.idempotency_key);
        const done = await runWorkflow(store, 'w', config(approvalGraph()), '', undefined, {}, tools, true, {
            id: 'run-1',
            resume: { snapshot, decision: 'approved' },
        });
        assert.equal(done.output, 'Đã duyệt 5000000 (approved)');
        assert.deepEqual(
            done.trace.map((t) => [t.node_id, t.status]),
            [
                ['start', 'success'],
                ['approval', 'success'],
                ['ok', 'success'],
                ['no', 'skipped'],
            ],
        );
        assert.ok(!calls.some((c) => c.name === 'decide_request'));
    }));

test('default approval_ticket: title and content come from the node, explicit values win', () =>
    withStore(async (store) => {
        const calls: CoreCall[] = [];
        const graph = approvalGraph({ request_model: 'approval_ticket', request_values: '{"amount":"{{input}}"}' });
        const paused = await runWorkflow(store, 'w', config(graph), '5000000', undefined, {}, toolsOf(fakeXt(), calls), true, {
            id: 'run-t',
        });
        assert.equal(paused.status, 'waiting');
        assert.equal(paused.request_ref, 'approval_ticket:r1');
        // title truncated to the model's max_length (20) after rendering
        assert.deepEqual(calls[1].arguments, {
            model_id: 'approval_ticket',
            values: { amount: '5000000', title: 'Duyệt chi 5000000', content: 'Nội dung: 5000000' },
            idempotency_key: calls[1].arguments.idempotency_key,
        });
        const explicit: CoreCall[] = [];
        await runWorkflow(
            store,
            'w',
            config(approvalGraph({ request_model: 'approval_ticket', request_values: '{"title":"Riêng","content":"Tự ghi"}' })),
            'x',
            undefined,
            {},
            toolsOf(fakeXt(), explicit),
            true,
            { id: 'run-e' },
        );
        assert.deepEqual(explicit[1].arguments.values, { title: 'Riêng', content: 'Tự ghi' });
        const long: CoreCall[] = [];
        await runWorkflow(
            store,
            'w',
            config(approvalGraph({ request_model: 'approval_ticket', title: 'Tiêu đề rất dài vượt quá hai mươi ký tự {{input}}' })),
            'x',
            undefined,
            {},
            toolsOf(fakeXt(), long),
            true,
            { id: 'run-l' },
        );
        assert.equal((long[1].arguments.values as Record<string, unknown>).title, 'Tiêu đề rất dài vượt');
    }));

test('editor default for a new Người duyệt node is approval_ticket', async () => {
    const { readFileSync } = await import('node:fs');
    const editor = readFileSync(new URL('../src/web/WorkflowEditor.tsx', import.meta.url), 'utf8');
    assert.match(editor, /request_model: 'approval_ticket', request_values: '\{\}'/);
    assert.match(editor, /<option value="advance_request">/);
});

test('non-request model is refused before anything is created', () =>
    withStore(async (store) => {
        const calls: CoreCall[] = [];
        await assert.rejects(
            runWorkflow(
                store,
                'w',
                config(approvalGraph({ request_model: 'proposal' })),
                'x',
                undefined,
                {},
                toolsOf(fakeXt(), calls),
                true,
            ),
            /không phải loại yêu cầu duyệt/,
        );
        assert.deepEqual(
            calls.map((c) => c.name),
            ['describe_model'],
        );
    }));

async function pausedRun(extra: Partial<GraphNode['data']> = {}) {
    const s = await integratedStudio(),
        xt = fakeXt();
    s.core.handler = (call) => xt.handle(call);
    const app = (await s.call('/api/apps', 'POST', { name: 'Chi tiền', kind: 'workflow' })).value;
    const saved = await s.call(`/api/apps/${app.id}`, 'PUT', { name: app.name, description: '', config: config(approvalGraph(extra)) });
    assert.equal(saved.status, 200, JSON.stringify(saved.value));
    const run = await s.call(`/api/apps/${app.id}/run`, 'POST', { input: '5000000', mode: 'draft' });
    assert.equal(run.status, 200, JSON.stringify(run.value));
    return { s, xt, run: run.value, app };
}

test('HTTP: run pauses as waiting, pending resume is 409, approved resume completes once', async () => {
    const { s, xt, run, app } = await pausedRun({ timeout_hours: 24 });
    try {
        assert.equal(run.status, 'waiting');
        assert.equal(run.request_ref, 'advance_request:r1');
        assert.equal(run.waiting, undefined);
        const row = s.store.db.prepare('SELECT status FROM runs WHERE id=?').get(run.run_id)!;
        assert.equal(row.status, 'waiting');
        const wait = s.store.db.prepare('SELECT request_ref,node_id,expires_at,snapshot FROM run_waits WHERE run_id=?').get(run.run_id)!;
        assert.equal(wait.request_ref, 'advance_request:r1');
        assert.equal(wait.node_id, 'approval');
        assert.ok(Number(wait.expires_at) > Date.now());
        assert.equal(JSON.parse(String(wait.snapshot)).index, 1);
        const create = s.calls.find((c) => c.name === 'create_record')!;
        assert.match(String(create.arguments.idempotency_key), /^studio\.[0-9a-f]{40}\.create$/);
        const listed = await s.call(`/api/apps/${app.id}/runs?status=waiting`);
        assert.equal(listed.value.items.length, 1);

        const pending = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(pending.status, 409);
        assert.equal(pending.value.error, 'Yêu cầu chưa được duyệt.');
        assert.equal(s.store.db.prepare('SELECT status FROM runs WHERE id=?').get(run.run_id)!.status, 'waiting');

        xt.states.set('advance_request:r1', 'approved');
        const done = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(done.status, 200, JSON.stringify(done.value));
        assert.equal(done.value.output, 'Đã duyệt 5000000 (approved)');
        const detail = await s.call(`/api/runs/${run.run_id}`);
        assert.equal(detail.value.status, 'success');
        assert.equal(detail.value.result.output, 'Đã duyệt 5000000 (approved)');

        const twice = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(twice.status, 409);
        assert.deepEqual([...new Set(s.calls.map((c) => c.name))].sort(), [
            'create_record',
            'describe_model',
            'get_record',
            'submit_request',
        ]);
    } finally {
        await s.close();
    }
});

test('HTTP: rejected request follows the rejected branch', async () => {
    const { s, xt, run } = await pausedRun();
    try {
        xt.states.set('advance_request:r1', 'rejected');
        const done = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(done.status, 200, JSON.stringify(done.value));
        assert.equal(done.value.output, 'Bị từ chối 5000000');
        assert.equal(done.value.trace.find((t: any) => t.node_id === 'ok').status, 'skipped');
    } finally {
        await s.close();
    }
});

test('HTTP: concurrent resumes, exactly one proceeds', async () => {
    const { s, xt, run } = await pausedRun();
    try {
        xt.states.set('advance_request:r1', 'approved');
        let open!: () => void;
        xt.hold(new Promise<void>((resolve) => (open = resolve)));
        const first = s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        await delay(50);
        const second = s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        await delay(50);
        open();
        const statuses = (await Promise.all([first, second])).map((r) => r.status).sort();
        assert.deepEqual(statuses, [200, 409]);
        assert.equal(s.calls.filter((c) => c.name === 'get_record').length, 1);
    } finally {
        await s.close();
    }
});

test('HTTP: expired wait errors and marks the run error', async () => {
    const { s, xt, run } = await pausedRun({ timeout_hours: 1 });
    try {
        xt.states.set('advance_request:r1', 'approved');
        s.store.db.prepare('UPDATE run_waits SET expires_at=? WHERE run_id=?').run(Date.now() - 1, run.run_id);
        const expired = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(expired.status, 410);
        assert.equal(expired.value.error, 'Đã quá hạn duyệt.');
        const row = s.store.db.prepare('SELECT status,error FROM runs WHERE id=?').get(run.run_id)!;
        assert.deepEqual([row.status, row.error], ['error', 'Đã quá hạn duyệt.']);
        assert.equal((await s.call(`/api/runs/${run.run_id}/resume`, 'POST')).status, 409);
    } finally {
        await s.close();
    }
});

test('HTTP: missing request keeps waiting; no xT session cannot resume; viewer may resume own run', async () => {
    const { s, xt, run } = await pausedRun();
    try {
        xt.states.delete('advance_request:r1');
        const missing = await s.call(`/api/runs/${run.run_id}/resume`, 'POST');
        assert.equal(missing.status, 404);
        assert.equal(s.store.db.prepare('SELECT status FROM runs WHERE id=?').get(run.run_id)!.status, 'waiting');
        const noSession = await s.call(`/api/runs/${run.run_id}/resume`, 'POST', undefined, { capability: false });
        assert.equal(noSession.status, 400);
        assert.equal(noSession.value.error, 'Node Người duyệt chỉ chạy khi Studio được mở từ xT.');
        xt.states.set('advance_request:r1', 'approved');
        const viewer = await s.call(`/api/runs/${run.run_id}/resume`, 'POST', undefined, { roles: ['studio_user'] });
        assert.equal(viewer.status, 200, JSON.stringify(viewer.value));
    } finally {
        await s.close();
    }
});

test('scheduled runs have no xT capability: they error and never pause', () =>
    withStore(async (store) => {
        const cfg = JSON.stringify(config(approvalGraph()));
        store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run('w', 'W');
        store.db
            .prepare(
                'INSERT INTO apps(id,workspace_id,name,description,kind,config,published,version,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',
            )
            .run('a', 'w', 'Wf', '', 'workflow', cfg, cfg, 1, new Date().toISOString());
        const trig = {
            id: 't',
            app_id: 'a',
            workspace_id: 'w',
            kind: 'schedule' as const,
            config: scheduleInput({ frequency: 'hourly' }),
            enabled: true,
            created_by: 'p',
            last_fired_at: null,
            created_at: '',
        };
        for (const [integrated, message] of [
            [true, xtTriggerDenied],
            [false, 'Node Người duyệt chỉ chạy khi Studio được mở từ xT.'],
        ] as const) {
            const { run_id, done } = createTriggerRunner(store, { integrated }).fire(trig);
            await assert.rejects(done, (error: Error) => error.message === message);
            assert.equal(store.db.prepare('SELECT status FROM runs WHERE id=?').get(run_id)!.status, 'error');
        }
        assert.equal(store.db.prepare('SELECT count(*) AS n FROM run_waits').get()!.n, 0);
    }));
