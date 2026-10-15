// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { AuthHeaders } from "./mcp";

export class AuthRequiredError extends Error {
  constructor() {
    super("Chưa đăng nhập: Desk chỉ gọi MCP/LLM bằng token của chính người dùng (Keycloak chưa tích hợp).");
  }
}

/**
 * Seam xác thực. Không có bearer tĩnh nào trong cấu hình/gateway.
 * - "oidc": token của người dùng từ Keycloak (v0.1). Chưa có phiên đăng nhập => FAIL CLOSED (ném AuthRequiredError).
 *   Khi tích hợp Keycloak, thay `getUserToken` bằng hàm trả access token của phiên hiện tại.
 * - "dev-unauthenticated": chỉ cho dev/e2e với server MCP không đòi xác thực; Desk hiện biểu ngữ cảnh báo.
 */
export type AuthMode = "oidc" | "dev-unauthenticated";

export function getUserToken(): Promise<string | null> {
  return Promise.resolve(null); // chưa có Keycloak
}

export function resolveAuth(mode: string, tokenSource: () => Promise<string | null> = getUserToken): AuthHeaders {
  if (mode === "dev-unauthenticated") return () => ({});
  if (mode === "oidc") {
    return async () => {
      const t = await tokenSource();
      if (!t) throw new AuthRequiredError();
      return { Authorization: `Bearer ${t}` };
    };
  }
  throw new Error(`DESK_AUTH_MODE không hợp lệ: "${mode}"`);
}
