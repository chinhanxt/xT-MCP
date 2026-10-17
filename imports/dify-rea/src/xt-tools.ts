import { createHmac } from 'node:crypto';
import { HttpError } from './validation.ts';
import { readLines } from './streams.ts';

export interface XtTool { name: string; description: string; input_schema: Record<string, unknown> }
export interface XtTools {
  list(signal?: AbortSignal): Promise<XtTool[]>;
  call(name: string, args: Record<string, unknown>, signal?: AbortSignal): Promise<unknown>;
}
export const xtToolNames = ['inspect_workspace', 'describe_model', 'describe_process', 'search_records',
  'get_record', 'aggregate_records', 'get_history', 'get_inbox', 'create_record', 'update_record', 'submit_request'];

export function xtTools(origin: string, secret: Buffer, capability: string): XtTools {
  const base = new URL(origin);
  if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.pathname !== '/' || base.search || base.hash)
    throw new Error('STUDIO_XT_CORE_URL must be a fixed HTTP(S) origin');
  if (!/^[A-Za-z0-9_-]{43}$/.test(capability)) throw new HttpError(401, 'Thiếu quyền công cụ của phiên xT.');
  async function request(payload: unknown, signal?: AbortSignal) {
    const body = JSON.stringify(payload);
    const signature = createHmac('sha256', secret).update(capability + '\n' + body).digest('hex');
    const response = await fetch(new URL('/studio-internal/tools', base), {
      method: 'POST', redirect: 'error', body,
      headers: { 'Content-Type': 'application/json', 'x-xt-capability': capability, 'x-xt-signature': signature },
      signal: AbortSignal.any([AbortSignal.timeout(30000), ...(signal ? [signal] : [])]),
    });
    if (!response.ok) throw new HttpError(response.status, 'Phiên công cụ xT đã hết hạn hoặc bị từ chối.');
    if (!response.body) throw new HttpError(502, 'Kết quả xT trống.');
    let raw = '';
    for await (const line of readLines(response.body, 1024 * 1024, 1024 * 1024)) raw += line + '\n';
    return JSON.parse(raw);
  }
  return {
    async list(signal) { return (await request({ action: 'list' }, signal)).tools as XtTool[]; },
    call(name, args, signal) {
      if (!xtToolNames.includes(name)) throw new HttpError(403, 'Công cụ xT không được cấp.');
      return request({ name, arguments: args }, signal);
    },
  };
}
