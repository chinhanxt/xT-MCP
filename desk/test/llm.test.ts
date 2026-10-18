// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { afterEach, describe, expect, it, vi } from "vitest";
import { chatFailureNote, createLlm, LLM_UNAVAILABLE_NOTE, LlmUnavailableError } from "../src/llm";

afterEach(() => vi.unstubAllGlobals());

it("signs the exact LLM request and passes the abort signal", async () => {
  vi.stubGlobal("location", { href: "https://desk.example/" });
  const authHeaders = vi.fn(async () => ({ Authorization: "DPoP token", DPoP: "fresh-proof" }));
  const fetch = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ choices: [{ message: { content: "ok" } }] }), {
    headers: { "content-type": "application/json" },
  }));
  vi.stubGlobal("fetch", fetch);
  const signal = new AbortController().signal;
  const llm = createLlm({ baseUrl: "/llm", model: "m", authHeaders });
  const result = await llm.complete({ messages: [], tools: [], signal }, () => {});
  expect(authHeaders).toHaveBeenCalledWith({ method: "POST", url: "https://desk.example/llm/chat/completions" });
  expect(fetch).toHaveBeenCalledWith("/llm/chat/completions", expect.objectContaining({
    signal, headers: expect.objectContaining({ Authorization: "DPoP token", DPoP: "fresh-proof" }),
  }));
  expect(result.content).toBe("ok");
  expect(JSON.parse(fetch.mock.calls[0][1]?.body as string).temperature).toBe(0);
});

describe("LLM chưa cấu hình (DESK_LLM_URL tắt hoặc không tới được)", () => {
  const complete = async (fetchImpl: () => Promise<Response>) => {
    vi.stubGlobal("location", { href: "https://desk.example/" });
    vi.stubGlobal("fetch", vi.fn(fetchImpl));
    const llm = createLlm({ baseUrl: "/llm", model: "m", authHeaders: async () => ({}) });
    return llm.complete({ messages: [], tools: [] }, () => {});
  };

  it.each([
    ["xt-api chưa bật route /llm", 404, ""],
    ["upstream LLM không tới được", 502, '{"error":"llm_unavailable"}'],
    ["gateway hết hạn", 504, ""],
    ["dịch vụ tạm ngừng", 503, ""],
  ])("%s (HTTP %i) -> trạng thái chưa cấu hình, không phải lỗi thô", async (_name, status, body) => {
    const err = await complete(async () => new Response(body, { status })).catch((e) => e);
    expect(err).toBeInstanceOf(LlmUnavailableError);
    expect(chatFailureNote(err)).toBe(LLM_UNAVAILABLE_NOTE);
    expect(chatFailureNote(err)).toMatch(/^Chưa cấu hình LLM/);
    expect(chatFailureNote(err)).not.toMatch(/Lỗi|HTTP|502|llm_unavailable/);
  });

  it("lỗi mạng (fetch TypeError) cũng là chưa cấu hình", async () => {
    const err = await complete(async () => { throw new TypeError("Failed to fetch"); }).catch((e) => e);
    expect(err).toBeInstanceOf(LlmUnavailableError);
  });

  it("hủy bởi người dùng vẫn là AbortError, không bị đổi thành chưa cấu hình", async () => {
    const abort = new DOMException("aborted", "AbortError");
    const err = await complete(async () => { throw abort; }).catch((e) => e);
    expect(err).toBe(abort);
  });

  it.each([400, 401, 413, 429])("HTTP %i vẫn là lỗi thật (phiên hết hạn không giả làm chưa cấu hình)", async (status) => {
    const err = await complete(async () => new Response('{"error":"x"}', { status })).catch((e) => e);
    expect(err).not.toBeInstanceOf(LlmUnavailableError);
    expect(chatFailureNote(err)).toMatch(/^Lỗi: LLM trả lỗi/);
  });
});
