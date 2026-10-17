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
  kind: { approval: 'Chờ duyệt', task: 'Công việc', notification: 'Thông báo', mention: 'Nhắc đến', receive: 'Nhập kho', issue: 'Xuất kho' },
};
valueLabels.status = valueLabels.state;
export const fieldLabel = (key: string) => fieldLabels[key] || key;

const isoDate = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
/** Giá trị enum/thời gian đã biết -> tiếng Việt; còn lại trả nguyên văn. */
export function valueLabel(key: string, value: string): string {
  const mapped = valueLabels[key]?.[value];
  if (mapped) return mapped;
  if (isoDate.test(value)) { const date = new Date(value); if (!Number.isNaN(date.getTime())) return date.toLocaleString('vi-VN'); }
  return value;
}
export const primitiveLabels: Record<string, string> = { record: 'Bản ghi', request: 'Yêu cầu', document: 'Tài liệu', task: 'Công việc' };
export const primitiveLabel = (value: unknown) => primitiveLabels[String(value)] || String(value);

/** Chuỗi số -> nhóm nghìn kiểu vi-VN, không qua Number (không mất chính xác). */
export function groupDigits(value: string): string {
  const match = /^(-?)(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return value;
  const whole = match[2].replace(/\B(?=(\d{3})+(?!\d))/g, '.');
  return `${match[1]}${whole}${match[3] ? `,${match[3]}` : ''}`;
}
