// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Audit (docs/audit-desk.md, BUG-5): confirm_url phải trỏ tới trang xác nhận của xT (/desk/confirm/{id}) trên origin xT đã cấu hình.
// Hiện confirmInfo nhận mọi URL http(s) nên các test dưới đây FAIL cho tới khi sửa.
// Chạy: npx vitest run -c tests/audit/vitest.audit.config.ts
import { describe, expect, it } from "vitest";
import { confirmInfo } from "../../src/confirm";

const res = (url: string) => ({
  isError: true,
  structuredContent: { ok: false, error: { code: "confirmation_required", details: { confirm_url: url, plan_summary: "Duyệt chi" } } },
});

describe("confirm_url chỉ mở trang xT", () => {
  it("từ chối origin ngoài xT", () => {
    expect(confirmInfo(res("https://evil.example/desk/confirm/cf_1"))).toBeNull();
  });
  it("từ chối origin trông giống xT", () => {
    expect(confirmInfo(res("https://xt.freepro.online.evil.example/desk/confirm/cf_1"))).toBeNull();
  });
  it("từ chối http khi xT chạy https", () => {
    expect(confirmInfo(res("http://xt.freepro.online/desk/confirm/cf_1"))).toBeNull();
  });
  it("từ chối đường dẫn ngoài /desk/confirm/", () => {
    expect(confirmInfo(res("https://xt.freepro.online/logout?next=https://evil.example"))).toBeNull();
  });
});
