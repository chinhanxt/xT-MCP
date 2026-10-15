// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { config } from "./config";
import { DpopSigner, indexedDbKeyStore } from "./dpop";
import { OidcSession } from "./oidc";

export { AuthRequiredError } from "./oidc";

/**
 * Phiên đăng nhập của Desk: OIDC auth-code + PKCE với client public `xt-desk-agent` (chat là AGENT thay mặt người dùng).
 * Client xác nhận `xt-desk-confirm` KHÔNG dùng ở đây: confirm_url mở tab mới, backend xt-desk tự đăng nhập bước-lên OTP.
 * DPoP: khóa P-256 non-extractable trong IndexedDB ràng token với trình duyệt này (RFC 9449).
 * Không bearer tĩnh, token chỉ trong bộ nhớ. Thiếu cấu hình => ném AuthRequiredError khi khởi tạo (fail closed).
 */
export function createSession(): OidcSession {
  return new OidcSession(
    {
      issuer: config.oidcIssuer,
      clientId: config.oidcClientId,
      scope: config.oidcScope,
      redirectUri: `${location.origin}${CALLBACK_PATH}`,
      postLogoutRedirectUri: `${location.origin}/`,
    },
    {
      fetch: (...a) => fetch(...a),
      session: sessionStorage,
      navigate: (u) => location.assign(u),
      now: () => Date.now(),
      dpop: new DpopSigner({ store: indexedDbKeyStore() }),
    },
  );
}

export const CALLBACK_PATH = "/auth/callback";
