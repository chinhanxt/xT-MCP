// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { describe, expect, it } from "vitest";
import { resultForModel, runAgent } from "../src/agent";
import { ToolCallAccumulator } from "../src/llm";
import type { AgentEvent, ChatMessage, LlmClient, LlmReply, McpCaller, McpTool, ToolResult } from "../src/types";

const tool = (name: string): McpTool => ({ name, description: name, inputSchema: { type: "object", properties: {} } });
const call = (id: string, name: string, args: unknown): LlmReply["tool_calls"][number] => ({
  id,
  type: "function",
  function: { name, arguments: typeof args === "string" ? args : JSON.stringify(args) },
});

function mockLlm(replies: LlmReply[]): LlmClient & { seen: ChatMessage[][]; seenTools: string[][] } {
  const seen: ChatMessage[][] = [];
  const seenTools: string[][] = [];
  let i = 0;
  return {
    seen,
    seenTools,
    async complete({ messages, tools }, onDelta) {
      seen.push(messages.map((m) => ({ ...m })));
      seenTools.push(tools.map((t) => t.function.name));
      const r = replies[Math.min(i++, replies.length - 1)];
      if (r.content) onDelta(r.content);
      return r;
    },
  };
}

function mockMcp(handler: (name: string, args: Record<string, unknown>) => ToolResult | Promise<ToolResult>) {
  const calls: Array<{ name: string; arguments: Record<string, unknown> }> = [];
  const mcp: McpCaller = {
    async callTool(p) {
      calls.push(p);
      return handler(p.name, p.arguments);
    },
  };
  return { mcp, calls };
}

const base = (): ChatMessage[] => [{ role: "system", content: "s" }, { role: "user", content: "hỏi" }];

it("preserves typed text-only error envelopes without nested JSON escaping", () => {
  const envelope = { ok: false, error: { code: "invalid_request", details: { valid_fields: ["x"] } } };
  expect(JSON.parse(resultForModel({ isError: true, content: [{ type: "text", text: JSON.stringify(envelope) }] }))).toEqual(envelope);
  expect(JSON.parse(resultForModel({ content: [{ type: "text", text: "plain business data" }] }))).toEqual([{ type: "text", text: "plain business data" }]);
});

describe("runAgent", () => {
  it("không có tool call: trả lời và dừng", async () => {
    const llm = mockLlm([{ content: "xin chào", tool_calls: [] }]);
    const { mcp, calls } = mockMcp(() => ({}));
    const events: AgentEvent[] = [];
    const history = base();
    const r = await runAgent({ llm, mcp, tools: [], history, maxSteps: 3, onEvent: (e) => events.push(e) });
    expect(r).toEqual({ steps: 1, stopped: "done" });
    expect(calls).toHaveLength(0);
    expect(history.at(-1)).toEqual({ role: "assistant", content: "xin chào" });
    expect(events.map((e) => e.type)).toEqual(["assistant_delta", "assistant_message"]);
  });

  it("gọi tool, đưa kết quả (structuredContent) lại cho model, rồi trả lời", async () => {
    const llm = mockLlm([
      { content: "", tool_calls: [call("c1", "find_people", { query: "Minh" })] },
      { content: "Minh là CTO", tool_calls: [] },
    ]);
    const { mcp, calls } = mockMcp(() => ({ structuredContent: { ok: true, data: { name: "Minh" } }, content: [{ type: "text", text: "x" }] }));
    const events: AgentEvent[] = [];
    const history = base();
    const r = await runAgent({ llm, mcp, tools: [tool("find_people")], history, maxSteps: 4, onEvent: (e) => events.push(e) });
    expect(r.stopped).toBe("done");
    expect(r.steps).toBe(2);
    expect(calls).toEqual([{ name: "find_people", arguments: { query: "Minh" } }]);
    const toolMsg = llm.seen[1].at(-1)!;
    expect(toolMsg).toEqual({ role: "tool", tool_call_id: "c1", content: JSON.stringify({ ok: true, data: { name: "Minh" } }) });
    expect(events.map((e) => e.type)).toEqual(["tool_start", "tool_end", "assistant_delta", "assistant_message"]);
  });

  it("max-steps guard: model gọi tool mãi thì dừng đúng giới hạn", async () => {
    const llm = mockLlm([{ content: "", tool_calls: [call("c", "find_people", {})] }]);
    const { mcp, calls } = mockMcp(() => ({ content: [{ type: "text", text: "ok" }] }));
    const events: AgentEvent[] = [];
    const r = await runAgent({ llm, mcp, tools: [tool("find_people")], history: base(), maxSteps: 3, onEvent: (e) => events.push(e) });
    expect(r).toEqual({ steps: 3, stopped: "max_steps" });
    expect(calls).toHaveLength(3);
    expect(events.at(-1)).toEqual({ type: "max_steps", steps: 3 });
  });

  it("tool ngoài hồ sơ bị từ chối, không gọi MCP, lỗi được đưa lại cho model", async () => {
    const llm = mockLlm([
      { content: "", tool_calls: [call("c1", "delete_record", {})] },
      { content: "không làm được", tool_calls: [] },
    ]);
    const { mcp, calls } = mockMcp(() => ({}));
    const events: AgentEvent[] = [];
    await runAgent({ llm, mcp, tools: [tool("find_people")], history: base(), maxSteps: 3, onEvent: (e) => events.push(e) });
    expect(calls).toHaveLength(0);
    const end = events.find((e) => e.type === "tool_end") as Extract<AgentEvent, { type: "tool_end" }>;
    expect(end.result.isError).toBe(true);
    expect(llm.seen[1].at(-1)!.content).toContain("không có trong hồ sơ");
  });

  it("JSON tham số hỏng và MCP ném lỗi đều thành tool error, vòng lặp không vỡ", async () => {
    const llm = mockLlm([
      { content: "", tool_calls: [call("c1", "find_people", "{bad"), call("c2", "find_people", {})] },
      { content: "xong", tool_calls: [] },
    ]);
    const { mcp, calls } = mockMcp(() => {
      throw new Error("mạng đứt");
    });
    const events: AgentEvent[] = [];
    const r = await runAgent({ llm, mcp, tools: [tool("find_people")], history: base(), maxSteps: 3, onEvent: (e) => events.push(e) });
    expect(r.stopped).toBe("done");
    expect(calls).toHaveLength(1); // call c1 không gọi MCP
    const ends = events.filter((e) => e.type === "tool_end") as Array<Extract<AgentEvent, { type: "tool_end" }>>;
    expect(ends.map((e) => e.result.isError)).toEqual([true, true]);
    expect(ends[1].result.content![0].text).toContain("mạng đứt");
    expect(events.filter((e) => e.type === "tool_start")).toHaveLength(2);
  });

  it("chỉ gửi cho LLM các tool đã lọc", async () => {
    const llm = mockLlm([{ content: "ok", tool_calls: [] }]);
    await runAgent({ llm, mcp: mockMcp(() => ({})).mcp, tools: [tool("a"), tool("b")], history: base(), maxSteps: 1 });
    expect(llm.seenTools[0]).toEqual(["a", "b"]);
  });

  it("abort trước bước đầu", async () => {
    const ac = new AbortController();
    ac.abort();
    const llm = mockLlm([{ content: "x", tool_calls: [] }]);
    const r = await runAgent({ llm, mcp: mockMcp(() => ({})).mcp, tools: [], history: base(), maxSteps: 2, signal: ac.signal });
    expect(r.stopped).toBe("aborted");
    expect(llm.seen).toHaveLength(0);
  });

  it("discards a late LLM reply after abort without events, history or idempotency changes", async () => {
    const ac = new AbortController();
    let finish!: (reply: LlmReply) => void;
    let delta!: (text: string) => void;
    const llm: LlmClient = { complete: (_request, onDelta) => {
      delta = onDelta;
      return new Promise(resolve => { finish = resolve; });
    } };
    const { mcp, calls } = mockMcp(() => ({}));
    const events: AgentEvent[] = [];
    const history = base();
    const idempotencyKeys = new Map<string, string>();
    const write: McpTool = { name: "write", inputSchema: { type: "object", properties: { idempotency_key: { type: "string" } } } };
    const running = runAgent({ llm, mcp, tools: [write], history, maxSteps: 2, signal: ac.signal, idempotencyKeys, onEvent: event => events.push(event) });
    ac.abort();
    delta("Prior tenant secret");
    finish({ content: "Prior tenant secret", tool_calls: [call("old", "write", { idempotency_key: "old-write" })] });
    expect((await running).stopped).toBe("aborted");
    expect(calls).toEqual([]);
    expect(events).toEqual([]);
    expect(history).toEqual(base());
    expect(idempotencyKeys.size).toBe(0);
  });

  it.each(["resolve", "reject"] as const)("stops remaining tools when the pending first tool %ss after abort", async outcome => {
    const ac = new AbortController();
    let finish!: (result: ToolResult) => void;
    let reject!: (error: Error) => void;
    let started!: () => void;
    const firstStarted = new Promise<void>(resolve => { started = resolve; });
    const llm = mockLlm([{ content: "", tool_calls: [call("first", "read", {}), call("second", "read", {})] }]);
    let dispatched = 0;
    const { mcp, calls } = mockMcp(() => {
      if (++dispatched > 1) return {};
      started();
      return new Promise((resolve, fail) => { finish = resolve; reject = fail; });
    });
    const events: AgentEvent[] = [];
    const history = base();
    const running = runAgent({ llm, mcp, tools: [tool("read")], history, maxSteps: 2, signal: ac.signal, onEvent: event => events.push(event) });
    await firstStarted;
    ac.abort();
    if (outcome === "resolve") finish({ content: [{ type: "text", text: "Prior tenant secret" }] });
    else reject(new Error("Old request failed"));
    expect((await running).stopped).toBe("aborted");
    expect(calls).toEqual([{ name: "read", arguments: {} }]);
    expect(events.map(event => event.type)).toEqual(["tool_start"]);
    expect(history.filter(message => message.role === "tool")).toEqual([]);
    expect(llm.seen).toHaveLength(1);
  });

  it("does not dispatch a tool when an event subscriber aborts the run", async () => {
    const ac = new AbortController();
    const llm = mockLlm([{ content: "Preparing action", tool_calls: [call("write", "write", {})] }]);
    const { mcp, calls } = mockMcp(() => ({}));
    const events: AgentEvent[] = [];
    const result = await runAgent({ llm, mcp, tools: [tool("write")], history: base(), maxSteps: 2, signal: ac.signal, onEvent: event => {
      events.push(event);
      if (event.type === "tool_start") ac.abort();
    } });
    expect(result.stopped).toBe("aborted");
    expect(calls).toEqual([]);
    expect(events.at(-1)?.type).toBe("tool_start");
  });
});

describe("ToolCallAccumulator", () => {
  it("chuẩn OpenAI: mảnh đầu id+name, mảnh sau chỉ arguments", () => {
    const a = new ToolCallAccumulator();
    a.add({ index: 0, id: "c1", function: { name: "find_people", arguments: '{"q"' } });
    a.add({ index: 0, function: { arguments: ':"x"}' } });
    expect(a.result()).toEqual([{ id: "c1", type: "function", function: { name: "find_people", arguments: '{"q":"x"}' } }]);
  });
  it("kiểu ollama: nhiều call đầy đủ, id riêng, index lặp lại", () => {
    const a = new ToolCallAccumulator();
    a.add({ index: 0, id: "c1", function: { name: "a", arguments: "{}" } });
    a.add({ index: 0, id: "c2", function: { name: "b", arguments: "{}" } });
    expect(a.result().map((c) => c.function.name)).toEqual(["a", "b"]);
  });
});

it("isolates generated idempotency aliases between chats while preserving retries and confirmation", async () => {
  const write: McpTool = { name: "write", inputSchema: { type: "object", properties: { idempotency_key: { type: "string" } } } };
  const issued = new Map<string, string>();
  const execute = async (keys: Map<string, string>, key: string) => {
    const history = base();
    const llm = mockLlm([{ content: "", tool_calls: [call("w", "write", { idempotency_key: key })] }, { content: "done", tool_calls: [] }]);
    const { mcp, calls } = mockMcp(() => ({ structuredContent: { ok: true } }));
    await runAgent({ llm, mcp, tools: [write], history, maxSteps: 2, idempotencyKeys: keys });
    const actual = calls[0].arguments.idempotency_key as string;
    expect(JSON.parse(history[2].tool_calls![0].function.arguments).idempotency_key).toBe(actual);
    return actual;
  };
  const first = await execute(issued, "create-advance-1");
  expect(first).not.toBe("create-advance-1");
  expect(await execute(issued, "create-advance-1")).toBe(first);
  expect(await execute(issued, first)).toBe(first);
  expect(await execute(new Map(), "create-advance-1")).not.toBe(first);
});

it("keeps trusted continuation rules after tool data without accumulating them in history", async () => {
  const llm = mockLlm([{ content: "", tool_calls: [call("c", "read", {})] }, { content: "done", tool_calls: [] }]);
  const { mcp } = mockMcp(() => ({ content: [{ type: "text", text: "untrusted content" }] }));
  const history = base();
  await runAgent({ llm, mcp, tools: [tool("read")], history, maxSteps: 2, continuationRules: "canonical rules" });
  expect(llm.seen[0].at(-1)?.role).toBe("user");
  expect(llm.seen[1].at(-1)).toEqual({ role: "system", content: "canonical rules" });
  expect(history.filter((m) => m.content === "canonical rules")).toHaveLength(0);
});
