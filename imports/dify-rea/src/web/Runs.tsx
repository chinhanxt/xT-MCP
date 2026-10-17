// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import type { RunResult } from '../contracts.ts';
import { api } from './api.ts';
import { Modal, ErrorBox, TraceList, Citations } from './ui.tsx';
export function Runs({ appId }: { appId?: string }) {
  const [runs, setRuns] = useState<any[]>([]),
    [selected, select] = useState<any>(null),
    [error, setError] = useState('');
  async function load() {
    try {
      setRuns(await api('/runs' + (appId ? `?app_id=${encodeURIComponent(appId)}` : '')));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, [appId]);
  const result = selected?.result ? (JSON.parse(selected.result) as RunResult) : null;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">QUAN SÁT</span>
          <h1>Lịch sử chạy</h1>
          <p>Kiểm tra kết quả, lỗi và các bước đã thực thi.</p>
        </div>
        <button onClick={load}>Làm mới ↻</button>
      </div>
      <ErrorBox error={error} />
      <div className="panel table-wrap">
        <table>
          <thead>
            <tr>
              <th>Ứng dụng / đầu vào</th>
              <th>Phiên bản</th>
              <th>Trạng thái</th>
              <th>Thời gian</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>
                  <button className="text-btn run-title" onClick={() => select(r)}>
                    {r.app_name && <strong>{r.app_name} · </strong>}
                    {String(r.input).slice(0, 100)}
                  </button>
                </td>
                <td>{r.mode === 'draft' ? 'Bản nháp' : 'Đã xuất bản'}</td>
                <td>
                  <span className={'pill ' + (r.status === 'success' ? 'green' : r.status === 'error' ? 'red' : '')}>
                    {r.status === 'success' ? 'Thành công' : r.status === 'error' ? 'Thất bại' : 'Đang chạy'}
                  </span>
                </td>
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
      {selected && (
        <Modal title="Chi tiết lượt chạy" close={() => select(null)}>
          <label>Đầu vào</label>
          <pre className="result-box">{selected.input}</pre>
          {selected.error && <ErrorBox error={selected.error} />}
          {result?.output && (
            <>
              <label>Kết quả</label>
              <pre className="result-box">{result.output}</pre>
            </>
          )}
          {result?.citations && <Citations items={result.citations} />}
          {result?.trace && <TraceList items={result.trace} />}
        </Modal>
      )}
    </>
  );
}
