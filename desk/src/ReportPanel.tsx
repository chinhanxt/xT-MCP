// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';

type Data = Record<string, unknown>;
type Choice = { value: string; label: string; arg: Data };
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
function cell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') { const data = obj(value); return String(data.name || data.label || data.ref || JSON.stringify(data)); }
  return String(value);
}
function when(value: unknown): string {
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value ?? '') : date.toLocaleString('vi-VN');
}
const NUMERIC = new Set(['integer', 'decimal', 'money']);

type Props = { mcp: McpConnection | null; tools: McpTool[]; models: Data[] | null };

/** D: báo cáo cơ bản từ aggregate_records. Số liệu luôn là kết quả server; lỗi thì không hiện số cũ. */
export function ReportPanel({ mcp, tools, models }: Props) {
  const list = models || [];
  const [modelId, setModelId] = useState(''), [choices, setChoices] = useState<Choice[]>([{ value: 'count', label: 'Số bản ghi', arg: { metrics: ['count'] } }]);
  const [groups, setGroups] = useState<Choice[]>([]), [metric, setMetric] = useState('count'), [group, setGroup] = useState('');
  const [report, setReport] = useState<Data | null>(null), [explain, setExplain] = useState<Data | null>(null);
  const [source, setSource] = useState<Data | null>(null), [history, setHistory] = useState<Data[] | null>(null);
  const [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [loading, setLoading] = useState(false);
  const seq = useRef(0), live = useRef(true), describing = useRef(0);
  const allowed = new Set(tools.map((tool) => tool.name));
  const chosen = modelId || String(list[0]?.id || '');
  useEffect(() => { live.current = true; return () => { live.current = false; seq.current++; }; }, []);

  async function run(name: string, args: Data): Promise<Data | null> {
    if (!mcp || !allowed.has(name)) { setError(`Không có công cụ ${name}.`); setKind('forbidden'); return null; }
    const mine = ++seq.current;
    setLoading(true); setError(''); setKind('error');
    try {
      const value = readToolResult(await mcp.client.callTool({ name, arguments: args }) as ToolResult);
      return live.current && mine === seq.current ? value : null;
    } catch (e) {
      if (live.current && mine === seq.current) { setError((e as Error).message); setKind(classifyError(e)); }
      return null;
    } finally { if (live.current && mine === seq.current) setLoading(false); }
  }
  // Chỉ số và trường nhóm lấy từ describe_model; thiếu tool này thì chỉ còn "Số bản ghi".
  useEffect(() => {
    setReport(null); setExplain(null); setSource(null); setHistory(null); setError(''); setMetric('count'); setGroup('');
    const base: Choice = { value: 'count', label: 'Số bản ghi', arg: { metrics: ['count'] } };
    setChoices([base]); setGroups([]);
    if (!chosen || !mcp || !allowed.has('describe_model')) return;
    const mine = ++describing.current;
    void mcp.client.callTool({ name: 'describe_model', arguments: { model_id: chosen } }).then((result) => {
      if (!live.current || mine !== describing.current) return;
      const model = obj(obj(readToolResult(result as ToolResult)).model);
      const fields = (Array.isArray(model.fields) ? model.fields : []).map(obj);
      const named = (Array.isArray(model.metrics) ? model.metrics : []).map(obj).filter((m) => typeof m.id === 'string')
        .map((m): Choice => ({ value: `m:${m.id}`, label: String(m.label || m.id), arg: { metric: m.id } }));
      const sums = fields.filter((f) => NUMERIC.has(String(f.type)) && !f.multiple).map((f): Choice => ({ value: `s:${f.id}`, label: `Tổng ${f.label || f.id}`, arg: { metrics: [`sum:${f.id}`] } }));
      setChoices([base, ...named, ...sums]);
      setGroups(fields.filter((f) => ['enum', 'ref'].includes(String(f.type)) && !f.multiple).map((f): Choice => ({ value: String(f.id), label: String(f.label || f.id), arg: { group_by: [f.id] } })));
    }).catch(() => { /* chỉ mất tùy chọn nâng cao; báo cáo đếm vẫn dùng được */ });
  }, [chosen, mcp]); // eslint-disable-line

  async function show() {
    setReport(null); setExplain(null); setSource(null); setHistory(null);
    const pick = choices.find((c) => c.value === metric) || choices[0], by = groups.find((g) => g.value === group);
    const value = await run('aggregate_records', { model_id: chosen, ...pick.arg, ...(by?.arg || {}) });
    if (value) setReport(value);
  }
  async function why() {
    const ref = String(report?.lineage_ref || '');
    const value = ref ? await run('get_history', { ref, view: 'explain' }) : null;
    if (value) setExplain(obj(value.explanation));
  }
  async function open(ref: string) {
    setHistory(null);
    const value = await run('get_record', { ref });
    if (value) setSource(obj(value.record || value));
  }
  async function trail(ref: string) {
    const value = await run('get_history', { ref });
    if (value) setHistory(Array.isArray(value.events) ? value.events as Data[] : []);
  }

  const rows = Array.isArray(report?.rows) ? report!.rows as Data[] : [];
  const keyCols = [...new Set(rows.flatMap((row) => Object.keys(obj(row.keys))))];
  const valueCols = [...new Set([...rows.flatMap((row) => Object.keys(obj(row.values))), ...Object.keys(obj(report?.totals))])];
  const refs = Array.isArray(explain?.record_refs) ? explain!.record_refs as unknown[] : [];
  return <section className="portal-report" aria-label="Báo cáo">
    <h3>Báo cáo</h3>
    {models === null ? <p role="status">Đang tải danh mục…</p> : !list.length ? <p role="status">Chưa có dữ liệu để lập báo cáo.</p> : <form onSubmit={(e) => { e.preventDefault(); void show(); }}>
      <label>Báo cáo cho <select aria-label="Đối tượng báo cáo" value={chosen} onChange={(e) => setModelId(e.target.value)}>{list.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.label || row.name || row.id)}</option>)}</select></label>
      <label>Chỉ số <select aria-label="Chỉ số" value={metric} onChange={(e) => setMetric(e.target.value)}>{choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
      {groups.length > 0 && <label>Nhóm theo <select aria-label="Nhóm theo" value={group} onChange={(e) => setGroup(e.target.value)}><option value="">Không nhóm</option>{groups.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}</select></label>}
      <button disabled={!mcp || loading}>Xem báo cáo</button>
    </form>}
    {loading && <p role="status">Đang tải…</p>}
    <PortalAlert text={error} kind={kind}/>
    {report && <div className="report-result">
      {typeof report.summary === 'string' && <p>{report.summary}</p>}
      <p><small>Số liệu tại {when(report.as_of)} · chỉ gồm bản ghi bạn được xem{typeof report.model_revision === 'number' ? ` · phiên bản mô hình ${report.model_revision}` : ''}</small></p>
      {rows.length === 0 ? <p role="status">Không có số liệu trong phạm vi của bạn.</p> : <table><caption className="sr-only">Kết quả báo cáo</caption>
        <thead><tr>{keyCols.map((c) => <th key={c} scope="col">{c}</th>)}{valueCols.map((c) => <th key={c} scope="col">{c}</th>)}</tr></thead>
        <tbody>{rows.map((row, i) => <tr key={i}>{keyCols.map((c) => <td key={c}>{cell(obj(row.keys)[c])}</td>)}{valueCols.map((c) => <td key={c}>{cell(obj(row.values)[c])}</td>)}</tr>)}</tbody>
        {Object.keys(obj(report.totals)).length > 0 && <tfoot><tr>{keyCols.map((c, i) => <th key={c} scope="row">{i === 0 ? 'Tổng' : ''}</th>)}{valueCols.map((c) => <td key={c}>{cell(obj(report.totals)[c])}</td>)}</tr></tfoot>}
      </table>}
      {typeof report.lineage_ref === 'string' && <button disabled={loading} onClick={() => void why()}>Nguồn số liệu</button>}
    </div>}
    {explain && <article className="portal-entry" aria-label="Nguồn số liệu"><strong>{cell(explain.outcome)}</strong>
      {Array.isArray(explain.reasons) && (explain.reasons as unknown[]).map((r, i) => <p key={i}>{cell(obj(r).message)}</p>)}
      {explain.query !== undefined && <pre>{JSON.stringify(explain.query, null, 2)}</pre>}
      {refs.length === 0 ? <p role="status">Máy chủ không trả về bản ghi nguồn.</p> : <ul className="portal-doclist">{refs.slice(0, 20).map((ref) => <li key={String(ref)}><span>{String(ref)}</span><button disabled={loading} onClick={() => void open(String(ref))}>{`Mở bản ghi ${String(ref)}`}</button></li>)}</ul>}
      {refs.length > 20 && <p>Hiển thị 20/{refs.length} bản ghi nguồn.</p>}
    </article>}
    {source && <article className="portal-entry" aria-label="Bản ghi nguồn"><strong>{cell(source.title || obj(source.values).name || source.ref)}</strong>
      <dl>{Object.entries(obj(source.values)).map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{cell(v)}</dd></div>)}</dl>
      {typeof source.ref === 'string' && <button disabled={loading} onClick={() => void trail(String(source.ref))}>Lịch sử bản ghi</button>}
      {history && (history.length === 0 ? <p role="status">Chưa có lịch sử.</p> : <ol aria-label="Lịch sử">{history.map((ev, i) => <li key={i}>{when(ev.at)} · {cell(obj(ev.actor).name || obj(ev.actor).ref)} · {cell(ev.summary || ev.action)}</li>)}</ol>)}
    </article>}
  </section>;
}
