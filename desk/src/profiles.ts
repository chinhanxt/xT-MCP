// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { McpTool } from "./types";

// docs/tool-contracts-v1.md §10: hồ sơ core-10 cho model nhỏ
export const CORE_10 = [
  "find_people",
  "search_records",
  "aggregate_records",
  "get_record",
  "get_inbox",
  "create_record",
  "create_task",
  "submit_request",
  "transition_record",
  "decide_request",
] as const;

/** Standalone Desk discovers tenant schemas before calling the fixed business primitives. */
export const STANDARD = ["inspect_workspace", "describe_model", ...CORE_10] as const;

const KNOWN_VISIBILITY = new Set(["model", "app"]);

/** visibility hợp lệ = mảng chuỗi thuộc {"model","app"}; thiếu => mặc định ["model","app"]; kiểu khác/giá trị lạ => null (từ chối). */
export function normalizeVisibility(v: unknown): string[] | null {
  if (v === undefined) return ["model", "app"];
  if (!Array.isArray(v) || !v.every((x) => typeof x === "string" && KNOWN_VISIBILITY.has(x))) return null;
  return v as string[];
}

/** Tool chỉ dành cho UI (visibility không có "model") không bao giờ đưa cho model. */
export function isModelVisible(t: McpTool): boolean {
  const vis = normalizeVisibility(t._meta?.ui?.visibility);
  return !!vis && vis.includes("model");
}

export function selectTools(all: McpTool[], profile: string): { tools: McpTool[]; missing: string[] } {
  const visible = all.filter(isModelVisible);
  const p = profile.trim();
  if (p === "all") return { tools: visible, missing: [] };
  const names = p === "standard" ? [...STANDARD] : p === "core-10" ? [...CORE_10] : p.split(",").map((s) => s.trim()).filter(Boolean);
  if (!names.length) throw new Error(`Hồ sơ tool rỗng: "${profile}"`);
  const byName = new Map(visible.map((t) => [t.name, t]));
  const tools: McpTool[] = [];
  const missing: string[] = [];
  for (const n of names) {
    const t = byName.get(n);
    if (t) tools.push(t);
    else missing.push(n);
  }
  return { tools, missing };
}
