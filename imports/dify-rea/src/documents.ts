// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { HttpError, text } from './validation.ts';
const exec = promisify(execFile);
export async function extractDocument(nameValue: unknown, encoded: unknown): Promise<{ name: string; content: string }> {
  const name = text(nameValue, 'Tên tài liệu', 200),
    base64 = text(encoded, 'File', 7 * 1024 * 1024);
  const bytes = Buffer.from(base64, 'base64');
  if (bytes.length > 5 * 1024 * 1024) throw new HttpError(413, 'File tối đa 5 MB.');
  if (bytes.toString('base64') !== base64) throw new HttpError(400, 'File base64 không hợp lệ.');
  const extension = name.split('.').at(-1)?.toLowerCase();
  let content = '';
  if (['txt', 'md', 'csv'].includes(extension ?? '')) {
    try {
      content = new TextDecoder('utf-8', { fatal: true }).decode(bytes);
    } catch {
      throw new HttpError(400, 'File văn bản cần UTF-8.');
    }
  } else if (extension === 'pdf' || extension === 'docx') {
    const directory = await mkdtemp(join(tmpdir(), 'xt-extract-'));
    try {
      const path = join(directory, 'document.' + extension);
      await writeFile(path, bytes, { mode: 0o600 });
      if (extension === 'pdf') {
        if (!bytes.subarray(0, 5).equals(Buffer.from('%PDF-'))) throw new HttpError(400, 'Nội dung file không phải PDF.');
        const result = await exec('pdftotext', ['-layout', '-enc', 'UTF-8', path, '-'], { timeout: 20000, maxBuffer: 4 * 1024 * 1024 });
        content = result.stdout;
      } else {
        const script = `import sys,zipfile,xml.etree.ElementTree as E
with zipfile.ZipFile(sys.argv[1]) as z:
 i=z.getinfo('word/document.xml')
 if i.file_size>10000000: raise ValueError('Document XML too large')
 data=z.read(i)
 if b'<!DOCTYPE' in data or b'<!ENTITY' in data: raise ValueError('XML declarations unsupported')
 root=E.fromstring(data)
 ns='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
 print('\\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p')))
`;
        content = (await exec('python3', ['-c', script, path], { timeout: 20000, maxBuffer: 4 * 1024 * 1024 })).stdout;
      }
    } catch (error) {
      if (error instanceof HttpError) throw error;
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new HttpError(503, 'Cần cài pdftotext cho PDF hoặc Python 3 cho DOCX.');
      throw new HttpError(400, 'Không trích được file; cần PDF có chữ hoặc DOCX hợp lệ, trong giới hạn kích thước.');
    } finally {
      await rm(directory, { recursive: true, force: true });
    }
  } else throw new HttpError(400, 'Chỉ hỗ trợ TXT, Markdown, CSV, PDF và DOCX.');
  if (!content.trim()) throw new HttpError(400, 'Không tìm thấy văn bản. PDF ảnh scan cần OCR trước khi nhập.');
  return { name, content: text(content.trim(), 'Nội dung trích xuất', 1000000) };
}
