// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Con người · Nhân sự AI (gói nhan-su-ai): danh sách agent, hồ sơ, tuyển, đình chỉ / cho nghỉ / mở lại.
// Chỉ dùng bộ tool cố định (search_records, get_record, create_record, update_record, submit_request, get_history, find_people).
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { config } from './config';
import { readToolResult, ToolError } from './portal-result';
import { confirmUrlOf } from './portal-request';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { Pill } from './ui';
import { AGENT_MODEL, activeJd, dateVi, INCIDENT_FIX, INCIDENT_KIND, OPS, agentErrorText, agentStatus, capChips, capabilities, errorReason, fieldsOf, flatCapabilities, jdFor, lastId, money, num, obj, permitCovers, permitLabel, refId, refName, riskFlags, wholeNumber, workHours, workload, type Data } from './agenthr';
import { ArrowLeft, Bot, Check, ChevronRight, History, Lock, Pause, Plus, RefreshCw, RotateCcw, Send, ShieldAlert, ShieldCheck, UserX, X } from 'lucide-react';

type View = { kind: 'list' } | { kind: 'detail'; ref: string } | { kind: 'hire' };
type Attempt = { signature: string; hoSo?: string; jd?: { ref: string; revision: number } };
type JdValues = { han_muc_viec_ngay: string; han_muc_gia_tri: string; khung_gio: string; ngay_lam: string; muc_tu_chu: string };
type Props = { mcp: McpConnection | null; tools: McpTool[]; models: Data[] | null; me: Data | null; onMutated?: () => void };

const rowsOf = (value: Data) => (Array.isArray(value.items) ? value.items as Data[] : []);
const title = (row: Data) => String(fieldsOf(row).ten || row.title || row.name || '').trim() || 'Agent';
function recordOf(value: Data, model: string): Data {
  const record = obj(value.record || value.request);
  if (typeof record.ref !== 'string' || !record.ref.startsWith(`${model}:`) || !lastId(record.ref)) throw new Error('Máy chủ không trả về mã bản ghi hợp lệ.');
  return record;
}
const jdFromSource = (f: Data): JdValues => ({
  han_muc_viec_ngay: num(f.han_muc_viec_ngay) === null ? '' : String(num(f.han_muc_viec_ngay)), han_muc_gia_tri: num(f.han_muc_gia_tri) === null ? '' : String(num(f.han_muc_gia_tri)),
  khung_gio: String(f.khung_gio || ''), ngay_lam: String(f.ngay_lam || ''), muc_tu_chu: num(f.muc_tu_chu) === null ? '' : String(num(f.muc_tu_chu)),
});

/** Khối cảnh báo nâng quyền của một jd_agent (Quy trình và sau khi tuyển). */
export function JdRiskBlock({ record, models }: { record: Data; models: Data[] }) {
  const f = fieldsOf(record), flags = riskFlags(f.canh_bao), caps = capabilities(f.nang_luc);
  return <section className="panel jd-risk" aria-label="Cảnh báo quyền">
    <div className="ph"><h3>Cảnh báo quyền</h3></div>
    {flags.length ? flags.map(flag => <div className="flag" key={flag.code}><ShieldAlert aria-hidden="true"/><div><b>{flag.title}</b>{flag.detail}</div></div>)
      : <p><Pill tone="ok" icon={ShieldCheck}>Không có cảnh báo quyền</Pill></p>}
    {caps.length > 0 && <CapTable caps={caps} models={models}/>}
  </section>;
}

function modelName(models: Data[], id: string) { const m = models.find(row => row.id === id); return String(m?.label || m?.name || id); }
function CapTable({ caps, models, permits }: { caps: ReturnType<typeof capabilities>; models: Data[]; permits?: Data[] }) {
  const released = (model: string) => (permits || []).some(p => permitCovers(fieldsOf(p), model));
  return <table><thead><tr><th>Dữ liệu</th>{OPS.map(op => <th key={op.id}>{op.label}</th>)}</tr></thead><tbody>{caps.map(cap => <tr key={cap.model}><td>{modelName(models, cap.model)}{cap.note ? <small className="muted"> {cap.note}</small> : null}</td>{OPS.map(op => <td key={op.id}>{
    !cap.ops.includes(op.id) ? <span className="faint" aria-label="Không"><X aria-hidden="true"/></span>
      : op.id === 'submit' && permits && !released(cap.model) ? <Pill tone="held" icon={Lock}>Giữ lại</Pill>
        : <span className="ok-ic" aria-label="Có"><Check aria-hidden="true"/></span>}</td>)}</tr>)}</tbody></table>;
}

export function AgentHrPanel({ mcp, tools, models, me, onMutated }: Props) {
  const catalog = models || [];
  const [view, setView] = useState<View>({ kind: 'list' });
  const [agents, setAgents] = useState<Data[] | null>(null), [roles, setRoles] = useState<Data[] | null>(null), [jds, setJds] = useState<Data[]>([]), [openIncidents, setOpenIncidents] = useState<Data[] | null>(null), [people, setPeople] = useState<Data[]>([]);
  const [detail, setDetail] = useState<{ record: Data; jds: Data[]; permits: Data[] | null; incidents: Data[] | null } | null>(null), [history, setHistory] = useState<Data[] | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [done, setDone] = useState(''), [confirmUrl, setConfirmUrl] = useState('');
  const [dialog, setDialog] = useState<'' | 'suspend' | 'retire'>(''), [reason, setReason] = useState('');
  const [role, setRole] = useState(''), [hire, setHire] = useState<{ ten: string; client_id: string; owner: string }>({ ten: '', client_id: '', owner: '' }), [jd, setJd] = useState<JdValues>(jdFromSource({}));
  const [permitModel, setPermitModel] = useState(''), [permitUntil, setPermitUntil] = useState(''), [showJd, setShowJd] = useState(false);
  const [jdChange, setJdChange] = useState(false), [submitted, setSubmitted] = useState<Data | null>(null), [pending, setPending] = useState('');
  const live = useRef(false), working = useRef(false), keys = useRef(new Map<string, string>()), attempt = useRef<Attempt | null>(null), scope = useRef('');
  const available = useRef(tools); available.current = tools;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  async function call(name: string, args: Data, write = false): Promise<Data> {
    if (!live.current) throw new Error('Phiên đã thay đổi.');
    if (!mcp || !available.current.some(t => t.name === name)) throw new ToolError(`Không có công cụ ${name}.`, 'access_denied', '');
    if (write) {
      const signature = `${JSON.stringify(scope.current)}:${name}:${JSON.stringify(args)}`;
      if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
      args = { ...args, idempotency_key: keys.current.get(signature) };
    }
    const result = await mcp.client.callTool({ name, arguments: args }) as ToolResult;
    if (!live.current) throw new Error('Phiên đã thay đổi.');
    try { return readToolResult(result); }
    catch (e) {
      try { const url = new URL(String(confirmUrlOf(result))); if (url.origin === config.xtOrigin && url.protocol === 'https:') setConfirmUrl(url.href); } catch { /* Không hiển thị URL không hợp lệ. */ }
      throw e;
    }
  }
  async function run(job: () => Promise<void>, operation = '') {
    if (working.current) return;
    working.current = true; scope.current = operation; setBusy(true); setError(''); setDone(''); setConfirmUrl('');
    try { await job(); for (const key of keys.current.keys()) if (key.startsWith(`${JSON.stringify(operation)}:`)) keys.current.delete(key); }
    catch (e) { if (live.current) { setError(agentErrorText(e)); setKind(classifyError(e)); } }
    finally { working.current = false; if (live.current) setBusy(false); }
  }
  const search = async (model_id: string, extra: Data = {}) => rowsOf(await call('search_records', { model_id, limit: 100, ...extra }));
  /** Đọc phụ (tên người, sự cố, JD): lỗi chỉ làm mất phần hiển thị đó, không chặn danh sách. */
  const optional = <T,>(p: Promise<T>, fallback: T) => p.catch(() => fallback);

  async function loadList() {
    const [list, roleRows, jdRows, incidents, found] = await Promise.all([
      search(AGENT_MODEL), optional(search('vai_tro_agent'), [] as Data[]), optional(search('jd_agent', { limit: 200 }), [] as Data[]),
      optional(search('su_co_agent', { where: { trang_thai: 'mo' }, limit: 200 }), null), optional(call('find_people', { limit: 50 }).then(v => Array.isArray(v.people) ? v.people as Data[] : []), [] as Data[]),
    ]);
    if (!live.current) return;
    setAgents(list); setRoles(roleRows); setJds(jdRows); setOpenIncidents(incidents); setPeople(found);
  }
  async function loadDetail(ref: string) {
    const id = lastId(ref);
    const [value, jdRows, permits, incidents] = await Promise.all([
      call('get_record', { ref }), optional(search('jd_agent', { where: { ho_so: id }, limit: 50 }), [] as Data[]),
      optional(search('giay_phep_hanh_dong', { where: { ho_so: id }, limit: 100 }), null), optional(search('su_co_agent', { where: { ho_so: id }, limit: 100 }), null),
    ]);
    if (!live.current) return;
    setDetail({ record: recordOf(value, AGENT_MODEL), jds: jdRows, permits, incidents });
  }
  useEffect(() => { if (mcp) void run(loadList); }, [mcp]); // eslint-disable-line

  const personName = (value: unknown) => refName(value) || String(people.find(p => refId(p.ref) === refId(value))?.name || '') || (refId(value) ? 'chưa rõ tên' : '—');
  const roleRow = (value: unknown) => (roles || []).find(r => refId(r.ref || r.id) === refId(value));
  const roleName = (value: unknown) => refName(value) || (roleRow(value) ? title(roleRow(value)!) : '') || '—';
  const label = (id: string) => modelName(catalog, id);
  const open = (ref: string) => { setView({ kind: 'detail', ref }); setDetail(null); setHistory(null); setJdChange(false); setShowJd(false); setPermitModel(''); setPermitUntil(''); setSubmitted(null); attempt.current = null; void run(() => loadDetail(ref)); };
  const back = () => { setView({ kind: 'list' }); setDetail(null); setDialog(''); setSubmitted(null); attempt.current = null; void run(loadList); };

  async function setState(set: Data, message: string) {
    const record = detail!.record;
    if (typeof record.revision !== 'number') throw new Error('Chưa đọc được phiên bản hồ sơ.');
    const text = reason.trim(), send = (revision: number) => call('update_record', { ref: record.ref, set, expected_revision: revision, ...(text ? { reason: text } : {}) }, true);
    let value: Data;
    try { value = await send(record.revision); }
    catch (e) {
      if (errorReason(e) !== 'revision_conflict') throw e;
      // Hồ sơ vừa đổi (vd lõi tự tạm dừng khi chạm hạn mức): đọc lại phiên bản mới, thử đúng một lần nữa.
      const fresh = recordOf(await call('get_record', { ref: record.ref }), AGENT_MODEL);
      if (typeof fresh.revision !== 'number') throw e;
      value = await send(fresh.revision);
    }
    recordOf(value, AGENT_MODEL);
    setDialog(''); setReason(''); onMutated?.();
    await loadDetail(String(record.ref));
    if (live.current) setDone(message);
  }
  /**
   * Giá trị gửi lõi: chỉ trường người dùng đã đổi so với `base` (vai trò hoặc JD gốc); trường vắng thì lõi sao từ vai trò của hồ sơ.
   * Không có `base` (hồ sơ không có vai trò) thì gửi đủ và kèm năng lực dạng phẳng.
   */
  function jdPayload(hoSoId: string, base: JdValues | null, fallbackCaps: unknown): Data {
    const perDay = wholeNumber(jd.han_muc_viec_ngay), value = wholeNumber(jd.han_muc_gia_tri), autonomy = wholeNumber(jd.muc_tu_chu);
    if (jd.han_muc_viec_ngay.trim() !== '' && perDay === null) throw new Error('Việc mỗi ngày phải là số nguyên không âm.');
    if (jd.han_muc_gia_tri.trim() !== '' && value === null) throw new Error('Giá trị mỗi phiếu phải là số nguyên VND không âm (0 = không giới hạn).');
    if (autonomy === null || autonomy > 3) throw new Error('Chọn mức tự chủ.');
    const changed = (key: keyof JdValues) => !base || jd[key].trim() !== base[key].trim();
    return {
      ho_so: hoSoId, ...(base ? {} : { nang_luc: flatCapabilities(fallbackCaps) }),
      ...(changed('muc_tu_chu') ? { muc_tu_chu: autonomy } : {}),
      ...(perDay !== null && changed('han_muc_viec_ngay') ? { han_muc_viec_ngay: perDay } : {}),
      ...(value !== null && changed('han_muc_gia_tri') ? { han_muc_gia_tri: value } : {}),
      ...(jd.khung_gio.trim() && changed('khung_gio') ? { khung_gio: jd.khung_gio.trim() } : {}),
      ...(jd.ngay_lam.trim() && changed('ngay_lam') ? { ngay_lam: jd.ngay_lam.trim() } : {}),
    };
  }
  /** Tạo jd_agent rồi gửi duyệt (OTP do IT xác nhận trên trang xT); giữ ref đã tạo để thử lại không tạo trùng. */
  async function submitJd(current: Attempt, hoSoId: string, base: JdValues | null, fallbackCaps: unknown) {
    if (!current.jd) {
      const record = recordOf(await call('create_record', { model_id: 'jd_agent', values: jdPayload(hoSoId, base, fallbackCaps) }, true), 'jd_agent');
      if (typeof record.revision !== 'number') throw new Error('Máy chủ không trả về phiên bản của mô tả công việc.');
      current.jd = { ref: String(record.ref), revision: record.revision };
      if (live.current) setPending('Đã tạo mô tả công việc; chờ gửi IT duyệt.');
    }
    const sent = await call('submit_request', { ref: current.jd.ref, expected_revision: current.jd.revision }, true);
    const request = obj(sent.request || sent.record);
    let shown: Data = request;
    try { shown = recordOf(await call('get_record', { ref: current.jd.ref }), 'jd_agent'); } catch { /* cảnh báo chỉ hiện nếu đọc được */ }
    attempt.current = null; onMutated?.();
    if (live.current) { setPending(''); setSubmitted(shown); }
  }
  async function hireAgent() {
    const picked = (roles || []).find(r => String(r.ref) === role);
    if (!picked) throw new Error('Chọn một vai trò.');
    const ownerId = hire.owner || refId(me?.ref);
    if (!hire.ten.trim() || !hire.client_id.trim() || !ownerId) throw new Error('Nhập tên, mã client agent và người được làm thay.');
    const signature = JSON.stringify({ role, hire: { ...hire, owner: ownerId } });
    if (!attempt.current || (!attempt.current.hoSo && attempt.current.signature !== signature)) attempt.current = { signature };
    const current = attempt.current;
    if (!current.hoSo) {
      current.hoSo = String(recordOf(await call('create_record', { model_id: AGENT_MODEL, values: { ten: hire.ten.trim(), client_id: hire.client_id.trim(), nguoi_phu_trach: ownerId, vai_tro: refId(picked.ref), trang_thai: 'cho_duyet' } }, true), AGENT_MODEL).ref);
      if (live.current) setPending('Đã tạo hồ sơ agent; tiếp tục mô tả công việc.');
    }
    await submitJd(current, lastId(current.hoSo), jdFromSource(fieldsOf(picked)), fieldsOf(picked).nang_luc);
    if (live.current) setDone(`Đã gửi IT duyệt ${hire.ten.trim()}.`);
  }
  async function changeJd() {
    const record = detail!.record, active = activeJd(record, detail!.jds), role = roleRow(fieldsOf(record).vai_tro);
    if (!active) throw new Error('Chưa có mô tả công việc đang hiệu lực để dựa theo.');
    if (!attempt.current) attempt.current = { signature: String(record.ref) };
    await submitJd(attempt.current, lastId(String(record.ref)), role ? jdFromSource(fieldsOf(role)) : null, fieldsOf(active).nang_luc);
    await loadDetail(String(record.ref));
    if (live.current) setDone('Đã gửi IT duyệt yêu cầu đổi mô tả công việc.');
  }

  async function openPermit() {
    const record = detail!.record;
    if (!permitModel) throw new Error('Chọn loại phiếu được gửi duyệt.');
    await call('create_record', { model_id: 'giay_phep_hanh_dong', values: { ho_so: lastId(String(record.ref)), hanh_dong: `request.submit:${permitModel}`, ...(permitUntil ? { den_ngay: permitUntil } : {}) } }, true);
    setPermitModel(''); setPermitUntil(''); onMutated?.();
    await loadDetail(String(record.ref));
    if (live.current) setDone('Đã mở giấy phép gửi duyệt.');
  }
  const alert = <PortalAlert text={error} kind={kind}>{confirmUrl && <> <a href={confirmUrl} target="_blank" rel="noopener noreferrer">Mở trang xác nhận</a><p>Sau khi xác nhận, thử lại đúng thao tác để đọc kết quả.</p></>}</PortalAlert>;
  // khi hộp thoại mở, thông báo lỗi/xác nhận hiện trong hộp thoại (một chỗ duy nhất)
  const status = <>{busy && <p role="status">Đang xử lý…</p>}{done && <p role="status" data-testid="agenthr-done">{done}</p>}{pending && <p role="status">{pending} Thử lại sẽ tiếp tục bước chưa xong.</p>}{!dialog && alert}</>;
  const jdFields = (caps: unknown) => <>
    <fieldset disabled={busy || !mcp}>
      <label>Việc mỗi ngày<input aria-label="Việc mỗi ngày" type="number" min="0" step="1" value={jd.han_muc_viec_ngay} onChange={e => setJd(v => ({ ...v, han_muc_viec_ngay: e.target.value }))} required/></label>
      <label>Giá trị mỗi phiếu (₫, 0 = không giới hạn)<input aria-label="Giá trị mỗi phiếu" type="number" min="0" step="1" value={jd.han_muc_gia_tri} onChange={e => setJd(v => ({ ...v, han_muc_gia_tri: e.target.value }))} required/></label>
      <label>Giờ làm<input aria-label="Giờ làm" value={jd.khung_gio} placeholder="08:00-18:00" onChange={e => setJd(v => ({ ...v, khung_gio: e.target.value }))}/></label>
      <label>Ngày làm<input aria-label="Ngày làm" value={jd.ngay_lam} placeholder="mon-fri" onChange={e => setJd(v => ({ ...v, ngay_lam: e.target.value }))}/></label>
      <label>Mức tự chủ<select aria-label="Mức tự chủ" value={jd.muc_tu_chu} onChange={e => setJd(v => ({ ...v, muc_tu_chu: e.target.value }))} required><option value="">Chọn mức</option>{[0, 1, 2, 3].map(n => <option key={n} value={n}>{`L${n}`}</option>)}</select></label>
    </fieldset>
    {capChips(caps, label).length > 0 && <div className="caps" aria-label="Năng lực theo mô tả công việc">{capChips(caps, label).map(c => <span key={c.label} className={c.write ? 'cap w' : 'cap'}>{c.label}</span>)}</div>}
  </>;

  if (view.kind === 'hire') {
    const list = roles || [];
    const picked = list.find(r => String(r.ref) === role);
    return <section className="agenthr" aria-label="Tuyển agent">
      <button className="ghost sm back" onClick={back}>Nhân sự AI<ArrowLeft aria-hidden="true"/></button>
      <h3 className="agenthr-title">Tuyển agent</h3><p className="lead-sm">Chọn vai trò. Quyền lấy từ danh mục vai trò; IT duyệt (OTP trên trang xT) trước khi agent bắt đầu.</p>
      {roles === null ? <p role="status">Đang tải danh mục vai trò…</p> : !list.length ? <p role="status" className="empty-note">Chưa có vai trò agent nào trong danh mục.</p> : <div className="hire-split">
        <div className="roles" role="group" aria-label="Vai trò agent">{list.map(r => { const f = fieldsOf(r), chips = capChips(f.nang_luc, label); return <button key={String(r.ref)} type="button" className="role" aria-pressed={role === String(r.ref)} disabled={busy || !!attempt.current?.hoSo} onClick={() => {
          setRole(String(r.ref)); setHire(h => ({ ...h, ten: title(r) })); setJd(jdFromSource(f)); setSubmitted(null);
        }}><span className="ri" aria-hidden="true"><Bot/></span><strong>{title(r)}</strong>{f.mo_ta ? <span className="desc">{String(f.mo_ta)}</span> : null}{chips.length > 0 && <span className="caps">{chips.map(c => <span key={c.label} className={c.write ? 'cap w' : 'cap'}>{c.label}</span>)}</span>}</button>; })}</div>
        <form className="panel" aria-label="Hồ sơ agent" onSubmit={e => { e.preventDefault(); void run(hireAgent, 'hire'); }}>
          <h3>Hồ sơ</h3>
          <fieldset disabled={busy || !mcp || !!attempt.current?.hoSo}>
            <label>Tên<input aria-label="Tên agent" value={hire.ten} onChange={e => setHire(h => ({ ...h, ten: e.target.value }))} maxLength={200} required/></label>
            <label>Mã client agent (OAuth)<input aria-label="Mã client agent" value={hire.client_id} placeholder="vd. chatgpt" onChange={e => setHire(h => ({ ...h, client_id: e.target.value }))} maxLength={100} required/></label>
            <label>Làm thay<select aria-label="Làm thay" value={hire.owner || refId(me?.ref)} onChange={e => setHire(h => ({ ...h, owner: e.target.value }))} required>
              <option value="">Chọn người</option>
              {me?.ref && !people.some(p => refId(p.ref) === refId(me.ref)) ? <option value={refId(me.ref)}>{String(me.name || 'Tôi')}</option> : null}
              {people.filter(p => refId(p.ref)).map(p => <option key={String(p.ref)} value={refId(p.ref)}>{String(p.name || refId(p.ref))}</option>)}
            </select></label>
          </fieldset>
          {picked ? jdFields(fieldsOf(picked).nang_luc) : <p className="muted small">Chọn một vai trò để điền hạn mức.</p>}
          <button className="pri" disabled={busy || !mcp || !picked}>{pending ? 'Thử lại gửi IT duyệt' : 'Gửi IT duyệt'}<Send aria-hidden="true"/></button>
        </form>
      </div>}
      {submitted && <JdRiskBlock record={submitted} models={catalog}/>}
      {status}
    </section>;
  }

  if (view.kind === 'detail') {
    const record = detail?.record, f = record ? fieldsOf(record) : {}, state = String(f.trang_thai || '');
    const active = record ? activeJd(record, detail!.jds) : undefined, pendingJd = record ? jdFor(detail!.jds, lastId(String(record.ref))).pending : undefined;
    const jf = active ? fieldsOf(active) : {}, load = record ? workload(record, active) : null, st = record ? agentStatus(record) : null, name = record ? title(record) : '';
    const caps = capabilities(jf.nang_luc);
    const startChange = () => { setJd(jdFromSource(jf)); setShowJd(false); setJdChange(true); setSubmitted(null); attempt.current = null; };
    return <section className="agenthr" aria-label="Hồ sơ agent">
      <button className="ghost sm back" onClick={back}>Nhân sự AI<ArrowLeft aria-hidden="true"/></button>
      {!record ? (busy ? <p role="status">Đang tải hồ sơ…</p> : null) : <>
        <div className="agent-head">
          <span className="av agent lg" aria-hidden="true"><Bot/></span>
          <div><h3 className="agenthr-title">{name}</h3><div className="sub"><Pill tone={st!.tone}>{st!.label}</Pill><span className="muted">{[String(f.client_id || ''), `làm thay ${personName(f.nguoi_phu_trach)}`].filter(Boolean).join(', ')}</span></div></div>
          <div className="act">
            {state === 'dang_lam' && <button className="danger" disabled={busy} onClick={() => { setReason(''); setDialog('suspend'); }}>Đình chỉ<Pause aria-hidden="true"/></button>}
            {state === 'tam_dung' && <button className="pri" disabled={busy} onClick={startChange}>Mở lại<RotateCcw aria-hidden="true"/></button>}
            {state !== 'da_nghi' && <button className="ghost" disabled={busy} onClick={() => { setReason(''); setDialog('retire'); }}>Cho nghỉ<UserX aria-hidden="true"/></button>}
            <button className="ghost" disabled={busy} onClick={() => void run(() => loadDetail(String(record.ref)))}>Làm mới hồ sơ<RefreshCw aria-hidden="true"/></button>
          </div>
        </div>
        {pendingJd && <p className="note" role="note">Có yêu cầu đổi mô tả công việc đang chờ IT duyệt.</p>}
        {jdChange && <form className="panel" aria-label="Yêu cầu đổi mô tả công việc" onSubmit={e => { e.preventDefault(); void run(changeJd, `jd:${String(record.ref)}`); }}>
          <h3>{state === 'tam_dung' ? 'Mở lại: yêu cầu đổi mô tả công việc' : 'Đổi mô tả công việc'}</h3>
          <p className="muted small">Năng lực lấy lại từ vai trò của agent; chỉ giá trị bạn sửa được gửi kèm. Yêu cầu đi IT duyệt (OTP trên trang xT); khi được duyệt, lõi xT mới cho agent làm lại.</p>
          {jdFields(jf.nang_luc)}
          <button className="pri" disabled={busy || !active}>{pending ? 'Thử lại gửi IT duyệt' : 'Gửi IT duyệt'}<Send aria-hidden="true"/></button><button type="button" className="ghost" disabled={busy} onClick={() => { setJdChange(false); attempt.current = null; setPending(''); }}>Hủy</button>
        </form>}
        {submitted && <JdRiskBlock record={submitted} models={catalog}/>}
        {showJd && active && <JdRiskBlock record={active} models={catalog}/>}
        <div className="agent-grid">
          <section className="panel" aria-label="Được làm"><div className="ph"><h3>Được làm</h3>{active && state !== 'da_nghi' && !jdChange && <div className="act"><button className="sm" disabled={busy} onClick={startChange}>Đổi mô tả</button></div>}</div>
            {!active ? <p role="status" className="empty-note">Chưa có mô tả công việc đang hiệu lực.</p> : caps.length ? <CapTable caps={caps} models={catalog} permits={detail!.permits ?? undefined}/> : <p className="empty-note">Mô tả công việc không có năng lực nào.</p>}
            <p className="muted small">Duyệt, xóa và chi tiền luôn cần người xác nhận.</p>
          </section>
          <div className="grid">
            <section className="panel" aria-label="Hạn mức"><div className="ph"><h3>Hạn mức</h3></div>
              {!active ? <p className="empty-note">Chưa có hạn mức (chưa có mô tả công việc hiệu lực).</p> : <div className="limits">
                <div><div className="row"><span>Việc mỗi ngày</span><b>{load!.done ?? '—'} / {load!.limit ?? '—'}</b></div>{load!.limit ? <div className={`bar ${load!.tone}`} role="meter" aria-label="Việc hôm nay so với hạn mức" aria-valuemin={0} aria-valuemax={load!.limit} aria-valuenow={load!.done ?? 0}><i style={{ width: `${load!.pct}%` }}/></div> : null}</div>
                <div className="row"><span>Giá trị mỗi phiếu</span><b>{money(jf.han_muc_gia_tri)}</b></div>
                <div className="row"><span>Giờ làm</span><b>{workHours(jf)}</b></div>
                {num(jf.muc_tu_chu) !== null && <div className="row"><span>Mức tự chủ</span><b>L{num(jf.muc_tu_chu)}</b></div>}
                {f.hieu_luc_den ? <div className="row"><span>Hiệu lực đến</span><b>{dateVi(f.hieu_luc_den)}</b></div> : null}
                {f.ngay_dem ? <div className="row"><span>Số việc tính cho ngày</span><b>{dateVi(f.ngay_dem)}</b></div> : null}
              </div>}
              {f.jd_hieu_luc ? <div className="row"><span>Mô tả công việc hiệu lực</span>{active ? <button type="button" className="ghost sm" aria-expanded={showJd} onClick={() => setShowJd(v => !v)}>{showJd ? 'Ẩn mô tả công việc' : 'Xem mô tả công việc'}</button> : <span className="faint small">Không đọc được</span>}</div> : null}
            </section>
            <section className="panel" aria-label="Giấy phép hành động"><div className="ph"><h3>Giấy phép hành động</h3></div>
              {detail!.permits === null ? <p className="empty-note">Không đọc được giấy phép hành động.</p> : !detail!.permits.length ? <div className="empty"><span className="ei" aria-hidden="true"><Lock/></span><div>Chưa mở hành động nào. Agent chỉ soạn nháp.</div></div>
                : <ul className="portal-doclist">{detail!.permits.map((p, i) => { const pf = fieldsOf(p); return <li key={String(p.ref || i)}><span><strong>{permitLabel(pf.hanh_dong, label)}</strong>{pf.dieu_kien ? <small>{String(pf.dieu_kien)}</small> : null}</span><span className="faint small">{[pf.tu_ngay ? `Từ ${dateVi(pf.tu_ngay)}` : '', pf.den_ngay ? `Đến ${dateVi(pf.den_ngay)}` : ''].filter(Boolean).join(' ')}</span></li>; })}</ul>}
              {state !== 'da_nghi' && detail!.permits !== null && (() => {
                const needs = caps.filter(c => c.ops.includes('submit') && !detail!.permits!.some(p => permitCovers(fieldsOf(p), c.model)));
                if (!needs.length) return null;
                return <form className="permit-form" aria-label="Mở giấy phép gửi duyệt" onSubmit={e => { e.preventDefault(); void run(openPermit, `permit:${String(record.ref)}`); }}>
                  <fieldset disabled={busy || !mcp}>
                    <label>Cho phép gửi duyệt<select aria-label="Loại phiếu được gửi duyệt" value={permitModel} onChange={e => setPermitModel(e.target.value)} required><option value="">Chọn loại phiếu</option>{needs.map(c => <option key={c.model} value={c.model}>{label(c.model)}</option>)}<option value="*">Mọi loại</option></select></label>
                    <label>Đến ngày<input aria-label="Giấy phép đến ngày" type="date" value={permitUntil} onChange={e => setPermitUntil(e.target.value)}/></label>
                  </fieldset>
                  <button className="pri" disabled={busy || !mcp || !permitModel}>Mở giấy phép<Send aria-hidden="true"/></button>
                </form>;
              })()}
            </section>
          </div>
        </div>
        <section className="panel" aria-label="Sự cố"><div className="ph"><h3>Sự cố</h3></div>
          {detail!.incidents === null ? <p className="empty-note">Không đọc được sự cố.</p> : !detail!.incidents.length ? <p role="status" className="empty-note">Chưa có sự cố.</p>
            : <ul className="portal-doclist">{detail!.incidents.map((row, i) => { const inc = fieldsOf(row), isOpen = inc.trang_thai === 'mo'; return <li key={String(row.ref || i)}><span><strong>{INCIDENT_KIND[String(inc.loai)] || String(inc.loai || 'Sự cố')}</strong>{inc.chi_tiet ? <small>{String(inc.chi_tiet)}</small> : null}{inc.xu_ly ? <small>Xử lý: {INCIDENT_FIX[String(inc.xu_ly)] || String(inc.xu_ly)}</small> : null}</span><Pill tone={isOpen ? 'block' : 'neutral'}>{isOpen ? 'Mở' : 'Đã xử lý'}</Pill></li>; })}</ul>}
        </section>
        <section className="panel" aria-label="Nhật ký"><div className="ph"><h3>Nhật ký</h3><div className="act"><button className="sm" disabled={busy} onClick={() => void run(async () => { const v = await call('get_history', { ref: record.ref }); if (live.current) setHistory(Array.isArray(v.events) ? v.events as Data[] : []); })}>Xem nhật ký<History aria-hidden="true"/></button></div></div>
          {history && !history.length && <p role="status" className="empty-note">Chưa có sự kiện.</p>}
          {history && history.length > 0 && <ul className="portal-doclist">{history.map((ev, i) => { const actor = obj(ev.actor), behalf = obj(ev.on_behalf_of); return <li key={String(ev.seq ?? i)}><span><strong>{String(ev.summary || ev.action || '—')}</strong><small>{[String(ev.at || ''), String(actor.name || actor.ref || ''), behalf.name ? `thay ${String(behalf.name)}` : ''].filter(Boolean).join(' · ')}</small></span>{ev.outcome && ev.outcome !== 'ok' ? <Pill tone="block">{ev.outcome === 'denied' ? 'Bị chặn' : 'Lỗi'}</Pill> : null}</li>; })}</ul>}
        </section>
      </>}
      {status}
      {dialog && record && <Dialog title={dialog === 'suspend' ? `Đình chỉ ${name}?` : `Cho ${name} nghỉ?`} tone={dialog === 'suspend' ? 'block' : 'neutral'} onClose={() => setDialog('')}>
        <p className="muted">{dialog === 'suspend' ? 'Agent dừng ngay ở lượt kế tiếp. Dữ liệu và nhật ký được giữ. Mở lại cần IT duyệt (OTP).' : 'Agent thôi việc và bị thu hồi quyền. Dữ liệu và nhật ký được giữ.'}</p>
        <label className="field">Lý do<input aria-label="Lý do" value={reason} onChange={e => setReason(e.target.value)} maxLength={1000} placeholder="Ví dụ: vượt hạn mức nhiều lần" autoFocus/></label>
        <div className="dialog-act">
          <button className="danger fill" disabled={busy} onClick={() => void run(() => dialog === 'suspend'
            ? setState({ trang_thai: 'tam_dung', ly_do_tam_dung: 'thu_cong' }, `Đã đình chỉ ${name}.`)
            : setState({ trang_thai: 'da_nghi' }, `Đã cho ${name} nghỉ.`), `${dialog}:${String(record.ref)}`)}>{dialog === 'suspend' ? 'Xác nhận đình chỉ' : 'Xác nhận cho nghỉ'}{dialog === 'suspend' ? <Pause aria-hidden="true"/> : <UserX aria-hidden="true"/>}</button>
          <button disabled={busy} onClick={() => setDialog('')}>Hủy</button>
        </div>
        {alert}
      </Dialog>}
    </section>;
  }

  const list = agents || [];
  const active = list.filter(a => fieldsOf(a).trang_thai === 'dang_lam').length, paused = list.filter(a => fieldsOf(a).trang_thai === 'tam_dung').length;
  const today = list.reduce((sum, a) => sum + (num(fieldsOf(a).so_viec_hom_nay) ?? 0), 0);
  return <section className="agenthr" aria-label="Nhân sự AI">
    <div className="agenthr-head"><p className="lead-sm">Agent làm thay người, chỉ trong mô tả công việc và hạn mức đã được IT duyệt.</p><div className="act"><button className="ghost sm" disabled={busy || !mcp} onClick={() => void run(loadList)}>Làm mới<RefreshCw aria-hidden="true"/></button><button className="pri" disabled={busy || !mcp} onClick={() => { setView({ kind: 'hire' }); setSubmitted(null); setDone(''); setError(''); attempt.current = null; setRole(''); setHire({ ten: '', client_id: '', owner: '' }); }}>Tuyển agent<Plus aria-hidden="true"/></button></div></div>
    {agents && list.length > 0 && <div className="stats" aria-label="Số liệu Nhân sự AI">
      <Stat value={active} label="Agent đang làm"/><Stat value={today} label="Việc hôm nay"/><Stat value={paused} label="Đang tạm dừng"/><Stat value={openIncidents === null ? '—' : openIncidents.length} label="Sự cố mở"/>
    </div>}
    {agents === null ? (busy ? <p role="status">Đang tải danh sách agent…</p> : null) : !list.length ? <div className="panel empty"><span className="ei" aria-hidden="true"><Bot/></span><div>Chưa có agent nào. Bấm Tuyển agent để thêm.</div></div> :
      <div className="panel flush"><table><thead><tr><th>Agent</th><th>Vai trò</th><th>Phụ trách</th><th>Hôm nay</th><th>Trạng thái</th><th><span className="sr-only">Mở</span></th></tr></thead><tbody>{list.map(a => {
        const f = fieldsOf(a), st = agentStatus(a), w = workload(a, activeJd(a, jds)), name = title(a);
        return <tr key={String(a.ref)}><td><div className="who"><span className="av agent" aria-hidden="true"><Bot/></span><span className="t">{name}<small>{String(f.client_id || '')}</small></span></div></td><td>{roleName(f.vai_tro)}</td><td>{personName(f.nguoi_phu_trach)}</td>
          <td>{w.limit ? <div className="meter"><div className={`bar ${w.tone}`} role="meter" aria-label={`Việc hôm nay của ${name}`} aria-valuemin={0} aria-valuemax={w.limit} aria-valuenow={w.done ?? 0}><i style={{ width: `${w.pct}%` }}/></div><span className="small">{w.done ?? 0}/{w.limit}</span></div> : <span className="faint">{w.done ?? '—'}</span>}</td>
          <td><Pill tone={st.tone}>{st.label}</Pill></td><td><button className="ghost sm" onClick={() => open(String(a.ref))}>{`Mở ${name}`}<ChevronRight aria-hidden="true"/></button></td></tr>;
      })}</tbody></table></div>}
    {status}
  </section>;
}

function Stat({ value, label }: { value: ReactNode; label: string }) { return <div className="stat"><div className="v">{value}</div><div className="l">{label}</div></div>; }

/** Hộp thoại xác nhận theo mockup (#suspend): Esc hoặc Hủy để đóng. */
function Dialog({ title, tone, onClose, children }: { title: string; tone: 'block' | 'neutral'; onClose: () => void; children: ReactNode }) {
  useEffect(() => { const key = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); }; addEventListener('keydown', key); return () => removeEventListener('keydown', key); }, [onClose]);
  return <div className="scrim on" onClick={e => { if (e.target === e.currentTarget) onClose(); }}><div className="modal" role="dialog" aria-modal="true" aria-labelledby="agenthr-dialog-title">
    <div className="modal-head"><span className={`av ${tone}`} aria-hidden="true">{tone === 'block' ? <Pause/> : <UserX/>}</span><h3 id="agenthr-dialog-title">{title}</h3></div>
    {children}
  </div></div>;
}
