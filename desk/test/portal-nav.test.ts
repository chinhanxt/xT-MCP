// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool } from '../src/types';
vi.mock('../src/config', () => ({ config: { xtOrigin: 'https://xt.test', appGlobalCallsPerSec: 10, appGlobalBurst: 10 } }));
import { Portal } from '../src/Portal';
import { AREAS, areaFromHash, hashFor, useAreaRoute, type Area } from '../src/portal-nav';

let root: Root | undefined;
let container: HTMLDivElement;
let api: { area: Area; navigate: (a: Area) => void; reset: () => void };
function Probe() { const [area, navigate, reset] = useAreaRoute(); api = { area, navigate, reset }; return createElement('span', null, area); }
async function mountProbe(hash = '') {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  history.replaceState(null, '', `/${hash}`);
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Probe)); });
}
const settle = () => act(async () => { await new Promise((r) => setTimeout(r, 20)); });
afterEach(async () => { if (root) await act(async () => root!.unmount()); root = undefined; document.body.innerHTML = ''; history.replaceState(null, '', '/'); vi.restoreAllMocks(); });

it('maps hashes to areas and ignores anything unknown', () => {
  for (const area of AREAS) expect(areaFromHash(hashFor(area))).toBe(area);
  expect(hashFor('Desk')).toBe('');
  for (const bad of ['#/admin', '#/h', '#/../x', '#/H/extra', '#javascript:alert(1)']) expect(areaFromHash(bad)).toBe('Desk');
});

it('opens the area in the hash, pushes history on navigate and follows the browser Back button', async () => {
  await mountProbe('#/D');
  expect(api.area).toBe('D');
  await act(async () => api.navigate('P'));
  expect(api.area).toBe('P');
  expect(location.hash).toBe('#/P');
  await act(async () => api.navigate('Kho'));
  expect(location.hash).toBe('#/Kho');
  await act(async () => { history.back(); }); await settle();
  expect(api.area).toBe('P');
  await act(async () => { history.back(); }); await settle();
  expect(api.area).toBe('D');
  await act(async () => { history.forward(); }); await settle();
  expect(api.area).toBe('P');
});

it('does not stack duplicate entries and reset clears the hash without a new history entry', async () => {
  await mountProbe('');
  const push = vi.spyOn(history, 'pushState'), replace = vi.spyOn(history, 'replaceState');
  await act(async () => api.navigate('H')); await act(async () => api.navigate('H'));
  expect(push).toHaveBeenCalledTimes(1);
  replace.mockClear();
  await act(async () => api.reset());
  expect(api.area).toBe('Desk');
  expect(location.hash).toBe('');
  expect(location.pathname).toBe('/');
  expect(push).toHaveBeenCalledTimes(1);
  expect(replace).toHaveBeenCalledTimes(1);
});

it('I space offers the Desk assistant and keeps Studio in the same-origin frame', async () => {
  (globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  const onNavigate = vi.fn();
  const mcp = { client: { callTool: vi.fn() }, tools: [] as McpTool[] } as unknown as McpConnection;
  container = document.createElement('div'); document.body.append(container);
  root = createRoot(container);
  await act(async () => { root!.render(createElement(Portal, { area: 'I', mcp, tools: [], sandboxUrl: new URL('https://sandbox.test'), onOpenLink: () => true, revealDpop: undefined, onNavigate })); });
  expect(container.querySelector('iframe[title="xT Studio"]')?.getAttribute('src')).toBe('/studio/?integrated=1');
  expect(container.querySelector('h2')?.getAttribute('tabindex')).toBe('-1');
  const button = Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Mở trợ lý Desk')!;
  await act(async () => button.click());
  expect(onNavigate).toHaveBeenCalledWith('Desk');
});
