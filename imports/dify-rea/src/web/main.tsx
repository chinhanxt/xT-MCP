// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { FormEvent } from 'react';
import type { StudioApp, Model, Knowledge, McpConnector } from '../contracts.ts';
import { api } from './api.ts';
import { Modal, ErrorBox, kindIcon, kindLabel } from './ui.tsx';
import { AppEditor } from './AppEditor.tsx';
import { PublishedApp } from './PublishedApp.tsx';
import { Models } from './Models.tsx';
import { KnowledgePage } from './KnowledgePage.tsx';
import { Runs } from './Runs.tsx';
import { SharedApp } from './SharedApp.tsx';
import { ImportApp } from './ImportApp.tsx';
import { McpConnectors } from './McpConnectors.tsx';
import './style.css';
function Auth({ setup, done }: { setup: boolean; done: () => void }) {
  const [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api(setup ? '/setup' : '/login', 'POST', Object.fromEntries(new FormData(e.currentTarget)));
      done();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth">
      <div className="brand large">
        <span className="brand-mark">xT</span> Studio
      </div>
      <form className="auth-card" onSubmit={submit}>
        <span className="eyebrow">KHÔNG GIAN AI CỦA BẠN</span>
        <h1>{setup ? 'Bắt đầu với không gian làm việc mới' : 'Chào mừng trở lại'}</h1>
        <p>Kết nối mô hình, xây dựng ứng dụng và đưa kiến thức của bạn vào cuộc trò chuyện.</p>
        {setup && (
          <label>
            Tên không gian làm việc
            <input name="workspace_name" required placeholder="Công ty của bạn" maxLength={100} />
          </label>
        )}
        <label>
          Email
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label>
          Mật khẩu
          <input
            name="password"
            type="password"
            minLength={8}
            maxLength={200}
            autoComplete={setup ? 'new-password' : 'current-password'}
            required
          />
        </label>
        <ErrorBox error={error} />
        <button className="primary full" disabled={busy}>
          {busy ? 'Đang xử lý…' : setup ? 'Tạo không gian làm việc' : 'Đăng nhập'} →
        </button>
      </form>
    </div>
  );
}
function Studio() {
  const [session, setSession] = useState<any>(undefined),
    [page, setPage] = useState('apps'),
    [apps, setApps] = useState<StudioApp[]>([]),
    [models, setModels] = useState<Model[]>([]),
    [knowledge, setKnowledge] = useState<Knowledge[]>([]),
    [connectors, setConnectors] = useState<McpConnector[]>([]),
    [selected, select] = useState<StudioApp | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [create, setCreate] = useState(false),
    [importing, setImporting] = useState(false);
  async function loadSession() {
    setSession(await api('/session'));
  }
  async function reload() {
    const [a, m, k, c] = await Promise.all([
      api<StudioApp[]>('/apps'),
      session?.user?.role !== 'viewer' ? api<Model[]>('/models') : Promise.resolve([]),
      session?.user?.role !== 'viewer' ? api<Knowledge[]>('/knowledge') : Promise.resolve([]),
      session?.user?.role !== 'viewer' ? api<McpConnector[]>('/mcp') : Promise.resolve([]),
    ]);
    setApps(a);
    setModels(m);
    setKnowledge(k);
    setConnectors(c);
  }
  useEffect(() => {
    loadSession().catch((e) => setError(e.message));
  }, []);
  useEffect(() => {
    if (session?.user) reload().catch((e) => setError(e.message));
  }, [session]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  if (!session) return <div className="loading">{error || 'Đang mở xT Studio…'}</div>;
  if (!session.user) return session.integrated ? <div className="auth">Cần đăng nhập xT và quyền Studio.</div> : <Auth setup={session.needs_setup} done={loadSession} />;
  async function newApp(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    try {
      const app = await api<StudioApp>('/apps', 'POST', Object.fromEntries(new FormData(e.currentTarget)));
      setCreate(false);
      await reload();
      select(app);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="shell">
      <aside className="sidebar">
        <button
          className="brand"
          onClick={() => {
            select(null);
            setPage('apps');
          }}
        >
          <span className="brand-mark">xT</span> Studio
        </button>
        <div className="workspace">
          <span className="workspace-avatar">{session.user.workspace_name[0].toUpperCase()}</span>
          <div>
            <strong>{session.user.workspace_name}</strong>
            <small>Không gian của bạn</small>
          </div>
        </div>
        <div className="nav-label">XÂY DỰNG</div>
        <nav>
          {[
            ['apps', '▦', 'Ứng dụng'],
            ...(session.user.role === 'viewer' ? [] : [['knowledge', '▤', 'Kiến thức']]),
            ...(session.user.role === 'admin' || !session.integrated ? [['models', '◈', 'Mô hình AI'], ['mcp', '⇄', 'Kết nối MCP']] : []),
            ['runs', '◷', 'Lịch sử chạy'],
          ].map(([id, icon, label]) => (
            <button
              key={id}
              className={page === id ? 'active' : ''}
              onClick={() => {
                select(null);
                setPage(id);
                setError('');
              }}
            >
              <span>{icon}</span>
              {label}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="local-badge"><span /> {session.integrated ? 'Studio tích hợp xT · chia sẻ công khai và API key đã tắt' : 'Chạy trên máy của bạn'}</div>
          <small>{session.user.email}</small>
          {!session.integrated && <button
            className="text-btn"
            onClick={async () => {
              await api('/logout', 'POST', {});
              select(null);
              await loadSession();
            }}
          >
            Đăng xuất ↗
          </button>}
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            {session.user.workspace_name} <span>/</span>{' '}
            {selected
              ? selected.name
              : (
                  { apps: 'Ứng dụng', knowledge: 'Kiến thức', models: 'Mô hình AI', mcp: 'Kết nối MCP', runs: 'Lịch sử chạy' } as Record<
                    string,
                    string
                  >
                )[page]}
          </div>
          <span className="avatar">{session.user.email[0].toUpperCase()}</span>
        </header>
        <ErrorBox error={error} />
        {notice && (
          <div className="toast" role="status">
            ✓ {notice}
          </div>
        )}
        {selected && session.user.role === 'viewer' ? (
          <PublishedApp app={selected} back={() => select(null)} />
        ) : selected ? (
          <AppEditor
            integrated={session.integrated}
            key={selected.id}
            app={selected}
            models={models}
            knowledge={knowledge}
            connectors={connectors}
            back={() => {
              select(null);
              reload();
            }}
            update={(app) => {
              select(app);
              reload();
            }}
            notify={setNotice}
          />
        ) : (
          <div className="page">
            {page === 'apps' && (
              <>
                <div className="page-heading">
                  <div>
                    <span className="eyebrow">STUDIO</span>
                    <h1>Ý tưởng của bạn. Ứng dụng AI của bạn.</h1>
                    <p>Từ cuộc trò chuyện đầu tiên đến quy trình làm việc tự động.</p>
                  </div>
                  {session.user.role !== 'viewer' && <button onClick={() => setImporting(true)}>Nhập cấu hình ↑</button>}
                  {session.user.role !== 'viewer' && <button className="primary" onClick={() => setCreate(true)}>
                    + Tạo ứng dụng
                  </button>}
                </div>
                <div className="stats">
                  <div>
                    <strong>{apps.length}</strong> ứng dụng
                  </div>
                  <div>
                    <strong>{knowledge.length}</strong> kho kiến thức
                  </div>
                  <div>
                    <strong>{models.filter((m) => m.kind === 'chat').length}</strong> mô hình trò chuyện
                  </div>
                </div>
                {!models.length && (
                  <div className="onboarding">
                    <span className="onboarding-icon">◈</span>
                    <div>
                      <strong>Kết nối mô hình đầu tiên</strong>
                      <p>Dùng Ollama trên máy hoặc dịch vụ tương thích OpenAI để chạy ứng dụng.</p>
                    </div>
                    <button onClick={() => setPage('models')}>Thêm mô hình →</button>
                  </div>
                )}
                <div className="section-title">
                  <h2>Tất cả ứng dụng</h2>
                  <span>{apps.length} ứng dụng</span>
                </div>
                <div className="app-grid">
                  {session.user.role !== 'viewer' && <button className="new-card" onClick={() => setCreate(true)}>
                    <span>+</span>
                    <strong>Tạo từ đầu</strong>
                    <small>Hội thoại, quy trình hoặc trợ lý tự động</small>
                  </button>}
                  {apps.map((app) => (
                    <button className="app-card" key={app.id} onClick={() => select(app)}>
                      <div className="row spread">
                        <span className={'app-icon ' + app.kind}>{kindIcon[app.kind]}</span>
                        <span className={'pill ' + (app.published ? 'green' : '')}>
                          {app.published ? `Đã xuất bản · v${app.version}` : 'Bản nháp'}
                        </span>
                      </div>
                      <h3>{app.name}</h3>
                      <p>{app.description || 'Ứng dụng AI trong không gian làm việc của bạn.'}</p>
                      <div className="card-footer">
                        <span>{kindLabel[app.kind]}</span>
                        <small>{new Date(app.updated_at).toLocaleDateString('vi-VN')}</small>
                      </div>
                    </button>
                  ))}
                </div>
              </>
            )}
            {page === 'models' && <Models models={models} reload={reload} notify={setNotice} />}
            {page === 'knowledge' && <KnowledgePage items={knowledge} models={models} reload={reload} notify={setNotice} integrated={session.integrated} />}
            {page === 'mcp' && <McpConnectors items={connectors} reload={reload} notify={setNotice} />}
            {page === 'runs' && <Runs />}
          </div>
        )}
      </main>
      {importing && (
        <ImportApp
          models={models}
          knowledge={knowledge}
          connectors={connectors}
          close={() => setImporting(false)}
          done={(app) => {
            setImporting(false);
            select(app);
            reload();
          }}
        />
      )}
      {create && (
        <Modal title="Tạo ứng dụng" close={() => setCreate(false)}>
          <form onSubmit={newApp}>
            <label>
              Tên ứng dụng
              <input name="name" required maxLength={100} placeholder="Trợ lý của tôi" autoFocus />
            </label>
            <label>
              Mô tả
              <textarea name="description" rows={2} maxLength={1000} placeholder="Ứng dụng giúp bạn làm gì?" />
            </label>
            <label>
              Loại ứng dụng
              <select name="kind">
                <option value="chat">Hội thoại — trò chuyện cùng kiến thức</option>
                <option value="workflow">Quy trình — kéo thả các bước</option>
                <option value="agent">Trợ lý tự động — tự chọn công cụ để xử lý</option>
              </select>
            </label>
            <button className="primary full">Tạo ứng dụng →</button>
          </form>
        </Modal>
      )}
    </div>
  );
}
const sharedMatch = location.pathname.match(/^\/s\/([a-f0-9]{48})$/);
createRoot(document.getElementById('root')!).render(sharedMatch ? <SharedApp shareId={sharedMatch[1]} /> : <Studio />);
