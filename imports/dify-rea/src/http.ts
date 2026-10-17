import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Store } from './store.ts';
import type { XtTools } from './xt-tools.ts';
import { HttpError, object } from './validation.ts';
import { createHash } from 'node:crypto';
export const hashToken = (v: string) => createHash('sha256').update(v).digest('hex');
export const now = () => new Date().toISOString();
export interface RouteContext {
  store: Store;
  req: IncomingMessage;
  res: ServerResponse;
  path: string;
  url: URL;
  method: string;
  workspace: string;
  send: (value: unknown, code?: number) => void;
  busy: Set<string>;
  apiAccess?: { workspace_id: string; audience?: string; share?: boolean };
  identity?: { person: string; role: 'admin' | 'editor' | 'viewer' };
  coreTools?: XtTools;
}
export async function body(req: IncomingMessage): Promise<Record<string, unknown>> {
  if (!req.headers['content-type']?.startsWith('application/json')) throw new HttpError(415, 'Cần Content-Type application/json.');
  const verified = (req as IncomingMessage & { verifiedBody?: Buffer }).verifiedBody;
  if (verified) {
    try { return object(JSON.parse(verified.toString('utf8'))); }
    catch (e) { if (e instanceof HttpError) throw e; throw new HttpError(400, 'JSON không hợp lệ.'); }
  }
  let size = 0;
  const parts: Buffer[] = [];
  for await (const part of req) {
    size += part.length;
    if (size > 8 * 1024 * 1024) throw new HttpError(413, 'Yêu cầu vượt quá 8 MB.');
    parts.push(part);
  }
  try {
    return object(JSON.parse(Buffer.concat(parts).toString('utf8')));
  } catch (e) {
    if (e instanceof HttpError) throw e;
    throw new HttpError(400, 'JSON không hợp lệ.');
  }
}
