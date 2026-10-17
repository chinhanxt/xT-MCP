// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type {
  AgentEvent,
  ChatMessage,
  LlmClient,
  McpCaller,
  McpTool,
  OpenAITool,
  ToolCall,
  ToolResult,
} from "./types";

export function toOpenAITools(tools: McpTool[]): OpenAITool[] {
  return tools.map((t) => ({
    type: "function",
    function: { name: t.name, description: t.description, parameters: t.inputSchema },
  }));
}

const errResult = (text: string): ToolResult => ({ isError: true, content: [{ type: "text", text }] });

/** Nội dung gửi lại cho model: ưu tiên structuredContent (envelope của xT), nếu không thì content. */
export function resultForModel(r: ToolResult): string {
  if (r.structuredContent === undefined && r.content?.length === 1 && r.content[0].type === "text") {
    try {
      const envelope: unknown = JSON.parse(r.content[0].text ?? "");
      if (envelope && typeof envelope === "object" && "ok" in envelope && typeof envelope.ok === "boolean") {
        return JSON.stringify(envelope);
      }
    } catch { /* Plain text and multimodal results retain their original representation. */ }
  }
  return JSON.stringify(r.structuredContent ?? r.content ?? null);
}

export interface RunAgentOptions {
  llm: LlmClient;
  mcp: McpCaller;
  tools: McpTool[]; // đã lọc theo profile
  history: ChatMessage[]; // bị sửa tại chỗ (thêm assistant/tool)
  maxSteps: number;
  continuationRules?: string; // trusted canonical skill, refreshed after untrusted tool data
  idempotencyKeys?: Map<string, string>; // aliases scoped to this authenticated conversation
  onEvent?: (e: AgentEvent) => void;
  signal?: AbortSignal;
}

export async function runAgent(o: RunAgentOptions): Promise<{ steps: number; stopped: "done" | "max_steps" | "aborted" }> {
  const emit = (event: AgentEvent) => { if (!o.signal?.aborted) o.onEvent?.(event); };
  const allowed = new Set(o.tools.map((t) => t.name));
  const schemas = new Map(o.tools.map((t) => [t.name, t.inputSchema]));
  const openaiTools = toOpenAITools(o.tools);

  for (let step = 1; step <= o.maxSteps; step++) {
    if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
    const reply = await o.llm.complete(
      { messages: step > 1 && o.continuationRules
        ? [...o.history, { role: "system", content: o.continuationRules }]
        : o.history, tools: openaiTools, signal: o.signal },
      (text) => emit({ type: "assistant_delta", step, text }),
    );
    if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
    const calls: ToolCall[] = reply.tool_calls;
    o.history.push({ role: "assistant", content: reply.content || "", ...(calls.length ? { tool_calls: calls } : {}) });
    if (reply.content) emit({ type: "assistant_message", step, content: reply.content });
    if (!calls.length) return { steps: step, stopped: "done" };

    for (const tc of calls) {
      if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
      let args: Record<string, unknown> = {};
      let result: ToolResult | undefined;
      try {
        const parsed: unknown = JSON.parse(tc.function.arguments || "{}");
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("arguments phải là object");
        args = parsed as Record<string, unknown>;
      } catch (e) {
        result = errResult(`Tham số tool không hợp lệ: ${e instanceof Error ? e.message : String(e)}`);
      }
      // Models can repeat predictable aliases in independent chats. Desk owns the
      // transport key; keep aliases stable for retries/confirmation in this chat.
      const properties = schemas.get(tc.function.name)?.properties as Record<string, unknown> | undefined;
      if (!result && o.idempotencyKeys && properties?.idempotency_key &&
          typeof args.idempotency_key === "string" && args.idempotency_key.length > 0) {
        const alias = `${tc.function.name}\0${args.idempotency_key}`;
        let actual = o.idempotencyKeys.get(alias);
        if (!actual) {
          actual = crypto.randomUUID();
          o.idempotencyKeys.set(alias, actual);
          o.idempotencyKeys.set(`${tc.function.name}\0${actual}`, actual);
        }
        args.idempotency_key = actual;
        // The next model turn and tool chip must see the key actually sent.
        tc.function.arguments = JSON.stringify(args);
      }
      emit({ type: "tool_start", id: tc.id, name: tc.function.name, args });
      if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
      if (!result) {
        if (!allowed.has(tc.function.name)) {
          result = errResult(`Tool "${tc.function.name}" không có trong hồ sơ tool hiện tại.`);
        } else {
          try {
            result = await o.mcp.callTool({ name: tc.function.name, arguments: args });
          } catch (e) {
            result = errResult(`Gọi tool lỗi: ${e instanceof Error ? e.message : String(e)}`);
          }
        }
      }
      if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
      emit({ type: "tool_end", id: tc.id, name: tc.function.name, args, result });
      o.history.push({ role: "tool", tool_call_id: tc.id, content: resultForModel(result) });
    }
  }
  emit({ type: "max_steps", steps: o.maxSteps });
  return { steps: o.maxSteps, stopped: "max_steps" };
}
