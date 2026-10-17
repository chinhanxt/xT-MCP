// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Integrated mode fails closed on knowledge documents without an xT source (legacy or mapped
// workspaces): no retrieval, no citation, no direct read; the list hides titles of denied sources.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { integratedStudio } from './xt-fixture.ts';
import { retrieve } from '../src/knowledge.ts';
import { runWorkflow } from '../src/workflow.ts';
import { emptyConfig } from '../src/contracts.ts';
import type { XtTools } from '../src/xt-tools.ts';

function legacyDocument(store: any, kb: string) {
  const doc = randomUUID(), chunk = randomUUID();
  store.db.prepare('INSERT INTO documents(id,knowledge_id,name,content,status) VALUES(?,?,?,?,?)').run(doc, kb, 'Legacy payroll', 'tồn kho legacy 777', 'ready');
  store.db.prepare('INSERT INTO chunks VALUES(?,?,?,?)').run(chunk, doc, 'tồn kho legacy 777', null);
  store.db.prepare('INSERT INTO chunk_search(id,content) VALUES(?,?)').run(chunk, 'tồn kho legacy 777');
  return doc;
}

test('integrated: documents without xt_ref are excluded from search, runs and direct reads; standalone still uses them', async () => {
  const s = await integratedStudio();
  try {
    s.records.set('document:kho', { revision: 1, title: 'Quy trình kho', pages: ['Tồn kho được kiểm hằng tuần.'] });
    const kb = (await s.call('/api/knowledge', 'POST', { name: 'Kho' })).value.id;
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:kho' })).status, 201);
    const legacy = legacyDocument(s.store, kb);
    const workspace = String(s.store.db.prepare('SELECT workspace_id FROM knowledge WHERE id=?').get(kb)!.workspace_id);

    const search = await s.call(`/api/knowledge/${kb}/search`, 'POST', { query: 'tồn kho' });
    assert.equal(search.status, 200);
    assert.deepEqual(search.value.map((c: any) => c.document_name), ['Quy trình kho']);
    // without a core connection the legacy chunk is still not served
    const noCap = await s.call(`/api/knowledge/${kb}/search`, 'POST', { query: 'legacy 777' }, { capability: false });
    assert.ok(!JSON.stringify(noCap.value).includes('777'), JSON.stringify(noCap.value));

    const read = await s.call(`/api/documents/${legacy}`);
    assert.equal(read.status, 404);
    assert.ok(!JSON.stringify(read.value).includes('777'));

    const tools: XtTools = { async list() { return []; }, async call(_name, args) {
      return { ok: true, data: { record: { ref: args.ref, title: 'Quy trình kho', revision: 1 } } };
    } };
    const config = { ...emptyConfig(), graph: { nodes: [
      { id: 'start', type: 'start' as const, position: { x: 0, y: 0 }, data: { label: 'Bắt đầu' } },
      { id: 'kb', type: 'retrieval' as const, position: { x: 0, y: 0 }, data: { label: 'Tìm', knowledge_id: kb, prompt: '{{input}}' } },
      { id: 'end', type: 'output' as const, position: { x: 0, y: 0 }, data: { label: 'Kết thúc', template: '{{kb}}' } },
    ], edges: [{ id: 'a', source: 'start', target: 'kb' }, { id: 'b', source: 'kb', target: 'end' }] } };
    const integrated = await runWorkflow(s.store, workspace, config, 'tồn kho legacy', undefined, {}, tools, true);
    assert.deepEqual(integrated.citations.map((c) => c.document_name), ['Quy trình kho']);
    assert.ok(!integrated.output.includes('777'));
    // integrated without a core connection: legacy chunks dropped, xT chunks fail closed
    const offline = await runWorkflow(s.store, workspace, config, 'legacy 777', undefined, {}, undefined, true);
    assert.deepEqual(offline.citations, []);
    assert.ok(!offline.output.includes('777'));
    await assert.rejects(runWorkflow(s.store, workspace, config, 'tồn kho', undefined, {}, undefined, true), /kiểm tra nguồn xT/);

    // standalone mode (no check, not integrated) is unchanged: the legacy document is retrievable
    s.store.db.prepare('DELETE FROM documents WHERE xt_ref IS NOT NULL').run();
    assert.deepEqual((await retrieve(s.store, workspace, [kb], 'legacy 777')).map((c) => c.document_name), ['Legacy payroll']);
  } finally { await s.close(); }
});

test('integrated: document list hides titles whose xT source is now denied', async () => {
  const s = await integratedStudio();
  try {
    s.records.set('document:luong', { revision: 1, title: 'Bảng lương giám đốc', pages: ['Lương 500 triệu.'] });
    s.records.set('document:kho', { revision: 1, title: 'Quy trình kho', pages: ['Kiểm kho.'] });
    const kb = (await s.call('/api/knowledge', 'POST', { name: 'HR' })).value.id;
    for (const ref of ['document:luong', 'document:kho'])
      assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: ref })).status, 201);
    const names = async () => ((await s.call(`/api/knowledge/${kb}/documents`)).value as any[]).map((d) => d.name).sort();
    assert.deepEqual(await names(), ['Bảng lương giám đốc', 'Quy trình kho']);
    s.records.get('document:luong')!.denied = true;
    const listed = await s.call(`/api/knowledge/${kb}/documents`);
    assert.equal(listed.status, 200);
    assert.ok(!JSON.stringify(listed.value).includes('lương'), JSON.stringify(listed.value));
    assert.deepEqual(await names(), ['Quy trình kho']);
    s.records.delete('document:kho');
    assert.deepEqual(await names(), []);
  } finally { await s.close(); }
});
