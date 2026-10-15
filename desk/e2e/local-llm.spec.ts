// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chạy với LLM thật (ollama). Không dùng trong CI: DESK_E2E_LOCAL_LLM=1, cần DESK_LLM_URL + DESK_LLM_MODEL.
import { expect, test } from "@playwright/test";
import { resolve } from "node:path";

const IMG = resolve(process.cwd(), "../docs/img");

test("LLM local thật gọi show_org_chart và sơ đồ hiện inline", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("#box")).toBeEnabled();
  const t0 = Date.now();
  await page.fill("#box", "Cho tôi xem sơ đồ tổ chức");
  await page.click("#send");
  await expect(page.locator(".chip", { hasText: "show_org_chart" })).toBeVisible({ timeout: 180_000 });
  let frame;
  await expect
    .poll(async () => {
      for (const f of page.frames()) if (f !== page.mainFrame() && (await f.locator("g.node").count().catch(() => 0)) > 0) return (frame = f), true;
      return false;
    }, { timeout: 60_000 })
    .toBe(true);
  const renderSeconds = (Date.now() - t0) / 1000;
  await expect(page.locator("#send")).toBeEnabled({ timeout: 180_000 }); // agent xong (đã có câu trả lời cuối)
  await page.screenshot({ path: `${IMG}/desk-4-local-llm.png` });
  await frame!.click("g.node[data-id='4']");
  await expect(frame!.locator("#detail")).toContainText("Tuan");
  console.log(`LOCAL_LLM render_seconds=${renderSeconds.toFixed(1)} total_seconds=${((Date.now() - t0) / 1000).toFixed(1)}`);
  console.log("LOCAL_LLM final answer:", (await page.getByTestId("msg-assistant").last().innerText()).replace(/\n/g, " "));
});
