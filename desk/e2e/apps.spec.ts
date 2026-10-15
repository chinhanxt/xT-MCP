// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Ba MCP App sản phẩm (ui://xt/orgchart, approval-card, record-table) chạy trong Desk thật,
// trên server xT thật (XtMcpServer + port giả): e2e/xt_apps_server.py.
import { login } from "./helpers";
import { expect, test, type Frame, type Page } from "@playwright/test";
import { resolve } from "node:path";

const IMG = resolve(process.cwd(), "../docs/img");
const XT = "http://127.0.0.1:19510";
const LLM_REQS = "http://127.0.0.1:19501/__requests";

interface Call {
  via: "app" | "dispatcher";
  tool: string;
  args: Record<string, unknown>;
}
const calls = async (page: Page): Promise<Call[]> => (await (await page.request.get(`${XT}/__calls`)).json()) as Call[];

async function ask(page: Page, text: string): Promise<void> {
  await page.fill("#box", text);
  await page.click("#send");
}

/** Khung App (iframe trong cùng) có phần tử `selector`; trả về Frame mới nhất. */
async function appFrame(page: Page, selector: string, timeout = 30_000): Promise<Frame> {
  let found: Frame | undefined;
  await expect
    .poll(
      async () => {
        for (const f of page.frames().reverse()) {
          if (f !== page.mainFrame() && (await f.locator(selector).count().catch(() => 0)) > 0) {
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

const DECIDE = (key: string) =>
  `chạy decide_request ${JSON.stringify({ ref: "advance_request:tu-0042", decision: "approve", expected_revision: 2, idempotency_key: key })}`;

test.beforeEach(async ({ page }) => {
  await login(page);
});

test("orgchart: SVG từ find_people, bấm nút gọi tool app-only, PII che", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await ask(page, 'chạy find_people {"query":"xt.vn"}');
  const f = await appFrame(page, "g.node");
  await expect(f.locator("g.node")).toHaveCount(6);
  await expect(f.locator("path.edge")).toHaveCount(5);
  await expect(f.locator("g.node[data-ref='person:tuan']")).toContainText("Trưởng nhóm dev");
  await page.screenshot({ path: `${IMG}/app-orgchart.png` });

  const before = (await calls(page)).filter((c) => c.via === "app").length;
  await f.locator("g.node[data-ref='person:tuan']").click();
  const detail = f.locator("#detail");
  await expect(detail).toContainText("Tuấn");
  await expect(detail).toContainText("Công nghệ");
  await expect(detail).toContainText("Minh"); // quản lý
  await expect(detail.locator(".reports button")).toHaveText(["Bình"]);
  await expect(detail.locator(".mask")).toHaveCount(2); // email + điện thoại đã che
  const text = await f.locator("body").innerText();
  expect(text).not.toContain("tuan@xt.vn");
  expect(text).not.toContain("0901000004");
  await page.screenshot({ path: `${IMG}/app-orgchart-detail.png` });

  const app = (await calls(page)).filter((c) => c.via === "app");
  expect(app.length).toBe(before + 1);
  expect(app.at(-1)).toMatchObject({ tool: "org_person_details", args: { ref: "person:tuan" } });
  // đi tiếp theo "Báo cáo trực tiếp": nút trong panel cũng là tool app-only
  await detail.locator(".reports button").click();
  await expect(detail).toContainText("Kỹ sư phần mềm");

  // model không thấy tool app-only
  const reqs = (await (await page.request.get(LLM_REQS)).json()) as Array<{ tools?: Array<{ function: { name: string } }> }>;
  const names = reqs.at(-1)!.tools!.map((t) => t.function.name);
  expect(names).toEqual(expect.arrayContaining(["find_people", "search_records", "decide_request"]));
  expect(names).not.toContain("org_person_details");
  expect(names).not.toContain("records_table_page");
  expect(errors).toEqual([]);
});

test("approval-card: chỉ mở confirm_url đúng origin xT; không có nút duyệt; origin lạ bị vô hiệu", async ({ page, context }) => {
  const base = (await calls(page)).length; // nhật ký server dùng chung giữa các test
  await ask(page, DECIDE("e2e-card-1"));
  const f = await appFrame(page, "#open");
  const body = f.locator("#body");
  await expect(body).toContainText("Mở trang xác nhận");
  await expect(body).toContainText("Chờ xác nhận");
  await expect(f.locator("button")).toHaveCount(1); // không có Duyệt/Từ chối trong iframe
  await expect(f.locator("button#open")).toBeEnabled();
  await page.screenshot({ path: `${IMG}/app-approval-card.png` });

  // confirm_url thật mà server cấp (lỗi xT không có structuredContent nên lấy từ chi tiết chip)
  // Thẻ xác nhận của Desk (ToolItem) phải hiện từ envelope lỗi thật (content[0].text)
  const card = page.locator("[data-testid=confirm-card]").first();
  await expect(card).toBeVisible();
  await expect(card.locator("a")).toHaveAttribute("href", new RegExp(`^${XT}/desk/confirm/`));
  await page.locator(".chip", { hasText: "decide_request" }).first().click();
  const detailText = await page.locator(".tool-detail").first().innerText();
  const confirmUrl = /"confirm_url\\?":\s*\\?"([^"\\]+)/.exec(detailText)?.[1] ?? "";
  expect(confirmUrl.startsWith(`${XT}/desk/confirm/`)).toBe(true);

  const [popup] = await Promise.all([context.waitForEvent("page"), f.locator("#open").click()]);
  expect(popup.url()).toBe(confirmUrl);
  expect(new URL(popup.url()).origin).toBe(XT);
  await popup.close();

  // Không tool nào duyệt từ App: chỉ một lời gọi decide_request (của model), không có lời gọi app-only
  const log = (await calls(page)).slice(base);
  expect(log.filter((c) => c.via === "app")).toEqual([]);
  expect(log.filter((c) => c.tool === "decide_request").length).toBe(1);

  // Server cấp confirm_url ở origin khác: thẻ vô hiệu hóa nút, không mở được gì
  await page.request.post(`${XT}/__confirm_base`, { data: { url: "https://evil.example" } });
  try {
    await ask(page, DECIDE("e2e-card-2"));
    await expect(page.locator("[data-testid=tool-item]")).toHaveCount(2);
    await expect.poll(async () => (await appFrame(page, "#open")).locator("#open").isDisabled()).toBe(true);
    const evil = await appFrame(page, "#open");
    await expect(evil.locator("#msg")).toContainText("không thuộc xT");
    await expect(page.locator("[data-testid=confirm-card]")).toHaveCount(1); // chỉ còn thẻ lượt 1; origin lạ (lượt 2): Desk không dựng thẻ
    await expect(page.locator("[data-testid=tool-item]").nth(1).locator("[data-testid=confirm-card]")).toHaveCount(0);
    const pages = context.pages().length;
    await evil.locator("#open").click({ force: true, timeout: 1500 }).catch(() => {});
    await page.waitForTimeout(500);
    expect(context.pages().length).toBe(pages);
    await page.screenshot({ path: `${IMG}/app-approval-card-blocked.png` });
  } finally {
    await page.request.post(`${XT}/__confirm_base`, { data: { url: XT } });
  }
});

test("lịch sử: hai câu hỏi confirmation_required liên tiếp giữ cả hai thẻ xác nhận, iframe lượt trước không remount", async ({ page }) => {
  await ask(page, DECIDE("e2e-hist-1"));
  const f1 = await appFrame(page, "#open");
  await expect(page.locator("[data-testid=confirm-card]")).toHaveCount(1);
  await f1.evaluate(() => ((window as unknown as { __keep: number }).__keep = 42));
  await expect(page.locator("#send")).toBeEnabled();
  await ask(page, DECIDE("e2e-hist-2"));
  await expect(page.locator("[data-testid=tool-item]")).toHaveCount(2);
  const cards = page.locator("[data-testid=confirm-card] a");
  await expect(cards).toHaveCount(2);
  const hrefs = await cards.evaluateAll((as) => as.map((a) => a.getAttribute("href")));
  for (const h of hrefs) expect(h).toMatch(new RegExp(`^${XT}/desk/confirm/[\\w-]+$`));
  expect(new Set(hrefs).size).toBe(2);
  // iframe lượt 1 vẫn là chính nó (state còn nguyên)
  const kept = await Promise.all(page.frames().map((fr) => fr.evaluate(() => (window as unknown as { __keep?: number }).__keep).catch(() => undefined)));
  expect(kept).toContain(42);
});

test("record-table: phân trang, lọc theo trạng thái và từ khóa qua tool app-only", async ({ page }) => {
  await ask(page, 'chạy search_records {"model_id":"advance_request","limit":5}');
  const f = await appFrame(page, "#b tr");
  const rows = f.locator("#b tr");
  await expect(rows).toHaveCount(5);
  await expect(f.locator("#info")).toContainText("Trang 1");
  await expect(f.locator("#info")).toContainText("tổng 14");
  await expect(f.locator("#prev")).toBeDisabled();
  const first = await rows.first().innerText();
  await page.screenshot({ path: `${IMG}/app-record-table.png` });

  await f.locator("#next").click();
  await expect(f.locator("#info")).toContainText("Trang 2");
  expect(await rows.first().innerText()).not.toBe(first);
  await f.locator("#prev").click();
  await expect(f.locator("#info")).toContainText("Trang 1");
  expect(await rows.first().innerText()).toBe(first);

  await f.locator("#s").selectOption("approved");
  await f.locator("#l").selectOption("20");
  await f.locator("#go").click();
  const stateSet = async () => [...new Set(await f.locator("#b td:nth-child(4)").allInnerTexts())];
  await expect.poll(stateSet).toEqual(["approved"]);
  expect(await rows.count()).toBeGreaterThanOrEqual(4);
  await page.screenshot({ path: `${IMG}/app-record-table-filtered.png` });

  await f.locator("#s").selectOption("*");
  await f.locator("#q").fill("đợt 7");
  await f.locator("#go").click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Tạm ứng công tác đợt 7");

  const app = (await calls(page)).filter((c) => c.via === "app" && c.tool === "records_table_page");
  expect(app.length).toBeGreaterThanOrEqual(4);
  expect(app.some((c) => typeof c.args.cursor === "string")).toBe(true);
  expect(app.some((c) => (c.args.where as { state?: string } | undefined)?.state === "approved")).toBe(true);
});
