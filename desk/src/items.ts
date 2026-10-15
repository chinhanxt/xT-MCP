// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ToolEntry } from "./ToolItem";
import type { AgentEvent, McpTool } from "./types";

export type Item =
  | { kind: "user"; id: string; text: string }
  | { kind: "assistant"; id: string; text: string }
  | { kind: "tool"; id: string; entry: ToolEntry }
  | { kind: "note"; id: string; text: string };

/** Khóa tool-call theo (lượt, id): LLM/server có thể dùng lại cùng tool_call_id ở các lượt hỏi khác nhau,
 *  nên id thô không được dùng làm khóa React hay để ghép tool_end (nếu không, lượt sau ghi đè thẻ lượt trước). */
export const toolItemId = (run: number, id: string) => `t-${run}-${id}`;

export function applyAgentEvent(prev: Item[], e: AgentEvent, run: number, toolsByName: Map<string, McpTool>, nid: () => string): Item[] {
  switch (e.type) {
    case "assistant_delta": {
      const id = `a-${e.step}-${run}`;
      const i = prev.findIndex((x) => x.id === id);
      if (i < 0) return [...prev, { kind: "assistant", id, text: e.text }];
      const cur = prev[i] as Extract<Item, { kind: "assistant" }>;
      const next = prev.slice();
      next[i] = { ...cur, text: cur.text + e.text };
      return next;
    }
    case "tool_start":
      return [
        ...prev,
        {
          kind: "tool",
          id: toolItemId(run, e.id),
          entry: { id: e.id, name: e.name, args: e.args, status: "running", resourceUri: toolsByName.get(e.name)?._meta?.ui?.resourceUri },
        },
      ];
    case "tool_end": {
      const id = toolItemId(run, e.id);
      return prev.map((x) => (x.kind === "tool" && x.id === id ? { ...x, entry: { ...x.entry, status: e.result.isError ? "error" : "ok", result: e.result } } : x));
    }
    case "max_steps":
      return [...prev, { kind: "note", id: nid(), text: `Đã dừng sau ${e.steps} bước (giới hạn DESK_MAX_STEPS).` }];
    default:
      return prev;
  }
}
