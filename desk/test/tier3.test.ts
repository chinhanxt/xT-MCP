// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
import { Tier3 } from '../src/Tier3';
import { areaFromHash } from '../src/portal-nav';
import { BUILTIN_WMS, GROUPS, isAdmin, modulePath, parseApps, visibleApps } from '../src/tier3';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
// Fixture theo hợp đồng dự kiến của agent nền tảng: apps[] {id,title,group,status,ui{kind,path}} (+ models cho giao diện chung).
const apps = [
  { id: 'kho-hang', title: 'Kho hàng (WMS)', group: 'dong-chay-hang-hoa', status: 'installed', ui: { kind: 'module', path: '/waretrack/' } },
  { id: 'cham-cong', title: 'Chấm công', group: 'thoi-gian-du-an', status: 'installed', ui: { kind: 'generic' }, models: ['cham_cong'] },
  { id: 'crm', title: 'CRM nhẹ', group: 'trai-nghiem-thue-bao', status: 'disabled', ui: { kind: 'generic' }, models: ['khach_hang'] },
  { id: 'tai-san', title: 'Tài sản', group: 'tai-san-khong-gian', status: 'installed', ui: { kind: 'module', path: '/apps/tai-san/' } },
  { id: 'xau', title: 'Đường dẫn xấu', group: 'tai-san-khong-gian', status: 'installed', ui: { kind: 'module', path: '/apps/../studio/' } },
  { id: 'sai', ui: { kind: 'generic' } },
  { id: 'la', title: 'Nhóm lạ', group: 'khong-co', status: 'installed', ui: { kind: 'generic' } },
];
let root: Root | undefined;
let container: HTMLDivElement;
async function mount(workspace: unknown | (() => ToolResult), tools = ['inspect_workspace']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const mcp = { client: { callTool: vi.fn(async () => typeof workspace === 'function' ? (workspace as () => ToolResult)() : ok(workspace)) }, tools: [] } as unknown as McpConnection;
  root = createRoot(container);
  const toolList = tools.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  await act(async () => { root!.render(createElement(Tier3, { mcp, tools: toolList, renderWarehouse: () => createElement('div', { 'data-testid': 'wms' }, 'Màn hình kho') })); });
}
const text = () => container.textContent || '';
const group = (title: string) => Array.from(container.querySelectorAll('.tier3-group')).find((s) => s.querySelector('h3')?.textContent === title)!;
async function click(label: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === label);
  expect(button, `button ${label}`).toBeTruthy();
  await act(async () => button!.click());
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('parses apps strictly, detects admins and validates module paths', () => {
  expect(parseApps({})).toBeNull();
  expect(parseApps({ apps })!.map((a) => a.id)).toEqual(['kho-hang', 'cham-cong', 'crm', 'tai-san', 'xau', 'la']);
  expect(parseApps({ apps: [{ id: 'x', status: 'weird', ui: { kind: 'generic' } }, { id: 'y', status: 'installed', ui: { kind: 'iframe' } }] })).toEqual([]);
  expect(isAdmin({ me: { roles: ['staff'] } })).toBe(false);
  expect(isAdmin({ me: {} })).toBe(false);
  expect(isAdmin({ me: { roles: ['staff', 'it'] } })).toBe(true);
  const all = parseApps({ apps })!;
  expect(visibleApps(all, false).some((a) => a.status === 'disabled')).toBe(false);
  expect(visibleApps(all, true).some((a) => a.status === 'disabled')).toBe(true);
  expect(modulePath('/waretrack/')).toBe('/waretrack/');
  expect(modulePath('/apps/tai-san/')).toBe('/apps/tai-san/');
  for (const bad of ['/apps/../studio/', '/studio/', '//evil.test/', 'https://evil.test/', '/apps/x', '/apps/X/', '/apps//', undefined]) expect(modulePath(bad)).toBeNull();
  expect(GROUPS.map((g) => g.title)).toEqual(['Dòng chảy Hàng hóa', 'Thời gian & Dự án', 'Trải nghiệm & Thuê bao', 'Tài sản & Không gian']);
});

it('keeps the old #/Kho link working by mapping it to Tier 3', () => {
  expect(areaFromHash('#/Kho')).toBe('T3');
  expect(areaFromHash('#/T3')).toBe('T3');
});

it('shows four groups, installed apps for staff, hides disabled ones and offers no lifecycle action', async () => {
  await mount({ me: { name: 'Lan', roles: ['staff'] }, apps });
  expect(Array.from(container.querySelectorAll('.tier3-group h3')).map((h) => h.textContent)).toEqual([...GROUPS.map((g) => g.title), 'Nhóm khác']);
  expect(group('Dòng chảy Hàng hóa').textContent).toContain('Kho hàng (WMS)');
  expect(group('Thời gian & Dự án').textContent).toContain('Chấm công');
  expect(group('Trải nghiệm & Thuê bao').textContent).toContain('Chưa có ứng dụng trong nhóm này.');
  expect(text()).not.toContain('CRM nhẹ');
  expect(group('Nhóm khác').textContent).toContain('Nhóm lạ');
  expect(text()).not.toContain('Đề xuất');
});

it('shows disabled apps greyed and non-openable for admins, with the lifecycle action disabled and explained', async () => {
  await mount({ me: { name: 'Mai', roles: ['it'] }, apps });
  const crm = group('Trải nghiệm & Thuê bao');
  expect(crm.textContent).toContain('CRM nhẹ');
  expect(crm.textContent).toContain('Đã tắt');
  expect(crm.querySelector('li')?.className).toContain('tier3-disabled');
  expect(Array.from(crm.querySelectorAll('button')).some((b) => b.textContent === 'Mở CRM nhẹ')).toBe(false);
  const propose = Array.from(container.querySelectorAll('button')).filter((b) => /^Đề xuất/.test(b.textContent || ''));
  expect(propose.map((b) => b.textContent)).toContain('Đề xuất bật lại');
  expect(propose.map((b) => b.textContent)).toContain('Đề xuất tắt');
  expect(propose.every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  expect(text()).toContain('không tạo đề xuất giả');
});

it('opens the built-in warehouse view for the WMS app and a same-origin iframe for module apps', async () => {
  await mount({ me: { roles: ['staff'] }, apps });
  await click('Mở Kho hàng (WMS)');
  expect(container.querySelector('[data-testid="wms"]')).toBeTruthy();
  await click('← Danh sách ứng dụng');
  await click('Mở Tài sản');
  const frame = container.querySelector<HTMLIFrameElement>('iframe[title="Ứng dụng Tài sản"]')!;
  expect(frame.getAttribute('src')).toBe('/apps/tai-san/?integrated=1');
  expect(frame.dataset.xtScope).toBeUndefined();
  await click('← Danh sách ứng dụng');
  await click('Mở Đường dẫn xấu');
  expect(container.querySelector('iframe')).toBeNull();
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('không hợp lệ');
});

it('falls back to the existing warehouse entry only when the server does not publish apps', async () => {
  await mount({ me: { name: 'Lan' } });
  expect(group('Dòng chảy Hàng hóa').textContent).toContain(BUILTIN_WMS.title);
  expect(text()).toContain('chưa công bố danh sách ứng dụng');
  expect(group('Thời gian & Dự án').textContent).toContain('Chưa có ứng dụng trong nhóm này.');
  await click('Mở Kho hàng (WMS)');
  expect(container.querySelector('[data-testid="wms"]')).toBeTruthy();
});

it('does not invent apps when the server lists none, and reports backend errors honestly', async () => {
  await mount({ me: {}, apps: [] });
  expect(text()).not.toContain('Kho hàng');
  expect(container.querySelectorAll('.tier3-group')).toHaveLength(4);
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(() => err('access_denied', 'Không có quyền'));
  expect(container.querySelector('[role="alert"]')?.getAttribute('data-state')).toBe('forbidden');
  expect(container.querySelectorAll('.tier3-group')).toHaveLength(0);
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount({}, []);
  expect(container.querySelector('[role="alert"]')?.getAttribute('data-state')).toBe('forbidden');
});
