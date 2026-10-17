// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { randomBytes } from 'node:crypto';
import { hashToken } from './http.ts';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Store } from './store.ts';
import { HttpError } from './validation.ts';
export function publicAppRequest(store: Store, req: IncomingMessage, res: ServerResponse, path: string, method: string) {
  const match = path.match(/^\/public\/apps\/([a-f0-9]{48})(\/run)?$/);
  if (!match) return null;
  const link = store.db.prepare('SELECT * FROM share_links WHERE id=?').get(match[1]);
  if (!link) throw new HttpError(404, 'Ứng dụng chia sẻ không tồn tại hoặc đã thu hồi.');
  if (method !== 'GET' && !(method === 'POST' && match[2])) throw new HttpError(405, 'Method không được hỗ trợ.');
  const cookieName = 'studio_guest_' + match[1],
    raw =
      req.headers.cookie
        ?.split(';')
        .map((x) => x.trim())
        .find((x) => x.startsWith(cookieName + '='))
        ?.slice(cookieName.length + 1) ?? '';
  let session = store.db
    .prepare('SELECT id FROM public_sessions WHERE token_hash=? AND share_id=? AND expires>?')
    .get(hashToken(raw), match[1], Date.now());
  if (!session) {
    if (method !== 'GET') throw new HttpError(401, 'Mở lại trang ứng dụng để tạo phiên mới.');
    const token = randomBytes(32).toString('hex'),
      id = store.id();
    store.db.prepare('DELETE FROM public_sessions WHERE expires<?').run(Date.now());
    store.db.prepare('INSERT INTO public_sessions VALUES(?,?,?,?)').run(id, hashToken(token), match[1], Date.now() + 86400000);
    session = { id };
    res.setHeader(
      'Set-Cookie',
      `${cookieName}=${token}; Path=/public/apps/${match[1]}; HttpOnly; SameSite=Strict; Max-Age=86400${'encrypted' in req.socket && req.socket.encrypted ? '; Secure' : ''}`,
    );
  }
  const workspace = String(link.workspace_id),
    app = store.app(workspace, String(link.app_id));
  if (!app.published) throw new HttpError(409, 'Ứng dụng chưa publish.');
  return {
    app,
    metadata:
      method === 'GET'
        ? {
            id: app.id,
            name: app.name,
            description: app.description,
            kind: app.kind,
            version: app.version,
            inputs: app.published.inputs ?? [],
          }
        : null,
    path: `/api/apps/${app.id}/run`,
    access: { workspace_id: workspace, audience: 'share:' + session.id, share: true },
  };
}
