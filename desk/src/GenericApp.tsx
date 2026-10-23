// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool } from './types';
import type { TierApp } from './tier3';
import { DocumentsPanel } from './DocumentsPanel';
import { RequestForm } from './RequestForm';
import { ReportPanel } from './ReportPanel';

type Data = Record<string, unknown>;
const LIST = ['record', 'request', 'document', 'task'];
const CREATE = ['record', 'request', 'document'];

/**
 * Giao diện chung cho ứng dụng không có module riêng: danh sách/chi tiết (search_records, get_record)
 * và tạo mới (describe_model -> create_record, thêm submit_request cho model là yêu cầu).
 * Chỉ các model mà manifest khai báo VÀ người dùng được thấy trong inspect_workspace.
 */
export function GenericApp({ mcp, tools, app, models }: { mcp: McpConnection | null; tools: McpTool[]; app: TierApp; models: Data[] }) {
  const [tab, setTab] = useState<'data' | 'report'>('data');
  if (!app.models.length) return <p role="status">Manifest của ứng dụng chưa khai báo model nào nên Desk không có gì để hiển thị.</p>;
  const visible = models.filter((row) => app.models.includes(String(row.id)));
  if (!visible.length) return <p role="status">Bạn chưa được cấp quyền xem model nào của ứng dụng này.</p>;
  const canReport = tools.some((tool) => tool.name === 'aggregate_records');
  return <div className="tier3-generic">
    {canReport && <div className="portal-actions tabs" role="group" aria-label="Mục của ứng dụng">
      <button aria-pressed={tab === 'data'} onClick={() => setTab('data')}>Dữ liệu</button>
      <button aria-pressed={tab === 'report'} onClick={() => setTab('report')}>Báo cáo</button>
    </div>}
    {canReport && tab === 'report' ? <ReportPanel mcp={mcp} tools={tools} models={visible} /> : <>
      <DocumentsPanel mcp={mcp} tools={tools} models={visible} primitives={LIST} noun="bản ghi" />
      <RequestForm mcp={mcp} tools={tools} models={visible} primitives={CREATE} heading="Tạo mới" generic />
    </>}
  </div>;
}
