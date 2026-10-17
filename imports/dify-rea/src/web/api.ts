// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { readLines } from '../streams.ts';
import type { RunResult, StreamEvent } from '../contracts.ts';
const integrated = new URLSearchParams(location.search).get('integrated') === '1';
type StudioBridge = { request: (path: string, init?: RequestInit) => Promise<Response> };
type XtPortalHost = { connect?: (win: Window) => Partial<StudioBridge> | null };
// Khung Studio nhận cầu nối riêng (chỉ `request`) từ cổng: parent.xtPortal.connect(window).
function portalBridge(): StudioBridge | null {
  try {
    const host = (window.parent as Window & { xtPortal?: XtPortalHost }).xtPortal;
    const bridge = host?.connect?.(window);
    return typeof bridge?.request === 'function' ? (bridge as StudioBridge) : null;
  } catch {
    return null;
  }
}
function request(path: string, init: RequestInit): Promise<Response> {
  if (!integrated) return fetch(path, init);
  const portal = portalBridge();
  if (!portal) throw new Error('Cổng xT chưa sẵn sàng.');
  if (!path.startsWith('/api/')) throw new Error('Đường dẫn Studio không hợp lệ.');
  return portal.request('/studio-api/' + path.slice(5), init);
}
export async function api<T = any>(path: string, method = 'GET', data?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await request('/api' + path, {
    method,
    headers: data === undefined ? {} : { 'Content-Type': 'application/json' },
    body: data === undefined ? undefined : JSON.stringify(data),
    signal,
  });
  const value = await response.json();
  if (!response.ok) throw Object.assign(new Error(value.error || 'Yêu cầu thất bại.'), { trace: value.trace });
  return value as T;
}
export async function streamRun(path: string, data: unknown, delta: (part: string) => void, signal?: AbortSignal): Promise<RunResult> {
  const response = await request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...(data as object), stream: true }),
    signal,
  });
  if (!response.ok) {
    const value = await response.json();
    throw Object.assign(new Error(value.error || 'Yêu cầu thất bại.'), { trace: value.trace });
  }
  if (!response.body) throw new Error('Phản hồi trống.');
  let result: RunResult | undefined;
  for await (const line of readLines(response.body)) {
    if (!line) continue;
    const event = JSON.parse(line) as StreamEvent;
    if (event.type === 'delta') delta(event.text);
    if (event.type === 'error') throw Object.assign(new Error(event.error), { trace: event.trace });
    if (event.type === 'result') result = event.result;
  }
  if (!result) throw new Error('Kết nối kết thúc trước khi lưu kết quả.');
  return result;
}
