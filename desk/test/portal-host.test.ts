// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import { createPortalBridge, createPortalHost, type StudioBridge, type WareTrackBridge } from '../src/portal';

afterEach(() => { document.body.innerHTML = ''; vi.unstubAllGlobals(); });
function frame(scope?: string) {
  const el = document.createElement('iframe');
  if (scope) el.dataset.xtScope = scope;
  document.body.append(el);
  return el;
}
function setup() {
  vi.stubGlobal('location', { origin: 'https://xt.test' });
  const callTool = vi.fn(async () => ({ structuredContent: { ok: true, data: { items: [] } } }));
  const fetchMock = vi.fn(async () => new Response('{}'));
  vi.stubGlobal('fetch', fetchMock);
  const mcp = { client: { callTool }, tools: [] } as unknown as McpConnection;
  const host = createPortalHost(createPortalBridge(async () => ({}), mcp));
  return { host, callTool, fetchMock };
}

it('does not expose request or callTool on the shared window object', () => {
  const { host } = setup();
  expect(Object.keys(host).sort()).toEqual(['connect', 'invalidate']);
  expect((host as unknown as Record<string, unknown>).request).toBeUndefined();
  expect((host as unknown as Record<string, unknown>).callTool).toBeUndefined();
});

it('gives the WareTrack frame read tools only and no Studio proxy', async () => {
  const { host, callTool, fetchMock } = setup();
  const ware = frame('waretrack'); frame('studio');
  const bridge = host.connect(ware.contentWindow!) as WareTrackBridge;
  expect(bridge.scope).toBe('waretrack');
  expect(Object.keys(bridge).sort()).toEqual(['callTool', 'scope']);
  expect((bridge as unknown as Record<string, unknown>).request).toBeUndefined();
  await bridge.callTool('search_records', { model_id: 'stock_balance' });
  expect(callTool).toHaveBeenCalledTimes(1);
  await expect(bridge.callTool('create_record', { model_id: 'stock_request' })).rejects.toThrow('Công cụ không được phép');
  expect(callTool).toHaveBeenCalledTimes(1);
  expect(fetchMock).not.toHaveBeenCalled();
  expect(Object.isFrozen(bridge)).toBe(true);
});

it('gives the Studio frame the proxy only and no MCP tools', async () => {
  const { host, callTool, fetchMock } = setup();
  frame('waretrack'); const studio = frame('studio');
  const bridge = host.connect(studio.contentWindow!) as StudioBridge;
  expect(bridge.scope).toBe('studio');
  expect(Object.keys(bridge).sort()).toEqual(['request', 'scope']);
  expect((bridge as unknown as Record<string, unknown>).callTool).toBeUndefined();
  await bridge.request('/studio-api/apps');
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(callTool).not.toHaveBeenCalled();
});

it('refuses the top window, unknown windows and frames without a known scope', () => {
  const { host } = setup();
  const unscoped = frame(); const odd = frame('admin'); const other = document.createElement('iframe');
  expect(host.connect(window)).toBeNull();
  expect(host.connect(unscoped.contentWindow!)).toBeNull();
  expect(host.connect(odd.contentWindow!)).toBeNull();
  expect(host.connect(other.contentWindow as unknown as Window)).toBeNull();
  expect(host.connect(null as unknown as Window)).toBeNull();
});

it('a detached frame loses its bridge, and invalidate (logout) kills issued and future bridges', async () => {
  const { host, callTool } = setup();
  const ware = frame('waretrack');
  const issued = host.connect(ware.contentWindow!) as WareTrackBridge;
  const win = ware.contentWindow!;
  ware.remove();
  expect(host.connect(win)).toBeNull();
  const again = frame('waretrack');
  const second = host.connect(again.contentWindow!) as WareTrackBridge;
  host.invalidate();
  await expect(issued.callTool('search_records', { model_id: 'stock_balance' })).rejects.toThrow();
  await expect(second.callTool('search_records', { model_id: 'stock_balance' })).rejects.toThrow();
  expect(host.connect(again.contentWindow!)).toBeNull();
  expect(callTool).not.toHaveBeenCalled();
});
