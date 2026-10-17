// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { mkdtempSync, mkdirSync, writeFileSync, readdirSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { randomBytes, createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import type { AddressInfo } from 'node:net';
import type { StudioApp } from '../src/contracts.ts';
import { liveMcpFixture } from './live-mcp.ts';
import { readLines } from '../src/streams.ts';
const mcp = await liveMcpFixture();
const dir = mkdtempSync(join(tmpdir(), 'xt-studio-live-'));
const store = new Store(dir),
  server = createStudio(store, { log: true });
await new Promise<void>((r) => server.listen(Number(process.env.LIVE_PORT || 4318), '127.0.0.1', r));
const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
let cookie = '';
const results: Record<string, unknown> = {
  observed_at: new Date().toISOString(),
  model: 'qwen3.5:4b',
  embedding: 'bge-m3:latest',
  origin,
  data_directory: dir,
};
function sourceSnapshot() {
  const files: string[] = ['package.json', 'package-lock.json', 'tsconfig.json'];
  const visit = (directory: string) => {
    for (const item of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, item.name);
      if (item.isDirectory()) visit(path);
      else files.push(path);
    }
  };
  for (const directory of ['src', 'config', 'tests']) visit(directory);
  const hashes = files.sort().map((path) => ({ path, sha256: createHash('sha256').update(readFileSync(path)).digest('hex') }));
  return { sha256: createHash('sha256').update(JSON.stringify(hashes)).digest('hex'), files: hashes };
}
results.source = sourceSnapshot();
async function call(path: string, data?: unknown, method = data === undefined ? 'GET' : 'POST') {
  const response = await fetch(origin + '/api' + path, {
    method,
    headers: { Cookie: cookie, 'Content-Type': 'application/json' },
    body: data === undefined ? undefined : JSON.stringify(data),
  });
  const json = await response.json();
  if (response.headers.get('set-cookie')) cookie = response.headers.get('set-cookie')!.split(';')[0];
  assert.ok(response.ok, `${path}: ${JSON.stringify(json)}`);
  return json as any;
}
try {
  const password = randomBytes(24).toString('hex');
  await call('/setup', { email: 'local-qa@example.test', workspace_name: 'xT Studio · Kiểm thử', password });
  const chatModel = await call('/models', {
    name: 'Qwen local',
    provider: 'ollama',
    base_url: 'http://127.0.0.1:11434',
    model: 'qwen3.5:4b',
    kind: 'chat',
  });
  const embedding = await call('/models', {
    name: 'BGE M3 local',
    provider: 'ollama',
    base_url: 'http://127.0.0.1:11434',
    model: 'bge-m3:latest',
    kind: 'embedding',
  });
  const knowledge = await call('/knowledge', {
    name: 'Quy định công ty mô phỏng',
    description: 'Dữ liệu kiểm thử độc lập.',
    embedding_model_id: embedding.id,
  });
  results.indexing = await call(`/knowledge/${knowledge.id}/documents`, {
    name: 'quy-dinh-tam-ung.md',
    content:
      'Công ty Sao Mai quy định hạn mức tạm ứng tối đa là 7.500.000 đồng mỗi lần. Trưởng phòng duyệt trước, kế toán kiểm tra sau. Mã chính sách là SM-2026-Q4.',
  });
  const hits = await call(`/knowledge/${knowledge.id}/search`, { query: 'Hạn mức tạm ứng là bao nhiêu?' });
  assert.ok(hits.length);
  results.vector_retrieval = hits;
  const chat: StudioApp = await call('/apps', {
    name: 'Trợ lý công ty',
    description: 'Chat cùng tài liệu nội bộ và dẫn nguồn.',
    kind: 'chat',
  });
  chat.config.model_id = chatModel.id;
  chat.config.knowledge_ids = [knowledge.id];
  await call(`/apps/${chat.id}`, chat, 'PUT');
  await call(`/apps/${chat.id}/publish`, {});
  const chatResult = await call(`/apps/${chat.id}/run`, {
    input: 'Theo chính sách SM-2026-Q4, hạn mức tạm ứng tối đa là bao nhiêu? Trả lời một câu và dẫn nguồn.',
    mode: 'published',
  });
  assert.ok(chatResult.citations.length);
  assert.match(chatResult.output, /7[.,\s]?500[.,\s]?000|7[.,]5 triệu|7 triệu 500/i);
  results.chat = chatResult;
  const savedConversation = await call(`/apps/${chat.id}/conversations/${chatResult.conversation_id}`);
  assert.ok(savedConversation.messages.at(-1).citations.length);
  let streamed = '',
    deltaCount = 0;
  const response = await fetch(origin + `/api/apps/${chat.id}/run`, {
    method: 'POST',
    headers: { Cookie: cookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: 'Theo tài liệu, mã chính sách là gì? Trả lời ngắn.', mode: 'published', stream: true }),
  });
  assert.equal(response.status, 200);
  for await (const line of readLines(response.body!)) {
    if (!line) continue;
    const event = JSON.parse(line);
    assert.notEqual(event.type, 'error', event.error);
    if (event.type === 'delta') {
      streamed += event.text;
      deltaCount++;
    }
    if (event.type === 'result') assert.equal(event.result.output, streamed);
  }
  assert.match(streamed, /SM-2026-Q4/);
  assert.ok(deltaCount > 1);
  results.streaming = { output: streamed, delta_count: deltaCount };
  const share = await call(`/apps/${chat.id}/share`, {});
  const publicResponse = await fetch(origin + '/public/apps/' + share.id);
  assert.equal(publicResponse.status, 200);
  const guestCookie = publicResponse.headers.get('set-cookie')!.split(';')[0];
  const publicRun = await fetch(origin + '/public/apps/' + share.id + '/run', {
    method: 'POST',
    headers: { Cookie: guestCookie, 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: 'Hạn mức tạm ứng tối đa là bao nhiêu?' }),
  });
  assert.ok(publicRun.ok);
  const sharedResult = await publicRun.json();
  assert.match(sharedResult.output, /7[.,\s]?500[.,\s]?000|7[.,]5 triệu|7 triệu 500/i);
  assert.deepEqual(sharedResult.trace, []);
  results.share = { id: share.id, output: sharedResult.output };
  const workflow: StudioApp = await call('/apps', {
    name: 'Tóm tắt văn bản',
    description: 'Workflow ba bước với Qwen local.',
    kind: 'workflow',
  });
  workflow.config.model_id = chatModel.id;
  workflow.config.graph.nodes[1].data.prompt = 'Tóm tắt nội dung sau trong một câu ngắn: {{input}}';
  await call(`/apps/${workflow.id}`, workflow, 'PUT');
  results.workflow = await call(`/apps/${workflow.id}/run`, {
    input: 'Nhân viên gửi đề nghị tạm ứng, trưởng phòng duyệt và kế toán thực hiện chi tiền.',
  });
  assert.ok((results.workflow as any).output);
  assert.equal((results.workflow as any).trace.length, 3);
  const agent: StudioApp = await call('/apps', { name: 'Agent tính toán', description: 'Agent gọi công cụ calculator.', kind: 'agent' });
  agent.config.model_id = chatModel.id;
  agent.config.tools = ['calculator'];
  agent.config.system_prompt = 'Bạn là trợ lý tính toán. Luôn gọi công cụ calculator cho phép tính rồi trả lời kết quả ngắn gọn.';
  await call(`/apps/${agent.id}`, agent, 'PUT');
  results.agent = await call(`/apps/${agent.id}/run`, { input: 'Dùng calculator tính (125000 + 75000) * 3.' });
  assert.match((results.agent as any).output, /600[.,\s]?000/);
  assert.ok((results.agent as any).trace.some((t: any) => t.label === 'calculator' && t.status === 'success'));
  const connector = await call('/mcp', { name: 'Chính sách công ty · MCP', url: mcp.url, token: mcp.token });
  const mcpAgent: StudioApp = await call('/apps', {
    name: 'Agent MCP công ty',
    description: 'Tra chính sách từ MCP server mẫu.',
    kind: 'agent',
  });
  mcpAgent.config.model_id = chatModel.id;
  mcpAgent.config.tools = [];
  mcpAgent.config.mcp_tools = connector.tools.map((tool: any) => ({
    connector_id: connector.id,
    tool_name: tool.name,
    schema_hash: tool.schema_hash,
  }));
  mcpAgent.config.system_prompt =
    'Luôn gọi công cụ company_policy để lấy chính sách, rồi trả lời hạn mức tạm ứng bằng số đồng. Không đoán thông tin.';
  await call(`/apps/${mcpAgent.id}`, mcpAgent, 'PUT');
  await call(`/apps/${mcpAgent.id}/publish`, {});
  results.mcp_agent = await call(`/apps/${mcpAgent.id}/run`, {
    input: 'Tra chính sách công ty: hạn mức tạm ứng là bao nhiêu?',
    mode: 'published',
  });
  assert.ok((results.mcp_agent as any).trace.some((t: any) => t.label === 'company_policy' && t.status === 'success'));
  assert.ok(mcp.calls.length);
  assert.match((results.mcp_agent as any).output, /7[.,\s]?500[.,\s]?000|7[.,]5 triệu|7 triệu 500/i);
  results.mcp_calls = mcp.calls;
  const typed: StudioApp = await call('/apps', {
    name: 'Đơn tạm ứng',
    description: 'Workflow có trường số và checkbox.',
    kind: 'workflow',
  });
  typed.config.inputs = [
    { name: 'amount', label: 'Số tiền', type: 'number', required: true, default: 1000000 },
    { name: 'urgent', label: 'Khẩn cấp', type: 'boolean', required: true },
  ];
  typed.config.graph.nodes[1].type = 'template';
  typed.config.graph.nodes[1].data = { label: 'Tạo nội dung', template: 'Đề nghị: {{amount}} đồng. Khẩn cấp: {{urgent}}' };
  await call(`/apps/${typed.id}`, typed, 'PUT');
  await call(`/apps/${typed.id}/publish`, {});
  results.typed_workflow = await call(`/apps/${typed.id}/run`, {
    input: '',
    inputs: { amount: 1000000, urgent: false },
    mode: 'published',
  });
  assert.match((results.typed_workflow as any).output, /1000000.*false/);
  results.typed_share = await call(`/apps/${typed.id}/share`, {});
  const exported = await call(`/apps/${mcpAgent.id}/export`);
  assert.ok(!JSON.stringify(exported).includes(mcp.token));
  results.imported_mcp_app = (await call('/apps/import', { manifest: exported })).id;
  const runs = await call('/runs');
  assert.ok(runs.length >= 3);
  assert.deepEqual(sourceSnapshot(), results.source, 'Mã đã đổi trong lúc chạy; cần kiểm thử lại snapshot mới.');
  results.passed = true;
  const docs = resolve('docs/evidence');
  mkdirSync(docs, { recursive: true });
  writeFileSync(join(docs, 'live-smoke.json'), JSON.stringify(results, null, 2));
  writeFileSync(join(dir, 'browser-login.json'), JSON.stringify({ origin, email: 'local-qa@example.test', password, cookie }, null, 2), {
    mode: 0o600,
  });
  console.log(JSON.stringify({ passed: true, evidence: join(docs, 'live-smoke.json'), origin, data_directory: dir }));
  if (process.env.LIVE_KEEP_OPEN === '1') {
    console.log('Keeping QA server open for browser verification.');
  } else {
    await new Promise<void>((r) => server.close(() => r()));
    await mcp.close();
  }
} catch (error) {
  results.error = error instanceof Error ? error.message : 'unknown';
  mkdirSync('docs/evidence', { recursive: true });
  writeFileSync('docs/evidence/live-smoke.json', JSON.stringify(results, null, 2));
  server.close();
  await mcp.close();
  throw error;
}
if (process.env.LIVE_KEEP_OPEN !== '1') store.close();
