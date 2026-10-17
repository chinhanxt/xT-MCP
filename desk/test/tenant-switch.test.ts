// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { webcrypto } from 'node:crypto';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { OidcSession } from '../src/oidc';
import type { McpConnection } from '../src/mcp';
import type { StudioBridge, WareTrackBridge } from '../src/portal';
import type { ToolResult } from '../src/types';

const mocks = vi.hoisted(() => ({ createSession: vi.fn(), connect: vi.fn(), complete: vi.fn(), config: {
  model: 'test', toolProfile: 'all', maxSteps: 2, coreSkill: '', reasoningEffort: '',
  sandboxUrl: 'https://sandbox.test', xtOrigin: 'https://xt.test', oidcClientId: 'desk',
  oidcOrganizations: [] as string[], appGlobalCallsPerSec: 10, appGlobalBurst: 10,
} }));
vi.mock('../src/config', () => ({ config: mocks.config, MCP_PATH: '/mcp', LLM_PATH: '/llm' }));
vi.mock('../src/auth', async () => ({ ...(await vi.importActual('../src/oidc')), CALLBACK_PATH: '/auth/callback', createSession: mocks.createSession }));
vi.mock('../src/mcp', () => ({ connectMcp: mocks.connect }));
vi.mock('../src/llm', () => ({ createLlm: () => ({ complete: mocks.complete }) }));
import { App } from '../src/App';

const issuer = 'https://kc.test/realms/xt';
const result = (data: unknown): ToolResult => ({ structuredContent: { ok: true, data } });
const token = (organization: unknown) => `e30.${btoa(JSON.stringify({ organization }))}.sig`;
let root: Root | undefined;
let container: HTMLDivElement;
let session: OidcSession;
let nav: string[];
let currentOrg: string;
let connections: McpConnection[];
let now: number;
let organizationClaim: unknown;
const tools = ['inspect_workspace', 'find_people', 'search_records', 'get_inbox', 'create_record'].map(name => ({ name, inputSchema: { type: 'object', properties: name === 'create_record' ? { idempotency_key: { type: 'string' } } : {} } }));

beforeEach(async () => {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true);
  vi.stubGlobal('crypto', webcrypto);
  vi.stubGlobal('requestAnimationFrame', (fn: FrameRequestCallback) => { fn(0); return 0; });
  mocks.config.oidcOrganizations = ['alpha', 'beta'];
  nav = []; currentOrg = 'alpha'; connections = []; now = Date.now(); organizationClaim = undefined;
  sessionStorage.clear(); history.replaceState(null, '', '/');
  session = new OidcSession({ issuer, clientId: 'desk', scope: 'openid organization', redirectUri: `${location.origin}/auth/callback`, postLogoutRedirectUri: `${location.origin}/` }, {
    session: sessionStorage, now: () => now, navigate: url => { nav.push(url); },
    fetch: vi.fn(async url => String(url).endsWith('openid-configuration')
      ? new Response(JSON.stringify({ issuer, authorization_endpoint: `${issuer}/auth`, token_endpoint: `${issuer}/token`, end_session_endpoint: `${issuer}/logout` }))
      : new Response(JSON.stringify({ access_token: token(organizationClaim ?? { [currentOrg]: {} }), expires_in: 300 }))) as typeof fetch,
  });
  mocks.createSession.mockReturnValue(session);
  mocks.connect.mockImplementation(async () => {
    const name = currentOrg;
    const connection = { tools, client: {
      getInstructions: () => '', close: vi.fn(async () => {}),
      callTool: vi.fn(async ({ name: tool }: { name: string }) => tool === 'inspect_workspace'
        ? result({ tenant: { name }, me: { name: `${name} user` }, models: [] })
        : result(tool === 'find_people' ? { people: [{ name: `${name} secret`, email: `${name}@private.test` }], next_cursor: null } : { items: [], next_cursor: null })),
    } } as unknown as McpConnection;
    connections.push(connection); return connection;
  });
  mocks.complete.mockResolvedValue({ content: 'answer', tool_calls: [] });
  await login();
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount()); root = undefined;
  document.body.innerHTML = ''; delete window.xtPortal; sessionStorage.clear();
  vi.clearAllMocks(); vi.unstubAllGlobals();
});
async function login() {
  await session.startLogin();
  const state = new URL(nav.at(-1)!).searchParams.get('state');
  await session.handleCallback(`${location.origin}/auth/callback?code=code&state=${state}`);
}
async function mount(area = 'H') {
  history.replaceState(null, '', `/#/${area}`);
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
  await act(async () => { root!.render(createElement(App)); });
}
async function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find(b => b.childNodes[0]?.textContent === text);
  expect(button, text).toBeTruthy(); await act(async () => button!.click());
}
async function switchToBeta() {
  const picker = container.querySelector<HTMLSelectElement>('select[aria-label="Doanh nghiệp"]');
  expect(picker, 'tenant picker').toBeTruthy();
  await act(async () => { picker!.value = 'beta'; picker!.dispatchEvent(new Event('change', { bubbles: true })); });
  await session.loggingOut;
}
async function finishSwitch() {
  currentOrg = 'beta';
  await act(async () => {
    await session.resumeTenantSwitch();
    const state = new URL(nav.at(-1)!).searchParams.get('state');
    await session.handleCallback(`${location.origin}/auth/callback?code=beta&state=${state}`);
  });
}

it('hides the picker without multiple real or configured organizations', async () => {
  mocks.config.oidcOrganizations = [];
  await mount();
  expect(container.querySelector('select[aria-label="Doanh nghiệp"]')).toBeNull();
});

it('switches using full logout, clears the route and remounts real Portal without prior tenant data', async () => {
  await mount();
  expect(container.textContent).toContain('alpha@private.test');
  const oldInput = container.querySelector<HTMLInputElement>('[aria-label="Tìm nhân sự"]')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(oldInput, 'old private query'); oldInput.dispatchEvent(new Event('input', { bubbles: true })); });
  await switchToBeta();
  expect(location.hash).toBe('');
  expect(container.textContent).not.toContain('alpha@private.test');
  expect(container.querySelector('.portal')).toBeNull();
  expect(new URL(nav.at(-1)!).pathname).toBe('/realms/xt/logout');
  expect(connections[0].client.close).toHaveBeenCalled();
  await finishSwitch();
  expect(new URL(nav.at(-1)!).searchParams.get('scope')).toContain('organization:beta');
  await click('H');
  expect(container.textContent).toContain('beta@private.test');
  expect(container.querySelector('.portal')!.textContent).not.toContain('alpha');
  expect(container.querySelector('[aria-label="Tìm nhân sự"]')).not.toBe(oldInput);
  expect(container.querySelector<HTMLInputElement>('[aria-label="Tìm nhân sự"]')!.value).toBe('');
});

it.each(['studio', 'waretrack'] as const)('invalidates issued %s frame bridges when switching', async scope => {
  await mount(scope === 'studio' ? 'I' : 'T3');
  if (scope === 'waretrack') await click('Mở Kho hàng (WMS)');
  const frame = container.querySelector<HTMLIFrameElement>(`iframe[data-xt-scope="${scope}"]`)!;
  expect(frame).toBeTruthy();
  const host = window.xtPortal!;
  const issued = host.connect(frame.contentWindow!)!;
  expect(issued).toBeTruthy();
  await switchToBeta();
  expect(host.connect(frame.contentWindow!)).toBeNull();
  if (scope === 'studio') await expect((issued as StudioBridge).request('/studio-api/apps')).rejects.toThrow();
  else await expect((issued as WareTrackBridge).callTool('inspect_workspace', {})).rejects.toThrow();
  await finishSwitch();
  expect(window.xtPortal).not.toBe(host);
});

it('drops late assistant output and resets chat after a switch', async () => {
  let finish!: (reply: { content: string; tool_calls: [] }) => void;
  mocks.complete.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount('Desk');
  const input = container.querySelector<HTMLInputElement>('#box')!;
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, 'private question'); input.dispatchEvent(new Event('input', { bubbles: true })); });
  await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  await switchToBeta();
  await finishSwitch();
  await act(async () => finish({ content: 'alpha confidential answer', tool_calls: [] }));
  expect(container.textContent).not.toContain('private question');
  expect(container.textContent).not.toContain('confidential');
  expect(container.querySelector<HTMLInputElement>('#box')!.value).toBe('');
  expect(container.querySelector('.thinking')).toBeNull();
});


it('uses multiple organization aliases already present in the token without configured choices', async () => {
  mocks.config.oidcOrganizations = [];
  organizationClaim = ['alpha', 'beta'];
  await login();
  await mount('Desk');
  expect(Array.from(container.querySelectorAll('select[aria-label="Doanh nghiệp"] option')).map(option => option.getAttribute('value'))).toEqual(['', 'alpha', 'beta']);
});

it('clears Portal and issued bridges when the token expires without a usable refresh token', async () => {
  await mount('I');
  const frame = container.querySelector<HTMLIFrameElement>('iframe')!;
  const host = window.xtPortal!;
  const issued = host.connect(frame.contentWindow!) as StudioBridge;
  now += 400_000;
  await act(async () => { await expect(session.authHeaders()).rejects.toThrow(); });
  expect(container.querySelector('.portal')).toBeNull();
  expect(location.hash).toBe('');
  expect(container.querySelector('#login')).toBeTruthy();
  await expect(issued.request('/studio-api/apps')).rejects.toThrow();
  expect(connections[0].client.close).toHaveBeenCalled();
});

it('closes a late prior MCP connection without replacing the new tenant connection', async () => {
  let finish!: (connection: McpConnection) => void;
  mocks.connect.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  await mount('H');
  await switchToBeta();
  await finishSwitch();
  const stale = { tools, client: { close: vi.fn(async () => {}), getInstructions: () => 'alpha private instructions' } } as unknown as McpConnection;
  await act(async () => finish(stale));
  expect(stale.client.close).toHaveBeenCalled();
  await click('H');
  expect(container.textContent).toContain('beta@private.test');
  expect(container.querySelector('.portal')!.textContent).not.toContain('alpha');
});

it('starts new idempotency keys and chat history for the same alias in the new tenant', async () => {
  await mount('Desk');
  async function ask(question: string) {
    mocks.complete.mockResolvedValueOnce({ content: '', tool_calls: [{ id: 'write', type: 'function', function: { name: 'create_record', arguments: JSON.stringify({ model_id: 'document', values: {}, idempotency_key: 'same-alias' }) } }] });
    const input = container.querySelector<HTMLInputElement>('#box')!;
    await act(async () => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, question); input.dispatchEvent(new Event('input', { bubbles: true })); });
    await act(async () => { container.querySelector('form')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })); });
  }
  await ask('alpha private question');
  const oldWrite = vi.mocked(connections[0].client.callTool).mock.calls.find(([call]) => call.name === 'create_record')![0];
  await switchToBeta();
  await finishSwitch();
  await ask('beta question');
  const newWrite = vi.mocked(connections[1].client.callTool).mock.calls.find(([call]) => call.name === 'create_record')![0];
  expect(newWrite.arguments!.idempotency_key).toEqual(expect.any(String));
  expect(newWrite.arguments!.idempotency_key).not.toBe(oldWrite.arguments!.idempotency_key);
  expect(JSON.stringify(mocks.complete.mock.calls[2][0].messages)).not.toContain('alpha private question');
  expect(container.textContent).not.toContain('alpha private question');
});

it('does not offer invalid configured aliases that could interrupt logout before clearing tokens', async () => {
  mocks.config.oidcOrganizations = ['alpha', 'bad org', '*', 'organization:beta'];
  await mount('Desk');
  expect(container.querySelector('select[aria-label="Doanh nghiệp"]')).toBeNull();
});

it('automatically restarts targeted PKCE when Desk mounts after the logout redirect', async () => {
  await mount('I');
  await switchToBeta();
  await act(async () => root!.unmount());
  root = undefined; container.remove();
  await mount('Desk');
  await vi.waitFor(() => expect(new URL(nav.at(-1)!).pathname).toBe('/realms/xt/auth'));
  const url = new URL(nav.at(-1)!);
  expect(url.pathname).toBe('/realms/xt/auth');
  expect(url.searchParams.get('scope')).toContain('organization:beta');
  expect(url.searchParams.get('code_challenge_method')).toBe('S256');
  expect(container.textContent).toContain('Đang đăng nhập');
});
