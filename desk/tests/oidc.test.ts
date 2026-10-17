// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Máy trạng thái đăng nhập OIDC (auth-code + PKCE) của Desk.
import { describe, expect, it, vi } from "vitest";
import { createHash, webcrypto } from "node:crypto";
import { AuthRequiredError, OidcSession, pkceChallenge, validateConfig, type KvStorage } from "../src/oidc";
import { DpopSigner, memoryKeyStore } from "../src/dpop";
import { connectMcp } from "../src/mcp";

const ISS = "https://kc.example/realms/xt";
const CFG = { issuer: ISS, clientId: "xt-desk-agent", scope: "openid mcp-xt", redirectUri: "https://desk.example/auth/callback" };
const b64 = (o: unknown) => btoa(JSON.stringify(o)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const idToken = (c: Record<string, unknown>) => `${b64({ alg: "none" })}.${b64(c)}.`;

function mem(): KvStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v), removeItem: (k) => void data.delete(k) };
}

function setup(over: { token?: (form: URLSearchParams, n: number) => { status?: number; body: unknown }; issuer?: string; endpointOrigin?: string; dpop?: DpopSigner; endSession?: string; scope?: string; organizationParam?: string; organizationScope?: string } = {}) {
  const session = mem();
  let t = 1_000_000;
  const nav: string[] = [];
  const forms: URLSearchParams[] = [];
  const tokenHeaders: Record<string, string>[] = [];
  let issued = 0;
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/.well-known/openid-configuration")) {
      const o = over.endpointOrigin ?? "https://kc.example";
      return new Response(JSON.stringify({ issuer: over.issuer ?? ISS, authorization_endpoint: `${o}/realms/xt/auth`, token_endpoint: `${o}/realms/xt/token`, ...(over.endSession ? { end_session_endpoint: over.endSession } : {}) }));
    }
    const form = new URLSearchParams(String(init!.body));
    forms.push(form);
    tokenHeaders.push({ ...(init!.headers as Record<string, string>) });
    const r = over.token?.(form, forms.length) ?? { body: { access_token: `at${++issued}`, refresh_token: `rt${issued}`, expires_in: 300 } };
    return new Response(JSON.stringify(r.body), { status: r.status ?? 200 });
  });
  const s = new OidcSession({ ...CFG, scope: over.scope ?? CFG.scope, organizationParam: over.organizationParam, organizationScope: over.organizationScope, postLogoutRedirectUri: "https://desk.example/" }, { fetch: fetchMock as never, session, navigate: (u) => nav.push(u), now: () => t, dpop: over.dpop });
  return { s, session, nav, forms, tokenHeaders, fetchMock, advance: (ms: number) => (t += ms) };
}

/** Chạy startLogin rồi trả URL callback hợp lệ (mô phỏng Keycloak) */
async function loginUrl(h: ReturnType<typeof setup>, mutate?: (q: URLSearchParams) => void) {
  await h.s.startLogin();
  const authz = new URL(h.nav.at(-1)!);
  const q = new URLSearchParams({ code: "CODE", state: authz.searchParams.get("state")! });
  mutate?.(q);
  return { authz, cb: `${CFG.redirectUri}?${q}`, nonce: authz.searchParams.get("nonce")! };
}

describe("cấu hình", () => {
  it("thiếu issuer/client/scope => AuthRequiredError (fail closed)", () => {
    expect(() => validateConfig({ ...CFG, issuer: "" })).toThrow(AuthRequiredError);
    expect(() => validateConfig({ ...CFG, clientId: "" })).toThrow(AuthRequiredError);
    expect(() => validateConfig({ ...CFG, scope: "mcp-xt" })).toThrow(/openid/);
  });
});

describe("startLogin", () => {
  it("URL Keycloak có PKCE S256, state, nonce, scope mcp-xt, đúng client và redirect", async () => {
    const h = setup();
    const { authz } = await loginUrl(h);
    const q = authz.searchParams;
    expect(authz.origin + authz.pathname).toBe("https://kc.example/realms/xt/auth");
    expect(q.get("client_id")).toBe("xt-desk-agent");
    expect(q.get("response_type")).toBe("code");
    expect(q.get("scope")).toBe("openid mcp-xt");
    expect(q.get("redirect_uri")).toBe(CFG.redirectUri);
    expect(q.get("code_challenge_method")).toBe("S256");
    const pending = JSON.parse(h.session.getItem("xt.desk.oidc.pending")!);
    expect(q.get("code_challenge")).toBe(await pkceChallenge(pending.verifier));
    expect(q.get("state")).toBe(pending.state);
    expect(pending.verifier.length).toBeGreaterThanOrEqual(43);
  });
  it("PKCE challenge đúng vector RFC 7636", async () => {
    expect(await pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe("E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
  });
  it("discovery sai issuer hoặc endpoint khác origin => lỗi, không chuyển hướng", async () => {
    for (const o of [{ issuer: "https://evil.example/realms/xt" }, { endpointOrigin: "https://evil.example" }]) {
      const h = setup(o);
      await h.s.startLogin();
      expect(h.s.state.status).toBe("error");
      expect(h.nav).toEqual([]);
    }
  });
});

describe("handleCallback", () => {
  it("thành công: đổi code kèm code_verifier, token chỉ trong bộ nhớ", async () => {
    const h = setup({ token: (_f, n) => ({ body: { access_token: "AT", refresh_token: "RT", expires_in: 300, id_token: idToken({ iss: ISS, aud: "xt-desk-agent", nonce: nonceHolder.n }) } }) });
    const nonceHolder = { n: "" };
    const { cb, nonce } = await loginUrl(h);
    nonceHolder.n = nonce;
    expect(await h.s.handleCallback(cb)).toBe(true);
    expect(h.s.state.status).toBe("authenticated");
    const f = h.forms[0];
    expect(f.get("grant_type")).toBe("authorization_code");
    expect(f.get("code")).toBe("CODE");
    expect(f.get("client_id")).toBe("xt-desk-agent");
    expect(f.get("redirect_uri")).toBe(CFG.redirectUri);
    expect(f.get("code_verifier")).toBe(JSON.parse(JSON.stringify({ v: f.get("code_verifier") })).v);
    expect(await h.s.authHeaders()).toEqual({ Authorization: "Bearer AT" });
    expect([...h.session.data.values()].join("")).not.toMatch(/AT|RT/); // sessionStorage không giữ token, và phiên chờ đã bị xóa
    expect(h.session.data.size).toBe(0);
  });
  it("state không khớp (CSRF): từ chối, không gọi token endpoint", async () => {
    const h = setup();
    const { cb } = await loginUrl(h, (q) => q.set("state", "sai"));
    expect(await h.s.handleCallback(cb)).toBe(false);
    expect(h.forms).toEqual([]);
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
  });
  it("không có phiên chờ (callback giả/lặp lại) hoặc Keycloak trả error: từ chối", async () => {
    const h = setup();
    expect(await h.s.handleCallback(`${CFG.redirectUri}?code=x&state=y`)).toBe(false);
    const h2 = setup();
    const { cb } = await loginUrl(h2, (q) => (q.delete("code"), q.set("error", "access_denied")));
    expect(await h2.s.handleCallback(cb)).toBe(false);
    expect(h2.s.state.error).toMatch(/access_denied/);
  });
  it("phiên chờ dùng một lần: callback lần 2 thất bại", async () => {
    const h = setup();
    const { cb } = await loginUrl(h);
    expect(await h.s.handleCallback(cb)).toBe(true);
    expect(await h.s.handleCallback(cb)).toBe(false);
  });
  it("id_token sai nonce/aud/iss bị từ chối", async () => {
    for (const bad of [{ nonce: "khac" }, { aud: "xt-desk" }, { iss: "https://evil.example" }]) {
      const h = setup({ token: () => ({ body: { access_token: "AT", expires_in: 60, id_token: idToken({ iss: ISS, aud: "xt-desk-agent", nonce: "x", ...bad }) } }) });
      const { cb } = await loginUrl(h);
      expect(await h.s.handleCallback(cb)).toBe(false);
      await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
    }
  });
  it("đổi token lỗi => trạng thái error, không có token", async () => {
    const h = setup({ token: () => ({ status: 400, body: { error: "invalid_grant" } }) });
    const { cb } = await loginUrl(h);
    expect(await h.s.handleCallback(cb)).toBe(false);
    expect(h.s.state.status).toBe("error");
  });
});

async function loggedIn(over: Parameters<typeof setup>[0] = {}) {
  const h = setup(over);
  const { cb } = await loginUrl(h);
  await h.s.handleCallback(cb);
  h.forms.length = 0;
  return h;
}

describe("làm mới token", () => {
  it("còn hạn (ngoài skew): không gọi token endpoint", async () => {
    const h = await loggedIn();
    h.advance(100_000);
    expect(await h.s.getAccessToken()).toBe("at1");
    expect(h.forms).toEqual([]);
  });
  it("sắp hết hạn: refresh_token grant, refresh token xoay vòng (cái mới thay cái cũ)", async () => {
    const h = await loggedIn();
    h.advance(280_000); // còn 20s < skew 30s
    expect(await h.s.getAccessToken()).toBe("at2");
    expect(h.forms[0].get("grant_type")).toBe("refresh_token");
    expect(h.forms[0].get("refresh_token")).toBe("rt1");
    h.advance(280_000);
    await h.s.getAccessToken();
    expect(h.forms[1].get("refresh_token")).toBe("rt2"); // dùng token đã xoay, không dùng lại rt1
  });
  it("single-flight: nhiều request đồng thời chỉ làm mới MỘT lần (rotation không cho dùng lại)", async () => {
    const h = await loggedIn();
    h.advance(290_000);
    const r = await Promise.all(Array.from({ length: 10 }, () => h.s.getAccessToken()));
    expect(new Set(r).size).toBe(1);
    expect(h.forms.length).toBe(1);
  });
  it("refresh thất bại (hết phiên/bị thu hồi): đăng xuất, xóa token, fail closed", async () => {
    const h = await loggedIn({ token: (f, n) => (f.get("grant_type") === "refresh_token" ? { status: 400, body: { error: "invalid_grant" } } : { body: { access_token: "at1", refresh_token: "rt1", expires_in: 300 } }) });
    h.advance(290_000);
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
    expect(h.s.state.status).toBe("unauthenticated");
    expect(await h.s.getAccessToken()).toBeNull();
  });
  it("không có refresh token mà hết hạn: fail closed", async () => {
    const h = await loggedIn({ token: () => ({ body: { access_token: "a", expires_in: 20 } }) });
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
  });
});

describe("không rò token", () => {
  it("không đụng localStorage; logout xóa token", async () => {
    const ls = new Proxy({}, { get() { throw new Error("localStorage bị dùng"); }, set() { throw new Error("localStorage bị dùng"); } });
    vi.stubGlobal("localStorage", ls);
    const h = await loggedIn();
    expect(await h.s.authHeaders()).toBeDefined();
    h.s.logout();
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
    vi.unstubAllGlobals();
  });
  it("subscribe nhận chuyển trạng thái", async () => {
    const h = setup();
    const seen: string[] = [];
    h.s.subscribe((s) => seen.push(s.status));
    const { cb } = await loginUrl(h);
    await h.s.handleCallback(cb);
    expect(seen).toEqual(["authenticating", "authenticating", "authenticated"]);
  });
});

describe("DPoP (RFC 9449)", () => {
  const TOKEN_EP = "https://kc.example/realms/xt/token";
  const dec = (x: string) => JSON.parse(Buffer.from(x, "base64url").toString("utf8"));
  const proofOf = (jws: string) => {
    const [h, p] = jws.split(".");
    return { h: dec(h), p: dec(p) };
  };
  const signer = () => new DpopSigner({ store: memoryKeyStore(), subtle: webcrypto.subtle as SubtleCrypto });
  const at = (claims: Record<string, unknown>) => `${b64({ alg: "RS256", typ: "at+jwt" })}.${b64(claims)}.c2ln`;

  async function boundLogin(tokenType: string | undefined, jkt?: (mine: string) => string | undefined) {
    const dpop = signer();
    const mine = await dpop.thumbprint();
    let n = 0;
    const h = setup({
      dpop,
      token: () => {
        n++;
        const k = jkt?.(mine);
        return { body: { access_token: at({ sub: "u1", n, ...(k ? { cnf: { jkt: k } } : {}) }), refresh_token: `rt${n}`, expires_in: 300, ...(tokenType ? { token_type: tokenType } : {}) } };
      },
    });
    const { cb } = await loginUrl(h);
    const ok = await h.s.handleCallback(cb);
    return { h, dpop, mine, ok };
  }

  it("đổi code và refresh đều gửi header DPoP (POST, htu = token endpoint, không ath)", async () => {
    const { h, dpop, ok } = await boundLogin("DPoP", (m) => m);
    expect(ok).toBe(true);
    h.advance(290_000);
    await h.s.getAccessToken();
    expect(h.forms.map((f) => f.get("grant_type"))).toEqual(["authorization_code", "refresh_token"]);
    for (const hd of h.tokenHeaders) {
      const { h: hdr, p } = proofOf(hd.DPoP);
      expect(hdr.typ).toBe("dpop+jwt");
      expect(p.htm).toBe("POST");
      expect(p.htu).toBe(TOKEN_EP);
      expect(p.ath).toBeUndefined();
    }
    expect(proofOf(h.tokenHeaders[0].DPoP).p.jti).not.toBe(proofOf(h.tokenHeaders[1].DPoP).p.jti);
    expect(h.s.dpopBound()).toBe(true);
    expect(h.s.dpop).toBe(dpop);
  });

  it("bound nhận từ token_type (không phân biệt hoa thường) hoặc cnf.jkt khớp thumbprint", async () => {
    expect((await boundLogin("dpop")).h.s.dpopBound()).toBe(true);
    expect((await boundLogin("Bearer", (m) => m)).h.s.dpopBound()).toBe(true);
    expect((await boundLogin("Bearer")).h.s.dpopBound()).toBe(false);
    expect((await boundLogin(undefined, () => "khac")).h.s.dpopBound()).toBe(false);
  });

  it("token_type DPoP nhưng cnf.jkt khác khóa của ta => đăng nhập thất bại", async () => {
    const { h, ok } = await boundLogin("DPoP", () => "jkt-cua-nguoi-khac");
    expect(ok).toBe(false);
    expect(h.s.state.status).toBe("error");
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
  });

  it("token_type DPoP mà Desk không có signer => lỗi", async () => {
    const h = setup({ token: () => ({ body: { access_token: "AT", expires_in: 300, token_type: "DPoP" } }) });
    const { cb } = await loginUrl(h);
    expect(await h.s.handleCallback(cb)).toBe(false);
    expect(h.tokenHeaders[0].DPoP).toBeUndefined();
  });

  it("bound + request => Authorization: DPoP + proof (htm, htu, ath); không có request (LLM) => Bearer", async () => {
    const { h } = await boundLogin("DPoP", (m) => m);
    const tok = (await h.s.getAccessToken())!;
    const hd = await h.s.authHeaders({ method: "post", url: "https://xt-mcp.example/mcp?x=1" });
    expect(hd.Authorization).toBe(`DPoP ${tok}`);
    const { p } = proofOf(hd.DPoP);
    expect(p).toMatchObject({ htm: "POST", htu: "https://xt-mcp.example/mcp", ath: createHash("sha256").update(tok).digest("base64url") });
    expect(await h.s.authHeaders()).toEqual({ Authorization: `Bearer ${tok}` });
  });

  it("chưa bound => Bearer kể cả khi có request", async () => {
    const { h } = await boundLogin("Bearer");
    const hd = await h.s.authHeaders({ method: "POST", url: "https://xt-mcp.example/mcp" });
    expect(hd).toEqual({ Authorization: `Bearer ${await h.s.getAccessToken()}` });
  });

  it("MCP: mỗi request HTTP có proof mới (jti mới) với htm/htu của chính request", async () => {
    const { h } = await boundLogin("DPoP", (m) => m);
    const tok = (await h.s.getAccessToken())!;
    const seen: { method: string; url: string; headers: Headers }[] = [];
    vi.stubGlobal("fetch", async (input: string | URL | Request, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      seen.push({ method: init?.method ?? "GET", url: String(input), headers });
      if ((init?.method ?? "GET") !== "POST") return new Response(null, { status: 405 });
      const msg = JSON.parse(String(init!.body));
      if (!("id" in msg)) return new Response(null, { status: 202 });
      const result =
        msg.method === "initialize"
          ? { protocolVersion: msg.params.protocolVersion, capabilities: { tools: {} }, serverInfo: { name: "xt", version: "1" } }
          : { tools: [] };
      return new Response(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result }), { headers: { "content-type": "application/json" } });
    });
    try {
      await connectMcp(new URL("https://xt-mcp.example/mcp?q=1"), h.s.authHeaders);
    } finally {
      vi.unstubAllGlobals();
    }
    const posts = seen.filter((r) => r.method === "POST");
    expect(posts.length).toBeGreaterThanOrEqual(3);
    const jtis = new Set<string>();
    for (const r of seen) {
      expect(r.headers.get("authorization")).toBe(`DPoP ${tok}`);
      const { p } = proofOf(r.headers.get("dpop")!);
      expect(p.htm).toBe(r.method);
      expect(p.htu).toBe("https://xt-mcp.example/mcp");
      expect(p.ath).toBe(createHash("sha256").update(tok).digest("base64url"));
      jtis.add(p.jti);
    }
    expect(jtis.size).toBe(seen.length);
  });
});

describe("đổi doanh nghiệp", () => {
  const END = "https://kc.example/realms/xt/logout";
  it("targets one organization in the scope and configured login hint", async () => {
    for (const organizationParam of [undefined, "org_hint"]) {
      const h = setup({ scope: "openid organization:* mcp-xt organization organization:old", organizationParam });
      await h.s.startLogin("binh-minh");
      const q = new URL(h.nav.at(-1)!).searchParams;
      expect(q.has("organization")).toBe(false);
      if (organizationParam) expect(q.get(organizationParam)).toBe("binh-minh");
      expect(q.get("scope")).toBe("openid organization:binh-minh mcp-xt");
    }
  });
  it("selects the tenant on the scope that carries the organization mapper (mcp-xt:<alias>), never next to the bare scope", async () => {
    const h = setup({ scope: "openid mcp-xt profile", organizationScope: "mcp-xt" });
    await h.s.startLogin("binh-minh");
    expect(new URL(h.nav.at(-1)!).searchParams.get("scope")).toBe("openid mcp-xt:binh-minh profile");
    const again = setup({ scope: "openid mcp-xt:old organization organization:old2", organizationScope: "mcp-xt" });
    await again.s.startLogin("second");
    expect(new URL(again.nav.at(-1)!).searchParams.get("scope")).toBe("openid mcp-xt:second");
    expect(() => setup({ organizationScope: "bad scope" })).toThrow(/organizationScope/);
  });
  it("rejects organization hints that can change scope selection", async () => {
    for (const alias of ["", "*", "alpha beta", "alpha\nadmin", "organization:*"]) {
      const h = setup();
      await h.s.startLogin(alias);
      expect(h.nav).toEqual([]);
      expect(h.s.state.status).toBe("error");
    }
  });
  it("rejects an organization hint parameter that overwrites OIDC security parameters", () => {
    for (const organizationParam of ["scope", "state", "nonce", "client_id", "redirect_uri", "code_challenge", "response_type", "code_challenge_method"])
      expect(() => setup({ organizationParam })).toThrow(/organizationParam/);
  });
  it("ends SSO, resets DPoP and resumes a single targeted login with fresh PKCE", async () => {
    const dpop = new DpopSigner({ store: memoryKeyStore(), subtle: webcrypto.subtle as SubtleCrypto });
    const h = await loggedIn({ dpop, endSession: END });
    const initial = new URL(h.nav[0]).searchParams;
    const before = await dpop.thumbprint();
    await h.s.switchTenant("second");
    expect(await h.s.getAccessToken()).toBeNull();
    expect(new URL(h.nav.at(-1)!).pathname).toBe("/realms/xt/logout");
    expect(await dpop.thumbprint()).not.toBe(before);
    const resumed = new OidcSession(CFG, { fetch: h.fetchMock as never, session: h.session, navigate: u => h.nav.push(u), now: Date.now });
    expect(await resumed.resumeTenantSwitch()).toBe(true);
    const next = new URL(h.nav.at(-1)!).searchParams;
    expect(next.has("organization")).toBe(false);
    expect(next.get("scope")).toBe("openid organization:second mcp-xt");
    expect(next.get("state")).not.toBe(initial.get("state"));
    expect(next.get("code_challenge")).not.toBe(initial.get("code_challenge"));
    expect(await resumed.resumeTenantSwitch()).toBe(false);
  });
  it("refuses switching when discovery has no logout endpoint", async () => {
    const h = await loggedIn();
    await h.s.switchTenant("second");
    expect(h.nav).toHaveLength(1);
    expect(h.s.state.status).toBe("error");
    expect(await h.s.getAccessToken()).toBeNull();
    expect(await h.s.resumeTenantSwitch()).toBe(false);
  });
  it("refuses switching without a configured return URL", async () => {
    const h = setup({ endSession: END });
    const s = new OidcSession(CFG, { fetch: h.fetchMock as never, session: h.session, navigate: u => h.nav.push(u), now: Date.now });
    await s.switchTenant("second");
    expect(h.nav).toEqual([]);
    expect(s.state.status).toBe("error");
    expect(await s.resumeTenantSwitch()).toBe(false);
  });
  it("ordinary logout clears pending tenant switches and PKCE", async () => {
    const h = await loggedIn({ endSession: END });
    await h.s.switchTenant("second");
    h.s.logout();
    await h.s.loggingOut;
    expect(await h.s.resumeTenantSwitch()).toBe(false);
    expect(h.session.data.size).toBe(0);
  });
  it("does not resume a switch after DPoP reset or discovery fails", async () => {
    const dpop = new DpopSigner({ store: memoryKeyStore(), subtle: webcrypto.subtle as SubtleCrypto });
    const h = await loggedIn({ dpop });
    vi.spyOn(dpop, "reset").mockRejectedValueOnce(new Error("reset unavailable"));
    await h.s.switchTenant("second");
    expect(h.nav).toHaveLength(1);
    expect(await h.s.resumeTenantSwitch()).toBe(false);
    expect(await h.s.getAccessToken()).toBeNull();
    const other = setup();
    other.fetchMock.mockRejectedValueOnce(new Error("network unavailable"));
    await other.s.switchTenant("second");
    expect(other.nav).toEqual([]);
    expect(await other.s.resumeTenantSwitch()).toBe(false);
  });
  it("reads organization display hints from object and list claims without persisting tokens", async () => {
    for (const [claim, aliases] of [[{ first: { id: "one" }, second: {} }, ["first", "second"]], [["second", "first"], ["second", "first"]], [null, []]] as const) {
      const h = await loggedIn({ token: () => ({ body: { access_token: idToken({ organization: claim }), expires_in: 300 } }) });
      expect(h.s.organizations()).toEqual(aliases);
      h.s.logout();
      expect(h.s.organizations()).toEqual([]);
    }
  });
});

describe("async logout isolation", () => {
  it("discards a pending DPoP authorization proof when refresh fails", async () => {
    const dpop = new DpopSigner({ store: memoryKeyStore(), subtle: webcrypto.subtle as SubtleCrypto });
    const h = await loggedIn({ dpop, token: () => ({ body: { access_token: "AT", refresh_token: "RT", token_type: "DPoP", expires_in: 300 } }) });
    let finish!: (proof: string) => void;
    const pending = new Promise<string>(resolve => { finish = resolve; });
    const proof = vi.spyOn(dpop, "proof").mockImplementationOnce(() => pending);
    const headers = h.s.authHeaders({ method: "POST", url: "https://desk.example/mcp" });
    await vi.waitFor(() => expect(proof).toHaveBeenCalledOnce());
    h.advance(290_000);
    h.fetchMock.mockRejectedValueOnce(new Error("revoked"));
    expect(await h.s.getAccessToken()).toBeNull();
    finish("old-proof");
    await expect(headers).rejects.toBeInstanceOf(AuthRequiredError);
  });
  it("does not recreate the reset DPoP key when an obsolete token response arrives", async () => {
    const store = memoryKeyStore();
    const dpop = new DpopSigner({ store, subtle: webcrypto.subtle as SubtleCrypto });
    const h = await loggedIn({ dpop });
    let finish!: (response: Response) => void;
    h.fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    h.advance(290_000);
    const refreshed = h.s.getAccessToken();
    await vi.waitFor(() => expect(finish).toBeDefined());
    h.s.logout();
    await h.s.loggingOut;
    expect(await store.get()).toBeUndefined();
    finish(new Response(JSON.stringify({ access_token: "late", expires_in: 300 })));
    expect(await refreshed).toBeNull();
    expect(await store.get()).toBeUndefined();
  });
  it("discards a successful refresh that completes after logout", async () => {
    const h = await loggedIn();
    let finish!: (response: Response) => void;
    const pending = new Promise<Response>(resolve => { finish = resolve; });
    h.fetchMock.mockImplementationOnce(() => pending);
    h.advance(290_000);
    const refreshed = h.s.getAccessToken();
    await vi.waitFor(() => expect(h.fetchMock).toHaveBeenCalledTimes(3));
    h.s.logout();
    finish(new Response(JSON.stringify({ access_token: "late", refresh_token: "late-refresh", expires_in: 300 })));
    expect(await refreshed).toBeNull();
    expect(await h.s.getAccessToken()).toBeNull();
    expect(h.s.state.status).toBe("unauthenticated");
  });
  it("discards a callback token exchange that completes after logout", async () => {
    const h = setup();
    const { cb } = await loginUrl(h);
    let finish!: (response: Response) => void;
    h.fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    const callback = h.s.handleCallback(cb);
    await vi.waitFor(() => expect(finish).toBeDefined());
    h.s.logout();
    finish(new Response(JSON.stringify({ access_token: "late", expires_in: 300 })));
    expect(await callback).toBe(false);
    expect(await h.s.getAccessToken()).toBeNull();
    expect(h.s.state.status).toBe("unauthenticated");
  });
  it("does not navigate a login whose discovery finishes after logout", async () => {
    const h = setup();
    let finish!: (response: Response) => void;
    h.fetchMock.mockImplementationOnce(() => new Promise<Response>(resolve => { finish = resolve; }));
    const login = h.s.startLogin();
    h.s.logout();
    finish(new Response(JSON.stringify({ issuer: ISS, authorization_endpoint: `${ISS}/auth`, token_endpoint: `${ISS}/token` })));
    await login;
    await h.s.loggingOut;
    expect(h.nav).toEqual([]);
    expect(h.session.data.size).toBe(0);
    expect(h.s.state.status).toBe("unauthenticated");
  });
});

describe("đăng xuất (audit DPA-03)", () => {
  const END = "https://kc.example/realms/xt/protocol/openid-connect/logout";
  it("xoay khóa DPoP (xóa khỏi kho) và RP-initiated logout với id_token_hint", async () => {
    const store = memoryKeyStore();
    const dpop = new DpopSigner({ store, subtle: webcrypto.subtle as SubtleCrypto });
    const before = await dpop.thumbprint();
    expect(await store.get()).toBeDefined();
    let nonce = "";
    const h = setup({
      dpop,
      endSession: END,
      token: () => ({ body: { access_token: "at1", refresh_token: "rt1", expires_in: 300, id_token: idToken({ iss: ISS, aud: CFG.clientId, nonce }) } }),
    });
    const l = await loginUrl(h);
    nonce = l.nonce;
    expect(await h.s.handleCallback(l.cb)).toBe(true);
    h.s.logout();
    await h.s.loggingOut;
    await expect(h.s.authHeaders()).rejects.toBeInstanceOf(AuthRequiredError);
    expect(await store.get()).toBeUndefined(); // IndexedDB không còn khóa của người trước
    expect(await dpop.thumbprint()).not.toBe(before); // người sau nhận jkt khác
    const u = new URL(h.nav.at(-1)!);
    expect(u.origin + u.pathname).toBe(END);
    expect(u.searchParams.get("id_token_hint")).toBe(idToken({ iss: ISS, aud: CFG.clientId, nonce }));
    expect(u.searchParams.get("post_logout_redirect_uri")).toBe("https://desk.example/");
    expect(u.searchParams.get("client_id")).toBe(CFG.clientId);
  });
  it("end_session_endpoint khác origin issuer => không điều hướng ra ngoài", async () => {
    const h = setup({ endSession: "https://evil.example/logout" });
    await h.s.startLogin();
    expect(h.s.state.status).toBe("error");
  });
});
