// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';
import { PortalAlert } from './PortalAlert';
import { classifyError, type ErrorKind } from './portal-state';
import { BUILTIN_WMS, GROUPS, LIFECYCLE_TOOL, OTHER_GROUP, isAdmin, modulePath, parseApps, visibleApps, type TierApp } from './tier3';

type Props = {
  mcp: McpConnection | null; tools: McpTool[];
  /** Hiển thị ứng dụng Kho dựng sẵn của cổng (biểu mẫu + WareTrack). */
  renderWarehouse: () => ReactNode;
  /** Hiển thị giao diện chung sinh từ describe_model cho ứng dụng không có module riêng. */
  renderGeneric?: (app: TierApp) => ReactNode;
};

/** Tầng 3: nền tảng Kinh doanh số, 4 nhóm; danh sách ứng dụng lấy từ inspect_workspace.apps. */
export function Tier3({ mcp, tools, renderWarehouse, renderGeneric }: Props) {
  const [workspace, setWorkspace] = useState<unknown>(null), [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(''), [kind, setKind] = useState<ErrorKind>('error'), [open, setOpen] = useState('');
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
  const current = apps.find((app) => app.id === open && app.status === 'installed');
  const groups = [...GROUPS, ...(apps.some((app) => !GROUPS.some((g) => g.id === app.group)) ? [OTHER_GROUP] : [])];

  function view(app: TierApp): ReactNode {
    if (app.ui.kind === 'module') {
      if (app.builtin || app.ui.path === '/waretrack/' || app.id === 'kho-hang') return renderWarehouse();
      const path = modulePath(app.ui.path);
      if (!path) return <PortalAlert text="Đường dẫn giao diện của ứng dụng không hợp lệ." kind="error" />;
      return <iframe className="portal-frame" title={`Ứng dụng ${app.title}`} src={`${path}?integrated=1`} loading="lazy" />;
    }
    return renderGeneric ? renderGeneric(app) : <p role="status">Giao diện chung của ứng dụng này chưa được hỗ trợ trên Desk.</p>;
  }

  return <section className="portal tier3" aria-label="Nền tảng Kinh doanh số"><h2 tabIndex={-1}>Nền tảng Kinh doanh số</h2>
    {!mcp && <p role="status">Đang kết nối xT…</p>}
    {mcp && !loaded && !error && <p role="status">Đang tải danh sách ứng dụng…</p>}
    <PortalAlert text={error} kind={kind} />
    {current ? <div className="tier3-app">
      <button onClick={() => setOpen('')}>← Danh sách ứng dụng</button>
      <h3>{current.title}</h3>
      {view(current)}
    </div> : loaded && <>
      {listed === null && <p role="note">Máy chủ chưa công bố danh sách ứng dụng cắm-rút; chỉ hiện ứng dụng Kho có sẵn của cổng.</p>}
      {groups.map((group) => {
        const items = apps.filter((app) => (group.id === OTHER_GROUP.id ? !GROUPS.some((g) => g.id === app.group) : app.group === group.id));
        return <section key={group.id} className="tier3-group" aria-labelledby={`tier3-${group.id}`}>
          <h3 id={`tier3-${group.id}`}>{group.title}</h3>
          {items.length === 0 ? <p role="status">Chưa có ứng dụng trong nhóm này.</p> : <ul className="portal-doclist">{items.map((app) =>
            <li key={app.id} className={app.status === 'disabled' ? 'tier3-disabled' : undefined}>
              <span><strong>{app.title}</strong> · {app.status === 'installed' ? 'Đã cài' : 'Đã tắt'}</span>
              {app.status === 'installed' ? <button onClick={() => setOpen(app.id)}>{`Mở ${app.title}`}</button> : <span aria-disabled="true">Không mở được khi đã tắt</span>}
              {admin && !app.builtin && <button disabled title={LIFECYCLE_TOOL ? undefined : 'Máy chủ chưa có công cụ đề xuất cài/tắt gói ứng dụng'}>{app.status === 'installed' ? 'Đề xuất tắt' : 'Đề xuất bật lại'}</button>}
            </li>)}</ul>}
        </section>;
      })}
      {admin && !LIFECYCLE_TOOL && !!listed && <p role="note">Đề xuất cài/tắt chưa dùng được: máy chủ chưa công bố công cụ tạo đề xuất cho gói ứng dụng, nên Desk không tạo đề xuất giả.</p>}
    </>}
  </section>;
}
