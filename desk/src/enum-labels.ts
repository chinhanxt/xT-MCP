// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Nhãn của giá trị enum lấy từ describe_model (options[].label), dùng chung cho danh sách, chi tiết và khóa nhóm báo cáo.
import { useEffect, useRef, useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { valueLabel } from './portal-labels';

type Data = Record<string, unknown>;
export type EnumLabels = Record<string, Record<string, string>>;
const obj = (value: unknown): Data => value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {};

/** Nhãn mỗi giá trị theo field: lấy từ `options[{value,label}]` của máy chủ; `enum_values` chỉ có giá trị thô nên không tạo nhãn. */
export function enumLabelsOf(model: unknown): EnumLabels {
  const out: EnumLabels = {};
  for (const field of (Array.isArray(obj(model).fields) ? obj(model).fields as unknown[] : []).map(obj)) {
    if (typeof field.id !== 'string') continue;
    const map: Record<string, string> = {};
    for (const option of Array.isArray(field.options) ? field.options.map(obj) : []) {
      if (typeof option.value === 'string' && typeof option.label === 'string' && option.label.trim()) map[option.value] = option.label.trim();
    }
    if (Object.keys(map).length) out[field.id] = map;
  }
  return out;
}

/** Model của một dòng kết quả: `model_id` hoặc tiền tố của `ref`. */
export function modelOf(row: unknown): string {
  const data = obj(row);
  if (typeof data.model_id === 'string' && data.model_id) return data.model_id;
  return typeof data.ref === 'string' && data.ref.includes(':') ? data.ref.split(':')[0] : '';
}

export type EnumLabel = (model: string, field: string, value: string) => string;

/** Nạp describe_model (một lần cho mỗi model) và trả hàm nhãn; chưa có nhãn máy chủ thì dùng bảng có sẵn, rồi giá trị gốc. */
export function useEnumLabels(mcp: McpConnection | null, tools: McpTool[], modelIds: string[]): EnumLabel {
  const cache = useRef(new Map<string, EnumLabels>()), asked = useRef(new Set<string>()), owner = useRef(mcp), live = useRef(true);
  const [, bump] = useState(0);
  if (owner.current !== mcp) { owner.current = mcp; cache.current = new Map(); asked.current = new Set(); }
  const canDescribe = tools.some((tool) => tool.name === 'describe_model');
  const wanted = [...new Set(modelIds.filter(Boolean))].sort().join(',');
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    if (!mcp || !canDescribe) return;
    for (const id of wanted.split(',').filter(Boolean)) {
      if (asked.current.has(id)) continue;
      asked.current.add(id);
      const mine = mcp, store = cache.current;
      void mine.client.callTool({ name: 'describe_model', arguments: { model_id: id } }).then((result) => {
        if (!live.current || owner.current !== mine) return;
        store.set(id, enumLabelsOf(obj(readToolResult(result as ToolResult)).model)); bump((n) => n + 1);
      }).catch(() => { /* chỉ mất nhãn đẹp; vẫn hiển thị giá trị gốc */ });
    }
  }, [mcp, canDescribe, wanted]);
  return (model, field, value) => cache.current.get(model)?.[field]?.[value] ?? valueLabel(field, value);
}
