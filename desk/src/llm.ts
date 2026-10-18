// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ChatMessage, LlmClient, LlmReply, OpenAITool, ToolCall } from "./types";

export interface LlmOptions {
  baseUrl: string; // vd. "/llm" (proxy cùng origin) -> POST {baseUrl}/chat/completions
  model: string;
  reasoningEffort?: string; // "" = không gửi
  temperature?: number;
  authHeaders: (req?: { method: string; url: string }) => Promise<Record<string, string>> | Record<string, string>; // proof gắn đúng request; ném lỗi => không gọi
}

interface Delta {
  content?: string | null;
  tool_calls?: Array<{ index?: number; id?: string; function?: { name?: string; arguments?: string } }>;
}

/** Gộp mảnh tool_call của stream. Chuẩn OpenAI: mảnh đầu có id+name, mảnh sau chỉ có index+arguments.
 *  Ollama gửi mỗi call đầy đủ với id riêng (index có thể lặp lại), nên call mới = id mới hoặc index mới. */
export class ToolCallAccumulator {
  private calls: ToolCall[] = [];
  private lastIndex: number | undefined;
  add(d: NonNullable<Delta["tool_calls"]>[number]): void {
    const cur = this.calls[this.calls.length - 1];
    const isNew = !cur || (d.id ? d.id !== cur.id : d.index !== undefined && d.index !== this.lastIndex);
    let target = cur;
    if (isNew) {
      target = { id: d.id || `call_${this.calls.length}`, type: "function", function: { name: "", arguments: "" } };
      this.calls.push(target);
    }
    this.lastIndex = d.index;
    target!.function.name += d.function?.name ?? "";
    target!.function.arguments += d.function?.arguments ?? "";
  }
  result(): ToolCall[] {
    return this.calls.filter((c) => c.function.name);
  }
}

/** LLM của Desk chưa bật hoặc không tới được (route /llm chưa cấu hình, upstream lỗi kết nối, gateway hết hạn).
 *  Đây là trạng thái cấu hình của máy chủ, không phải lỗi của người dùng: Desk báo rõ thay vì in lỗi HTTP thô. */
export class LlmUnavailableError extends Error {
  constructor(readonly status?: number) {
    super(LLM_UNAVAILABLE_NOTE);
    this.name = "LlmUnavailableError";
  }
}
export const LLM_UNAVAILABLE_NOTE =
  "Chưa cấu hình LLM: trợ lý trò chuyện của Desk đang tắt trên máy chủ này. Các không gian nghiệp vụ trên thanh điều hướng vẫn dùng bình thường.";
// 404: xt-api chưa đăng ký /llm (XT_DESK_LLM_URL rỗng); 502 llm_unavailable/503/504: upstream không tới được.
const UNAVAILABLE = new Set([404, 502, 503, 504]);

/** Dòng ghi chú trong khung chat cho lỗi của một lượt agent. */
export function chatFailureNote(e: unknown): string {
  if (e instanceof LlmUnavailableError) return e.message;
  return `Lỗi: ${e instanceof Error ? e.message : String(e)}`;
}

export function createLlm(o: LlmOptions): LlmClient {
  return {
    async complete({ messages, tools, signal }, onDelta): Promise<LlmReply> {
      const body: Record<string, unknown> = { model: o.model, messages: messages as ChatMessage[], stream: true, temperature: o.temperature ?? 0 };
      if (tools.length) body.tools = tools as OpenAITool[];
      if (o.reasoningEffort) body.reasoning_effort = o.reasoningEffort;
      const url = `${o.baseUrl}/chat/completions`;
      const auth = await o.authHeaders({ method: "POST", url: new URL(url, globalThis.location?.href).href });
      let r: Response;
      try {
        r = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json", ...auth },
          body: JSON.stringify(body),
          signal,
        });
      } catch (e) {
        if (e instanceof TypeError && !signal?.aborted) throw new LlmUnavailableError(); // không tới được máy chủ
        throw e;
      }
      if (UNAVAILABLE.has(r.status)) throw new LlmUnavailableError(r.status);
      if (!r.ok) throw new Error(`LLM trả lỗi ${r.status}: ${(await r.text()).slice(0, 300)}`);

      if (!(r.headers.get("content-type") || "").includes("text/event-stream")) {
        const msg = (await r.json()).choices?.[0]?.message ?? {};
        if (msg.content) onDelta(msg.content);
        return { content: msg.content || "", tool_calls: msg.tool_calls ?? [] };
      }

      const acc = new ToolCallAccumulator();
      let content = "";
      const dec = new TextDecoder();
      let buf = "";
      const handle = (line: string) => {
        if (!line.startsWith("data:")) return;
        const data = line.slice(5).trim();
        if (!data || data === "[DONE]") return;
        const delta: Delta = JSON.parse(data).choices?.[0]?.delta ?? {};
        if (delta.content) {
          content += delta.content;
          onDelta(delta.content);
        }
        for (const tc of delta.tool_calls ?? []) acc.add(tc);
      };
      const reader = r.body!.getReader();
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let nl: number;
        while ((nl = buf.indexOf("\n")) >= 0) {
          handle(buf.slice(0, nl).trim());
          buf = buf.slice(nl + 1);
        }
      }
      if (buf.trim()) handle(buf.trim());
      return { content, tool_calls: acc.result() };
    },
  };
}
