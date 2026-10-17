// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { createHmac, randomBytes } from 'node:crypto';
import { Store } from '../src/store.ts';
import { emptyConfig } from '../src/contracts.ts';
import { configInput } from '../src/validation.ts';
import { runAgent } from '../src/agent.ts';
import { xtTools } from '../src/xt-tools.ts';

test('agent exposes only selected xT tools and preserves confirmation in tool trace', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-agent-tools-'));
  const store = new Store(dir);
  const calls: unknown[] = [];
  const model = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    const payload = JSON.parse(Buffer.concat(chunks).toString());
    assert.deepEqual(payload.tools.map((tool: any) => tool.function.name), ['xt_create_record']);
    const message = payload.messages.at(-1).role === 'user'
      ? { role: 'assistant', content: null, tool_calls: [{ id: 'call', type: 'function', function: {
        name: 'xt_create_record', arguments: JSON.stringify({ model_id: 'stock_request', values: {}, idempotency_key: 'one-operation' }),
      } }] }
      : { role: 'assistant', content: 'Cần người dùng xác nhận.' };
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify({ choices: [{ message }] }));
  });
  await new Promise<void>((resolve) => model.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(model.address() as AddressInfo).port}`;
  try {
    store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run('tenant', 'Tenant');
    store.db.prepare('INSERT INTO models VALUES(?,?,?,?,?,?,?,?)').run('model', 'tenant', 'Fixture', 'openai', origin, 'fixture', 'chat', '');
    const config = { ...emptyConfig(), model_id: 'model', tools: [], xt_tools: ['create_record'] };
    const result = await runAgent(store, 'tenant', config, [{ role: 'user', content: 'Tạo phiếu' }], undefined, {
      async list() { return [{ name: 'create_record', description: 'Create', input_schema: { type: 'object' } }, { name: 'get_record', description: 'Read', input_schema: { type: 'object' } }]; },
      async call(name, args) {
        calls.push({ name, args });
        return { ok: false, error: { code: 'confirmation_required', details: { confirm_url: 'https://xt.test/desk/confirm/op' } } };
      },
    });
    assert.equal(calls.length, 1);
    assert.equal(result.trace[0].status, 'error');
    assert.equal(JSON.parse(result.trace[0].output!).error.code, 'confirmation_required');
    await assert.rejects(runAgent(store, 'tenant', config, []), /chưa sẵn sàng/);
    assert.throws(() => configInput({ ...config, xt_tools: ['decide_request'] }), /không hợp lệ/);
  } finally {
    await new Promise<void>((resolve) => model.close(() => resolve()));
    store.close(); rmSync(dir, { recursive: true, force: true });
  }
});

test('internal callback client signs the fixed endpoint without user credentials', async () => {
  const key = randomBytes(32), capability = randomBytes(32).toString('base64url');
  const server = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    assert.equal(request.url, '/studio-internal/tools');
    assert.equal(request.headers.authorization, undefined);
    assert.equal(request.headers.cookie, undefined);
    assert.equal(request.headers['x-xt-capability'], capability);
    assert.equal(request.headers['x-xt-signature'], createHmac('sha256', key).update(capability + '\n' + body).digest('hex'));
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(JSON.parse(body).action === 'list' ? { tools: [] } : { ok: true }));
  });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const tools = xtTools(`http://127.0.0.1:${(server.address() as AddressInfo).port}`, key, capability);
    assert.deepEqual(await tools.list(), []);
    assert.deepEqual(await tools.call('get_record', { ref: 'stock_item:one' }), { ok: true });
    assert.throws(() => tools.call('decide_request', {}), /không được cấp/);
  } finally { await new Promise<void>((resolve) => server.close(() => resolve())); }
});
