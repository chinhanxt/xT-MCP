// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ToolResult } from "./types";

export interface ConfirmInfo {
  url: string;
  summary?: string;
}

const CONFIRM_PATH = /^\/desk\/confirm\/[\w-]+$/;

/** confirm_url chỉ hợp lệ khi đúng origin xT đã cấu hình và đúng đường dẫn /desk/confirm/{id}. Thiếu origin cấu hình thì từ chối (fail closed). */
export function isXtConfirmUrl(url: unknown, xtOrigin: string | undefined): url is string {
  if (typeof url !== "string" || !xtOrigin) return false;
  try {
    const u = new URL(url);
    return u.origin === new URL(xtOrigin).origin && !u.username && !u.password && !u.search && !u.hash && CONFIRM_PATH.test(u.pathname);
  } catch {
    return false;
  }
}

interface ErrEnvelope {
  error?: { code?: string; details?: { confirm_url?: unknown; confirmation_id?: unknown; plan_summary?: unknown } };
}

/** Envelope lỗi: ưu tiên structuredContent; không có (xT thật: isError, JSON ở content[0].text) thì parse an toàn. */
function errorEnvelope(r: ToolResult | undefined): ErrEnvelope | null {
  const sc = r?.structuredContent as ErrEnvelope | undefined;
  if (sc && typeof sc === "object" && sc.error) return sc;
  const t = r?.content?.[0];
  if (!t || t.type !== "text" || typeof t.text !== "string") return null;
  try {
    const v: unknown = JSON.parse(t.text);
    return v && typeof v === "object" && !Array.isArray(v) ? (v as ErrEnvelope) : null;
  } catch {
    return null;
  }
}

export function confirmInfo(r: ToolResult | undefined, xtOrigin?: string): ConfirmInfo | null {
  const err = errorEnvelope(r)?.error;
  if (!err || typeof err !== "object" || err.code !== "confirmation_required") return null;
  const d = err.details && typeof err.details === "object" ? err.details : undefined;
  const url = d?.confirm_url;
  if (!isXtConfirmUrl(url, xtOrigin)) return null;
  const id = d?.confirmation_id;
  if (typeof id === "string" && new URL(url).pathname !== `/desk/confirm/${id}`) return null;
  const s = d?.plan_summary;
  return { url, summary: typeof s === "string" ? s : undefined };
}
