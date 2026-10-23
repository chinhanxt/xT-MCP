// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Integrated Studio plus a fixture xT core callback; identities are fixtures, not Keycloak.
import { createServer } from 'node:http';
import { createHash, createHmac, randomBytes, randomUUID } from 'node:crypto';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';

export interface FixtureRecord { revision: number; title: string; pages?: string[]; denied?: boolean }
export interface CoreCall { name: string; arguments: Record<string, unknown> }

export async function integratedStudio() {
  const dir = mkdtempSync(join(tmpdir(), 'xt-studio-int-'));
  const store = new Store(dir);
  const secret = randomBytes(32);
  const records = new Map<string, FixtureRecord>();
  const calls: CoreCall[] = [];
  // `handler` replaces the record fixture with a programmable xT tool responder.
  const core: { status: number; handler?: (call: CoreCall) => unknown } = { status: 200 };
  const coreServer = createServer(async (request, response) => {
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(chunk);
    const body = Buffer.concat(chunks).toString();
    const capability = String(request.headers['x-xt-capability']);
    if (request.headers['x-xt-signature'] !== createHmac('sha256', secret).update(capability + '\n' + body).digest('hex')) {
      response.writeHead(401).end('{}');
      return;
    }
    const payload = JSON.parse(body);
    response.setHeader('Content-Type', 'application/json');
    if (core.status !== 200) { response.writeHead(core.status).end('{"error":"unavailable"}'); return; }
    if (payload.action === 'list') { response.end(JSON.stringify({ tools: [] })); return; }
    calls.push(payload);
    if (core.handler) { response.end(JSON.stringify(await core.handler(payload))); return; }
    const args = payload.arguments, record = records.get(String(args.ref));
    if (!record) { response.end(JSON.stringify({ ok: false, error: { code: 'not_found' } })); return; }
    if (record.denied) { response.end(JSON.stringify({ ok: false, error: { code: 'access_denied' } })); return; }
    const index = args.text_cursor ? Number(args.text_cursor) : 0;
    const wantsText = Array.isArray(args.include) && args.include.includes('text');
    response.end(JSON.stringify({ ok: true, untrusted: true, meta: {}, data: {
      record: { ref: args.ref, title: record.title, revision: record.revision },
      ...(wantsText && record.pages ? { text: { content: record.pages[index],
        next_text_cursor: index + 1 < record.pages.length ? String(index + 1) : null } } : {}),
    } }));
  });
  await new Promise<void>((resolve) => coreServer.listen(0, '127.0.0.1', resolve));
  const previousCore = process.env.STUDIO_XT_CORE_URL;
  process.env.STUDIO_XT_CORE_URL = `http://127.0.0.1:${(coreServer.address() as AddressInfo).port}`;
  const server = createStudio(store, { integrated: true, secret });
  await new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const tenant = randomUUID(), person = randomUUID();
  async function call(path: string, method = 'GET', value?: unknown, options: { roles?: string[]; capability?: boolean } = {}) {
    const payload = value === undefined ? '' : JSON.stringify(value);
    const claim = { v: 1, tenant, person, name: 'Fixture', workspace_name: 'Fixture tenant', roles: options.roles ?? ['studio_editor'],
      method, path, body_sha256: createHash('sha256').update(payload).digest('hex'), iat: Math.floor(Date.now() / 1000),
      nonce: randomUUID(), ...(options.capability === false ? {} : { capability: randomBytes(32).toString('base64url') }) };
    const encoded = Buffer.from(JSON.stringify(claim)).toString('base64url');
    const response = await fetch(origin + path, { method, body: payload || undefined, headers: {
      'x-xt-assertion': encoded, 'x-xt-signature': createHmac('sha256', secret).update(encoded).digest('hex'),
      ...(payload ? { 'content-type': 'application/json' } : {}) } });
    return { status: response.status, value: await response.json() as any };
  }
  async function close() {
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await new Promise<void>((resolve) => coreServer.close(() => resolve()));
    if (previousCore === undefined) delete process.env.STUDIO_XT_CORE_URL;
    else process.env.STUDIO_XT_CORE_URL = previousCore;
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
  return { store, records, calls, core, call, close };
}
