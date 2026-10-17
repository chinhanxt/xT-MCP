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
const err = (message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code: 'internal', message } }) }] });
let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'find_people', 'search_records', 'get_record', 'get_inbox']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'H', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0] as Call).filter((c) => c.name === name);
async function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === text);
  expect(button, `button ${text}`).toBeTruthy();
  await act(async () => button!.click());
}
async function type(label: string, value: string) {
  const input = container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value); input.dispatchEvent(new Event('input', { bubbles: true })); });
}
const submit = (label: string) => act(async () => container.querySelector(`form:has([aria-label="${label}"])`)!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
const workspace = { me: { name: 'Lan' }, models: [{ id: 'policy', label: 'Quy chế', primitive: 'document' }, { id: 'leave', label: 'Nghỉ phép', primitive: 'request' }, { id: 'minutes', label: 'Biên bản', primitive: 'document' }] };
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('lists only document-type models from the workspace, loads a page, pages with the same params and opens a record', async () => {
  await mount(async (call) => {
    if (call.name === 'inspect_workspace') return ok(workspace);
    if (call.name === 'find_people') return ok({ people: [], next_cursor: null });
    if (call.name === 'search_records') {
      return call.arguments.cursor
        ? ok({ items: [{ ref: 'policy:2', title: 'Quy chế B' }], next_cursor: null })
        : ok({ items: [{ ref: 'policy:1', title: 'Quy chế A' }], next_cursor: 'c1' });
    }
    if (call.name === 'get_record') return ok({ record: { ref: 'policy:1', title: 'Quy chế A', revision: 3, values: { version: '2' } } });
    throw new Error(`unexpected ${call.name}`);
  });
  const select = container.querySelector<HTMLSelectElement>('select[aria-label="Loại tài liệu"]')!;
  expect(Array.from(select.options).map((o) => o.textContent)).toEqual(['Quy chế', 'Biên bản']);
  expect(calls('search_records')[0].arguments).toEqual({ model_id: 'policy', limit: 20 });
  expect(JSON.stringify(calls('search_records'))).not.toContain('SOP');
  expect(container.textContent).toContain('Quy chế A');
  await type('Tìm tài liệu', 'nghỉ'); // đổi ô tìm sau trang đầu không được làm lệch cursor
  await click('Tải thêm');
  expect(calls('search_records')[1].arguments).toEqual({ model_id: 'policy', limit: 20, cursor: 'c1' });
  expect(container.textContent).toContain('Quy chế B');
  expect(Array.from(container.querySelectorAll('button')).some((b) => b.textContent === 'Tải thêm')).toBe(false);
  await click('Mở Quy chế A');
  expect(calls('get_record')[0].arguments).toEqual({ ref: 'policy:1' });
  expect(container.querySelector('[aria-label="Chi tiết tài liệu"]')?.textContent).toContain('version');
  await submit('Tìm tài liệu');
  expect(calls('search_records').at(-1)!.arguments).toEqual({ model_id: 'policy', limit: 20, query: 'nghỉ' });
});

it('shows an empty state when a search has no match and when the tenant has no document models', async () => {
  await mount(async (call) => call.name === 'inspect_workspace' ? ok(workspace) : call.name === 'search_records' ? ok({ items: [], next_cursor: null }) : ok({ people: [] }));
  expect(container.textContent).toContain('Không có tài liệu phù hợp.');
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(async (call) => call.name === 'inspect_workspace' ? ok({ me: {}, models: [{ id: 'leave', label: 'Nghỉ phép', primitive: 'request' }] }) : ok({ people: [] }));
  expect(container.textContent).toContain('chưa có loại tài liệu');
  expect(calls('search_records')).toHaveLength(0);
});

it('shows the backend error and no stale rows when the search fails', async () => {
  let fail = false;
  await mount(async (call) => {
    if (call.name === 'inspect_workspace') return ok(workspace);
    if (call.name === 'search_records') return fail ? err('Kho tài liệu lỗi') : ok({ items: [{ ref: 'policy:1', title: 'Quy chế A' }], next_cursor: null });
    return ok({ people: [] });
  });
  expect(container.textContent).toContain('Quy chế A');
  fail = true;
  await submit('Tìm tài liệu');
  const alerts = Array.from(container.querySelectorAll('[role="alert"]')).map((a) => a.textContent);
  expect(alerts.some((t) => t?.includes('Kho tài liệu lỗi'))).toBe(true);
});

it('reports a missing search tool as a permission problem without calling it', async () => {
  await mount(async (call) => call.name === 'inspect_workspace' ? ok(workspace) : ok({ people: [] }), ['inspect_workspace', 'find_people']);
  expect(container.textContent).toContain('Thiếu quyền');
  expect(calls('search_records')).toHaveLength(0);
});
