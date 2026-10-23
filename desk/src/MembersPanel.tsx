// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { config } from './config';
import { readToolResult, ToolError } from './portal-result';
import { confirmUrlOf } from './portal-request';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { recordTitle, refKind, roleLabel } from './portal-labels';
import { TabList, TabPanel } from './Tabs';

type Data = Record<string, unknown>;
type Attempt = { signature: string; employee?: string; caseRef?: string };
const obj = (v: unknown): Data => v && typeof v === 'object' && !Array.isArray(v) ? v as Data : {};
const id = (ref: string) => ref.split(':').at(-1)!;
function recordRef(value: Data, model: string): string {
  const ref = obj(value.record).ref;
  if (typeof ref !== 'string' || !ref.startsWith(`${model}:`) || !id(ref)) throw new Error('Máy chủ không trả về mã bản ghi hợp lệ.');
  return ref;
}
const MEMBERS_TABS = [{ id: 'roles', label: 'Vai trò của tôi' }, { id: 'onboard', label: 'Tiếp nhận nhân viên' }, { id: 'grant', label: 'Cấp quyền SSO' }, { id: 'revoke', label: 'Thu hồi' }] as const;
type MembersTab = (typeof MEMBERS_TABS)[number]['id'];
type Props = { mcp: McpConnection | null; tools: McpTool[]; workspace: Data | null; onRefresh: () => void };
export function MembersPanel({ mcp, tools, workspace, onRefresh }: Props) {
  const [raw, setRaw] = useState<Record<string, string>>({}), [grant, setGrant] = useState<Record<string, string>>({}), [revokedOn, setRevokedOn] = useState('');
  const [names, setNames] = useState<Record<string, string>>({}), [grants, setGrants] = useState<Data[] | null>(null), [tasks, setTasks] = useState<Data[] | null>(null), [cursors, setCursors] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false), [expired, setExpired] = useState(false), [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [done, setDone] = useState(''), [confirmUrl, setConfirmUrl] = useState(''), [pending, setPending] = useState('');
  const [tab, setTab] = useState<MembersTab>('roles');
  const [hasErrorDetail, setHasErrorDetail] = useState(false), [failedOp, setFailedOp] = useState('');
  const live = useRef(false), working = useRef(false), attempt = useRef<Attempt | null>(null), keys = useRef(new Map<string, string>());
  const scope = useRef('');
  const available = useRef(tools); available.current = tools;
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  function requireTools(names: string[]) {
    if (!live.current) throw new Error('Phiên đã thay đổi.');
    if (expired) throw new ToolError('Phiên đã hết hạn.', 'unauthenticated', '');
    for (const name of names) if (!mcp || !available.current.some(t => t.name === name)) throw new ToolError(`Không có công cụ ${name}.`, 'access_denied', '');
  }
  async function call(name: string, args: Data, write = false): Promise<Data> {
    requireTools([name]);
    if (write) {
      const signature = `${JSON.stringify(scope.current)}:${name}:${JSON.stringify(args)}`;
      if (!keys.current.has(signature)) keys.current.set(signature, crypto.randomUUID());
      args = { ...args, idempotency_key: keys.current.get(signature) };
    }
    const result = await mcp!.client.callTool({ name, arguments: args }) as ToolResult;
    if (!live.current) throw new Error('Phiên đã thay đổi.');
    try { return readToolResult(result); }
    catch (e) {
      try { const url = new URL(String(confirmUrlOf(result))); if (url.origin === config.xtOrigin && url.protocol === 'https:') setConfirmUrl(url.href); } catch { /* Không hiển thị URL không hợp lệ. */ }
      throw e;
    }
  }
  async function run(job: () => Promise<void>, operation = '') {
    if (working.current) return;
    working.current = true; scope.current = operation; setBusy(true); setError(''); setHasErrorDetail(false); setFailedOp(''); setDone(''); setConfirmUrl('');
    try { await job(); for (const key of keys.current.keys()) if (key.startsWith(`${JSON.stringify(operation)}:`)) keys.current.delete(key); }
    catch (e) { if (live.current) {
      const state = classifyError(e), detail = e instanceof ToolError && e.code === 'precondition_failed' && typeof e.details.reason === 'string' && typeof e.details.message === 'string' ? e.details.message.trim() : '';
      setFailedOp(operation); setHasErrorDetail(!!detail); setError(detail ? `${detail}${e instanceof ToolError && e.requestId ? ` (mã yêu cầu: ${e.requestId})` : ''}` : (e as Error).message); setKind(state);
      if (state === 'expired') { setExpired(true); setGrants(null); setTasks(null); setRaw({}); setGrant({}); setPending(''); setCursors({}); attempt.current = null; keys.current.clear(); }
    } }
    finally { working.current = false; if (live.current) setBusy(false); }
  }
  async function onboard() {
    requireTools(['create_record', 'start_process']);
    const me = obj(workspace?.me), signature = JSON.stringify(raw);
    if (!me.ref) throw new Error('Chưa đọc được người khởi tạo từ phiên hiện tại.');
    if (!attempt.current || (!attempt.current.employee && attempt.current.signature !== signature)) attempt.current = { signature };
    const current = attempt.current;
    if (!current.employee) {
      current.employee = recordRef(await call('create_record', { model_id: 'employee', values: { emp_no: raw.emp_no, full_name: raw.full_name, job_title: raw.job_title, department: raw.department, user_id: raw.user_id } }, true), 'employee');
      setPending(`Đã tạo ${refKind(current.employee)}; tiếp tục hồ sơ tiếp nhận.`);
    }
    if (!current.caseRef) {
      current.caseRef = recordRef(await call('create_record', { model_id: 'lifecycle_case', values: { code: raw.code, employee: id(current.employee), kind: 'onboarding', effective_on: raw.effective_on, initiated_by: id(String(me.ref)) } }, true), 'lifecycle_case');
      setPending(`Đã tạo ${refKind(current.caseRef)}; chờ khởi động quy trình.`);
    }
    const value = await call('start_process', { process_id: 'employee_onboarding', subject_ref: current.caseRef, parameters: { case_id: id(current.caseRef), employee_id: id(current.employee), employee_user: `person:${raw.user_id}` } }, true);
    if (typeof value.process_instance !== 'string' || !value.process_instance.startsWith('process_instance:') || !id(value.process_instance) || !['running', 'completed'].includes(String(value.state))) throw new Error('Chưa xác nhận được quy trình đã khởi động.');
    setDone(`Đã khởi động ${refKind(value.process_instance)} tiếp nhận.`); setPending(''); setRaw({}); attempt.current = null;
  }
  async function load(which: 'grants' | 'tasks', cursor = '') {
    if (which === 'grants') setGrants(cursor ? grants : null); else setTasks(cursor ? tasks : null);
    if (!cursor) setCursors(previous => ({ ...previous, [which]: '' }));
    const value = await call(which === 'grants' ? 'search_records' : 'get_inbox', { ...(which === 'grants' ? { model_id: 'access_grant' } : { kinds: ['task'] }), limit: 50, ...(cursor ? { cursor } : {}) });
    if (!Array.isArray(value.items)) throw new Error('Máy chủ không trả về danh sách hợp lệ.');
    const rows = value.items as Data[];
    if (which === 'grants') {
      setGrants(previous => cursor ? [...(previous || []), ...rows] : rows);
      void employeeNames();
    } else setTasks(previous => cursor ? [...(previous || []), ...rows] : rows);
    setCursors(previous => ({ ...previous, [which]: typeof value.next_cursor === 'string' ? value.next_cursor : '' }));
  }
  /** Tên nhân viên để dòng quyền hiện "Nhân viên: Lan" thay vì UUID; lỗi thì dòng quyền ghi "chưa rõ tên". */
  async function employeeNames() {
    try {
      const value = await call('search_records', { model_id: 'employee', limit: 100 });
      const found: Record<string, string> = {};
      for (const row of Array.isArray(value.items) ? value.items as Data[] : []) {
        const name = recordTitle(row) || String(obj(row.values).full_name || '').trim();
        if (name) found[id(String(row.ref || row.id || ''))] = name;
      }
      if (live.current) setNames(previous => ({ ...previous, ...found }));
    } catch { /* chỉ mất tên hiển thị */ }
  }
  async function complete(task: Data) {
    const value = await call('transition_record', { ref: task.ref, to_state: 'done', expected_revision: task.revision }, true);
    if (recordRef(value, 'lifecycle_task') !== task.ref || value.to_state !== 'done') throw new Error('Chưa xác nhận được bước đã hoàn thành.');
    setTasks(previous => previous?.filter(t => t.ref !== task.ref) || []); setDone(`Đã hoàn thành ${String(task.title || refKind(task.ref))}.`); onRefresh();
  }
  async function revoke(row: Data) {
    if (!revokedOn) throw new Error('Chọn ngày thu hồi.');
    const value = await call('update_record', { ref: row.ref, set: { revoked_on: revokedOn }, expected_revision: row.revision }, true);
    if (recordRef(value, 'access_grant') !== row.ref || obj(obj(value.record).values).revoked_on !== revokedOn) throw new Error('Chưa xác nhận được quyền đã thu hồi.');
    setGrants(previous => previous?.map(g => g.ref === row.ref ? obj(value.record) : g) || []); setDone(`Đã thu hồi ${refKind(row.ref)} ${obj(row.values).role ? roleLabel(obj(row.values).role) : ''}`.trim() + '.'); onRefresh();
  }
  const roles = obj(workspace?.me).roles;
  const input = (field: string, label: string, values: Record<string, string>, set: typeof setRaw, type = 'text') => <label key={field}>{label}<input aria-label={label} type={type} value={values[field] || ''} onChange={e => set(previous => ({ ...previous, [field]: e.target.value }))} required/></label>;
  const lifecycleTasks = tasks?.filter(t => String(t.ref).startsWith('lifecycle_task:'));
  const panel = (id: MembersTab, children: ReactNode) => <TabPanel prefix="members" id={id} active={tab === id}>{children}</TabPanel>;
  return <section className="panel portal-members" aria-label="Thành viên và quyền">
    <h3>Thành viên và quyền</h3>
    <TabList label="Mục cấp quyền" prefix="members" tabs={MEMBERS_TABS} value={tab} onChange={setTab} disabled={expired}/>
    {panel('roles', <>
    {!expired && (workspace ? <p>Vai trò hiệu lực của tôi: {Array.isArray(roles) ? roles.map(roleLabel).join(', ') || 'Chưa có vai trò' : 'Chưa đọc được vai trò'}</p> : <p role="status">Đang tải vai trò…</p>)}
    <button disabled={busy || expired || !mcp} onClick={onRefresh}>Làm mới vai trò</button>
    </>)}
    {panel('onboard', <>
    <form aria-label="Tiếp nhận nhân viên" onSubmit={e => { e.preventDefault(); void run(onboard, 'onboard'); }}>
      <h4>HR: Tiếp nhận nhân viên</h4><fieldset disabled={busy || expired || !!attempt.current?.employee || !mcp}>
        {input('emp_no', 'Mã nhân viên', raw, setRaw)}{input('full_name', 'Họ và tên', raw, setRaw)}{input('job_title', 'Chức danh', raw, setRaw)}{input('department', 'ID phòng ban', raw, setRaw)}{input('user_id', 'ID tài khoản nhân viên', raw, setRaw)}
      </fieldset><fieldset disabled={busy || expired || !!attempt.current?.caseRef || !mcp}>
        {input('code', 'Mã hồ sơ', raw, setRaw)}{input('effective_on', 'Ngày hiệu lực', raw, setRaw, 'date')}
      </fieldset><p>Mã nhân viên: NV-0001; mã hồ sơ: ON-2026-0001. ID phòng ban và tài khoản là UUID có sẵn, không gồm tiền tố.</p>
      <button className="pri" disabled={busy || expired || !mcp || !workspace}>{pending ? 'Thử lại tiếp nhận' : 'Tạo và bắt đầu tiếp nhận'}</button>
    </form>
    {pending && <p role="status">{pending} Thử lại sẽ tiếp tục bước chưa xong.</p>}
    </>)}
    {panel('grant', <>
    <form aria-label="Đề xuất cấp quyền" onSubmit={e => { e.preventDefault(); void run(async () => {
      recordRef(await call('create_record', { model_id: 'access_grant', values: { employee: grant.employee, system: 'keycloak', role: grant.role, issued_on: grant.issued_on } }, true), 'access_grant');
      setDone('Đã ghi đề xuất cấp quyền; quyền chỉ hiệu lực sau bước IT.'); setGrant({});
    }, 'grant'); }}><h4>IT: Cấp quyền SSO</h4><fieldset disabled={busy || expired || !mcp}>{input('employee', 'ID nhân viên được cấp', grant, setGrant)}{input('role', 'Vai trò được cấp', grant, setGrant)}{input('issued_on', 'Ngày cấp', grant, setGrant, 'date')}</fieldset><button className="pri" disabled={busy || expired || !mcp}>Ghi đề xuất cấp quyền</button></form>
    <p>Chỉ cấp vai trò thuộc danh sách cho phép của doanh nghiệp. Người tạo không được tự duyệt cấp quyền. Bước IT xuất hiện sau bước HR; người được giao phải xác nhận.</p>
    <button disabled={busy || expired || !mcp} onClick={() => void run(() => load('tasks'))}>Tải bước quy trình</button>
    {lifecycleTasks && !lifecycleTasks.length && <p role="status">Chưa có bước nhân sự được giao cho bạn.</p>}
    {lifecycleTasks?.map(task => <article className="portal-entry" key={String(task.ref)}><strong>{String(task.title || task.ref)}</strong><p>Bước {String(task.step).toUpperCase()}</p><button disabled={busy || expired || typeof task.revision !== 'number' || !Array.isArray(task.to_states) || !task.to_states.includes('done')} onClick={() => void run(() => complete(task), `step:${task.ref}`)}>Hoàn thành {String(task.step).toUpperCase()}</button></article>)}
    {cursors.tasks && <button disabled={busy || expired} onClick={() => void run(() => load('tasks', cursors.tasks))}>Tải thêm bước</button>}
    </>)}
    {panel('revoke', <>
    <button disabled={busy || expired || !mcp} onClick={() => void run(() => load('grants'))}>Tải quyền truy cập</button>
    <label>Ngày thu hồi<input aria-label="Ngày thu hồi" type="date" value={revokedOn} disabled={busy || expired} onChange={e => setRevokedOn(e.target.value)}/></label>
    {grants && !grants.length && <p role="status">Chưa có quyền truy cập bạn được xem.</p>}
    {grants?.map(row => { const values = obj(row.values), name = values.role ? roleLabel(values.role) : recordTitle(row, 'Quyền truy cập'), who = names[id(String(values.employee || ''))] || 'chưa rõ tên'; return <article className="portal-entry" key={String(row.ref)}><strong>{name}</strong><p>Nhân viên: {who} · Hệ thống: {String(values.system)} · Cấp: {String(values.issued_on)} · {values.revoked_on ? `Đã thu hồi: ${values.revoked_on}` : 'Chưa thu hồi (chưa khẳng định đã hiệu lực)'}</p>{!values.revoked_on && <><button title={String(row.ref)} aria-describedby={`grant-ref-${String(row.ref)}`} disabled={busy || expired || !revokedOn || typeof row.revision !== 'number'} onClick={() => void run(() => revoke(row), `revoke:${row.ref}`)}>Thu hồi {name}</button><span id={`grant-ref-${String(row.ref)}`} className="sr-only">Mã {String(row.ref)}</span></>}</article>; })}
    {cursors.grants && <button disabled={busy || expired} onClick={() => void run(() => load('grants', cursors.grants))}>Tải thêm quyền</button>}
    </>)}
    {busy && <p role="status">Đang xử lý…</p>}{done && <p role="status">{done}</p>}
    <PortalAlert text={error} kind={kind}>{confirmUrl && <> <a href={confirmUrl} target="_blank" rel="noopener noreferrer">Mở trang xác nhận</a><p>Sau khi xác nhận, thử lại đúng thao tác để đọc kết quả.</p></>}{error && !hasErrorDetail && kind === 'error' && (failedOp === 'grant' || failedOp.startsWith('step:')) && <p>Nếu bước cấp quyền thất bại, kiểm tra vai trò được phép và việc người tạo tự duyệt; máy chủ có thể chỉ trả lỗi quy trình tổng quát.</p>}</PortalAlert>
  </section>;
}
