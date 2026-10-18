// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';

test('every auth rejection logs a validated header request id or a fresh safe id', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-rejection-log-'));
  const store = new Store(dir), secret = randomBytes(32);
  const server = createStudio(store, { integrated: true, secret, log: true });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const lines: string[] = [], realLog = console.log;
  console.log = (line: string) => { lines.push(line); };
  const rid = '1234567890abcdef1234567890abcdef';
  function signed(overrides: Record<string, unknown> = {}) {
    const claim = { v: 1, tenant: randomUUID(), person: randomUUID(), name: 'Alice', workspace_name: 'Acme',
      roles: ['studio_user'], method: 'GET', path: '/api/session', iat: Math.floor(Date.now() / 1000), nonce: randomUUID(),
      body_sha256: createHash('sha256').update('').digest('hex'), ...overrides };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    return { 'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex') };
  }
  async function call(headers: Record<string, string>, status: number, expectedId?: string, path = '/api/session') {
    const before = lines.length;
    const response = await fetch(origin + path, { headers });
    await response.arrayBuffer();
    assert.equal(response.status, status);
    assert.equal(lines.length, before + 1);
    const line = lines[before];
    const loggedId = line.match(/ request_id=([0-9a-f]{32})$/)?.[1];
    assert.ok(loggedId, `rejection must carry a safe request id: ${line}`);
    if (expectedId) assert.equal(loggedId, expectedId);
    assert.ok(!line.includes('attacker'));
    return loggedId;
  }
  try {
    await call({ 'x-request-id': rid }, 401, rid);
    await call({ ...signed({ request_id: 'f'.repeat(32) }), 'x-request-id': rid }, 200, 'f'.repeat(32));
    const generated = new Set<string>();
    for (const unsafeId of [undefined, 'attacker log value', 'A'.repeat(32), 'a'.repeat(33), `${rid}, ${rid}`]) {
      const id = await call(unsafeId === undefined ? {} : { 'x-request-id': unsafeId }, 401);
      assert.ok(!generated.has(id));
      generated.add(id);
    }
    await call({ 'x-request-id': rid, 'x-xt-assertion': 'attacker!', 'x-xt-signature': 'bad' }, 401, rid);
    await call({ ...signed({ request_id: 'f'.repeat(32) }), 'x-xt-signature': '0'.repeat(64), 'x-request-id': rid }, 401, rid);
    await call({ ...signed({ request_id: 'attacker\nforged line' }), 'x-request-id': rid }, 401, rid);
    await call({ ...signed({ iat: 1 }), 'x-request-id': rid }, 401, rid);
    await call({ ...signed({ roles: [] }), 'x-request-id': rid }, 403, rid);
    await call({ ...signed({ body_sha256: '0'.repeat(64) }), 'x-request-id': rid }, 401, rid);
    const replay = { ...signed(), 'x-request-id': rid };
    await call(replay, 200, rid);
    await call(replay, 401, rid);
    await call({ ...signed({ path: '/api/models' }), 'x-request-id': rid }, 403, rid, '/api/models');
    await call({ 'x-request-id': rid }, 404, rid, '/v1/apps/disabled');
  } finally {
    console.log = realLog;
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
