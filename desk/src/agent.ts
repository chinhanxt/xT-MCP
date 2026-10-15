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
  return JSON.stringify(r.structuredContent ?? r.content ?? null);
}

export interface RunAgentOptions {
  llm: LlmClient;
  mcp: McpCaller;
  tools: McpTool[]; // đã lọc theo profile
  history: ChatMessage[]; // bị sửa tại chỗ (thêm assistant/tool)
  maxSteps: number;
  onEvent?: (e: AgentEvent) => void;
  signal?: AbortSignal;
}

export async function runAgent(o: RunAgentOptions): Promise<{ steps: number; stopped: "done" | "max_steps" | "aborted" }> {
  const emit = o.onEvent ?? (() => {});
  const allowed = new Set(o.tools.map((t) => t.name));
  const openaiTools = toOpenAITools(o.tools);

  for (let step = 1; step <= o.maxSteps; step++) {
    if (o.signal?.aborted) return { steps: step - 1, stopped: "aborted" };
    const reply = await o.llm.complete(
      { messages: o.history, tools: openaiTools, signal: o.signal },
      (text) => emit({ type: "assistant_delta", step, text }),
    );
    const calls: ToolCall[] = reply.tool_calls;
    o.history.push({ role: "assistant", content: reply.content || "", ...(calls.length ? { tool_calls: calls } : {}) });
    if (reply.content) emit({ type: "assistant_message", step, content: reply.content });
    if (!calls.length) return { steps: step, stopped: "done" };

    for (const tc of calls) {
      let args: Record<string, unknown> = {};
      let result: ToolResult | undefined;
      try {
        const parsed: unknown = JSON.parse(tc.function.arguments || "{}");
        if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("arguments phải là object");
        args = parsed as Record<string, unknown>;
      } catch (e) {
        result = errResult(`Tham số tool không hợp lệ: ${e instanceof Error ? e.message : String(e)}`);
      }
      emit({ type: "tool_start", id: tc.id, name: tc.function.name, args });
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
      emit({ type: "tool_end", id: tc.id, name: tc.function.name, args, result });
      o.history.push({ role: "tool", tool_call_id: tc.id, content: resultForModel(result) });
    }
  }
  emit({ type: "max_steps", steps: o.maxSteps });
  return { steps: o.maxSteps, stopped: "max_steps" };
}
