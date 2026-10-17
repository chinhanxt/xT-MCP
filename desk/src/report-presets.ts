// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors

import { inSentence } from './portal-labels';

type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }

export const BUCKETS = [['day', 'Ngày'], ['week', 'Tuần'], ['month', 'Tháng'], ['quarter', 'Quý'], ['year', 'Năm']] as const;
export type Bucket = typeof BUCKETS[number][0];

export type Choice = { value: string; label: string; arg: Data };
export interface DateField { id: string; label: string }
/** Gợi ý báo cáo: mọi lựa chọn là giá trị của các ô chọn trong ReportPanel nên người dùng đổi tiếp được. */
export interface ReportPreset { id: string; label: string; metric: string; group: string; bucket: '' | Bucket; dateField: string }
export interface ReportOptions { metrics: Choice[]; groups: Choice[]; dates: DateField[]; presets: ReportPreset[] }

export const COUNT: Choice = { value: 'count', label: 'Số bản ghi', arg: { metrics: ['count'] } };
const NUMERIC = new Set(['integer', 'decimal', 'money']);
// Chỉ là thứ tự ưu tiên khi chọn gợi ý, không phải điều kiện: model nào không khớp vẫn có đủ chỉ số/nhóm trong ô chọn.
const AMOUNT = /milli|amount|hours|value|total|cost|price|revenue|qty|quantity/i;
const STATUS = /state|status|stage/i;

/** Chỉ số, trường nhóm, trường ngày và gợi ý suy ra hoàn toàn từ `describe_model.model` (không biết tên gói/model nào). */
export function reportOptions(model: unknown): ReportOptions {
  const m = obj(model);
  const fields = (Array.isArray(m.fields) ? m.fields : []).map(obj)
    .filter((f) => typeof f.id === 'string' && f.id && !f.multiple && f.pii !== true && f.sensitive !== true);
  const label = (f: Data) => String(f.label || f.id);
  const named = (Array.isArray(m.metrics) ? m.metrics : []).map(obj).filter((x) => typeof x.id === 'string')
    .map((x): Choice => ({ value: `m:${x.id}`, label: String(x.label || x.id), arg: { metric: x.id } }));
  const numeric = fields.filter((f) => NUMERIC.has(String(f.type)));
  const sums = numeric.map((f): Choice => ({ value: `s:${f.id}`, label: `Tổng ${inSentence(label(f))}`, arg: { metrics: [`sum:${f.id}`] } }));
  const groupFields = fields.filter((f) => f.type === 'enum' || f.type === 'ref');
  const groups = groupFields.map((f): Choice => ({ value: String(f.id), label: label(f), arg: { group_by: [f.id] } }));
  const dates = fields.filter((f) => f.type === 'date' || f.type === 'datetime').map((f): DateField => ({ id: String(f.id), label: label(f) }));

  const sum = numeric.find((f) => AMOUNT.test(String(f.id))) ?? numeric[0];
  const status = groupFields.find((f) => f.type === 'enum' && STATUS.test(String(f.id))) ?? groupFields.find((f) => f.type === 'enum') ?? groupFields[0];
  const dim = groupFields.find((f) => f.type === 'ref') ?? status; // chiều thứ hai (dự án, khách...) cho báo cáo theo thời gian
  const date = dates[0];
  const presets: ReportPreset[] = [];
  const add = (text: string, metric: string, group: Data | undefined, bucket: '' | Bucket = '') =>
    presets.push({ id: `${presets.length}`, label: text, metric, group: group ? String(group.id) : '', bucket, dateField: bucket && date ? date.id : '' });
  if (sum && status) add(`Tổng ${inSentence(label(sum))} theo ${inSentence(label(status))}`, `s:${sum.id}`, status);
  if (sum && date && dim) add(`Tổng ${inSentence(label(sum))} theo ${inSentence(label(dim))} theo tuần`, `s:${sum.id}`, dim, 'week');
  if (status) add(`Số bản ghi theo ${inSentence(label(status))}`, 'count', status);
  if (!presets.length) add('Số bản ghi', 'count', undefined);
  return { metrics: [COUNT, ...named, ...sums], groups, dates, presets };
}
