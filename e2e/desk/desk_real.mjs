// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Desk THẬT (container xt-desk sau APISIX) + Keycloak thật + xT thật, Chromium headless của Playwright.
// Không phục vụ bản build qua route: mọi https://xt*.freepro.online đều đi APISIX 127.0.0.1:${APISIX_HTTP_PORT:-9080} với Host gốc.
// LLM (chỉ chế độ chat): URL DESK_LLM_URL nướng trong ảnh Desk (http://ollama:11434/v1) -> Ollama host 127.0.0.1:11434.
//   stdin JSON: ["oidc", user, password, totp_seed]
//   stdin JSON: ["chat", user, password, totp_seed, prompt]
// In JSON một dòng cuối: {"checks":[...], "fails":n, ...}. Mã thoát 1 khi có FAIL.
import { createRequire } from "node:module";
import http from "node:http";
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.resolve(here, "../../desk/package.json"));
const { chromium } = require("@playwright/test");

const [MODE, USER, PASS, SEED, PROMPT] = JSON.parse(readFileSync(0, "utf8"));
const GW = process.env.GW || `127.0.0.1:${process.env.APISIX_HTTP_PORT || 9080}`;
const LLM = process.env.LLM_HOST || "127.0.0.1:11434";
const H = { desk: "xt.freepro.online", auth: "xt-auth.freepro.online", mcp: "xt-mcp.freepro.online", api: "xt-api.freepro.online" };
const ip = `203.0.113.${1 + Math.floor(Math.random() * 250)}`;
const out = { mode: MODE, checks: [], fails: 0, mcp: [], llm: [], tools: [], http: [] };
const check = (ok, msg) => { out.checks.push({ ok: !!ok, msg }); if (!ok) out.fails++; console.log(`  ${ok ? "PASS" : "FAIL"}  ${msg}`); };
const b64j = (s) => JSON.parse(Buffer.from(s.split(".")[1], "base64url").toString());

function totp(seed) {
  const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const mac = crypto.createHmac("sha1", Buffer.from(seed, "utf8")).update(c).digest();
  const o = mac[mac.length - 1] & 0xf;
  return String((mac.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, "0");
}

function forward(target, req, hostHeader) {
  const u = new URL(req.url());
  const headers = { ...req.headers(), host: hostHeader, "cf-connecting-ip": ip, "x-forwarded-proto": "https" };
  delete headers["accept-encoding"]; delete headers["content-length"];
  const [th, tp] = target.split(":");
  const body = ["GET", "HEAD"].includes(req.method()) ? null : req.postDataBuffer();
  return new Promise((resolve, reject) => {
    const rq = http.request({ host: th, port: tp, path: u.pathname + u.search, method: req.method(), headers }, (res) => {
      const ch = []; res.on("data", (x) => ch.push(x)); res.on("end", () => resolve({ status: res.statusCode, raw: res.headers, buf: Buffer.concat(ch) }));
    });
    rq.on("error", reject); if (body) rq.write(body); rq.end();
  });
}

async function viaGateway(route, target, hostHeader) {
  const req = route.request();
  const r = await forward(target, req, hostHeader);
  if (new URL(req.url()).pathname === "/mcp" && req.method() === "POST") {
    try {
      const call = JSON.parse(req.postData() || "{}");
      if (call.method === "tools/call") {
        const raw = r.buf.toString();
        const data = raw.split("\n").find((s) => s.startsWith("data:"));
        const value = JSON.parse(data ? data.slice(5) : raw);
        const result = value.result;
        const envelope = result?.structuredContent || JSON.parse(result?.content?.[0]?.text || "{}");
        (out.tool_outcomes ||= []).push({ name: call.params?.name, ok: envelope.ok, code: envelope.error?.code,
          reason: envelope.error?.details?.reason, missing: envelope.error?.details?.missing,
          fields: envelope.error?.details?.fields, valid_fields: envelope.error?.details?.valid_fields });
      }
    } catch { /* Diagnostics must not change the real protocol response. */ }
  }
  const hdr = {};
  for (const [k, v] of Object.entries(r.raw)) if (!["content-encoding", "content-length", "transfer-encoding", "set-cookie", "connection"].includes(k)) hdr[k] = Array.isArray(v) ? v.join(", ") : v;
  if (r.raw["set-cookie"]) hdr["set-cookie"] = r.raw["set-cookie"].join("\n");
  if (r.status >= 400) out.http.push(`${r.status} ${req.method()} ${req.url().slice(0, 120)}` + (req.url().includes("/openid-connect/token") ? ` ${r.buf.toString().slice(0, 200)}` : ""));
  // Chromium không route lại đích redirect do route.fulfill tạo ra: điều hướng 3xx -> trang location.replace (giữ Set-Cookie).
  if (r.status >= 300 && r.status < 400 && hdr.location && req.isNavigationRequest()) {
    const to = new URL(r.raw.location, req.url()).href; delete hdr.location;
    return route.fulfill({ status: 200, headers: { ...hdr, "content-type": "text/html" }, body: `<!doctype html><script>location.replace(${JSON.stringify(to)})</script>` });
  }
  return route.fulfill({ status: r.status, headers: hdr, body: r.buf });
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true });
await ctx.route(/^https:\/\/xt(-auth|-mcp|-api)?\.freepro\.online\//, async (route) => {
  const req = route.request(); const u = new URL(req.url());
  try {
    if (u.pathname === "/mcp" && req.method() === "POST") {
      const auth = req.headers()["authorization"] || null; let m = null;
      try { m = JSON.parse(req.postData() || "{}"); } catch {}
      out.mcp.push({ host: u.host, auth: !!auth, method: m?.method, tool: m?.params?.name });
      if (m?.method === "tools/call") out.tools.push(m.params.name);
    }
    return await viaGateway(route, GW, u.host);
  } catch (e) { console.log("  route lỗi", u.host, u.pathname, e.message); return route.abort(); }
});
await ctx.route(/^http:\/\/ollama:11434\//, (route) => viaGateway(route, LLM, LLM));
const page = await ctx.newPage();
page.on("pageerror", (e) => out.http.push(`pageerror ${e.message.slice(0, 200)}`));
page.on("response", (r) => {
  if (new URL(r.url()).pathname === "/mcp" && r.request().method() === "POST") {
    let method = "unknown";
    try { method = JSON.parse(r.request().postData() || "{}").method; } catch {}
    out.http.push(`${r.status()} POST /mcp ${method}`);
  }
  if (new URL(r.url()).pathname === "/llm/chat/completions") {
    const headers = r.request().headers();
    out.llm.push({ status: r.status(), scheme: (headers.authorization || "").split(" ")[0], proof: !!headers.dpop });
  }
});

let token = null;
page.on("request", (r) => {
  const a = r.url().endsWith("/mcp") && r.headers()["authorization"];
  if (a) { token = a.replace(/^(Bearer|DPoP) /, ""); out.scheme = a.split(" ")[0]; out.dpop_proof = !!r.headers()["dpop"]; }
});

try {
  const res = await page.goto(`https://${H.desk}/`);
  check(res && res.status() === 200, `Desk (container xt-desk qua APISIX) trả 200 (${res && res.status()})`);
  check((await page.content()).length > 200 && !!(await page.$("#chat")), "trang Desk có khung chat");
  await page.waitForSelector("#login", { timeout: 20000 });
  check(out.mcp.length === 0, "chưa đăng nhập: không gọi /mcp");
  await page.click("#login");
  await page.waitForURL(new RegExp(`^https://${H.auth}/`), { timeout: 20000 });
  await page.fill("#username", USER);
  if (!(await page.$("#password"))) { await page.click("#kc-login"); await page.waitForSelector("#password", { timeout: 15000 }); }
  await page.fill("#password", PASS);
  await page.click("#kc-login");
  const otp = await page.waitForSelector("#otp", { timeout: 6000 }).catch(() => null);
  if (otp) { check(!!SEED, "Keycloak đòi OTP, có seed TOTP"); await page.fill("#otp", totp(SEED || "")); await page.click("#kc-login"); }
  await page.waitForURL(new RegExp(`^https://${H.desk}/`), { timeout: 30000 });
  await page.waitForFunction(() => !document.querySelector("#login"), null, { timeout: 30000 }).catch(() => {});
  for (let i = 0; i < 60 && !out.mcp.some((r) => r.auth && r.method === "tools/list"); i++) await page.waitForTimeout(250);
  check(!(await page.$("#login")), "đã đăng nhập (nút login biến mất)");
  check(out.mcp.some((r) => r.auth && r.method === "tools/list"), "Desk gọi tools/list có Bearer");
  if (token) {
    const c = b64j(token);
    out.claims = { azp: c.azp, aud: c.aud, organization: c.organization, scope: c.scope };
    check(c.azp === "xt-desk-agent", `azp=${c.azp}`);
    check([].concat(c.aud).includes(`https://${H.mcp}/mcp`), "aud = resource MCP");
    if (process.env.XT_DPOP_REQUIRED === "true") {
      check(out.scheme === "DPoP" && out.dpop_proof && !!c.cnf?.jkt, `DPoP: scheme ${out.scheme}, proof ${out.dpop_proof}, cnf.jkt ${!!c.cnf?.jkt}`);
    }
  }
  const enabled = await page.waitForSelector("#box:not([disabled])", { timeout: 15000 }).catch(() => null);
  out.notes = (await page.locator(".note").allTextContents()).map((s) => s.slice(0, 800));
  check(!!enabled, "ô chat bật (kết nối MCP thật thành công)");
  const ls = JSON.stringify(await page.evaluate(() => ({ ...localStorage, ...sessionStorage })));
  check(!/eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./.test(ls), "local/sessionStorage không chứa JWT");

  if (MODE === "chat") {
    const t0 = Date.now();
    await page.fill("#box", PROMPT);
    await page.click("#send");
    const agentDeadline = Date.now() + 420000;
    while (Date.now() < agentDeadline) {
      if (out.http.some((s) => s.startsWith("pageerror "))) {
        check(false, "lỗi JavaScript trong lượt agent");
        break;
      }
      if (await page.locator("#send").isEnabled().catch(() => false)) break;
      await page.waitForTimeout(250);
    }
    check(await page.locator("#send").isEnabled().catch(() => false), "lượt agent hoàn tất trong thời hạn");
    out.seconds = (Date.now() - t0) / 1000;
    check(out.llm.some((r) => r.status === 200), "Desk gọi LLM thật qua gateway và proxy xác thực -> 200");
    if (process.env.XT_DPOP_REQUIRED === "true") check(out.llm.some((r) => r.status === 200 && r.scheme === "DPoP" && r.proof), "request LLM có DPoP proof");
    out.notes = (await page.locator(".note").allTextContents()).map((s) => s.slice(0, 800));
    const answers = page.getByTestId("msg-assistant");
    out.answer = (await answers.count() ? await answers.last().innerText() : "").slice(0, 800);
    check(out.tools.includes("create_record") || out.tools.includes("submit_request"), `LLM gọi tool ghi (tools: ${out.tools.join(",")})`);
    check(out.tools.includes("submit_request"), "LLM gửi duyệt (submit_request)");
    let card = false;
    const until = Date.now() + 15000;
    while (!card && Date.now() < until) {
      for (const f of page.frames()) {
        if (f === page.mainFrame()) continue;
        const content = await f.evaluate(() => ({
          title: document.querySelector("#card #title")?.textContent || "",
          body: document.querySelector("#card #body")?.textContent || "",
        })).catch(() => ({ title: "", body: "" }));
        if (content.title === "Đề xuất đã gửi duyệt" && content.body.includes("in_review")) card = true;
      }
      if (!card) await page.waitForTimeout(100);
    }
    check(card, "MCP App approval-card hiển thị đề xuất đã gửi duyệt (in_review)");
    await page.screenshot({ path: process.env.SHOT || "/tmp/xt-e2e-desk-chat.png" }).catch(() => {});
  }
} catch (e) {
  check(false, `lỗi luồng: ${e.message.split("\n")[0]}`);
}
await browser.close();
console.log("RESULT " + JSON.stringify(out));
process.exit(out.fails ? 1 : 0);
