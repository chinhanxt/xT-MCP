// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';
import { PortalPending } from '../src/PortalBoundary';
import { areaNames } from '../src/portal-nav';
import { actionLabel, fieldLabel, groupDigits, primitiveLabel, refKind, valueLabel } from '../src/portal-labels';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
let root: Root | undefined;
let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; });

it('maps known technical keys and values to Vietnamese and leaves unknown ones untouched', () => {
  expect(valueLabel('state', 'in_review')).toBe('Đang duyệt');
  expect(valueLabel('sla_status', 'breached')).toBe('Quá hạn');
  expect(valueLabel('state', 'khong_biet')).toBe('khong_biet');
  expect(valueLabel('due_at', '2026-10-20T09:00:00Z')).toContain('2026');
  expect(valueLabel('code', 'TU-0012')).toBe('TU-0012');
  expect(fieldLabel('amount')).toBe('Số tiền');
  expect(fieldLabel('custom_field')).toBe('custom_field');
  expect(primitiveLabel('request')).toBe('Yêu cầu');
  expect(Object.keys(areaNames)).toEqual(['H', 'P', 'D', 'I', 'T3', 'Desk']);
});
it('labels money, plain dates, record refs and history actions without leaking technical keys', () => {
  expect(valueLabel('amount', '1500000')).toBe('1.500.000');
  expect(valueLabel('amount', 'n/a')).toBe('n/a');
  expect(valueLabel('effective_on', '2026-01-01')).toBe('01/01/2026');
  expect(refKind('advance_request:0000')).toBe('phiếu tạm ứng');
  expect(refKind('lifecycle_task:0000')).toBe('bước quy trình');
  expect(refKind('unknown_model:1')).toBe('bản ghi');
  expect(actionLabel('submit')).toBe('Gửi duyệt');
  expect(actionLabel('custom_action')).toBe('custom_action');
});
it('groups digits vi-VN style without going through Number', () => {
  expect(groupDigits('1500000')).toBe('1.500.000');
  expect(groupDigits('1500000.50')).toBe('1.500.000,50');
  expect(groupDigits('-9007199254740993')).toBe('-9.007.199.254.740.993');
  expect(groupDigits('2026-W40')).toBe('2026-W40');
  expect(groupDigits('0900000001')).toBe('0.900.000.001'); // chỉ dùng cho cột chỉ số, không cho mã/điện thoại
});
it('shows an expired connection with the session-expired title and hint, not a bare error', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  await act(async () => { root!.render(createElement(PortalPending, { label: 'Con người', error: 'Máy chủ xT từ chối phiên đăng nhập hiện tại.', kind: 'expired' })); });
  expect(container.querySelector('h2')?.textContent).toBe('Con người');
  const alert = container.querySelector('.portal > [role="alert"]');
  expect(alert?.getAttribute('data-state')).toBe('expired');
  expect(alert?.textContent).toContain('Phiên đã hết hạn');
  expect(alert?.textContent).toContain('đăng nhập lại');
});
it('renders the inbox result and its decision actions above the request form, and translates enum values', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const request = { ref: 'advance_request:one', title: 'Tạm ứng TU-1', state: 'in_review', revision: 2, values: { code: 'TU-1', amount: '1500000' }, model_id: 'advance_request' };
  const tools = ['inspect_workspace', 'get_inbox', 'get_record', 'describe_model', 'create_record', 'submit_request', 'decide_request'].map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const mcp = { tools, client: { callTool: vi.fn(async ({ name }: { name: string }) => {
    if (name === 'inspect_workspace') return ok({ me: { name: 'Mai' }, models: [{ id: 'advance_request', label: 'Tạm ứng', primitive: 'request' }], processes: [] });
    if (name === 'get_inbox') return ok({ counts: { approval: 1 }, items: [{ ref: request.ref, title: request.title, kind: 'approval', state: 'in_review', sla_status: 'on_track' }], next_cursor: null });
    if (name === 'get_record') return ok({ record: request });
    if (name === 'describe_model') return ok({ model: { id: 'advance_request', fields: [{ id: 'amount', label: 'Số tiền', type: 'money', required: true }] } });
    throw new Error(`Unexpected ${name}`);
  }) } } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'P', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  expect(container.textContent).toContain('Chờ duyệt: 1');
  const open = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Mở Tạm ứng TU-1')!;
  await act(async () => open.click());
  const text = container.textContent!;
  expect(text).toContain('Đang duyệt');
  expect(text).toContain('Số tiền'); // nhãn tiếng Việt thay cho khóa "amount"
  expect(text).not.toContain('model_id');
  const results = container.querySelector('.portal-results')!, form = container.querySelector('.portal-request')!;
  expect(results.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  const approve = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Duyệt')!;
  expect(approve.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});
