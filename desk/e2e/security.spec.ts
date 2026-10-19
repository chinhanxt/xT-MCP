// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Hồi quy cho docs/archive/audit-desk.md (BUG-1..5, 7). Các "App" thử nghiệm ở e2e/evil_server.py chỉ là fixture.
import { login } from "./helpers";
import { expect, test, type Frame, type Page } from "@playwright/test";

const EVIL = "http://127.0.0.1:19503";
const COLLECT = "http://127.0.0.1:19501"; // origin xT giả (cổng LLM giả) + /pii/reveal
const HITS = "http://127.0.0.1:19501/__hits";
const OTHER_HITS = "http://127.0.0.1:19505";

async function hits(page: Page): Promise<string[]> {
  return ((await (await page.request.get(HITS)).json()) as Array<{ url: string }>).map((h) => h.url);
}
// collector thứ hai ghi vào cùng danh sách hits của mock-llm (xem e2e/mock-llm.mjs)
const hitsOther = hits;
async function calls(page: Page): Promise<Array<{ tool: string; n?: number }>> {
  return (await page.request.get(`${EVIL}/__calls`)).json();
}

async function runApp(page: Page, tool: string): Promise<Frame> {
  await login(page);
  await page.fill("#box", `chạy ${tool}`);
  await page.click("#send");
  await expect(page.locator(".chip", { hasText: tool })).toContainText("xong");
  let f: Frame | undefined;
  await expect
    .poll(async () => {
      for (const fr of page.frames()) {
        if (fr !== page.mainFrame() && (await fr.locator("#out").count().catch(() => 0)) > 0) return (f = fr), true;
      }
      return false;
    }, { timeout: 20_000 })
    .toBe(true);
  return f!;
}
const outText = async (f: Frame) => JSON.parse((await f.locator("#out").textContent()) || "{}");

test.beforeEach(async ({ page }) => {
  void page;
});

for (const [bug, tool, marker] of [
  ["BUG-2a", "app_csp_comment", "csp-comment"],
  ["BUG-2b", "app_csp_header", "csp-header"],
  ["BUG-2c", "app_csp_prehead", "csp-prehead"],
] as const) {
  test(`${bug}: CSP áp trước mọi script, không bị regex lừa (${tool})`, async ({ page }) => {
    const f = await runApp(page, tool);
    await expect(f.locator("#out")).toContainText(/BLOCKED|LEAK/);
    expect((await outText(f)).r).toBe("BLOCKED");
    expect(await hits(page)).not.toContain(`/${marker}`);
  });
}

test("BUG-3: App không điều hướng được iframe (form + location), bridge không chuyển tiếp sau điều hướng", async ({ page }) => {
  await runApp(page, "app_nav");
  await page.waitForTimeout(3500); // đủ cho form.submit (300ms), location (700ms) và lời gọi tool sau đó (2500ms)
  // collector (origin khác xT) không được nhận request điều hướng nào; trang "tấn công" của nó không bao giờ nạp
  expect((await (await page.request.get("http://127.0.0.1:19505/__none")).text()).length).toBeGreaterThan(0); // collector sống
  expect((await hitsOther(page)).filter((u) => u.startsWith("/nav-"))).toEqual([]);
  expect((await calls(page)).filter((c) => c.tool === "ping_tool" && c.n === 7)).toEqual([]);
});

test("BUG-1 + BUG-4: App chỉ gọi tool app-only, validate tham số, ui:// only, bị rate limit", async ({ page }) => {
  const f = await runApp(page, "app_bridge");
  await expect(f.locator("#out")).toContainText("burst_ok", { timeout: 20_000 });
  const r = await outText(f);
  expect(r.secret_admin).toBe("DENIED");
  expect(r.decide_request).toBe("DENIED");
  expect(r.unknown).toBe("DENIED");
  expect(r.bad_args).toBe("DENIED");
  expect(r.good).toBe("OK");
  expect(r.read_file).toBe("DENIED");
  expect(r.burst_ok + r.burst_denied).toBe(100);
  expect(r.burst_ok).toBeLessThanOrEqual(20);
  expect(r.burst_denied).toBeGreaterThan(70);
  const c = await calls(page);
  expect(c.filter((x) => x.tool === "secret_admin" || x.tool === "decide_request")).toEqual([]);
  expect(c.filter((x) => x.tool === "ping_tool").length).toBeLessThanOrEqual(25);
});

test("BUG-5: ui/open-link cần user activation; origin ngoài xT phải qua hộp thoại có URL đầy đủ", async ({ page }) => {
  const dialogs: string[] = [];
  page.on("dialog", async (d) => {
    dialogs.push(d.message());
    await d.dismiss();
  });
  const popups: string[] = [];
  page.on("popup", (p) => popups.push(p.url()));
  const f = await runApp(page, "app_open_link");
  await expect(f.locator("#out")).toContainText("auto");
  expect((await outText(f)).auto).toBe("DENIED"); // không có thao tác người dùng
  await f.locator("#go").click();
  await expect.poll(() => dialogs.length).toBe(1);
  expect(dialogs[0]).toContain("https://evil.example/phish");
  expect(popups).toEqual([]); // người dùng từ chối
  page.removeAllListeners("dialog");
  await f.locator("#goxt").click();
  await expect.poll(() => popups.length).toBe(1); // origin xT: mở thẳng
  expect(popups[0]).toContain("/desk/confirm/abc");
});

test("BUG-7: _meta.ui.csp chỉ cho connect tới origin xT; domain khác, wildcard, resource/frame bị bỏ", async ({ page }) => {
  const f = await runApp(page, "app_connect");
  await expect(f.locator("#out")).toContainText("xt");
  const r = await outText(f);
  expect(r.xt).toBe("OK"); // kênh /pii/reveal tới xT
  expect(r.other).toBe("BLOCKED");
  expect(await hits(page)).toContain("/pii/reveal");
  expect(await hits(page)).not.toContain("/connect-other");
  void COLLECT;
});
