// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
export interface McpTool {
  name: string;
  description?: string;
  inputSchema: Record<string, unknown>;
  _meta?: { ui?: { resourceUri?: string; visibility?: string[] }; [k: string]: unknown };
}

export interface ToolResult {
  isError?: boolean;
  content?: Array<{ type: string; text?: string; [k: string]: unknown }>;
  structuredContent?: unknown;
  _meta?: Record<string, unknown>;
}

export interface ToolCall {
  id: string;
  type: "function";
  function: { name: string; arguments: string };
}

export interface ChatMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
}

export interface OpenAITool {
  type: "function";
  function: { name: string; description?: string; parameters: Record<string, unknown> };
}

export interface LlmReply {
  content: string;
  tool_calls: ToolCall[];
}

export interface LlmClient {
  complete(
    req: { messages: ChatMessage[]; tools: OpenAITool[]; signal?: AbortSignal },
    onDelta: (text: string) => void,
  ): Promise<LlmReply>;
}

export interface McpCaller {
  callTool(p: { name: string; arguments: Record<string, unknown> }): Promise<ToolResult>;
}

export type AgentEvent =
  | { type: "assistant_delta"; step: number; text: string }
  | { type: "assistant_message"; step: number; content: string }
  | { type: "tool_start"; id: string; name: string; args: Record<string, unknown> }
  | { type: "tool_end"; id: string; name: string; args: Record<string, unknown>; result: ToolResult }
  | { type: "max_steps"; steps: number };
