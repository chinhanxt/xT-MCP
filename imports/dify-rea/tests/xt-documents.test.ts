// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { integratedStudio } from './xt-fixture.ts';

test('integrated knowledge documents come only from xT records read under the caller capability', async () => {
  const s = await integratedStudio();
  try {
    s.records.set('document:sop-kho', { revision: 3, title: 'SOP xuất kho', pages: ['Bước 1: lập phiếu. ', 'Bước 2: quản lý duyệt.'] });
    s.records.set('document:no-text', { revision: 1, title: 'Không có văn bản' });
    s.records.set('document:secret', { revision: 1, title: 'Mật', pages: ['x'], denied: true });
    const kb = (await s.call('/api/knowledge', 'POST', { name: 'SOP' })).value.id;
    const created = await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:sop-kho' });
    assert.equal(created.status, 201, JSON.stringify(created.value));
    const row = s.store.db.prepare('SELECT name,content,xt_ref,xt_revision FROM documents WHERE id=?').get(created.value.id)!;
    assert.deepEqual({ ...row }, { name: 'SOP xuất kho', content: 'Bước 1: lập phiếu. Bước 2: quản lý duyệt.', xt_ref: 'document:sop-kho', xt_revision: 3 });
    assert.deepEqual(s.calls.map((c) => c.name), ['get_record', 'get_record']);
    assert.deepEqual(s.calls[1].arguments, { ref: 'document:sop-kho', include: ['text'], text_cursor: '1' });

    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { name: 'raw', content: 'tự gõ' })).status, 403);
    assert.equal((await s.call(`/api/knowledge/${kb}/extract`, 'POST', { name: 'a.txt', file_base64: 'eA==' })).status, 403);
    assert.equal((await s.call(`/api/documents/${created.value.id}`, 'PUT', { content: 'sửa tay' })).status, 403);
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:missing' })).status, 404);
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:secret' })).status, 403);
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:no-text' })).status, 422);
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: '../etc/passwd' })).status, 400);
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:sop-kho' }, { capability: false })).status, 503);
    s.core.status = 502;
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:sop-kho' })).status, 502);
    s.core.status = 200;
    assert.equal(s.store.db.prepare('SELECT count(*) AS n FROM documents').get()!.n, 1);

    s.records.set('document:sop-kho', { revision: 4, title: 'SOP xuất kho v4', pages: ['Bước 1 mới.'] });
    const synced = await s.call(`/api/documents/${created.value.id}`, 'PUT', {});
    assert.equal(synced.status, 200, JSON.stringify(synced.value));
    const after = s.store.db.prepare('SELECT name,content,xt_revision FROM documents WHERE id=?').get(created.value.id)!;
    assert.deepEqual({ ...after }, { name: 'SOP xuất kho v4', content: 'Bước 1 mới.', xt_revision: 4 });
    assert.equal((await s.call(`/api/knowledge/${kb}/documents`, 'POST', { xt_ref: 'document:sop-kho' }, { roles: ['studio_user'] })).status, 403);
  } finally { await s.close(); }
});
