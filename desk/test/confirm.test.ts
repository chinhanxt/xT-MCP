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
  const text = (o: unknown, isError = true) => ({ isError, content: [{ type: "text", text: typeof o === "string" ? o : JSON.stringify(o) }] });
  const body = (details: unknown, code = "confirmation_required") => ({ ok: false, untrusted: true, error: { code, details } });
  it("xT thật: envelope lỗi JSON trong content[0].text, không có structuredContent", () => {
    const d = { confirmation_id: "cf_1", confirm_url: "https://xt.freepro.online/desk/confirm/cf_1", plan_summary: "Duyệt 5 laptop" };
    expect(confirmInfo(text(body(d)), XT)).toEqual({ url: d.confirm_url, summary: "Duyệt 5 laptop" });
  });
  it("content text: từ chối JSON hỏng, mã khác, origin lạ, id lệch URL", () => {
    expect(confirmInfo(text("không phải json"), XT)).toBeNull();
    expect(confirmInfo(text("null"), XT)).toBeNull();
    expect(confirmInfo(text("[1]"), XT)).toBeNull();
    expect(confirmInfo(text(body({ confirm_url: "https://xt.freepro.online/desk/confirm/a" }, "access_denied")), XT)).toBeNull();
    expect(confirmInfo(text(body({ confirm_url: "https://evil.example/desk/confirm/a" })), XT)).toBeNull();
    expect(confirmInfo(text(body({ confirmation_id: "b", confirm_url: "https://xt.freepro.online/desk/confirm/a" })), XT)).toBeNull();
    expect(confirmInfo({ isError: true, content: [{ type: "image" }] }, XT)).toBeNull();
  });
});
