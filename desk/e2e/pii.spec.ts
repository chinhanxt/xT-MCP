// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { login } from "./helpers";
import { expect, test } from "@playwright/test";

const XT = "http://127.0.0.1:19507";
const LLM_REQS = "http://127.0.0.1:19501/__requests";
const SECRET = "25000000";

test("Hiện PII: vé dùng một lần, giá trị chỉ ở UI, không vào LLM, mất khi tải lại", async ({ page }) => {
  const logs: string[] = [];
  page.on("console", (m) => logs.push(m.text()));
  await login(page);
  const before = ((await (await page.request.get(LLM_REQS)).json()) as unknown[]).length;

  await page.fill("#box", "chạy get_employee_pii");
  await page.click("#send");
  await expect(page.locator(".chip", { hasText: "get_employee_pii" })).toContainText("xong");

  const box = page.getByTestId("pii-reveal");
  const btn = box.getByRole("button", { name: /Hiện/ });
  await expect(btn).toHaveAttribute("title", /không bao giờ được gửi cho mô hình AI/);
  await expect(page.locator("body")).not.toContainText(SECRET);

  await btn.click();
  await expect(page.getByTestId("pii-values")).toContainText(SECRET);
  let st = await (await page.request.get(`${XT}/__state`)).json();
  expect([st.minted, st.redeemed, st.rejected]).toEqual([1, 1, 0]);
  expect(st.reveals[0].credentials).toBe(false); // không cookie/Authorization tới /pii/reveal

  // ẩn rồi xem lại => vé mới, không dùng lại vé cũ
  await box.getByRole("button", { name: "Ẩn" }).click();
  await expect(page.locator("body")).not.toContainText(SECRET);
  await box.getByRole("button", { name: /Hiện/ }).click();
  await expect(page.getByTestId("pii-values")).toContainText(SECRET);
  st = await (await page.request.get(`${XT}/__state`)).json();
  expect([st.minted, st.redeemed, st.rejected]).toEqual([2, 2, 0]);

  // giá trị thật không bao giờ tới LLM; token thì có; không log
  const reqs = ((await (await page.request.get(LLM_REQS)).json()) as unknown[]).slice(before);
  expect(reqs.length).toBeGreaterThan(0);
  expect(JSON.stringify(reqs)).not.toContain(SECRET);
  expect(JSON.stringify(reqs)).not.toContain("pii_reveal_ticket");
  expect(JSON.stringify(reqs)).toContain("⟨NV-0042.luong⟩");
  expect(logs.join("\n")).not.toContain(SECRET);

  // tải lại: mất phiên chat và giá trị; DOM không còn
  await page.reload();
  await expect(page.locator("body")).not.toContainText(SECRET);
  expect(await page.evaluate(() => JSON.stringify([localStorage, sessionStorage]))).not.toContain(SECRET);
});
