// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// One build tree, preserving each component's existing dependency lockfile.
import { spawnSync } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseEnv } from 'node:util';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const components = ['imports/dify-rea', 'imports/waretrack-3d', 'desk'];
const action = process.argv[2];
if (!['setup', 'build', 'test', 'check'].includes(action)) {
  throw new Error('Usage: node scripts/unified.mjs setup|build|test|check (Node >=24)');
}
if (Number(process.versions.node.split('.')[0]) < 24) throw new Error('Node >=24 is required');
const deskFile = existsSync(resolve(root, 'desk/.env')) ? 'desk/.env' : 'desk/.env.example';
const env = { ...parseEnv(readFileSync(resolve(root, deskFile), 'utf8')), ...process.env };

function run(component, args) {
  const result = spawnSync('npm', args, { cwd: resolve(root, component), env, stdio: 'inherit' });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error(`${component}: npm ${args.join(' ')} failed (${result.signal || result.status})`);
}
if (action === 'setup') {
  for (const component of components) run(component, ['ci', '--no-audit', '--no-fund']);
} else {
  if (action === 'test' || action === 'check') {
    for (const component of components) {
      const manifest = JSON.parse(readFileSync(resolve(root, component, 'package.json'), 'utf8'));
      if (manifest.scripts?.test) run(component, ['test']);
      else {
        console.log(`${component}: no automated test suite; verifying production build.`);
        if (action === 'test') run(component, ['run', 'build']);
      }
    }
  }
  if (action === 'build' || action === 'check') {
    for (const component of components) run(component, ['run', 'build']);
    for (const [component, name] of [['imports/dify-rea', 'studio'], ['imports/waretrack-3d', 'waretrack']]) {
      const target = resolve(root, 'desk/dist', name);
      mkdirSync(target, { recursive: true });
      cpSync(resolve(root, component, 'dist'), target, { recursive: true });
      if (name === 'studio') {
        const index = resolve(target, 'index.html');
        writeFileSync(index, readFileSync(index, 'utf8').replaceAll('href="/app.css"', 'href="./app.css"').replaceAll('src="/app.js"', 'src="./app.js"'));
      }
    }
    console.log('Unified portal built in desk/dist (Studio and WareTrack included).');
  }
}
