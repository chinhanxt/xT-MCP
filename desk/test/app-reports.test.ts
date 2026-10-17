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
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
const f = (id: string, label: string, type: string, extra: Record<string, unknown> = {}) => ({ id, label, type, multiple: false, pii: false, sensitive: false, ...extra });
const models: Record<string, unknown> = {
  co_hoi: { id: 'co_hoi', label: 'Cơ hội', fields: [f('title', 'Tên', 'string'), f('customer', 'Khách hàng', 'ref'), f('stage', 'Giai đoạn', 'enum'), f('value_milli', 'Giá trị (milli)', 'decimal'), f('expected_close', 'Ngày chốt', 'date')] },
  cham_cong: { id: 'cham_cong', label: 'Chấm công', fields: [f('project', 'Dự án', 'ref'), f('date', 'Ngày làm', 'date'), f('hours_milli', 'Số giờ (milli)', 'decimal')] },
};
const workspace = {
  me: { name: 'Lan', roles: ['staff'] },
  models: [{ id: 'co_hoi', label: 'Cơ hội', primitive: 'record' }, { id: 'cham_cong', label: 'Chấm công', primitive: 'record' }],
  apps: [
    { id: 'crm', title: 'CRM', group: 'trai-nghiem-thue-bao', status: 'enabled', ui: [{ kind: 'generic' }], models: ['co_hoi'] },
    { id: 'cham-cong', title: 'Chấm công', group: 'thoi-gian-du-an', status: 'enabled', ui: [{ kind: 'generic' }], models: ['cham_cong'] },
  ],
};
const report = { model_id: 'co_hoi', model_revision: 2, as_of: '2026-10-17T08:30:00Z', scope: 'authorized_records', summary: '2 nhóm.', lineage_ref: 'explain:lin-9',
  rows: [{ keys: { stage: 'thang' }, values: { 'sum:value_milli': '5000' } }], totals: { 'sum:value_milli': '5000' }, next_cursor: null };
const explain = { view: 'explain', explanation: { outcome: 'ok', query: { model_id: 'co_hoi', replay: 'current_data', historical_snapshot: false },
  reasons: [{ source: 'aggregate', message: 'Replay query.search_records with a group\'s key filters and paginate.' }], record_refs: ['co_hoi:1'] } };

let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'describe_model', 'aggregate_records', 'get_history', 'get_record', 'search_records', 'create_record']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
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
async function choose(label: string, value: string) {
  const el = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;
  expect(el, `select ${label}`).toBeTruthy();
  await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); });
}
const base = (extra: (call: Call) => ToolResult | undefined = () => undefined) => async (call: Call): Promise<ToolResult> => {
  const special = extra(call); if (special) return special;
  if (call.name === 'inspect_workspace') return ok(workspace);
  if (call.name === 'describe_model') return ok({ model: models[String(call.arguments.model_id)] });
  if (call.name === 'search_records') return ok({ items: [], next_cursor: null });
  throw new Error(`unexpected ${call.name}`);
};
const agg = () => (call: Call) => call.name === 'aggregate_records' ? ok(report) : undefined;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('adds a "Báo cáo" tab to a generic app, scoped to the app models, and only when aggregate_records is allowed', async () => {
  await mount(base(agg()));
  await click('Mở CRM');
  expect(container.querySelector('.portal-report')).toBeNull();
  await click('Báo cáo');
  const models = Array.from(container.querySelectorAll<HTMLSelectElement>('select[aria-label="Đối tượng báo cáo"] option')).map((o) => o.textContent);
  expect(models).toEqual(['Cơ hội']);
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(base(), ['inspect_workspace', 'describe_model', 'search_records']);
  await click('Mở CRM');
  expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent === 'Báo cáo')).toBe(false);
});

it('CRM preset sums value_milli by stage; the timesheet preset buckets hours by project per week on the date field', async () => {
  await mount(base(agg()));
  await click('Mở CRM'); await click('Báo cáo');
  await click('Tổng giá trị (milli) theo giai đoạn');
  expect(calls('aggregate_records')[0].arguments).toEqual({ model_id: 'co_hoi', metrics: ['sum:value_milli'], group_by: ['stage'] });
  expect(container.querySelector('.report-result')?.textContent).toContain('5.000');
  await click('← Danh sách ứng dụng'); await click('Mở Chấm công'); await click('Báo cáo');
  await click('Tổng số giờ (milli) theo dự án theo tuần');
  expect(calls('aggregate_records')[1].arguments).toEqual({ model_id: 'cham_cong', metrics: ['sum:hours_milli'], group_by: ['project'], time_bucket: 'week', date_field: 'date' });
});

it('lets the user change the preset: metric, group, bucket and date field', async () => {
  await mount(base(agg()));
  await click('Mở Chấm công'); await click('Báo cáo');
  await choose('Nhóm theo', ''); await choose('Theo thời gian', 'month'); await choose('Trường ngày', 'date');
  await click('Xem báo cáo');
  expect(calls('aggregate_records')[0].arguments).toEqual({ model_id: 'cham_cong', metrics: ['count'], time_bucket: 'month', date_field: 'date' });
});

it('keeps the as-of and explains the source honestly: replay uses current data, not a snapshot', async () => {
  await mount(base((call) => call.name === 'aggregate_records' ? ok(report) : call.name === 'get_history' ? ok(explain) : undefined));
  await click('Mở CRM'); await click('Báo cáo'); await click('Tổng giá trị (milli) theo giai đoạn');
  expect(container.querySelector('.report-result small')?.textContent).toContain(new Date('2026-10-17T08:30:00Z').toLocaleString('vi-VN'));
  await click('Xem nguồn');
  expect(calls('get_history')[0].arguments).toEqual({ ref: 'explain:lin-9', view: 'explain' });
  const box = container.querySelector('[aria-label="Nguồn số liệu"]')!.textContent!;
  expect(box).toContain('đối chiếu theo dữ liệu hiện tại');
  expect(box).toContain('không phải ảnh chụp');
});

it('does not claim current-data replay when the server did not say so', async () => {
  const plain = { explanation: { outcome: 'ok', reasons: [], record_refs: [] } };
  await mount(base((call) => call.name === 'aggregate_records' ? ok(report) : call.name === 'get_history' ? ok(plain) : undefined));
  await click('Mở CRM'); await click('Báo cáo'); await click('Tổng giá trị (milli) theo giai đoạn'); await click('Xem nguồn');
  expect(container.querySelector('[aria-label="Nguồn số liệu"]')?.textContent).not.toContain('dữ liệu hiện tại');
});

it('shows access_denied via PortalAlert and never leaves the previous numbers visible', async () => {
  let deny = false;
  await mount(base((call) => call.name === 'aggregate_records' ? (deny ? err('access_denied', 'Không có quyền tổng hợp') : ok(report)) : undefined));
  await click('Mở CRM'); await click('Báo cáo'); await click('Tổng giá trị (milli) theo giai đoạn');
  expect(container.querySelector('.report-result')).toBeTruthy();
  deny = true;
  await click('Xem báo cáo');
  const alert = container.querySelector('.portal-report [role="alert"]')!;
  expect(alert.getAttribute('data-state')).toBe('forbidden');
  expect(alert.textContent).toContain('Không có quyền tổng hợp');
  expect(container.querySelector('.report-result')).toBeNull();
  expect(container.textContent).not.toContain('2 nhóm.');
});

it('shows a precondition error as a generic alert and clears stale numbers, including the source panel', async () => {
  let fail = false;
  await mount(base((call) => {
    if (call.name === 'aggregate_records') return fail ? err('precondition_failed', 'Mô hình đã đổi, hãy tải lại') : ok(report);
    if (call.name === 'get_history') return ok(explain);
  }));
  await click('Mở CRM'); await click('Báo cáo'); await click('Tổng giá trị (milli) theo giai đoạn'); await click('Xem nguồn');
  expect(container.querySelector('[aria-label="Nguồn số liệu"]')).toBeTruthy();
  fail = true;
  await click('Xem báo cáo');
  expect(container.querySelector('.portal-report [role="alert"]')?.getAttribute('data-state')).toBe('error');
  expect(container.querySelector('.portal-report [role="alert"]')?.textContent).toContain('Mô hình đã đổi');
  expect(container.querySelector('.report-result')).toBeNull();
  expect(container.querySelector('[aria-label="Nguồn số liệu"]')).toBeNull();
});
