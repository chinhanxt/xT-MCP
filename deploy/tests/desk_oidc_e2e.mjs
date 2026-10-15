// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// E2E headless: Desk (build thật) -> Keycloak thật qua APISIX. Không sửa /etc/hosts, không đụng display thật.
// https://xt*.freepro.online được ánh xạ bằng Playwright route tới APISIX 127.0.0.1:9080 (Host giữ nguyên) và thư mục dist của Desk.
// Dùng: node desk_oidc_e2e.mjs <desk-dist-dir> <username> <password>   (chạy qua desk_oidc_e2e.sh)
import { createRequire } from "node:module";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.resolve(here, "../../desk/package.json"));
const { chromium } = require("@playwright/test");

const [dist, USER, PASS] = process.argv.slice(2);
const GW = process.env.GW || "127.0.0.1:9080";
const H = { desk: "xt.freepro.online", auth: "xt-auth.freepro.online", mcp: "xt-mcp.freepro.online", api: "xt-api.freepro.online" };
const CID = "xt-desk-agent";
const MIME = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon" };

let fails = 0;
const check = (ok, msg) => { console.log(`  ${ok ? "PASS" : "FAIL"}  ${msg}`); if (!ok) fails++; };
const b64j = (s) => JSON.parse(Buffer.from(s.split(".")[1], "base64url").toString());

// static server cho dist (SPA fallback cho /auth/callback)
const srv = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  let f = path.join(dist, p);
  if (!f.startsWith(dist) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(dist, "index.html");
  res.writeHead(200, { "content-type": MIME[path.extname(f)] || "application/octet-stream" });
  res.end(fs.readFileSync(f));
});
await new Promise((r) => srv.listen(0, "127.0.0.1", r));
const staticPort = srv.address().port;

const authorizeUrls = [], tokenBodies = [], tokenResponses = [], mcpReqs = [];
const ip = `203.0.113.${1 + Math.floor(Math.random() * 250)}`;

async function viaGateway(route, host, hostIsStatic) {
  const req = route.request();
  const u = new URL(req.url());
  if (hostIsStatic) {
    const r = await fetch(`http://127.0.0.1:${staticPort}${u.pathname}`);
    return route.fulfill({ status: r.status, headers: { "content-type": r.headers.get("content-type") }, body: Buffer.from(await r.arrayBuffer()) });
  }
  const headers = { ...req.headers(), host, "cf-connecting-ip": ip };
  delete headers["accept-encoding"]; delete headers["content-length"];
  const [gh, gp] = GW.split(":");
  const body = ["GET", "HEAD"].includes(req.method()) ? null : req.postDataBuffer();
  const r = await new Promise((resolve, reject) => {
    const rq = http.request({ host: gh, port: gp, path: u.pathname + u.search, method: req.method(), headers }, (res) => {
      const ch = []; res.on("data", (c) => ch.push(c)); res.on("end", () => resolve({ status: res.statusCode, raw: res.headers, buf: Buffer.concat(ch) }));
    });
    rq.on("error", reject); if (body) rq.write(body); rq.end();
  });
  const out = {};
  for (const [k, v] of Object.entries(r.raw)) if (!["content-encoding", "content-length", "transfer-encoding", "set-cookie", "connection"].includes(k)) out[k] = Array.isArray(v) ? v.join(", ") : v;
  if (r.raw["set-cookie"]) out["set-cookie"] = r.raw["set-cookie"].join("\n");
  // Chromium không route lại đích của redirect do route.fulfill tạo (rơi ra mạng thật): với điều hướng, đổi 3xx thành trang tự chuyển hướng (giữ Set-Cookie).
  if (r.status >= 300 && r.status < 400 && out.location && req.isNavigationRequest()) {
    delete out.location;
    const to = new URL(r.raw.location, req.url()).href;
    return route.fulfill({ status: 200, headers: { ...out, "content-type": "text/html" }, body: `<!doctype html><script>location.replace(${JSON.stringify(to)})</script>` });
  }
  return route.fulfill({ status: r.status, headers: out, body: r.buf });
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
await ctx.route(/^https:\/\/xt(-auth|-mcp|-api)?\.freepro\.online\//, async (route) => {
  const req = route.request();
  const u = new URL(req.url());
  try {
    if (u.host === H.auth) {
      if (u.pathname.endsWith("/openid-connect/auth")) authorizeUrls.push(req.url());
      if (u.pathname.endsWith("/openid-connect/token") && req.method() === "POST") tokenBodies.push(req.postData());
      return await viaGateway(route, H.auth);
    }
    if (u.host === H.desk && u.pathname === "/mcp") {
      mcpReqs.push({ method: req.method(), auth: req.headers()["authorization"] || null, url: req.url() });
      return await viaGateway(route, H.mcp);   // build Desk hiện gọi /mcp cùng origin; chuyển tới host MCP thật để xem phản hồi thật
    }
    if (u.host === H.desk) return await viaGateway(route, H.desk, true);
    if (u.host === H.mcp) { mcpReqs.push({ method: req.method(), auth: req.headers()["authorization"] || null, url: req.url() }); return await viaGateway(route, H.mcp); }
    return await viaGateway(route, u.host);
  } catch (e) { console.log("  route lỗi", u.host, u.pathname, e.message); return route.abort(); }
});
const page = await ctx.newPage();
page.on("response", async (r) => {
  if (r.url().includes("/openid-connect/token") && r.request().method() === "POST") { try { tokenResponses.push(await r.json()); } catch {} }
});
page.on("response", (r) => { if (r.status() >= 400) console.log("  http", r.status(), r.url()); });
page.on("pageerror", (e) => console.log("  pageerror:", e.message));

console.log("== Headless flow: Desk -> Keycloak -> Desk ==");
await page.goto(`https://${H.desk}/`);
await page.waitForSelector("#login", { timeout: 20000 });
check(true, "Desk hiện nút đăng nhập khi chưa có phiên (chưa gọi MCP)");
check(mcpReqs.length === 0, `chưa đăng nhập: 0 request /mcp (thực tế ${mcpReqs.length})`);
page.on("console", (m) => console.log("  console:", m.text())); page.on("requestfailed", (r) => console.log("  reqfailed:", r.url(), r.failure()?.errorText));
await page.click("#login");
await page.waitForURL(new RegExp(`^https://${H.auth}/`), { timeout: 20000 });
await page.fill("#username", USER);
await page.fill("#password", PASS);
await Promise.all([page.waitForURL(new RegExp(`^https://${H.desk}/`), { timeout: 30000 }), page.click("#kc-login")]);
await page.waitForFunction(() => !document.querySelector("#login"), null, { timeout: 30000 }).catch(() => {});
for (let i = 0; i < 40 && !mcpReqs.some((r) => r.auth); i++) await page.waitForTimeout(250);
check(!(await page.$("#login")), "quay lại Desk, nút đăng nhập biến mất (đã đăng nhập)");
await page.screenshot({ path: process.env.SHOT || "/tmp/desk_oidc_e2e.png" }).catch(() => {});

console.log("== Yêu cầu authorize / token (PKCE) ==");
const au = new URL(authorizeUrls[0]);
const q = au.searchParams;
check(q.get("client_id") === CID, `authorize client_id=${q.get("client_id")}`);
check(q.get("response_type") === "code", "response_type=code");
check(q.get("code_challenge_method") === "S256" && /^[A-Za-z0-9_-]{43}$/.test(q.get("code_challenge") || ""), `PKCE: code_challenge_method=S256, challenge=${q.get("code_challenge")}`);
check((q.get("scope") || "").split(" ").includes("mcp-xt"), `scope xin: "${q.get("scope")}"`);
check(q.get("redirect_uri") === `https://${H.desk}/auth/callback`, `redirect_uri=${q.get("redirect_uri")}`);
const tb = new URLSearchParams(tokenBodies.find((b) => b.includes("authorization_code")) || "");
check(tb.get("grant_type") === "authorization_code" && !!tb.get("code_verifier") && !tb.has("client_secret"), `token request: có code_verifier (${(tb.get("code_verifier") || "").length} ký tự), không client_secret`);
const { createHash } = await import("node:crypto");
check(createHash("sha256").update(tb.get("code_verifier") || "").digest("base64url") === q.get("code_challenge"), "SHA256(code_verifier) == code_challenge");

console.log("== Token trên request /mcp ==");
const withTok = mcpReqs.find((r) => r.auth);
check(!!withTok, `bắt được request ${withTok?.method} ${withTok?.url} có Authorization`);
let tok = null, claims = {};
if (withTok) {
  check(/^Bearer eyJ/.test(withTok.auth), "Authorization: Bearer <JWT>");
  tok = withTok.auth.replace(/^Bearer /, ""); claims = b64j(tok);
  console.log("  claims:", JSON.stringify({ iss: claims.iss, azp: claims.azp, aud: claims.aud, scope: claims.scope, sub: claims.sub, preferred_username: claims.preferred_username, realm_access: claims.realm_access, exp: claims.exp }));
  const aud = [].concat(claims.aud || []);
  check(claims.azp === CID, `azp=${claims.azp}`);
  check(aud.includes(`https://${H.mcp}/mcp`), `aud chứa MCP resource https://${H.mcp}/mcp (aud=${JSON.stringify(claims.aud)})`);
  check((claims.scope || "").split(" ").includes("mcp-xt"), `scope chứa mcp-xt ("${claims.scope}")`);
  check(claims.iss === `https://${H.auth}/realms/xt`, `iss=${claims.iss}`);
  check(!(claims.realm_access?.roles || []).some((r) => ["dept_head", "director", "chief_accountant", "payment_accountant", "hr", "hr_pii", "it_admin", "admin"].includes(r)), "user test không có role approver");
}

console.log("== Lưu trữ phía trình duyệt (origin Desk) ==");
const toks = [tokenResponses[0]?.access_token, tokenResponses[0]?.refresh_token, tokenResponses[0]?.id_token, tok].filter(Boolean);
const dump = await page.evaluate(() => ({ ls: { ...localStorage }, ss: { ...sessionStorage }, idb: indexedDB.databases ? null : null, docCookie: document.cookie }));
const cookies = await ctx.cookies(`https://${H.desk}/`);
const lsS = JSON.stringify(dump.ls), ssS = JSON.stringify(dump.ss), ckS = JSON.stringify(cookies) + dump.docCookie;
const hasTok = (s) => toks.some((t) => s.includes(t)) || /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./.test(s);
console.log(`  localStorage keys=${Object.keys(dump.ls)} sessionStorage keys=${Object.keys(dump.ss)} cookies(Desk origin)=${cookies.length}`);
check(!hasTok(lsS), "localStorage không chứa token/JWT");
check(!hasTok(ssS), "sessionStorage không chứa token/JWT");
check(!hasTok(ckS) && cookies.length === 0, "cookie origin Desk không chứa token (và rỗng)");
check(toks.length >= 2, `đã thấy ${toks.length} loại token để đối chiếu (access/refresh/id)`);

console.log("== Phản hồi MCP thật cho token này ==");
if (withTok) {
  const { execFileSync } = await import("node:child_process");
  const out = execFileSync("curl", ["-s", "-w", "\nHTTP %{http_code}", "-H", `Host: ${H.mcp}`, "-H", `CF-Connecting-IP: ${ip}`, "-H", `Authorization: ${withTok.auth}`, "-H", "Content-Type: application/json", "-H", "Accept: application/json, text/event-stream", "-d", '{"jsonrpc":"2.0","id":1,"method":"tools/list"}', `http://${GW}/mcp`]).toString();
  console.log(`  POST https://${H.mcp}/mcp (token trên) -> ${out.replace(/\n/g, " ").slice(0, 200)}`);
}
await browser.close(); srv.close();
console.log(`FAIL=${fails}`);
process.exit(fails ? 1 : 0);
