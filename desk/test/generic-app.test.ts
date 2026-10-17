// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Tier3 } from '../src/Tier3';

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
// Fixture theo hợp đồng dự kiến của agent nền tảng (xem tier3.ts).
const workspace = {
  me: { name: 'Lan', roles: ['staff'] },
  models: [
    { id: 'cham_cong', label: 'Chấm công', primitive: 'record' },
    { id: 'de_nghi_giam_gia', label: 'Đề nghị giảm giá', primitive: 'request' },
    { id: 'khach_hang', label: 'Khách hàng', primitive: 'record' },
    { id: 'khac', label: 'Model không thuộc ứng dụng', primitive: 'record' },
  ],
  apps: [
    { id: 'cham-cong', title: 'Chấm công', pack: 'cham-cong', group: 'thoi-gian-du-an', status: 'enabled', ui: [{ kind: 'generic' }], models: ['cham_cong'] },
    { id: 'crm', title: 'CRM nhẹ', group: 'trai-nghiem-thue-bao', status: 'enabled', ui: [{ kind: 'generic' }], models: ['khach_hang', 'de_nghi_giam_gia', 'khong_thay'] },
    { id: 'trong', title: 'Chưa khai báo', group: 'tai-san-khong-gian', status: 'enabled', ui: [{ kind: 'generic' }] },
    { id: 'an', title: 'Không quyền', group: 'tai-san-khong-gian', status: 'enabled', ui: [{ kind: 'generic' }], models: ['khong_thay'] },
  ],
};
let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = ['inspect_workspace', 'describe_model', 'search_records', 'get_record', 'create_record', 'submit_request'].map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Tier3, { mcp, tools, renderWarehouse: () => null })); });
}
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0] as Call).filter((c) => c.name === name);
async function click(label: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === label);
  expect(button, `button ${label}`).toBeTruthy();
  await act(async () => button!.click());
}
async function fill(id: string, value: string) {
  const el = container.querySelector<HTMLInputElement>(`#req-${id}`)!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(el, value); el.dispatchEvent(new Event('input', { bubbles: true })); });
}
const send = () => act(async () => { container.querySelector('.portal-request form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
const base = (extra: (call: Call) => ToolResult | undefined) => async (call: Call): Promise<ToolResult> => {
  const special = extra(call); if (special) return special;
  if (call.name === 'inspect_workspace') return ok(workspace);
  if (call.name === 'describe_model') return ok({ model: { id: call.arguments.model_id, fields: [{ id: 'note', label: 'Ghi chú', type: 'string', required: true }] } });
  if (call.name === 'search_records') return ok({ items: [], next_cursor: null });
  throw new Error(`unexpected ${call.name}`);
};
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('lists records of the app model only and opens a record through get_record', async () => {
  await mount(base((call) => {
    if (call.name === 'search_records') return ok({ items: [{ ref: 'cham_cong:1', title: 'Ca sáng 17/10' }], next_cursor: null });
    if (call.name === 'get_record') return ok({ record: { ref: 'cham_cong:1', title: 'Ca sáng 17/10', values: { gio: '8' } } });
  }));
  await click('Mở Chấm công');
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="Loại bản ghi"]')!;
  expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Chấm công']);
  expect(calls('search_records')[0].arguments).toEqual({ model_id: 'cham_cong', limit: 20 });
  expect(container.textContent).toContain('Ca sáng 17/10');
  await click('Mở Ca sáng 17/10');
  expect(calls('get_record')[0].arguments).toEqual({ ref: 'cham_cong:1' });
  expect(container.querySelector('[aria-label="Chi tiết bản ghi"]')?.textContent).toContain('gio');
});

it('creates a plain record with create_record only (no submit_request)', async () => {
  await mount(base((call) => call.name === 'create_record' ? ok({ record: { ref: 'cham_cong:9' } }) : undefined));
  await click('Mở Chấm công');
  expect(container.querySelector('.portal-request h3')?.textContent).toBe('Tạo chấm công');
  await fill('note', 'Ca chiều'); await send();
  expect(calls('create_record')[0].arguments).toEqual({ model_id: 'cham_cong', values: { note: 'Ca chiều' }, idempotency_key: expect.any(String) });
  expect(calls('submit_request')).toHaveLength(0);
  expect(container.querySelector('[data-testid="request-done"]')?.textContent).toBe('Đã tạo Chấm công.');
});

it('keeps the approval flow for request models and drops models the user cannot see', async () => {
  await mount(base((call) => {
    if (call.name === 'create_record') return ok({ record: { ref: 'de_nghi_giam_gia:3', revision: 1 } });
    if (call.name === 'submit_request') return ok({ request: { ref: 'de_nghi_giam_gia:3', state: 'in_review', revision: 2 } });
  }));
  await click('Mở CRM nhẹ');
  const options = Array.from(container.querySelectorAll<HTMLOptionElement>('select[aria-label="Loại bản ghi"] option')).map((o) => o.textContent);
  expect(options).toEqual(['Đề nghị giảm giá', 'Khách hàng']);
  const model = container.querySelector<HTMLSelectElement>('#req-model')!;
  await act(async () => { model.value = 'de_nghi_giam_gia'; model.dispatchEvent(new Event('change', { bubbles: true })); });
  await fill('note', 'Khách lớn'); await send();
  expect(calls('create_record')[0].arguments.model_id).toBe('de_nghi_giam_gia');
  expect(calls('submit_request')[0].arguments).toMatchObject({ ref: 'de_nghi_giam_gia:3', expected_revision: 1 });
  expect(container.querySelector('[data-testid="request-done"]')?.textContent).toBe('Đã gửi duyệt Đề nghị giảm giá.');
});

it('explains honestly when the manifest lists no models or none are visible', async () => {
  await mount(base(() => undefined));
  await click('Mở Chưa khai báo');
  expect(container.textContent).toContain('Manifest của ứng dụng chưa khai báo model');
  expect(calls('search_records')).toHaveLength(0);
  await click('← Danh sách ứng dụng');
  await click('Mở Không quyền');
  expect(container.textContent).toContain('chưa được cấp quyền xem model nào');
});
