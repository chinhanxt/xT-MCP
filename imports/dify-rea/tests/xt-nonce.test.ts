// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import type { Server } from 'node:http';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';

const path = '/api/session';
const issuer = (secret: Buffer) => createHash('sha256').update(secret).digest('hex');
function signed(secret: Buffer, tenant: string, nonce: string = randomUUID(), iat = Math.floor(Date.now() / 1000)) {
  const claim = { v: 1, tenant, person: randomUUID(), name: 'Alice', workspace_name: 'Acme', roles: ['studio_user'],
    method: 'GET', path, body_sha256: createHash('sha256').update('').digest('hex'), iat, nonce };
  const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
  return { 'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex') };
}
async function fixture(run: (f: { directory: string; open: () => Store; listen: (store: Store, secret: Buffer) => Promise<string>;
  stop: (server: string, store: Store) => Promise<void> }) => Promise<void>) {
  const directory = mkdtempSync(join(tmpdir(), 'xt-studio-nonce-'));
  const stores = new Set<Store>(), servers = new Map<string, Server>();
  const open = () => { const store = new Store(directory); stores.add(store); return store; };
  const listen = async (store: Store, secret: Buffer) => {
    const server = createStudio(store, { integrated: true, secret });
    await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    servers.set(origin, server); return origin;
  };
  const stop = async (origin: string, store: Store) => {
    await new Promise<void>((resolve, reject) => servers.get(origin)!.close((error) => error ? reject(error) : resolve()));
    servers.delete(origin); store.close(); stores.delete(store);
  };
  try { await run({ directory, open, listen, stop }); }
  finally {
    for (const server of servers.values()) await new Promise<void>((resolve) => server.close(() => resolve()));
    for (const store of stores) store.close();
    rmSync(directory, { recursive: true, force: true });
  }
}
async function status(origin: string, headers: Record<string, string>) {
  const response = await fetch(origin + path, { headers });
  await response.arrayBuffer(); return response.status;
}
const count = (store: Store) => Number(store.db.prepare('SELECT count(*) AS n FROM xt_assertion_nonces').get()!.n);

test('accepted assertions stay consumed after closing and reopening Studio and SQLite', async () => {
  await fixture(async ({ open, listen, stop }) => {
    const secret = randomBytes(32), headers = signed(secret, randomUUID());
    let store = open(), origin = await listen(store, secret);
    assert.equal(await status(origin, headers), 200);
    await stop(origin, store);
    store = open(); origin = await listen(store, secret);
    assert.equal(await status(origin, headers), 401);
    assert.equal(count(store), 1);
  });
});

test('separate Store connections and verifiers reject shared-database replay atomically', async () => {
  await fixture(async ({ open, listen }) => {
    const secret = randomBytes(32), tenant = randomUUID(), a = open(), b = open();
    const origins = [await listen(a, secret), await listen(b, secret)];
    const headers = signed(secret, tenant);
    assert.equal(await status(origins[0], headers), 200);
    assert.equal(await status(origins[1], headers), 401);
    const concurrent = signed(secret, tenant);
    const statuses = await Promise.all(Array.from({ length: 12 }, (_, i) => status(origins[i % 2], concurrent)));
    assert.deepEqual(statuses.sort(), [200, ...Array(11).fill(401)]);
    assert.equal(count(a), 2);
    assert.equal(count(b), 2);
  });
});

test('UUID letter casing does not create a second tenant or nonce replay identity', async () => {
  await fixture(async ({ open, listen }) => {
    const store = open(), secret = randomBytes(32), origin = await listen(store, secret);
    const tenant = 'aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa', nonce = 'bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb';
    assert.equal(await status(origin, signed(secret, tenant, nonce)), 200);
    assert.equal(await status(origin, signed(secret, tenant, nonce.toUpperCase())), 401);
    assert.equal(await status(origin, signed(secret, tenant.toUpperCase(), nonce)), 401);
    assert.equal(await status(origin, signed(secret, tenant.toUpperCase(), nonce.toUpperCase())), 401);
    assert.equal(count(store), 1);
  });
});

test('forged signature floods never allocate durable nonce entries', async () => {
  await fixture(async ({ open, listen }) => {
    const store = open(), secret = randomBytes(32), tenant = randomUUID(), origin = await listen(store, secret);
    assert.equal(count(store), 0);
    const expired = randomUUID();
    store.db.prepare('INSERT INTO xt_assertion_nonces(issuer,tenant_id,nonce,expires_at) VALUES(?,?,?,?)')
      .run(issuer(secret), tenant, expired, Math.floor(Date.now() / 1000) - 1);
    assert.equal(await status(origin, {}), 401);
    for (let i = 0; i < 128; i++) {
      const headers = signed(secret, tenant);
      headers['x-xt-signature'] = '0'.repeat(64);
      assert.equal(await status(origin, headers), 401);
    }
    assert.equal(count(store), 1);
    assert.ok(store.db.prepare('SELECT nonce FROM xt_assertion_nonces WHERE nonce=?').get(expired));
    assert.equal(await status(origin, signed(secret, tenant)), 200);
    assert.equal(count(store), 1);
    assert.equal(store.db.prepare('SELECT nonce FROM xt_assertion_nonces WHERE nonce=?').get(expired), undefined);
  });
});

test('expiry cleanup preserves the inclusive validity boundary and future-issued replay entries', async () => {
  await fixture(async ({ open, listen }) => {
    const store = open(), secret = randomBytes(32), tenant = randomUUID(), origin = await listen(store, secret);
    const realNow = Date.now, base = Math.floor(realNow() / 1000), nonce = randomUUID(), futureNonce = randomUUID();
    let now = base;
    Date.now = () => now * 1000;
    try {
      const old = signed(secret, tenant, nonce, base), future = signed(secret, tenant, futureNonce, base + 29);
      assert.equal(await status(origin, old), 200);
      assert.equal(await status(origin, future), 200);
      now = base + 30;
      assert.equal(await status(origin, old), 401);
      assert.equal(count(store), 2);
      now = base + 31;
      assert.equal(await status(origin, old), 401);
      assert.equal(await status(origin, signed(secret, tenant)), 200);
      assert.equal(store.db.prepare('SELECT nonce FROM xt_assertion_nonces WHERE nonce=?').get(nonce), undefined);
      assert.ok(store.db.prepare('SELECT nonce FROM xt_assertion_nonces WHERE nonce=?').get(futureNonce));
      now = base + 59;
      assert.equal(await status(origin, future), 401);
      now = base + 60;
      assert.equal(await status(origin, future), 401);
      assert.equal(await status(origin, signed(secret, tenant, futureNonce)), 200);
      assert.equal(count(store), 2);
    } finally { Date.now = realNow; }
  });
});

test('quota is isolated by tenant and issuer and never evicts live replay entries', async () => {
  await fixture(async ({ open, listen }) => {
    const store = open(), secretA = randomBytes(32), secretB = randomBytes(32), tenantA = randomUUID(), tenantB = randomUUID();
    const originA = await listen(store, secretA), originB = await listen(store, secretB);
    const now = Math.floor(Date.now() / 1000), nonce = randomUUID();
    const consumed = signed(secretA, tenantA, nonce, now);
    assert.equal(await status(originA, consumed), 200);
    const insert = store.db.prepare('INSERT INTO xt_assertion_nonces(issuer,tenant_id,nonce,expires_at) VALUES(?,?,?,?)');
    store.transaction(() => {
      for (let i = 1; i < 9999; i++) insert.run(issuer(secretA), tenantA, randomUUID(), now + 31);
    });
    assert.equal(count(store), 9999);
    assert.equal(await status(originA, signed(secretA, tenantA, randomUUID(), now)), 200);
    assert.equal(await status(originA, consumed), 401);
    assert.equal(await status(originA, signed(secretA, tenantA)), 401);
    assert.equal(count(store), 10000);
    assert.equal(await status(originA, signed(secretA, tenantA.toUpperCase())), 401);
    assert.equal(count(store), 10000);
    assert.equal(await status(originA, signed(secretA, tenantB, nonce, now)), 200);
    assert.equal(await status(originB, signed(secretB, tenantA, nonce, now)), 200);
    assert.equal(await status(originA, consumed), 401);
    assert.equal(count(store), 10002);
    assert.equal(store.db.prepare('SELECT count(*) AS n FROM xt_assertion_nonces WHERE issuer=? AND tenant_id=?')
      .get(issuer(secretA), tenantA)!.n, 10000);
    const realNow = Date.now;
    Date.now = () => (now + 31) * 1000;
    try {
      assert.equal(await status(originA, signed(secretA, tenantA)), 200);
      assert.equal(count(store), 1);
    } finally { Date.now = realNow; }
  });
});
