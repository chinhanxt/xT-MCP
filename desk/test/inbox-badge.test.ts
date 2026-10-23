// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { OidcSession } from '../src/oidc';
import type { McpConnection } from '../src/mcp';
import type { ToolResult } from '../src/types';
import { badgeText, inboxSummary } from '../src/inbox-count';

const mocks = vi.hoisted(() => ({ createSession: vi.fn(), connect: vi.fn(), config: {
  model: 'test', toolProfile: 'all', maxSteps: 2, coreSkill: '', reasoningEffort: '',
  sandboxUrl: 'https://sandbox.test', xtOrigin: 'https://xt.test', oidcClientId: 'desk',
  oidcOrganizations: [] as string[], appGlobalCallsPerSec: 10, appGlobalBurst: 10,
} }));
vi.mock('../src/config', () => ({ config: mocks.config, MCP_PATH: '/mcp', LLM_PATH: '/llm' }));
vi.mock('../src/auth', async () => ({ ...(await vi.importActual('../src/oidc')), CALLBACK_PATH: '/auth/callback', createSession: mocks.createSession }));
vi.mock('../src/mcp', () => ({ connectMcp: mocks.connect }));
vi.mock('../src/llm', () => ({ createLlm: () => ({ complete: vi.fn() }) }));
import { App } from '../src/App';

type Call = { name: string; arguments: Record<string, unknown> };
const issuer = 'https://kc.test/realms/xt';
const ok = (data: unknown): ToolResult => ({ structuredContent: { ok: true, data } });
let root: Root | undefined, container: HTMLDivElement, session: OidcSession, nav: string[];
let inbox: () => ToolResult | Promise<ToolResult>, toolNames: string[], connection: McpConnection;

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { fn(0); return 0; });
  nav = []; sessionStorage.clear(); history.replaceState(null, '', '/');
  toolNames = ['inspect_workspace', 'find_people', 'get_inbox', 'get_record', 'search_records', 'decide_request'];
  inbox = () => ok({ counts: { approval: 3, task: 1 }, items: [], next_cursor: null });
  session = new OidcSession({ issuer, clientId: 'desk', scope: 'openid', redirectUri: `${location.origin}/auth/callback`, postLogoutRedirectUri: `${location.origin}/` }, {
    session: sessionStorage, now: () => Date.now(), navigate: url => { nav.push(url); },
    fetch: vi.fn(async url => String(url).endsWith('openid-configuration')
      ? new Response(JSON.stringify({ issuer, authorization_endpoint: `${issuer}/auth`, token_endpoint: `${issuer}/token`, end_session_endpoint: `${issuer}/logout` }))
      : new Response(JSON.stringify({ access_token: 'e30.e30.sig', expires_in: 300 }))) as typeof fetch,
  });
  mocks.createSession.mockReturnValue(session);
  mocks.connect.mockImplementation(async () => {
    const tools = toolNames.map(name => ({ name, inputSchema: { type: 'object' } }));
    connection = { tools, client: { getInstructions: () => '', close: vi.fn(async () => {}), callTool: vi.fn(async (call: Call) => {
      if (call.name === 'get_inbox') return inbox();
      if (call.name === 'inspect_workspace') return ok({ me: { name: 'Lan' }, models: [] });
      if (call.name === 'get_record') return ok({ record: { ref: 'stock_request:r1', title: 'PN-1', state: 'in_review', revision: 2 } });
      if (call.name === 'decide_request') return ok({ request: { ref: 'stock_request:r1', state: 'approved', revision: 3 } });
      return ok({ items: [], people: [], next_cursor: null });
    }) } } as unknown as McpConnection;
    return connection;
  });
  await session.startLogin();
  const state = new URL(nav.at(-1)!).searchParams.get('state');
  await session.handleCallback(`${location.origin}/auth/callback?code=code&state=${state}`);
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount()); root = undefined;
  document.body.innerHTML = ''; delete window.xtPortal; sessionStorage.clear();
  vi.clearAllMocks(); vi.unstubAllGlobals();
});
async function mount(hash = '') {
  history.replaceState(null, '', `/${hash}`);
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  await act(async () => { root!.render(createElement(App)); });
}
const inboxCalls = () => vi.mocked(connection.client.callTool).mock.calls.map(c => c[0] as Call).filter(c => c.name === 'get_inbox' && Object.keys(c.arguments).length === 0);
const navButton = (code: string) => Array.from(container.querySelectorAll<HTMLButtonElement>('nav button')).find(b => b.childNodes[0]?.textContent === code)!;

it('summarises a real get_inbox payload: counts first, items as fallback, nothing invented', () => {
  expect(inboxSummary({ counts: { approval: 2, task: 0 }, items: [] })).toEqual({ approvals: 2, waiting: true });
  expect(inboxSummary({ counts: {}, items: [{ kind: 'approval' }, { kind: 'task' }] })).toEqual({ approvals: 1, waiting: true });
  expect(inboxSummary({ counts: { notification: 1 }, items: [] })).toEqual({ approvals: 0, waiting: true });
  expect(inboxSummary({ counts: { approval: 0 }, items: [] })).toEqual({ approvals: 0, waiting: false });
  expect(inboxSummary({})).toEqual({ approvals: 0, waiting: false });
  expect(inboxSummary({ counts: { approval: -4, task: 'x' } })).toEqual({ approvals: 0, waiting: false });
  expect(badgeText(7)).toBe('7'); expect(badgeText(120)).toBe('99+');
});

it('calls get_inbox once on load and shows the approval badge and bell dot without renaming the nav button', async () => {
  await mount();
  expect(inboxCalls()).toHaveLength(1);
  const p = navButton('P');
  expect(p.textContent).toBe('PQuy trình3'); // không có văn bản ẩn nào khác trong nút: tên truy cập vẫn là "P" (huy hiệu aria-hidden)
  expect(p.querySelector('[data-testid="nav-approvals"]')?.textContent).toBe('3');
  expect(p.querySelector('.n')?.getAttribute('aria-hidden')).toBe('true');
  expect(document.getElementById(p.getAttribute('aria-describedby')!)?.textContent).toBe('3 yêu cầu chờ bạn duyệt');
  const bell = container.querySelector<HTMLButtonElement>('button[aria-label="Hộp duyệt"]')!;
  expect(bell.querySelector('[data-testid="bell-dot"]')).toBeTruthy();
  expect(document.getElementById(bell.getAttribute('aria-describedby')!)?.textContent).toContain('đang chờ');
});

it('refreshes after a successful write and hides counts when nothing waits', async () => {
  inbox = () => ok({ counts: { approval: 1 }, items: [{ ref: 'stock_request:r1', kind: 'approval', title: 'PN-1', state: 'in_review' }], next_cursor: null });
  await mount('#/P');
  expect(navButton('P').querySelector('.n')?.textContent).toBe('1');
  inbox = () => ok({ counts: { approval: 0, task: 0, notification: 0 }, items: [], next_cursor: null });
  const click = async (text: string) => { const b = Array.from(container.querySelectorAll('button')).find(x => x.textContent === text)!; expect(b, text).toBeTruthy(); await act(async () => b.click()); };
  await click('Mở PN-1'); await click('Duyệt');
  expect(inboxCalls()).toHaveLength(2);
  expect(navButton('P').querySelector('.n')).toBeNull();
  expect(container.querySelector('[data-testid="bell-dot"]')).toBeNull();
});

it('does not call get_inbox when the server lacks it, and shows nothing on errors or stale replies', async () => {
  toolNames = toolNames.filter(n => n !== 'get_inbox');
  await mount();
  expect(vi.mocked(connection.client.callTool).mock.calls.some(c => (c[0] as Call).name === 'get_inbox')).toBe(false);
  expect(container.querySelector('.nav .n, [data-testid="bell-dot"]')).toBeNull();
  await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = '';
  toolNames = [...toolNames, 'get_inbox'];
  inbox = () => ({ isError: true, structuredContent: { ok: false, error: { code: 'access_denied', message: 'Không có quyền' } } });
  await mount();
  expect(container.querySelector('.nav .n, [data-testid="bell-dot"]')).toBeNull();
  expect(container.querySelector('[role="alert"]')).toBeNull();
});

it('drops a late inbox reply after logout so counts never leak into the next session', async () => {
  let finish!: (r: ToolResult) => void;
  inbox = () => new Promise(resolve => { finish = resolve; });
  await mount();
  await act(async () => { Array.from(container.querySelectorAll('button')).find(b => b.textContent === 'Đăng xuất')!.click(); });
  await act(async () => finish(ok({ counts: { approval: 9 }, items: [], next_cursor: null })));
  expect(container.textContent).not.toContain('9');
  expect(container.querySelector('.n, [data-testid="bell-dot"]')).toBeNull();
});
