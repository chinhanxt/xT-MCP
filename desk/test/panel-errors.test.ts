// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';
import { PortalBoundary, PortalPending } from '../src/PortalBoundary';

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
const workspace = { me: { name: 'Lan' }, models: [{ id: 'policy', label: 'Quy chế', primitive: 'document' }, { id: 'advance_request', label: 'Tạm ứng', primitive: 'request' }] };
const ALL = ['inspect_workspace', 'describe_model', 'find_people', 'search_records', 'aggregate_records', 'get_inbox', 'create_record', 'submit_request', 'get_record', 'get_history'];
let root: Root | undefined;
let container: HTMLDivElement;
async function mount(area: string, failing: string, failure: ToolResult, names = ALL) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const caller = async (call: Call): Promise<ToolResult> => {
    if (call.name === failing) return failure;
    if (call.name === 'inspect_workspace') return ok(workspace);
    if (call.name === 'describe_model') return ok({ model: { id: call.arguments.model_id, fields: [{ id: 'purpose', label: 'Mục đích', type: 'string', required: true }], metrics: [] } });
    if (call.name === 'search_records') return ok({ items: [], next_cursor: null });
    if (call.name === 'find_people') return ok({ people: [] });
    return ok({ items: [] });
  };
  const mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area, mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
async function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === text);
  expect(button, `button ${text}`).toBeTruthy();
  await act(async () => button!.click());
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

const cases: Array<[string, string, string, () => Promise<void>]> = [
  ['documents', 'H', 'search_records', async () => {}],
  ['departments', 'H', 'find_people', async () => {}],
  ['report', 'D', 'aggregate_records', async () => { await click('Xem báo cáo'); }],
  ['request form', 'P', 'describe_model', async () => {}],
];
describe.each(cases)('%s panel', (name, area, tool, act2) => {
  const scoped = (selector: string) => container.querySelector(`.portal-${name === 'documents' ? 'docs' : name === 'departments' ? 'departments' : name === 'report' ? 'report' : 'request'} ${selector}`);
  it('shows expired session for unauthenticated (401) and never a generic error', async () => {
    await mount(area, tool, err('unauthenticated', 'Token hết hạn'));
    await act2();
    const alert = scoped('[role="alert"]')!;
    expect(alert.getAttribute('data-state')).toBe('expired');
    expect(alert.textContent).toContain('Phiên đã hết hạn');
    expect(alert.textContent).toContain('Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại');
    expect(alert.getAttribute('title')).toBe('Token hết hạn');
  });
  it('shows forbidden for access_denied', async () => {
    await mount(area, tool, err('access_denied', 'Không có quyền'));
    await act2();
    const alert = scoped('[role="alert"]')!;
    expect(alert.getAttribute('data-state')).toBe('forbidden');
    expect(alert.textContent).toContain('Thiếu quyền');
  });
  it('treats a missing tool in the profile as forbidden', async () => {
    await mount(area, '-', ok({}), ALL.filter((n) => n !== tool));
    await act2();
    expect(scoped('[role="alert"]')?.getAttribute('data-state')).toBe('forbidden');
  });
});

it('classifies a transport 401 thrown by the MCP client as expired', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = ALL.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const mcp = { client: { callTool: vi.fn(async (call: Call) => { if (call.name === 'find_people') throw new Error('Error POSTing to endpoint (HTTP 401): invalid_token'); return ok(workspace); }) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'H', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  expect(container.querySelector('.portal-departments [role="alert"]')?.getAttribute('data-state')).toBe('expired');
});

it('fallback and pending panels have a focusable heading so focus logic works', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(console, 'error').mockImplementation(() => {});
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  const Bad = () => { throw new Error('hỏng'); };
  await act(async () => { root!.render(createElement(PortalBoundary, { label: 'D' }, createElement(Bad))); });
  expect(container.querySelector('.portal h2')?.getAttribute('tabindex')).toBe('-1');
  await act(async () => { root!.render(createElement(PortalPending, { label: 'H', error: '' })); });
  expect(container.querySelector('.portal h2')?.getAttribute('tabindex')).toBe('-1');
});

it('D no longer carries the duplicate count button next to the report panel', async () => {
  await mount('D', '-', ok({}));
  expect(Array.from(container.querySelectorAll('button')).map((b) => b.textContent)).not.toContain('Thống kê');
  expect(container.querySelector('.portal-report')).toBeTruthy();
});
