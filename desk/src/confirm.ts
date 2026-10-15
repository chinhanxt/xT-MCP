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

export function confirmInfo(r: ToolResult | undefined, xtOrigin?: string): ConfirmInfo | null {
  const sc = r?.structuredContent as { error?: { code?: string; details?: { confirm_url?: unknown; plan_summary?: unknown } } } | undefined;
  if (sc?.error?.code !== "confirmation_required") return null;
  const url = sc.error.details?.confirm_url;
  if (!isXtConfirmUrl(url, xtOrigin)) return null;
  const s = sc.error.details?.plan_summary;
  return { url, summary: typeof s === "string" ? s : undefined };
}
