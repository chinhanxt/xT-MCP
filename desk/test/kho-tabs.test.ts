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

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const item = '00000000-0000-4000-8000-0000000000a1', location = '00000000-0000-4000-8000-0000000000b1';
let root: Root | undefined, container: HTMLDivElement, mcp: McpConnection;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });
async function mount(rows: Record<string, unknown[]>) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  const tools = ['search_records', 'get_record', 'create_record', 'submit_request'].map(name => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(async (call: Call) => ok({ items: rows[String(call.arguments.model_id)] ?? [], next_cursor: null })) }, tools } as unknown as McpConnection;
  await act(async () => { root!.render(createElement(Portal, { area: 'Kho', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const tab = (name: string) => Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]')).find(t => t.textContent === name)!;
const panel = (id: string) => container.querySelector<HTMLElement>(`#kho-panel-${id}`)!;

it('splits Kho into Tồn kho / Phiếu / Danh mục / Sơ đồ 3D tabs and keeps every form mounted', async () => {
  await mount({});
  expect(Array.from(container.querySelectorAll('[role="tablist"][aria-label="Mục kho"] [role="tab"]')).map(t => t.textContent)).toEqual(['Tồn kho', 'Phiếu', 'Danh mục', 'Sơ đồ 3D']);
  expect(tab('Tồn kho').getAttribute('aria-selected')).toBe('true');
  expect(panel('stock').hidden).toBe(false);
  for (const id of ['requests', 'catalog', 'map']) expect(panel(id).hidden).toBe(true);
  expect(panel('requests').querySelector('form[aria-label="Phiếu nhập/xuất"]')).toBeTruthy();
  expect(panel('catalog').querySelector('form[aria-label="Thêm danh mục"]')).toBeTruthy();
  expect(panel('map').querySelector('iframe[title="xT WareTrack"]')).toBeTruthy();
  expect(container.textContent).toContain('Chưa có tồn kho nào bạn được xem.');
  // không có nút (role=button) nào trùng tên tab "Sơ đồ 3D"
  expect(Array.from(container.querySelectorAll('button:not([role="tab"])')).filter(b => b.textContent === 'Sơ đồ 3D')).toHaveLength(0);
  await act(async () => tab('Sơ đồ 3D').click());
  expect(panel('map').hidden).toBe(false); expect(panel('stock').hidden).toBe(true);
  await act(async () => tab('Sơ đồ 3D').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true })));
  expect(tab('Tồn kho').getAttribute('aria-selected')).toBe('true');
  await act(async () => Array.from(container.querySelectorAll('button')).find(b => b.textContent === 'Tạo phiếu')!.click());
  expect(panel('requests').hidden).toBe(false);
  expect(container.querySelector('[aria-label="Dữ liệu kho"]')?.closest('[hidden]')).toBeNull();
});

it('renders stock balances from real rows with names from the catalog and a low-stock chip', async () => {
  await mount({
    stock_item: [{ ref: `stock_item:${item}`, title: 'SKU-A4', values: { sku: 'SKU-A4', name: 'Giấy A4', unit: 'ram', reorder_level_milli: 10000 } }],
    warehouse_location: [{ ref: `warehouse_location:${location}`, title: 'A-02', values: { code: 'A-02', name: 'Kệ A-02' } }],
    stock_balance: [{ ref: 'stock_balance:1', values: { item, location, quantity_milli: 8000 } }, { ref: 'stock_balance:2', values: { item: { name: 'Bút bi' }, location: { name: 'Kệ B' }, quantity_milli: 120500 } }],
  });
  const rows = Array.from(panel('stock').querySelectorAll('tbody tr')).map(r => Array.from(r.querySelectorAll('td')).map(td => td.textContent));
  expect(rows[0]).toEqual(['Giấy A4', 'SKU-A4', 'Kệ A-02', '8 ram', '10 ram', 'Sắp hết']);
  expect(rows[1]).toEqual(['Bút bi', '—', 'Kệ B', '120,5', '—', '']);
  const calls = vi.mocked(mcp.client.callTool).mock.calls.map(c => c[0] as Call);
  expect(calls).toContainEqual({ name: 'search_records', arguments: { model_id: 'stock_balance', limit: 100, fields: ['item', 'location', 'quantity_milli'] } });
  const before = calls.length;
  await act(async () => Array.from(panel('stock').querySelectorAll('button')).find(b => b.textContent === 'Làm mới tồn kho')!.click());
  expect(vi.mocked(mcp.client.callTool).mock.calls.slice(before).map(c => (c[0] as Call).arguments.model_id)).toEqual(['stock_item', 'warehouse_location', 'stock_balance']);
});
