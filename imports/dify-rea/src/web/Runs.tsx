// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import type { RunResult } from '../contracts.ts';
import { api } from './api.ts';
import { Modal, ErrorBox, TraceList, Citations } from './ui.tsx';
const triggerLabel: Record<string, string> = { manual: 'Thủ công', api: 'API', schedule: 'Lịch', share: 'Chia sẻ' };
const statusLabel: Record<string, string> = { success: 'Thành công', error: 'Thất bại', waiting: 'Chờ duyệt', running: 'Đang chạy' };
const statusClass: Record<string, string> = { success: 'green', error: 'red', waiting: 'amber' };
export function Runs({ appId }: { appId?: string }) {
  const [runs, setRuns] = useState<any[]>([]),
    [selected, select] = useState<any>(null),
    [error, setError] = useState(''),
    [resuming, setResuming] = useState(false),
    [resumeNote, setResumeNote] = useState('');
  const [status, setStatus] = useState(''),
    [trigger, setTrigger] = useState(''),
    [cursor, setCursor] = useState<string | null>(null);
  async function load(more = false) {
    try {
      if (!appId) return setRuns(await api('/runs'));
      const q = new URLSearchParams({ limit: '50' });
      if (status) q.set('status', status);
      if (trigger) q.set('trigger', trigger);
      if (more && cursor) q.set('cursor', cursor);
      const page = await api(`/apps/${encodeURIComponent(appId)}/runs?${q}`);
      setRuns(more ? [...runs, ...page.items] : page.items);
      setCursor(page.next_cursor);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, [appId, status, trigger]);
  async function open(id: string) {
    setResumeNote('');
    try {
      select(await api('/runs/' + encodeURIComponent(id)));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function resume(id: string) {
    setResuming(true);
    setResumeNote('');
    try {
      const done = (await api('/runs/' + encodeURIComponent(id) + '/resume', 'POST')) as RunResult;
      if (done.status === 'waiting') setResumeNote(done.output);
    } catch (e) {
      setResumeNote((e as Error).message);
    } finally {
      setResuming(false);
    }
    await open(id).catch(() => {});
    load();
  }
  const result = selected?.result as RunResult | null;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">QUAN SÁT</span>
          <h1>Lịch sử chạy</h1>
          <p>Kiểm tra kết quả, lỗi và các bước đã thực thi.</p>
        </div>
        <button onClick={() => load()}>Làm mới ↻</button>
      </div>
      {appId && (
        <div className="actions">
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">Mọi trạng thái</option>
            <option value="success">Thành công</option>
            <option value="error">Thất bại</option>
            <option value="running">Đang chạy</option>
            <option value="waiting">Chờ duyệt</option>
          </select>
          <select value={trigger} onChange={(e) => setTrigger(e.target.value)}>
            <option value="">Mọi nguồn</option>
            {Object.entries(triggerLabel).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
      )}
      <ErrorBox error={error} />
      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ứng dụng / đầu vào</th>
              <th>Nguồn</th>
              <th>Phiên bản</th>
              <th>Trạng thái</th>
              <th>Thời lượng</th>
              <th>Thời gian</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>
                  <button className="text-btn run-title" onClick={() => open(r.id)}>
                    {r.app_name && <strong>{r.app_name} · </strong>}
                    {String(r.input).slice(0, 100)}
                  </button>
                </td>
                <td>{triggerLabel[r.trigger] ?? r.trigger}</td>
                <td>{r.mode === 'draft' ? 'Bản nháp' : 'Đã xuất bản'}</td>
                <td>
                  <span className={'pill ' + (statusClass[r.status] ?? '')}>{statusLabel[r.status] ?? r.status}</span>
                </td>
                <td>{r.duration_ms == null ? '—' : r.duration_ms >= 1000 ? (r.duration_ms / 1000).toFixed(1) + ' s' : r.duration_ms + ' ms'}</td>
                <td>{new Date(r.created_at).toLocaleString('vi-VN')}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!runs.length && (
          <div className="empty">
            <h2>Chưa có lượt chạy</h2>
            <p>Chạy thử một ứng dụng để thấy lịch sử tại đây.</p>
          </div>
        )}
      </div>
      {appId && cursor && <button onClick={() => load(true)}>Tải thêm</button>}
      {selected && (
        <Modal title="Chi tiết lượt chạy" close={() => select(null)}>
          <label>Đầu vào</label>
          <pre className="result-box">{selected.input}</pre>
          {selected.error && <ErrorBox error={selected.error} />}
          {selected.status === 'waiting' && (
            <div className="actions">
              <span className="pill amber">Chờ duyệt{result?.request_ref ? ` · ${result.request_ref}` : ''}</span>
              <button disabled={resuming} onClick={() => resume(selected.id)}>
                {resuming ? 'Đang kiểm tra…' : 'Kiểm tra và chạy tiếp'}
              </button>
            </div>
          )}
          {resumeNote && <p className="hint">{resumeNote}</p>}
          {result?.output && (
            <>
              <label>Kết quả</label>
              <pre className="result-box">{result.output}</pre>
            </>
          )}
          {result?.citations && <Citations items={result.citations} />}
          {selected.trace?.length > 0 && (
            <>
              <label>Các bước ({triggerLabel[selected.trigger] ?? selected.trigger}
                {selected.duration_ms != null ? ` · ${selected.duration_ms} ms` : ''})</label>
              <TraceList items={selected.trace} />
            </>
          )}
        </Modal>
      )}
    </>
  );
}
