// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { HttpError } from './validation.ts';
import { readLines } from './streams.ts';
export function httpNodeUrl(value: string): URL {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new HttpError(400, 'URL HTTP node không hợp lệ.');
  }
  if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.host.includes('{') || url.host.includes('}'))
    throw new HttpError(400, 'HTTP node cần origin HTTP(S) cố định và không chứa tài khoản.');
  return url;
}
export async function runHttpNode(
  template: string,
  rendered: string,
  method: 'GET' | 'POST',
  body: string,
  signal?: AbortSignal,
): Promise<string> {
  const original = httpNodeUrl(template),
    target = httpNodeUrl(rendered);
  if (original.origin !== target.origin) throw new HttpError(400, 'Đầu vào không được thay đổi origin của HTTP node.');
  if (method === 'POST') {
    try {
      JSON.parse(body || '{}');
    } catch {
      throw new HttpError(400, 'HTTP POST body cần JSON hợp lệ sau khi thay biến.');
    }
  }
  try {
    const response = await fetch(target, {
      method,
      redirect: 'error',
      body: method === 'POST' ? body || '{}' : undefined,
      headers: method === 'POST' ? { 'Content-Type': 'application/json' } : {},
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new HttpError(502, `HTTP node trả về ${response.status}.`);
    }
    if (!response.body) return '';
    let output = '';
    for await (const line of readLines(response.body, 200000)) output += line + '\n';
    return output.trimEnd();
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(
      signal?.aborted ? 499 : 502,
      signal?.aborted ? 'Đã dừng HTTP node.' : 'HTTP node thất bại hoặc vượt giới hạn 15 giây / 200 KB.',
    );
  }
}
