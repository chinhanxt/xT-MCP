// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { describe, expect, it } from "vitest";
import { applyAgentEvent, type Item } from "../src/items";

const nid = () => "n";
const none = new Map();
const res = (t: string) => ({ content: [{ type: "text", text: t }], isError: true });

describe("applyAgentEvent", () => {
  it("cùng tool_call_id ở hai lượt: không ghi đè kết quả lượt trước", () => {
    let items: Item[] = [];
    for (const [run, text] of [[1, "A"], [2, "B"]] as const) {
      items = applyAgentEvent(items, { type: "tool_start", id: "call_1", name: "decide_request", args: {} } as never, run, none, nid);
      items = applyAgentEvent(items, { type: "tool_end", id: "call_1", name: "decide_request", args: {}, result: res(text) } as never, run, none, nid);
    }
    const tools = items.filter((i) => i.kind === "tool") as Extract<Item, { kind: "tool" }>[];
    expect(tools.map((t) => t.id)).toEqual([...new Set(tools.map((t) => t.id))]);
    expect(tools.map((t) => t.entry.result?.content?.[0]?.text)).toEqual(["A", "B"]);
  });
});
