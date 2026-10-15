// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { expect, test, type Frame, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const IMG = resolve(process.cwd(), "../docs/img");
const MCP_LOG = join(tmpdir(), "xt-desk-e2e-19500", "server-calls.log");
const LLM_REQS = "http://127.0.0.1:19501/__requests";

async function orgFrame(page: Page, timeout = 30_000): Promise<Frame> {
  let found: Frame | undefined;
  await expect
    .poll(
      async () => {
        for (const f of page.frames()) {
          if (f !== page.mainFrame() && (await f.locator("g.node").count().catch(() => 0)) > 0) {
            found = f;
            return true;
          }
        }
        return false;
      },
      { timeout },
    )
    .toBe(true);
  return found!;
}

test("chat: tool-call chip, MCP App inline, markdown an toàn, UI->tool qua bridge", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.locator("header .meta")).toContainText("mock-model");
  await expect(page.locator("#box")).toBeEnabled();
  await page.screenshot({ path: `${IMG}/desk-1-start.png` });

  await page.fill("#box", "Cho tôi xem sơ đồ tổ chức");
  await page.click("#send");

  const chip = page.locator(".chip", { hasText: "show_org_chart" });
  await expect(chip).toBeVisible();
  const frame = await orgFrame(page);
  await expect(chip).toContainText("xong");

  // markdown: in đậm thật, không còn dấu ** thô; HTML/JS độc bị loại
  const msg = page.getByTestId("msg-assistant").last();
  await expect(msg.locator("strong").first()).toHaveText("sơ đồ tổ chức");
  await expect(msg.locator("li")).toHaveCount(2);
  await expect(msg).not.toContainText("**");
  await expect(msg.locator(".pii")).toHaveText("[đã che]");
  await expect(msg.locator("img, script, a[href^='javascript']")).toHaveCount(0);
  expect(await page.evaluate(() => (window as unknown as { __xss?: number }).__xss)).toBeUndefined();

  // sandbox khác origin với Desk
  const sandboxFrame = page.frames().find((f) => f.url().startsWith("http://127.0.0.1:19502/sandbox.html"));
  expect(sandboxFrame, "iframe sandbox phải ở origin 127.0.0.1").toBeTruthy();
  await page.screenshot({ path: `${IMG}/desk-2-orgchart-inline.png` });

  // UI -> tool (get_employee là tool app-only) đi qua client MCP của Desk
  await frame.click("g.node[data-id='4']");
  await expect(frame.locator("#detail")).toContainText("Tuan");
  await expect(frame.locator("#detail")).toContainText("Minh");
  await page.screenshot({ path: `${IMG}/desk-3-ui-tool-call.png` });
  await expect.poll(() => readFileSync(MCP_LOG, "utf8")).toContain('"get_employee"');
  expect(readFileSync(MCP_LOG, "utf8")).toContain('"args": {"id": 4}');

  // chip mở chi tiết
  await chip.click();
  await expect(page.locator(".tool-detail").first()).toContainText("employees");

  // LLM nhận đúng tool đã lọc + reasoning_effort, và không thấy tool app-only
  const reqs = (await (await page.request.get(LLM_REQS)).json()) as Array<{ model: string; reasoning_effort?: string; stream: boolean; tools: Array<{ function: { name: string } }> }>;
  expect(reqs.length).toBe(2); // gọi tool + trả lời
  expect(reqs[0].model).toBe("mock-model");
  expect(reqs[0].reasoning_effort).toBe("none");
  expect(reqs[0].stream).toBe(true);
  expect(reqs[0].tools.map((t) => t.function.name).sort()).toEqual(["show_approval_card", "show_org_chart"]);
  expect(errors).toEqual([]);
});

test("chat thường không gọi tool", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#box")).toBeEnabled();
  await page.fill("#box", "xin chào");
  await page.click("#send");
  await expect(page.getByTestId("msg-assistant").last()).toContainText("Xin chào, tôi có thể giúp gì?");
  await expect(page.locator(".chip")).toHaveCount(0);
});
