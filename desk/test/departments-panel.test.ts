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
import { departmentsOf } from '../src/DepartmentsPanel';

type Call = { name: string; arguments: Record<string, unknown> };
const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const err = (code: string, message: string): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message } }) }] });
const people = [
  { ref: 'person:1', name: 'Lan', title: 'Kế toán', email: 'lan@example.test', org_unit: { ref: 'org_unit:fin', name: 'Tài chính' } },
  { ref: 'person:2', name: 'Mai', title: 'Trưởng phòng', org_unit: { ref: 'org_unit:fin', name: 'Tài chính' } },
  { ref: 'person:3', name: 'Bình', title: 'Kỹ sư', org_unit: { ref: 'org_unit:it', name: 'Công nghệ' } },
  { ref: 'person:4', name: 'Chi', org_unit: null },
];
let root: Root | undefined;
let container: HTMLDivElement;
let mcp: McpConnection;
async function mount(caller: (call: Call) => Promise<ToolResult>, names = ['inspect_workspace', 'find_people', 'get_inbox', 'search_records']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'H', mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
}
const calls = (name: string) => vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0] as Call).filter((c) => c.name === name);
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('derives departments only from people the caller may see', () => {
  expect(departmentsOf(people).map((u) => `${u.name}:${u.count}`)).toEqual(['Công nghệ:1', 'Tài chính:2']);
  expect(departmentsOf([])).toEqual([]);
});

it('lists departments from the directory and loads members with find_people({org_unit})', async () => {
  await mount(async (call) => {
    if (call.name === 'inspect_workspace') return ok({ me: { name: 'Lan' }, models: [] });
    if (call.name === 'find_people' && call.arguments.org_unit) return ok({ people: people.slice(0, 2), next_cursor: null });
    if (call.name === 'find_people') return ok({ people, next_cursor: null });
    throw new Error('unexpected');
  });
  const group = container.querySelector('[aria-label="Chọn phòng ban"]')!;
  expect(Array.from(group.querySelectorAll('button')).map((b) => b.textContent)).toEqual(['Công nghệ (1)', 'Tài chính (2)']);
  await act(async () => Array.from(group.querySelectorAll('button')).find((b) => b.textContent === 'Tài chính (2)')!.click());
  expect(calls('find_people').at(-1)!.arguments).toEqual({ org_unit: 'org_unit:fin', limit: 100 });
  const members = container.querySelector('.portal-departments [aria-label="Thành viên Tài chính"]')!;
  expect(members.textContent).toContain('Lan');
  expect(members.textContent).toContain('lan@example.test');
  expect(members.textContent).toContain('Trưởng phòng');
  expect(group.querySelector('[aria-pressed="true"]')?.textContent).toBe('Tài chính (2)');
});

it('shows empty, error and missing-tool states without inventing departments', async () => {
  await mount(async (call) => call.name === 'find_people' ? ok({ people: [people[3]] }) : ok({ me: {}, models: [] }));
  expect(container.querySelector('.portal-departments')?.textContent).toContain('chưa ghi nhận phòng ban nào');
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(async (call) => call.name === 'find_people' ? err('access_denied', 'Không có quyền person.read') : ok({ me: {}, models: [] }));
  expect(container.querySelector('.portal-departments [role="alert"]')?.textContent).toContain('Không có quyền person.read');
  expect(container.querySelector('.portal-departments button')).toBeNull();
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  await mount(async () => ok({ me: {}, models: [] }), ['inspect_workspace']);
  expect(container.querySelector('.portal-departments [role="alert"]')?.textContent).toContain('Thiếu quyền');
  expect(calls('find_people')).toHaveLength(0);
});

it('follows next_cursor so a large directory is not silently truncated, and warns when the page cap is hit', async () => {
  const page = (n: number, next: string | null) => ok({ people: [{ ref: `person:${n}`, name: `P${n}`, org_unit: { ref: `org_unit:u${n}`, name: `Đơn vị ${n}` } }], next_cursor: next });
  await mount(async (call) => {
    if (call.name === 'inspect_workspace') return ok({ me: {}, models: [] });
    if (call.arguments.cursor === 'c1') return page(2, null);
    return page(1, 'c1');
  });
  expect(Array.from(container.querySelectorAll('.portal-departments [aria-label="Chọn phòng ban"] button')).map((b) => b.textContent)).toEqual(['Đơn vị 1 (1)', 'Đơn vị 2 (1)']);
  expect(calls('find_people').map((c) => c.arguments).filter((a) => a.limit === 100)).toEqual([{ limit: 100 }, { limit: 100, cursor: 'c1' }]);
  expect(container.querySelector('.portal-departments')?.textContent).not.toContain('chưa đầy đủ');
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  let n = 0;
  await mount(async (call) => call.name === 'inspect_workspace' ? ok({ me: {}, models: [] }) : page(++n, `c${n}`));
  expect(calls('find_people').filter((c) => c.arguments.limit === 100)).toHaveLength(5);
  expect(container.querySelector('.portal-departments [role="note"]')?.textContent).toContain('chưa đầy đủ');
});
