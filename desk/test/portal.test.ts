// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { afterEach, expect, it, vi } from 'vitest';
import { createPortalBridge, studioPath } from '../src/portal';
import { readToolResult } from '../src/portal-result';

afterEach(() => vi.unstubAllGlobals());
function origin() { vi.stubGlobal('location', { origin: 'https://xt.test' }); }
it('passes through JSON and error status as Response with request-bound auth', async () => {
  origin();
  const auth = vi.fn(async () => ({ Authorization: 'DPoP scoped' }));
  const fetchMock = vi.fn(async (_url: string, _init: RequestInit) => new Response(JSON.stringify({ code: 'forbidden' }), { status: 403, headers: { 'content-type': 'application/json' } }));
  vi.stubGlobal('fetch', fetchMock);
  const response = await createPortalBridge(auth, null).request('/studio-api/apps?limit=5', { method: 'POST' });
  expect(response.ok).toBe(false);
  expect(response.status).toBe(403);
  await expect(response.json()).resolves.toEqual({ code: 'forbidden' });
  expect(auth).toHaveBeenCalledWith({ method: 'POST', url: 'https://xt.test/studio-api/apps?limit=5' });
  expect(fetchMock.mock.calls[0][1].credentials).toBe('same-origin');
});
it('preserves NDJSON streaming and aborts the body on logout', async () => {
  origin();
  let push: ReadableStreamDefaultController<Uint8Array> | undefined;
  const body = new ReadableStream<Uint8Array>({ start(controller) { push = controller; } });
  const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
    init.signal?.addEventListener('abort', () => push?.error(new DOMException('Aborted','AbortError')));
    return new Response(body, { headers: { 'content-type': 'application/x-ndjson' } });
  });
  vi.stubGlobal('fetch', fetchMock);
  const bridge = createPortalBridge(async () => ({}), null);
  const response = await bridge.request('/studio-api/apps/run');
  const reader = response.body!.getReader();
  push!.enqueue(new TextEncoder().encode('{"part":1}\n'));
  expect(new TextDecoder().decode((await reader.read()).value)).toBe('{"part":1}\n');
  bridge.invalidate();
  await expect(reader.read()).rejects.toThrow();
  await expect(bridge.request('/studio-api/apps')).rejects.toThrow('Phiên');
});
it('rejects path normalization and encoded separators before auth', () => {
  for (const path of ['/studio-api/../mcp','/studio-api/%2Fsecret','/studio-api/%2esecret','/studio-api//apps','https://evil.test/studio-api/apps','/studio-api/apps#x']) {
    expect(() => studioPath(path,'https://xt.test')).toThrow();
  }
});
it('preserves core errors, including confirmation payloads', () => {
  const envelope = { error: { code:'confirmation_required', message:'Confirm', details:{confirm_url:'https://xt.test/desk/confirm/one'} } };
  expect(() => readToolResult({ isError:true, structuredContent:envelope })).toThrow('Confirm');
  expect(() => readToolResult({ isError:true, content:[{type:'text',text:JSON.stringify(envelope)}] })).toThrow('Confirm');
});
it('does not send a request when logout happens while authorization is pending', async () => {
  origin();
  let release!: (headers: Record<string,string>) => void;
  const pending = new Promise<Record<string,string>>(resolve => { release = resolve; });
  const fetchMock = vi.fn(); vi.stubGlobal('fetch',fetchMock);
  const bridge = createPortalBridge(() => pending, null);
  const request = bridge.request('/studio-api/apps');
  bridge.invalidate(); release({ Authorization:'late' });
  await expect(request).rejects.toThrow('Phiên');
  expect(fetchMock).not.toHaveBeenCalled();
});
it('uses caller abort signal for a Studio request', async () => {
  origin();
  const fetchMock = vi.fn(async (_url:string, init:RequestInit) => {
    expect(init.signal?.aborted).toBe(false);
    return new Response('{}');
  });
  vi.stubGlobal('fetch',fetchMock);
  const caller = new AbortController();
  await createPortalBridge(async () => ({}),null).request('/studio-api/apps',{signal:caller.signal});
  expect(fetchMock).toHaveBeenCalledOnce();
});
it('converts exact decimal quantities without floating point and enforces warehouse bounds', async () => {
  const { parseMilli } = await import('../src/portal-result');
  expect(parseMilli('1.001')).toBe(1001);
  expect(parseMilli('0.001')).toBe(1);
  expect(parseMilli('1.23')).toBe(1230);
  expect(parseMilli('0',true)).toBe(0);
  expect(parseMilli('9000000000000')).toBe(9_000_000_000_000_000);
  for (const value of ['0','1.0001','1e3','-1','NaN','9000000000000.001','9007199254740992','']) expect(parseMilli(value)).toBeNull();
});
it('discards MCP data when the Studio bridge is invalidated during the call', async () => {
  let finish!: (value:unknown)=>void;
  const pending = new Promise(resolve=>{finish=resolve;});
  const bridge=createPortalBridge(async()=>({}),{client:{callTool:()=>pending}} as unknown as import('../src/mcp').McpConnection);
  const call=bridge.callTool('inspect_workspace',{});
  bridge.invalidate();finish({structuredContent:{me:{name:'Prior account'}}});
  await expect(call).rejects.toThrow('Phiên');
});
