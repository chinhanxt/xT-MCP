// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Hồi quy docs/audit-desk.md: BUG-1 (allowlist/validate), BUG-4 (rate limit), BUG-5 (open-link), BUG-7 (CSP), BUG-6 (confirm_url).
import { describe, expect, it, vi } from "vitest";
import { McpError } from "@modelcontextprotocol/sdk/types.js";
import { APP_DENYLIST, checkOpenLink, createGlobalToolLimiter, createAppBridgeHandlers, createRateLimiter, sanitizeUiCsp } from "../src/bridge";
import { confirmInfo, isXtConfirmUrl } from "../src/confirm";
import type { McpTool } from "../src/types";

const XT = "https://xt.freepro.online";
const tool = (name: string, visibility?: string[], schema: Record<string, unknown> = { type: "object", properties: {}, additionalProperties: false }): McpTool => ({
  name,
  inputSchema: schema,
  _meta: visibility ? { ui: { visibility } } : undefined,
});
const limiter = (o: Partial<Parameters<typeof createRateLimiter>[0]> = {}) => createRateLimiter({ ratePerSec: 5, burst: 10, maxInflight: 4, ...o });

function setup(tools: McpTool[], l = limiter()) {
  const callTool = vi.fn(async (p: { name: string; arguments: Record<string, unknown> }) => ({ content: [{ type: "text", text: p.name }] }));
  const readResource = vi.fn(async () => ({ contents: [] }));
  return { h: createAppBridgeHandlers({ tools, callTool, readResource, limiter: l, globalLimiter: createGlobalToolLimiter({ ratePerSec: 1000, burst: 1000 }), maxArgsBytes: 32768 }), callTool, readResource };
}

describe("BUG-1: onCallTool của host", () => {
  const tools = [
    tool("app_only", ["app"], { type: "object", properties: { n: { type: "integer" } }, required: ["n"], additionalProperties: false }),
    tool("model_only", ["model"]),
    tool("both", ["model", "app"]),
    tool("no_meta"),
    tool("decide_request", ["app"]), // server khai app cũng bị denylist chặn
  ];
  it("cho tool app-only với tham số đúng schema", async () => {
    const { h, callTool } = setup(tools);
    await h.onCallTool({ name: "app_only", arguments: { n: 1 } });
    expect(callTool).toHaveBeenCalledWith({ name: "app_only", arguments: { n: 1 } });
  });
  it.each(["model_only", "no_meta", "decide_request", "khong_ton_tai"])("từ chối %s và không gọi server", async (n) => {
    const { h, callTool } = setup(tools);
    await expect(h.onCallTool({ name: n, arguments: {} })).rejects.toBeInstanceOf(McpError);
    expect(callTool).not.toHaveBeenCalled();
  });
  it("tool khai cả model và app: được (app khai tường minh)", async () => {
    const { h, callTool } = setup(tools);
    await h.onCallTool({ name: "both", arguments: {} });
    expect(callTool).toHaveBeenCalledTimes(1);
  });
  it("từ chối tham số sai schema (kiểu, thiếu, thừa)", async () => {
    const { h, callTool } = setup(tools);
    for (const args of [{ n: "abc" }, {}, { n: 1, extra: true }]) await expect(h.onCallTool({ name: "app_only", arguments: args })).rejects.toBeInstanceOf(McpError);
    expect(callTool).not.toHaveBeenCalled();
  });
  it("denylist chứa decide_request và các tool ghi của v0.1", () => {
    for (const n of ["decide_request", "create_record", "update_record", "transition_record", "submit_request"]) expect(APP_DENYLIST.has(n)).toBe(true);
  });
  it("chỉ đọc resource ui://", async () => {
    const { h, readResource } = setup(tools);
    for (const uri of ["file:///etc/passwd", "https://evil.example/x", "xt://secret"]) await expect(h.onReadResource({ uri })).rejects.toBeInstanceOf(McpError);
    await h.onReadResource({ uri: "ui://xt/org-chart.html" });
    expect(readResource).toHaveBeenCalledTimes(1);
  });
  it("không liệt kê resource/prompt của server cho App", async () => {
    const { h } = setup(tools);
    expect(await h.onListResources()).toEqual({ resources: [] });
    expect(await h.onListResourceTemplates()).toEqual({ resourceTemplates: [] });
    expect(await h.onListPrompts()).toEqual({ prompts: [] });
  });
});

describe("BUG-4: rate limit theo instance App", () => {
  it("token bucket: burst rồi chặn, hồi phục theo thời gian", () => {
    let t = 0;
    const l = createRateLimiter({ ratePerSec: 5, burst: 10, maxInflight: 100, now: () => t });
    const ok = Array.from({ length: 100 }, () => l.tryAcquire()).filter(Boolean).length;
    expect(ok).toBe(10);
    t += 1000;
    expect(Array.from({ length: 100 }, () => l.tryAcquire()).filter(Boolean).length).toBe(5);
  });
  it("trần request đang chờ", () => {
    const l = limiter({ burst: 100, maxInflight: 2 });
    expect([l.tryAcquire(), l.tryAcquire(), l.tryAcquire()]).toEqual([true, true, false]);
    l.release();
    expect(l.tryAcquire()).toBe(true);
  });
  it("100 lời gọi song song tới handler: chỉ <= burst tới server, phần còn lại bị từ chối", async () => {
    const { h, callTool } = setup([tool("t", ["app"])], limiter({ burst: 10, maxInflight: 100 }));
    const rs = await Promise.allSettled(Array.from({ length: 100 }, () => h.onCallTool({ name: "t", arguments: {} })));
    expect(rs.filter((r) => r.status === "fulfilled").length).toBe(callTool.mock.calls.length);
    expect(callTool.mock.calls.length).toBeLessThanOrEqual(10);
    expect(rs.filter((r) => r.status === "rejected").length).toBeGreaterThanOrEqual(90);
  });
  it("mỗi instance có bucket riêng", () => {
    const a = limiter({ burst: 1 });
    const b = limiter({ burst: 1 });
    expect([a.tryAcquire(), a.tryAcquire(), b.tryAcquire()]).toEqual([true, false, true]);
  });
});

describe("BUG-5: ui/open-link", () => {
  const o = { xtOrigin: XT, userActive: true };
  it("không có user activation: từ chối", () => {
    expect(checkOpenLink(`${XT}/desk/confirm/a`, { ...o, userActive: false }).action).toBe("deny");
  });
  it("origin xT: mở; origin khác: phải xác nhận", () => {
    expect(checkOpenLink(`${XT}/desk/confirm/a`, o).action).toBe("open");
    expect(checkOpenLink("https://evil.example/p", o).action).toBe("confirm");
    expect(checkOpenLink("https://xt.freepro.online.evil.example/", o).action).toBe("confirm");
  });
  it("chặn scheme không phải http(s), URL hỏng, URL có credentials", () => {
    const sch = ["java" + "script:alert(1)", "data:text/html,x", "file:///etc/passwd", "not a url", "https://u:p@xt.freepro.online/"];
    for (const u of sch) expect(checkOpenLink(u, o).action).toBe("deny");
  });
});

describe("BUG-7: CSP truyền cho sandbox", () => {
  it("chỉ giữ connect tới đúng origin xT (kênh /pii/reveal)", () => {
    expect(sanitizeUiCsp({ connectDomains: [XT, "https://evil.example"] }, XT)).toEqual({ connectDomains: [XT] });
  });
  it("bỏ wildcard, origin giống xT, đường dẫn, các nhóm resource/frame/base-uri", () => {
    const r = sanitizeUiCsp(
      { connectDomains: ["*", "https://*.freepro.online", "https://xt.freepro.online.evil.example", `${XT}/x`, "http://xt.freepro.online"], resourceDomains: ["https://cdn.example"], frameDomains: ["https://f.example"], baseUriDomains: ["https://b.example"] },
      XT,
    );
    expect(r).toEqual({});
  });
  it("không có csp: rỗng", () => {
    expect(sanitizeUiCsp(undefined, XT)).toEqual({});
  });
});

describe("BUG-6: confirm_url", () => {
  const res = (url: string) => ({ structuredContent: { error: { code: "confirmation_required", details: { confirm_url: url } } } });
  it("chỉ nhận đúng origin xT + /desk/confirm/{id}", () => {
    expect(confirmInfo(res(`${XT}/desk/confirm/cf_1`), XT)?.url).toBe(`${XT}/desk/confirm/cf_1`);
    for (const u of ["https://evil.example/desk/confirm/cf_1", `${XT}/logout`, `${XT}/desk/confirm/a/../../x`, `${XT}/desk/confirm/a?next=https://e.example`, "https://u:p@xt.freepro.online/desk/confirm/a", "http://xt.freepro.online/desk/confirm/a"]) {
      expect(confirmInfo(res(u), XT), u).toBeNull();
    }
  });
  it("thiếu origin cấu hình: fail closed", () => {
    expect(isXtConfirmUrl(`${XT}/desk/confirm/a`, undefined)).toBe(false);
    expect(confirmInfo(res(`${XT}/desk/confirm/a`))).toBeNull();
  });
});
