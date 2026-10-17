// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Read-only preview of an existing unified build; API/identity fixtures live in the spec.
// UNIFIED_DIST=/absolute/desk/dist npx playwright test -c e2e/unified-preview.mjs
import { defineConfig } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, extname, join, resolve, sep } from 'node:path';
import { tmpdir } from 'node:os';
const here = fileURLToPath(import.meta.url);
const port = Number(process.env.UNIFIED_E2E_PORT || 19612);
const dist = resolve(process.env.UNIFIED_DIST || join(dirname(here), '../dist'));
if (process.argv.includes('--serve')) {
  const types = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.png':'image/png','.webp':'image/webp','.woff2':'font/woff2'};
  createServer(async (request,response) => {
    try {
      const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
      let file = resolve(dist, '.' + (path === '/auth/callback' ? '/index.html' : path));
      if (file !== dist && !file.startsWith(dist + sep)) { response.writeHead(403).end(); return; }
      if ((await stat(file)).isDirectory()) file = join(file, 'index.html');
      response.writeHead(200, {'content-type':types[extname(file)] || 'application/octet-stream', 'cache-control':'no-store'});
      response.end(await readFile(file));
    } catch { response.writeHead(404).end(); }
  }).listen(port, '127.0.0.1');
}
export default defineConfig({
  testDir:dirname(here), testMatch:/unified-[a-z0-9-]+\.spec\.ts/, workers:1, timeout:45_000,
  reporter:[['list']], outputDir:join(tmpdir(),'xt-unified-browser-results'),
  use:{baseURL:`http://localhost:${port}`,headless:true,viewport:{width:1280,height:960},trace:'retain-on-failure'},
  webServer:{command:`node '${here.replaceAll("'", "'\\''")}' --serve`,url:`http://localhost:${port}`,timeout:10_000,reuseExistingServer:false},
});
