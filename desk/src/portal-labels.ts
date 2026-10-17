// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Nhãn tiếng Việt cho khóa/giá trị kỹ thuật hiển thị cho người dùng. Khóa lạ giữ nguyên (không đoán).

export const fieldLabels: Record<string, string> = {
  state: 'Trạng thái', revision: 'Phiên bản', code: 'Mã', sku: 'Mã hàng', name: 'Tên', unit: 'Đơn vị', warehouse: 'Kho', item: 'Hàng hóa', location: 'Vị trí',
  quantity_milli: 'Số lượng', reorder_level_milli: 'Ngưỡng đặt lại', kind: 'Loại', title: 'Chức danh', email: 'Email', phone: 'Điện thoại', org_unit: 'Bộ phận',
  manager: 'Quản lý', due_at: 'Hạn', sla_status: 'SLA', received_at: 'Nhận lúc', note: 'Ghi chú', lines: 'Dòng hàng', amount: 'Số tiền', reason: 'Lý do',
  priority: 'Mức ưu tiên', owner: 'Phụ trách', effective_on: 'Hiệu lực từ', issued_on: 'Ngày cấp', revoked_on: 'Ngày thu hồi', posted_at: 'Ghi sổ lúc',
  approved_by: 'Người duyệt', customer: 'Khách', stage: 'Giai đoạn', status: 'Trạng thái', description: 'Mô tả', period: 'Kỳ', created_at: 'Tạo lúc', updated_at: 'Cập nhật lúc',
};
/** Khóa kỹ thuật không có ý nghĩa với người dùng cuối. */
export const hiddenFields = new Set(['model_id', 'revision', 'allowed_actions']);

const valueLabels: Record<string, Record<string, string>> = {
  state: { draft: 'Nháp', submitted: 'Đã gửi', in_review: 'Đang duyệt', approved: 'Đã duyệt', rejected: 'Đã từ chối', returned: 'Trả lại', done: 'Hoàn thành', cancelled: 'Đã hủy', published: 'Đã ban hành', open: 'Đang mở', closed: 'Đã đóng' },
  sla_status: { on_track: 'Đúng hạn', at_risk: 'Sắp trễ', breached: 'Quá hạn' },
  unit: { pcs: 'cái', piece: 'cái', box: 'hộp', set: 'bộ', pack: 'gói', bottle: 'chai', meter: 'mét' },
  kind: { approval: 'Chờ duyệt', task: 'Công việc', notification: 'Thông báo', mention: 'Nhắc đến', receive: 'Nhập kho', issue: 'Xuất kho' },
};
valueLabels.status = valueLabels.state;
export const fieldLabel = (key: string) => fieldLabels[key] || key;

const isoDate = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const plainDate = /^(\d{4})-(\d{2})-(\d{2})$/;
const plainNumber = /^-?\d+(\.\d+)?$/;
/** Giá trị enum/thời gian đã biết -> tiếng Việt; còn lại trả nguyên văn. */
export function valueLabel(key: string, value: string): string {
  const mapped = valueLabels[key]?.[value];
  if (mapped) return mapped;
  if (key === 'amount' && plainNumber.test(value)) return groupDigits(value);
  const day = plainDate.exec(value);
  if (day) return `${day[3]}/${day[2]}/${day[1]}`;
  if (isoDate.test(value)) { const date = new Date(value); if (!Number.isNaN(date.getTime())) return date.toLocaleString('vi-VN'); }
  return value;
}
export const primitiveLabels: Record<string, string> = { record: 'Bản ghi', request: 'Yêu cầu', document: 'Tài liệu', task: 'Công việc' };
export const primitiveLabel = (value: unknown) => primitiveLabels[String(value)] || String(value);

/** Chữ đầu về chữ thường khi nhãn đứng giữa câu ("Tổng Số tiền" -> "Tổng số tiền"); từ viết hoa hoàn toàn (SLA, VAT) giữ nguyên. */
export function inSentence(label: string): string {
  return /^\p{Lu}\p{Ll}/u.test(label) ? label.charAt(0).toLocaleLowerCase('vi-VN') + label.slice(1) : label;
}

/** Tên người đọc được của bản ghi (title_field do máy chủ trả ở `title`); không có thì mới dùng `fallback`. */
export function recordTitle(row: Record<string, unknown>, fallback = ''): string {
  const values = row.values && typeof row.values === 'object' ? row.values as Record<string, unknown> : {};
  for (const candidate of [row.title, row.name, values.title, values.name, values.code]) if (typeof candidate === 'string' && candidate.trim()) return candidate.trim();
  return fallback;
}

/** Chuỗi số -> nhóm nghìn kiểu vi-VN, không qua Number (không mất chính xác). */
export function groupDigits(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const whole = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${match[1]}${whole}${match[3] ? `,${match[3]}` : ''}`;
}

/** Loại bản ghi đọc được từ ref `model:uuid`; không có nhãn thì "bản ghi" (không lộ mã kỹ thuật). */
const refKinds: Record<string, string> = {
  advance_request: 'phiếu tạm ứng', leave_request: 'đơn nghỉ phép', employee: 'hồ sơ nhân viên', lifecycle_case: 'hồ sơ tiếp nhận', lifecycle_task: 'bước quy trình',
  access_grant: 'quyền truy cập', process_instance: 'quy trình',
};
export function refKind(ref: unknown): string { return refKinds[String(ref).split(':')[0]] || 'bản ghi'; }

/** Hành động trong lịch sử bản ghi khi máy chủ không trả `summary`. */
const actionLabels: Record<string, string> = {
  create: 'Tạo phiếu', update: 'Cập nhật', submit: 'Gửi duyệt', approve: 'Duyệt', reject: 'Từ chối', return: 'Trả lại', cancel: 'Hủy', transition: 'Chuyển trạng thái', delete: 'Xóa',
};
export const actionLabel = (action: string) => actionLabels[action] || action;

/** Tên công cụ MCP -> nhãn tiếng Việt cho chip chat/xác nhận; tool lạ (của ứng dụng) giữ tên gốc. */
const toolLabels: Record<string, string> = {
  inspect_workspace: 'Xem không gian làm việc', describe_model: 'Xem cấu trúc dữ liệu', describe_process: 'Xem quy trình', find_people: 'Tìm nhân sự',
  search_records: 'Tìm bản ghi', aggregate_records: 'Tổng hợp số liệu', get_record: 'Mở bản ghi', get_inbox: 'Xem hộp thư', get_history: 'Xem lịch sử',
  create_record: 'Tạo bản ghi', update_record: 'Cập nhật bản ghi', transition_record: 'Chuyển trạng thái', delete_record: 'Xóa bản ghi', upload_file: 'Tải tệp lên',
  submit_request: 'Gửi duyệt', decide_request: 'Duyệt yêu cầu', create_task: 'Giao việc', start_process: 'Khởi động quy trình', notify: 'Gửi thông báo',
  propose_model_change: 'Đề xuất đổi mô hình dữ liệu', propose_process_change: 'Đề xuất đổi quy trình',
};
export const toolLabel = (name: string) => toolLabels[name] || name;

/** Vai trò trong doanh nghiệp -> tiếng Việt; vai trò lạ giữ nguyên. */
const roleLabels: Record<string, string> = {
  staff: 'Nhân viên', employee: 'Nhân viên', hr: 'Nhân sự', accountant: 'Kế toán', manager: 'Quản lý', it: 'IT', it_admin: 'Quản trị IT', iam_admin: 'Quản trị định danh',
  approver: 'Người duyệt', admin: 'Quản trị viên', tenant_admin: 'Quản trị doanh nghiệp', warehouse: 'Thủ kho', warehouse_manager: 'Quản lý kho', agent: 'Trợ lý',
  ceo: 'Giám đốc', sales: 'Kinh doanh', finance: 'Tài chính',
};
export const roleLabel = (role: unknown) => roleLabels[String(role)] || String(role);

/** `kind` của mục hộp thư (cần duyệt, công việc...) khác `kind` của phiếu kho (nhập/xuất): nhãn trường theo giá trị. */
const inboxKinds = new Set(['approval', 'task', 'notification', 'mention']);
export const kindFieldLabel = (value: unknown) => (inboxKinds.has(String(value)) ? 'Hộp thư' : fieldLabel('kind'));
