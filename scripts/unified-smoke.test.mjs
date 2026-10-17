// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// node --test scripts/unified-smoke.test.mjs  (fake local servers only; no real stack, no credentials)
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { createServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { runSmoke } from './unified-smoke.mjs';

const script = fileURLToPath(new URL('./unified-smoke.mjs', import.meta.url));
const servers = [];

// Async on purpose: spawnSync would block this process's event loop and starve the fake servers.
function cli(args, env = process.env) {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [script, ...args], { env });
    let output = '';
    child.stdout.on('data', (d) => (output += d));
    child.stderr.on('data', (d) => (output += d));
    child.on('close', (status) => resolve({ status, output }));
  });
}

function serve(handler) {
  return new Promise((resolve) => {
    const server = createServer(handler);
    servers.push(server);
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${server.address().port}`));
  });
}

function reply(res, status, body = '', type = 'application/json') {
  res.writeHead(status, { 'content-type': type });
  res.end(typeof body === 'string' ? body : JSON.stringify(body));
}

// A healthy fake: every endpoint behaves as the real stack documents.
const healthy = (req, res) => {
  if (req.url === '/healthz') return reply(res, 200, { status: 'ok' });
  if (req.url === '/.well-known/openid-configuration') return reply(res, 200, { issuer: 'x', jwks_uri: 'x/jwks' });
  if (req.url === '/studio/' || req.url === '/waretrack/') return reply(res, 200, '<html></html>', 'text/html');
  if (req.url === '/studio-api/session' || req.url === '/mcp') return reply(res, 401, { error: 'unauthenticated' });
  return reply(res, 404, {});
};

after(() => servers.forEach((s) => s.close()));

test('all probes pass against a healthy fake stack', async () => {
  const base = await serve(healthy);
  const report = await runSmoke({ core: base, portal: base, issuer: base });
  assert.equal(report.ok, true, JSON.stringify(report.results.filter((r) => !r.ok)));
  assert.deepEqual(report.results.map((r) => r.status), [200, 200, 200, 200, 401, 401, 401]);
});

test('an unauthenticated endpoint that answers 200 is a failure', async () => {
  const base = await serve((req, res) => (req.url === '/mcp' ? reply(res, 200, {}) : healthy(req, res)));
  const report = await runSmoke({ core: base, portal: base, issuer: base });
  assert.equal(report.ok, false);
  const failed = report.results.filter((r) => !r.ok);
  assert.deepEqual(failed.map((r) => r.name), ['core /mcp without token']);
  assert.match(failed[0].detail, /expected HTTP 401, got 200/);
});

test('missing static module, bad health body and missing issuer are reported separately', async () => {
  const base = await serve((req, res) => {
    if (req.url === '/waretrack/') return reply(res, 404, '');
    if (req.url === '/healthz') return reply(res, 200, { status: 'degraded' });
    return healthy(req, res);
  });
  const report = await runSmoke({ core: base, portal: base, issuer: undefined });
  assert.deepEqual(
    report.results.filter((r) => !r.ok).map((r) => r.name),
    ['core /healthz', 'oidc discovery', 'portal /waretrack/'],
  );
});

test('a closed port fails without throwing', async () => {
  const dead = await serve(healthy);
  servers.pop().close();
  const report = await runSmoke({ core: dead, portal: dead, issuer: dead });
  assert.equal(report.ok, false);
  assert.ok(report.results.every((r) => r.status === null && r.detail));
});

test('CLI writes JSON evidence without secrets and exits non-zero on failure', async () => {
  const base = await serve(healthy);
  const dir = mkdtempSync(join(tmpdir(), 'xt-smoke-'));
  try {
    const out = join(dir, 'evidence', 'smoke.json');
    const ok = await cli(['--core', base, '--portal', base, '--issuer', base, '--out', out]);
    assert.equal(ok.status, 0, ok.output);
    const evidence = JSON.parse(readFileSync(out, 'utf8'));
    assert.equal(evidence.ok, true);
    assert.equal(evidence.results.length, 7);
    assert.doesNotMatch(JSON.stringify(evidence), /authorization|bearer|token":/i);
    const bad = await cli(['--core', base, '--portal', base, '--out', join(dir, 'bad.json')], { PATH: process.env.PATH });
    assert.equal(bad.status, 1);
    assert.equal(JSON.parse(readFileSync(join(dir, 'bad.json'), 'utf8')).ok, false);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
