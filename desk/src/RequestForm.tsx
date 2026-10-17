// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import { config } from './config';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { ToolError, readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { inSentence, recordTitle, refKind, valueLabel } from './portal-labels';
import { buildValues, confirmUrlOf, fieldErrorsOf, isSupported, readFields, validate, type FieldSpec } from './portal-request';

type Data = Record<string, unknown>;
type Attempt = { sig: string; createKey: string; submitKey: string; draft?: { ref: string; revision: number } };
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
const inputType: Record<string, string> = { integer: 'number', decimal: 'text', money: 'text', date: 'date', datetime: 'datetime-local' };
function safeUrl(raw: unknown): string {
  try { const url = new URL(String(raw)); return url.origin === config.xtOrigin && url.protocol === 'https:' ? url.href : ''; } catch { return ''; }
}

type Props = { mcp: McpConnection | null; tools: McpTool[]; models: Data[] | null; onDone?: () => void; primitives?: readonly string[]; heading?: string; generic?: boolean };
const rowId = (row: Data) => String(row.id || row.ref || '').split(':').at(-1) || '';

/** P: biểu mẫu sinh từ describe_model cho model có primitive=request. create_record rồi submit_request. */
export function RequestForm({ mcp, tools, models, onDone, primitives = ['request'], heading = 'Tạo yêu cầu', generic = false }: Props) {
  const requestModels = (models || []).filter((row) => primitives.includes(String(row.primitive)));
  const [modelId, setModelId] = useState(''), [fields, setFields] = useState<FieldSpec[] | null>(null);
  const [raw, setRaw] = useState<Record<string, string>>({}), [errors, setErrors] = useState<Record<string, string>>({});
  const [message, setMessage] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [confirmUrl, setConfirmUrl] = useState(''), [done, setDone] = useState('');
  const [busy, setBusy] = useState(false), [pending, setPending] = useState(''), [refRows, setRefRows] = useState<Record<string, Data[]>>({});
  const attempt = useRef<Attempt | null>(null), working = useRef(false), live = useRef(true), seq = useRef(0);
  const allowed = new Set(tools.map((tool) => tool.name));
  const canPick = allowed.has('search_records'), supported = (f: FieldSpec) => isSupported(f, canPick);
  const chosen = modelId || String(requestModels[0]?.id || '');
  const modelLabel = String(requestModels.find((row) => String(row.id) === chosen)?.label || refKind(chosen + ':'));
  const needsSubmit = requestModels.find((row) => String(row.id) === chosen)?.primitive === 'request'; // record/document chỉ cần create_record
  useEffect(() => { live.current = true; return () => { live.current = false; seq.current++; }; }, []);

  async function call(name: string, args: Data): Promise<ToolResult> {
    if (!mcp || !allowed.has(name)) throw new ToolError(`Không có công cụ ${name}.`, 'access_denied', '');
    return await mcp.client.callTool({ name, arguments: args }) as ToolResult;
  }
  useEffect(() => {
    if (!chosen || !mcp) return;
    const mine = ++seq.current;
    setFields(null); setRefRows({}); setRaw({}); setErrors({}); setMessage(''); setDone(''); attempt.current = null; setPending('');
    call('describe_model', { model_id: chosen }).then((result) => {
      if (!live.current || mine !== seq.current) return;
      const loaded = readFields(obj(readToolResult(result)).model);
      setFields(loaded);
      for (const target of new Set(loaded.filter((f) => f.type === 'ref' && f.refModel && !f.multiple).map((f) => f.refModel!))) {
        if (!canPick) break;
        call('search_records', { model_id: target, limit: 100 }).then((found) => {
          const list = obj(readToolResult(found)).items;
          if (live.current && mine === seq.current) setRefRows((prev) => ({ ...prev, [target]: Array.isArray(list) ? list.map(obj) : [] }));
        }).catch(() => { if (live.current && mine === seq.current) setRefRows((prev) => ({ ...prev, [target]: [] })); });
      }
    }).catch((e: Error) => { if (live.current && mine === seq.current) { setFields([]); setMessage(e.message); setKind(classifyError(e)); } });
  }, [chosen, mcp]); // eslint-disable-line

  async function send() {
    if (working.current || !fields || !mcp) return;
    const found = validate(fields, raw);
    setErrors(found); setMessage(''); setKind('error'); setConfirmUrl(''); setDone('');
    if (Object.keys(found).length) return;
    const values = buildValues(fields, raw), sig = `${chosen}:${JSON.stringify(values)}`;
    if (attempt.current?.sig !== sig && !attempt.current?.draft) attempt.current = { sig, createKey: crypto.randomUUID(), submitKey: crypto.randomUUID() };
    const current = attempt.current!;
    working.current = true; setBusy(true);
    try {
      if (!current.draft) {
        const record = obj(obj(read(await call('create_record', { model_id: chosen, values, idempotency_key: current.createKey }))).record);
        const ref = String(record.ref || '');
        if (!ref) throw new Error('Máy chủ không trả về mã phiếu nháp');
        if (!needsSubmit) { attempt.current = null; if (live.current) { setDone(modelLabel); setRaw({}); setErrors({}); } onDone?.(); return; }
        let revision = record.revision;
        if (typeof revision !== 'number') revision = obj(obj(read(await call('get_record', { ref }))).record).revision;
        if (typeof revision !== 'number') throw new Error('Không đọc được phiên bản của phiếu nháp');
        current.draft = { ref, revision };
        if (live.current) setPending(ref);
      }
      read(await call('submit_request', { ref: current.draft.ref, expected_revision: current.draft.revision, idempotency_key: current.submitKey }));
      attempt.current = null;
      if (!live.current) return;
      setDone(modelLabel); setPending(''); setRaw({}); setErrors({});
      onDone?.();
    } catch (e) {
      if (!live.current) return;
      const text = (e as Error).message;
      setKind(classifyError(e));
      setMessage(current.draft ? `${text}. Phiếu nháp ${modelLabel} đã tạo nhưng chưa gửi duyệt; bấm Gửi lại để thử tiếp với cùng khóa an toàn.` : text);
    } finally { working.current = false; if (live.current) setBusy(false); }
  }
  /** Đọc kết quả; khi lỗi, giữ lỗi theo từng field và liên kết xác nhận (chỉ origin xT). */
  function read(result: ToolResult): Data {
    try { return readToolResult(result); }
    catch (e) { if (live.current) { setErrors(fieldErrorsOf(result)); setConfirmUrl(safeUrl(confirmUrlOf(result))); } throw e; }
  }

  const field = (f: FieldSpec) => {
    const id = `req-${f.id}`, err = errors[f.id], common = { id, 'aria-invalid': !!err, 'aria-describedby': err ? `${id}-err` : undefined, required: f.required, disabled: busy || !!pending };
    const set = (v: string) => setRaw((p) => ({ ...p, [f.id]: v }));
    return <div key={f.id} className="req-field">
      <label htmlFor={id}>{f.label}{f.required ? ' *' : ''}</label>
      {!supported(f) ? <p id={id} role="note">{f.required ? 'Trường bắt buộc nhưng chưa nhập được ở biểu mẫu này — hãy nhờ trợ lý Desk hoặc dùng Studio.' : 'Chưa nhập được ở biểu mẫu này; có thể bỏ qua.'}</p>
        : f.type === 'ref' ? <select {...common} value={raw[f.id] ?? ''} onChange={(e) => set(e.target.value)}><option value="">Chọn {inSentence(f.label)}</option>{(refRows[f.refModel!] ?? []).map((row) => <option key={rowId(row)} value={rowId(row)}>{recordTitle(row, 'Bản ghi')}</option>)}</select>
        : f.type === 'text' ? <textarea {...common} value={raw[f.id] ?? ''} onChange={(e) => set(e.target.value)} maxLength={f.maxLength} rows={3} />
        : f.type === 'enum' ? <select {...common} value={raw[f.id] ?? ''} onChange={(e) => set(e.target.value)}><option value="">Chọn</option>{f.enumValues.map((v) => <option key={v} value={v}>{f.enumLabels[v] ?? valueLabel(f.id, v)}</option>)}</select>
        : f.type === 'boolean' ? <input {...common} type="checkbox" checked={raw[f.id] === 'true'} onChange={(e) => set(String(e.target.checked))} />
        : <input {...common} type={inputType[f.type] || 'text'} value={raw[f.id] ?? ''} onChange={(e) => set(e.target.value)} maxLength={f.maxLength} inputMode={f.type === 'integer' ? 'numeric' : f.type === 'money' || f.type === 'decimal' ? 'decimal' : undefined} />}
      {f.description && <small>{f.description}</small>}
      {err && <small id={`${id}-err`} role="alert" className="field-error">{err}</small>}
    </div>;
  };
  const blocked = !!fields?.some((f) => f.required && !supported(f));
  // Form tạo bản ghi: tên theo chính model ("Tạo khách hàng"), không gọi là "yêu cầu".
  const title = generic && requestModels.length ? `Tạo ${inSentence(modelLabel)}` : heading, noun = generic ? 'Loại bản ghi' : 'Loại yêu cầu';
  return <section className="portal-request" aria-label={title}>
    <h3>{title}</h3>
    {models === null ? <p role="status">Đang tải danh mục quy trình…</p> : !requestModels.length ? <p role="status">Doanh nghiệp chưa có loại yêu cầu nào bạn được tạo.</p> : <form noValidate onSubmit={(e) => { e.preventDefault(); void send(); }}>
      {(!generic || requestModels.length > 1) && <div className="req-field"><label htmlFor="req-model">{noun}</label>
        <select id="req-model" value={chosen} disabled={busy || !!pending} onChange={(e) => setModelId(e.target.value)}>{requestModels.map((row) => <option key={String(row.id)} value={String(row.id)}>{String(row.label || row.name || row.id)}</option>)}</select></div>}
      {fields === null && <p role="status">Đang tải biểu mẫu…</p>}
      {fields?.length === 0 && !message && <p role="status">{noun} này chưa khai báo trường nào.</p>}
      {fields?.map(field)}
      <button disabled={!mcp || busy || !fields?.length || blocked}>{pending ? 'Gửi lại' : needsSubmit ? 'Tạo và gửi duyệt' : 'Tạo'}</button>
      {pending && <button type="button" disabled={busy} onClick={() => { attempt.current = null; setPending(''); setMessage(''); }}>Làm lại từ đầu</button>}
    </form>}
    {busy && <p role="status">Đang gửi…</p>}
    {done && <p role="status" data-testid="request-done">{needsSubmit ? 'Đã gửi duyệt' : 'Đã tạo'} {done}.</p>}
    <PortalAlert text={message} kind={kind}>{confirmUrl && <> <a href={confirmUrl} target="_blank" rel="noopener noreferrer">Mở trang xác nhận</a></>}</PortalAlert>
  </section>;
}
