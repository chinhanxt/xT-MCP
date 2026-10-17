// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Model } from '../contracts.ts';
import { api } from './api.ts';
import { Modal, ErrorBox } from './ui.tsx';
export function Models({ models, reload, notify }: { models: Model[]; reload: () => Promise<void>; notify: (s: string) => void }) {
  const [edit, setEdit] = useState<Model | 'new' | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState('');
  const current = edit && edit !== 'new' ? edit : null;
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy('save');
    setError('');
    const data = Object.fromEntries(new FormData(e.currentTarget));
    if (current && !data.api_key) delete data.api_key;
    try {
      await api(current ? `/models/${current.id}` : '/models', current ? 'PUT' : 'POST', data);
      setEdit(null);
      await reload();
      notify('Đã lưu mô hình');
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy('');
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">KẾT NỐI</span>
          <h1>Mô hình AI</h1>
          <p>Chọn bộ não cho ứng dụng. Cấu hình được dùng trong không gian này.</p>
        </div>
        <button
          className="primary"
          onClick={() => {
            setError('');
            setEdit('new');
          }}
        >
          + Thêm mô hình
        </button>
      </div>
      <ErrorBox error={error} />
      <div className="model-grid">
        {models.map((m) => (
          <article className="model-card" key={m.id}>
            <div className="row spread">
              <span className="app-icon model">◈</span>
              <span className="pill">{m.kind === 'chat' ? 'Trò chuyện' : 'Nhúng văn bản'}</span>
            </div>
            <h3>{m.name}</h3>
            <p>{m.model}</p>
            <code>{m.base_url}</code>
            <div className="row actions">
              <button
                onClick={() => {
                  setEdit(m);
                  setError('');
                }}
              >
                Chỉnh sửa
              </button>
              <button
                disabled={!!busy}
                onClick={async () => {
                  setBusy(m.id);
                  setError('');
                  try {
                    const r = await api(`/models/${m.id}/test`, 'POST', {});
                    notify(r.dimensions ? `Kết nối tốt · ${r.dimensions} chiều` : 'Mô hình đã phản hồi thành công');
                  } catch (e) {
                    setError((e as Error).message);
                  } finally {
                    setBusy('');
                  }
                }}
              >
                {busy === m.id ? 'Đang thử…' : 'Thử kết nối'}
              </button>
              <button
                className="danger text-btn"
                onClick={async () => {
                  if (!confirm('Xóa cấu hình mô hình này?')) return;
                  try {
                    await api(`/models/${m.id}`, 'DELETE');
                    await reload();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Xóa
              </button>
            </div>
          </article>
        ))}
      </div>
      {!models.length && (
        <div className="empty">
          <span>◈</span>
          <h2>Thêm mô hình để bắt đầu</h2>
          <p>
            Ollama: URL http://127.0.0.1:11434, tên mô hình ví dụ qwen3.5:4b.
            <br />
            OpenAI: URL https://api.openai.com/v1, tên mô hình theo tài khoản của bạn.
          </p>
        </div>
      )}
      {edit && (
        <Modal title={current ? 'Chỉnh sửa mô hình' : 'Thêm mô hình'} close={() => setEdit(null)}>
          <form key={current?.id ?? 'new'} onSubmit={save}>
            <label>
              Tên hiển thị
              <input name="name" required defaultValue={current?.name} maxLength={100} autoFocus />
            </label>
            <div className="form-grid">
              <label>
                Provider
                <select name="provider" defaultValue={current?.provider ?? 'ollama'}>
                  <option value="ollama">Ollama</option>
                  <option value="openai">OpenAI compatible</option>
                  <option value="anthropic">Anthropic / Claude native</option>
                </select>
              </label>
              <label>
                Loại
                <select name="kind" defaultValue={current?.kind ?? 'chat'}>
                  <option value="chat">Trò chuyện</option>
                  <option value="embedding">Nhúng văn bản</option>
                </select>
              </label>
            </div>
            <label>
              Base URL
              <input name="base_url" type="url" required defaultValue={current?.base_url ?? 'http://127.0.0.1:11434'} />
              <small>Claude native: https://api.anthropic.com/v1</small>
            </label>
            <label>
              Tên mô hình
              <input name="model" required defaultValue={current?.model ?? 'qwen3.5:4b'} />
            </label>
            <label>
              API key
              <input
                name="api_key"
                type="password"
                autoComplete="off"
                placeholder={current?.has_key ? 'Đã lưu · để trống để giữ nguyên' : 'Để trống nếu không cần'}
              />
            </label>
            <ErrorBox error={error} />
            <button className="primary full" disabled={!!busy}>
              {busy === 'save' ? 'Đang lưu…' : 'Lưu mô hình'}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
