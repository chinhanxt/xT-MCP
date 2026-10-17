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
import { areaFromHash } from '../src/portal-nav';
import { BUILTIN_WMS, GROUPS, appViews, isAdmin, lifecycleCommand, modulePath, moduleScope, parseApps, tenantName, visibleApps } from '../src/tier3';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
// Fixture theo hợp đồng thật (inspect_workspace.apps, PR #59): ui là mảng, status enabled|disabled.
const mod = (path: string) => ({ kind: 'module', path });
const apps = [
  { id: 'kho-hang', pack: 'kho-hang', title: 'Kho hàng (WMS)', group: 'hang-hoa', status: 'enabled', version: '1.0.0', ui: [mod('/waretrack/'), { kind: 'generic' }], tools: ['search_records'], models: ['ton_kho'] },
  { id: 'cham-cong', pack: 'cham-cong', title: 'Chấm công', group: 'thoi-gian-du-an', status: 'enabled', version: '0.2.0', ui: [{ kind: 'generic' }], tools: [], models: ['cham_cong'] },
  { id: 'crm', pack: 'crm-nhe', title: 'CRM nhẹ', group: 'trai-nghiem-thue-bao', status: 'disabled', version: '0.1.0', ui: [{ kind: 'generic' }], tools: [], models: ['khach_hang'] },
  { id: 'tai-san', pack: 'tai-san', title: 'Tài sản', group: 'tai-san-khong-gian', status: 'enabled', version: '0.1.0', ui: [mod('/apps/tai-san/')], tools: [], models: [] },
  { id: 'xau', pack: 'xau', title: 'Đường dẫn xấu', group: 'tai-san-khong-gian', status: 'enabled', version: '0.1.0', ui: [mod('/apps/../studio/')], tools: [], models: [] },
  { id: 'sai', ui: [{ kind: 'generic' }] },
  { id: 'khong-ui', title: 'Không giao diện', status: 'enabled', ui: [] },
  { id: 'la', pack: 'la', title: 'Nhóm lạ', group: 'khong-co', status: 'enabled', version: '1', ui: [{ kind: 'generic' }], tools: [], models: [] },
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
  expect(parseApps({ apps: [{ id: 'x', status: 'weird', ui: [{ kind: 'generic' }] }, { id: 'y', status: 'enabled', ui: [{ kind: 'iframe' }] }, { id: 'z', status: 'enabled', ui: { kind: 'generic' } }, { id: 'w', status: 'installed', ui: [{ kind: 'generic' }] }] })).toEqual([]);
  expect(parseApps({ apps: [{ id: 'm', status: 'enabled', ui: [{ kind: 'bogus' }, { kind: 'generic' }] }] })![0]).toMatchObject({ id: 'm', pack: 'm', ui: [{ kind: 'generic' }], tools: [], models: [] });
  expect(parseApps({ apps })!.find((a) => a.id === 'kho-hang')).toMatchObject({ pack: 'kho-hang', version: '1.0.0', tools: ['search_records'], models: ['ton_kho'], ui: [{ kind: 'module', path: '/waretrack/' }, { kind: 'generic' }] });
  expect(isAdmin({ me: { roles: ['staff'] } })).toBe(false);
  expect(isAdmin({ me: {} })).toBe(false);
  expect(isAdmin({ me: { roles: ['it', 'director'] } })).toBe(false);
  expect(isAdmin({ me: { roles: ['staff', 'admin'] } })).toBe(true);
  const all = parseApps({ apps })!;
  expect(visibleApps(all, false).some((a) => a.status === 'disabled')).toBe(false);
  expect(visibleApps(all, true).some((a) => a.status === 'disabled')).toBe(true);
  expect(modulePath('/waretrack/')).toBe('/waretrack/');
  expect(modulePath('/apps/tai-san/')).toBe('/apps/tai-san/');
  for (const bad of ['/apps/../studio/', '/studio/', '//evil.test/', 'https://evil.test/', '/apps/x', '/apps/X/', '/apps//', undefined]) expect(modulePath(bad)).toBeNull();
  expect(moduleScope('/waretrack/')).toBe('waretrack');
  expect(moduleScope('/apps/tai-san/')).toBe('module');
  expect(appViews(all.find((a) => a.id === 'kho-hang')!).map((v) => v.label)).toEqual(['Sơ đồ 3D', 'Danh sách']);
  expect(appViews(all.find((a) => a.id === 'cham-cong')!).map((v) => v.id)).toEqual(['generic']);
  expect(appViews(all.find((a) => a.id === 'tai-san')!).map((v) => v.id)).toEqual(['module']);
  expect(tenantName({ tenant: { name: 'acme' } })).toBe('acme');
  expect(tenantName({})).toBe('<tenant>');
  expect(lifecycleCommand(all.find((a) => a.id === 'crm')!, 'acme')).toBe('xt pack enable --tenant acme crm-nhe --proposed-by <người đề xuất> --approved-by <người duyệt>');
  expect(lifecycleCommand(all.find((a) => a.id === 'cham-cong')!, "a b'c")).toContain("--tenant 'a b'\\''c' cham-cong");
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
  expect(text()).not.toContain('xt pack');
  expect(container.querySelector('details')).toBeNull();
  expect(text()).not.toContain('Không giao diện');
});

it('shows disabled apps greyed and non-openable for admins, with the exact CLI command and no fake action', async () => {
  await mount({ me: { name: 'Mai', roles: ['admin'] }, tenant: { name: 'acme' }, apps });
  const crm = group('Trải nghiệm & Thuê bao');
  expect(crm.textContent).toContain('CRM nhẹ');
  expect(crm.textContent).toContain('Đã tắt');
  expect(crm.querySelector('li')?.className).toContain('tier3-disabled');
  expect(Array.from(crm.querySelectorAll('button')).some((b) => b.textContent === 'Mở CRM nhẹ')).toBe(false);
  expect(crm.querySelector('code')?.textContent).toBe('xt pack enable --tenant acme crm-nhe --proposed-by <người đề xuất> --approved-by <người duyệt>');
  expect(group('Thời gian & Dự án').querySelector('code')?.textContent).toBe('xt pack disable --tenant acme cham-cong --proposed-by <người đề xuất> --approved-by <người duyệt>');
  expect(crm.textContent).toContain('vận hành viên duyệt');
  expect(crm.textContent).toContain('không có công cụ MCP');
  expect(Array.from(container.querySelectorAll('button')).some((b) => /Đề xuất|Tắt|Bật/.test(b.textContent || ''))).toBe(false);
});

it('does not treat non-admin roles (it, director) as admins', async () => {
  await mount({ me: { roles: ['it', 'director'] }, apps });
  expect(text()).not.toContain('CRM nhẹ');
  expect(container.querySelector('code')).toBeNull();
});

it('opens the built-in warehouse view for the WMS app and a same-origin iframe for module apps', async () => {
  await mount({ me: { roles: ['staff'] }, apps });
  await click('Mở Kho hàng (WMS)');
  expect(container.querySelector('[data-testid="wms"]')).toBeTruthy();
  const choice = Array.from(container.querySelectorAll('[role="group"] button'));
  expect(choice.map((b) => b.textContent)).toEqual(['Sơ đồ 3D', 'Danh sách']);
  expect(choice[0].getAttribute('aria-pressed')).toBe('true');
  await click('Danh sách');
  expect(container.querySelector('[data-testid="wms"]')).toBeNull();
  expect(text()).toContain('chưa được cấp quyền xem model nào');
  await click('Sơ đồ 3D');
  expect(container.querySelector('[data-testid="wms"]')).toBeTruthy();
  await click('← Danh sách ứng dụng');
  await click('Mở Chấm công');
  expect(container.querySelector('[role="group"]')).toBeNull();
  await click('← Danh sách ứng dụng');
  await click('Mở Tài sản');
  expect(container.querySelector('[role="group"]')).toBeNull();
  const frame = container.querySelector<HTMLIFrameElement>('iframe[title="Ứng dụng Tài sản"]')!;
  expect(frame.getAttribute('src')).toBe('/apps/tai-san/?integrated=1');
  expect(frame.dataset.xtScope).toBe('module');
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
