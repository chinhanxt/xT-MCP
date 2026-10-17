// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors

/** Bốn nhóm Tầng 3 (D-UNI-T3-1); id trùng `notes.group` trong pack.json của các gói ứng dụng. */
export const GROUPS = [
  { id: 'dong-chay-hang-hoa', title: 'Dòng chảy Hàng hóa' },
  { id: 'thoi-gian-du-an', title: 'Thời gian & Dự án' },
  { id: 'trai-nghiem-thue-bao', title: 'Trải nghiệm & Thuê bao' },
  { id: 'tai-san-khong-gian', title: 'Tài sản & Không gian' },
] as const;
export const OTHER_GROUP = { id: 'khac', title: 'Nhóm khác' } as const;

export interface TierApp {
  id: string; title: string; group: string; status: 'installed' | 'disabled';
  ui: { kind: 'generic' | 'module'; path?: string };
  models: string[];
  builtin?: boolean;
}

type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }

/** Ứng dụng Kho hiện có của cổng; chỉ dùng khi máy chủ chưa công bố danh sách `apps`. */
export const BUILTIN_WMS: TierApp = {
  id: 'kho-hang', title: 'Kho hàng (WMS)', group: 'dong-chay-hang-hoa', status: 'installed',
  ui: { kind: 'module', path: '/waretrack/' }, models: [], builtin: true,
};

/**
 * Đọc `inspect_workspace.apps`. Trả null khi máy chủ không có trường này (chưa hỗ trợ cắm-rút);
 * mục sai dạng bị bỏ qua chứ không được đoán.
 */
export function parseApps(workspace: unknown): TierApp[] | null {
  const raw = obj(workspace).apps;
  if (!Array.isArray(raw)) return null;
  const apps: TierApp[] = [];
  for (const item of raw.map(obj)) {
    const ui = obj(item.ui);
    if (typeof item.id !== 'string' || !item.id || (item.status !== 'installed' && item.status !== 'disabled')) continue;
    if (ui.kind !== 'generic' && ui.kind !== 'module') continue;
    apps.push({
      id: item.id, title: String(item.title || item.id), group: String(item.group || ''), status: item.status,
      ui: { kind: ui.kind, ...(typeof ui.path === 'string' ? { path: ui.path } : {}) },
      models: Array.isArray(item.models) ? item.models.filter((m): m is string => typeof m === 'string') : [],
    });
  }
  return apps;
}

const ADMIN_ROLES = ['admin', 'it', 'director'];
/** Quản trị = `me.roles` có vai trò được đề xuất thay đổi (D-INT-7). Thiếu `roles` thì coi là nhân viên. */
export function isAdmin(workspace: unknown): boolean {
  const roles = obj(obj(workspace).me).roles;
  return Array.isArray(roles) && roles.some((role) => typeof role === 'string' && ADMIN_ROLES.includes(role));
}

/** Nhân viên không thấy ứng dụng đã tắt; quản trị thấy (hiển thị mờ). */
export function visibleApps(apps: TierApp[], admin: boolean): TierApp[] {
  return admin ? apps : apps.filter((app) => app.status === 'installed');
}

/** Chỉ các đường dẫn module cùng origin đã biết: `/waretrack/` hoặc `/apps/<id>/`. */
export function modulePath(path: string | undefined): string | null {
  if (!path) return null;
  return path === '/waretrack/' || /^\/apps\/[a-z0-9][a-z0-9_-]{0,62}\/$/.test(path) ? path : null;
}

/** Công cụ tạo đề xuất cài/tắt gói ứng dụng. Chưa có trong hợp đồng công cụ nên null: nút bị khóa, không giả lập thành công. */
export const LIFECYCLE_TOOL: string | null = null;
