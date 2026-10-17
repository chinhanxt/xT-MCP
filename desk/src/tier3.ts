// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors

/** Bốn nhóm Tầng 3 (D-UNI-T3-1); id trùng `app.group` trong `inspect_workspace.apps` (khối `app` của pack.json). */
export const GROUPS = [
  { id: 'hang-hoa', title: 'Dòng chảy Hàng hóa' },
  { id: 'thoi-gian-du-an', title: 'Thời gian & Dự án' },
  { id: 'trai-nghiem-thue-bao', title: 'Trải nghiệm & Thuê bao' },
  { id: 'tai-san-khong-gian', title: 'Tài sản & Không gian' },
] as const;
export const OTHER_GROUP = { id: 'khac', title: 'Nhóm khác' } as const;

export interface TierUi { kind: 'generic' | 'module'; path?: string }
export interface TierApp {
  id: string; pack: string; title: string; group: string; status: 'enabled' | 'disabled'; version: string;
  ui: TierUi[]; tools: string[]; models: string[];
  builtin?: boolean;
}

type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
function strings(value: unknown): string[] { return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : []; }

/** Ứng dụng Kho hiện có của cổng; chỉ dùng khi máy chủ chưa công bố danh sách `apps`. */
export const BUILTIN_WMS: TierApp = {
  id: 'kho-hang', pack: 'kho-hang', title: 'Kho hàng (WMS)', group: 'hang-hoa', status: 'enabled', version: '',
  ui: [{ kind: 'module', path: '/waretrack/' }], tools: [], models: [], builtin: true,
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
    if (typeof item.id !== 'string' || !item.id || (item.status !== 'enabled' && item.status !== 'disabled') || !Array.isArray(item.ui)) continue;
    const ui: TierUi[] = [];
    for (const entry of item.ui.map(obj)) {
      if (entry.kind !== 'generic' && entry.kind !== 'module') continue;
      ui.push({ kind: entry.kind, ...(typeof entry.path === 'string' ? { path: entry.path } : {}) });
    }
    if (!ui.length) continue;
    apps.push({
      id: item.id, pack: typeof item.pack === 'string' && item.pack ? item.pack : item.id, title: String(item.title || item.id),
      group: String(item.group || ''), status: item.status, version: typeof item.version === 'string' ? item.version : '',
      ui, tools: strings(item.tools), models: strings(item.models),
    });
  }
  return apps;
}

/** Quản trị = `me.roles` có vai trò `admin`. Thiếu `roles` thì coi là nhân viên. */
export function isAdmin(workspace: unknown): boolean {
  const roles = obj(obj(workspace).me).roles;
  return Array.isArray(roles) && roles.includes('admin');
}

/** Máy chủ chỉ gửi ứng dụng đã bật cho nhân viên; lọc thêm ở đây để không bao giờ lộ ứng dụng đã tắt. Quản trị thấy cả hai (đã tắt hiển thị mờ). */
export function visibleApps(apps: TierApp[], admin: boolean): TierApp[] {
  return admin ? apps : apps.filter((app) => app.status === 'enabled');
}

/** Chỉ các đường dẫn module cùng origin đã biết: `/waretrack/` hoặc `/apps/<id>/`. */
export function modulePath(path: string | undefined): string | null {
  if (!path) return null;
  return path === '/waretrack/' || /^\/apps\/[a-z0-9][a-z0-9_-]{0,62}\/$/.test(path) ? path : null;
}

/** Phạm vi cầu nối của khung (`data-xt-scope`): `/waretrack/` chỉ đọc số liệu kho; module khác nhận `module` = cổng không cấp công cụ nào. */
export function moduleScope(path: string): 'waretrack' | 'module' { return path === '/waretrack/' ? 'waretrack' : 'module'; }

/** Cách hiển thị của ứng dụng: module ưu tiên; có cả hai thì cho chọn "Sơ đồ 3D" / "Danh sách". */
export interface AppView { id: 'module' | 'generic'; label: string; path?: string }
export function appViews(app: TierApp): AppView[] {
  const module = app.ui.find((u) => u.kind === 'module');
  const views: AppView[] = [];
  if (module) views.push({ id: 'module', label: 'Sơ đồ 3D', ...(module.path ? { path: module.path } : {}) });
  if (app.ui.some((u) => u.kind === 'generic')) views.push({ id: 'generic', label: 'Danh sách' });
  return views;
}

/** Tên tenant cho lệnh CLI; thiếu thì để chỗ trống `<tenant>`. */
export function tenantName(workspace: unknown): string {
  const name = obj(obj(workspace).tenant).name;
  return typeof name === 'string' && name ? name : '<tenant>';
}

function shellWord(value: string): string { return /^[A-Za-z0-9_.@:/=-]+$/.test(value) ? value : `'${value.replace(/'/g, "'\\''")}'`; }

/** Lệnh vòng đời gói: chỉ có ở CLI của vận hành viên (người đề xuất + người duyệt), không có công cụ MCP tương ứng. */
export function lifecycleCommand(app: TierApp, tenant: string): string {
  const verb = app.status === 'enabled' ? 'disable' : 'enable';
  return `xt pack ${verb} --tenant ${shellWord(tenant)} ${shellWord(app.pack)} --proposed-by <người đề xuất> --approved-by <người duyệt>`;
}
