// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Đăng nhập OIDC (auth-code + PKCE, client xt-desk-agent) với Keycloak giả; token chỉ ở bộ nhớ; fail closed.
import { expect, test } from "@playwright/test";
import { login } from "./helpers";

const OIDC = "http://127.0.0.1:19506";
type Ev = { ev: string; grant?: string; ok?: boolean; reused?: boolean; client_id?: string; method?: string; scope?: string; redirect_uri?: string };
const oidcLog = async (page: import("@playwright/test").Page): Promise<Ev[]> => (await page.request.get(`${OIDC}/__log`)).json();

test("chưa đăng nhập: fail closed, không có request tới /mcp hay /llm", async ({ page }) => {
  const calls: string[] = [];
  page.on("request", (r) => {
    const p = new URL(r.url()).pathname;
    if (p.startsWith("/mcp") || p.startsWith("/llm")) calls.push(p);
  });
  await page.goto("/");
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.locator("#box")).toHaveCount(0); // khung chat chỉ có sau khi đăng nhập
  await page.waitForTimeout(800);
  expect(calls).toEqual([]);
});

test("đăng nhập: client xt-desk-agent, PKCE S256, scope mcp-xt; bearer của người dùng tới MCP và LLM", async ({ page }) => {
  const before = (await oidcLog(page)).length;
  const auths: string[] = [];
  page.on("request", (r) => {
    if (new URL(r.url()).pathname.startsWith("/mcp")) auths.push(r.headers()["authorization"] ?? "");
  });
  await login(page);
  const log = (await oidcLog(page)).slice(before);
  const authz = log.find((e) => e.ev === "authorize")!;
  expect(authz.client_id).toBe("xt-desk-agent");
  expect(authz.method).toBe("S256");
  expect(authz.scope).toContain("mcp-xt");
  expect(authz.redirect_uri).toBe("http://localhost:19502/auth/callback");
  expect(log.find((e) => e.ev === "token" && e.grant === "authorization_code")?.ok).toBe(true);
  await expect(page).toHaveURL("http://localhost:19502/"); // callback đã được dọn khỏi URL

  await page.fill("#box", "xin chào");
  await page.click("#send");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("Xin chào");
  expect(auths.length).toBeGreaterThan(0);
  for (const a of auths) expect(a).toMatch(/^Bearer at-\d+$/);
  const reqs = (await (await page.request.get("http://127.0.0.1:19501/__requests")).json()) as Array<{ __auth: string | null }>;
  expect(reqs.at(-1)!.__auth).toMatch(/^Bearer at-\d+$/);
});

test("token chỉ trong bộ nhớ: không localStorage/sessionStorage, reload là đăng xuất", async ({ page }) => {
  await login(page);
  const dump = await page.evaluate(() => JSON.stringify({ l: { ...localStorage }, s: { ...sessionStorage }, c: document.cookie }));
  expect(dump).not.toMatch(/at-\d|rt-\d|Bearer|verifier/);
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  await page.reload();
  await expect(page.getByTestId("login")).toBeVisible();
  await expect(page.locator("#box")).toHaveCount(0); // khung chat chỉ có sau khi đăng nhập
});

test("refresh token rotation: token sắp hết hạn được làm mới, đồng thời nhiều request vẫn không dùng lại refresh token cũ", async ({ page }) => {
  const before = (await oidcLog(page)).length;
  await login(page);
  await page.fill("#box", "Cho tôi xem sơ đồ tổ chức"); // gọi LLM + MCP + LLM (+ app) liên tiếp
  await page.click("#send");
  await expect(page.locator(".chip", { hasText: "show_org_chart" })).toContainText("xong");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("sơ đồ tổ chức");
  const refreshes = (await oidcLog(page)).slice(before).filter((e) => e.ev === "token" && e.grant === "refresh_token");
  expect(refreshes.length).toBeGreaterThanOrEqual(2);
  expect(refreshes.every((e) => e.ok && !e.reused)).toBe(true);
  await expect(page.getByTestId("login")).toHaveCount(0); // vẫn đăng nhập
});

test("callback sai state (CSRF): từ chối, không đổi token", async ({ page }) => {
  const before = (await oidcLog(page)).length;
  await page.goto("/auth/callback?code=abc&state=sai");
  await expect(page.getByTestId("login")).toContainText(/state|phiên đăng nhập/i);
  await expect(page.locator("#box")).toHaveCount(0); // khung chat chỉ có sau khi đăng nhập
  expect((await oidcLog(page)).slice(before).filter((e) => e.ev === "token")).toEqual([]);
});
