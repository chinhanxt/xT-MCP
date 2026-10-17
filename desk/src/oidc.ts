// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// OIDC authorization-code + PKCE (S256) cho client public `xt-desk-agent`. Token CHỈ nằm trong bộ nhớ.
// sessionStorage chỉ giữ {state, verifier, nonce} tạm thời qua lần chuyển hướng đăng nhập (không phải token).
// Có signer DPoP => mọi request token gửi proof (RFC 9449); Keycloak trả token_type DPoP + cnf.jkt => phiên bị ràng khóa.
import type { DpopProver } from "./dpop";

export interface OidcConfig {
  issuer: string;
  clientId: string;
  scope: string;
  redirectUri: string;
  /** Đích sau RP-initiated logout (phải đăng ký ở `post.logout.redirect.uris` của client). */
  postLogoutRedirectUri?: string;
  /** Tùy chọn (mặc định tắt): tên query param gợi ý thêm cho IdP khác; Keycloak chọn tenant bằng scope organization:<alias>. */
  organizationParam?: string;
  /**
   * Client scope mang mapper Organization dùng để chọn tenant (mặc định `organization`). Keycloak coi MỌI scope có mapper
   * `oidc-organization-membership-mapper` là scope tổ chức dạng động `<scope>:<alias>`; xin đồng thời hai scope như vậy
   * (vd. `mcp-xt` trơn + `organization:<alias>`) thì kết quả phụ thuộc thứ tự nội bộ của Keycloak (khi sai: mất tenant hoặc
   * hiện lại bộ chọn). xT đặt `mcp-xt` (scope mang mapper) để Desk xin duy nhất `mcp-xt:<alias>`.
   */
  organizationScope?: string;
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
  dpop?: DpopProver;
}

/** Request mà header được gắn vào (cho proof DPoP: htm/htu). */
export interface AuthRequest {
  method: string;
  url: string;
}

export type AuthStatus = "unauthenticated" | "authenticating" | "authenticated" | "error";
export interface AuthState {
  status: AuthStatus;
  error?: string; // câu tiếng Việt cho người dùng
  detail?: string; // mã/lý do kỹ thuật, chỉ để tooltip và chẩn đoán
}

/** Lỗi đăng nhập: `message` là câu cho người dùng, `detail` giữ mã kỹ thuật. */
export class LoginFailure extends Error {
  constructor(message: string, readonly detail: string) {
    super(message);
    this.name = "LoginFailure";
  }
}
const LOGIN_AGAIN = "Phiên đăng nhập không hợp lệ, vui lòng đăng nhập lại.";
const errorState = (status: AuthStatus, e: unknown): AuthState => ({ status, error: (e as Error).message, ...(e instanceof LoginFailure ? { detail: e.detail } : {}) });

export class AuthRequiredError extends Error {
  constructor(msg = "Chưa đăng nhập: Desk chỉ truy cập dữ liệu bằng tài khoản của chính bạn.") {
    super(msg);
    this.name = "AuthRequiredError";
  }
}

interface Discovery {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
}
interface Tokens {
  access: string;
  refresh?: string;
  expiresAt: number; // ms
  bound: boolean; // DPoP-bound
  idToken?: string; // id_token_hint cho end_session (chỉ trong bộ nhớ)
}
interface TokenResponse {
  access_token: string;
  refresh_token?: string;
  expires_in?: number;
  id_token?: string;
  token_type?: string;
}
const PKCE_KEY = "xt.desk.oidc.pending";
const TENANT_KEY = "xt.desk.oidc.tenant-switch";
/**
 * Chọn tenant bằng scope: bỏ mọi scope tổ chức (`organization`, `organization:*`, `<mang>:*`), rồi hoặc đổi chính scope
 * mang mapper (`mcp-xt` -> `mcp-xt:<alias>`) hoặc, nếu cấu hình không có scope đó, chèn `organization:<alias>` ngay sau `openid`.
 */
function organizationScope(scope: string, organization: string, carrier = "organization"): string {
  const words = scope.split(/\s+/).filter(s => s && s !== "organization" && !s.startsWith("organization:") && !s.startsWith(`${carrier}:`));
  const target = `${carrier}:${organization}`;
  const at = words.indexOf(carrier);
  if (at >= 0) words[at] = target;
  else words.splice(words.indexOf("openid") + 1, 0, target);
  return words.join(" ");
}
export const validOrganization = (alias: string) => !!alias && !/[\s\u0000-\u001f\u007f:*]/.test(alias);

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
  if (c.organizationParam && (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(c.organizationParam) ||
      ["response_type", "client_id", "redirect_uri", "scope", "state", "nonce", "code_challenge", "code_challenge_method"].includes(c.organizationParam)))
    throw new AuthRequiredError("OIDC organizationParam không hợp lệ");
  if (c.organizationScope !== undefined && !/^[A-Za-z][A-Za-z0-9_.-]*$/.test(c.organizationScope))
    throw new AuthRequiredError("OIDC organizationScope không hợp lệ");
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
  private epoch = 0;
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
      if (j.end_session_endpoint !== undefined && (typeof j.end_session_endpoint !== "string" || new URL(j.end_session_endpoint).origin !== origin))
        throw new Error("end_session_endpoint không cùng origin với issuer");
      for (const u of [j.authorization_endpoint, j.token_endpoint]) {
        if (typeof u !== "string" || new URL(u).origin !== origin) throw new Error("endpoint OIDC không cùng origin với issuer");
      }
      return j;
    })();
    this.disc.catch(() => (this.disc = null));
    return this.disc;
  }

  /** Bắt đầu đăng nhập: chuyển hướng top-level tới Keycloak. */
  async startLogin(organization?: string): Promise<void> {
    const epoch = ++this.epoch;
    this.tokens = null;
    this.refreshing = null;
    this.d.session.removeItem(TENANT_KEY);
    this.d.session.removeItem(PKCE_KEY);
    this.set({ status: "authenticating" });
    try {
      if (organization !== undefined && !validOrganization(organization)) throw new Error("Doanh nghiệp không hợp lệ");
      const disc = await this.discover();
      if (epoch !== this.epoch) return;
      const state = rand();
      const verifier = rand(48);
      const nonce = rand();
      const challenge = await pkceChallenge(verifier);
      if (epoch !== this.epoch) return;
      this.d.session.setItem(PKCE_KEY, JSON.stringify({ state, verifier, nonce }));
      const u = new URL(disc.authorization_endpoint);
      u.search = new URLSearchParams({
        response_type: "code",
        client_id: this.cfg.clientId,
        redirect_uri: this.cfg.redirectUri,
        scope: organization === undefined ? this.cfg.scope : organizationScope(this.cfg.scope, organization, this.cfg.organizationScope),
        state,
        nonce,
        code_challenge: challenge,
        code_challenge_method: "S256",
      }).toString();
      if (organization !== undefined && this.cfg.organizationParam) u.searchParams.set(this.cfg.organizationParam, organization);
      this.d.navigate(u.toString());
    } catch (e) {
      if (epoch === this.epoch) this.set(errorState("error", e));
    }
  }

  /** Xử lý redirect về redirect_uri. Trả true nếu đăng nhập thành công. */
  async handleCallback(url: string): Promise<boolean> {
    const epoch = ++this.epoch;
    const q = new URL(url).searchParams;
    const raw = this.d.session.getItem(PKCE_KEY);
    this.d.session.removeItem(PKCE_KEY); // dùng một lần
    this.set({ status: "authenticating" });
    try {
      if (q.get("error")) throw new LoginFailure("Đăng nhập không thành công, vui lòng thử lại.", `Keycloak từ chối đăng nhập: ${q.get("error")}`);
      const code = q.get("code");
      if (!raw || !code) throw new Error("Thiếu phiên đăng nhập đang chờ hoặc thiếu code");
      const pending = JSON.parse(raw) as { state: string; verifier: string; nonce: string };
      if (q.get("state") !== pending.state) throw new Error("state không khớp (có thể là CSRF)");
      const disc = await this.discover();
      if (epoch !== this.epoch) return false;
      const body = new URLSearchParams({
        grant_type: "authorization_code",
        code,
        redirect_uri: this.cfg.redirectUri,
        client_id: this.cfg.clientId,
        code_verifier: pending.verifier,
      });
      const tok = await this.tokenRequest(disc, body, epoch);
      if (tok.id_token) {
        const c = jwtClaims(tok.id_token);
        const aud = Array.isArray(c.aud) ? c.aud : [c.aud];
        if (c.iss !== disc.issuer) throw new Error("id_token: iss sai");
        if (!aud.includes(this.cfg.clientId)) throw new Error("id_token: aud sai");
        if (c.nonce !== pending.nonce) throw new Error("id_token: nonce sai");
      }
      const bound = await this.bindingOf(tok);
      if (epoch !== this.epoch) return false;
      this.store(tok, bound);
      this.set({ status: "authenticated" });
      return true;
    } catch (e) {
      if (epoch !== this.epoch) return false;
      this.tokens = null;
      this.set(errorState("error", e));
      return false;
    }
  }

  private async tokenRequest(disc: Discovery, body: URLSearchParams, epoch: number): Promise<TokenResponse> {
    const headers: Record<string, string> = { "content-type": "application/x-www-form-urlencoded" };
    if (this.d.dpop) headers.DPoP = await this.d.dpop.proof("POST", disc.token_endpoint);
    if (epoch !== this.epoch) throw new AuthRequiredError();
    const r = await this.d.fetch(disc.token_endpoint, { method: "POST", headers, body });
    const j = await r.json().catch(() => ({}));
    if (epoch !== this.epoch) throw new AuthRequiredError();
    if (!r.ok || !j.access_token) throw new LoginFailure(LOGIN_AGAIN, `Đổi token thất bại (${j.error ?? r.status})`);
    return j;
  }

  /** Bound khi token_type = DPoP hoặc cnf.jkt = thumbprint của ta; token_type DPoP mà jkt khác (hoặc không có signer) => lỗi. */
  private async bindingOf(t: TokenResponse): Promise<boolean> {
    const isDpop = typeof t.token_type === "string" && t.token_type.toLowerCase() === "dpop";
    let jkt: unknown;
    try {
      jkt = (jwtClaims(t.access_token).cnf as { jkt?: unknown } | undefined)?.jkt;
    } catch {
      jkt = undefined; // access token không phải JWT
    }
    if (!this.d.dpop) {
      if (isDpop) throw new Error("Token DPoP nhưng Desk không có khóa DPoP");
      return false;
    }
    const mine = await this.d.dpop.thumbprint();
    if (isDpop && jkt !== undefined && jkt !== mine) throw new Error("Token DPoP ràng với khóa khác (cnf.jkt không khớp)");
    return isDpop || jkt === mine;
  }

  private store(t: TokenResponse, bound: boolean) {
    const idToken = t.id_token ?? this.tokens?.idToken; // refresh có thể không trả id_token mới
    this.tokens = { access: t.access_token, refresh: t.refresh_token, expiresAt: this.d.now() + (t.expires_in ?? 60) * 1000, bound, idToken };
  }

  /** Access token hợp lệ; làm mới (refresh token rotation, single-flight) khi sắp hết hạn. Null nếu chưa đăng nhập. */
  async getAccessToken(): Promise<string | null> {
    const t = this.tokens;
    if (!t) return null;
    const skew = (this.d.skewSeconds ?? 30) * 1000;
    if (t.expiresAt - skew > this.d.now()) return t.access;
    if (!this.refreshing) {
      const refreshing = this.refresh(t, this.epoch).finally(() => { if (this.refreshing === refreshing) this.refreshing = null; });
      this.refreshing = refreshing;
    }
    return this.refreshing;
  }

  private async refresh(t: Tokens, epoch: number): Promise<string | null> {
    try {
      if (!t.refresh) throw new Error("Không có refresh token");
      const disc = await this.discover();
      const tok = await this.tokenRequest(
        disc,
        new URLSearchParams({ grant_type: "refresh_token", refresh_token: t.refresh, client_id: this.cfg.clientId }),
        epoch,
      );
      const bound = await this.bindingOf(tok);
      if (epoch !== this.epoch) return null;
      this.store(tok, bound); // refresh token mới thay thế cái cũ (rotation); cái cũ không dùng lại
      return this.tokens!.access;
    } catch (e) {
      if (epoch !== this.epoch) return null;
      this.epoch++;
      this.tokens = null;
      this.set({ status: "unauthenticated", error: "Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại.", detail: `Phiên đã hết hạn: ${(e as Error).message}` });
      return null;
    }
  }

  logout(): void {
    const idToken = this.tokens?.idToken;
    this.clearLocal();
    const epoch = this.epoch;
    this.loggingOut = this.endSession(idToken, epoch).then(() => undefined).catch(e => {
      if (epoch === this.epoch) this.set(errorState("unauthenticated", e));
    });
  }

  private clearLocal(): void {
    this.epoch++;
    this.tokens = null;
    this.refreshing = null;
    this.d.session.removeItem(PKCE_KEY);
    this.d.session.removeItem(TENANT_KEY);
    this.set({ status: "unauthenticated" });
  }

  /** Xóa phiên trước; chỉ alias (không credential) tồn tại qua chuyển hướng đăng xuất SSO. */
  async switchTenant(organization: string): Promise<void> {
    if (!validOrganization(organization)) throw new Error("Doanh nghiệp không hợp lệ");
    const idToken = this.tokens?.idToken;
    this.clearLocal();
    const epoch = this.epoch;
    this.d.session.setItem(TENANT_KEY, organization);
    this.loggingOut = this.endSession(idToken, epoch, true).then(() => undefined).catch(e => {
      if (epoch !== this.epoch) return;
      this.d.session.removeItem(TENANT_KEY);
      this.set(errorState("error", e));
    });
    await this.loggingOut;
  }

  /** Một lần duy nhất sau post_logout_redirect_uri; luôn tạo state/verifier mới. */
  async resumeTenantSwitch(): Promise<boolean> {
    const organization = this.d.session.getItem(TENANT_KEY);
    this.d.session.removeItem(TENANT_KEY);
    if (organization === null) return false;
    await this.startLogin(organization);
    return true;
  }

  /** Chỉ nhãn hiển thị; backend xác minh JWT và membership trên từng request. */
  organizations(): string[] {
    if (!this.tokens) return [];
    try {
      const claim = jwtClaims(this.tokens.access).organization;
      const aliases = Array.isArray(claim) ? claim : claim && typeof claim === "object" ? Object.keys(claim) : [];
      return [...new Set(aliases.filter((alias): alias is string => typeof alias === "string" && validOrganization(alias)))];
    } catch { return []; }
  }

  /** Promise của lần đăng xuất gần nhất (test/UI chờ được). */
  loggingOut: Promise<void> = Promise.resolve();

  /** DPA-03: xoay khóa DPoP (xóa khỏi IndexedDB) rồi kết thúc phiên SSO Keycloak (RP-initiated
   * logout, `id_token_hint`), để người đăng nhập sau trên cùng trình duyệt có khóa và phiên mới. */
  private async endSession(idToken: string | undefined, epoch: number, switching = false): Promise<boolean> {
    await this.d.dpop?.reset?.();
    if (epoch !== this.epoch) return true;
    const end = (await this.discover()).end_session_endpoint;
    if (epoch !== this.epoch) return true;
    if (switching && (!end || !this.cfg.postLogoutRedirectUri)) throw new Error("Không đổi doanh nghiệp: OIDC thiếu endpoint đăng xuất hoặc URL quay về");
    if (!end) return false;
    const u = new URL(end);
    const q = new URLSearchParams({ client_id: this.cfg.clientId });
    if (idToken) q.set("id_token_hint", idToken);
    if (this.cfg.postLogoutRedirectUri) q.set("post_logout_redirect_uri", this.cfg.postLogoutRedirectUri);
    u.search = q.toString();
    this.d.navigate(u.toString());
    return true;
  }

  /** Phiên hiện tại có token ràng khóa DPoP. */
  dpopBound(): boolean {
    return this.tokens?.bound ?? false;
  }

  /** Signer DPoP của phiên (luồng PII reveal dùng chung khóa). */
  get dpop(): DpopProver | undefined {
    return this.d.dpop;
  }

  /**
   * Header cho MCP/LLM. Chưa đăng nhập => ném AuthRequiredError (fail closed).
   * Bound + có request => `Authorization: DPoP` + proof mới (jti mới, ath của token); không có request (LLM gateway) => Bearer.
   */
  authHeaders = async (req?: AuthRequest): Promise<Record<string, string>> => {
    const epoch = this.epoch;
    const t = await this.getAccessToken();
    if (!t || epoch !== this.epoch) throw new AuthRequiredError();
    if (req && this.d.dpop && this.tokens?.bound) {
      const proof = await this.d.dpop.proof(req.method, req.url, { accessToken: t });
      if (epoch !== this.epoch) throw new AuthRequiredError();
      return { Authorization: `DPoP ${t}`, DPoP: proof };
    }
    return { Authorization: `Bearer ${t}` };
  };
}
