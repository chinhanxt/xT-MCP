// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Chính sách của HOST cho mọi thứ MCP App (iframe không tin cậy) yêu cầu qua bridge.
import { Ajv2020 } from "ajv/dist/2020.js";
import { ErrorCode, McpError } from "@modelcontextprotocol/sdk/types.js";
import { normalizeVisibility } from "./profiles";
import type { McpTool, ToolResult } from "./types";

/** Tool ghi / human-only của xT: App không được gọi dù server khai visibility thế nào (TCV1: "thẻ MCP App không có tool app-only để ghi"). */
export const APP_DENYLIST: ReadonlySet<string> = new Set([
  "decide_request", "transition_record", "create_record", "update_record", "submit_request", "create_task",
  "upload_file", "propose_model_change", "propose_process_change", "start_process", "delete_record", "notify",
]);

export interface RateLimiterOptions {
  ratePerSec: number;
  burst: number;
  maxInflight: number;
  now?: () => number;
}

/** Token bucket cho MỖI instance App + trần số request đang chờ. */
export function createRateLimiter(o: RateLimiterOptions) {
  const now = o.now ?? (() => Date.now());
  let tokens = o.burst;
  let last = now();
  let inflight = 0;
  return {
    tryAcquire(): boolean {
      const t = now();
      tokens = Math.min(o.burst, tokens + ((t - last) / 1000) * o.ratePerSec);
      last = t;
      if (tokens < 1 || inflight >= o.maxInflight) return false;
      tokens -= 1;
      inflight += 1;
      return true;
    },
    release(): void {
      inflight = Math.max(0, inflight - 1);
    },
  };
}

type Limiter = ReturnType<typeof createRateLimiter>;

/** Rate limit CHUNG cho mọi instance App, theo từng tên tool (token bucket, không có trần inflight). */
export function createGlobalToolLimiter(o: { ratePerSec: number; burst: number; now?: () => number }) {
  const buckets = new Map<string, ReturnType<typeof createRateLimiter>>();
  return {
    tryAcquire(name: string): boolean {
      let b = buckets.get(name);
      if (!b) {
        b = createRateLimiter({ ratePerSec: o.ratePerSec, burst: o.burst, maxInflight: Number.MAX_SAFE_INTEGER, now: o.now });
        buckets.set(name, b);
      }
      const ok = b.tryAcquire();
      if (ok) b.release();
      return ok;
    },
  };
}

/** Với lời gọi từ App: thiếu additionalProperties nghĩa là false (đệ quy qua properties/items). Không sửa schema gốc. */
export function closeSchema(schema: unknown): unknown {
  if (Array.isArray(schema)) return schema.map(closeSchema);
  if (schema === null || typeof schema !== "object") return schema;
  const o: Record<string, unknown> = { ...(schema as Record<string, unknown>) };
  const isObj = o.type === "object" || (o.properties !== undefined && typeof o.properties === "object");
  if (isObj && o.additionalProperties === undefined) o.additionalProperties = false;
  if (o.properties && typeof o.properties === "object") {
    o.properties = Object.fromEntries(Object.entries(o.properties as object).map(([k, v]) => [k, closeSchema(v)]));
  }
  if (o.items !== undefined) o.items = closeSchema(o.items);
  return o;
}

export interface AppBridgeDeps {
  tools: McpTool[]; // danh sách ĐẦY ĐỦ từ tools/list (không phải hồ sơ đã lọc)
  callTool: (p: { name: string; arguments: Record<string, unknown> }) => Promise<ToolResult>;
  readResource: (p: { uri: string }) => Promise<unknown>;
  limiter: Limiter;
  globalLimiter: { tryAcquire(name: string): boolean };
  maxArgsBytes: number;
}

const deny = (msg: string) => new McpError(ErrorCode.InvalidParams, msg);

export function createAppBridgeHandlers(d: AppBridgeDeps) {
  const ajv = new Ajv2020({ strict: false });
  const validators = new Map<string, ReturnType<typeof ajv.compile>>();
  const byName = new Map(d.tools.map((t) => [t.name, t]));

  return {
    async onCallTool(params: { name: string; arguments?: Record<string, unknown> }): Promise<ToolResult> {
      const tool = byName.get(params.name);
      if (!tool) throw deny(`Tool "${params.name}" không tồn tại`);
      // phải khai "app" TƯỜNG MINH; không dựa vào mặc định ["model","app"]
      const vis = normalizeVisibility(tool._meta?.ui?.visibility);
      if (!vis) throw deny(`Tool "${params.name}" có visibility không hợp lệ`);
      if (tool._meta?.ui?.visibility === undefined || !vis.includes("app")) throw deny(`Tool "${params.name}" không dành cho App`);
      if (APP_DENYLIST.has(tool.name)) throw deny(`Tool "${params.name}" bị chặn với App`);
      const args = params.arguments ?? {};
      if (new TextEncoder().encode(JSON.stringify(args)).length > d.maxArgsBytes) throw deny(`Tham số của "${params.name}" vượt quá ${d.maxArgsBytes} byte`);
      let validate = validators.get(tool.name);
      if (!validate) {
        try {
          validate = ajv.compile(closeSchema(tool.inputSchema) as object);
        } catch {
          throw deny(`Không kiểm được schema của tool "${params.name}"`);
        }
        validators.set(tool.name, validate);
      }
      if (!validate(args)) throw deny(`Tham số không hợp lệ cho "${params.name}": ${ajv.errorsText(validate.errors)}`);
      if (!d.limiter.tryAcquire()) throw new McpError(ErrorCode.InvalidRequest, "Quá nhiều lời gọi tool từ App, thử lại sau");
      if (!d.globalLimiter.tryAcquire(tool.name)) {
        d.limiter.release();
        throw new McpError(ErrorCode.InvalidRequest, `Quá nhiều lời gọi "${tool.name}" từ các App, thử lại sau`);
      }
      try {
        return await d.callTool({ name: tool.name, arguments: args });
      } finally {
        d.limiter.release();
      }
    },
    async onReadResource(params: { uri: string }) {
      if (typeof params.uri !== "string" || !params.uri.startsWith("ui://")) throw deny("App chỉ được đọc resource ui://");
      return d.readResource({ uri: params.uri });
    },
    async onListResources() {
      return { resources: [] };
    },
    async onListResourceTemplates() {
      return { resourceTemplates: [] };
    },
    async onListPrompts() {
      return { prompts: [] };
    },
  };
}

// ---- ui/open-link ----
export type OpenLinkDecision = { action: "open" | "confirm" } | { action: "deny"; reason: string };

export function checkOpenLink(url: string, o: { xtOrigin: string; userActive: boolean }): OpenLinkDecision {
  let u: URL;
  try {
    u = new URL(url);
  } catch {
    return { action: "deny", reason: "URL không hợp lệ" };
  }
  if (u.protocol !== "https:" && u.protocol !== "http:") return { action: "deny", reason: "Chỉ cho phép http(s)" };
  if (u.username || u.password) return { action: "deny", reason: "URL chứa thông tin đăng nhập" };
  if (!o.userActive) return { action: "deny", reason: "Cần thao tác của người dùng" };
  return { action: u.origin === new URL(o.xtOrigin).origin ? "open" : "confirm" };
}

/** Nội dung hộp thoại xác nhận mở link: origin (host dạng punycode) nổi bật, URL chuẩn hóa đã bỏ ký tự điều khiển/bidi, một dòng. */
export function formatOpenLinkPrompt(url: string): string {
  // eslint-disable-next-line no-control-regex
  const bad = /[\u0000-\u001f\u007f-\u009f\u00ad\u061c\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff]/g;
  const u = new URL(url); // đã qua checkOpenLink; href chuẩn hóa: host punycode, ký tự lạ trong path/query bị percent-encode
  const shown = u.href.replace(bad, "");
  const clipped = shown.length > 300 ? `${shown.slice(0, 300)}…` : shown;
  return `Ứng dụng muốn mở một trang NGOÀI xT.\n\nTrang đích: ${u.origin.replace(bad, "")}\n\nĐịa chỉ đầy đủ: ${clipped}\n\nChỉ mở nếu bạn tin tưởng trang này.`;
}

/** Activation chỉ tính khi tiêu điểm đang nằm trong iframe của CHÍNH khung App yêu cầu (không mượn cú bấm vào App khác). */
export function isActivationInFrame(active: Element | null, frame: Element | null): boolean {
  return !!active && !!frame && active.tagName === "IFRAME" && frame.contains(active);
}

// ---- CSP truyền cho sandbox ----
export interface UiCsp {
  connectDomains?: string[];
  resourceDomains?: string[];
  frameDomains?: string[];
  baseUriDomains?: string[];
}

/** Chỉ giữ connectDomains trùng ĐÚNG origin xT (kênh /pii/reveal). Mọi domain khác, `*` và các nhóm khác bị bỏ. */
export function sanitizeUiCsp(csp: UiCsp | undefined, xtOrigin: string): UiCsp {
  const xt = new URL(xtOrigin).origin;
  const connect = (csp?.connectDomains ?? []).filter((d) => {
    try {
      return typeof d === "string" && !d.includes("*") && new URL(d).origin === xt && d.replace(/\/$/, "") === xt;
    } catch {
      return false;
    }
  });
  return connect.length ? { connectDomains: [xt] } : {};
}
