// SPDX-License-Identifier: Apache-2.0
import type { AuthHeaders, McpConnection } from './mcp';
import type { ToolResult } from './types';

export interface PortalBridge {
  request(path: string, init?: RequestInit): Promise<Response>;
  callTool(name: string, args: Record<string, unknown>): Promise<ToolResult>;
  invalidate(): void;
}

export function studioPath(path: string, origin: string): URL {
  if (!/^\/studio-api\/(?:[A-Za-z0-9_~.-]+\/?)*(?:\?[A-Za-z0-9_~.%=&+\-]*)?$/.test(path) ||
      /%(?:2e|2f|5c)/i.test(path) || path.includes('..') || path.includes('//') || path.includes('#')) {
    throw new Error('Đường dẫn Studio không hợp lệ');
  }
  const url = new URL(path, origin);
  if (url.origin !== origin || !url.pathname.startsWith('/studio-api/')) throw new Error('Đường dẫn Studio không hợp lệ');
  return url;
}

export function createPortalBridge(auth: AuthHeaders, mcp: McpConnection | null): PortalBridge {
  let active = true;
  const controllers = new Set<AbortController>();
  return {
    async request(path, init = {}) {
      if (!active) throw new Error('Phiên đã kết thúc');
      const url = studioPath(path, location.origin);
      const method = (init.method || 'GET').toUpperCase();
      if (!['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) throw new Error('Phương thức không hợp lệ');
      const controller = new AbortController(); controllers.add(controller);
      const signal = init.signal ? AbortSignal.any([controller.signal, init.signal]) : controller.signal;
      try {
        const headers = new Headers(init.headers);
        for (const [key, value] of Object.entries(await auth({ method, url: url.href }))) headers.set(key, value);
        if (!active || signal.aborted) throw new Error('Phiên đã kết thúc');
        const response = await fetch(url.href, { ...init, method, headers, signal, credentials: 'same-origin' });
        if (!active || signal.aborted) throw new Error('Phiên đã kết thúc');
        if (!response.body) { controllers.delete(controller); return response; }
        const reader = response.body.getReader();
        const body = new ReadableStream<Uint8Array>({
          async pull(stream) {
            try { const chunk = await reader.read(); if (chunk.done) { controllers.delete(controller); stream.close(); } else stream.enqueue(chunk.value); }
            catch (error) { controllers.delete(controller); stream.error(error); }
          },
          async cancel(reason) { controllers.delete(controller); await reader.cancel(reason); },
        });
        return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
      } catch (error) { controllers.delete(controller); throw error; }
    },
    async callTool(name, args) {
      if (!active || !mcp || !['search_records', 'get_record', 'aggregate_records', 'get_history', 'inspect_workspace'].includes(name)) throw new Error('Công cụ không được phép');
      if (name === 'search_records' && !['warehouse', 'warehouse_location', 'stock_item', 'stock_request', 'stock_balance', 'stock_movement'].includes(String(args.model_id))) throw new Error('Chỉ hỗ trợ dữ liệu kho');
      const result = await mcp.client.callTool({ name, arguments: args }) as ToolResult;
      if (!active) throw new Error('Phiên đã kết thúc');
      return result;
    },
    invalidate() { active = false; for (const controller of controllers) controller.abort(); controllers.clear(); },
  };
}
