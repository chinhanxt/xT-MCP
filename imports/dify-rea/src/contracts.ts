// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
export type AppKind = 'chat' | 'workflow' | 'agent';
export type NodeKind = 'start' | 'llm' | 'retrieval' | 'condition' | 'template' | 'aggregate' | 'http' | 'output';
export interface GenerationOptions {
  temperature: number;
  max_tokens: number;
  context_messages: number;
  json_mode: boolean;
}
export interface InputField {
  name: string;
  label: string;
  type: 'text' | 'number' | 'boolean' | 'select';
  required: boolean;
  options?: string[];
  default?: string | number | boolean;
}
export type ToolName = 'knowledge_search' | 'calculator';
export interface GraphNode {
  id: string;
  type: NodeKind;
  position: { x: number; y: number };
  data: {
    label: string;
    prompt?: string;
    model_id?: string;
    knowledge_id?: string;
    variable?: string;
    operator?: 'contains' | 'equals';
    value?: string;
    template?: string;
    candidates?: string[];
    url?: string;
    method?: 'GET' | 'POST';
    body?: string;
    retries?: number;
    error_mode?: 'stop' | 'fallback';
    fallback?: string;
  };
}
export interface GraphEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle?: string | null;
}
export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}
export interface McpTool {
  name: string;
  description: string;
  input_schema: Record<string, unknown>;
  schema_hash: string;
}
export interface McpConnector {
  id: string;
  name: string;
  url: string;
  has_key: boolean;
  enabled: boolean;
  tools: McpTool[];
}
export interface McpToolSelection {
  connector_id: string;
  tool_name: string;
  schema_hash: string;
}
export interface AppConfig {
  xt_tools?: string[];
  mcp_tools?: McpToolSelection[];
  generation?: GenerationOptions;
  inputs?: InputField[];
  model_id: string;
  system_prompt: string;
  knowledge_ids: string[];
  tools: ToolName[];
  max_steps: number;
  graph: Graph;
}
export interface StudioApp {
  id: string;
  name: string;
  description: string;
  kind: AppKind;
  config: AppConfig;
  published: AppConfig | null;
  version: number;
  updated_at: string;
}
export interface Model {
  id: string;
  name: string;
  provider: 'openai' | 'ollama' | 'anthropic';
  base_url: string;
  model: string;
  kind: 'chat' | 'embedding';
  has_key: boolean;
}
export interface Knowledge {
  id: string;
  name: string;
  description: string;
  embedding_model_id: string | null;
  document_count: number;
  chunk_count: number;
}
export interface Citation {
  document_id: string;
  document_name: string;
  chunk_id: string;
  content: string;
  score: number;
}
export interface Trace {
  attempts?: number;
  used_fallback?: boolean;
  node_id: string;
  label: string;
  type: string;
  status: 'success' | 'error' | 'skipped';
  input: string;
  output: string;
  duration_ms: number;
}
export interface RunResult {
  run_id?: string;
  output: string;
  citations: Citation[];
  trace: Trace[];
  conversation_id?: string;
}
export type StreamEvent =
  { type: 'delta'; text: string } | { type: 'result'; result: RunResult } | { type: 'error'; error: string; trace?: Trace[] };
export const defaultGeneration = (): GenerationOptions => ({ temperature: 0.3, max_tokens: 1024, context_messages: 30, json_mode: false });
export interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool';
  content: string | null;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  citations?: Citation[];
}
export interface ToolCall {
  id: string;
  type: 'function';
  function: { name: string; arguments: string };
}
export const emptyGraph = (): Graph => ({
  nodes: [
    { id: 'start', type: 'start', position: { x: 50, y: 160 }, data: { label: 'Đầu vào' } },
    { id: 'llm', type: 'llm', position: { x: 330, y: 160 }, data: { label: 'Model AI', prompt: '{{input}}' } },
    { id: 'output', type: 'output', position: { x: 610, y: 160 }, data: { label: 'Kết quả', template: '{{llm}}' } },
  ],
  edges: [
    { id: 'e1', source: 'start', target: 'llm', sourceHandle: null },
    { id: 'e2', source: 'llm', target: 'output', sourceHandle: null },
  ],
});
export const emptyConfig = (): AppConfig => ({
  generation: defaultGeneration(),
  inputs: [],
  model_id: '',
  system_prompt:
    'Bạn là trợ lý hữu ích. Trả lời chính xác bằng tiếng Việt. Chỉ dẫn nguồn [1], [2] khi có tài liệu tham khảo được cung cấp; không bịa nguồn.',
  knowledge_ids: [],
  tools: ['knowledge_search', 'calculator'],
  max_steps: 5,
  graph: emptyGraph(),
});
