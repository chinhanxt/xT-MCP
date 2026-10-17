// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Headless smoke for a running integrated stack: no credentials are sent or read, so it can only
// prove liveness and that unauthenticated access is refused. It does not prove login or tenancy.
//   node scripts/unified-smoke.mjs [--core URL] [--portal URL] [--issuer URL] [--out FILE]
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const TIMEOUT_MS = 5000;

async function probe(name, url, { method = 'GET', body, expect, check }) {
  const started = Date.now();
  const result = { name, method, url, expect, status: null, ok: false, ms: 0, detail: '' };
  try {
    const response = await fetch(url, {
      method,
      redirect: 'manual',
      signal: AbortSignal.timeout(TIMEOUT_MS),
      ...(body === undefined ? {} : { body: JSON.stringify(body), headers: { 'content-type': 'application/json' } }),
    });
    result.status = response.status;
    const text = await response.text();
    result.ok = response.status === expect;
    if (result.ok && check) {
      const problem = check(text, response);
      if (problem) { result.ok = false; result.detail = problem; }
    }
    if (!result.ok && !result.detail) result.detail = `expected HTTP ${expect}, got ${response.status}`;
  } catch (error) {
    result.detail = error?.name === 'TimeoutError' ? `no response within ${TIMEOUT_MS} ms` : String(error?.cause?.code ?? error?.cause?.errors?.[0]?.code ?? error?.cause?.message ?? error?.message ?? error);
  }
  result.ms = Date.now() - started;
  return result;
}

const json = (text) => { try { return JSON.parse(text); } catch { return undefined; } };

export async function runSmoke({ core, portal, issuer }) {
  const join = (base, path) => base.replace(/\/+$/, '') + path;
  const checks = [
    ['core /healthz', join(core, '/healthz'), { expect: 200, check: (t) => (json(t)?.status === 'ok' ? '' : 'body is not {"status":"ok"}') }],
    ['oidc discovery', issuer ? join(issuer, '/.well-known/openid-configuration') : '', {
      expect: 200,
      check: (t) => { const d = json(t); return d?.issuer && d?.jwks_uri ? '' : 'missing issuer or jwks_uri'; },
    }],
    ['portal /studio/', join(portal, '/studio/'), { expect: 200 }],
    ['portal /waretrack/', join(portal, '/waretrack/'), { expect: 200 }],
    ['portal /studio-api/session without token', join(portal, '/studio-api/session'), { expect: 401 }],
    ['core /studio-api/session without token', join(core, '/studio-api/session'), { expect: 401 }],
    ['core /mcp without token', join(core, '/mcp'), { method: 'POST', body: {}, expect: 401 }],
  ];
  const results = [];
  for (const [name, url, options] of checks) {
    results.push(url
      ? await probe(name, url, options)
      : { name, method: 'GET', url: '', expect: options.expect, status: null, ok: false, ms: 0, detail: 'XT_IDP_ISSUER / --issuer not set' });
  }
  return {
    generated_at: new Date().toISOString(),
    node: process.version,
    targets: { core, portal, issuer: issuer ?? null },
    ok: results.every((r) => r.ok),
    results,
  };
}

function option(args, name, fallback) {
  const index = args.indexOf(name);
  return index >= 0 ? args[index + 1] : fallback;
}

async function main() {
  const args = process.argv.slice(2);
  const env = process.env;
  const report = await runSmoke({
    core: option(args, '--core', `http://127.0.0.1:${env.XT_UNIFIED_CORE_PORT || 8010}`),
    portal: option(args, '--portal', `http://localhost:${env.DESK_PORT || 5173}`),
    issuer: option(args, '--issuer', env.XT_IDP_ISSUER || env.DESK_OIDC_ISSUER),
  });
  const out = resolve(option(args, '--out', 'unified-smoke.json'));
  mkdirSync(dirname(out), { recursive: true });
  writeFileSync(out, JSON.stringify(report, null, 2) + '\n');
  for (const r of report.results) console.log(`${r.ok ? 'PASS' : 'FAIL'}  ${r.name}  [${r.status ?? '-'}]  ${r.detail}`.trimEnd());
  console.log(`${report.ok ? 'OK' : 'FAILED'}: evidence written to ${out}`);
  process.exitCode = report.ok ? 0 : 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
