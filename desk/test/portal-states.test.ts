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
import { PortalBoundary, PortalPending } from '../src/PortalBoundary';
import { ToolError, readToolResult } from '../src/portal-result';
import { classifyError } from '../src/portal-state';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
const fail = (code: string, message: string, extra: Record<string, unknown> = {}): ToolResult => ({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, error: { code, message, ...extra } }) }] });
let root: Root | undefined;
let container: HTMLDivElement;
async function mount(area: string, caller: (r: { name: string; arguments: Record<string, unknown> }) => Promise<ToolResult>, names = ['inspect_workspace', 'find_people', 'search_records', 'get_record', 'get_inbox', 'decide_request']) {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  const tools = names.map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  const mcp = { client: { callTool: vi.fn(caller) }, tools } as unknown as McpConnection;
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area, mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  return mcp;
}
async function click(text: string) {
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === text);
  expect(button, `button ${text}`).toBeTruthy();
  await act(async () => button!.click());
}
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });

it('keeps the error code and request id from a failed tool result', () => {
  try { readToolResult(fail('access_denied', 'Không được xem', { details: { request_id: 'req-42' } })); expect.unreachable(); }
  catch (e) {
    expect(e).toBeInstanceOf(ToolError);
    expect((e as ToolError).code).toBe('access_denied');
    expect((e as ToolError).message).toBe('Không được xem (mã yêu cầu: req-42)');
  }
  expect(() => readToolResult({ isError: true, _meta: { 'x-request-id': 'abc' }, content: [{ type: 'text', text: JSON.stringify({ error: { code: 'x', message: 'Lỗi' } }) }] })).toThrow('Lỗi (mã yêu cầu: abc)');
  expect(() => readToolResult({ isError: true, content: [{ type: 'text', text: JSON.stringify({ ok: false, meta: { trace_id: 'tr_01' }, error: { code: 'x', message: 'Lỗi envelope' } }) }] })).toThrow('Lỗi envelope (mã yêu cầu: tr_01)');
  expect(() => readToolResult(fail('x', 'Lỗi thường'))).toThrow(/^Lỗi thường$/);
});

it('classifies forbidden, expired and generic errors', () => {
  expect(classifyError(new ToolError('m', 'access_denied', ''))).toBe('forbidden');
  expect(classifyError(new ToolError('m', 'unauthenticated', ''))).toBe('expired');
  expect(classifyError(new ToolError('m', 'rule_violation', ''))).toBe('error');
  expect(classifyError(new Error('Error POSTing to endpoint (HTTP 401): invalid_token'))).toBe('expired');
  expect(classifyError(new Error('network down'))).toBe('error');
});

it('shows a forbidden state with the request id instead of fake data', async () => {
  await mount('H', async (call) => call.name === 'find_people' ? fail('access_denied', 'Không có quyền person.read', { details: { request_id: 'req-9' } }) : ok({ me: { name: 'Lan' }, models: [] }));
  const alert = container.querySelector('[role="alert"]')!;
  expect(alert.getAttribute('data-state')).toBe('forbidden');
  expect(alert.textContent).toContain('Thiếu quyền');
  expect(alert.textContent).toContain('req-9');
  expect(container.textContent).not.toContain('Không có kết quả');
});

it('shows an expired-session state and does not retry', async () => {
  const mcp = await mount('P', async (call) => call.name === 'get_inbox' ? fail('unauthenticated', 'Token hết hạn') : ok({ models: [] }));
  const alert = container.querySelector('[role="alert"]')!;
  expect(alert.getAttribute('data-state')).toBe('expired');
  expect(alert.textContent).toContain('đăng nhập lại');
  expect(vi.mocked(mcp.client.callTool).mock.calls.filter((c) => c[0].name === 'get_inbox')).toHaveLength(1);
});

it('reports a missing tool as forbidden', async () => {
  await mount('H', async () => ok({ models: [] }), ['inspect_workspace']);
  expect(container.querySelector('[role="alert"]')?.getAttribute('data-state')).toBe('forbidden');
});

it('collects the rejection reason in a labelled textarea, never window.prompt', async () => {
  const prompt = vi.spyOn(window, 'prompt');
  const request = { ref: 'stock_request:one', title: 'Nhập vật tư', state: 'in_review', revision: 2, values: { code: 'PN-1' } };
  const mcp = await mount('P', async (call) => {
    if (call.name === 'get_inbox') return ok({ items: [{ ref: request.ref, title: request.title, kind: 'approval' }] });
    if (call.name === 'get_record') return ok({ record: request });
    if (call.name === 'decide_request') return ok({ request: { ...request, state: 'rejected', revision: 3 } });
    return ok({ models: [] });
  });
  await click('Mở Nhập vật tư');
  await click('Từ chối');
  const area = container.querySelector<HTMLTextAreaElement>('textarea#reject-reason')!;
  expect(container.querySelector('label[for="reject-reason"]')?.textContent).toBe('Lý do từ chối');
  await act(async () => container.querySelector('form:has(#reject-reason)')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('Nhập lý do từ chối');
  expect(vi.mocked(mcp.client.callTool).mock.calls.some((c) => c[0].name === 'decide_request')).toBe(false);
  await act(async () => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!.call(area, '  Thiếu chứng từ  '); area.dispatchEvent(new Event('input', { bubbles: true })); });
  await act(async () => container.querySelector('form:has(#reject-reason)')!.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));
  const decide = vi.mocked(mcp.client.callTool).mock.calls.map((c) => c[0]).find((c) => c.name === 'decide_request')!;
  expect(decide.arguments).toMatchObject({ ref: request.ref, decision: 'reject', reason: 'Thiếu chứng từ', expected_revision: 2 });
  expect(prompt).not.toHaveBeenCalled();
});

it('isolates a crashing module and lets the user retry', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  vi.spyOn(console, 'error').mockImplementation(() => {});
  let broken = true;
  const Child = () => { if (broken) throw new Error('hỏng module'); return createElement('p', null, 'đã phục hồi'); };
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(createElement('div', null, createElement('nav', null, 'khung điều hướng'), createElement(PortalBoundary, { label: 'D' }, createElement(Child)))); });
  expect(container.querySelector('nav')?.textContent).toBe('khung điều hướng');
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('hỏng module');
  broken = false;
  await click('Thử lại');
  expect(container.textContent).toContain('đã phục hồi');
});

it('renders connecting and connection-failed panels', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(createElement(PortalPending, { label: 'H', error: '' })); });
  expect(container.querySelector('[role="status"]')?.textContent).toContain('Đang kết nối');
  await act(async () => { root!.render(createElement(PortalPending, { label: 'H', error: 'Không kết nối được MCP: 502' })); });
  expect(container.querySelector('[role="alert"]')?.textContent).toContain('502');
});
