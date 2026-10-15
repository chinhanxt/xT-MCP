// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Hồi quy vòng 2 của docs/audit-desk.md (5 điểm Low).
import { describe, expect, it, vi } from "vitest";
import { McpError } from "@modelcontextprotocol/sdk/types.js";
import { closeSchema, createAppBridgeHandlers, createGlobalToolLimiter, createRateLimiter, formatOpenLinkPrompt, isActivationInFrame } from "../src/bridge";
import { isModelVisible, normalizeVisibility } from "../src/profiles";
import type { McpTool } from "../src/types";

const mk = (name: string, visibility: unknown, schema: Record<string, unknown> = { type: "object", properties: { a: { type: "string" } } }): McpTool =>
  ({ name, inputSchema: schema, _meta: { ui: { visibility } } }) as unknown as McpTool;

function setup(tools: McpTool[], o: { maxArgsBytes?: number; global?: { ratePerSec: number; burst: number }; burst?: number } = {}, shared = createGlobalToolLimiter(o.global ?? { ratePerSec: 1000, burst: 1000 })) {
  const callTool = vi.fn(async () => ({ content: [] }));
  const h = createAppBridgeHandlers({
    tools,
    callTool,
    readResource: async () => ({}),
    limiter: createRateLimiter({ ratePerSec: 1000, burst: o.burst ?? 1000, maxInflight: 1000 }),
    globalLimiter: shared,
    maxArgsBytes: o.maxArgsBytes ?? 32768,
  });
  return { h, callTool };
}

describe("(1) thiếu additionalProperties = false; trần kích thước arguments", () => {
  it("thuộc tính thừa bị từ chối khi schema server không khai additionalProperties", async () => {
    const { h, callTool } = setup([mk("t", ["app"])]);
    await expect(h.onCallTool({ name: "t", arguments: { a: "x", injected: 1 } })).rejects.toBeInstanceOf(McpError);
    expect(callTool).not.toHaveBeenCalled();
    await h.onCallTool({ name: "t", arguments: { a: "x" } });
    expect(callTool).toHaveBeenCalledTimes(1);
  });
  it("đệ quy vào object lồng và items; additionalProperties khai tường minh được giữ", async () => {
    const s = closeSchema({ type: "object", properties: { o: { type: "object", properties: { k: { type: "string" } } }, l: { type: "array", items: { type: "object", properties: {} } }, free: { type: "object", additionalProperties: true } } }) as any;
    expect(s.additionalProperties).toBe(false);
    expect(s.properties.o.additionalProperties).toBe(false);
    expect(s.properties.l.items.additionalProperties).toBe(false);
    expect(s.properties.free.additionalProperties).toBe(true);
  });
  it("không sửa schema gốc", () => {
    const src = { type: "object", properties: {} };
    closeSchema(src);
    expect(src).toEqual({ type: "object", properties: {} });
  });
  it("arguments vượt trần byte bị từ chối (tính theo UTF-8)", async () => {
    const { h, callTool } = setup([mk("t", ["app"], { type: "object", properties: { a: { type: "string" } } })], { maxArgsBytes: 100 });
    await expect(h.onCallTool({ name: "t", arguments: { a: "x".repeat(200) } })).rejects.toThrow(/vượt quá 100 byte/);
    await expect(h.onCallTool({ name: "t", arguments: { a: "ệ".repeat(40) } })).rejects.toThrow(/vượt quá/); // 40 ký tự x 3 byte
    expect(callTool).not.toHaveBeenCalled();
    await h.onCallTool({ name: "t", arguments: { a: "ok" } });
    expect(callTool).toHaveBeenCalledTimes(1);
  });
});

describe("(2) rate limit chung theo tool, cộng dồn mọi instance", () => {
  it("N instance, mỗi instance còn burst riêng nhưng tổng tới server <= burst chung", async () => {
    const shared = createGlobalToolLimiter({ ratePerSec: 1, burst: 5 });
    const tools = [mk("t", ["app"])];
    const instances = Array.from({ length: 4 }, () => setup(tools, { burst: 10 }, shared));
    const rs = await Promise.allSettled(instances.flatMap((i) => Array.from({ length: 10 }, () => i.h.onCallTool({ name: "t", arguments: {} }))));
    const reached = instances.reduce((n, i) => n + i.callTool.mock.calls.length, 0);
    expect(reached).toBe(5);
    expect(rs.filter((r) => r.status === "fulfilled").length).toBe(5);
  });
  it("bucket chung theo từng tên tool (tool khác không bị ảnh hưởng)", () => {
    let t = 0;
    const g = createGlobalToolLimiter({ ratePerSec: 1, burst: 1, now: () => t });
    expect([g.tryAcquire("a"), g.tryAcquire("a"), g.tryAcquire("b")]).toEqual([true, false, true]);
    t += 1000;
    expect(g.tryAcquire("a")).toBe(true);
  });
  it("bị chặn ở mức chung thì nhả slot inflight của instance", async () => {
    const shared = createGlobalToolLimiter({ ratePerSec: 0.001, burst: 1 });
    const callTool = vi.fn(async () => ({ content: [] }));
    const limiter = createRateLimiter({ ratePerSec: 1000, burst: 1000, maxInflight: 1 });
    const h = createAppBridgeHandlers({ tools: [mk("t", ["app"])], callTool, readResource: async () => ({}), limiter, globalLimiter: shared, maxArgsBytes: 1000 });
    await h.onCallTool({ name: "t", arguments: {} });
    await expect(h.onCallTool({ name: "t", arguments: {} })).rejects.toThrow(/các App/);
    expect(limiter.tryAcquire()).toBe(true); // slot đã được nhả
  });
});

describe("(3) hộp thoại open-link hiển thị URL an toàn", () => {
  const bidi = String.fromCharCode(0x202e);
  const zw = String.fromCharCode(0x200b);
  it("origin nổi bật; host IDN hiện dạng punycode", () => {
    const m = formatOpenLinkPrompt("https://xt.freepro.online.іvil.example/p");
    expect(m).toContain("Trang đích: https://xt.freepro.online.xn--");
    expect(m).not.toMatch(/і/);
  });
  it("không có xuống dòng/ký tự điều khiển/bidi/zero-width từ URL; URL chỉ ở một dòng", () => {
    const u = `https://evil.example/a%0A%0DDa-xac-nhan-xT${bidi}${zw}?q=1\n\nDONG-GIA`;
    const m = formatOpenLinkPrompt(u);
    const urlLine = m.split("\n").find((l) => l.startsWith("Địa chỉ đầy đủ:"))!;
    expect(urlLine).toContain("evil.example");
    expect(m.split("\n").length).toBe(formatOpenLinkPrompt("https://evil.example/").split("\n").length); // không thêm dòng nào
    expect(m).not.toMatch(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]/);
  });
  it("URL quá dài bị cắt", () => {
    expect(formatOpenLinkPrompt(`https://evil.example/${"a".repeat(1000)}`).length).toBeLessThan(600);
  });
});

describe("(4) activation gắn với đúng khung App", () => {
  const mkFrame = () => {
    const box = document.createElement("div");
    box.className = "app-frame";
    const inner = document.createElement("iframe");
    box.append(inner);
    document.body.append(box);
    return { box, inner };
  };
  it("tiêu điểm trong iframe của chính khung đó: hợp lệ", () => {
    const a = mkFrame();
    expect(isActivationInFrame(a.inner, a.box)).toBe(true);
  });
  it("App A không dùng được cú bấm vào App B", () => {
    const a = mkFrame();
    const b = mkFrame();
    expect(isActivationInFrame(b.inner, a.box)).toBe(false);
  });
  it("tiêu điểm ở phần tử khác (nút Gửi, body) hoặc thiếu khung: không hợp lệ", () => {
    const a = mkFrame();
    const btn = document.createElement("button");
    document.body.append(btn);
    expect(isActivationInFrame(btn, a.box)).toBe(false);
    expect(isActivationInFrame(document.body, a.box)).toBe(false);
    expect(isActivationInFrame(a.inner, null)).toBe(false);
    expect(isActivationInFrame(null, a.box)).toBe(false);
  });
});

describe("(5) visibility phải là mảng chuỗi đã biết", () => {
  const bad: unknown[] = ["app", "model,app", { 0: "app", length: 1 }, ["app", 1], ["App"], ["app", "admin"], null, 1, true];
  it.each(bad.map((v) => [JSON.stringify(v), v]))("bridge từ chối visibility=%s", async (_n, v) => {
    const { h, callTool } = setup([mk("t", v)]);
    await expect(h.onCallTool({ name: "t", arguments: {} })).rejects.toBeInstanceOf(McpError);
    expect(callTool).not.toHaveBeenCalled();
  });
  it.each(bad.map((v) => [JSON.stringify(v), v]))("model không thấy tool có visibility=%s", (_n, v) => {
    expect(isModelVisible(mk("t", v))).toBe(false);
  });
  it("thiếu visibility: model thấy nhưng App KHÔNG gọi được; hợp lệ thì đúng như khai", async () => {
    const t = { name: "t", inputSchema: { type: "object", properties: {} } } as McpTool;
    expect(isModelVisible(t)).toBe(true);
    const { h } = setup([t]);
    await expect(h.onCallTool({ name: "t", arguments: {} })).rejects.toBeInstanceOf(McpError);
    expect(normalizeVisibility(["model"])).toEqual(["model"]);
    expect(isModelVisible(mk("m", ["model"]))).toBe(true);
    expect(isModelVisible(mk("a", ["app"]))).toBe(false);
  });
});
