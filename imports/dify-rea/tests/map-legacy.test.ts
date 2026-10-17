// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { createStudio } from '../src/server.ts';
import { Store } from '../src/store.ts';
import { emptyConfig } from '../src/contracts.ts';
import { mapLegacyWorkspace } from '../src/map-legacy.ts';

function legacyStore() {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-legacy-'));
  const store = new Store(dir);
  const workspace = store.id(), other = store.id();
  store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run(workspace, 'Legacy A');
  store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run(other, 'Legacy B');
  store.db.prepare('INSERT INTO apps(id,workspace_id,name,description,kind,config,updated_at) VALUES(?,?,?,?,?,?,?)')
    .run(store.id(), workspace, 'App', '', 'chat', JSON.stringify(emptyConfig()), new Date().toISOString());
  const knowledge = store.id();
  store.db.prepare('INSERT INTO knowledge VALUES(?,?,?,?,?)').run(knowledge, workspace, 'KB', '', null);
  store.db.prepare('INSERT INTO documents(id,knowledge_id,name,content,status) VALUES(?,?,?,?,?)').run(store.id(), knowledge, 'Doc', 'x', 'ready');
  store.close();
  return { dir, workspace, other };
}
const mappings = (dir: string) => {
  const store = new Store(dir);
  try { return store.db.prepare('SELECT tenant_id,workspace_id FROM xt_workspace_map ORDER BY tenant_id').all().map((r) => ({ ...r })); }
  finally { store.close(); }
};

test('dry-run reports the plan without changing the database', () => {
  const { dir, workspace } = legacyStore();
  const tenant = randomUUID();
  try {
    const before = statSync(join(dir, 'studio.sqlite')).mtimeMs;
    const plan = mapLegacyWorkspace({ dataDir: dir, workspace, tenant });
    assert.equal(plan.status, 'would_map');
    assert.deepEqual(plan.workspace, { id: workspace, name: 'Legacy A' });
    assert.equal(plan.counts.apps, 1);
    assert.equal(plan.counts.documents, 1);
    assert.equal(statSync(join(dir, 'studio.sqlite')).mtimeMs, before);
    assert.deepEqual(mappings(dir), []);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('apply maps exactly the chosen workspace once; a second run changes nothing and fails', () => {
  const { dir, workspace, other } = legacyStore();
  const tenant = randomUUID();
  try {
    assert.equal(mapLegacyWorkspace({ dataDir: dir, workspace, tenant, apply: true }).status, 'mapped');
    assert.deepEqual(mappings(dir), [{ tenant_id: tenant, workspace_id: workspace }]);
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace, tenant, apply: true }), /already mapped to this tenant/);
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace, tenant }), /already mapped to this tenant/);
    assert.deepEqual(mappings(dir), [{ tenant_id: tenant, workspace_id: workspace }]);
    const store = new Store(dir);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM xt_workspace_map WHERE workspace_id=?').get(other)!.n, 0);
    store.close();
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('refuses cross-tenant collisions in both directions', () => {
  const { dir, workspace, other } = legacyStore();
  const tenantA = randomUUID(), tenantB = randomUUID();
  try {
    mapLegacyWorkspace({ dataDir: dir, workspace, tenant: tenantA, apply: true });
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace, tenant: tenantB, apply: true }), /already mapped to another tenant/);
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace: other, tenant: tenantA, apply: true }),
      new RegExp(`tenant already uses workspace ${workspace}`));
    assert.deepEqual(mappings(dir), [{ tenant_id: tenantA, workspace_id: workspace }]);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('rejects missing database, unknown workspace and non-UUID tenant without creating files', () => {
  const { dir, workspace } = legacyStore();
  const empty = mkdtempSync(join(tmpdir(), 'xt-studio-empty-'));
  try {
    assert.throws(() => mapLegacyWorkspace({ dataDir: empty, workspace, tenant: randomUUID() }), /studio.sqlite not found/);
    assert.throws(() => statSync(join(empty, 'credential.key')));
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace: 'missing', tenant: randomUUID(), apply: true }), /workspace not found/);
    assert.throws(() => mapLegacyWorkspace({ dataDir: dir, workspace, tenant: 'all' }), /tenant must be a UUID/);
    assert.deepEqual(mappings(dir), []);
  } finally {
    rmSync(dir, { recursive: true, force: true });
    rmSync(empty, { recursive: true, force: true });
  }
});

test('dry-run works on a pre-integration database without the mapping table', () => {
  const { dir, workspace } = legacyStore();
  const store = new Store(dir);
  store.db.exec('DROP TABLE xt_workspace_map');
  store.close();
  try {
    assert.equal(mapLegacyWorkspace({ dataDir: dir, workspace, tenant: randomUUID() }).status, 'would_map');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

test('after apply, integrated requests of the tenant see the legacy apps', async () => {
  const { dir, workspace } = legacyStore();
  const tenant = randomUUID();
  mapLegacyWorkspace({ dataDir: dir, workspace, tenant, apply: true });
  const store = new Store(dir);
  const secret = randomBytes(32);
  const server = createStudio(store, { integrated: true, secret });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  try {
    const claim = { v: 1, tenant, person: randomUUID(), name: 'Alice', workspace_name: 'Acme', roles: ['studio_editor'], method: 'GET',
      path: '/api/apps', body_sha256: createHash('sha256').update('').digest('hex'), iat: Math.floor(Date.now() / 1000), nonce: randomUUID() };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    const response = await fetch(`http://127.0.0.1:${(server.address() as AddressInfo).port}/api/apps`, { headers: {
      'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex') } });
    assert.deepEqual((await response.json() as { name: string }[]).map((a) => a.name), ['App']);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM workspaces').get()!.n, 2);
  } finally {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
