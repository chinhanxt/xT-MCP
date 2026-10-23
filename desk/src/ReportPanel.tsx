// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { actionLabel, fieldLabel, groupDigits, recordTitle } from './portal-labels';
import { modelOf, useEnumLabels } from './enum-labels';
import { ChartColumn } from 'lucide-react';
import { BUCKETS, COUNT, reportOptions, type Bucket, type ReportOptions, type ReportPreset } from './report-presets';

type Data = Record<string, unknown>;
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
type Sel = { metric: string; group: string; bucket: '' | Bucket; dateField: string };
const EMPTY: ReportOptions = { metrics: [COUNT], groups: [], dates: [], presets: [] };

type Props = { mcp: McpConnection | null; tools: McpTool[]; models: Data[] | null };

/** D: báo cáo cơ bản từ aggregate_records. Số liệu luôn là kết quả server; lỗi thì không hiện số cũ. */
export function ReportPanel({ mcp, tools, models }: Props) {
  const list = models || [];
  const [modelId, setModelId] = useState(''), [opts, setOpts] = useState<ReportOptions>(EMPTY);
  const [metric, setMetric] = useState('count'), [group, setGroup] = useState(''), [bucket, setBucket] = useState<'' | Bucket>(''), [dateField, setDateField] = useState('');
  const [report, setReport] = useState<Data | null>(null), [explain, setExplain] = useState<Data | null>(null);
  const [source, setSource] = useState<Data | null>(null), [history, setHistory] = useState<Data[] | null>(null);
  const [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [loading, setLoading] = useState(false);
  const seq = useRef(0), live = useRef(true), describing = useRef(0);
  const allowed = new Set(tools.map((tool) => tool.name));
  const chosen = modelId || String(list[0]?.id || '');
  const [titles, setTitles] = useState<Record<string, string>>({}), titleSeq = useRef(0);
  const enumLabel = useEnumLabels(mcp, tools, [report ? chosen : '', source ? modelOf(source) || chosen : '']);
  useEffect(() => { live.current = true; return () => { live.current = false; seq.current++; }; }, []);

  async function run(name: string, args: Data): Promise<Data | null> {
    if (!mcp || !allowed.has(name)) { setError(`Không có công cụ ${name}.`); setKind('forbidden'); return null; }
    const mine = ++seq.current;
    setLoading(true); setError(''); setKind('error');
    try {
      const value = readToolResult(await mcp.client.callTool({ name, arguments: args }) as ToolResult);
      return live.current && mine === seq.current ? value : null;
    } catch (e) {
      if (live.current && mine === seq.current) { setReport(null); setExplain(null); setSource(null); setHistory(null); setError((e as Error).message); setKind(classifyError(e)); }
      return null;
    } finally { if (live.current && mine === seq.current) setLoading(false); }
  }
  // Chỉ số, trường nhóm/ngày và gợi ý lấy từ describe_model; thiếu tool này thì chỉ còn "Số bản ghi".
  useEffect(() => {
    setReport(null); setExplain(null); setSource(null); setHistory(null); setError(''); setMetric('count'); setGroup(''); setBucket(''); setDateField('');
    setOpts(EMPTY);
    if (!chosen || !mcp || !allowed.has('describe_model')) return;
    const mine = ++describing.current;
    void mcp.client.callTool({ name: 'describe_model', arguments: { model_id: chosen } }).then((result) => {
      if (!live.current || mine !== describing.current) return;
      setOpts(reportOptions(obj(readToolResult(result as ToolResult)).model));
    }).catch(() => { /* chỉ mất tùy chọn nâng cao; báo cáo đếm vẫn dùng được */ });
  }, [chosen, mcp]); // eslint-disable-line

  async function show(sel: Sel = { metric, group, bucket, dateField }) {
    setReport(null); setExplain(null); setSource(null); setHistory(null);
    const pick = opts.metrics.find((c) => c.value === sel.metric) || opts.metrics[0], by = opts.groups.find((g) => g.value === sel.group);
    const value = await run('aggregate_records', { model_id: chosen, ...pick.arg, ...(by?.arg || {}), ...(sel.bucket ? { time_bucket: sel.bucket, ...(sel.dateField ? { date_field: sel.dateField } : {}) } : {}) });
    if (value) setReport(value);
  }
  function preset(p: ReportPreset) {
    const sel: Sel = { metric: p.metric, group: p.group, bucket: p.bucket, dateField: p.dateField };
    setMetric(sel.metric); setGroup(sel.group); setBucket(sel.bucket); setDateField(sel.dateField);
    void show(sel);
  }
  async function why() {
    const ref = String(report?.lineage_ref || '');
    const value = ref ? await run('get_history', { ref, view: 'explain' }) : null;
    if (!value) return;
    const found = obj(value.explanation);
    setExplain(found); setTitles({});
    // Tên người đọc được của từng bản ghi nguồn để nút không phải hiện mã kỹ thuật.
    const mine = ++titleSeq.current, sources = (Array.isArray(found.record_refs) ? found.record_refs as unknown[] : []).slice(0, 20).map(String);
    if (!mcp || !allowed.has('get_record')) return;
    for (const ref of sources) {
      try {
        const record = obj(obj(readToolResult(await mcp.client.callTool({ name: 'get_record', arguments: { ref } }) as ToolResult)).record);
        const title = recordTitle(record);
        if (!live.current || mine !== titleSeq.current) return;
        if (title) setTitles((previous) => ({ ...previous, [ref]: title }));
      } catch { /* giữ tên dự phòng */ }
    }
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
  const keyLabel = (c: string) => opts.groups.find((g) => g.value === c)?.label ?? fieldLabel(c);
  const metricLabel = (c: string) => opts.metrics.find((m) => (Array.isArray(m.arg.metrics) && m.arg.metrics[0] === c) || m.arg.metric === c)?.label ?? c;
  const num = (value: unknown) => (typeof value === 'number' || typeof value === 'string') && value !== '' ? groupDigits(String(value)) : cell(value);
  const refs = Array.isArray(explain?.record_refs) ? explain!.record_refs as unknown[] : [];
  const modelName = (ref: string) => { const id = ref.split(':')[0], row = list.find((m) => String(m.id) === id); return String(row?.label || row?.name || 'Bản ghi'); };
  const refTitle = (ref: string, index: number) => titles[ref] ?? `${modelName(ref)} ${index + 1}`;
  const keyCell = (c: string, v: unknown) => typeof v === 'string' && v ? enumLabel(chosen, c, v) : cell(v);
  return <section className="portal-report panel" aria-label="Báo cáo">
    <h3>Báo cáo</h3>
    {opts.presets.length > 0 && models !== null && list.length > 0 && <div className="portal-actions seg" role="group" aria-label="Gợi ý báo cáo">{opts.presets.map((p) => <button key={p.id} type="button" disabled={!mcp || loading} onClick={() => preset(p)}>{p.label}</button>)}</div>}
    {models === null ? <p role="status">Đang tải danh mục…</p> : !list.length ? <p role="status">Chưa có dữ liệu để lập báo cáo.</p> : <form onSubmit={(e) => { e.preventDefault(); void show(); }}>
      <label>Báo cáo cho <select aria-label="Đối tượng báo cáo" value={chosen} onChange={(e) => setModelId(e.target.value)}>{list.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.label || row.name || row.id)}</option>)}</select></label>
      <label>Chỉ số <select aria-label="Chỉ số" value={metric} onChange={(e) => setMetric(e.target.value)}>{opts.metrics.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></label>
      {opts.groups.length > 0 && <label>Nhóm theo <select aria-label="Nhóm theo" value={group} onChange={(e) => setGroup(e.target.value)}><option value="">Không nhóm</option>{opts.groups.map((g) => <option key={g.value} value={g.value}>{g.label}</option>)}</select></label>}
      <label>Theo thời gian <select aria-label="Theo thời gian" value={bucket} onChange={(e) => setBucket(e.target.value as '' | Bucket)}><option value="">Không chia kỳ</option>{BUCKETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      {bucket && <label>Trường ngày <select aria-label="Trường ngày" value={dateField} onChange={(e) => setDateField(e.target.value)}><option value="">Ngày tạo</option>{opts.dates.map((d) => <option key={d.id} value={d.id}>{d.label}</option>)}</select></label>}
      <button className="pri" disabled={!mcp || loading}>Xem báo cáo<ChartColumn aria-hidden="true" /></button>
    </form>}
    {loading && <p role="status">Đang tải…</p>}
    <PortalAlert text={error} kind={kind}/>
    {report && <div className="report-result">
      {typeof report.summary === 'string' && <p>{report.summary}</p>}
      <p><small>Số liệu tại {when(report.as_of)} · chỉ gồm bản ghi bạn được xem{typeof report.model_revision === 'number' ? ` · phiên bản mô hình ${report.model_revision}` : ''}</small></p>
      {rows.length === 0 ? <p role="status">Không có số liệu trong phạm vi của bạn.</p> : <table><caption className="sr-only">Kết quả báo cáo</caption>
        <thead><tr>{keyCols.map((c) => <th key={c} scope="col">{keyLabel(c)}</th>)}{valueCols.map((c) => <th key={c} scope="col" className="num">{metricLabel(c)}</th>)}</tr></thead>
        <tbody>{rows.map((row, i) => <tr key={i}>{keyCols.map((c) => <td key={c}>{keyCell(c, obj(row.keys)[c])}</td>)}{valueCols.map((c) => <td key={c} className="num">{num(obj(row.values)[c])}</td>)}</tr>)}</tbody>
        {Object.keys(obj(report.totals)).length > 0 && <tfoot><tr>{keyCols.map((c, i) => <th key={c} scope="row">{i === 0 ? 'Tổng' : ''}</th>)}{valueCols.map((c) => <td key={c} className="num">{num(obj(report.totals)[c])}</td>)}</tr></tfoot>}
      </table>}
      {typeof report.lineage_ref === 'string' && <button disabled={loading} onClick={() => void why()}>Xem nguồn</button>}
    </div>}
    {explain && <article className="portal-entry" aria-label="Nguồn số liệu"><strong>{cell(explain.outcome)}</strong>
      {Array.isArray(explain.reasons) && (explain.reasons as unknown[]).map((r, i) => <p key={i}>{cell(obj(r).message)}</p>)}
      {obj(explain.query).replay === 'current_data' && <p role="note">Số liệu tại {when(report?.as_of)}; phần nguồn được đối chiếu theo dữ liệu hiện tại và quyền hiện tại của bạn, không phải ảnh chụp lịch sử tại thời điểm đó.</p>}
      {explain.query !== undefined && <details><summary>Chi tiết truy vấn</summary><pre>{JSON.stringify(explain.query, null, 2)}</pre></details>}
      {refs.length === 0 ? <p role="status">Máy chủ không trả về bản ghi nguồn.</p> : <ul className="portal-doclist">{refs.slice(0, 20).map((ref, i) => <li key={String(ref)}><span>{refTitle(String(ref), i)}</span><button disabled={loading} title={String(ref)} aria-describedby={`src-ref-${i}`} onClick={() => void open(String(ref))}>{`Mở bản ghi ${refTitle(String(ref), i)}`}</button><span id={`src-ref-${i}`} className="sr-only">{`Mã ${String(ref)}`}</span></li>)}</ul>}
      {refs.length > 20 && <p>Hiển thị 20/{refs.length} bản ghi nguồn.</p>}
    </article>}
    {source && <article className="portal-entry" aria-label="Bản ghi nguồn"><strong>{cell(source.title || obj(source.values).name || source.ref)}</strong>
      <dl>{Object.entries(obj(source.values)).map(([k, v]) => <div key={k}><dt>{fieldLabel(k)}</dt><dd>{typeof v === 'string' ? enumLabel(modelOf(source) || chosen, k, v) : cell(v)}</dd></div>)}</dl>
      {typeof source.ref === 'string' && <button disabled={loading} onClick={() => void trail(String(source.ref))}>Lịch sử bản ghi</button>}
      {history && (history.length === 0 ? <p role="status">Chưa có lịch sử.</p> : <ol aria-label="Lịch sử">{history.map((ev, i) => <li key={i}>{when(ev.at)} · {cell(obj(ev.actor).name || obj(ev.actor).ref)} · {ev.summary ? cell(ev.summary) : actionLabel(cell(ev.action))}</li>)}</ol>)}
    </article>}
  </section>;
}
