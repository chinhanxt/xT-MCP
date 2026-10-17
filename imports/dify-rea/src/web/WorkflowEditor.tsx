// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useCallback, useMemo, useState } from 'react';
import { ReactFlow, Background, Controls, Handle, Position, addEdge, applyNodeChanges, applyEdgeChanges } from '@xyflow/react';
import type { Connection, NodeChange, EdgeChange, NodeProps, Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { Graph, GraphNode, Knowledge, Model, NodeKind } from '../contracts.ts';
const names: Record<NodeKind, string> = {
  aggregate: 'Gộp biến',
  http: 'HTTP Request',
  start: 'Đầu vào',
  llm: 'Model AI',
  retrieval: 'Truy xuất kiến thức',
  condition: 'Điều kiện',
  template: 'Biến đổi văn bản',
  output: 'Kết quả',
};
function GraphCard({ data, type, selected }: NodeProps<Node<Record<string, unknown>>>) {
  return (
    <div className={`graph-card ${selected ? 'selected' : ''}`}>
      {type !== 'start' && <Handle type="target" position={Position.Left} />}
      <span className={'node-symbol ' + type}>
        {
          (
            { start: '↗', llm: '✦', retrieval: '▤', condition: '⑂', template: 'T', aggregate: '⋈', http: '↔', output: '✓' } as Record<
              string,
              string
            >
          )[type ?? '']
        }
      </span>
      <strong>{String(data.label)}</strong>
      <small>{names[type as NodeKind]}</small>
      {type === 'condition' ? (
        <>
          <Handle id="true" type="source" position={Position.Right} style={{ top: '35%' }} />
          <Handle id="false" type="source" position={Position.Right} style={{ top: '75%' }} />
          <span className="branch-label yes">true</span>
          <span className="branch-label no">false</span>
        </>
      ) : (
        type !== 'output' && <Handle type="source" position={Position.Right} />
      )}
    </div>
  );
}
const nodeTypes = Object.fromEntries(Object.keys(names).map((k) => [k, GraphCard]));
export function WorkflowEditor({
  graph,
  onChange,
  models,
  knowledge,
}: {
  graph: Graph;
  onChange: (g: Graph) => void;
  models: Model[];
  knowledge: Knowledge[];
}) {
  const [selected, select] = useState<string>('');
  const node = graph.nodes.find((n) => n.id === selected);
  const nodes = useMemo(() => graph.nodes.map((n) => ({ ...n, selected: n.id === selected })), [graph.nodes, selected]);
  const changeNodes = useCallback(
    (changes: NodeChange[]) => {
      const edits = changes.filter((change) => change.type === 'position' || change.type === 'remove');
      if (edits.length) {
        const nodes = (applyNodeChanges(edits, graph.nodes) as GraphNode[]).map(({ id, type, position, data }) => ({
          id,
          type,
          position,
          data,
        }));
        onChange({ ...graph, nodes });
      }
    },
    [graph, onChange],
  );
  const changeEdges = useCallback(
    (changes: EdgeChange[]) => {
      const edits = changes.filter((change) => change.type === 'remove');
      if (edits.length) onChange({ ...graph, edges: applyEdgeChanges(edits, graph.edges) });
    },
    [graph, onChange],
  );
  const connect = useCallback(
    (connection: Connection) =>
      onChange({
        ...graph,
        edges: addEdge(connection, graph.edges).map(({ id, source, target, sourceHandle }) => ({
          id,
          source,
          target,
          sourceHandle: sourceHandle ?? null,
        })),
      }),
    [graph, onChange],
  );
  const patch = (data: Partial<GraphNode['data']>) =>
    onChange({ ...graph, nodes: graph.nodes.map((n) => (n.id === selected ? { ...n, data: { ...n.data, ...data } } : n)) });
  function add(type: NodeKind) {
    const id = `${type}_${crypto.randomUUID().slice(0, 8)}`;
    onChange({
      ...graph,
      nodes: [
        ...graph.nodes,
        {
          id,
          type,
          position: { x: 220 + graph.nodes.length * 40, y: 80 + (graph.nodes.length % 3) * 120 },
          data: {
            label: names[type],
            ...(type === 'llm' || type === 'retrieval' ? { prompt: '{{input}}' } : {}),
            ...(type === 'output' || type === 'template' ? { template: '{{input}}' } : {}),
          },
        },
      ],
    });
    select(id);
  }
  return (
    <div className="workflow-editor">
      <div className="graph-toolbar">
        <span>Thêm bước</span>
        {(['llm', 'retrieval', 'condition', 'template', 'aggregate', 'http', 'output'] as NodeKind[]).map((type) => (
          <button key={type} onClick={() => add(type)}>
            + {names[type]}
          </button>
        ))}
      </div>
      <div className="graph-body">
        <div className="graph-canvas">
          <ReactFlow
            nodes={nodes}
            edges={graph.edges}
            nodeTypes={nodeTypes}
            onNodesChange={changeNodes}
            onEdgesChange={changeEdges}
            onConnect={connect}
            onEdgeDoubleClick={(_, edge) => onChange({ ...graph, edges: graph.edges.filter((e) => e.id !== edge.id) })}
            onNodeClick={(_, n) => select(n.id)}
            onPaneClick={() => select('')}
            fitView
            minZoom={0.2}
            deleteKeyCode={['Backspace', 'Delete']}
          >
            <Background gap={22} size={1} />
            <Controls />
          </ReactFlow>
        </div>
        {node && (
          <aside className="node-inspector">
            <div className="row spread">
              <strong>Cấu hình bước</strong>
              <button className="icon-btn" onClick={() => select('')} aria-label="Đóng cấu hình">
                ×
              </button>
            </div>
            <code>{node.id}</code>
            <label>
              Tên hiển thị
              <input value={node.data.label} onChange={(e) => patch({ label: e.target.value })} />
            </label>
            {(node.type === 'llm' || node.type === 'retrieval') && (
              <label>
                Prompt / truy vấn
                <textarea rows={6} value={node.data.prompt ?? ''} onChange={(e) => patch({ prompt: e.target.value })} />
              </label>
            )}
            {node.type === 'llm' && (
              <label>
                Model
                <select value={node.data.model_id ?? ''} onChange={(e) => patch({ model_id: e.target.value })}>
                  <option value="">Dùng model của ứng dụng</option>
                  {models
                    .filter((m) => m.kind === 'chat')
                    .map((m) => (
                      <option value={m.id} key={m.id}>
                        {m.name}
                      </option>
                    ))}
                </select>
              </label>
            )}
            {node.type === 'retrieval' && (
              <label>
                Kho kiến thức
                <select value={node.data.knowledge_id ?? ''} onChange={(e) => patch({ knowledge_id: e.target.value })}>
                  <option value="">Chọn kho</option>
                  {knowledge.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {node.type === 'condition' && (
              <>
                <label>
                  Giá trị kiểm tra
                  <input value={node.data.variable ?? '{{input}}'} onChange={(e) => patch({ variable: e.target.value })} />
                </label>
                <label>
                  Phép so sánh
                  <select
                    value={node.data.operator ?? 'contains'}
                    onChange={(e) => patch({ operator: e.target.value as 'contains' | 'equals' })}
                  >
                    <option value="contains">Có chứa</option>
                    <option value="equals">Bằng chính xác</option>
                  </select>
                </label>
                <label>
                  Giá trị
                  <input value={node.data.value ?? ''} onChange={(e) => patch({ value: e.target.value })} />
                </label>
              </>
            )}
            {(node.type === 'output' || node.type === 'template') && (
              <label>
                Mẫu văn bản
                <textarea rows={7} value={node.data.template ?? ''} onChange={(e) => patch({ template: e.target.value })} />
              </label>
            )}
            {node.type === 'aggregate' && (
              <label>
                Node ID cần gộp, mỗi dòng một biến
                <textarea
                  rows={5}
                  value={(node.data.candidates ?? []).join('\n')}
                  onChange={(e) => patch({ candidates: e.target.value.split('\n').filter(Boolean) })}
                />
              </label>
            )}
            {node.type === 'http' && (
              <>
                <label>
                  URL (origin cố định)
                  <input
                    value={node.data.url ?? ''}
                    placeholder="https://example.com/api?q={{input}}"
                    onChange={(e) => patch({ url: e.target.value })}
                  />
                </label>
                <label>
                  Method
                  <select value={node.data.method ?? 'GET'} onChange={(e) => patch({ method: e.target.value as 'GET' | 'POST' })}>
                    <option>GET</option>
                    <option>POST</option>
                  </select>
                </label>
                {node.data.method === 'POST' && (
                  <label>
                    JSON body
                    <textarea rows={5} value={node.data.body ?? ''} onChange={(e) => patch({ body: e.target.value })} />
                  </label>
                )}
              </>
            )}
            {['llm', 'retrieval', 'http'].includes(node.type) && (
              <>
                <label>
                  Số lần retry
                  <input
                    type="number"
                    min={0}
                    max={3}
                    value={node.data.retries ?? 0}
                    onChange={(e) => patch({ retries: Number(e.target.value) })}
                  />
                </label>
                <label>
                  Khi vẫn lỗi
                  <select
                    value={node.data.error_mode ?? 'stop'}
                    onChange={(e) => patch({ error_mode: e.target.value as 'stop' | 'fallback' })}
                  >
                    <option value="stop">Dừng workflow</option>
                    <option value="fallback">Dùng giá trị thay thế</option>
                  </select>
                </label>
                {node.data.error_mode === 'fallback' && (
                  <label>
                    Giá trị thay thế
                    <textarea rows={3} value={node.data.fallback ?? ''} onChange={(e) => patch({ fallback: e.target.value })} />
                  </label>
                )}
              </>
            )}
            <p className="hint">
              Tham chiếu đầu vào bằng {'{{input}}'}, kết quả bước khác bằng {'{{node_id}}'}. Kéo từ chấm bên phải sang chấm bên trái để nối
              bước.
            </p>
            {node.type !== 'start' && (
              <button
                className="danger"
                onClick={() => {
                  onChange({
                    nodes: graph.nodes.filter((n) => n.id !== node.id),
                    edges: graph.edges.filter((e) => e.source !== node.id && e.target !== node.id),
                  });
                  select('');
                }}
              >
                Xóa bước
              </button>
            )}
          </aside>
        )}
      </div>
    </div>
  );
}
