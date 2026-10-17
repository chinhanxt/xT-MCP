// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync, copyFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import { emptyConfig } from '../src/contracts.ts';
import type { Graph, GraphNode, RunResult, StudioApp } from '../src/contracts.ts';
import { calculate } from '../src/agent.ts';
import { chunkText, indexDocument, retrieve } from '../src/knowledge.ts';
import { runWorkflow, validateGraph } from '../src/workflow.ts';
import { configInput } from '../src/validation.ts';
const dir = mkdtempSync(join(tmpdir(), 'xt-studio-test-')),
  store = new Store(dir),
  app = createStudio(store);
let origin = '',
  mockUrl = '',
  cookie = '',
  workspace = '',
  modelId = '';
let failures = false;
let invalid: boolean | 'null' = false;
let concurrentBarrier: Promise<void> | null = null;
let releaseConcurrent = () => {};
let arrivals = 0;
let repeatTool = false;
const mock = createServer(async (req, res) => {
  const parts: Buffer[] = [];
  for await (const p of req) parts.push(p);
  const body = JSON.parse(Buffer.concat(parts).toString('utf8'));
  if (failures) {
    res.writeHead(503);
    res.end('private provider details');
    return;
  }
  if (invalid) {
    res.end(JSON.stringify(invalid === 'null' ? null : { wrong: true }));
    return;
  }
  if (req.url === '/embeddings') {
    res.end(
      JSON.stringify({
        data: body.input.map((text: string, index: number) => ({ index, embedding: /quy trình|tạm ứng/i.test(text) ? [1, 0] : [0, 1] })),
      }),
    );
    return;
  }
  const last = body.messages.at(-1);
  if (last.content === 'concurrent' && concurrentBarrier) {
    arrivals++;
    if (arrivals === 2) releaseConcurrent();
    await concurrentBarrier;
  }
  if (body.tools && (last.role === 'user' || repeatTool)) {
    res.end(
      JSON.stringify({
        choices: [
          {
            message: {
              content: null,
              tool_calls: [
                { id: 'tool-1', type: 'function', function: { name: 'calculator', arguments: '{"expression":"(12 + 3) * 2"}' } },
              ],
            },
          },
        ],
      }),
    );
    return;
  }
  res.end(
    JSON.stringify({
      choices: [{ message: { role: 'assistant', content: last.role === 'tool' ? `Result: ${last.content}` : `Echo: ${last.content}` } }],
    }),
  );
});
async function call(path: string, method = 'GET', data?: unknown, auth = cookie) {
  const response = await fetch(origin + '/api' + path, {
    method,
    headers: { Cookie: auth, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  return { status: response.status, body: (await response.json()) as any, cookie: response.headers.get('set-cookie')?.split(';')[0] };
}
before(async () => {
  await new Promise<void>((resolve) => mock.listen(0, '127.0.0.1', resolve));
  mockUrl = `http://127.0.0.1:${(mock.address() as AddressInfo).port}`;
  await new Promise<void>((resolve) => app.listen(0, '127.0.0.1', resolve));
  origin = `http://127.0.0.1:${(app.address() as AddressInfo).port}`;
});
after(async () => {
  await new Promise<void>((r) => app.close(() => r()));
  await new Promise<void>((r) => mock.close(() => r()));
  store.close();
  rmSync(dir, { recursive: true, force: true });
});
test('setup, login, sessions and cross-origin mutation checks', async () => {
  assert.equal((await call('/models', 'GET', undefined, '')).status, 401);
  assert.equal((await call('/setup', 'POST', { workspace_name: 'QA', email: 'qa@example.test', password: 'short' })).status, 400);
  const setup = await call('/setup', 'POST', { workspace_name: 'QA', email: 'qa@example.test', password: 'temporary-test-password' });
  assert.equal(setup.status, 201);
  cookie = setup.cookie!;
  const session = await call('/session');
  workspace = session.body.user.workspace_id;
  assert.equal(session.body.user.workspace_name, 'QA');
  assert.equal((await call('/setup', 'POST', {})).status, 409);
  assert.equal((await call('/login', 'POST', { email: 'qa@example.test', password: 'wrong-password' })).status, 401);
  const login = await call('/login', 'POST', { email: 'qa@example.test', password: 'temporary-test-password' });
  assert.equal(login.status, 200);
  const foreign = await fetch(origin + '/api/apps', {
    method: 'POST',
    headers: { Cookie: cookie, Origin: 'https://unrelated.example', 'Content-Type': 'application/json' },
    body: '{}',
  });
  assert.equal(foreign.status, 403);
});
test('model CRUD encrypts secrets and never returns them', async () => {
  assert.equal(
    (await call('/models', 'POST', { name: 'bad', provider: 'openai', base_url: 'file:///etc', model: 'test', kind: 'chat' })).status,
    400,
  );
  const created = await call('/models', 'POST', {
    name: 'Test model',
    provider: 'openai',
    base_url: mockUrl,
    model: 'echo',
    kind: 'chat',
    api_key: 'test-secret-do-not-return',
  });
  assert.equal(created.status, 200);
  modelId = created.body.id;
  const list = await call('/models');
  assert.equal(list.body[0].has_key, 1);
  assert.ok(!JSON.stringify(list.body).includes('test-secret'));
  const row = store.db.prepare('SELECT secret FROM models WHERE id=?').get(modelId)!;
  assert.ok(!String(row.secret).includes('test-secret'));
  assert.equal(store.model(workspace, modelId).api_key, 'test-secret-do-not-return');
  assert.equal((await call(`/models/${modelId}/test`, 'POST', {})).status, 200);
});
test('knowledge indexing, retrieval, document disable and workspace isolation', async () => {
  const dataset = await call('/knowledge', 'POST', { name: 'Quy định', description: '' });
  assert.equal(dataset.status, 201);
  const id = dataset.body.id;
  const indexed = await call(`/knowledge/${id}/documents`, 'POST', {
    name: 'tam-ung.md',
    content: 'Quy trình tạm ứng: nhân viên lập đề nghị và trưởng phòng phê duyệt.',
  });
  assert.equal(indexed.status, 201);
  const hits = await call(`/knowledge/${id}/search`, 'POST', { query: 'tạm ứng' });
  assert.ok(hits.body.length);
  assert.equal(hits.body[0].document_name, 'tam-ung.md');
  await call(`/documents/${indexed.body.id}`, 'PATCH', { enabled: false });
  assert.equal((await call(`/knowledge/${id}/search`, 'POST', { query: 'tạm ứng' })).body.length, 0);
  await call(`/documents/${indexed.body.id}`, 'PATCH', { enabled: true });
  await assert.rejects(() => retrieve(store, 'another-workspace', [id], 'tạm ứng'), /Không tìm thấy/);
  await call(`/documents/${indexed.body.id}`, 'DELETE');
  assert.equal((await call(`/knowledge/${id}/search`, 'POST', { query: 'tạm ứng' })).body.length, 0);
});
test('vector retrieval persists embeddings and excludes failed indexing', async () => {
  const model = await call('/models', 'POST', { name: 'Embed', provider: 'openai', base_url: mockUrl, model: 'embed', kind: 'embedding' });
  const dataset = await call('/knowledge', 'POST', { name: 'Vector', embedding_model_id: model.body.id });
  const id = dataset.body.id;
  await indexDocument(store, workspace, id, 'Quy định', 'Quy trình tạm ứng của công ty.');
  await indexDocument(store, workspace, id, 'Khác', 'Weather and music');
  assert.equal((await retrieve(store, workspace, [id], 'quy trình tạm ứng'))[0].document_name, 'Quy định');
  failures = true;
  await assert.rejects(() => indexDocument(store, workspace, id, 'Lỗi', 'quy trình thử lỗi'), /HTTP 503/);
  failures = false;
  assert.equal(store.db.prepare("SELECT count(*) AS count FROM documents WHERE knowledge_id=? AND status='error'").get(id)!.count, 1);
  assert.equal(
    (await retrieve(store, workspace, [id], 'quy trình')).some((c) => c.document_name === 'Lỗi'),
    false,
  );
});
test('chat persists history, publish keeps snapshot, failed calls are recorded', async () => {
  const created = (await call('/apps', 'POST', { name: 'Chat QA', kind: 'chat' })).body as StudioApp;
  created.config.model_id = modelId;
  assert.equal((await call(`/apps/${created.id}`, 'PUT', created)).status, 200);
  const published = await call(`/apps/${created.id}/publish`, 'POST', {});
  assert.equal(published.body.version, 1);
  const run = await call(`/apps/${created.id}/run`, 'POST', { input: 'Xin chào', mode: 'published' });
  assert.equal(run.status, 200);
  assert.equal(run.body.output, 'Echo: Xin chào');
  const messages = (await call(`/apps/${created.id}/conversations/${run.body.conversation_id}`)).body.messages;
  assert.equal(messages.length, 2);
  assert.equal(
    (
      await call(`/apps/${created.id}/run`, 'POST', {
        input: 'Không được trộn bản nháp',
        mode: 'draft',
        conversation_id: run.body.conversation_id,
      })
    ).status,
    404,
  );
  const second = await call(`/apps/${created.id}/run`, 'POST', {
    input: 'Lần hai',
    mode: 'published',
    conversation_id: run.body.conversation_id,
  });
  assert.equal(second.status, 200);
  created.config.system_prompt = 'Đã đổi bản nháp';
  await call(`/apps/${created.id}`, 'PUT', created);
  const after = (await call(`/apps/${created.id}`)).body;
  assert.notEqual(after.config.system_prompt, after.published.system_prompt);
  failures = true;
  const failed = await call(`/apps/${created.id}/run`, 'POST', { input: 'failure' });
  failures = false;
  assert.equal(failed.status, 502);
  assert.ok(!JSON.stringify(failed.body).includes('private provider details'));
  assert.ok((await call(`/runs?app_id=${created.id}`)).body.some((r: any) => r.status === 'error'));
  assert.equal((await call(`/models/${modelId}`, 'DELETE')).status, 409);
  assert.equal((await call(`/apps/${created.id}/conversations/not-owned`)).status, 404);
});
const node = (id: string, type: GraphNode['type'], data: Partial<GraphNode['data']> = {}): GraphNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { label: id, ...data },
});
const branchGraph = (): Graph => ({
  nodes: [
    node('s', 'start'),
    node('if', 'condition', { variable: '{{input}}', operator: 'contains', value: 'yes' }),
    node('a', 'template', { template: 'Đồng ý {{input}}' }),
    node('b', 'template', { template: 'Từ chối {{input}}' }),
    node('oa', 'output', { template: '{{a}}' }),
    node('ob', 'output', { template: '{{b}}' }),
  ],
  edges: [
    { id: '1', source: 's', target: 'if' },
    { id: '2', source: 'if', target: 'a', sourceHandle: 'true' },
    { id: '3', source: 'if', target: 'b', sourceHandle: 'false' },
    { id: '4', source: 'a', target: 'oa' },
    { id: '5', source: 'b', target: 'ob' },
  ],
});
test('workflow conditions activate only the matching branch and expose per-node trace', async () => {
  const config = { ...emptyConfig(), graph: branchGraph() };
  const yes = await runWorkflow(store, workspace, config, 'yes');
  assert.equal(yes.output, 'Đồng ý yes');
  assert.equal(yes.trace.find((t) => t.node_id === 'b')!.status, 'skipped');
  const no = await runWorkflow(store, workspace, config, 'no');
  assert.equal(no.output, 'Từ chối no');
  const app = (await call('/apps', 'POST', { name: 'WF', kind: 'workflow' })).body as StudioApp;
  app.config = config;
  await call(`/apps/${app.id}`, 'PUT', app);
  await call(`/apps/${app.id}/publish`, 'POST', {});
  app.config.graph.nodes.find((n) => n.id === 'a')!.data.template = 'Draft changed';
  await call(`/apps/${app.id}`, 'PUT', app);
  assert.equal((await call(`/apps/${app.id}/run`, 'POST', { input: 'yes', mode: 'published' })).body.output, 'Đồng ý yes');
  assert.equal((await call(`/apps/${app.id}/run`, 'POST', { input: 'yes', mode: 'draft' })).body.output, 'Draft changed');
});
test('workflow rejects cycles, dangling edges, unknown/forward variables and disconnected nodes', () => {
  const cycle = branchGraph();
  cycle.edges.push({ id: 'cycle', source: 'a', target: 'if' });
  assert.throws(() => validateGraph(cycle), /vòng lặp/);
  const missing = branchGraph();
  missing.edges[0].target = 'missing';
  assert.throws(() => validateGraph(missing), /không tồn tại/);
  const unknown = branchGraph();
  unknown.nodes[2].data.template = '{{unknown}}';
  assert.throws(() => validateGraph(unknown), /không tồn tại/);
  const forward = branchGraph();
  forward.nodes[2].data.template = '{{oa}}';
  assert.throws(() => validateGraph(forward), /phải nằm trước/);
  const join = branchGraph();
  join.nodes = join.nodes.filter((n) => n.id !== 'ob');
  join.edges = join.edges.filter((e) => e.id !== '5');
  join.edges.push({ id: 'join', source: 'b', target: 'oa' });
  assert.throws(() => validateGraph(join), /mọi nhánh/);
  const disconnected = branchGraph();
  disconnected.nodes.push(node('lonely', 'output'));
  assert.throws(() => validateGraph(disconnected), /chưa được nối/);
  assert.throws(() => configInput({ ...emptyConfig(), max_steps: 100 }), /1–8/);
});
test('agent executes allowlisted calculator and stops on malformed provider responses', async () => {
  const app = (await call('/apps', 'POST', { name: 'Agent QA', kind: 'agent' })).body as StudioApp;
  app.config.model_id = modelId;
  await call(`/apps/${app.id}`, 'PUT', app);
  const run = await call(`/apps/${app.id}/run`, 'POST', { input: 'Compute (12 + 3) * 2' });
  assert.equal(run.status, 200);
  assert.equal(run.body.output, 'Result: 30');
  assert.equal(run.body.trace[0].label, 'calculator');
  invalid = true;
  const bad = await call(`/apps/${app.id}/run`, 'POST', { input: 'bad response' });
  invalid = false;
  assert.equal(bad.status, 502);
  invalid = 'null';
  const nullResponse = await call(`/apps/${app.id}/run`, 'POST', { input: 'null response' });
  invalid = false;
  assert.equal(nullResponse.status, 502);
});
test('calculator rejects code, division by zero and malformed syntax; chunks preserve Unicode', () => {
  assert.equal(calculate('-(2 + 3) * 4 / 2'), -10);
  assert.equal(calculate('0.5 + .25'), 0.75);
  for (const input of ['process.exit()', '1/0', '(1+2', '2 3', '1 +', 'Infinity']) assert.throws(() => calculate(input));
  const source = '🦞Kiến thức '.repeat(300),
    chunks = chunkText(source);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every((c) => !/[\uD800-\uDBFF]$/.test(c)));
});
test('SQLite and encrypted credentials survive reopening the store', () => {
  const second = new Store(dir);
  assert.equal(second.model(workspace, modelId).api_key, 'test-secret-do-not-return');
  assert.ok(second.db.prepare('SELECT id FROM conversations').all().length);
  second.close();
  const bytes = readFileSync(join(dir, 'studio.sqlite'));
  assert.ok(!bytes.includes(Buffer.from('temporary-test-password')));
});
test('published API keys are app-scoped, force published config and can be revoked', async () => {
  const app = (await call('/apps', 'POST', { name: 'API QA', kind: 'workflow' })).body as StudioApp;
  app.config.graph = {
    nodes: [node('s', 'start'), node('out', 'output', { template: 'Published {{input}}' })],
    edges: [{ id: 'e', source: 's', target: 'out' }],
  };
  await call(`/apps/${app.id}`, 'PUT', app);
  await call(`/apps/${app.id}/publish`, 'POST', {});
  const key = await call(`/apps/${app.id}/keys`, 'POST', {});
  assert.equal(key.status, 201);
  assert.ok(key.body.token.startsWith('xt_'));
  const keyList = await call(`/apps/${app.id}/keys`);
  assert.ok(!JSON.stringify(keyList.body).includes(key.body.token));
  app.config.graph.nodes[1].data.template = 'Draft only';
  await call(`/apps/${app.id}`, 'PUT', app);
  const response = await fetch(origin + `/v1/apps/${app.id}/run`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${key.body.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: 'hello', mode: 'draft' }),
  });
  assert.equal(response.status, 200);
  assert.equal((await response.json()).output, 'Published hello');
  const otherApp = await fetch(origin + '/v1/apps/another-app/run', {
    method: 'POST',
    headers: { Authorization: `Bearer ${key.body.token}`, 'Content-Type': 'application/json' },
    body: '{"input":"hello"}',
  });
  assert.equal(otherApp.status, 401);
  await call(`/apps/${app.id}/keys/${key.body.id}`, 'DELETE');
  assert.equal((await fetch(origin + `/v1/apps/${app.id}`, { headers: { Authorization: `Bearer ${key.body.token}` } })).status, 401);
});
test('concurrent server instances detect stale conversation writes instead of losing messages', { timeout: 5000 }, async () => {
  const app = (await call('/apps', 'POST', { name: 'Concurrency', kind: 'chat' })).body as StudioApp;
  app.config.model_id = modelId;
  await call(`/apps/${app.id}`, 'PUT', app);
  const first = await call(`/apps/${app.id}/run`, 'POST', { input: 'start' });
  const otherStore = new Store(dir),
    otherServer = createStudio(otherStore);
  await new Promise<void>((resolve) => otherServer.listen(0, '127.0.0.1', resolve));
  concurrentBarrier = new Promise<void>((resolve) => {
    releaseConcurrent = resolve;
  });
  arrivals = 0;
  try {
    const payload = { input: 'concurrent', conversation_id: first.body.conversation_id };
    const requests = await Promise.all([
      call(`/apps/${app.id}/run`, 'POST', payload),
      fetch(`http://127.0.0.1:${(otherServer.address() as AddressInfo).port}/api/apps/${app.id}/run`, {
        method: 'POST',
        headers: { Cookie: cookie, 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      }).then(async (r) => ({ status: r.status, body: await r.json() })),
    ]);
    assert.deepEqual(requests.map((r) => r.status).sort(), [200, 409]);
    const messages = (await call(`/apps/${app.id}/conversations/${first.body.conversation_id}`)).body.messages;
    assert.equal(messages.length, 4);
  } finally {
    releaseConcurrent();
    concurrentBarrier = null;
    await new Promise<void>((r) => otherServer.close(() => r()));
    otherStore.close();
  }
});
test('agent enforces maximum steps and records its tool trace on failure', async () => {
  const app = (await call('/apps', 'POST', { name: 'Bounded agent', kind: 'agent' })).body as StudioApp;
  app.config.model_id = modelId;
  app.config.max_steps = 2;
  await call(`/apps/${app.id}`, 'PUT', app);
  repeatTool = true;
  try {
    const response = await call(`/apps/${app.id}/run`, 'POST', { input: 'repeat' });
    assert.equal(response.status, 422);
    assert.equal(response.body.trace.length, 2);
  } finally {
    repeatTool = false;
  }
});
test('resource deletion follows typed references rather than UUIDs mentioned in prompts', async () => {
  const spare = await call('/models', 'POST', { name: 'Spare model', provider: 'openai', base_url: mockUrl, model: 'spare', kind: 'chat' });
  const knowledge = await call('/knowledge', 'POST', { name: 'Spare knowledge' });
  const app = (await call('/apps', 'POST', { name: 'References', kind: 'chat' })).body as StudioApp;
  app.config.model_id = modelId;
  app.config.system_prompt = `Some text with ${spare.body.id} and ${knowledge.body.id}`;
  await call(`/apps/${app.id}`, 'PUT', app);
  assert.equal((await call(`/models/${spare.body.id}`, 'DELETE')).status, 200);
  assert.equal((await call(`/knowledge/${knowledge.body.id}`, 'DELETE')).status, 200);
});
test('opening a restored database without its encryption key fails without creating a replacement', () => {
  const restored = mkdtempSync(join(tmpdir(), 'xt-studio-missing-key-'));
  try {
    copyFileSync(join(dir, 'studio.sqlite'), join(restored, 'studio.sqlite'));
    assert.throws(() => new Store(restored), /Thiếu credential.key/);
    assert.equal(existsSync(join(restored, 'credential.key')), false);
  } finally {
    rmSync(restored, { recursive: true, force: true });
  }
});
