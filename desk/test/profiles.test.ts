// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { describe, expect, it } from "vitest";
import { CORE_10, STANDARD, selectTools } from "../src/profiles";
import type { McpTool } from "../src/types";

const t = (name: string, visibility?: string[]): McpTool => ({ name, inputSchema: {}, _meta: visibility ? { ui: { visibility } } : undefined });

describe("selectTools", () => {
  const all = [...CORE_10.map((n) => t(n)), t("inspect_workspace"), t("describe_model"), t("get_employee", ["app"]), t("both", ["model", "app"])];
  it("standard: discovers workspace and schemas before the ten business primitives", () => {
    const selected = selectTools(all, "standard");
    expect(selected.tools.map((tool) => tool.name)).toEqual([...STANDARD]);
    expect(selected.tools).toHaveLength(12);
    expect(selected.missing).toEqual([]);
    expect(CORE_10).toHaveLength(10);
  });
  it("core-10: đúng 10 tool, thứ tự hồ sơ", () => {
    const r = selectTools(all, "core-10");
    expect(r.tools.map((x) => x.name)).toEqual([...CORE_10]);
    expect(r.missing).toEqual([]);
  });
  it("all: bỏ tool chỉ dành cho app", () => {
    expect(selectTools(all, "all").tools.map((x) => x.name)).not.toContain("get_employee");
    expect(selectTools(all, "all").tools.map((x) => x.name)).toContain("both");
  });
  it("danh sách tùy chọn + báo tool thiếu; tool app-only không bao giờ lọt", () => {
    const r = selectTools(all, "find_people, get_employee, nope");
    expect(r.tools.map((x) => x.name)).toEqual(["find_people"]);
    expect(r.missing).toEqual(["get_employee", "nope"]);
  });
  it("hồ sơ rỗng là lỗi", () => {
    expect(() => selectTools(all, " , ")).toThrow();
  });
});
