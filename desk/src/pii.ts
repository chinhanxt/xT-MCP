// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Kênh xem giá trị thật PII cho NGƯỜI (research/spike-pii-lane.md §3.7, docs/integration-notes.md "Desk ↔ /pii/reveal").
// Giá trị thật chỉ nằm trong RevealStore (bộ nhớ của component), KHÔNG vào history của LLM, không log, xóa khi điều hướng.
import { athOf, type DpopProver } from "./dpop";
import type { McpTool, ToolResult } from "./types";

/** Định dạng token DUY NHẤT của server xT: `[PII:<16 base32>]` (tool-contracts-v1 §3.8). Định dạng
 * `⟨MÃ.trường⟩` của spike đã bỏ (audit PIIR-06): server không bao giờ phát ra nó. */
export const PII_TOKEN_RE = /\[PII:[a-z2-7]{16}\]/g;
export const REVEAL_TICKET_TOOL = "pii_reveal_ticket";
export const MAX_REVEAL_TOKENS = 50;
export const REVEAL_TTL_MS = 60_000;

export const REVEAL_TOOLTIP =
  "Giá trị thật của dữ liệu cá nhân không bao giờ được gửi cho mô hình AI. Khi bấm \"Hiện\", xT cấp một vé dùng một lần và chỉ hiển thị giá trị cho riêng bạn trên màn hình này. Giá trị không được lưu và sẽ tự ẩn khi bạn tải lại hoặc chuyển trang.";

export function extractTokens(r: ToolResult | undefined): string[] {
  if (!r) return [];
  const text = JSON.stringify(r.structuredContent ?? r.content ?? null);
  return [...new Set(text.match(PII_TOKEN_RE) ?? [])].slice(0, MAX_REVEAL_TOKENS);
}

export interface RevealOutcome {
  values: Record<string, string>;
  denied: string[];
}

export class RevealError extends Error {
  constructor(public code: string) {
    super(code);
  }
}

const b64u = (b: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");

export interface RevealDeps {
  /** Gọi tool qua client MCP của Desk (KHÔNG qua runAgent/history). */
  callTool: (p: { name: string; arguments: Record<string, unknown> }) => Promise<ToolResult>;
  fetch: typeof fetch;
  xtOrigin: string;
  subtle?: SubtleCrypto;
  now?: () => number;
  /** Phiên DPoP-bound: vé ràng với khóa DPoP của access token, đổi vé bằng proof (ath = SHA-256 của vé). */
  dpop?: { signer: DpopProver; bound: boolean };
}

async function postReveal(d: RevealDeps, url: string, headers: Record<string, string>, body: unknown): Promise<Response> {
  return d.fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: JSON.stringify(body),
    credentials: "omit",
    cache: "no-store",
    referrerPolicy: "no-referrer",
  });
}

async function mintTicket(args: Record<string, unknown>, d: RevealDeps): Promise<{ ticket: string; url: string }> {
  const minted = await d.callTool({ name: REVEAL_TICKET_TOOL, arguments: args });
  const meta = minted._meta?.["xt/reveal"] as { ticket?: unknown; url?: unknown } | undefined;
  if (minted.isError || typeof meta?.ticket !== "string" || !meta.ticket) throw new RevealError("TICKET_UNAVAILABLE");
  const url = `${new URL(d.xtOrigin).origin}/pii/reveal`;
  // chỉ gửi vé tới origin xT đã cấu hình; url do server/tool khai mà khác thì từ chối (vé không rời Desk)
  if (typeof meta.url === "string" && meta.url !== url) throw new RevealError("BAD_REVEAL_URL");
  return { ticket: meta.ticket, url };
}

/**
 * Một lần xem. Phiên DPoP-bound: tool chỉ nhận {tokens} (server ràng vé với khóa DPoP của access token)
 * -> POST {ticket} kèm proof DPoP (ath = SHA-256 của vé). Chưa bound: khóa ECDSA P-256 non-extractable riêng
 * cho khung này -> vé ràng pubkey_jwk -> POST {ticket, ts, sig}. Vé dùng đúng một lần; mỗi lần gọi là vé mới, không thử lại.
 */
export async function revealTokens(tokens: string[], d: RevealDeps): Promise<RevealOutcome> {
  if (!tokens.length || tokens.length > MAX_REVEAL_TOKENS) throw new RevealError("BAD_TOKENS");
  let res: Response;
  if (d.dpop?.bound) {
    const { ticket, url } = await mintTicket({ tokens }, d);
    const proof = await d.dpop.signer.proof("POST", url, { ath: await athOf(ticket, d.subtle) });
    res = await postReveal(d, url, { DPoP: proof }, { ticket });
  } else {
    const subtle = d.subtle ?? globalThis.crypto.subtle;
    const now = d.now ?? (() => Date.now());
    const keys = await subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
    const pubkey_jwk = await subtle.exportKey("jwk", keys.publicKey);
    const { ticket, url } = await mintTicket({ tokens, pubkey_jwk }, d);
    const ts = Math.floor(now() / 1000);
    const sig = b64u(await subtle.sign({ name: "ECDSA", hash: "SHA-256" }, keys.privateKey, new TextEncoder().encode(`${ticket}.${ts}`)));
    res = await postReveal(d, url, {}, { ticket, ts, sig });
  }
  if (!res.ok) {
    let code = `HTTP_${res.status}`;
    try {
      const e = (await res.json()) as { error?: unknown };
      if (typeof e.error === "string") code = e.error;
    } catch {
      /* chỉ lấy mã lỗi */
    }
    throw new RevealError(code);
  }
  const body = (await res.json()) as { values?: Record<string, unknown>; denied?: unknown };
  const values: Record<string, string> = {};
  for (const t of tokens) if (typeof body.values?.[t] === "string") values[t] = body.values[t] as string;
  return { values, denied: tokens.filter((t) => !(t in values)) };
}

/** Giữ giá trị thật trong bộ nhớ; clear() khi điều hướng/tải lại/hết hạn. Không có persistence nào. */
export function createRevealStore() {
  let state: RevealOutcome | null = null;
  const subs = new Set<() => void>();
  const emit = () => subs.forEach((f) => f());
  return {
    get: () => state,
    set(o: RevealOutcome) {
      state = o;
      emit();
    },
    clear() {
      if (state) {
        state = null;
        emit();
      }
    },
    subscribe(f: () => void) {
      subs.add(f);
      return () => void subs.delete(f);
    },
    /** Xóa khi rời trang (pagehide, popstate, hashchange) hoặc bfcache khôi phục; trả hàm gỡ. */
    clearOnNavigation(target: Pick<EventTarget, "addEventListener" | "removeEventListener">) {
      const h = () => this.clear();
      const evs = ["pagehide", "popstate", "hashchange", "pageshow"];
      evs.forEach((e) => target.addEventListener(e, h));
      return () => evs.forEach((e) => target.removeEventListener(e, h));
    },
  };
}
export type RevealStore = ReturnType<typeof createRevealStore>;

/** pii_reveal_ticket là app-only: không bao giờ nằm trong tool đưa cho model. */
export const isRevealTool = (t: McpTool) => t.name === REVEAL_TICKET_TOOL;
