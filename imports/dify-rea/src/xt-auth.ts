// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { createHash, createHmac, randomBytes, timingSafeEqual } from 'node:crypto';
import { readFileSync } from 'node:fs';
import type { IncomingMessage } from 'node:http';
import type { Store } from './store.ts';
import { HttpError } from './validation.ts';

export type StudioRole = 'admin' | 'editor' | 'viewer';
export interface XtIdentity { tenant: string; person: string; name: string; workspace_name: string; role: StudioRole; capability?: string; request_id?: string }
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const digest = /^[0-9a-f]{64}$/;
const encoded = /^[A-Za-z0-9_-]+$/;
const requestId = /^[0-9a-f]{32}$/;
const roleRank: Record<StudioRole, number> = { viewer: 1, editor: 2, admin: 3 };

export function studioRole(roles: string[]): StudioRole | undefined {
  if (roles.some((r) => ['studio_admin', 'director', 'xt_admin'].includes(r))) return 'admin';
  if (roles.includes('studio_editor')) return 'editor';
  if (roles.includes('studio_user')) return 'viewer';
}

export function requireStudioRole(role: StudioRole, required: StudioRole) {
  if (roleRank[role] < roleRank[required]) throw new HttpError(403, 'Không đủ quyền Studio.');
}

export function authorizeStudioRoute(path: string, method: string, role: StudioRole) {
  if (path === '/api/session' && method === 'GET') return;
  if (path === '/api/xt/tools' && method === 'GET') { requireStudioRole(role, 'editor'); return; }
  if (path === '/api/setup' || path === '/api/login' || path === '/api/logout' || path.startsWith('/v1/') || path.startsWith('/public/'))
    throw new HttpError(404, 'Tính năng này không hỗ trợ trong Studio tích hợp.');
  if (/^\/api\/apps\/[\w-]+\/(keys|share)(\/|$)/.test(path))
    throw new HttpError(404, 'API key và public share không hỗ trợ trong Studio tích hợp.');
  if (/^\/api\/(models|mcp)(\/|$)/.test(path)) {
    if ((path === '/api/models' || path === '/api/mcp') && method === 'GET') requireStudioRole(role, 'editor');
    else requireStudioRole(role, 'admin');
    return;
  }
  if (role === 'viewer') {
    if ((path === '/api/apps' || /^\/api\/apps\/[\w-]+$/.test(path)) && method === 'GET') return;
    if (/^\/api\/apps\/[\w-]+\/run$/.test(path) && method === 'POST') return;
    if (/^\/api\/apps\/[\w-]+\/conversations(?:\/[\w-]+)?$/.test(path) && ['GET', 'DELETE'].includes(method)) return;
    if (path === '/api/runs' && method === 'GET') return;
    if (/^\/api\/runs\/[\w-]+\/resume$/.test(path) && method === 'POST') return;
    throw new HttpError(403, 'Không đủ quyền Studio.');
  }
  if (/^\/api\/(apps|knowledge|documents|runs)(\/|$)/.test(path)) return;
  throw new HttpError(404, 'Không tìm thấy API Studio.');
}

export function studioSecret(): Buffer {
  const path = process.env.XT_STUDIO_SECRET_FILE;
  if (!path) throw new Error('XT_STUDIO_SECRET_FILE is required in integrated mode');
  const secret = readFileSync(path);
  if (secret.length < 32) throw new Error('XT_STUDIO_SECRET_FILE must contain at least 32 raw bytes');
  return secret;
}

export function safeRequestId(value: unknown): string {
  return typeof value === 'string' && requestId.test(value) ? value : randomBytes(16).toString('hex');
}

export function assertionVerifier(secret: Buffer, store: Store) {
  // The wire has no issuer claim; the trusted signing key identifies its issuer.
  const issuer = createHash('sha256').update(secret).digest('hex');
  return async (req: IncomingMessage): Promise<XtIdentity> => {
    const assertion = req.headers['x-xt-assertion'];
    const signature = req.headers['x-xt-signature'];
    if (typeof assertion !== 'string' || typeof signature !== 'string' || !encoded.test(assertion) || !digest.test(signature))
      throw new HttpError(401, 'Thiếu xác thực xT.');
    const expected = createHmac('sha256', secret).update(assertion).digest();
    if (!timingSafeEqual(expected, Buffer.from(signature, 'hex'))) throw new HttpError(401, 'Chữ ký xT không hợp lệ.');
    let claim: Record<string, unknown>;
    try {
      const raw = Buffer.from(assertion, 'base64url');
      if (raw.length > 8192 || raw.toString('base64url') !== assertion) throw new Error('encoding');
      claim = JSON.parse(raw.toString('utf8'));
    } catch { throw new HttpError(401, 'Assertion xT không hợp lệ.'); }
    const now = Math.floor(Date.now() / 1000);
    if (!claim || claim.v !== 1 || typeof claim.tenant !== 'string' || !uuid.test(claim.tenant) ||
        typeof claim.person !== 'string' || !uuid.test(claim.person) || typeof claim.nonce !== 'string' || !uuid.test(claim.nonce) ||
        typeof claim.name !== 'string' || !claim.name || claim.name.length > 200 ||
        typeof claim.workspace_name !== 'string' || !claim.workspace_name || claim.workspace_name.length > 200 ||
        !Array.isArray(claim.roles) || !claim.roles.every((r) => typeof r === 'string' && r.length < 100) ||
        typeof claim.method !== 'string' || claim.method !== req.method ||
        typeof claim.path !== 'string' || claim.path !== req.url || !claim.path.startsWith('/api/') ||
        typeof claim.body_sha256 !== 'string' || !digest.test(claim.body_sha256) ||
        typeof claim.iat !== 'number' || !Number.isInteger(claim.iat) || Math.abs(now - claim.iat) > 30 ||
        (claim.request_id !== undefined && (typeof claim.request_id !== 'string' || !requestId.test(claim.request_id))))
      throw new HttpError(401, 'Assertion xT không hợp lệ hoặc đã hết hạn.');
    const role = studioRole(claim.roles as string[]);
    if (!role) throw new HttpError(403, 'Chưa được cấp quyền Studio.');
    // Signature and claims are verified before any nonce storage access. Keep the
    // full iat-based validity window, including accepted future clock skew.
    if (!store.consumeAssertionNonce(issuer, (claim.tenant as string).toLowerCase(), (claim.nonce as string).toLowerCase(), (claim.iat as number) + 31, now))
      throw new HttpError(401, 'Assertion xT đã dùng hoặc quá tải.');
    const parts: Buffer[] = []; let size = 0;
    for await (const part of req) {
      size += part.length;
      if (size > 8 * 1024 * 1024) throw new HttpError(413, 'Yêu cầu vượt quá 8 MB.');
      parts.push(part);
    }
    const rawBody = Buffer.concat(parts);
    if (createHash('sha256').update(rawBody).digest('hex') !== claim.body_sha256) throw new HttpError(401, 'Nội dung yêu cầu xT không hợp lệ.');
    (req as IncomingMessage & { verifiedBody?: Buffer }).verifiedBody = rawBody;
    return { tenant: claim.tenant as string, person: claim.person as string, name: claim.name as string,
      workspace_name: claim.workspace_name as string, role,
      ...(typeof claim.capability === 'string' ? { capability: claim.capability } : {}),
      ...(typeof claim.request_id === 'string' ? { request_id: claim.request_id } : {}) };
  };
}
