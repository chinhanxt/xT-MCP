// SPDX-License-Identifier: Apache-2.0
import type { ToolResult } from './types';
type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }

/** Lỗi nghiệp vụ từ tool: giữ mã lỗi để UI phân biệt thiếu quyền, hết phiên và lỗi thường. */
export class ToolError extends Error {
  constructor(message: string, readonly code: string, readonly requestId: string) { super(message); this.name = 'ToolError'; }
}

function requestIdOf(result: ToolResult, value: Data, error: Data): string {
  const meta = obj(result._meta), details = obj(error.details);
  for (const source of [error, details, value, meta]) {
    for (const key of ['request_id', 'x-request-id', 'x_request_id']) {
      const found = source[key];
      if (typeof found === 'string' && found.trim()) return found.trim().slice(0, 100);
    }
  }
  return '';
}

export function readToolResult(result: ToolResult): Data {
  let raw = result.structuredContent;
  if (raw === undefined && result.content?.[0]?.type === 'text') {
    try { raw = JSON.parse(result.content[0].text || '{}'); }
    catch { raw = { message: result.content[0].text }; }
  }
  const value = obj(raw);
  if (result.isError || value.error) {
    const error = obj(value.error);
    const message = String(error.message || value.message || error.code || 'Thao tác bị từ chối');
    const requestId = requestIdOf(result, value, error);
    throw new ToolError(requestId ? `${message} (mã yêu cầu: ${requestId})` : message, String(error.code || ''), requestId);
  }
  return obj(value.data || value);
}

export function parseMilli(value: string, allowZero = false): number | null {
  if (!/^\d+(?:\.\d{1,3})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  const milli = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0'));
  return milli >= (allowZero ? 0n : 1n) && milli <= 9_000_000_000_000_000n ? Number(milli) : null;
}
