// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { describe, expect, it } from "vitest";
import { webcrypto } from "node:crypto";
import { runAgent } from "../src/agent";
import { createRevealStore, extractTokens, isRevealTool, revealTokens, RevealError, REVEAL_TOOLTIP } from "../src/pii";
import { selectTools } from "../src/profiles";
import { APP_DENYLIST } from "../src/bridge";
import type { ChatMessage, LlmClient, LlmReply, McpTool, ToolResult } from "../src/types";

const XT = "https://xt.example";
const TOKEN = "[PII:nv42luongaaaaaaa]";
const SECRET = "25000000";

const b64uDec = (s: string) => Uint8Array.from(Buffer.from(s, "base64url"));

/** xT giả: cấp vé (lưu JWK), đổi vé đúng 1 lần, kiểm chữ ký P1363 như server thật. */
function mockXt() {
  const tickets = new Map<string, { jwk: JsonWebKey; tokens: string[]; used: boolean }>();
  const minted: string[] = [];
  const posts: string[] = [];
  const callTool = async (p: { name: string; arguments: Record<string, unknown> }): Promise<ToolResult> => {
    expect(p.name).toBe("pii_reveal_ticket");
    const jwk = p.arguments.pubkey_jwk as JsonWebKey;
    expect(jwk.d).toBeUndefined(); // chỉ khóa công khai rời khỏi Desk
    const ticket = `tk-${minted.length}-${Math.random().toString(36).slice(2)}`;
    minted.push(ticket);
    tickets.set(ticket, { jwk, tokens: p.arguments.tokens as string[], used: false });
    return { content: [{ type: "text", text: "ok" }], _meta: { "xt/reveal": { ticket, url: `${XT}/pii/reveal` } } };
  };
  const fetchImpl: typeof fetch = async (url, init) => {
    posts.push(String(url));
    const b = JSON.parse(String(init?.body));
    const t = tickets.get(b.ticket);
    if (!t || t.used) return new Response(JSON.stringify({ error: "TICKET_INVALID" }), { status: 403 });
    t.used = true;
    const key = await webcrypto.subtle.importKey("jwk", t.jwk, { name: "ECDSA", namedCurve: "P-256" }, false, ["verify"]);
    const ok = await webcrypto.subtle.verify({ name: "ECDSA", hash: "SHA-256" }, key, b64uDec(b.sig), new TextEncoder().encode(`${b.ticket}.${b.ts}`));
    if (!ok) return new Response(JSON.stringify({ error: "TICKET_INVALID" }), { status: 403 });
    return new Response(JSON.stringify({ values: Object.fromEntries(t.tokens.map((x) => [x, SECRET])), denied: [] }));
  };
  return { callTool, fetchImpl, minted, posts, replay: (ticket: string) => fetchImpl(`${XT}/pii/reveal`, { method: "POST", body: JSON.stringify({ ticket, ts: 1, sig: "x" }) }) };
}

describe("extractTokens", () => {
  it("lấy token duy nhất từ structuredContent, bỏ qua giá trị thường", () => {
    const r: ToolResult = { structuredContent: { a: TOKEN, b: TOKEN, c: "[PII:nvmothotenaaaaaa]", d: "lương 5" } };
    expect(extractTokens(r)).toEqual([TOKEN, "[PII:nvmothotenaaaaaa]"]);
    expect(extractTokens(undefined)).toEqual([]);
  });
  it("nhận token của server xT [PII:<16 base32>] (tool-contracts-v1 §3.8)", () => {
    const r: ToolResult = { structuredContent: { values: { bank_account: "[PII:k5jkkvvmaxb2spjp]", x: "[PII:short]" } } };
    expect(extractTokens(r)).toEqual(["[PII:k5jkkvvmaxb2spjp]"]);
  });
  it("bỏ định dạng spike ⟨MÃ.trường⟩ (PIIR-06): server chỉ phát [PII:…]", () => {
    expect(extractTokens({ structuredContent: { a: "⟨NV-0042.luong⟩" } })).toEqual([]);
  });
  it("MCP App trong iframe không gọi được pii_reveal_ticket (PIIR-07)", () => {
    expect(APP_DENYLIST.has("pii_reveal_ticket")).toBe(true);
  });
  it("tooltip tiếng Việt giải thích không gửi cho AI", () => {
    expect(REVEAL_TOOLTIP).toContain("không bao giờ được gửi cho mô hình AI");
  });
});

describe("luồng reveal", () => {
  it("vé -> POST xT -> giá trị; vé dùng một lần; mỗi lần xem là vé mới", async () => {
    const xt = mockXt();
    const d = { callTool: xt.callTool, fetch: xt.fetchImpl, xtOrigin: XT, subtle: webcrypto.subtle as SubtleCrypto };
    const a = await revealTokens([TOKEN], d);
    expect(a.values[TOKEN]).toBe(SECRET);
    expect(xt.posts).toEqual([`${XT}/pii/reveal`]);
    expect((await xt.replay(xt.minted[0])).status).toBe(403); // replay bị từ chối
    const b = await revealTokens([TOKEN], d);
    expect(b.values[TOKEN]).toBe(SECRET);
    expect(new Set(xt.minted).size).toBe(2);
  });

  it("từ chối url reveal khác origin xT: vé không rời Desk", async () => {
    const xt = mockXt();
    const evil: typeof xt.callTool = async (p) => {
      const r = await xt.callTool(p);
      return { ...r, _meta: { "xt/reveal": { ticket: "t", url: "https://evil.example/pii/reveal" } } };
    };
    await expect(revealTokens([TOKEN], { callTool: evil, fetch: xt.fetchImpl, xtOrigin: XT, subtle: webcrypto.subtle as SubtleCrypto })).rejects.toThrow(RevealError);
    expect(xt.posts).toEqual([]);
  });

  it("lỗi server chỉ trả mã, không chứa giá trị", async () => {
    const xt = mockXt();
    const f: typeof fetch = async () => new Response(JSON.stringify({ error: "TICKET_INVALID" }), { status: 403 });
    await expect(revealTokens([TOKEN], { callTool: xt.callTool, fetch: f, xtOrigin: XT, subtle: webcrypto.subtle as SubtleCrypto })).rejects.toMatchObject({ code: "TICKET_INVALID" });
  });
});

describe("giá trị thật không vào history LLM", () => {
  it("agent chạy với token; reveal sau đó không đổi history, LLM không thấy giá trị, tool vé không có trong tool của model", async () => {
    const xt = mockXt();
    const tools: McpTool[] = [
      { name: "get_record", inputSchema: { type: "object", properties: {} } },
      { name: "pii_reveal_ticket", inputSchema: { type: "object", properties: {} }, _meta: { ui: { visibility: ["app"] } } },
    ];
    const sel = selectTools(tools, "all");
    expect(sel.tools.some(isRevealTool)).toBe(false);

    const seen: ChatMessage[][] = [];
    const replies: LlmReply[] = [
      { content: "", tool_calls: [{ id: "c1", type: "function", function: { name: "get_record", arguments: "{}" } }] },
      { content: `Lương: ${TOKEN}`, tool_calls: [] },
    ];
    let i = 0;
    const llm: LlmClient = {
      async complete({ messages }) {
        seen.push(messages.map((m) => ({ ...m })));
        return replies[i++];
      },
    };
    const history: ChatMessage[] = [{ role: "system", content: "s" }, { role: "user", content: "xem lương" }];
    const result: ToolResult = { structuredContent: { luong: TOKEN } };
    await runAgent({ llm, mcp: { callTool: async () => result }, tools: sel.tools, history, maxSteps: 4 });

    const before = JSON.stringify(history);
    const out = await revealTokens(extractTokens(result), { callTool: xt.callTool, fetch: xt.fetchImpl, xtOrigin: XT, subtle: webcrypto.subtle as SubtleCrypto });
    expect(out.values[TOKEN]).toBe(SECRET);

    expect(JSON.stringify(history)).toBe(before);
    expect(JSON.stringify(history)).not.toContain(SECRET);
    expect(JSON.stringify(history)).not.toContain("tk-");
    expect(JSON.stringify(seen)).not.toContain(SECRET);
    expect(JSON.stringify(history)).toContain(TOKEN);
  });
});

describe("RevealStore", () => {
  it("clear khi điều hướng/tải lại (pagehide, popstate, hashchange, pageshow) và khi gỡ", () => {
    for (const ev of ["pagehide", "popstate", "hashchange", "pageshow"]) {
      const target = new EventTarget();
      const s = createRevealStore();
      const off = s.clearOnNavigation(target);
      s.set({ values: { [TOKEN]: SECRET }, denied: [] });
      expect(s.get()?.values[TOKEN]).toBe(SECRET);
      target.dispatchEvent(new Event(ev));
      expect(s.get()).toBeNull();
      s.set({ values: { [TOKEN]: SECRET }, denied: [] });
      off();
      target.dispatchEvent(new Event(ev));
      expect(s.get()).not.toBeNull();
    }
  });
  it("không ghi console", async () => {
    const logs: unknown[] = [];
    const orig = { log: console.log, error: console.error, warn: console.warn };
    console.log = console.error = console.warn = (...a: unknown[]) => void logs.push(a);
    try {
      const xt = mockXt();
      await revealTokens([TOKEN], { callTool: xt.callTool, fetch: xt.fetchImpl, xtOrigin: XT, subtle: webcrypto.subtle as SubtleCrypto });
    } finally {
      Object.assign(console, orig);
    }
    expect(logs).toEqual([]);
  });
});
