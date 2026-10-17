// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
import { enumLabelsOf, modelOf } from '../src/enum-labels';
import { inSentence, recordTitle } from '../src/portal-labels';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const model = { id: 'advance_request', label: 'Tạm ứng', fields: [
  { id: 'priority', label: 'Mức ưu tiên', type: 'enum', options: [{ value: 'low', label: 'Thấp' }, { value: 'high', label: 'Cao' }] },
  { id: 'stage', label: 'Giai đoạn', type: 'enum', options: [{ value: 'lead', label: 'Tiềm năng' }] }] };
const row = { ref: 'advance_request:1', title: 'Tạm ứng TU-1', model_id: 'advance_request', values: { priority: 'high', stage: 'lead', other: 'raw' } };
let root: Root | undefined, container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('pure helpers: enum labels from options, model of a row, sentence-case labels, record titles', () => {
  expect(enumLabelsOf(model)).toEqual({ priority: { low: 'Thấp', high: 'Cao' }, stage: { lead: 'Tiềm năng' } });
  expect(enumLabelsOf({ fields: [{ id: 'k', type: 'enum', enum_values: ['a'] }] })).toEqual({});
  expect(modelOf({ model_id: 'x', ref: 'y:1' })).toBe('x'); expect(modelOf({ ref: 'y:1' })).toBe('y'); expect(modelOf(null)).toBe('');
  expect(inSentence('Số tiền')).toBe('số tiền'); expect(inSentence('SLA')).toBe('SLA'); expect(inSentence('')).toBe('');
  expect(recordTitle({ title: ' TU-1 ' })).toBe('TU-1'); expect(recordTitle({ values: { name: 'N' } })).toBe('N'); expect(recordTitle({}, 'Dự phòng')).toBe('Dự phòng');
});

it('shows describe_model enum labels in portal lists and record detail, leaving unknown values raw', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  const tools = ['inspect_workspace', 'describe_model', 'search_records', 'get_record'].map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const mcp = { client: { callTool: vi.fn(async (call: Call) => {
    if (call.name === 'inspect_workspace') return ok({ me: { name: 'Lan' }, models: [{ id: 'advance_request', label: 'Tạm ứng', primitive: 'request' }] });
    if (call.name === 'describe_model') return ok({ model });
    if (call.name === 'search_records') return ok({ items: [row], next_cursor: null });
    return ok({ record: row });
  }) }, tools } as unknown as McpConnection;
  await act(async () => { root!.render(createElement(Portal, { area: 'D', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  const card = () => container.querySelector('.portal-results')!.textContent!;
  expect(card()).toContain('Cao'); expect(card()).toContain('Tiềm năng'); expect(card()).toContain('raw'); expect(card()).not.toMatch(/\bhigh\b|\blead\b/);
  const described = () => vi.mocked(mcp.client.callTool).mock.calls.filter((c) => (c[0] as Call).name === 'describe_model').length, before = described();
  await act(async () => { Array.from(container.querySelectorAll('.portal-results button')).find((b) => b.textContent === 'Mở Tạm ứng TU-1')!.dispatchEvent(new MouseEvent('click', { bubbles: true })); });
  expect(card()).toContain('Cao'); expect(card()).not.toMatch(/\bhigh\b|\blead\b/);
  expect(described()).toBe(before); // mỗi model chỉ nạp một lần
});
