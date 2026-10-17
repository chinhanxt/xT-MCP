// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { GenerationOptions, InputField } from './contracts.ts';
import { defaultGeneration } from './contracts.ts';
import { HttpError, object, text, choice, strings } from './validation.ts';
export function generationInput(value: unknown): GenerationOptions {
  if (value === undefined) return defaultGeneration();
  const v = object(value),
    temperature = Number(v.temperature),
    tokens = Number(v.max_tokens),
    context = Number(v.context_messages);
  if (
    !Number.isFinite(temperature) ||
    temperature < 0 ||
    temperature > 2 ||
    !Number.isInteger(tokens) ||
    tokens < 1 ||
    tokens > 16384 ||
    !Number.isInteger(context) ||
    context < 1 ||
    context > 100 ||
    typeof v.json_mode !== 'boolean'
  )
    throw new HttpError(400, 'Tham số model không hợp lệ.');
  return { temperature, max_tokens: tokens, context_messages: context, json_mode: v.json_mode };
}
export function fieldDefinitions(value: unknown): InputField[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || value.length > 20) throw new HttpError(400, 'Tối đa 20 trường đầu vào.');
  const fields = value.map((raw) => {
    const v = object(raw),
      name = text(v.name, 'Tên biến', 64);
    if (!/^[A-Za-z][\w-]*$/.test(name) || name === 'input' || ['constructor', 'prototype'].includes(name))
      throw new HttpError(400, 'Tên biến đầu vào không hợp lệ hoặc trùng input.');
    const type = choice(v.type, ['text', 'number', 'boolean', 'select'] as const, 'Kiểu đầu vào');
    if (typeof v.required !== 'boolean') throw new HttpError(400, 'required cần boolean.');
    const field: InputField = { name, label: text(v.label, 'Nhãn', 100), type, required: v.required };
    if (type === 'select') {
      field.options = strings(v.options, 'Lựa chọn', 30);
      if (!field.options.length || new Set(field.options).size !== field.options.length)
        throw new HttpError(400, 'Lựa chọn trống hoặc bị trùng.');
    }
    if (v.default !== undefined) field.default = fieldValue(field, v.default);
    return field;
  });
  if (new Set(fields.map((f) => f.name)).size !== fields.length) throw new HttpError(400, 'Tên đầu vào bị trùng.');
  return fields;
}
function fieldValue(field: InputField, value: unknown): string | number | boolean {
  if (field.type === 'number') {
    if (typeof value !== 'number' || !Number.isFinite(value)) throw new HttpError(400, `${field.label} cần một số.`);
    return value;
  }
  if (field.type === 'boolean') {
    if (typeof value !== 'boolean') throw new HttpError(400, `${field.label} cần boolean.`);
    return value;
  }
  const result = text(value, field.label, 20000, field.required);
  if (field.type === 'select' && !field.options?.includes(result)) throw new HttpError(400, `${field.label} không nằm trong lựa chọn.`);
  return result;
}
export function runInputs(fields: InputField[], value: unknown): Record<string, string | number | boolean> {
  const v = value === undefined ? {} : object(value),
    result: Record<string, string | number | boolean> = {};
  if (Object.keys(v).some((key) => !fields.some((f) => f.name === key))) throw new HttpError(400, 'Có trường đầu vào không được khai báo.');
  for (const field of fields) {
    const raw = v[field.name] ?? field.default;
    if (raw === undefined) {
      if (field.required) throw new HttpError(400, `Thiếu ${field.label}.`);
      result[field.name] = '';
    } else result[field.name] = fieldValue(field, raw);
  }
  return result;
}
