import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { extractDocument } from '../src/documents.ts';
import { indexDocument, retrieve } from '../src/knowledge.ts';
import { Store } from '../src/store.ts';
function pdf(text: string): string {
  const stream = `BT /F1 12 Tf 50 750 Td (${text}) Tj ET`,
    parts = ['%PDF-1.4\n'],
    offsets = [0];
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
  ];
  for (let i = 0; i < objects.length; i++) {
    offsets.push(Buffer.byteLength(parts.join('')));
    parts.push(`${i + 1} 0 obj\n${objects[i]}\nendobj\n`);
  }
  const xref = Buffer.byteLength(parts.join(''));
  parts.push(
    `xref\n0 6\n0000000000 65535 f \n` +
      offsets
        .slice(1)
        .map((n) => `${String(n).padStart(10, '0')} 00000 n \n`)
        .join('') +
      `trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`,
  );
  return Buffer.from(parts.join('')).toString('base64');
}
test('extract TXT and real PDF text without executing file names', async () => {
  assert.equal(
    (await extractDocument('ghi-chu.md', Buffer.from('Kiến thức tiếng Việt 🌱').toString('base64'))).content,
    'Kiến thức tiếng Việt 🌱',
  );
  assert.match((await extractDocument('invoice.pdf', pdf('Policy limit 7500000 VND'))).content, /7500000/);
  assert.match((await extractDocument('../../untrusted.pdf', pdf('Safe text'))).content, /Safe text/);
});
test('DOCX extraction uses bounded archive XML and preserves paragraphs', async () => {
  const script = `import io,zipfile,base64
b=io.BytesIO()
with zipfile.ZipFile(b,'w') as z: z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>First paragraph</w:t></w:r></w:p><w:p><w:r><w:t>Second paragraph</w:t></w:r></w:p></w:body></w:document>')
print(base64.b64encode(b.getvalue()).decode())`;
  const data = execFileSync('python3', ['-c', script], { encoding: 'utf8' }).trim();
  assert.equal((await extractDocument('policy.docx', data)).content, 'First paragraph\nSecond paragraph');
});
test('malformed, unsupported, oversized and scanned documents have explicit errors', async () => {
  await assert.rejects(() => extractDocument('file.txt', 'not base64!'), /base64/);
  await assert.rejects(() => extractDocument('file.exe', Buffer.from('hello').toString('base64')), /Chỉ hỗ trợ/);
  await assert.rejects(() => extractDocument('file.pdf', Buffer.from('not a pdf').toString('base64')), /không phải PDF/);
  await assert.rejects(() => extractDocument('file.docx', Buffer.from('not a zip').toString('base64')), /Không trích/);
  await assert.rejects(() => extractDocument('file.txt', Buffer.from([0xff, 0xfe]).toString('base64')), /UTF-8/);
  await assert.rejects(() => extractDocument('file.txt', Buffer.alloc(5 * 1024 * 1024 + 1, 65).toString('base64')), /tối đa 5 MB/);
  await assert.rejects(() => extractDocument('scan.pdf', pdf('')), /OCR/);
});
test('successful reindex replaces chunks atomically while preserving document identity', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-documents-')),
    store = new Store(dir);
  try {
    store.db.prepare('INSERT INTO knowledge VALUES(?,?,?,?,?)').run('k', 'w', 'KB', '', null);
    const original = await indexDocument(store, 'w', 'k', 'Old', 'alpha old knowledge');
    await indexDocument(store, 'w', 'k', 'New', 'beta replacement', undefined, original.id);
    assert.equal((await retrieve(store, 'w', ['k'], 'alpha')).length, 0);
    assert.equal((await retrieve(store, 'w', ['k'], 'beta'))[0].document_id, original.id);
    assert.equal(store.db.prepare('SELECT name FROM documents WHERE id=?').get(original.id)!.name, 'New');
  } finally {
    store.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
test('failed reindex keeps the last usable content and chunks', async () => {
  let fail = false;
  const mock = createServer(async (req, res) => {
    for await (const _ of req) {
    }
    if (fail) {
      res.writeHead(503);
      res.end();
    } else res.end(JSON.stringify({ data: [{ index: 0, embedding: [1, 0] }] }));
  });
  await new Promise<void>((r) => mock.listen(0, '127.0.0.1', r));
  const dir = mkdtempSync(join(tmpdir(), 'xt-reindex-')),
    store = new Store(dir);
  try {
    store.db
      .prepare('INSERT INTO models VALUES(?,?,?,?,?,?,?,?)')
      .run('embed', 'w', 'Embed', 'openai', `http://127.0.0.1:${(mock.address() as AddressInfo).port}`, 'embed', 'embedding', '');
    store.db.prepare('INSERT INTO knowledge VALUES(?,?,?,?,?)').run('k', 'w', 'KB', '', 'embed');
    const doc = await indexDocument(store, 'w', 'k', 'Original', 'preserved content');
    const chunks = store.db.prepare('SELECT id,content FROM chunks WHERE document_id=?').all(doc.id);
    fail = true;
    await assert.rejects(() => indexDocument(store, 'w', 'k', 'Changed', 'replacement content', undefined, doc.id), /503/);
    assert.deepEqual(store.db.prepare('SELECT id,content FROM chunks WHERE document_id=?').all(doc.id), chunks);
    const old = store.db.prepare('SELECT * FROM documents WHERE id=?').get(doc.id)!;
    assert.equal(old.status, 'ready');
    assert.equal(old.content, 'preserved content');
    assert.equal(old.name, 'Original');
    assert.match(String(old.error), /503/);
  } finally {
    store.close();
    await new Promise<void>((r) => mock.close(() => r()));
    rmSync(dir, { recursive: true, force: true });
  }
});
