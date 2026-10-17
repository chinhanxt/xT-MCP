// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Knowledge documents in integrated mode are derived from xT records read with the caller's capability.
import type { XtTools } from './xt-tools.ts';
import { HttpError } from './validation.ts';

const refPattern = /^(xrn:[a-z0-9][a-z0-9_-]{0,63}:)?[a-z][a-z0-9_]{0,62}:[a-z0-9][a-z0-9_-]{0,63}$/;
const maxCharacters = 1000000, maxPages = 50;
type Envelope = { ok?: boolean; error?: { code?: string }; data?: { record?: { title?: unknown; revision?: unknown };
  text?: { content?: unknown; next_text_cursor?: unknown } } };

export function xtRef(value: unknown): string {
  if (typeof value !== 'string' || !refPattern.test(value)) throw new HttpError(400, 'Ref xT không hợp lệ.');
  return value;
}

export function envelopeError(value: Envelope): HttpError {
  const code = value.error?.code;
  if (code === 'not_found') return new HttpError(404, 'Không tìm thấy bản ghi xT hoặc bạn không có quyền xem.');
  if (code === 'access_denied') return new HttpError(403, 'xT từ chối quyền đọc bản ghi.');
  return new HttpError(502, `xT trả lỗi${code ? ': ' + code : ''}.`);
}

export async function fetchXtDocument(tools: XtTools | undefined, ref: string, signal?: AbortSignal) {
  if (!tools) throw new HttpError(503, 'Kết nối công cụ xT chưa sẵn sàng.');
  let content = '', cursor: string | undefined, name = '', revision: number | undefined;
  for (let page = 0; page < maxPages; page++) {
    const value = (await tools.call('get_record', { ref, include: ['text'], ...(cursor ? { text_cursor: cursor } : {}) }, signal)) as Envelope;
    if (value?.ok !== true) throw envelopeError(value ?? {});
    const record = value.data?.record, text = value.data?.text;
    if (!record || typeof record.revision !== 'number' || !Number.isInteger(record.revision) || typeof record.title !== 'string')
      throw new HttpError(502, 'Bản ghi xT thiếu revision hoặc tiêu đề.');
    if (revision !== undefined && revision !== record.revision) throw new HttpError(409, 'Bản ghi xT vừa thay đổi; thử lại.');
    if (!text || typeof text.content !== 'string') throw new HttpError(422, 'Bản ghi xT không có nội dung văn bản.');
    revision = record.revision;
    name = record.title.slice(0, 200) || ref;
    content += text.content;
    if (content.length > maxCharacters) throw new HttpError(413, 'Văn bản xT vượt quá 1 triệu ký tự.');
    if (text.next_text_cursor === null || text.next_text_cursor === undefined) return { name, content, revision: revision! };
    if (typeof text.next_text_cursor !== 'string') throw new HttpError(502, 'Cursor xT không hợp lệ.');
    cursor = text.next_text_cursor;
  }
  throw new HttpError(413, 'Văn bản xT có quá nhiều trang.');
}

export type SourceCheck = (ref: string, signal?: AbortSignal) => Promise<number | null>;

// Current revision visible to the caller, or null when xT says the record is gone or not readable.
// Transport failures throw so a run fails visibly instead of answering without sources.
// Integrated mode always gets a check (retrieve uses it to drop documents without an xT source);
// without a core connection that check fails closed. Standalone mode gets none.
export function sourceCheck(tools: XtTools | undefined, integrated = tools !== undefined): SourceCheck | undefined {
  if (!tools)
    return integrated
      ? async () => {
          throw new HttpError(503, 'Không kiểm tra nguồn xT được cho tài liệu này.');
        }
      : undefined;
  const seen = new Map<string, Promise<number | null>>();
  return (ref, signal) => {
    let pending = seen.get(ref);
    if (!pending) {
      pending = (async () => {
        const value = (await tools.call('get_record', { ref }, signal)) as Envelope;
        if (value?.ok === true) {
          const revision = value.data?.record?.revision;
          if (typeof revision !== 'number' || !Number.isInteger(revision)) throw new HttpError(502, 'Bản ghi xT thiếu revision.');
          return revision;
        }
        if (value?.error?.code === 'not_found' || value?.error?.code === 'access_denied') return null;
        throw envelopeError(value ?? {});
      })();
      seen.set(ref, pending);
    }
    return pending;
  };
}
