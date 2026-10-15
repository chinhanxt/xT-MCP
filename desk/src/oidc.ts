// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// OIDC authorization-code + PKCE (S256) cho client public `xt-desk-agent`. Token CHỈ nằm trong bộ nhớ.
// sessionStorage chỉ giữ {state, verifier, nonce} tạm thời qua lần chuyển hướng đăng nhập (không phải token).

export interface OidcConfig {
  issuer: string;
  clientId: string;
  scope: string;
  redirectUri: string;
}

export interface KvStorage {
  getItem(k: string): string | null;
  setItem(k: string, v: string): void;
  removeItem(k: string): void;
}

export interface OidcDeps {
  fetch: typeof fetch;
  session: KvStorage;
  navigate: (url: string) => void;
  now: () => number; // ms
  skewSeconds?: number; // làm mới trước khi hết hạn
}

export type AuthStatus = "unauthenticated" | "authenticating" | "authenticated" | "error";
export interface AuthState {
  status: AuthStatus;
  error?: string;
}

export class AuthRequiredError extends Error {
  constructor(msg = "Chưa đăng nhập: Desk chỉ gọi MCP/LLM bằng token của chính người dùng (Keycloak, client xt-desk-agent).") {
    super(msg);
    this.name = "AuthRequiredError";
  }
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
}
interface Tokens {
  access: string;
  refresh?: string;
  expiresAt: number; // ms
}
const PKCE_KEY = "xt.desk.oidc.pending";

const b64url = (b: ArrayBuffer | Uint8Array) =>
  btoa(String.fromCharCode(...new Uint8Array(b instanceof Uint8Array ? b : new Uint8Array(b))))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
const rand = (n = 32) => b64url(crypto.getRandomValues(new Uint8Array(n)));

export async function pkceChallenge(verifier: string): Promise<string> {
  return b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
}

export function validateConfig(c: Partial<OidcConfig>): OidcConfig {
  const miss = (["issuer", "clientId", "scope", "redirectUri"] as const).filter((k) => !c[k]);
  if (miss.length) throw new AuthRequiredError(`OIDC chưa cấu hình (thiếu ${miss.join(", ")}): Desk không gọi MCP/LLM.`);
  if (!c.scope!.split(/\s+/).includes("openid")) throw new AuthRequiredError("OIDC scope phải có openid");
  return c as OidcConfig;
}

function jwtClaims(jwt: string): Record<string, unknown> {
  const part = jwt.split(".")[1];
  if (!part) throw new Error("id_token không hợp lệ");
  const json = atob(part.replace(/-/g, "+").replace(/_/g, "/"));
  return JSON.parse(decodeURIComponent(escape(json))) as Record<string, unknown>;
}

export class OidcSession {
  private cfg: OidcConfig;
  private tokens: Tokens | null = null;
  private disc: Promise<Discovery> | null = null;
  private refreshing: Promise<string | null> | null = null;
  private listeners = new Set<(s: AuthState) => void>();
  state: AuthState = { status: "unauthenticated" };

  constructor(cfg: Partial<OidcConfig>, private d: OidcDeps) {
    this.cfg = validateConfig(cfg);
  }

  subscribe(fn: (s: AuthState) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private set(s: AuthState) {
    this.state = s;
    this.listeners.forEach((f) => f(s));
  }

  private discover(): Promise<Discovery> {
    this.disc ??= (async () => {
      const base = this.cfg.issuer.replace(/\/$/, "");
      const r = await this.d.fetch(`${base}/.well-known/openid-configuration`);
      if (!r.ok) throw new Error(`Không đọc được cấu hình OIDC (${r.status})`);
      const j = (await r.json()) as Discovery;
      if (j.issuer !== this.cfg.issuer.replace(/\/$/, "")) throw new Error("issuer của OIDC không khớp cấu hình");
      const origin = new URL(base).origin;
      for (const u of [j.authorization_endpoint, j.token_endpoint]) {
        if (typeof u !== "string" || new URL(u).origin !== origin) throw new Error("endpoint OIDC không cùng origin với issuer");
      }
      return j;
    })();
    this.disc.catch(() => (this.disc = null));
    return this.disc;
  }

  /** Bắt đầu đăng nhập: chuyển hướng top-level tới Keycloak. */
  async startLogin(): Promise<void> {
    this.set({ status: "authenticating" });
    try {
      const disc = await this.discover();
      const state = rand();
      const verifier = rand(48);
      const nonce = rand();
      this.d.session.setItem(PKCE_KEY, JSON.stringify({ state, verifier, nonce }));
      const u = new URL(disc.authorization_endpoint);
      u.search = new URLSearchParams({
        response_type: "code",
        client_id: this.cfg.clientId,
        redirect_uri: this.cfg.redirectUri,
        scope: this.cfg.scope,
        state,
        nonce,
        code_challenge: await pkceChallenge(verifier),
        code_challenge_method: "S256",
      }).toString();
      this.d.navigate(u.toString());
    } catch (e) {
      this.set({ status: "error", error: (e as Error).message });
    }
  }

  /** Xử lý redirect về redirect_uri. Trả true nếu đăng nhập thành công. */
  async handleCallback(url: string): Promise<boolean> {
    const q = new URL(url).searchParams;
    const raw = this.d.session.getItem(PKCE_KEY);
    this.d.session.removeItem(PKCE_KEY); // dùng một lần
    this.set({ status: "authenticating" });
    try {
      if (q.get("error")) throw new Error(`Keycloak từ chối đăng nhập: ${q.get("error")}`);
      const code = q.get("code");
      if (!raw || !code) throw new Error("Thiếu phiên đăng nhập đang chờ hoặc thiếu code");
      const pending = JSON.parse(raw) as { state: string; verifier: string; nonce: string };
      if (q.get("state") !== pending.state) throw new Error("state không khớp (có thể là CSRF)");
      const disc = await this.discover();
      const body = new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: this.cfg.redirectUri,
        client_id: this.cfg.clientId,
        code_verifier: pending.verifier,
      });
      const tok = await this.tokenRequest(disc, body);
      if (tok.id_token) {
        const c = jwtClaims(tok.id_token);
        const aud = Array.isArray(c.aud) ? c.aud : [c.aud];
        if (c.iss !== disc.issuer) throw new Error("id_token: iss sai");
        if (!aud.includes(this.cfg.clientId)) throw new Error("id_token: aud sai");
        if (c.nonce !== pending.nonce) throw new Error("id_token: nonce sai");
      }
      this.store(tok);
      this.set({ status: "authenticated" });
      return true;
    } catch (e) {
      this.tokens = null;
      this.set({ status: "error", error: (e as Error).message });
      return false;
    }
  }

  private async tokenRequest(disc: Discovery, body: URLSearchParams): Promise<{ access_token: string; refresh_token?: string; expires_in?: number; id_token?: string }> {
    const r = await this.d.fetch(disc.token_endpoint, {
      method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" },
      body,
    });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || !j.access_token) throw new Error(`Đổi token thất bại (${j.error ?? r.status})`);
    return j;
  }

  private store(t: { access_token: string; refresh_token?: string; expires_in?: number }) {
    this.tokens = { access: t.access_token, refresh: t.refresh_token, expiresAt: this.d.now() + (t.expires_in ?? 60) * 1000 };
  }

  /** Access token hợp lệ; làm mới (refresh token rotation, single-flight) khi sắp hết hạn. Null nếu chưa đăng nhập. */
  async getAccessToken(): Promise<string | null> {
    const t = this.tokens;
    if (!t) return null;
    const skew = (this.d.skewSeconds ?? 30) * 1000;
    if (t.expiresAt - skew > this.d.now()) return t.access;
    this.refreshing ??= this.refresh(t).finally(() => (this.refreshing = null));
    return this.refreshing;
  }

  private async refresh(t: Tokens): Promise<string | null> {
    try {
      if (!t.refresh) throw new Error("Không có refresh token");
      const disc = await this.discover();
      const tok = await this.tokenRequest(
        disc,
        new URLSearchParams({ grant_type: "refresh_token", refresh_token: t.refresh, client_id: this.cfg.clientId }),
      );
      this.store(tok); // refresh token mới thay thế cái cũ (rotation); cái cũ không dùng lại
      return this.tokens!.access;
    } catch (e) {
      this.tokens = null;
      this.set({ status: "unauthenticated", error: `Phiên đã hết hạn: ${(e as Error).message}` });
      return null;
    }
  }

  logout(): void {
    this.tokens = null;
    this.set({ status: "unauthenticated" });
  }

  /** Header Authorization cho MCP/LLM. Chưa đăng nhập => ném AuthRequiredError (fail closed). */
  authHeaders = async (): Promise<Record<string, string>> => {
    const t = await this.getAccessToken();
    if (!t) throw new AuthRequiredError();
    return { Authorization: `Bearer ${t}` };
  };
}
