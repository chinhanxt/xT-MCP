// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ToolResult } from './types';

type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }

export interface FieldSpec {
  id: string; label: string; type: string; required: boolean; multiple: boolean;
  enumValues: string[]; enumLabels: Record<string, string>; description: string; maxLength?: number; refModel?: string;
}
const options = (f: Data): Data[] => (Array.isArray(f.options) ? f.options.map(obj).filter((o) => typeof o.value === 'string') : []);

/** Đọc `describe_model.model.fields`; không đoán field nào ngoài những gì server khai báo. */
export function readFields(model: unknown): FieldSpec[] {
  const fields = obj(model).fields;
  if (!Array.isArray(fields)) return [];
  return fields.map(obj).filter((f) => typeof f.id === 'string').map((f) => ({
    id: String(f.id), label: String(f.label || f.id), type: String(f.type || 'string'),
    required: f.required === true, multiple: f.multiple === true,
    enumValues: Array.isArray(f.enum_values) ? f.enum_values.map(String) : options(f).map((o) => String(o.value)),
    enumLabels: Object.fromEntries(options(f).filter((o) => typeof o.label === 'string' && o.label.trim()).map((o) => [String(o.value), String(o.label).trim()])),
    description: typeof f.description === 'string' ? f.description : '',
    maxLength: typeof f.max_length === 'number' ? f.max_length : undefined,
    refModel: typeof f.ref_model === 'string' && f.ref_model ? f.ref_model : typeof f.ref === 'string' && f.ref ? f.ref : undefined,
  }));
}

const SIMPLE = new Set(['string', 'text', 'integer', 'decimal', 'money', 'boolean', 'date', 'datetime', 'enum']);
/** ref có model đích chọn được bằng search_records (`canPick`); person/file hoặc nhiều giá trị chưa có bộ chọn: báo rõ, không giả lập. */
export const isSupported = (f: FieldSpec, canPick = true) => !f.multiple && (SIMPLE.has(f.type) || (canPick && f.type === 'ref' && !!f.refModel));

export function validate(fields: FieldSpec[], raw: Record<string, string>): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of fields.filter((f) => isSupported(f))) {
    const v = (raw[f.id] ?? '').trim();
    if (!v && f.type !== 'boolean') { if (f.required) errors[f.id] = 'Trường bắt buộc'; continue; }
    if (f.type === 'integer' && !/^-?\d+$/.test(v)) errors[f.id] = 'Phải là số nguyên';
    else if ((f.type === 'decimal' || f.type === 'money') && !/^-?\d+(?:\.\d+)?$/.test(v)) errors[f.id] = 'Phải là số, dùng dấu chấm cho phần thập phân';
    else if (f.type === 'enum' && v && !f.enumValues.includes(v)) errors[f.id] = 'Giá trị không hợp lệ';
  }
  return errors;
}

/** Số tiền/thập phân gửi dưới dạng chuỗi (hợp đồng), không qua Number. */
export function buildValues(fields: FieldSpec[], raw: Record<string, string>): Data {
  const values: Data = {};
  for (const f of fields.filter((f) => isSupported(f))) {
    const v = (raw[f.id] ?? '').trim();
    if (f.type === 'boolean') { if (raw[f.id] === 'true' || f.required) values[f.id] = raw[f.id] === 'true'; continue; }
    if (!v) continue;
    values[f.id] = f.type === 'integer' ? Number(v) : f.type === 'datetime' ? new Date(v).toISOString() : v;
  }
  return values;
}

function envelope(result: ToolResult): Data {
  let raw: unknown = result.structuredContent;
  if (raw === undefined && result.content?.[0]?.type === 'text') { try { raw = JSON.parse(result.content[0].text || '{}'); } catch { return {}; } }
  return obj(raw);
}
export function confirmUrlOf(result: ToolResult): unknown { return obj(obj(envelope(result).error).details).confirm_url; }

/** Lỗi theo field từ envelope lỗi của create_record: details.violations[] hoặc details.fields[]. */
export function fieldErrorsOf(result: ToolResult): Record<string, string> {
  const error = obj(envelope(result).error), details = obj(error.details);
  const out: Record<string, string> = {};
  for (const list of [details.violations, details.fields]) {
    if (!Array.isArray(list)) continue;
    for (const item of list.map(obj)) {
      if (typeof item.field === 'string') out[item.field] = String(item.message || error.message || 'Giá trị không hợp lệ');
    }
  }
  return out;
}
