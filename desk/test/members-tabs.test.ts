// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { MembersPanel } from '../src/MembersPanel';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
let root: Root | undefined, container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('splits members and access into four short tabs with the same forms and buttons', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  const tools = ['create_record', 'start_process', 'search_records', 'get_inbox', 'transition_record', 'update_record'].map(name => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const mcp = { client: { callTool: vi.fn(async () => ok({ items: [], next_cursor: null })) }, tools } as unknown as McpConnection;
  await act(async () => { root!.render(createElement(MembersPanel, { mcp, tools, workspace: { me: { ref: 'person:hr1', roles: ['hr'] } }, onRefresh: () => {} })); });
  const tabs = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tablist"][aria-label="Mục cấp quyền"] [role="tab"]'));
  expect(tabs.map(t => t.textContent)).toEqual(['Vai trò của tôi', 'Tiếp nhận nhân viên', 'Cấp quyền SSO', 'Thu hồi']);
  const panel = (id: string) => container.querySelector<HTMLElement>(`#members-panel-${id}`)!;
  expect(panel('roles').hidden).toBe(false); expect(panel('roles').textContent).toContain('Vai trò hiệu lực của tôi: Nhân sự');
  expect(panel('onboard').hidden).toBe(true); expect(panel('onboard').querySelector('form[aria-label="Tiếp nhận nhân viên"]')).toBeTruthy();
  expect(panel('grant').querySelector('form[aria-label="Đề xuất cấp quyền"]')).toBeTruthy();
  expect(Array.from(panel('grant').querySelectorAll('button')).map(b => b.textContent)).toContain('Tải bước quy trình');
  expect(Array.from(panel('revoke').querySelectorAll('button')).map(b => b.textContent)).toContain('Tải quyền truy cập');
  expect(panel('revoke').querySelector('[aria-label="Ngày thu hồi"]')).toBeTruthy();
  await act(async () => tabs[3].click());
  expect(panel('revoke').hidden).toBe(false); expect(panel('roles').hidden).toBe(true);
  expect(tabs[3].getAttribute('aria-selected')).toBe('true');
  // các panel nằm trong cùng vùng "Thành viên và quyền" và không mang nhãn trùng với biểu mẫu
  expect(container.querySelectorAll('[aria-label="Tiếp nhận nhân viên"]')).toHaveLength(1);
});
