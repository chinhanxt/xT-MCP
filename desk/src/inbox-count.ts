// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Số đếm trên thanh bên (Quy trình) và chấm đỏ trên chuông, đọc từ đúng một lời gọi get_inbox({}) (mặc định status=open).
type Data = Record<string, unknown>;
const obj = (v: unknown): Data => (v && typeof v === 'object' && !Array.isArray(v) ? v as Data : {});
const count = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0);

export type InboxSummary = { approvals: number; waiting: boolean };

/** `counts.approval` nếu máy chủ trả; nếu không thì đếm mục kind=approval. Chấm đỏ khi còn bất kỳ mục mở nào. */
export function inboxSummary(value: Data): InboxSummary {
  const counts = obj(value.counts), items = Array.isArray(value.items) ? value.items.map(obj) : [];
  const approvals = typeof counts.approval === 'number' ? count(counts.approval) : items.filter(item => item.kind === 'approval').length;
  const waiting = approvals > 0 || ['task', 'notification', 'overdue'].some(key => count(counts[key]) > 0) || items.length > 0;
  return { approvals, waiting };
}

/** Nhãn hiển thị của huy hiệu (gọn, không tràn). */
export const badgeText = (n: number) => (n > 99 ? '99+' : String(n));
