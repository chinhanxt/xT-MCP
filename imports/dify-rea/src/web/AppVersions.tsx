// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import { api } from './api.ts';
import type { StudioApp } from '../contracts.ts';
import { ErrorBox } from './ui.tsx';
export function AppVersions({ app, onRestore }: { app: StudioApp; onRestore: (a: StudioApp) => void }) {
  const [versions, setVersions] = useState<{ version: number; name: string; created_at: string }[]>([]),
    [error, setError] = useState('');
  useEffect(() => {
    api(`/apps/${app.id}/versions`)
      .then(setVersions)
      .catch((e) => setError(e.message));
  }, [app.id, app.version]);
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <h1>Lịch sử phiên bản</h1>
          <p>Khôi phục vào bản nháp, chạy thử rồi publish thành một phiên bản mới.</p>
        </div>
        <button
          onClick={async () => {
            try {
              const manifest = await api(`/apps/${app.id}/export`);
              const url = URL.createObjectURL(new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' })),
                a = document.createElement('a');
              a.href = url;
              a.download = app.name.replace(/[^\p{L}\p{N}_-]/gu, '_') + '.json';
              a.click();
              setTimeout(() => URL.revokeObjectURL(url), 1000);
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          Xuất cấu hình ↓
        </button>
      </div>
      <ErrorBox error={error} />
      <div className="panel">
        {versions.map((v) => (
          <div className="document" key={v.version}>
            <div>
              <strong>
                Phiên bản {v.version} · {v.name}
              </strong>
              <small>{new Date(v.created_at).toLocaleString('vi-VN')}</small>
            </div>
            <button
              onClick={async () => {
                if (!confirm('Thay bản nháp hiện tại bằng phiên bản này?')) return;
                try {
                  onRestore(await api(`/apps/${app.id}/versions/${v.version}/restore`, 'POST', {}));
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              Khôi phục vào bản nháp
            </button>
          </div>
        ))}
        {!versions.length && <p>Publish lần đầu để lưu một phiên bản.</p>}
      </div>
    </div>
  );
}
