// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// AgentHR (gói nhan-su-ai): hàm thuần đọc bản ghi ho_so_agent / vai_tro_agent / jd_agent / giay_phep_hanh_dong / su_co_agent.
// Tên model và trường theo hợp đồng dữ liệu cố định; mọi số liệu lấy từ bản ghi thật, không có giá trị mặc định bịa.
import type { Tone } from './ui';

export type Data = Record<string, unknown>;
export const obj = (v: unknown): Data => (v && typeof v === 'object' && !Array.isArray(v) ? v as Data : {});
/** Trường của bản ghi (values) gộp với trường cấp trên mà máy chủ có thể trả kèm. */
export const fieldsOf = (row: Data): Data => ({ ...row, ...obj(row.values) });
export const AGENT_MODEL = 'ho_so_agent';
export const lastId = (ref: string) => ref.split(':').at(-1) || '';

/** Giá trị ref: UUID trần, "model:uuid" hoặc đối tượng {ref|id, name}. */
export function refId(value: unknown): string {
  if (typeof value === 'string') return lastId(value);
  const o = obj(value);
  return lastId(String(o.ref || o.id || ''));
}
export function refName(value: unknown): string {
  const o = obj(value);
  return String(o.name || o.title || o.label || '').trim();
}
/** Trường json: mảng hoặc chuỗi JSON của mảng. */
export function jsonList(value: unknown): unknown[] {
  if (Array.isArray(value)) return value;
  if (typeof value === 'string' && value.trim().startsWith('[')) { try { const v = JSON.parse(value); return Array.isArray(v) ? v : []; } catch { return []; } }
  return [];
}
export const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : typeof v === 'string' && v.trim() !== '' && Number.isFinite(Number(v)) ? Number(v) : null);

export type Capability = { model: string; ops: string[]; note?: string };
const OP_IDS = ['read', 'create', 'update', 'submit'] as const;
const truthy = (v: unknown) => v === true || v === 'true' || v === 1;
/** Thao tác dạng cũ: danh sách hoặc chuỗi "read,create". */
const legacyOps = (v: unknown): string[] => (Array.isArray(v) ? v.map(String) : typeof v === 'string' ? v.split(',') : []).map(s => s.trim()).filter(Boolean);
/**
 * Năng lực: dạng phẳng của lõi `[{model, read, create, update, submit, ghi_chu?}]`; vẫn đọc dạng cũ `thao_tac` (danh sách hoặc chuỗi).
 */
export function capabilities(value: unknown): Capability[] {
  return jsonList(value).map(obj).filter(c => typeof c.model === 'string' && c.model).map(c => {
    const flat: string[] = OP_IDS.filter(op => truthy(c[op])), ops = flat.length || !('thao_tac' in c) ? flat : legacyOps(c.thao_tac);
    const note = typeof c.ghi_chu === 'string' && c.ghi_chu.trim() ? c.ghi_chu.trim() : undefined;
    return { model: String(c.model), ops: ops.filter(op => (OP_IDS as readonly string[]).includes(op)), ...(note ? { note } : {}) };
  });
}
/** Năng lực dạng phẳng gửi cho lõi (không mảng lồng): dùng khi hồ sơ không có vai trò để sao. */
export function flatCapabilities(value: unknown): Data[] {
  return capabilities(value).map(cap => ({ model: cap.model, ...Object.fromEntries(OP_IDS.map(op => [op, cap.ops.includes(op)])), ...(cap.note ? { ghi_chu: cap.note } : {}) }));
}
export const OPS = [{ id: 'read', label: 'Đọc' }, { id: 'create', label: 'Tạo' }, { id: 'update', label: 'Sửa' }, { id: 'submit', label: 'Gửi duyệt' }] as const;
/** Nhãn ngắn của năng lực cho thẻ vai trò: "Đọc Hàng hóa", "Tạo Phiếu" (thao tác ghi đánh dấu write). */
export function capChips(value: unknown, modelLabel: (id: string) => string): { label: string; write: boolean }[] {
  const out: { label: string; write: boolean }[] = [];
  for (const cap of capabilities(value)) for (const op of OPS) if (cap.ops.includes(op.id)) out.push({ label: `${op.label} ${modelLabel(cap.model)}`, write: op.id !== 'read' });
  return out;
}

export const AGENT_STATUS: Record<string, { label: string; tone: Tone }> = {
  cho_duyet: { label: 'Chờ IT duyệt', tone: 'held' }, dang_lam: { label: 'Đang làm', tone: 'ok' },
  tam_dung: { label: 'Tạm dừng', tone: 'block' }, da_nghi: { label: 'Đã nghỉ', tone: 'neutral' },
};
export const PAUSE_REASON: Record<string, string> = { thu_cong: 'thủ công', han_muc: 'hạn mức', su_co: 'sự cố' };
export function agentStatus(row: Data): { label: string; tone: Tone } {
  const f = fieldsOf(row), state = String(f.trang_thai || ''), base = AGENT_STATUS[state];
  if (!base) return { label: state || '—', tone: 'neutral' };
  const why = state === 'tam_dung' ? PAUSE_REASON[String(f.ly_do_tam_dung || '')] : '';
  return why ? { ...base, label: `${base.label} · ${why}` } : base;
}

/** Cờ rủi ro do lõi tính (canh_bao); mã lạ hiện nguyên mã. */
export const RISK_FLAGS: Record<string, { title: string; detail: string }> = {
  pii_reach_expansion: { title: 'Đụng dữ liệu cá nhân', detail: 'Thêm quyền đọc hoặc ghi dữ liệu có thông tin cá nhân.' },
  money_reach_expansion: { title: 'Đụng tiền', detail: 'Thêm quyền ghi dữ liệu có giá trị tiền (phiếu, đề xuất chi).' },
  outbound_release: { title: 'Gửi đi duyệt / ra ngoài', detail: 'Mở quyền tự gửi duyệt hoặc gửi ra ngoài, không cần người bấm gửi.' },
  capability_expansion: { title: 'Thêm thao tác', detail: 'Thêm thao tác mới trên dữ liệu đã có quyền.' },
  autonomy_raise: { title: 'Tăng mức tự chủ', detail: 'Mức tự chủ của agent cao hơn trước.' },
  limit_removed: { title: 'Bỏ hoặc hạ hạn mức cứng', detail: 'Hạn mức việc hoặc giá trị bị bỏ hay nới rộng.' },
};
export type RiskFlag = { code: string; title: string; detail: string };
export function riskFlags(value: unknown): RiskFlag[] {
  return jsonList(value).map(item => {
    const o = obj(item), code = typeof item === 'string' ? item : String(o.code || o.loai || o.flag || o.ma || '');
    const known = RISK_FLAGS[code], extra = typeof item === 'string' ? '' : String(o.chi_tiet || o.detail || o.message || '');
    return { code, title: known?.title || code, detail: extra || known?.detail || '' };
  }).filter(f => f.code);
}

export const INCIDENT_KIND: Record<string, string> = {
  vuot_han_muc: 'Vượt hạn mức việc', vuot_gia_tri: 'Vượt hạn mức giá trị', ngoai_gio: 'Ngoài giờ làm',
  ngoai_jd: 'Ngoài mô tả công việc', giu_nhap: 'Giữ ở dạng nháp', tam_dung: 'Tạm dừng',
};
export const INCIDENT_FIX: Record<string, string> = { bo_qua: 'Bỏ qua', nang_han_muc: 'Nâng hạn mức', giu_tam_dung: 'Giữ tạm dừng' };

/** Tiền VND nguyên (lõi trả số hoặc chuỗi số): "5.000.000 ₫"; 0 = không giới hạn. */
export function money(value: unknown): string {
  const n = num(value);
  if (n === null) return '—';
  return n === 0 ? 'Không giới hạn' : `${Math.round(n).toLocaleString('vi-VN')} ₫`;
}
/** Số nguyên không âm từ ô nhập (VND, số việc…); không hợp lệ -> null. */
export const wholeNumber = (text: string): number | null => { const n = Number(text); return text.trim() !== '' && Number.isInteger(n) && n >= 0 ? n : null; };

/** Giấy phép `request.submit:<model>` / `request.submit:*` -> "Gửi duyệt: <nhãn model>" / "Gửi duyệt: mọi loại". */
export function permitLabel(action: unknown, modelLabel: (id: string) => string): string {
  const text = String(action || '').trim(), m = /^request\.submit:(.+)$/.exec(text);
  if (!m) return text || '—';
  return m[1] === '*' ? 'Gửi duyệt: mọi loại' : `Gửi duyệt: ${modelLabel(m[1])}`;
}
/** Giấy phép (trường của giay_phep_hanh_dong) đang hiệu lực hôm nay và khớp model (đúng model hoặc `*`). */
export function permitCovers(permit: Data, model: string, today = new Date().toLocaleDateString('sv-SE')): boolean {
  const action = String(permit.hanh_dong || ''), from = String(permit.tu_ngay || ''), until = String(permit.den_ngay || '');
  if ((from && today < from) || (until && today > until)) return false;
  return action === `request.submit:${model}` || action === 'request.submit:*';
}

const CODES: Record<string, string> = {
  hire_requires_jd_approval: 'Hồ sơ agent mới luôn ở Chờ IT duyệt; agent chỉ vào làm khi IT duyệt mô tả công việc.',
  immutable_field: 'Không đổi được client hoặc người phụ trách của agent; cho nghỉ rồi tuyển hồ sơ mới.',
  reopen_requires_jd_approval: 'Mở lại agent phải qua mô tả công việc mới được IT duyệt, không sửa trực tiếp.',
  portal_client: 'Client của Desk là phiên của chính người dùng, không tuyển làm agent được.',
  agent_terminated: 'Agent đã nghỉ việc, hồ sơ này không đổi trạng thái được.',
  behalf_permission: 'Bạn chưa có quyền làm thay người phụ trách này.',
  revision_conflict: 'Hồ sơ vừa được người khác cập nhật. Làm mới hồ sơ rồi thử lại.',
};
/** Mã lý do của lỗi tool: `error.details.reason`, không có thì chính mã lỗi. */
export function errorReason(error: unknown): string {
  const e = error as { code?: unknown; details?: unknown } | null;
  const reason = String(obj(e?.details).reason || '');
  return CODES[reason] ? reason : CODES[String(e?.code || '')] ? String(e?.code) : reason;
}
/** Thông báo ngắn tiếng Việt cho mã lỗi AgentHR; mã lạ giữ nguyên thông báo gốc. */
export function agentErrorText(error: unknown): string {
  const text = CODES[errorReason(error)], original = error instanceof Error ? error.message : String(error);
  if (!text) return original;
  const id = /\(mã yêu cầu: [^)]+\)\s*$/.exec(original)?.[0];
  return id ? `${text} ${id}` : text;
}
const DAY: Record<string, string> = { mon: 'T2', tue: 'T3', wed: 'T4', thu: 'T5', fri: 'T6', sat: 'T7', sun: 'CN' };
export function workDays(value: unknown): string {
  const text = String(value || '').trim();
  if (!text) return '';
  return text.split(',').map(part => part.trim().split('-').map(d => DAY[d.toLowerCase()] || d).join('–')).join(', ');
}
export function workHours(f: Data): string {
  const hours = String(f.khung_gio || '').trim().replace('-', ' – '), days = workDays(f.ngay_lam);
  return [hours, days].filter(Boolean).join(', ') || '—';
}

/** JD đang hiệu lực của một hồ sơ: yêu cầu đã duyệt mới nhất; JD chờ duyệt mới nhất tách riêng. */
const APPROVED = new Set(['approved', 'hieu_luc', 'done', 'completed']);
const PENDING = new Set(['in_review', 'submitted', 'draft', 'returned', 'cho_duyet']);
const when = (row: Data) => String(row.updated_at || row.created_at || '');
const latest = (rows: Data[]) => rows.slice().sort((a, b) => when(b).localeCompare(when(a)))[0];
export function jdFor(jds: Data[], hoSoId: string): { active?: Data; pending?: Data } {
  const mine = jds.filter(row => refId(fieldsOf(row).ho_so) === hoSoId);
  const state = (row: Data) => String(row.state || fieldsOf(row).state || '');
  return { active: latest(mine.filter(row => APPROVED.has(state(row)))), pending: latest(mine.filter(row => PENDING.has(state(row)))) };
}

/** JD đang hiệu lực: bản ghi `jd_hieu_luc` của hồ sơ (do lõi đặt khi IT duyệt); chưa có thì JD đã duyệt mới nhất. */
export function activeJd(agent: Data, jds: Data[]): Data | undefined {
  const id = refId(fieldsOf(agent).jd_hieu_luc);
  return (id ? jds.find(row => refId(row.ref || row.id) === id) : undefined) ?? jdFor(jds, lastId(String(agent.ref || ''))).active;
}
/** "2026-10-23" -> "23/10/2026"; giá trị khác giữ nguyên. */
export const dateVi = (value: unknown): string => { const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(value || '')); return m ? `${m[3]}/${m[2]}/${m[1]}` : String(value || ''); };

/** Số việc hôm nay so với hạn mức (JD đang hiệu lực); không có hạn mức thì không có thanh. */
export function workload(agent: Data, jd?: Data): { done: number | null; limit: number | null; pct: number; tone: '' | 'warn' | 'full' } {
  const done = num(fieldsOf(agent).so_viec_hom_nay), limit = jd ? num(fieldsOf(jd).han_muc_viec_ngay) : null;
  if (done === null || !limit || limit <= 0) return { done, limit, pct: 0, tone: '' };
  const pct = Math.min(100, Math.round((done / limit) * 100));
  return { done, limit, pct, tone: pct >= 100 ? 'full' : pct >= 80 ? 'warn' : '' };
}
