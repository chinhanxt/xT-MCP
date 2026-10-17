// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { ListToolsRequestSchema, CallToolRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import { hashToken } from '../src/http.ts';
import { callMcp, discoverTools } from '../src/mcp.ts';
import type { McpConnector, StudioApp, McpToolSelection } from '../src/contracts.ts';
import { formInputValues } from '../src/web/input-values.ts';
import { runInputs } from '../src/input-fields.ts';
const dir = mkdtempSync(join(tmpdir(), 'xt-mcp-')),
  store = new Store(dir),
  studio = createStudio(store);
let origin = '',
  remote = '',
  cookie = '',
  workspace = '',
  schemaType = 'string';
let connectorA: McpConnector, connectorB: McpConnector, app: StudioApp;
const executions: { path: string; name: string; args: unknown }[] = [];
const advertised: any[][] = [];
let slowStarted: (() => void) | undefined;
const secret = 'fixture-secret-should-never-leak';
const fixture = createServer(async (req, res) => {
  const parts: Buffer[] = [];
  for await (const chunk of req) parts.push(chunk);
  const value = parts.length ? JSON.parse(Buffer.concat(parts).toString()) : undefined;
  if (req.url === '/chat/completions') {
    const tools = value.tools ?? [];
    advertised.push(tools);
    let message: unknown = { role: 'assistant', content: 'Done' };
    if (value.messages.at(-1).role === 'user') {
      const input = value.messages.at(-1).content;
      const calls =
        input === 'unknown'
          ? [{ id: 'bad', type: 'function', function: { name: 'unselected', arguments: '{}' } }]
          : tools.map((tool: any, i: number) => ({
              id: 'tool-' + i,
              type: 'function',
              function: { name: tool.function.name, arguments: JSON.stringify({ who: input === 'invalid' ? 42 : input }) },
            }));
      message = { role: 'assistant', content: null, tool_calls: calls };
    }
    res.setHeader('Content-Type', 'application/json');
    res.end(JSON.stringify({ choices: [{ message }] }));
    return;
  }
  if (req.headers.authorization !== 'Bearer ' + secret) {
    res.writeHead(401).end(secret);
    return;
  }
  if (req.method !== 'POST') {
    res.writeHead(405).end();
    return;
  }
  const server = new Server({ name: 'fixture', version: '1.0.0' }, { capabilities: { tools: {} } });
  server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
      {
        name: 'greet',
        description: 'Greet a person',
        inputSchema: { type: 'object', properties: { who: { type: schemaType } }, required: ['who'], additionalProperties: false },
      },
      { name: 'unselected', description: 'Never selected', inputSchema: { type: 'object', properties: {} } },
    ],
  }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    executions.push({ path: req.url!, name: request.params.name, args: request.params.arguments });
    const who = request.params.arguments?.who;
    if (who === 'slow') {
      slowStarted?.();
      await new Promise((r) => setTimeout(r, 150));
    }
    return {
      content: [{ type: 'text', text: who === 'oversize' ? 'x'.repeat(70000) : req.url + ':' + who }],
      isError: who === 'remote-error',
    };
  });
  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined, enableJsonResponse: req.url === '/b' });
  res.once('close', () => {
    void transport.close();
    void server.close();
  });
  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, value);
  } catch {
    if (!res.headersSent) res.writeHead(500).end();
  }
});
async function request(path: string, value?: unknown, method = value === undefined ? 'GET' : 'POST', token = cookie) {
  const response = await fetch(origin + '/api' + path, {
    method,
    headers: { Cookie: token, 'Content-Type': 'application/json' },
    body: value === undefined ? undefined : JSON.stringify(value),
  });
  if (response.headers.has('set-cookie')) cookie = response.headers.get('set-cookie')!.split(';')[0];
  return { status: response.status, body: (await response.json()) as any };
}
function selection(c: McpConnector): McpToolSelection {
  return { connector_id: c.id, tool_name: 'greet', schema_hash: c.tools.find((t) => t.name === 'greet')!.schema_hash };
}
before(async () => {
  await new Promise<void>((r) => fixture.listen(0, '127.0.0.1', r));
  await new Promise<void>((r) => studio.listen(0, '127.0.0.1', r));
  remote = 'http://127.0.0.1:' + (fixture.address() as AddressInfo).port;
  origin = 'http://127.0.0.1:' + (studio.address() as AddressInfo).port;
  assert.equal(
    (await request('/setup', { workspace_name: 'MCP QA', email: 'mcp@example.test', password: 'Fixture-password-123' })).status,
    201,
  );
  workspace = (await request('/session')).body.user.workspace_id;
});
after(async () => {
  await Promise.all([new Promise<void>((r) => studio.close(() => r())), new Promise<void>((r) => fixture.close(() => r()))]);
  store.close();
  rmSync(dir, { recursive: true, force: true });
});
test('MCP discovery handles JSON and SSE; encrypted tokens never appear in API', async () => {
  for (const path of ['/a', '/b']) {
    const result = await request('/mcp', { name: path, url: remote + path, token: secret });
    assert.equal(result.status, 201, JSON.stringify(result.body));
    assert.equal(result.body.tools.length, 2);
    if (path === '/a') connectorA = result.body;
    else connectorB = result.body;
  }
  const list = await request('/mcp');
  assert.equal(list.body.length, 2);
  assert.ok(!JSON.stringify(list.body).includes(secret));
  assert.ok(!('token' in list.body[0]));
  assert.ok(!('secret' in list.body[0]));
  const saved = store.db.prepare('SELECT secret FROM mcp_connectors WHERE id=?').get(connectorA.id)!;
  assert.notEqual(saved.secret, secret);
  assert.equal(store.decrypt(String(saved.secret)), secret);
  assert.ok(!readFileSync(join(dir, 'studio.sqlite')).includes(secret));
  const failed = await request('/mcp', { name: 'bad', url: remote + '/a', token: 'wrong' });
  assert.equal(failed.status, 502);
  assert.ok(!JSON.stringify(failed.body).includes(secret));
  assert.equal((await request('/mcp', { name: 'bad', url: 'http://user:pass@localhost/mcp' })).status, 400);
});
test('agent exposes only selected tools, namespaces duplicates and calls exact server', async () => {
  const model = await request('/models', { name: 'fixture', provider: 'openai', base_url: remote, model: 'fixture', kind: 'chat' });
  app = (await request('/apps', { name: 'MCP agent', kind: 'agent' })).body;
  app.config.model_id = model.body.id;
  app.config.tools = [];
  app.config.mcp_tools = [selection(connectorA), selection(connectorB)];
  assert.equal((await request('/apps/' + app.id, app, 'PUT')).status, 200);
  const result = await request('/apps/' + app.id + '/run', { input: 'Mai' });
  assert.equal(result.status, 200, JSON.stringify(result.body));
  assert.equal(result.body.trace.length, 2);
  assert.deepEqual(
    executions.map((x) => [x.path, x.name]),
    [
      ['/a', 'greet'],
      ['/b', 'greet'],
    ],
  );
  assert.equal(advertised[0].length, 2);
  assert.notEqual(advertised[0][0].function.name, advertised[0][1].function.name);
  assert.ok(advertised[0].every((t) => /^mcp_[a-f0-9]{32}$/.test(t.function.name)));
  const previous = executions.length;
  const denied = await request('/apps/' + app.id + '/run', { input: 'unknown' });
  assert.equal(denied.body.trace[0].status, 'error');
  assert.equal(executions.length, previous);
  const invalid = await request('/apps/' + app.id + '/run', { input: 'invalid' });
  assert.ok(invalid.body.trace.every((t: any) => t.status === 'error'));
  assert.equal(executions.length, previous);
});
test('MCP argument schema, result limits, error results and cancellation are enforced', async () => {
  const binding = selection(connectorA),
    before = executions.length;
  await assert.rejects(callMcp(store, workspace, binding, { who: 1 }), /schema/);
  assert.equal(executions.length, before);
  await assert.rejects(callMcp(store, workspace, binding, { who: 'oversize' }), /64.000/);
  assert.equal((await callMcp(store, workspace, binding, { who: 'remote-error' })).isError, true);
  const active = new AbortController();
  const started = new Promise<void>((resolve) => {
    slowStarted = resolve;
  });
  const pending = callMcp(store, workspace, binding, { who: 'slow' }, active.signal);
  await started;
  active.abort();
  await assert.rejects(pending, /dừng/);
  assert.equal((await callMcp(store, workspace, binding, { who: 'After cancel' })).isError, false);
  const abort = new AbortController();
  abort.abort();
  await assert.rejects(callMcp(store, workspace, binding, { who: 'Mai' }, abort.signal), /dừng/);
});
test('workspace isolation, schema pinning, import bindings and revocation hold for published agents', async () => {
  const other = store.id(),
    user = store.id();
  store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run(other, 'Other');
  store.db.prepare('INSERT INTO users VALUES(?,?,?,?)').run(user, other, 'other@example.test', 'unused');
  store.db.prepare('INSERT INTO sessions VALUES(?,?,?)').run(hashToken('other-session'), user, Date.now() + 60000);
  assert.equal((await request('/mcp/' + connectorA.id, undefined, 'DELETE', 'studio_session=other-session')).status, 404);
  const manifest = (await request('/apps/' + app.id + '/export')).body;
  assert.equal(manifest.dependencies.connectors.length, 2);
  assert.ok(!JSON.stringify(manifest).includes(remote));
  assert.ok(!JSON.stringify(manifest).includes(secret));
  const imported = await request('/apps/import', {
    manifest,
    bindings: { connectors: { [connectorA.id]: connectorB.id, [connectorB.id]: connectorA.id } },
  });
  assert.equal(imported.status, 201);
  assert.equal(imported.body.config.mcp_tools[0].connector_id, connectorB.id);
  const foreign = await request('/apps/import', { manifest }, 'POST', 'studio_session=other-session');
  assert.equal(foreign.status, 404);
  assert.equal((await request('/apps/' + app.id + '/publish', {})).status, 200);
  assert.equal((await request('/mcp/' + connectorA.id, undefined, 'DELETE')).status, 409);
  assert.equal((await request('/mcp/' + connectorA.id, { enabled: false }, 'PATCH')).status, 200);
  assert.equal((await request('/apps/' + app.id + '/run', { input: 'Mai', mode: 'published' })).status, 409);
  await request('/mcp/' + connectorA.id, { enabled: true }, 'PATCH');
  schemaType = 'number';
  const before = executions.length;
  await assert.rejects(callMcp(store, workspace, selection(connectorA), { who: 'Mai' }), /Schema.*đổi/);
  assert.equal(executions.length, before);
  assert.equal((await request('/mcp/' + connectorA.id + '/refresh', {})).status, 200);
  assert.equal((await request('/apps/' + app.id + '/run', { input: 'Mai', mode: 'published' })).status, 409);
  schemaType = 'string';
});
test('form values preserve unchecked boolean false and prune fields from old mode', () => {
  const fields = [
    { name: 'accepted', label: 'Accepted', type: 'boolean' as const, required: true },
    { name: 'count', label: 'Count', type: 'number' as const, required: true, default: 0 },
  ];
  const values = formInputValues(fields, { old_field: 'stale' });
  assert.deepEqual(values, { accepted: false, count: 0 });
  assert.deepEqual(runInputs(fields, values), values);
  assert.deepEqual(formInputValues([], values), {});
});
