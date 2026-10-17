// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Tenant and person scoping of every integrated Studio endpoint family (U03): apps, versions,
// knowledge, documents, conversations and runs.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import { emptyConfig } from '../src/contracts.ts';

async function studio() {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-scope-'));
  const store = new Store(dir);
  const secret = randomBytes(32);
  const server = createStudio(store, { integrated: true, secret });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  async function call(as: { tenant: string; person: string; roles: string[] }, path: string, method = 'GET', value?: unknown) {
    const payload = value === undefined ? '' : JSON.stringify(value);
    const claim = { v: 1, tenant: as.tenant, person: as.person, name: 'Fixture', workspace_name: 'Fixture', roles: as.roles, method, path,
      body_sha256: createHash('sha256').update(payload).digest('hex'), iat: Math.floor(Date.now() / 1000), nonce: randomUUID() };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    const response = await fetch(origin + path, { method, body: payload || undefined, headers: {
      'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex'),
      ...(payload ? { 'content-type': 'application/json' } : {}) } });
    return { status: response.status, value: (await response.json()) as any };
  }
  async function close() {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
  return { store, call, close };
}

// Seed tenant A directly in SQLite so the fixture does not depend on which upload path is allowed.
async function seedTenant(s: Awaited<ReturnType<typeof studio>>, admin: { tenant: string; person: string; roles: string[] }) {
  const app = (await s.call(admin, '/api/apps', 'POST', { name: 'Secret app', kind: 'chat' })).value.id as string;
  const workspace = String(s.store.db.prepare('SELECT workspace_id FROM xt_workspace_map WHERE tenant_id=?').get(admin.tenant)!.workspace_id);
  const now = new Date().toISOString(), config = JSON.stringify(emptyConfig());
  s.store.db.prepare('UPDATE apps SET published=config,version=1 WHERE id=?').run(app);
  s.store.db.prepare('INSERT INTO app_versions VALUES(?,?,?,?,?,?)').run(app, 1, config, 'Secret app v1', 'secret description', now);
  const kb = randomUUID(), doc = randomUUID(), chunk = randomUUID();
  s.store.db.prepare('INSERT INTO knowledge VALUES(?,?,?,?,?)').run(kb, workspace, 'Secret KB', '', null);
  s.store.db.prepare('INSERT INTO documents(id,knowledge_id,name,content,status) VALUES(?,?,?,?,?)').run(doc, kb, 'Payroll', 'secret payroll 500', 'ready');
  s.store.db.prepare('INSERT INTO chunks VALUES(?,?,?,?)').run(chunk, doc, 'secret payroll 500', null);
  s.store.db.prepare('INSERT INTO chunk_search(id,content) VALUES(?,?)').run(chunk, 'secret payroll 500');
  const conversation = randomUUID();
  s.store.db.prepare('INSERT INTO conversations(id,app_id,workspace_id,title,messages,updated_at,mode,audience) VALUES(?,?,?,?,?,?,?,?)')
    .run(conversation, app, workspace, 'secret', JSON.stringify([{ role: 'user', content: 'secret question' }]), now, 'published', `xt:${admin.person}`);
  return { app, workspace, kb, doc, conversation };
}
const insertRun = (store: Store, app: string, workspace: string, person: string, input: string) =>
  store.db.prepare('INSERT INTO runs(id,app_id,workspace_id,mode,input,result,status,created_at,config_hash,app_version,person_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)')
    .run(randomUUID(), app, workspace, 'draft', input, JSON.stringify({ output: input + ' answer', trace: [], citations: [] }), 'success',
      new Date().toISOString(), 'hash', 0, person);

test('runs: editors see colleagues\' run metadata but not their input or result; admins and owners see everything', async () => {
  const s = await studio();
  const tenant = randomUUID();
  const admin = { tenant, person: randomUUID(), roles: ['studio_admin'] };
  const alice = { tenant, person: randomUUID(), roles: ['studio_editor'] };
  const bob = { tenant, person: randomUUID(), roles: ['studio_editor'] };
  try {
    const a = await seedTenant(s, admin);
    insertRun(s.store, a.app, a.workspace, alice.person, 'alice salary question');
    insertRun(s.store, a.app, a.workspace, bob.person, 'bob private question');
    insertRun(s.store, a.app, a.workspace, '', 'legacy run before integration');
    s.store.db.prepare("UPDATE runs SET status='error',error=? WHERE person_id=?").run('tool failed on bob private question', bob.person);
    for (const path of ['/api/runs', `/api/runs?app_id=${a.app}`]) {
      const seen = (await s.call(alice, path)).value as any[];
      assert.equal(seen.length, 3);
      const mine = seen.find((r) => r.person_id === alice.person);
      assert.equal(mine.input, 'alice salary question');
      assert.match(mine.result, /alice salary question answer/);
      const text = JSON.stringify(seen.filter((r) => r.person_id !== alice.person));
      assert.ok(!text.includes('bob private') && !text.includes('legacy run'), text);
      assert.ok(seen.filter((r) => r.person_id !== alice.person).every((r) => r.input === '' && r.result === null && r.status));
      const bobs = seen.find((r) => r.person_id === bob.person);
      assert.equal(bobs.status, 'error');
      assert.ok(bobs.error && !bobs.error.includes('bob'), bobs.error);
      assert.equal(seen.find((r) => r.person_id === '').error, null);
      const all = JSON.stringify((await s.call(admin, path)).value);
      assert.ok(all.includes('bob private question') && all.includes('alice salary question') && all.includes('legacy run'));
      assert.ok(all.includes('tool failed on bob private question'));
    }
    const viewer = { tenant, person: bob.person, roles: ['studio_user'] };
    assert.deepEqual(((await s.call(viewer, '/api/runs')).value as any[]).map((r) => r.input), ['bob private question']);
  } finally { await s.close(); }
});

test('tenant B cannot read or change tenant A versions, knowledge, documents, conversations or runs by id', async () => {
  const s = await studio();
  const adminA = { tenant: randomUUID(), person: randomUUID(), roles: ['studio_admin'] };
  const adminB = { tenant: randomUUID(), person: adminA.person, roles: ['studio_admin'] };
  try {
    const a = await seedTenant(s, adminA);
    insertRun(s.store, a.app, a.workspace, adminA.person, 'tenant A run secret');
    const denied: [string, string, unknown?][] = [
      [`/api/apps/${a.app}`, 'GET'],
      [`/api/apps/${a.app}/versions`, 'GET'],
      [`/api/apps/${a.app}/versions/1/restore`, 'POST', {}],
      [`/api/apps/${a.app}/export`, 'GET'],
      [`/api/apps/${a.app}/publish`, 'POST', {}],
      [`/api/apps/${a.app}`, 'DELETE'],
      [`/api/apps/${a.app}/conversations?mode=published`, 'GET'],
      [`/api/apps/${a.app}/conversations/${a.conversation}`, 'GET'],
      [`/api/apps/${a.app}/conversations/${a.conversation}`, 'DELETE'],
      [`/api/apps/${a.app}/run`, 'POST', { input: 'hi', conversation_id: a.conversation, mode: 'published' }],
      [`/api/knowledge/${a.kb}/documents`, 'GET'],
      [`/api/knowledge/${a.kb}/documents`, 'POST', { name: 'x', content: 'x' }],
      [`/api/knowledge/${a.kb}/search`, 'POST', { query: 'secret payroll' }],
      [`/api/knowledge/${a.kb}`, 'DELETE'],
      [`/api/documents/${a.doc}`, 'GET'],
      [`/api/documents/${a.doc}`, 'PUT', { content: 'overwritten by B' }],
      [`/api/documents/${a.doc}`, 'PATCH', { enabled: false }],
      [`/api/documents/${a.doc}`, 'DELETE'],
    ];
    for (const [path, method, value] of denied) {
      const response = await s.call(adminB, path, method, value);
      assert.equal(response.status, 404, `${method} ${path} -> ${response.status} ${JSON.stringify(response.value)}`);
      assert.ok(!JSON.stringify(response.value).includes('secret'), `${method} ${path} leaked`);
    }
    for (const path of ['/api/runs', `/api/runs?app_id=${a.app}`, '/api/knowledge', '/api/apps']) {
      const response = await s.call(adminB, path);
      assert.equal(response.status, 200);
      assert.deepEqual(response.value, [], path);
    }
    const db = s.store.db;
    assert.equal(db.prepare('SELECT count(*) AS n FROM apps WHERE id=?').get(a.app)!.n, 1);
    assert.equal(db.prepare('SELECT count(*) AS n FROM knowledge WHERE id=?').get(a.kb)!.n, 1);
    assert.deepEqual({ ...db.prepare('SELECT content,enabled FROM documents WHERE id=?').get(a.doc) }, { content: 'secret payroll 500', enabled: 1 });
    assert.equal(db.prepare('SELECT count(*) AS n FROM conversations WHERE id=?').get(a.conversation)!.n, 1);
    assert.equal(db.prepare('SELECT count(*) AS n FROM runs WHERE app_id=?').get(a.app)!.n, 1);
    assert.equal((await s.call(adminA, `/api/apps/${a.app}/versions`)).value.length, 1);
  } finally { await s.close(); }
});
