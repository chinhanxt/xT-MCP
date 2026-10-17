// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { fieldLabel, hiddenFields, valueLabel } from './portal-labels';

type Data = Record<string, unknown>;
const PAGE = 20;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
function text(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.map(text).join(', ');
  if (typeof value === 'object') { const data = obj(value); return String(data.name || data.label || data.ref || JSON.stringify(data)); }
  return String(value);
}
export function title(row: Data): string {
  const values = obj(row.values);
  return String(row.title || row.name || values.title || values.name || values.code || row.ref || 'Tài liệu');
}

type Props = { mcp: McpConnection | null; tools: McpTool[]; models: Data[] | null; primitives?: readonly string[]; noun?: string };

/** H: tài liệu theo quyền. Loại tài liệu lấy từ inspect_workspace (primitive=document), không đặt cứng tên. */
export function DocumentsPanel({ mcp, tools, models, primitives = ['document'], noun = 'tài liệu' }: Props) {
  const docModels = (models || []).filter((row) => primitives.includes(String(row.primitive)));
  const Noun = noun.charAt(0).toUpperCase() + noun.slice(1);
  const [model, setModel] = useState(''), [query, setQuery] = useState('');
  const [rows, setRows] = useState<Data[] | null>(null), [cursor, setCursor] = useState<string | null>(null);
  const [detail, setDetail] = useState<Data | null>(null), [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [loading, setLoading] = useState(false);
  const seq = useRef(0), live = useRef(true), applied = useRef('');
  const allowed = new Set(tools.map((tool) => tool.name));
  const chosen = model || String(docModels[0]?.id || '');

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
  async function search(next: string | null) {
    if (!chosen) return;
    if (!next) applied.current = query.trim(); // trang sau giữ nguyên tham số của trang đầu
    const value = await run('search_records', { model_id: chosen, limit: PAGE, ...(applied.current ? { query: applied.current } : {}), ...(next ? { cursor: next } : {}) });
    if (!value) return;
    const items = Array.isArray(value.items) ? value.items as Data[] : [];
    setRows((previous) => next && previous ? [...previous, ...items] : items);
    setCursor(typeof value.next_cursor === 'string' && value.next_cursor ? value.next_cursor : null);
    if (!next) setDetail(null);
  }
  async function open(ref: string) {
    const value = await run('get_record', { ref });
    if (value) setDetail(obj(value.record || value));
  }
  useEffect(() => { if (chosen && rows === null && mcp) void search(null); }, [chosen, rows === null, mcp]); // eslint-disable-line
  useEffect(() => { live.current = true; return () => { live.current = false; seq.current++; }; }, []);

  const details = detail ? Object.entries({ ...obj(detail.values), ...Object.fromEntries(Object.entries(detail).filter(([key]) => !['values', 'ref', 'id', 'title', 'name'].includes(key))) }).filter(([key]) => !hiddenFields.has(key)) : [];
  return <section className="portal-docs" aria-label={Noun}>
    <h3>{Noun}</h3>
    {models === null ? <p role="status">{`Đang tải danh mục ${noun}…`}</p> : !docModels.length ? <p role="status">{`Doanh nghiệp chưa có loại ${noun} nào mà bạn được xem.`}</p> : <>
      <form onSubmit={(e) => { e.preventDefault(); setCursor(null); void search(null); }}>
        <label>{`Loại ${noun}`} <select aria-label={`Loại ${noun}`} value={chosen} onChange={(e) => { setModel(e.target.value); setRows(null); setCursor(null); setDetail(null); }}>
          {docModels.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.label || row.name || row.id)}</option>)}
        </select></label>
        <input aria-label={`Tìm ${noun}`} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Từ khóa" maxLength={200} />
        <button disabled={!mcp || loading}>{`Tìm ${noun}`}</button>
      </form>
      {loading && <p role="status">Đang tải…</p>}
      {rows && !rows.length && !loading && !error && <p role="status">{`Không có ${noun} phù hợp.`}</p>}
      {rows && rows.length > 0 && <ul className="portal-doclist">{rows.map((row, i) => {
        const ref = typeof row.ref === 'string' ? row.ref : '';
        return <li key={ref || i}><span>{title(row)}</span>{ref && <button onClick={() => void open(ref)}>{`Mở ${title(row)}`}</button>}</li>;
      })}</ul>}
      {cursor && <button disabled={loading} onClick={() => void search(cursor)}>Tải thêm</button>}
      {detail && <article className="portal-entry" aria-label={`Chi tiết ${noun}`}><strong>{title(detail)}</strong>
        <dl>{details.map(([key, value]) => <div key={key}><dt>{fieldLabel(key)}</dt><dd>{typeof value === 'string' ? valueLabel(key, value) : text(value)}</dd></div>)}</dl>
        <button onClick={() => setDetail(null)}>{`Đóng ${noun}`}</button></article>}
    </>}
    <PortalAlert text={error} kind={kind}/>
  </section>;
}
