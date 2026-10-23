// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { GenericApp } from './GenericApp';
import { Pill } from './ui';
import { Boxes, Clock, Handshake, LayoutGrid, Warehouse, type LucideIcon } from 'lucide-react';
import { BUILTIN_WMS, GROUPS, OTHER_GROUP, appViews, isAdmin, lifecycleCommand, modulePath, moduleScope, parseApps, tenantName, visibleApps, type AppView, type TierApp } from './tier3';

type Props = {
  mcp: McpConnection | null; tools: McpTool[];
  /** Hiển thị ứng dụng Kho dựng sẵn của cổng (biểu mẫu + WareTrack). */
  renderWarehouse: () => ReactNode;
};

const GROUP_ICON: Record<string, LucideIcon> = { 'hang-hoa': Warehouse, 'thoi-gian-du-an': Clock, 'trai-nghiem-thue-bao': Handshake, 'tai-san-khong-gian': Boxes };
const obj = (value: unknown): Record<string, unknown> => (value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {});

/** Tầng 3: nền tảng Kinh doanh số, 4 nhóm; danh sách ứng dụng lấy từ inspect_workspace.apps. */
export function Tier3({ mcp, tools, renderWarehouse }: Props) {
  const [workspace, setWorkspace] = useState<unknown>(null), [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [open, setOpen] = useState(''), [pick, setPick] = useState('');
  const live = useRef(true);
  const canInspect = tools.some((tool) => tool.name === 'inspect_workspace');

  useEffect(() => {
    live.current = true;
    if (!mcp) return;
    if (!canInspect) { setError('Không có công cụ inspect_workspace.'); setKind('forbidden'); return; }
    mcp.client.callTool({ name: 'inspect_workspace', arguments: {} }).then((result) => {
      if (live.current) { setWorkspace(readToolResult(result as ToolResult)); setLoaded(true); }
    }).catch((e: Error) => { if (live.current) { setError(e.message); setKind(classifyError(e)); } });
    return () => { live.current = false; };
  }, [mcp]); // eslint-disable-line

  const listed = loaded ? parseApps(workspace) : null;
  const admin = loaded && isAdmin(workspace);
  const all = !loaded ? [] : listed ?? [BUILTIN_WMS];
  const apps = visibleApps(all, admin);
  const current = apps.find((app) => app.id === open && app.status === 'enabled');
  const tenant = tenantName(workspace);
  const groups = [...GROUPS, ...(apps.some((app) => !GROUPS.some((g) => g.id === app.group)) ? [OTHER_GROUP] : [])];

  function view(app: TierApp, chosen: AppView): ReactNode {
    if (chosen.id === 'module') {
      if (app.builtin || chosen.path === '/waretrack/') return renderWarehouse();
      const path = modulePath(chosen.path);
      if (!path) return <PortalAlert text="Đường dẫn giao diện của ứng dụng không hợp lệ." kind="error" />;
      return <iframe className="portal-frame" data-xt-scope={moduleScope(path)} title={`Ứng dụng ${app.title}`} src={`${path}?integrated=1`} loading="lazy" />;
    }
    const models = Array.isArray(obj(workspace).models) ? obj(workspace).models as Record<string, unknown>[] : [];
    return <GenericApp key={app.id} mcp={mcp} tools={tools} app={app} models={models} />;
  }

  function body(app: TierApp): ReactNode {
    const views = appViews(app);
    if (!views.length) return <p role="status">Ứng dụng này không khai báo giao diện.</p>;
    const chosen = views.find((v) => v.id === pick) ?? views[0];
    return <>
      {views.length > 1 && <div className="portal-actions tabs" role="group" aria-label="Kiểu hiển thị">{views.map((v) =>
        <button key={v.id} aria-pressed={v.id === chosen.id} onClick={() => setPick(v.id)}>{v.label}</button>)}</div>}
      {view(app, chosen)}
    </>;
  }

  return <section className="portal tier3" aria-label="Nền tảng Kinh doanh số"><h2 tabIndex={-1}>Ứng dụng</h2>
    {!mcp && <p role="status">Đang kết nối xT…</p>}
    {mcp && !loaded && !error && <p role="status">Đang tải danh sách ứng dụng…</p>}
    <PortalAlert text={error} kind={kind} />
    {current ? <div className="tier3-app">
      <button className="ghost sm" onClick={() => { setOpen(''); setPick(''); }}>← Danh sách ứng dụng</button>
      <h3>{current.title}</h3>
      {body(current)}
    </div> : loaded && <>
      {listed === null && <p role="note" className="lead-sm">Máy chủ chưa công bố danh sách ứng dụng cắm-rút; chỉ hiện ứng dụng Kho có sẵn của cổng.</p>}
      {groups.map((group) => {
        const items = apps.filter((app) => (group.id === OTHER_GROUP.id ? !GROUPS.some((g) => g.id === app.group) : app.group === group.id));
        return <section key={group.id} className="tier3-group" aria-labelledby={`tier3-${group.id}`}>
          <h3 id={`tier3-${group.id}`}>{group.title}</h3>
          {items.length === 0 ? <p role="status">Chưa có ứng dụng trong nhóm này.</p> : <ul className="portal-doclist">{items.map((app) => {
            const Icon = GROUP_ICON[app.group] ?? LayoutGrid;
            return <li key={app.id} className={app.status === 'disabled' ? 'tier3-disabled' : undefined}>
              <div className="app-ic" aria-hidden="true"><Icon /></div>
              <span><strong>{app.title}</strong> {app.status === 'enabled' ? <Pill tone="ok">Đã bật</Pill> : <Pill tone="neutral">Đã tắt</Pill>}</span>
              {app.status === 'enabled' ? <button className="sm" onClick={() => { setPick(''); setOpen(app.id); }}>{`Mở ${app.title}`}</button> : <span className="faint small" aria-disabled="true">Không mở được khi đã tắt</span>}
              {admin && !app.builtin && <details className="tier3-lifecycle"><summary>{app.status === 'enabled' ? 'Cách tắt (vận hành)' : 'Cách bật lại (vận hành)'}</summary>
                <p>Bật/tắt ứng dụng do vận hành viên duyệt và chạy bằng CLI, không có công cụ MCP hay nút trên Desk. Chạy trên máy chủ:</p>
                <code>{lifecycleCommand(app, tenant)}</code></details>}
            </li>;
          })}</ul>}
        </section>;
      })}
    </>}
  </section>;
}
