// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { expect, type Page } from "@playwright/test";

/** Đăng nhập qua Keycloak giả (auth-code + PKCE, tự đồng ý) rồi chờ ô chat sẵn sàng. */
export async function login(page: Page): Promise<void> {
  await page.goto("/");
  await page.click("#login");
  await expect(page.locator("#box")).toBeEnabled({ timeout: 20_000 });
}
