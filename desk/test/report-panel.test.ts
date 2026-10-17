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
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
const workspace = { me: { name: 'Lan' }, models: [{ id: 'stock_level', label: 'Tồn kho', primitive: 'record' }, { id: 'advance_request', label: 'Tạm ứng', primitive: 'request' }] };
const stockModel = { id: 'stock_level', label: 'Tồn kho', fields: [
  { id: 'warehouse', label: 'Kho', type: 'ref', ref_model: 'warehouse' }, { id: 'zone', label: 'Khu', type: 'enum', enum_values: ['a', 'b'] },
  { id: 'quantity', label: 'Số lượng', type: 'decimal' }, { id: 'note', label: 'Ghi chú', type: 'text' }], metrics: [{ id: 'below_reorder', label: 'Dưới ngưỡng', op: 'count', field: null }] };
const report = { model_id: 'stock_level', model_revision: 3, as_of: '2026-10-17T08:30:00Z', scope: 'authorized_records', summary: '5 dòng tồn.', lineage_ref: 'explain:lin-1',
  rows: [{ keys: { zone: 'a' }, values: { count: 3 } }, { keys: { zone: 'b' }, values: { count: 2 } }], totals: { count: 5 }, next_cursor: null };
let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'describe_model', 'aggregate_records', 'get_history', 'get_record', 'search_records']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'D', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0] as Call).filter((c) => c.name === name);
async function choose(label: string, value: string) {
  const el = container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;
  await act(async () => { el.value = value; el.dispatchEvent(new Event('change', { bubbles: true })); });
}
async function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === text);
  expect(button, `button ${text}`).toBeTruthy();
  await act(async () => button!.click());
}
const base = (extra: (call: Call) => ToolResult | undefined) => async (call: Call): Promise<ToolResult> => {
  const special = extra(call); if (special) return special;
  if (call.name === 'inspect_workspace') return ok(workspace);
  if (call.name === 'describe_model') return ok({ model: stockModel });
  throw new Error(`unexpected ${call.name}`);
};
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('offers metrics and group-by from describe_model, renders rows/totals with as-of, scope and revision', async () => {
  await mount(base((call) => call.name === 'aggregate_records' ? ok(report) : undefined));
  const metric = container.querySelector<HTMLSelectElement>('select[aria-label="Chỉ số"]')!;
  expect(Array.from(metric.options).map((o) => o.textContent)).toEqual(['Số bản ghi', 'Dưới ngưỡng', 'Tổng số lượng']);
  expect(Array.from(container.querySelector<HTMLSelectElement>('select[aria-label="Nhóm theo"]')!.options).map((o) => o.textContent)).toEqual(['Không nhóm', 'Kho', 'Khu']);
  await choose('Nhóm theo', 'zone');
  await click('Xem báo cáo');
  expect(calls('aggregate_records')[0].arguments).toEqual({ model_id: 'stock_level', metrics: ['count'], group_by: ['zone'] });
  const table = container.querySelector('.report-result table')!;
  expect(Array.from(table.querySelectorAll('thead th')).map((t) => t.textContent)).toEqual(['Khu', 'Số bản ghi']);
  expect(Array.from(table.querySelectorAll('tbody tr')).map((r) => r.textContent)).toEqual(['a3', 'b2']);
  expect(table.querySelector('tfoot')?.textContent).toBe('Tổng5');
  const note = container.querySelector('.report-result small')!.textContent!;
  expect(note).toContain('chỉ gồm bản ghi bạn được xem');
  expect(note).toContain('phiên bản mô hình 3');
  expect(note).toContain(new Date('2026-10-17T08:30:00Z').toLocaleString('vi-VN'));
  expect(container.textContent).toContain('5 dòng tồn.');
});

it('sends named metrics with `metric` and decimal sums as sum:<field>', async () => {
  await mount(base((call) => call.name === 'aggregate_records' ? ok({ ...report, rows: [], totals: { sum_quantity: '12.500' } }) : undefined));
  await choose('Chỉ số', 'm:below_reorder'); await click('Xem báo cáo');
  expect(calls('aggregate_records')[0].arguments).toEqual({ model_id: 'stock_level', metric: 'below_reorder' });
  await choose('Chỉ số', 's:quantity'); await click('Xem báo cáo');
  expect(calls('aggregate_records')[1].arguments).toEqual({ model_id: 'stock_level', metrics: ['sum:quantity'] });
  expect(container.textContent).toContain('Không có số liệu trong phạm vi của bạn.');
});

it('traces a number back to source records and their history (lineage -> get_record -> get_history)', async () => {
  await mount(base((call) => {
    if (call.name === 'aggregate_records') return ok(report);
    if (call.name === 'get_history' && call.arguments.view === 'explain') return ok({ view: 'explain', explanation: { outcome: 'lineage', reasons: [{ source: 'lineage', message: 'Tổng hợp từ 2 bản ghi' }], query: { model_id: 'stock_level' }, record_refs: ['stock_level:a1', 'stock_level:b1'] } });
    if (call.name === 'get_record') return ok({ record: { ref: 'stock_level:a1', title: 'Tồn A1', values: { quantity: '3.000' } } });
    if (call.name === 'get_history') return ok({ subject: { ref: 'stock_level:a1' }, events: [{ at: '2026-10-17T07:00:00Z', actor: { ref: 'person:1', name: 'Mai', kind: 'human' }, action: 'posted', summary: 'Ghi nhận nhập kho' }] });
  }));
  await click('Xem báo cáo'); await click('Xem nguồn');
  expect(calls('get_history')[0].arguments).toEqual({ ref: 'explain:lin-1', view: 'explain' });
  expect(container.querySelector('[aria-label="Nguồn số liệu"]')?.textContent).toContain('Tổng hợp từ 2 bản ghi');
  await click('Mở bản ghi Tồn A1');
  expect(calls('get_record')[0].arguments).toEqual({ ref: 'stock_level:a1' });
  expect(container.querySelector('[aria-label="Bản ghi nguồn"]')?.textContent).toContain('3.000');
  await click('Lịch sử bản ghi');
  expect(calls('get_history')[1].arguments).toEqual({ ref: 'stock_level:a1' });
  expect(container.querySelector('ol[aria-label="Lịch sử"]')?.textContent).toContain('Mai · Ghi nhận nhập kho');
});

it('shows a backend error as an alert and never keeps the previous numbers', async () => {
  let fail = false;
  await mount(base((call) => call.name === 'aggregate_records' ? (fail ? err('internal', 'Kho dữ liệu không phản hồi') : ok(report)) : undefined));
  await click('Xem báo cáo');
  expect(container.querySelector('.report-result table')).toBeTruthy();
  fail = true;
  await click('Xem báo cáo');
  expect(container.querySelector('.portal-report [role="alert"]')?.textContent).toContain('Kho dữ liệu không phản hồi');
  expect(container.querySelector('.report-result')).toBeNull();
  expect(container.querySelector('.portal-report')?.textContent).not.toContain('5 dòng tồn.');
});

it('stays usable without describe_model or aggregate permission', async () => {
  await mount(base((call) => call.name === 'aggregate_records' ? ok(report) : undefined), ['inspect_workspace', 'aggregate_records']);
  expect(Array.from(container.querySelectorAll<HTMLSelectElement>('select[aria-label="Chỉ số"] option')).map((o) => o.textContent)).toEqual(['Số bản ghi']);
  expect(container.querySelector('select[aria-label="Nhóm theo"]')).toBeNull();
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(base(() => undefined), ['inspect_workspace', 'describe_model']);
  await click('Xem báo cáo');
  expect(container.querySelector('.portal-report [role="alert"]')?.textContent).toContain('Thiếu quyền');
  expect(calls('aggregate_records')).toHaveLength(0);
});

it('shows an empty state when the workspace has no models', async () => {
  await mount(async () => ok({ me: {}, models: [] }));
  expect(container.querySelector('.portal-report')?.textContent).toContain('Chưa có dữ liệu để lập báo cáo.');
});

it('keeps sentence labels lowercase after the first word: "Tổng số lượng", never "Tổng Số lượng"', async () => {
  await mount(base((call) => call.name === 'aggregate_records' ? ok(report) : undefined));
  const text = Array.from(container.querySelectorAll('button, option')).map((e) => e.textContent).join('|');
  expect(text).toContain('Tổng số lượng theo khu'); expect(text).toContain('Số bản ghi theo khu');
  expect(text).not.toMatch(/Tổng Số|theo Khu/);
});
it('shows enum labels from describe_model for report group keys and the source record detail', async () => {
  const model = { ...stockModel, fields: stockModel.fields.map((f) => f.id === 'zone' ? { ...f, enum_values: undefined, options: [{ value: 'a', label: 'Khu A' }, { value: 'b', label: 'Khu B' }] } : f.id === 'note' ? { ...f, type: 'enum', options: [{ value: 'high', label: 'Cao' }] } : f) };
  await mount(base((call) => {
    if (call.name === 'describe_model') return ok({ model });
    if (call.name === 'aggregate_records') return ok(report);
    if (call.name === 'get_history' && call.arguments.view === 'explain') return ok({ view: 'explain', explanation: { outcome: 'lineage', reasons: [], record_refs: ['stock_level:a1'] } });
    if (call.name === 'get_record') return ok({ record: { ref: 'stock_level:a1', title: 'Tồn A1', values: { zone: 'a', note: 'high' } } });
  }));
  await choose('Nhóm theo', 'zone'); await click('Xem báo cáo');
  expect(Array.from(container.querySelectorAll('.report-result tbody td:first-child')).map((td) => td.textContent)).toEqual(['Khu A', 'Khu B']);
  await click('Xem nguồn'); await click('Mở bản ghi Tồn A1');
  const detail = container.querySelector('[aria-label="Bản ghi nguồn"]')!.textContent!;
  expect(detail).toContain('Khu A'); expect(detail).toContain('Cao'); expect(detail).not.toMatch(/\bhigh\b/);
});
it('names source buttons after the record title, keeping the ref only as tooltip and description', async () => {
  await mount(base((call) => {
    if (call.name === 'aggregate_records') return ok(report);
    if (call.name === 'get_history' && call.arguments.view === 'explain') return ok({ view: 'explain', explanation: { outcome: 'lineage', reasons: [], record_refs: ['stock_level:a1', 'stock_level:b1'] } });
    if (call.name === 'get_record') return ok({ record: { ref: call.arguments.ref, title: call.arguments.ref === 'stock_level:a1' ? 'Tồn A1' : undefined, values: {} } });
  }));
  await click('Xem báo cáo'); await click('Xem nguồn');
  const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>('[aria-label="Nguồn số liệu"] li button'));
  expect(buttons.map((b) => b.textContent)).toEqual(['Mở bản ghi Tồn A1', 'Mở bản ghi Tồn kho 2']);
  expect(buttons.map((b) => b.getAttribute('title'))).toEqual(['stock_level:a1', 'stock_level:b1']);
  expect(container.querySelector(`[id="${buttons[0].getAttribute('aria-describedby')}"]`)?.textContent).toContain('stock_level:a1');
  expect(buttons.every((b) => !b.textContent!.includes('stock_level:'))).toBe(true);
});
