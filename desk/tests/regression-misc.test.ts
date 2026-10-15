// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Hồi quy docs/audit-desk.md: BUG-8 (markdown giả giao diện), BUG-9 (không bearer tĩnh, fail closed), BUG-2/3 (sandbox.html).
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AuthRequiredError, resolveAuth } from "../src/auth";
import { createLlm } from "../src/llm";
import { connectMcp } from "../src/mcp";
import { renderMarkdown } from "../src/markdown";

const read = (p: string) => readFileSync(resolve(process.cwd(), p), "utf8");

describe("BUG-8: markdown không cho class/style/id/title", () => {
  const evil = [
    '<a class="confirm-card" href="https://evil.example">Mở trang xác nhận</a>',
    '<p style="position:fixed;inset:0">x</p>',
    '<div id="root" class="msg user">x</div><span class="pii">[đã che]</span>',
    '<a href="https://e.example" title="Trang xT chính chủ">x</a>',
  ];
  it.each(evil)("loại thuộc tính giả giao diện: %s", (src) => {
    const h = renderMarkdown(src);
    expect(h).not.toMatch(/\sclass=|\sstyle=|\sid=|\stitle=/);
  });
  it("nhãn PII do Desk dựng vẫn có; token nằm trong thuộc tính không bơm được markup", () => {
    const h = renderMarkdown('Lương [PII:ab12] và <a href="https://e.example/[PII:zz]">x</a>');
    expect(h.match(/class="pii"/g)?.length).toBe(1);
    expect(h).not.toMatch(/href="[^"]*<span/);
  });
});

describe("BUG-9: không bearer tĩnh; fail closed khi chưa có token người dùng", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("vite.config.ts không gắn Authorization/Bearer, không đọc DESK_MCP_TOKEN/DESK_LLM_API_KEY", () => {
    const v = read("vite.config.ts").replace(/^\s*\/\/.*$/gm, "");
    expect(v).not.toMatch(/Authorization|Bearer|DESK_MCP_TOKEN|DESK_LLM_API_KEY/);
  });
  it(".env.example không có token/api key tĩnh", () => {
    const e = read(".env.example").replace(/^\s*#.*$/gm, "");
    expect(e).not.toMatch(/TOKEN|API_KEY|BEARER/i);
  });
  it("oidc không có token => AuthRequiredError", async () => {
    await expect(Promise.resolve(resolveAuth("oidc", async () => null)())).rejects.toBeInstanceOf(AuthRequiredError);
  });
  it("oidc có token người dùng => Authorization của người dùng", async () => {
    expect(await resolveAuth("oidc", async () => "user-token")()).toEqual({ Authorization: "Bearer user-token" });
  });
  it("mode lạ bị từ chối", () => {
    expect(() => resolveAuth("static-bearer")).toThrow();
  });
  it("LLM: chưa đăng nhập thì KHÔNG gọi fetch", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    const llm = createLlm({ baseUrl: "/llm", model: "m", authHeaders: resolveAuth("oidc", async () => null) });
    await expect(llm.complete({ messages: [], tools: [] }, () => {})).rejects.toBeInstanceOf(AuthRequiredError);
    expect(f).not.toHaveBeenCalled();
  });
  it("MCP: chưa đăng nhập thì KHÔNG gửi request", async () => {
    const f = vi.fn();
    vi.stubGlobal("fetch", f);
    await expect(connectMcp(new URL("http://localhost/mcp"), resolveAuth("oidc", async () => null))).rejects.toBeDefined();
    expect(f).not.toHaveBeenCalled();
  });
  it("LLM có token: gửi header của người dùng", async () => {
    const f = vi.fn(async () => new Response(JSON.stringify({ choices: [{ message: { content: "ok" } }] }), { headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", f);
    const llm = createLlm({ baseUrl: "/llm", model: "m", authHeaders: resolveAuth("oidc", async () => "u1") });
    await llm.complete({ messages: [], tools: [] }, () => {});
    expect((f.mock.calls[0] as unknown as [string, RequestInit])[1].headers).toMatchObject({ Authorization: "Bearer u1" });
  });
});

describe("BUG-2/3: sandbox.html (kiểm tĩnh; hành vi thật ở e2e/security.spec.ts)", () => {
  const html = read("public/sandbox.html");
  it("không chèn CSP bằng regex lên HTML không tin cậy; srcdoc là HTML nguyên bản", () => {
    expect(html).not.toMatch(/<head\[\^>\]\*>|\.replace\(\/<head/);
    expect(html).toMatch(/inner\.srcdoc = String\(p\.html/);
  });
  it("CSP đặt vào tài liệu proxy TRƯỚC khi tạo iframe trong", () => {
    expect(html.indexOf("document.head.appendChild(meta)")).toBeGreaterThan(0);
    expect(html.indexOf("document.head.appendChild(meta)")).toBeLessThan(html.indexOf('document.createElement("iframe")'));
  });
  it("frame-src 'none', form-action 'none', không allow-forms/allow-same-origin cho iframe trong", () => {
    expect(html).toContain(`"frame-src 'none'"`);
    expect(html).toContain(`"form-action 'none'"`);
    expect(html).toContain('inner.setAttribute("sandbox", "allow-scripts")');
  });
  it("bỏ iframe khi bị nạp lại/điều hướng; chỉ nhận resource một lần; không cho domain wildcard", () => {
    expect(html).toMatch(/loads > 1\) kill\(\)/);
    expect(html).toMatch(/if \(inner\) return;/);
    expect(html).toContain("ORIGIN_RE");
    expect("https://*.evil.example").not.toMatch(/^https?:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?$/);
  });
});
