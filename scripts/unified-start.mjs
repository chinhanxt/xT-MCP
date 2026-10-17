// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Start the integrated app against provisioned local xT services; never seed existing databases.
import { spawn, spawnSync } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, writeFileSync, statSync } from 'node:fs';
import { createServer } from 'node:net';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('Node >=24 is required');
if (!process.env.XT_UNIFIED_ENV_FILE) throw new Error('Set XT_UNIFIED_ENV_FILE to a private xT runtime .env (see docs/unified-local.md)');
const configFile = resolve(process.env.XT_UNIFIED_ENV_FILE);
if ((statSync(configFile).mode & 0o077) !== 0) throw new Error('Runtime env file must be private (chmod 600)');
const env = { ...parseEnv(readFileSync(resolve(root, 'desk/.env.example'), 'utf8')), ...parseEnv(readFileSync(configFile, 'utf8')), ...process.env };
for (const name of ['XT_DB_APP_URL', 'XT_IDP_ISSUER', 'XT_MCP_RESOURCE', 'XT_RECEIPT_SIGNING_KEY_FILE', 'XT_CURSOR_KEY_FILE']) {
  if (!env[name]) throw new Error(`Missing ${name} in runtime config`);
}
const corePort = port('XT_UNIFIED_CORE_PORT', 8010);
const studioPort = port('XT_UNIFIED_STUDIO_PORT', 4327);
const deskPort = port('DESK_PORT', 5173);
if (new Set([corePort, studioPort, deskPort]).size !== 3) throw new Error('Service ports must differ');
const core = `http://127.0.0.1:${corePort}`;
const desk = `http://localhost:${deskPort}`;
const state = resolve(env.XT_UNIFIED_STATE_DIR || resolve(homedir(), '.local/state/xt-unified'));
mkdirSync(state, { recursive: true, mode: 0o700 });
const secret = resolve(env.XT_STUDIO_SECRET_FILE || resolve(state, 'studio.key'));
if (!existsSync(secret)) writeFileSync(secret, randomBytes(32), { mode: 0o600, flag: 'wx' });
if ((statSync(secret).mode & 0o077) !== 0 || readFileSync(secret).length < 32) throw new Error('Studio key must be private and at least 32 bytes');
for (const value of [corePort, studioPort, deskPort]) await available(value);
const runtime = {
  ...env,
  XT_STUDIO_URL: `http://127.0.0.1:${studioPort}`,
  XT_STUDIO_SECRET_FILE: secret,
  XT_DESK_LLM_URL: env.XT_DESK_LLM_URL || env.DESK_LLM_URL,
  XT_DESK_LLM_MODEL: env.DESK_LLM_MODEL,
  XT_DESK_URL: desk,
  XT_MCP_ALLOWED_ORIGINS: desk,
  XT_IDP_ALLOW_HTTP: 'true',
  XT_MCP_ALLOWED_HOSTS: [...new Set([...(env.XT_MCP_ALLOWED_HOSTS || '').split(','), 'localhost', '127.0.0.1'])].filter(Boolean).join(','),
  DESK_HOST: 'localhost', DESK_PORT: String(deskPort), DESK_MCP_URL: `${core}/mcp`,
  DESK_LLM_URL: `${core}/llm`, DESK_XT_ORIGIN: desk,
  DESK_SANDBOX_URL: `http://127.0.0.1:${deskPort}/sandbox.html`,
};
// Rebuild with the same origin/client/model used at runtime, avoiding stale embedded auth config.
const build = spawnSync(process.execPath, ['scripts/unified.mjs', 'build'], { cwd: root, env: runtime, stdio: 'inherit' });
if (build.error) throw build.error;
if (build.status !== 0) throw new Error('Integrated build failed; services have not been started');
const children = [];
let stopping = false;
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) if (child.exitCode === null) terminate(child, 'SIGTERM');
  const timer = setTimeout(() => {
    for (const child of children) if (child.exitCode === null) terminate(child, 'SIGKILL');
  }, 5000);
  timer.unref();
  process.exitCode = code;
}
function start(command, args, cwd, extra = {}) {
  const child = spawn(command, args, { cwd: resolve(root, cwd), env: { ...runtime, ...extra }, stdio: 'inherit', detached: process.platform !== 'win32' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) { console.error(`${command} exited (${code})`); stop(code || 1); } });
  return child;
}
process.once('SIGINT', () => stop());
process.once('SIGTERM', () => stop());
start(process.execPath, ['src/server.ts'], 'imports/dify-rea', {
  STUDIO_XT_MODE: '1', PORT: String(studioPort), STUDIO_DATA_DIR: resolve(state, 'studio'),
  STUDIO_XT_CORE_URL: core,
});
start('uv', ['run', 'xt', 'serve', '--transport', 'http', '--host', '127.0.0.1', '--port', String(corePort)], '.');
start(process.execPath, ['node_modules/vite/bin/vite.js', 'preview'], 'desk');
console.log(`xT portal: ${desk} — uses the supplied provisioned xT database and identity provider.`);

function port(name, fallback) {
  const value = Number(env[name] || fallback);
  if (!Number.isInteger(value) || value < 1024 || value > 65535) throw new Error(`Invalid ${name}`);
  return value;
}
function available(value) {
  return new Promise((resolvePromise, reject) => {
    const server = createServer();
    server.once('error', () => reject(new Error(`Port ${value} already in use; existing services were not stopped`)));
    server.listen(value, () => server.close(resolvePromise));
  });
}
function terminate(child, signal) {
  try {
    if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) { if (error.code !== 'ESRCH') throw error; }
}
