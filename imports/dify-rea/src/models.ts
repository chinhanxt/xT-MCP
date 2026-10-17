// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ChatMessage, GenerationOptions, Model, ToolCall } from './contracts.ts';
import { defaultGeneration } from './contracts.ts';
import type { Store } from './store.ts';
import { HttpError } from './validation.ts';
import { wireRequest } from './provider-wire.ts';
import { readLines, sseFrames } from './streams.ts';
export interface FunctionTool {
  type: 'function';
  function: { name: string; description: string; parameters: Record<string, unknown> };
}
async function request(model: Model & { api_key: string }, path: string, payload: unknown, signal?: AbortSignal): Promise<Response> {
  try {
    const response = await fetch(model.base_url + path, {
      method: 'POST',
      redirect: 'error',
      headers: {
        'Content-Type': 'application/json',
        ...(model.provider === 'anthropic'
          ? { 'x-api-key': model.api_key, 'anthropic-version': '2023-06-01' }
          : model.api_key
            ? { Authorization: `Bearer ${model.api_key}` }
            : {}),
      },
      body: JSON.stringify(payload),
      signal: signal ? AbortSignal.any([signal, AbortSignal.timeout(120000)]) : AbortSignal.timeout(120000),
    });
    if (!response.ok) {
      await response.body?.cancel();
      throw new HttpError(502, `Model trả về HTTP ${response.status}. Kiểm tra URL, tên model và API key.`);
    }
    if (!response.body) throw new HttpError(502, 'Model trả về nội dung trống.');
    return response;
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(signal?.aborted ? 499 : 502, signal?.aborted ? 'Đã dừng yêu cầu.' : 'Không gọi được model hoặc hết thời gian chờ.');
  }
}
function json(value: string): Record<string, any> {
  try {
    const parsed: unknown = JSON.parse(value);
    if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) return parsed as Record<string, any>;
  } catch {}
  throw new HttpError(502, 'Model trả về JSON không hợp lệ.');
}
async function readJson(response: Response) {
  let content = '';
  try {
    for await (const line of readLines(response.body!, 4 * 1024 * 1024, 4 * 1024 * 1024)) content += line;
    return json(content);
  } catch (error) {
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, 'Không đọc được phản hồi model.');
  }
}
function checkJsonMode(content: string, options: GenerationOptions) {
  if (options.json_mode) {
    try {
      JSON.parse(content);
    } catch {
      throw new HttpError(502, 'Model chưa trả đúng JSON như cấu hình.');
    }
  }
}
export async function generate(
  store: Store,
  workspace: string,
  modelId: string,
  messages: ChatMessage[],
  tools: FunctionTool[] = [],
  signal?: AbortSignal,
  options = defaultGeneration(),
): Promise<ChatMessage> {
  const model = store.model(workspace, modelId);
  if (model.kind !== 'chat') throw new HttpError(400, 'Cần chọn model chat.');
  const wire = wireRequest(model, messages, tools, options, false),
    r = await readJson(await request(model, wire.path, wire.body, signal));
  let content: string,
    calls: ToolCall[] = [];
  if (model.provider === 'anthropic') {
    if (!Array.isArray(r.content)) throw new HttpError(502, 'Nội dung Anthropic không hợp lệ.');
    content = r.content
      .filter((b: any) => b?.type === 'text' && typeof b.text === 'string')
      .map((b: any) => b.text)
      .join('');
    calls = r.content
      .filter((b: any) => b?.type === 'tool_use')
      .map((b: any) => {
        if (typeof b.id !== 'string' || typeof b.name !== 'string' || !b.input || typeof b.input !== 'object')
          throw new HttpError(502, 'Anthropic tool call không hợp lệ.');
        return { id: b.id, type: 'function', function: { name: b.name, arguments: JSON.stringify(b.input) } };
      });
  } else {
    const msg = model.provider === 'ollama' ? r.message : r.choices?.[0]?.message;
    if (!msg || (typeof msg.content !== 'string' && !Array.isArray(msg.tool_calls)))
      throw new HttpError(502, 'Model trả về nội dung không hợp lệ.');
    content = msg.content ?? '';
    calls = Array.isArray(msg.tool_calls)
      ? msg.tool_calls.map((raw: any) => {
          if (!raw || typeof raw.function?.name !== 'string' || (model.provider !== 'ollama' && typeof raw.function.arguments !== 'string'))
            throw new HttpError(502, 'Tool call từ model không hợp lệ.');
          return {
            id: typeof raw.id === 'string' ? raw.id : store.id(),
            type: 'function',
            function: {
              name: raw.function.name,
              arguments: model.provider === 'ollama' ? JSON.stringify(raw.function.arguments ?? {}) : raw.function.arguments,
            },
          };
        })
      : [];
  }
  if (!calls.length) checkJsonMode(content, options);
  return { role: 'assistant', content, ...(calls.length ? { tool_calls: calls } : {}) };
}
export async function generateStream(
  store: Store,
  workspace: string,
  modelId: string,
  messages: ChatMessage[],
  delta: (text: string) => void,
  signal?: AbortSignal,
  options = defaultGeneration(),
): Promise<string> {
  const model = store.model(workspace, modelId);
  if (model.kind !== 'chat') throw new HttpError(400, 'Cần chọn model chat.');
  const wire = wireRequest(model, messages, [], options, true),
    response = await request(model, wire.path, wire.body, signal);
  let content = '',
    terminal = false;
  const push = (value: unknown) => {
    if (typeof value === 'string' && value) {
      content += value;
      delta(value);
    }
  };
  try {
    if (model.provider === 'ollama')
      for await (const line of readLines(response.body!)) {
        if (!line.trim()) continue;
        const chunk = json(line);
        if (chunk.error) throw new HttpError(502, 'Ollama báo lỗi khi sinh nội dung.');
        push(chunk.message?.content);
        if (chunk.done === true) {
          terminal = true;
          break;
        }
      }
    else
      for await (const frame of sseFrames(response.body!)) {
        if (frame.data === '[DONE]' && model.provider === 'openai') {
          terminal = true;
          break;
        }
        const chunk = json(frame.data);
        if (chunk.error || chunk.type === 'error') throw new HttpError(502, 'Provider báo lỗi trong luồng phản hồi.');
        if (model.provider === 'anthropic') {
          if (chunk.type === 'content_block_delta' && chunk.delta?.type === 'text_delta') push(chunk.delta.text);
          if (chunk.type === 'message_stop') {
            terminal = true;
            break;
          }
        } else {
          if (chunk.choices?.[0]?.delta?.tool_calls) throw new HttpError(502, 'Chat nhận tool call ngoài cấu hình.');
          push(chunk.choices?.[0]?.delta?.content);
        }
      }
    if (!terminal) throw new HttpError(502, 'Luồng model kết thúc sớm; phản hồi chưa được lưu.');
    checkJsonMode(content, options);
    return content;
  } catch (error) {
    if (signal?.aborted) throw new HttpError(499, 'Đã dừng yêu cầu.');
    if (error instanceof HttpError) throw error;
    throw new HttpError(502, 'Luồng model không hợp lệ hoặc bị gián đoạn.');
  }
}
export async function embed(store: Store, workspace: string, modelId: string, input: string[], signal?: AbortSignal): Promise<number[][]> {
  const model = store.model(workspace, modelId);
  if (model.kind !== 'embedding' || model.provider === 'anthropic') throw new HttpError(400, 'Cần model embedding được hỗ trợ.');
  const native = model.provider === 'ollama',
    r = await readJson(await request(model, native ? '/api/embed' : '/embeddings', { model: model.model, input }, signal));
  const vectors = native
    ? r.embeddings
    : Array.isArray(r.data)
      ? r.data.sort((a: any, b: any) => a?.index - b?.index).map((x: any) => x?.embedding)
      : undefined;
  if (
    !Array.isArray(vectors) ||
    vectors.length !== input.length ||
    vectors.some(
      (v) => !Array.isArray(v) || !v.length || v.length > 16384 || v.some((n: unknown) => typeof n !== 'number' || !Number.isFinite(n)),
    )
  )
    throw new HttpError(502, 'Embedding trả về không hợp lệ.');
  if (vectors.some((v) => v.length !== vectors[0].length)) throw new HttpError(502, 'Số chiều embedding không đồng nhất.');
  return vectors;
}
