// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Keycloak giả cho e2e: discovery, authorize (tự đăng nhập, kiểm PKCE S256), token (auth-code + refresh rotation, reuse bị từ chối).
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";

const port = Number(process.env.E2E_OIDC_PORT);
if (!port) throw new Error("E2E_OIDC_PORT chưa đặt");
const issuer = `http://127.0.0.1:${port}/realms/xt`;
const CLIENT = "xt-desk-agent";
const codes = new Map(); // code -> {challenge, nonce, redirect}
const live = new Set(); // refresh token còn hiệu lực
const used = new Set(); // refresh token đã bị xoay (reuse => invalid_grant)
const log = [];
let n = 0;
const b64u = (b) => Buffer.from(b).toString("base64url");
const jwt = (claims) => `${b64u('{"alg":"none"}')}.${b64u(JSON.stringify(claims))}.`;

function cors(req, res) {
  res.setHeader("access-control-allow-origin", req.headers.origin || "*");
  res.setHeader("access-control-allow-headers", "content-type, dpop"); // như Keycloak 26 (Cors.DEFAULT_ALLOW_HEADERS có DPoP)
}

createServer((req, res) => {
  const u = new URL(req.url, issuer);
  cors(req, res);
  if (req.method === "OPTIONS") return res.writeHead(204).end();
  const json = (code, o) => (res.writeHead(code, { "content-type": "application/json" }), res.end(JSON.stringify(o)));
  if (u.pathname === "/__log") return json(200, log);
  if (u.pathname === "/realms/xt/.well-known/openid-configuration")
    return json(200, { issuer, authorization_endpoint: `${issuer}/protocol/openid-connect/auth`, token_endpoint: `${issuer}/protocol/openid-connect/token` });

  if (u.pathname === "/realms/xt/protocol/openid-connect/auth") {
    const q = u.searchParams;
    log.push({ ev: "authorize", client_id: q.get("client_id"), method: q.get("code_challenge_method"), scope: q.get("scope"), redirect_uri: q.get("redirect_uri") });
    if (q.get("client_id") !== CLIENT || q.get("code_challenge_method") !== "S256" || !q.get("code_challenge") || !(q.get("scope") || "").includes("mcp-xt") || q.get("response_type") !== "code")
      return json(400, { error: "invalid_request" });
    const code = b64u(randomBytes(12));
    codes.set(code, { challenge: q.get("code_challenge"), nonce: q.get("nonce"), redirect: q.get("redirect_uri") });
    const r = new URL(q.get("redirect_uri"));
    r.searchParams.set("code", code);
    r.searchParams.set("state", q.get("state"));
    res.writeHead(302, { location: r.toString() });
    return res.end();
  }

  if (u.pathname === "/realms/xt/protocol/openid-connect/token" && req.method === "POST") {
    let b = "";
    req.on("data", (d) => (b += d));
    req.on("end", () => {
      const f = new URLSearchParams(b);
      const grant = f.get("grant_type");
      const issue = (nonce) => {
        n++;
        const refresh = `rt-${n}`;
        live.add(refresh);
        const o = { access_token: `at-${n}`, refresh_token: refresh, expires_in: 20, token_type: "Bearer" }; // < skew 30s: mỗi lời gọi đều làm mới
        if (nonce !== undefined) o.id_token = jwt({ iss: issuer, aud: CLIENT, nonce, sub: "u1" });
        return o;
      };
      if (f.get("client_id") !== CLIENT) return json(400, { error: "invalid_client" });
      if (grant === "authorization_code") {
        const c = codes.get(f.get("code"));
        codes.delete(f.get("code"));
        const ok = c && c.redirect === f.get("redirect_uri") && createHash("sha256").update(f.get("code_verifier") || "").digest("base64url") === c.challenge;
        log.push({ ev: "token", grant, ok: !!ok });
        return ok ? json(200, issue(c.nonce)) : json(400, { error: "invalid_grant" });
      }
      if (grant === "refresh_token") {
        const rt = f.get("refresh_token");
        const ok = live.has(rt);
        log.push({ ev: "token", grant, ok, reused: used.has(rt) });
        if (!ok) return json(400, { error: "invalid_grant" });
        live.delete(rt);
        used.add(rt);
        return json(200, issue());
      }
      json(400, { error: "unsupported_grant_type" });
    });
    return;
  }
  res.writeHead(404).end();
}).listen(port, "127.0.0.1");
