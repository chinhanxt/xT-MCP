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
import { BRIDGE_READ_TOOLS, createPortalBridge } from '../src/portal';

const ok = (data: unknown): ToolResult => ({ content: [{ type: 'text', text: JSON.stringify({ ok: true, data }) }] });
let root: Root | undefined;
let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; vi.restoreAllMocks(); });
function connection() {
  const callTool = vi.fn(async () => ok({ items: [], next_cursor: null }));
  return { mcp: { client: { callTool }, tools: [] as McpTool[] } as unknown as McpConnection, callTool };
}

it('embeds WareTrack same-origin in Kho and removes it when the session ends', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const { mcp } = connection();
  const tools = ['search_records', 'create_record', 'submit_request'].map((name) => ({ name, inputSchema: { type: 'object' } })) as McpTool[];
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  const render = (area: string) => act(async () => { root!.render(createElement(Portal, { area, mcp, tools, sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined })); });
  await render('Kho');
  const frame = container.querySelector<HTMLIFrameElement>('iframe[title="xT WareTrack"]')!;
  expect(frame.getAttribute('src')).toBe('/waretrack/?integrated=1');
  expect(container.querySelector('.portal-waretrack')?.textContent).toContain('không đổi tồn kho');
  expect(container.querySelector('a[href="/waretrack/"]')?.getAttribute('rel')).toBe('noopener noreferrer');
  await render('H');
  expect(container.querySelector('iframe[title="xT WareTrack"]')).toBeNull();
});

it('bridge exposes only read tools to embedded frames and never reaches MCP for writes (U11)', async () => {
  const { mcp, callTool } = connection();
  const bridge = createPortalBridge(async () => ({}), mcp);
  for (const name of ['create_record', 'submit_request', 'decide_request', 'update_record', 'transition_record', 'create_task', 'delete_record', 'describe_model', 'find_people', 'get_inbox']) {
    await expect(bridge.callTool(name, { model_id: 'stock_request' }), name).rejects.toThrow('Công cụ không được phép');
  }
  expect(callTool).not.toHaveBeenCalled();
  expect(BRIDGE_READ_TOOLS).toEqual(['search_records', 'get_record', 'aggregate_records', 'get_history', 'inspect_workspace']);
  await expect(bridge.callTool('search_records', { model_id: 'stock_balance' })).resolves.toBeTruthy();
  await expect(bridge.callTool('aggregate_records', { model_id: 'stock_level', metrics: ['count'] })).resolves.toBeTruthy();
  await expect(bridge.callTool('get_record', { ref: 'stock_request:1' })).resolves.toBeTruthy();
  expect(callTool).toHaveBeenCalledTimes(3);
});

it('bridge refuses non-warehouse models for search and aggregate, and everything after logout', async () => {
  const { mcp, callTool } = connection();
  const bridge = createPortalBridge(async () => ({}), mcp);
  await expect(bridge.callTool('search_records', { model_id: 'advance_request' })).rejects.toThrow('Chỉ hỗ trợ dữ liệu kho');
  await expect(bridge.callTool('aggregate_records', { model_id: 'person' })).rejects.toThrow('Chỉ hỗ trợ dữ liệu kho');
  await expect(bridge.callTool('search_records', {})).rejects.toThrow('Chỉ hỗ trợ dữ liệu kho');
  expect(callTool).not.toHaveBeenCalled();
  bridge.invalidate();
  await expect(bridge.callTool('search_records', { model_id: 'stock_balance' })).rejects.toThrow();
  expect(callTool).not.toHaveBeenCalled();
});
