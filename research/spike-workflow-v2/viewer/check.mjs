// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Kiểm BPMN bằng công cụ chuẩn: bpmn-moddle (schema BPMN 2.0) + render bpmn-js trong Chrome headless.
import fs from 'node:fs';
import path from 'node:path';
import BpmnModdle from 'bpmn-moddle';
import { chromium } from 'playwright-core';
const files = process.argv.slice(2);
const outDir = process.env.OUT || '.';
const moddle = new BpmnModdle();
const browser = await chromium.launch({ executablePath: '/usr/bin/google-chrome', headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1800, height: 900 } });
const lib = fs.readFileSync(new URL('./node_modules/bpmn-js/dist/bpmn-viewer.production.min.js', import.meta.url), 'utf8');
await page.setContent('<html><body style="margin:0"><div id="c" style="width:1800px;height:900px"></div></body></html>');
await page.addScriptTag({ content: lib });
let fail = 0;
for (const f of files) {
  const xml = fs.readFileSync(f, 'utf8');
  const { warnings } = await moddle.fromXML(xml);
  const r = await page.evaluate(async (xml) => {
    document.getElementById('c').innerHTML = '';
    const v = new BpmnJS({ container: '#c' });
    try {
      const res = await v.importXML(xml);
      const reg = v.get('elementRegistry');
      const all = reg.getAll();
      const shapes = all.filter(e => !e.waypoints && e.type !== 'label' && e.type !== 'bpmn:Collaboration' && e.type !== 'bpmn:Process');
      const conns = all.filter(e => e.waypoints);
      v.get('canvas').zoom('fit-viewport');
      return { ok: true, warnings: res.warnings.map(w => w.message), shapes: shapes.length, connections: conns.length,
               types: [...new Set(shapes.map(s => s.type))].sort() };
    } catch (e) { return { ok: false, error: String(e) }; }
  }, xml);
  const png = path.join(outDir, path.basename(f).replace(/\.bpmn$/, '.png'));
  await page.screenshot({ path: png });
  const bad = !r.ok || r.warnings.length || warnings.length;
  if (bad) fail++;
  console.log(JSON.stringify({ file: path.basename(f), moddle_warnings: warnings.map(w => w.message), ...r, png: path.basename(png) }));
}
await browser.close();
process.exit(fail ? 1 : 0);
