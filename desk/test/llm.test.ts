// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { afterEach, expect, it, vi } from "vitest";
import { createLlm } from "../src/llm";

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
