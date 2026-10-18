// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Kiểm trình duyệt cho bản tích hợp trên stack compose thật (APISIX + Keycloak + xt-api + studio + xt-desk):
// đăng nhập Desk thật, mở Studio (khu I) qua /studio-api với phiên xT, mở WareTrack, gửi một câu chat khi LLM tắt.
//   stdin JSON: [user, password, totp_seed]   env: GW=127.0.0.1:<APISIX_HTTP_PORT>, SHOT_DIR=<thư mục ảnh>
// In JSON một dòng cuối: {"checks":[...], "fails":n}. Mã thoát 1 khi có FAIL.
import { createRequire } from "node:module";
import http from "node:http";
import crypto from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(path.resolve(here, "../../desk/package.json"));
const { chromium } = require("@playwright/test");

const [USER, PASS, SEED] = JSON.parse(readFileSync(0, "utf8"));
const GW = process.env.GW || `127.0.0.1:${process.env.APISIX_HTTP_PORT || 9080}`;
const SHOT = process.env.SHOT_DIR || "/tmp";
const DESK = process.env.DESK_HOST || "xt.freepro.online";
const out = { checks: [], fails: 0, studio_api: [], llm: [], errors: [] };
const check = (ok, msg) => { out.checks.push({ ok: !!ok, msg }); if (!ok) out.fails++; console.log(`  ${ok ? "PASS" : "FAIL"}  ${msg}`); };
function totp(seed) {
  const c = Buffer.alloc(8); c.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const mac = crypto.createHmac("sha1", Buffer.from(seed, "utf8")).update(c).digest();
  const o = mac[mac.length - 1] & 0xf;
  return String((mac.readUInt32BE(o) & 0x7fffffff) % 1000000).padStart(6, "0");
}
const ip = `203.0.113.${1 + Math.floor(Math.random() * 250)}`;
function forward(req, host) {
  const u = new URL(req.url());
  const headers = { ...req.headers(), host, "cf-connecting-ip": ip, "x-forwarded-proto": "https" };
  delete headers["accept-encoding"]; delete headers["content-length"];
  const [th, tp] = GW.split(":");
  const body = ["GET", "HEAD"].includes(req.method()) ? null : req.postDataBuffer();
  return new Promise((resolve, reject) => {
    const rq = http.request({ host: th, port: tp, path: u.pathname + u.search, method: req.method(), headers }, (res) => {
      const ch = []; res.on("data", (x) => ch.push(x)); res.on("end", () => resolve({ status: res.statusCode, raw: res.headers, buf: Buffer.concat(ch) }));
    });
    rq.on("error", reject); if (body) rq.write(body); rq.end();
  });
}
async function viaGateway(route) {
  const req = route.request(); const u = new URL(req.url());
  const r = await forward(req, u.host);
  const hdr = {};
  for (const [k, v] of Object.entries(r.raw)) if (!["content-encoding", "content-length", "transfer-encoding", "set-cookie", "connection"].includes(k)) hdr[k] = Array.isArray(v) ? v.join(", ") : v;
  if (r.raw["set-cookie"]) hdr["set-cookie"] = r.raw["set-cookie"].join("\n");
  if (r.status >= 300 && r.status < 400 && hdr.location && req.isNavigationRequest()) {
    const to = new URL(r.raw.location, req.url()).href; delete hdr.location;
    return route.fulfill({ status: 200, headers: { ...hdr, "content-type": "text/html" }, body: `<!doctype html><script>location.replace(${JSON.stringify(to)})</script>` });
  }
  return route.fulfill({ status: r.status, headers: hdr, body: r.buf });
}
async function kcLogin(page) {
  for (let i = 0; i < 12; i++) {
    if (new URL(page.url()).host === DESK) return;
    const state = await Promise.race([
      page.waitForURL(new RegExp(`^https://${DESK}/`), { timeout: 15000 }).then(() => "desk"),
      page.waitForSelector("#username:visible", { timeout: 15000 }).then(() => "username"),
      page.waitForSelector("#password:visible", { timeout: 15000 }).then(() => "password"),
      page.waitForSelector("#otp:visible", { timeout: 15000 }).then(() => "otp"),
      page.waitForSelector('button[name="kc.org"]', { timeout: 15000 }).then(() => "org"),
    ]).catch(() => "timeout");
    if (state === "desk") return;
    if (state === "timeout") throw new Error(`kcLogin kẹt ở ${page.url().slice(0, 100)}`);
    if (state === "username") { await page.fill("#username", USER); await page.click("#kc-login"); }
    else if (state === "password") { await page.fill("#password", PASS); await page.click("#kc-login"); }
    else if (state === "otp") { await page.fill("#otp", totp(SEED || "")); await page.click("#kc-login"); }
    else await page.click('button[name="kc.org"]');
    await page.waitForLoadState("domcontentloaded").catch(() => {});
  }
  throw new Error("kcLogin: quá nhiều bước");
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1440, height: 900 } });
await ctx.route(/^https:\/\/xt(-auth|-mcp|-api)?\.freepro\.online\//, (route) => viaGateway(route).catch((e) => { out.errors.push(e.message); return route.abort(); }));
ctx.on("response", (r) => {
  const p = new URL(r.url()).pathname;
  if (p.startsWith("/studio-api/")) out.studio_api.push(`${r.status()} ${r.request().method()} ${p}`);
  if (p === "/llm/chat/completions") out.llm.push(r.status());
});
const page = await ctx.newPage();
page.on("pageerror", (e) => out.errors.push(`pageerror ${e.message.slice(0, 160)}`));
try {
  const res = await page.goto(`https://${DESK}/`);
  check(res?.status() === 200, `Desk 200 (${res?.status()})`);
  await page.waitForSelector("#login", { timeout: 20000 });
  await page.click("#login");
  await page.waitForURL(/^https:\/\/xt-auth\.freepro\.online\//, { timeout: 20000 });
  await kcLogin(page);
  await page.waitForSelector(".logout", { timeout: 30000 });
  check(true, `đăng nhập ${USER}`);

  // Studio (khu I): iframe /studio/?integrated=1, gọi /studio-api/* qua xt-api bằng phiên Desk
  await page.getByRole("button", { name: /^I/ }).first().click();
  const frameEl = await page.waitForSelector('iframe[data-xt-scope="studio"]', { timeout: 20000 });
  const frame = await frameEl.contentFrame();
  for (let i = 0; i < 80 && !out.studio_api.some((s) => s.startsWith("200 ")); i++) await page.waitForTimeout(250);
  check(out.studio_api.some((s) => s.startsWith("200 ") && s.endsWith("/studio-api/session")), `Studio: /studio-api/session 200 với phiên xT (${out.studio_api.slice(0, 4).join(", ")})`);
  check(!out.studio_api.some((s) => /^(401|403|5\d\d) /.test(s)), "Studio: không có /studio-api 401/403/5xx");
  const studioText = await frame.evaluate(() => document.body.innerText).catch(() => "");
  check(studioText.length > 20 && !/Thiếu xác thực|Không đủ quyền/.test(studioText), `Studio hiện nội dung (${studioText.slice(0, 80).replace(/\s+/g, " ")})`);
  await page.screenshot({ path: `${SHOT}/unified-studio.png` });

  // WareTrack 3D
  const wt = await ctx.newPage();
  const wres = await wt.goto(`https://${DESK}/waretrack/`);
  check(wres?.status() === 200, `/waretrack/ 200 (${wres?.status()})`);
  const canvas = await wt.waitForSelector("canvas", { timeout: 20000 }).then(() => true).catch(() => false);
  check(canvas, "WareTrack vẽ canvas 3D");
  await wt.screenshot({ path: `${SHOT}/unified-waretrack.png` });
  await wt.close();

  // Desk chat khi LLM tắt: ghi chú "Chưa cấu hình LLM", không phải lỗi HTTP thô
  await page.getByRole("button", { name: /^Desk/ }).first().click();
  await page.waitForSelector("#box:not([disabled])", { timeout: 30000 });
  await page.fill("#box", "Xin chào");
  await page.keyboard.press("Enter");
  let note = "";
  for (let i = 0; i < 120; i++) { note = await page.locator(".note").allInnerTexts().then((x) => x.join(" | ")); if (/LLM/.test(note)) break; await page.waitForTimeout(250); }
  check(/^Chưa cấu hình LLM/.test(note.split(" | ").find((x) => /LLM/.test(x)) || ""), `chat: ghi chú "${note.slice(0, 90)}" (LLM HTTP ${out.llm.join(",")})`);
  check(!/Lỗi: LLM trả lỗi/.test(note), "chat: không hiện lỗi HTTP thô");
  await page.screenshot({ path: `${SHOT}/unified-desk-llm-off.png` });
} catch (e) {
  check(false, `ngoại lệ: ${e.message}`);
  await page.screenshot({ path: `${SHOT}/unified-error.png` }).catch(() => {});
}
await browser.close();
console.log(JSON.stringify(out));
process.exitCode = out.fails ? 1 : 0;
