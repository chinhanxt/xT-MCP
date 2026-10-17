// SPDX-License-Identifier: Apache-2.0
import type { ToolResult } from './types';
type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
export function readToolResult(result: ToolResult): Data {
  let raw = result.structuredContent;
  if (raw === undefined && result.content?.[0]?.type === 'text') {
    try { raw = JSON.parse(result.content[0].text || '{}'); }
    catch { raw = { message: result.content[0].text }; }
  }
  const value = obj(raw);
  if (result.isError || value.error) {
    const error = obj(value.error);
    throw new Error(String(error.message || value.message || error.code || 'Thao tác bị từ chối'));
  }
  return obj(value.data || value);
}

export function parseMilli(value: string, allowZero = false): number | null {
  if (!/^\d+(?:\.\d{1,3})?$/.test(value)) return null;
  const [whole, fraction = ''] = value.split('.');
  const milli = BigInt(whole) * 1000n + BigInt(fraction.padEnd(3, '0'));
  return milli >= (allowZero ? 0n : 1n) && milli <= 9_000_000_000_000_000n ? Number(milli) : null;
}
