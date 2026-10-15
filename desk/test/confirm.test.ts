// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { describe, expect, it } from "vitest";
import { confirmInfo } from "../src/confirm";

const XT = "https://xt.freepro.online";

const env = (details: unknown, code = "confirmation_required") => ({ isError: true, structuredContent: { ok: false, error: { code, details } } });

describe("confirmInfo (TCV1 §3.6)", () => {
  it("lấy confirm_url + plan_summary", () => {
    expect(confirmInfo(env({ confirm_url: "https://xt.freepro.online/desk/confirm/abc", plan_summary: "Duyệt 5 laptop" }), XT)).toEqual({
      url: "https://xt.freepro.online/desk/confirm/abc",
      summary: "Duyệt 5 laptop",
    });
  });
  it("bỏ qua lỗi khác, URL không phải http(s), hoặc thiếu URL", () => {
    expect(confirmInfo(env({ confirm_url: "https://xt.freepro.online/desk/confirm/a" }, "access_denied"), XT)).toBeNull();
    expect(confirmInfo(env({ confirm_url: "javascript:alert(1)" }), XT)).toBeNull();
    expect(confirmInfo(env({}), XT)).toBeNull();
    expect(confirmInfo(undefined)).toBeNull();
  });
});
