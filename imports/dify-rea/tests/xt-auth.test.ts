import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';

test('integrated assertions bind tenant, person, path, body, role and nonce', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-sso-'));
  const store = new Store(dir);
  const secret = randomBytes(32);
  const server = createStudio(store, { integrated: true, secret });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const tenantA = randomUUID(), tenantB = randomUUID(), personA = randomUUID(), personB = randomUUID();
  function signed(path: string, method: string, tenant: string, person: string, roles: string[], payload = '', overrides: Record<string, unknown> = {}) {
    const claim = { v: 1, tenant, person, name: 'Alice', workspace_name: 'Acme', roles, method, path,
      body_sha256: createHash('sha256').update(payload).digest('hex'), iat: Math.floor(Date.now() / 1000), nonce: randomUUID(), ...overrides };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    return { 'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex') };
  }
  async function call(path: string, method: string, headers: Record<string, string>, payload?: string) {
    const response = await fetch(origin + path, { method, headers: { ...headers, ...(payload ? { 'content-type': 'application/json' } : {}) }, body: payload });
    return { status: response.status, value: await response.json() as any };
  }
  try {
    assert.equal((await call('/api/session', 'GET', {})).status, 401);
    assert.equal((await call('/api/login', 'POST', signed('/api/login', 'POST', tenantA, personA, ['studio_admin'], '{}'), '{}')).status, 404);
    assert.equal((await call('/api/session', 'GET', signed('/api/session', 'GET', tenantA, personA, []))).status, 403);
    const wrongPath = signed('/api/apps', 'GET', tenantA, personA, ['studio_admin']);
    assert.equal((await call('/api/session', 'GET', wrongPath)).status, 401);
    const stale = signed('/api/session', 'GET', tenantA, personA, ['studio_admin'], '', { iat: 1 });
    assert.equal((await call('/api/session', 'GET', stale)).status, 401);
    const replay = signed('/api/session', 'GET', tenantA, personA, ['studio_admin']);
    assert.equal((await call('/api/session', 'GET', replay)).status, 200);
    assert.equal((await call('/api/session', 'GET', replay)).status, 401);
    const parallel = signed('/api/session', 'GET', tenantA, personA, ['studio_admin']);
    assert.deepEqual((await Promise.all(Array.from({ length: 8 }, () => call('/api/session', 'GET', parallel)))).map((r) => r.status).sort(),
      [200, 401, 401, 401, 401, 401, 401, 401]);
    const future = signed('/api/session', 'GET', tenantA, personA, ['studio_admin'], '', { iat: Math.floor(Date.now() / 1000) + 29 });
    assert.equal((await call('/api/session', 'GET', future)).status, 200);
    const realNow = Date.now;
    try {
      Date.now = () => realNow() + 32000;
      assert.equal((await call('/api/session', 'GET', future)).status, 401);
    } finally { Date.now = realNow; }
    const tampered = signed('/api/session', 'GET', tenantA, personA, ['studio_admin']);
    tampered['x-xt-signature'] = '0'.repeat(64);
    assert.equal((await call('/api/session', 'GET', tampered)).status, 401);
    const badBody = signed('/api/apps', 'POST', tenantA, personA, ['studio_admin'], '{}');
    assert.equal((await call('/api/apps', 'POST', badBody, '{"name":"bad"}')).status, 401);
    const createBody = JSON.stringify({ name: 'Secret app', kind: 'chat' });
    const created = await call('/api/apps', 'POST', signed('/api/apps', 'POST', tenantA, personA, ['studio_admin'], createBody), createBody);
    assert.equal(created.status, 201);
    const id = created.value.id;
    const mapped = store.db.prepare('SELECT workspace_id FROM xt_workspace_map WHERE tenant_id=?').get(tenantA)!;
    store.db.prepare('UPDATE apps SET published=config,version=1 WHERE id=?').run(id);
    const conversation = randomUUID(), otherConversation = randomUUID();
    const insertConversation = store.db.prepare('INSERT INTO conversations(id,app_id,workspace_id,title,messages,updated_at,mode,audience) VALUES(?,?,?,?,?,?,?,?)');
    insertConversation.run(conversation, id, mapped.workspace_id, 'person A', '[]', new Date().toISOString(), 'published', `xt:${personA}`);
    insertConversation.run(otherConversation, id, mapped.workspace_id, 'person B', '[]', new Date().toISOString(), 'published', `xt:${personB}`);
    const insertRun = store.db.prepare('INSERT INTO runs(id,app_id,workspace_id,mode,input,status,created_at,config_hash,app_version,person_id) VALUES(?,?,?,?,?,?,?,?,?,?)');
    insertRun.run(randomUUID(), id, mapped.workspace_id, 'published', 'A', 'success', new Date().toISOString(), 'hash', 1, personA);
    insertRun.run(randomUUID(), id, mapped.workspace_id, 'published', 'B', 'success', new Date().toISOString(), 'hash', 1, personB);
    assert.equal((await call(`/api/apps/${id}/conversations?mode=published`, 'GET', signed(`/api/apps/${id}/conversations?mode=published`, 'GET', tenantA, personA, ['studio_user']))).value.length, 1);
    assert.equal((await call(`/api/apps/${id}/conversations/${otherConversation}`, 'GET', signed(`/api/apps/${id}/conversations/${otherConversation}`, 'GET', tenantA, personA, ['studio_user']))).status, 404);
    assert.equal((await call('/api/runs', 'GET', signed('/api/runs', 'GET', tenantA, personA, ['studio_user']))).value.length, 1);
    assert.equal((await call('/api/runs', 'GET', signed('/api/runs', 'GET', tenantA, personB, ['studio_user']))).value.length, 1);
    assert.equal((await call(`/api/apps/${id}/conversations?mode=published`, 'GET', signed(`/api/apps/${id}/conversations?mode=published`, 'GET', tenantA, personB, ['studio_user']))).value[0].id, otherConversation);
    assert.equal((await call(`/api/apps/${id}`, 'GET', signed(`/api/apps/${id}`, 'GET', tenantB, personA, ['studio_admin']))).status, 404);
    assert.equal((await call('/api/apps', 'GET', signed('/api/apps', 'GET', tenantA, personB, ['studio_user']))).value.length, 1);
    assert.equal((await call('/api/models', 'GET', signed('/api/models', 'GET', tenantA, personB, ['studio_editor']))).status, 200);
    assert.equal((await call('/api/models', 'POST', signed('/api/models', 'POST', tenantA, personB, ['studio_editor'], '{}'), '{}')).status, 403);
    assert.equal((await call('/api/apps', 'POST', signed('/api/apps', 'POST', tenantA, personB, ['studio_user'], createBody), createBody)).status, 403);
    assert.equal((await call('/api/apps', 'GET', signed('/api/apps', 'GET', tenantA, personA, ['studio_admin']))).value.length, 1);
    assert.equal((await call('/v1/apps/x', 'GET', {})).status, 404);
    const cookieOnly = await fetch(origin + '/api/session', { headers: { Cookie: 'studio_session=anything' } });
    assert.equal(cookieOnly.status, 401);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('signed request_id is validated and written to the request log', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-rid-'));
  const store = new Store(dir);
  const secret = randomBytes(32);
  const server = createStudio(store, { integrated: true, secret, log: true });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const lines: string[] = [];
  const realLog = console.log;
  console.log = (line: string) => { lines.push(line); };
  const call = (requestId: unknown) => {
    const claim = { v: 1, tenant: randomUUID(), person: randomUUID(), name: 'Alice', workspace_name: 'Acme', roles: ['studio_user'],
      method: 'GET', path: '/api/session', body_sha256: createHash('sha256').update('').digest('hex'),
      iat: Math.floor(Date.now() / 1000), nonce: randomUUID(), request_id: requestId };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    return fetch(origin + '/api/session', { headers: { 'x-xt-assertion': encoded,
      'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex') } });
  };
  try {
    const rid = randomBytes(16).toString('hex');
    assert.equal((await call(rid)).status, 200);
    assert.ok(lines.some((line) => line.startsWith('GET /api/session 200 ') && line.endsWith(` request_id=${rid}`)));
    assert.equal((await call('bad id\nforged log line')).status, 401);
    assert.equal((await call(42)).status, 401);
    assert.ok(!lines.some((line) => line.includes('forged')));
  } finally {
    console.log = realLog;
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});

test('tenant mapping survives a Studio restart without claiming legacy workspace', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-map-'));
  const tenant = randomUUID();
  let store = new Store(dir);
  const legacy = store.id();
  store.db.prepare('INSERT INTO workspaces(id,name) VALUES(?,?)').run(legacy, 'legacy');
  const mapped = store.transaction(() => {
    const id = store.id();
    store.db.prepare('INSERT INTO workspaces(id,name) VALUES(?,?)').run(id, 'new tenant');
    store.db.prepare('INSERT INTO xt_workspace_map(tenant_id,workspace_id) VALUES(?,?)').run(tenant, id);
    return id;
  });
  store.close();
  store = new Store(dir);
  assert.equal(store.db.prepare('SELECT workspace_id FROM xt_workspace_map WHERE tenant_id=?').get(tenant)?.workspace_id, mapped);
  assert.notEqual(mapped, legacy);
  store.close();
  rmSync(dir, { recursive: true, force: true });
});
