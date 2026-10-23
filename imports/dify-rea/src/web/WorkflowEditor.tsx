// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useCallback, useMemo, useState } from 'react';
import { ReactFlow, Background, Controls, Handle, Position, addEdge, applyNodeChanges, applyEdgeChanges } from '@xyflow/react';
import type { Connection, NodeChange, EdgeChange, NodeProps, Node } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type { CompareOperator, ConditionCase, ExtractorParameter, Graph, GraphNode, Knowledge, Model, NodeKind } from '../contracts.ts';
import { compareOperators, parameterTypes, xtToolNames } from '../contracts.ts';
const names: Record<NodeKind, string> = {
  aggregate: 'Gộp biến',
  http: 'HTTP Request',
  start: 'Đầu vào',
  llm: 'Mô hình AI',
  retrieval: 'Truy xuất kiến thức',
  condition: 'Điều kiện',
  template: 'Biến đổi văn bản',
  output: 'Kết quả',
  xt_tool: 'Công cụ xT',
  question_classifier: 'Phân loại câu hỏi',
  parameter_extractor: 'Trích tham số',
  list_operator: 'Xử lý danh sách',
};
const operatorNames: Record<CompareOperator, string> = {
  contains: 'Có chứa',
  not_contains: 'Không chứa',
  starts_with: 'Bắt đầu bằng',
  ends_with: 'Kết thúc bằng',
  is: 'Là',
  is_not: 'Không là',
  empty: 'Rỗng',
  not_empty: 'Không rỗng',
  eq: '= (số)',
  ne: '≠ (số)',
  gt: '> (số)',
  lt: '< (số)',
  ge: '≥ (số)',
  le: '≤ (số)',
};
function branchHandles(type: string | undefined, data: Record<string, unknown>): string[] | undefined {
  if (type === 'condition') return Array.isArray(data.cases) ? [...(data.cases as ConditionCase[]).map((c) => c.id), 'else'] : undefined;
  if (type === 'question_classifier') return ((data.classes as { id: string }[] | undefined) ?? []).map((c) => c.id);
  return undefined;
}
function OperatorSelect({ value, onChange }: { value: CompareOperator; onChange: (v: CompareOperator) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value as CompareOperator)}>
      {compareOperators.map((op) => (
        <option key={op} value={op}>
          {operatorNames[op]}
        </option>
      ))}
    </select>
  );
}
function GraphCard({ data, type, selected }: NodeProps<Node<Record<string, unknown>>>) {
  return (
    <div className={`graph-card ${selected ? 'selected' : ''}`}>
      {type !== 'start' && <Handle type="target" position={Position.Left} />}
      <span className={'node-symbol ' + type}>
        {
          (
            {
              start: '↗',
              llm: '✦',
              retrieval: '▤',
              condition: '⑂',
              template: 'T',
              aggregate: '⋈',
              http: '↔',
              output: '✓',
              xt_tool: '⚙',
              question_classifier: '☰',
              parameter_extractor: '{}',
              list_operator: '≡',
            } as Record<
              string,
              string
            >
          )[type ?? '']
        }
      </span>
      <strong>{String(data.label)}</strong>
      <small>{names[type as NodeKind]}</small>
      {branchHandles(type, data) ? (
        branchHandles(type, data)!.map((handle, index, all) => {
          const top = `${((index + 1) * 100) / (all.length + 1)}%`;
          return (
            <span key={handle}>
              <Handle id={handle} type="source" position={Position.Right} style={{ top }} />
              <span className="branch-label" style={{ top, transform: 'translateY(-50%)' }}>
                {handle}
              </span>
            </span>
          );
        })
      ) : type === 'condition' ? (
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
  const cases = node?.data.cases ?? [];
  const setCase = (index: number, value: Partial<ConditionCase>) =>
    patch({ cases: cases.map((c, i) => (i === index ? { ...c, ...value } : c)) });
  const classes = node?.data.classes ?? [];
  const params = node?.data.parameters ?? [];
  const setParam = (index: number, value: Partial<ExtractorParameter>) =>
    patch({ parameters: params.map((p, i) => (i === index ? { ...p, ...value } : p)) });
  const modelSelect = node && (
    <label>
      Mô hình
      <select value={node.data.model_id ?? ''} onChange={(e) => patch({ model_id: e.target.value })}>
        <option value="">Dùng mô hình của ứng dụng</option>
        {models
          .filter((m) => m.kind === 'chat')
          .map((m) => (
            <option value={m.id} key={m.id}>
              {m.name}
            </option>
          ))}
      </select>
    </label>
  );
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
            ...(type === 'xt_tool' ? { tool: 'search_records', args: '{}' } : {}),
            ...(type === 'question_classifier'
              ? {
                  query: '{{input}}',
                  classes: [
                    { id: 'c1', name: 'Nhóm 1' },
                    { id: 'c2', name: 'Nhóm 2' },
                  ],
                }
              : {}),
            ...(type === 'parameter_extractor'
              ? { query: '{{input}}', parameters: [{ name: 'ten', type: 'string' as const, description: '', required: true }] }
              : {}),
            ...(type === 'list_operator' ? { variable: '{{input}}' } : {}),
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
        {(
          [
            'llm',
            'retrieval',
            'condition',
            'question_classifier',
            'parameter_extractor',
            'template',
            'aggregate',
            'list_operator',
            'http',
            'xt_tool',
            'output',
          ] as NodeKind[]
        ).map((type) => (
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
                Mô hình
                <select value={node.data.model_id ?? ''} onChange={(e) => patch({ model_id: e.target.value })}>
                  <option value="">Dùng mô hình của ứng dụng</option>
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
              <label className="check">
                <input
                  type="checkbox"
                  checked={node.data.cases !== undefined}
                  onChange={(e) =>
                    patch({
                      cases: e.target.checked
                        ? [
                            {
                              id: 'case1',
                              logical_operator: 'and',
                              conditions: [{ variable: '{{input}}', operator: 'contains', value: '' }],
                            },
                          ]
                        : undefined,
                    })
                  }
                />
                Nhiều nhánh (if / else)
              </label>
            )}
            {node.type === 'condition' && node.data.cases !== undefined && (
              <>
                {cases.map((c, index) => (
                  <fieldset key={index}>
                    <legend>Nhánh {index + 1}</legend>
                    <label>
                      ID nhánh
                      <input value={c.id} onChange={(e) => setCase(index, { id: e.target.value })} />
                    </label>
                    <label>
                      Kết hợp
                      <select
                        value={c.logical_operator}
                        onChange={(e) => setCase(index, { logical_operator: e.target.value as 'and' | 'or' })}
                      >
                        <option value="and">Tất cả (và)</option>
                        <option value="or">Một trong (hoặc)</option>
                      </select>
                    </label>
                    {c.conditions.map((x, j) => {
                      const setItem = (value: Partial<typeof x>) =>
                        setCase(index, { conditions: c.conditions.map((y, k) => (k === j ? { ...y, ...value } : y)) });
                      return (
                        <div key={j} className="condition-row">
                          <input value={x.variable} placeholder="{{input}}" onChange={(e) => setItem({ variable: e.target.value })} />
                          <OperatorSelect value={x.operator} onChange={(operator) => setItem({ operator })} />
                          {!['empty', 'not_empty'].includes(x.operator) && (
                            <input value={x.value ?? ''} placeholder="Giá trị" onChange={(e) => setItem({ value: e.target.value })} />
                          )}
                          <button onClick={() => setCase(index, { conditions: c.conditions.filter((_, k) => k !== j) })}>Bỏ</button>
                        </div>
                      );
                    })}
                    <button
                      onClick={() =>
                        setCase(index, { conditions: [...c.conditions, { variable: '{{input}}', operator: 'contains', value: '' }] })
                      }
                    >
                      + Điều kiện
                    </button>
                    <button onClick={() => patch({ cases: cases.filter((_, i) => i !== index) })}>Xóa nhánh</button>
                  </fieldset>
                ))}
                <button
                  onClick={() =>
                    patch({
                      cases: [
                        ...cases,
                        {
                          id: `case${cases.length + 1}`,
                          logical_operator: 'and',
                          conditions: [{ variable: '{{input}}', operator: 'contains', value: '' }],
                        },
                      ],
                    })
                  }
                >
                  + Nhánh
                </button>
                <p className="hint">Không nhánh nào khớp thì đi nhánh else.</p>
              </>
            )}
            {node.type === 'xt_tool' && (
              <>
                <label>
                  Công cụ
                  <select value={node.data.tool ?? ''} onChange={(e) => patch({ tool: e.target.value })}>
                    {xtToolNames.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
                <label>
                  Tham số (JSON, biến trong chuỗi)
                  <textarea
                    rows={6}
                    value={node.data.args ?? '{}'}
                    placeholder={'{"query": "{{input}}"}'}
                    onChange={(e) => patch({ args: e.target.value })}
                  />
                </label>
              </>
            )}
            {(node.type === 'question_classifier' || node.type === 'parameter_extractor') && (
              <>
                <label>
                  Câu hỏi / văn bản
                  <input value={node.data.query ?? ''} onChange={(e) => patch({ query: e.target.value })} />
                </label>
                {modelSelect}
                <label>
                  Hướng dẫn thêm
                  <textarea rows={3} value={node.data.instruction ?? ''} onChange={(e) => patch({ instruction: e.target.value })} />
                </label>
              </>
            )}
            {node.type === 'question_classifier' && (
              <>
                {classes.map((c, index) => (
                  <div key={index} className="condition-row">
                    <input
                      value={c.id}
                      placeholder="ID"
                      onChange={(e) => patch({ classes: classes.map((x, i) => (i === index ? { ...x, id: e.target.value } : x)) })}
                    />
                    <input
                      value={c.name}
                      placeholder="Tên nhóm"
                      onChange={(e) => patch({ classes: classes.map((x, i) => (i === index ? { ...x, name: e.target.value } : x)) })}
                    />
                    <button onClick={() => patch({ classes: classes.filter((_, i) => i !== index) })}>Bỏ</button>
                  </div>
                ))}
                <button
                  onClick={() =>
                    patch({ classes: [...classes, { id: `c${classes.length + 1}`, name: `Nhóm ${classes.length + 1}` }] })
                  }
                >
                  + Nhóm
                </button>
              </>
            )}
            {node.type === 'parameter_extractor' && (
              <>
                {params.map((p, index) => (
                  <fieldset key={index}>
                    <legend>Tham số {index + 1}</legend>
                    <div className="condition-row">
                      <input value={p.name} placeholder="Tên" onChange={(e) => setParam(index, { name: e.target.value })} />
                      <select
                        value={p.type}
                        onChange={(e) => setParam(index, { type: e.target.value as ExtractorParameter['type'] })}
                      >
                        {parameterTypes.map((t) => (
                          <option key={t}>{t}</option>
                        ))}
                      </select>
                    </div>
                    <input
                      value={p.description}
                      placeholder="Mô tả"
                      onChange={(e) => setParam(index, { description: e.target.value })}
                    />
                    {p.type === 'select' && (
                      <input
                        value={(p.options ?? []).join(', ')}
                        placeholder="Lựa chọn, cách nhau bởi dấu phẩy"
                        onChange={(e) =>
                          setParam(index, {
                            options: e.target.value
                              .split(',')
                              .map((x) => x.trim())
                              .filter(Boolean),
                          })
                        }
                      />
                    )}
                    <label className="check">
                      <input type="checkbox" checked={p.required} onChange={(e) => setParam(index, { required: e.target.checked })} />
                      Bắt buộc
                    </label>
                    <button onClick={() => patch({ parameters: params.filter((_, i) => i !== index) })}>Bỏ</button>
                  </fieldset>
                ))}
                <button
                  onClick={() =>
                    patch({
                      parameters: [...params, { name: `thamso${params.length + 1}`, type: 'string', description: '', required: false }],
                    })
                  }
                >
                  + Tham số
                </button>
                <p className="hint">Dùng {'{{node_id.ten_tham_so}}'} ở bước sau.</p>
              </>
            )}
            {node.type === 'list_operator' && (
              <>
                <label>
                  Biến danh sách (mảng JSON)
                  <input value={node.data.variable ?? ''} onChange={(e) => patch({ variable: e.target.value })} />
                </label>
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!node.data.filter}
                    onChange={(e) => patch({ filter: e.target.checked ? { operator: 'contains', value: '' } : undefined })}
                  />
                  Lọc
                </label>
                {node.data.filter && (
                  <div className="condition-row">
                    <input
                      value={node.data.filter.key ?? ''}
                      placeholder="Khóa (bỏ trống = cả phần tử)"
                      onChange={(e) => patch({ filter: { ...node.data.filter!, key: e.target.value || undefined } })}
                    />
                    <OperatorSelect
                      value={node.data.filter.operator}
                      onChange={(operator) => patch({ filter: { ...node.data.filter!, operator } })}
                    />
                    <input
                      value={node.data.filter.value ?? ''}
                      placeholder="Giá trị"
                      onChange={(e) => patch({ filter: { ...node.data.filter!, value: e.target.value } })}
                    />
                  </div>
                )}
                <label className="check">
                  <input
                    type="checkbox"
                    checked={!!node.data.order}
                    onChange={(e) => patch({ order: e.target.checked ? { dir: 'asc' } : undefined })}
                  />
                  Sắp xếp
                </label>
                {node.data.order && (
                  <div className="condition-row">
                    <input
                      value={node.data.order.key ?? ''}
                      placeholder="Khóa (bỏ trống = cả phần tử)"
                      onChange={(e) => patch({ order: { ...node.data.order!, key: e.target.value || undefined } })}
                    />
                    <select
                      value={node.data.order.dir}
                      onChange={(e) => patch({ order: { ...node.data.order!, dir: e.target.value as 'asc' | 'desc' } })}
                    >
                      <option value="asc">Tăng dần</option>
                      <option value="desc">Giảm dần</option>
                    </select>
                  </div>
                )}
                <label>
                  Giới hạn số phần tử
                  <input
                    type="number"
                    min={1}
                    max={1000}
                    value={node.data.limit ?? ''}
                    onChange={(e) => patch({ limit: e.target.value ? Number(e.target.value) : undefined })}
                  />
                </label>
                <label>
                  Lấy một phần tử
                  <input
                    value={node.data.extract === undefined ? '' : String(node.data.extract)}
                    placeholder="first, last hoặc số thứ tự (từ 1)"
                    onChange={(e) => {
                      const v = e.target.value.trim();
                      patch({ extract: !v ? undefined : v === 'first' || v === 'last' ? v : Number(v) || undefined });
                    }}
                  />
                </label>
              </>
            )}
            {node.type === 'condition' && node.data.cases === undefined && (
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
                    <option value="stop">Dừng quy trình</option>
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
              Tham chiếu đầu vào bằng {'{{input}}'}, kết quả bước khác bằng {'{{node_id}}'}, trường JSON bằng {'{{node_id.truong}}'}. Kéo từ chấm bên phải sang chấm bên trái để nối
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
