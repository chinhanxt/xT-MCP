// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import type { StudioApp } from '../contracts.ts';
import { api } from './api.ts';
import { ErrorBox } from './ui.tsx';
export function AppApi({ app }: { app: StudioApp }) {
  const [keys, setKeys] = useState<{ id: string; created_at: string }[]>([]),
    [token, setToken] = useState(''),
    [error, setError] = useState(''),
    [share, setShare] = useState<{ id: string } | null>(null);
  async function load() {
    try {
      setKeys(await api(`/apps/${app.id}/keys`));
      setShare(await api(`/apps/${app.id}/share`));
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    load();
  }, [app.id]);
  const endpoint = `${location.origin}/v1/apps/${app.id}/run`;
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">PUBLISH & TÍCH HỢP</span>
          <h1>Đưa ứng dụng vào hệ thống của bạn</h1>
          <p>API luôn dùng cấu hình đã publish. Các thay đổi bản nháp có hiệu lực sau lần publish tiếp theo.</p>
        </div>
        <button
          className="primary"
          disabled={!app.published}
          onClick={async () => {
            try {
              const r = await api(`/apps/${app.id}/keys`, 'POST', {});
              setToken(r.token);
              await load();
            } catch (e) {
              setError((e as Error).message);
            }
          }}
        >
          + Tạo API key
        </button>
      </div>
      <ErrorBox error={error} />
      {!app.published && <div className="onboarding">Publish ứng dụng trước để sử dụng API.</div>}
      <div className="panel">
        <h2>Trang ứng dụng chia sẻ</h2>
        <p className="hint">
          Ai có link đều có thể chạy ứng dụng đã publish, đọc kiến thức đính kèm và gọi các tool đã chọn bằng kết nối của workspace. Mỗi
          trình duyệt có phiên hội thoại riêng.
        </p>
        {share ? (
          <>
            <a className="share-link" href={'/s/' + share.id} target="_blank" rel="noopener noreferrer">
              {location.origin + '/s/' + share.id} ↗
            </a>
            <button
              className="danger"
              onClick={async () => {
                if (!confirm('Thu hồi link và các phiên truy cập công khai?')) return;
                try {
                  await api(`/apps/${app.id}/share`, 'DELETE');
                  setShare(null);
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              Thu hồi link
            </button>
          </>
        ) : (
          <button
            disabled={!app.published}
            onClick={async () => {
              try {
                setShare(await api(`/apps/${app.id}/share`, 'POST', {}));
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Tạo link chia sẻ
          </button>
        )}
      </div>
      {token && (
        <div className="panel">
          <h2>API key vừa tạo</h2>
          <p className="hint">Lưu key này để dùng trong hệ thống của bạn. Key chỉ hiển thị tại lần tạo.</p>
          <pre className="result-box">{token}</pre>
          <button onClick={() => setToken('')}>Đã lưu, ẩn key</button>
        </div>
      )}
      <div className="panel" style={{ marginTop: 20 }}>
        <h2>Gọi ứng dụng</h2>
        <pre className="result-box">{`curl '${endpoint}' \\\n  -H 'Authorization: Bearer YOUR_API_KEY' \\\n  -H 'Content-Type: application/json' \\\n  -d '{"input":"Xin chào"}'`}</pre>
        <p className="hint">Kết quả có output, citations, trace và conversation_id. Gửi lại conversation_id để tiếp tục hội thoại.</p>
      </div>
      <div className="panel" style={{ marginTop: 20 }}>
        <h2>Key đang hoạt động ({keys.length})</h2>
        {keys.map((k) => (
          <div className="document" key={k.id}>
            <div>
              <code>{k.id}</code>
              <small>Tạo lúc {new Date(k.created_at).toLocaleString('vi-VN')}</small>
            </div>
            <button
              className="danger"
              onClick={async () => {
                if (!confirm('Thu hồi API key này?')) return;
                await api(`/apps/${app.id}/keys/${k.id}`, 'DELETE');
                setToken('');
                await load();
              }}
            >
              Thu hồi
            </button>
          </div>
        ))}
      </div>
    </>
  );
}
