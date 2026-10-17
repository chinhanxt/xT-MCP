import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { setTimeout as pause } from 'node:timers/promises';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import { defaultGeneration, emptyConfig } from '../src/contracts.ts';
import type { GraphNode, StudioApp } from '../src/contracts.ts';
import { generateStream, embed } from '../src/models.ts';
import { readLines, sseFrames } from '../src/streams.ts';
import { runWorkflow } from '../src/workflow.ts';
import { runInputs, fieldDefinitions } from '../src/input-fields.ts';
const dir = mkdtempSync(join(tmpdir(), 'xt-extended-')),
  store = new Store(dir),
  server = createStudio(store),
  requests: any[] = [];
let origin = '',
  provider = '',
  cookie = '',
  workspace = '',
  chatId = '';
const mock = createServer(async (req, res) => {
  if (req.url?.startsWith('/hook')) {
    res.end('hook:' + new URL(req.url, 'http://local').searchParams.get('q'));
    return;
  }
  if (req.url === '/redirect') {
    res.writeHead(302, { Location: '/hook' });
    res.end();
    return;
  }
  const parts: Buffer[] = [];
  for await (const p of req) parts.push(p);
  const value = JSON.parse(Buffer.concat(parts).toString());
  requests.push({ url: req.url, headers: req.headers, body: value });
  if (req.url === '/embeddings') {
    res.end(
      JSON.stringify({
        data: value.input.map((_: string, index: number) => ({ index, embedding: Array(1024).fill(0.12345678901234568) })),
      }),
    );
    return;
  }
  const native = req.url === '/api/chat',
    anthropic = req.url === '/messages';
  if (value.stream) {
    res.writeHead(200, { 'Content-Type': native ? 'application/x-ndjson' : 'text/event-stream' });
    if (native) {
      res.write(JSON.stringify({ message: { content: 'Tiếng Việt 🌱' }, done: false }) + '\n');
      res.end(JSON.stringify({ done: true }) + '\n');
      return;
    }
    if (anthropic) {
      res.write('event: ping\ndata: {"type":"ping"}\n\n');
      res.write(
        'event: content_block_delta\ndata: ' +
          JSON.stringify({ type: 'content_block_delta', delta: { type: 'text_delta', text: 'Tiếng Việt 🌱' } }) +
          '\n\n',
      );
      res.end('event: message_stop\ndata: {"type":"message_stop"}\n\n');
      return;
    }
    res.write('data: ' + JSON.stringify({ choices: [{ delta: { content: 'Tiếng Việt 🌱' } }] }) + '\n\n');
    if (value.model === 'early') {
      res.end();
      return;
    }
    if (value.model === 'error') {
      res.end('data: {"error":{"message":"private provider details"}}\n\n');
      return;
    }
    if (value.model === 'slow') {
      req.on('close', () => res.end());
      return;
    }
    res.end('data: [DONE]\n\n');
    return;
  }
  if (anthropic) {
    const last = value.messages.at(-1);
    if (value.tools && !last.content.some((x: any) => x.type === 'tool_result'))
      res.end(JSON.stringify({ content: [{ type: 'tool_use', id: 'anthropic-tool', name: 'calculator', input: { expression: '2+3' } }] }));
    else
      res.end(
        JSON.stringify({
          content: [{ type: 'text', text: last.content.some((x: any) => x.type === 'tool_result') ? '5' : 'Claude fixture' }],
        }),
      );
    return;
  }
  const content = value.response_format ? ' {"ok":true} ' : 'Echo: ' + value.messages.at(-1).content;
  res.end(JSON.stringify(native ? { message: { content } } : { choices: [{ message: { content } }] }));
});
before(async () => {
  await new Promise<void>((r) => mock.listen(0, '127.0.0.1', r));
  provider = `http://127.0.0.1:${(mock.address() as AddressInfo).port}`;
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const setup = await call('/api/setup', 'POST', {
    email: 'extended@example.test',
    password: 'test-only-extended-password',
    workspace_name: 'Extended',
  });
  cookie = setup.cookie!;
  workspace = (await call('/api/session')).value.user.workspace_id;
  chatId = (await model('openai', 'normal')).id;
});
after(async () => {
  await new Promise<void>((r) => server.close(() => r()));
  await new Promise<void>((r) => mock.close(() => r()));
  store.close();
  rmSync(dir, { recursive: true, force: true });
});
async function call(path: string, method = 'GET', data?: unknown, auth = cookie) {
  const r = await fetch(origin + path, {
    method,
    headers: { Cookie: auth, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  return { status: r.status, value: (await r.json()) as any, cookie: r.headers.get('set-cookie')?.split(';')[0] };
}
async function model(kind: 'openai' | 'ollama' | 'anthropic', name: string, type = 'chat') {
  const r = await call('/api/models', 'POST', {
    name,
    provider: kind,
    base_url: provider,
    model: name,
    kind: type,
    api_key: 'fixture-key',
  });
  assert.equal(r.status, 200);
  return r.value as { id: string };
}
async function app(kind = 'chat', name = 'Extended app') {
  const r = await call('/api/apps', 'POST', { name, kind });
  const a = r.value as StudioApp;
  a.config.model_id = chatId;
  await save(a);
  return a;
}
async function save(a: StudioApp) {
  const r = await call(`/api/apps/${a.id}`, 'PUT', a);
  assert.equal(r.status, 200, JSON.stringify(r.value));
  return r.value as StudioApp;
}
async function stream(a: StudioApp, modelId = chatId) {
  a.config.model_id = modelId;
  await save(a);
  const r = await fetch(origin + `/api/apps/${a.id}/run`, {
    method: 'POST',
    headers: { Cookie: cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: 'hello', stream: true }),
  });
  assert.equal(r.status, 200);
  const events = [];
  for await (const line of readLines(r.body!)) if (line) events.push(JSON.parse(line));
  return events;
}
test('UTF-8 and SSE frames remain correct across byte boundaries and enforce caps', async () => {
  const raw = new TextEncoder().encode('event: token\r\ndata: {"text":"Việt 🌱"}\r\n\r\n'),
    body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const byte of raw) c.enqueue(Uint8Array.of(byte));
        c.close();
      },
    });
  const frames = [];
  for await (const frame of sseFrames(body)) frames.push(frame);
  assert.deepEqual(frames, [{ event: 'token', data: '{"text":"Việt 🌱"}' }]);
  const large = new ReadableStream<Uint8Array>({
    start(c) {
      c.enqueue(new Uint8Array(20));
      c.close();
    },
  });
  await assert.rejects(async () => {
    for await (const _ of readLines(large, 10)) {
    }
  }, /byte limit/);
});
test('OpenAI/Ollama/Anthropic streaming returns deltas followed by a persisted result', async () => {
  for (const kind of ['openai', 'ollama', 'anthropic'] as const) {
    const m = kind === 'openai' ? { id: chatId } : await model(kind, kind);
    const a = await app();
    const events = await stream(a, m.id);
    assert.equal(events[0].type, 'delta');
    assert.equal(events[0].text, 'Tiếng Việt 🌱');
    assert.equal(events.at(-1).type, 'result');
    const id = events.at(-1).result.conversation_id;
    assert.equal((await call(`/api/apps/${a.id}/conversations/${id}`)).value.messages.at(-1).content, 'Tiếng Việt 🌱');
  }
  const native = requests.find((r) => r.url === '/api/chat' && r.body.stream);
  assert.equal(native.body.options.num_predict, 1024);
  const anthropic = requests.find((r) => r.url === '/messages' && r.body.stream);
  assert.equal(anthropic.headers['x-api-key'], 'fixture-key');
  assert.equal(anthropic.headers['anthropic-version'], '2023-06-01');
  assert.ok(!anthropic.body.messages.some((m: any) => m.role === 'system'));
});
test('early EOF and provider errors after deltas never persist a partial conversation', async () => {
  for (const name of ['early', 'error']) {
    const m = await model('openai', name),
      a = await app(),
      events = await stream(a, m.id);
    assert.equal(events[0].type, 'delta');
    assert.equal(events.at(-1).type, 'error');
    assert.ok(!JSON.stringify(events).includes('private provider details'));
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM conversations WHERE app_id=?').get(a.id)!.n, 0);
    assert.equal((await call(`/api/runs?app_id=${a.id}`)).value[0].status, 'error');
  }
});
test('client cancellation aborts an active provider stream without storing partial output', async () => {
  const m = await model('openai', 'slow'),
    a = await app();
  a.config.model_id = m.id;
  await save(a);
  const c = new AbortController(),
    r = await fetch(origin + `/api/apps/${a.id}/run`, {
      method: 'POST',
      headers: { Cookie: cookie, 'Content-Type': 'application/json' },
      body: JSON.stringify({ input: 'cancel me', stream: true }),
      signal: c.signal,
    });
  const reader = r.body!.getReader();
  await reader.read();
  c.abort();
  await reader.cancel().catch(() => {});
  for (let i = 0; i < 100; i++) {
    if (store.db.prepare('SELECT status FROM runs WHERE app_id=?').get(a.id)?.status === 'error') break;
    await pause(10);
  }
  assert.equal(store.db.prepare('SELECT status FROM runs WHERE app_id=?').get(a.id)!.status, 'error');
  assert.equal(store.db.prepare('SELECT count(*) AS n FROM conversations WHERE app_id=?').get(a.id)!.n, 0);
});
test('Anthropic agent adapts system, tool_use and tool_result into the common tool loop', async () => {
  const m = await model('anthropic', 'claude-tools'),
    a = await app('agent');
  a.config.model_id = m.id;
  await save(a);
  const result = await call(`/api/apps/${a.id}/run`, 'POST', { input: 'calculate 2+3' });
  assert.equal(result.status, 200);
  assert.equal(result.value.output, '5');
  assert.equal(result.value.trace[0].label, 'calculator');
  assert.ok(
    requests.some((r) => r.url === '/messages' && r.body.messages.some((m: any) => m.content.some((c: any) => c.type === 'tool_result'))),
  );
  assert.equal(
    (await call('/api/models', 'POST', { name: 'bad', provider: 'anthropic', base_url: provider, model: 'bad', kind: 'embedding' })).status,
    400,
  );
});
test('large embedding JSON is bounded by response size rather than streaming frame size', async () => {
  const m = await model('openai', 'large-embed', 'embedding');
  const vectors = await embed(store, workspace, m.id, Array(16).fill('text'));
  assert.equal(vectors.length, 16);
  assert.equal(vectors[0].length, 1024);
});
test('shared visitors and API keys cannot continue another audience conversation; revocation applies immediately', async () => {
  const a = await app();
  await call(`/api/apps/${a.id}/publish`, 'POST', {});
  const link = (await call(`/api/apps/${a.id}/share`, 'POST', {})).value;
  const path = '/public/apps/' + link.id,
    guestA = await call(path, 'GET', undefined, ''),
    guestB = await call(path, 'GET', undefined, '');
  assert.equal(guestA.status, 200);
  assert.ok(!('config' in guestA.value));
  assert.notEqual(guestA.cookie, guestB.cookie);
  const run = await call(path + '/run', 'POST', { input: 'secret guest history' }, guestA.cookie!);
  assert.equal(run.status, 200);
  const id = run.value.conversation_id;
  assert.equal((await call(path + '/run', 'POST', { input: 'steal', conversation_id: id }, guestB.cookie!)).status, 404);
  assert.equal((await call(`/api/apps/${a.id}/run`, 'POST', { input: 'studio', mode: 'published', conversation_id: id })).status, 404);
  assert.equal((await call('/api/models', 'GET', undefined, guestA.cookie!)).status, 401);
  assert.equal((await call(path + '/models', 'GET', undefined, guestA.cookie!)).status, 404);
  await call(`/api/apps/${a.id}/share`, 'DELETE');
  assert.equal((await call(path + '/run', 'POST', { input: 'revoked' }, guestA.cookie!)).status, 404);
});
test('publish snapshots are immutable and restoring a draft produces a new monotonic published version', async () => {
  const a = await app('workflow');
  a.config.graph.nodes = a.config.graph.nodes.filter((n) => n.id !== 'llm');
  a.config.graph.edges = [{ id: 'e', source: 'start', target: 'output' }];
  a.config.graph.nodes[1].data.template = 'one';
  await save(a);
  await call(`/api/apps/${a.id}/publish`, 'POST', {});
  a.config.graph.nodes[1].data.template = 'two';
  await save(a);
  await call(`/api/apps/${a.id}/publish`, 'POST', {});
  const restored = await call(`/api/apps/${a.id}/versions/1/restore`, 'POST', {});
  assert.equal(restored.value.config.graph.nodes[1].data.template, 'one');
  assert.equal(restored.value.published.graph.nodes[1].data.template, 'two');
  assert.equal((await call(`/api/apps/${a.id}/publish`, 'POST', {})).value.version, 3);
  assert.equal(
    JSON.parse(String(store.db.prepare('SELECT config FROM app_versions WHERE app_id=? AND version=1').get(a.id)!.config)).graph.nodes[1]
      .data.template,
    'one',
  );
});
test('portable export omits model secrets; import remaps resources and creates a fresh draft', async () => {
  const a = await app(),
    exported = await call(`/api/apps/${a.id}/export`);
  assert.ok(!JSON.stringify(exported.value).includes('fixture-key'));
  exported.value.app.config.model_id = 'foreign-model';
  const imported = await call('/api/apps/import', 'POST', { manifest: exported.value, bindings: { models: { 'foreign-model': chatId } } });
  assert.equal(imported.status, 201);
  assert.notEqual(imported.value.id, a.id);
  assert.equal(imported.value.version, 0);
  assert.equal(imported.value.published, null);
  assert.equal(imported.value.config.model_id, chatId);
  assert.equal((await call('/api/apps/import', 'POST', { manifest: exported.value })).status, 400);
});
const node = (id: string, type: GraphNode['type'], data: Partial<GraphNode['data']> = {}): GraphNode => ({
  id,
  type,
  position: { x: 0, y: 0 },
  data: { label: id, ...data },
});
test('typed fields, branch aggregation and published input schemas round-trip through API', async () => {
  const a = await app('workflow');
  a.config.inputs = [
    { name: 'amount', label: 'Số tiền', type: 'number', required: true },
    { name: 'name', label: 'Tên', type: 'text', required: true },
  ];
  a.config.graph = {
    nodes: [
      node('start', 'start'),
      node('gate', 'condition', { variable: '{{amount}}', operator: 'equals', value: '100' }),
      node('yes', 'template', { template: 'YES {{name}}' }),
      node('no', 'template', { template: 'NO {{name}}' }),
      node('joined', 'aggregate', { candidates: ['yes', 'no'] }),
      node('output', 'output', { template: '{{joined}}' }),
    ],
    edges: [
      { id: '1', source: 'start', target: 'gate' },
      { id: '2', source: 'gate', target: 'yes', sourceHandle: 'true' },
      { id: '3', source: 'gate', target: 'no', sourceHandle: 'false' },
      { id: '4', source: 'yes', target: 'joined' },
      { id: '5', source: 'no', target: 'joined' },
      { id: '6', source: 'joined', target: 'output' },
    ],
  };
  await save(a);
  assert.equal((await call(`/api/apps/${a.id}/run`, 'POST', { inputs: { amount: 100, name: 'Lan' } })).value.output, 'YES Lan');
  assert.equal((await call(`/api/apps/${a.id}/run`, 'POST', { inputs: { amount: 99, name: 'Lan' } })).value.output, 'NO Lan');
  assert.equal((await call(`/api/apps/${a.id}/run`, 'POST', { inputs: { amount: '100', name: 'Lan' } })).status, 400);
  assert.equal((await call(`/api/apps/${a.id}/run`, 'POST', { inputs: { amount: 100 } })).status, 400);
  assert.throws(() => runInputs(a.config.inputs!, { amount: 100, name: 'Lan', unknown: true }), /khai báo/);
  assert.throws(() => fieldDefinitions([{ name: 'input', label: 'x', type: 'text', required: true }]), /không hợp lệ/);
  await call(`/api/apps/${a.id}/publish`, 'POST', {});
  assert.equal((await call(`/api/apps/${a.id}`)).value.published.inputs.length, 2);
});
test('HTTP workflow uses a fixed configured origin, and aggregation rejects ambiguous parallel values', async () => {
  const config = emptyConfig();
  config.graph = {
    nodes: [
      node('start', 'start'),
      node('http', 'http', { url: provider + '/hook?q={{input}}' }),
      node('output', 'output', { template: '{{http}}' }),
    ],
    edges: [
      { id: '1', source: 'start', target: 'http' },
      { id: '2', source: 'http', target: 'output' },
    ],
  };
  assert.equal((await runWorkflow(store, workspace, config, 'Lan')).output, 'hook:Lan');
  config.graph.nodes[1].data.url = provider + '/redirect';
  await assert.rejects(() => runWorkflow(store, workspace, config, 'Lan'), /HTTP node/);
  config.graph.nodes[1].data.url = 'http://{{input}}/path';
  await assert.rejects(() => runWorkflow(store, workspace, config, 'evil.example'), /origin HTTP/);
  config.graph = {
    nodes: [
      node('start', 'start'),
      node('a', 'template', { template: 'A' }),
      node('b', 'template', { template: 'B' }),
      node('agg', 'aggregate', { candidates: ['a', 'b'] }),
      node('out', 'output', { template: '{{agg}}' }),
    ],
    edges: [
      { id: '1', source: 'start', target: 'a' },
      { id: '2', source: 'start', target: 'b' },
      { id: '3', source: 'a', target: 'agg' },
      { id: '4', source: 'b', target: 'agg' },
      { id: '5', source: 'agg', target: 'out' },
    ],
  };
  await assert.rejects(() => runWorkflow(store, workspace, config, 'x'), /đúng một biến/);
});
test('pre-migration SQLite data and credentials survive two idempotent upgrades', () => {
  const oldDir = mkdtempSync(join(tmpdir(), 'xt-legacy-'));
  try {
    const old = new Store(oldDir),
      config: any = emptyConfig();
    delete config.inputs;
    delete config.generation;
    old.db.exec('ALTER TABLE conversations DROP COLUMN audience; DROP TABLE app_versions; PRAGMA user_version=0;');
    old.db
      .prepare('INSERT INTO apps(id,workspace_id,name,description,kind,config,published,version,updated_at) VALUES(?,?,?,?,?,?,?,?,?)')
      .run('legacy', 'workspace', 'Legacy', '', 'chat', JSON.stringify(config), JSON.stringify(config), 2, new Date().toISOString());
    const key = readFileSync(join(oldDir, 'credential.key'));
    old.close();
    for (let i = 0; i < 2; i++) {
      const migrated = new Store(oldDir);
      assert.ok(
        migrated.db
          .prepare('PRAGMA table_info(conversations)')
          .all()
          .some((c) => c.name === 'audience'),
      );
      assert.equal(migrated.app('workspace', 'legacy').config.generation!.max_tokens, 1024);
      assert.equal(migrated.db.prepare('SELECT count(*) AS n FROM app_versions WHERE app_id=?').get('legacy')!.n, 1);
      assert.deepEqual(readFileSync(join(oldDir, 'credential.key')), key);
      migrated.close();
    }
  } finally {
    rmSync(oldDir, { recursive: true, force: true });
  }
});
test('shared JSON errors omit internal workflow prompts and outputs', async () => {
  const a = await app('workflow');
  a.config.graph.nodes[1].data.model_id = (await model('openai', 'normal')).id;
  a.config.graph.nodes[1].data.prompt = 'private-internal-prompt';
  a.config.generation = { ...defaultGeneration(), json_mode: true };
  await save(a);
  await call(`/api/apps/${a.id}/publish`, 'POST', {});
  // A runtime-only malformed variable forces failure after an earlier template step.
  store.db.prepare('UPDATE apps SET published=? WHERE id=?').run(
    JSON.stringify({
      ...a.config,
      graph: {
        nodes: [
          node('start', 'start'),
          node('secret', 'template', { template: 'private-internal-prompt' }),
          node('http', 'http', { url: provider + '/redirect' }),
          node('out', 'output', { template: '{{http}}' }),
        ],
        edges: [
          { id: '1', source: 'start', target: 'secret' },
          { id: '2', source: 'secret', target: 'http' },
          { id: '3', source: 'http', target: 'out' },
        ],
      },
    }),
    a.id,
  );
  const link = (await call(`/api/apps/${a.id}/share`, 'POST', {})).value,
    path = '/public/apps/' + link.id,
    guest = await call(path, 'GET', undefined, '');
  const result = await call(path + '/run', 'POST', { input: 'x' }, guest.cookie!);
  assert.equal(result.status, 502);
  assert.deepEqual(result.value.trace, []);
  assert.ok(!JSON.stringify(result.value).includes('private-internal-prompt'));
});
test('editing an earlier turn truncates future history only after a successful replacement', async () => {
  const a = await app(),
    first = await call(`/api/apps/${a.id}/run`, 'POST', { input: 'first' }),
    id = first.value.conversation_id;
  await call(`/api/apps/${a.id}/run`, 'POST', { input: 'second', conversation_id: id });
  const rerun = await call(`/api/apps/${a.id}/run`, 'POST', { input: 'replacement', conversation_id: id, retry_from: 0 });
  assert.equal(rerun.status, 200);
  let saved = (await call(`/api/apps/${a.id}/conversations/${id}`)).value.messages;
  assert.equal(saved.length, 2);
  assert.equal(saved[0].content, 'replacement');
  a.config.generation = { ...defaultGeneration(), json_mode: true, temperature: 0.7, max_tokens: 400, context_messages: 2 };
  await save(a);
  const result = await call(`/api/apps/${a.id}/run`, 'POST', { input: 'json', conversation_id: id });
  assert.equal(result.status, 200);
  assert.deepEqual(JSON.parse(result.value.output), { ok: true });
  const last = requests.at(-1).body;
  assert.equal(last.temperature, 0.7);
  assert.equal(last.max_tokens, 400);
  assert.equal(last.response_format.type, 'json_object');
});
test('document extraction endpoint accepts file payloads beyond the old 2 MB request limit', async () => {
  const knowledge = (await call('/api/knowledge', 'POST', { name: 'Large extract' })).value;
  const value = '漢'.repeat(900000) + '\n';
  const file = Buffer.from(value);
  const result = await call(`/api/knowledge/${knowledge.id}/extract`, 'POST', { name: 'large.txt', file_base64: file.toString('base64') });
  assert.equal(result.status, 200);
  assert.equal(result.value.content.length, 900000);
});
