// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { AppSettings } from './AppSettings.tsx';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppConfig, StudioApp, Model, Knowledge, McpConnector, RunResult, Citation, Trace } from '../contracts.ts';
import { api, streamRun } from './api.ts';
import { MessageContent } from './MessageContent.tsx';
import { formInputValues } from './input-values.ts';
import { TypedInputs } from './TypedInputs.tsx';
import { AppVersions } from './AppVersions.tsx';
import { WorkflowEditor } from './WorkflowEditor.tsx';
import { AppApi } from './AppApi.tsx';
import { Runs } from './Runs.tsx';
import { ErrorBox, Citations, TraceList, kindIcon, kindLabel } from './ui.tsx';
export function AppEditor({
  app,
  models,
  knowledge,
  connectors,
  back,
  update,
  notify,
  integrated = false,
}: {
  app: StudioApp;
  models: Model[];
  knowledge: Knowledge[];
  connectors: McpConnector[];
  back: () => void;
  update: (a: StudioApp) => void;
  notify: (s: string) => void;
  integrated?: boolean;
}) {
  const [config, setConfig] = useState<AppConfig>(app.config),
    [name, setName] = useState(app.name),
    [description, setDescription] = useState(app.description),
    [tab, setTab] = useState('build'),
    [error, setError] = useState(''),
    [saving, setSaving] = useState(false),
    [busy, setBusy] = useState(false),
    [mode, setMode] = useState<'draft' | 'published'>('draft'),
    [input, setInput] = useState(''),
    [inputValues, setInputValues] = useState<Record<string, string | number | boolean>>({}),
    [retryFrom, setRetryFrom] = useState<number | undefined>(),
    [messages, setMessages] = useState<{ role: string; content: string; citations?: Citation[] }[]>([]),
    [trace, setTrace] = useState<Trace[]>([]),
    [conversation, setConversation] = useState(''),
    [conversations, setConversations] = useState<{ id: string; title: string }[]>([]);
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);
  const dirty = name !== app.name || description !== app.description || JSON.stringify(config) !== JSON.stringify(app.config);
  useEffect(() => {
    setConfig(app.config);
    setName(app.name);
    setDescription(app.description);
    setMessages([]);
    setConversation('');
    setTrace([]);
  }, [app.id]);
  useEffect(() => {
    if (app.kind !== 'workflow')
      api(`/apps/${app.id}/conversations?mode=${mode}`)
        .then(setConversations)
        .catch((e) => setError(e.message));
  }, [app.id, busy, mode]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, [messages, busy]);
  useEffect(() => () => controller.current?.abort(), []);
  const patch = (values: Partial<AppConfig>) => setConfig((c) => ({ ...c, ...values }));
  async function save() {
    setSaving(true);
    setError('');
    try {
      const saved = await api<StudioApp>(`/apps/${app.id}`, 'PUT', { name, description, config });
      setConfig(saved.config);
      update(saved);
      return saved;
    } finally {
      setSaving(false);
    }
  }
  async function run(e: FormEvent) {
    e.preventDefault();
    const activeInputs = (mode === 'published' ? app.published?.inputs : config.inputs) ?? [];
    if ((!input.trim() && !(activeInputs.length && app.kind === 'workflow')) || busy) return;
    const value = input;
    setError('');
    setBusy(true);
    setTrace([]);
    const before = messages;
    try {
      if (mode === 'draft' && dirty) await save();
      const base = retryFrom === undefined ? messages : messages.slice(0, retryFrom);
      setMessages([...base, { role: 'user', content: value || JSON.stringify(inputValues) }, { role: 'assistant', content: '' }]);
      setInput('');
      controller.current = new AbortController();
      const result = await streamRun(
        `/api/apps/${app.id}/run`,
        {
          input: value,
          inputs: formInputValues(app.kind === 'workflow' ? activeInputs : [], inputValues),
          mode,
          ...(conversation ? { conversation_id: conversation } : {}),
          ...(retryFrom !== undefined ? { retry_from: retryFrom } : {}),
        },
        (part) => setMessages((previous) => previous.map((m, i) => (i === previous.length - 1 ? { ...m, content: m.content + part } : m))),
        controller.current.signal,
      );
      setMessages((prev) =>
        prev.map((m, i) => (i === prev.length - 1 ? { role: 'assistant', content: result.output, citations: result.citations } : m)),
      );
      setRetryFrom(undefined);
      setTrace(result.trace);
      if (result.conversation_id) setConversation(result.conversation_id);
    } catch (e) {
      setMessages(before);
      setInput(value);
      if ((e as Error).name !== 'AbortError') setError((e as Error).message);
      else setError('Đã dừng yêu cầu.');
      setTrace((e as { trace?: Trace[] }).trace ?? []);
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }
  function reset() {
    setMessages([]);
    setTrace([]);
    setConversation('');
    setRetryFrom(undefined);
    setInputValues({});
  }
  const promptConfig = (
    <AppSettings
      app={app}
      config={config}
      models={models}
      knowledge={knowledge}
      connectors={connectors}
      name={name}
      description={description}
      patch={patch}
      setName={setName}
      setDescription={setDescription}
      back={back}
      onError={setError}
    />
  );

  return (
    <div className="editor">
      <div className="editor-header">
        <div className="row">
          <button
            className="icon-btn"
            onClick={() => {
              if (!dirty || confirm('Rời ứng dụng và bỏ các thay đổi chưa lưu?')) back();
            }}
            aria-label="Quay lại"
          >
            ←
          </button>
          <span className={'app-icon small ' + app.kind}>{kindIcon[app.kind]}</span>
          <div>
            <h2>{name}</h2>
            <small>
              {kindLabel[app.kind]} · {dirty ? 'Có thay đổi chưa lưu' : 'Đã lưu'}
            </small>
          </div>
        </div>
        <div className="row">
          <button
            disabled={saving || busy}
            onClick={() =>
              save()
                .then(() => notify('Đã lưu bản nháp'))
                .catch((e) => setError(e.message))
            }
          >
            {saving ? 'Đang lưu…' : 'Lưu bản nháp'}
          </button>
          <button
            className="primary"
            disabled={saving || busy}
            onClick={async () => {
              try {
                await save();
                const published = await api<StudioApp>(`/apps/${app.id}/publish`, 'POST', {});
                update(published);
                notify(`Đã publish phiên bản ${published.version}`);
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            Publish ↗
          </button>
        </div>
      </div>
      <div className="editor-tabs">
        <button className={tab === 'build' ? 'active' : ''} onClick={() => setTab('build')}>
          Xây dựng & thử nghiệm
        </button>
        {app.kind === 'workflow' && (
          <button className={tab === 'settings' ? 'active' : ''} onClick={() => setTab('settings')}>
            Model & hướng dẫn
          </button>
        )}
        <button className={tab === 'logs' ? 'active' : ''} onClick={() => setTab('logs')}>
          Lịch sử chạy
        </button>
        {!integrated && <button className={tab === 'api' ? 'active' : ''} onClick={() => setTab('api')}>
          API & tích hợp
        </button>}
        <button className={tab === 'versions' ? 'active' : ''} onClick={() => setTab('versions')}>
          Phiên bản & xuất
        </button>
        <span className="pill">{app.published ? `Đã publish · v${app.version}` : 'Chưa publish'}</span>
      </div>
      <ErrorBox error={error} />
      {tab === 'versions' ? (
        <AppVersions
          app={app}
          onRestore={(restored) => {
            setConfig(restored.config);
            setName(restored.name);
            setDescription(restored.description);
            update(restored);
            notify('Đã khôi phục vào bản nháp');
          }}
        />
      ) : tab === 'api' ? (
        <div className="page">
          <AppApi app={app} />
        </div>
      ) : tab === 'logs' ? (
        <div className="page">
          <Runs appId={app.id} />
        </div>
      ) : tab === 'settings' ? (
        <div className="settings-panel">{promptConfig}</div>
      ) : (
        <div className={'editor-body ' + (app.kind === 'workflow' ? 'workflow' : '')}>
          {app.kind === 'workflow' ? (
            <WorkflowEditor graph={config.graph} onChange={(graph) => patch({ graph })} models={models} knowledge={knowledge} />
          ) : (
            <aside className="config-panel">
              <span className="eyebrow">CẤU HÌNH ỨNG DỤNG</span>
              {promptConfig}
            </aside>
          )}
          <section className="preview">
            <div className="preview-header">
              <strong>{app.kind === 'workflow' ? 'Chạy thử' : 'Xem trước'}</strong>
              <select
                aria-label="Phiên bản chạy"
                value={mode}
                disabled={busy}
                onChange={(e) => {
                  setMode(e.target.value as 'draft' | 'published');
                  reset();
                }}
              >
                <option value="draft">Bản nháp</option>
                <option value="published" disabled={!app.published}>
                  Bản publish · v{app.version}
                </option>
              </select>
              <button className="icon-btn" onClick={reset} disabled={busy} aria-label="Cuộc trò chuyện mới">
                ↻
              </button>
            </div>
            {app.kind !== 'workflow' && conversations.length > 0 && (
              <select
                className="conversation-select"
                aria-label="Lịch sử hội thoại"
                value={conversation}
                disabled={busy}
                onChange={async (e) => {
                  setConversation(e.target.value);
                  if (!e.target.value) {
                    setMessages([]);
                    return;
                  }
                  const r = await api(`/apps/${app.id}/conversations/${e.target.value}`);
                  setMessages(r.messages);
                  setTrace([]);
                }}
              >
                <option value="">Cuộc trò chuyện mới</option>
                {conversations.map((c) => (
                  <option value={c.id} key={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
            )}
            <div className="messages">
              {!messages.length && (
                <div className="chat-empty">
                  <span>{kindIcon[app.kind]}</span>
                  <h2>{app.kind === 'workflow' ? 'Đưa quy trình vào hoạt động' : 'Bắt đầu cuộc trò chuyện'}</h2>
                  <p>
                    {app.kind === 'workflow'
                      ? 'Nhập dữ liệu, chạy và xem kết quả của từng bước.'
                      : 'Nội dung trả lời sẽ dùng model và kiến thức bạn đã cấu hình.'}
                  </p>
                </div>
              )}
              {messages.map((m, i) => (
                <article key={i} className={'message ' + m.role}>
                  <small>{m.role === 'user' ? 'BẠN' : name}</small>
                  <MessageContent content={m.content} />
                  {m.citations && <Citations items={m.citations} />}
                  {!busy && conversation && m.role === 'user' && (
                    <button
                      className="text-btn message-action"
                      onClick={() => {
                        setRetryFrom(i);
                        setInput(m.content);
                      }}
                    >
                      Sửa & chạy lại
                    </button>
                  )}
                </article>
              ))}
              {busy && (
                <div className="thinking">
                  <span /> Đang xử lý yêu cầu…
                </div>
              )}
              {trace.length > 0 && (
                <details className="execution" open={app.kind === 'workflow'}>
                  <summary>Các bước thực thi ({trace.length})</summary>
                  <TraceList items={trace} />
                </details>
              )}
              <div ref={bottom} />
            </div>
            <form className="composer" onSubmit={run}>
              {app.kind === 'workflow' && (
                <TypedInputs
                  fields={(mode === 'published' ? app.published?.inputs : config.inputs) ?? []}
                  values={inputValues}
                  onChange={setInputValues}
                  disabled={busy}
                />
              )}
              {retryFrom !== undefined && (
                <div className="row spread">
                  <small>Đang sửa tin nhắn #{retryFrom + 1}; các phản hồi sau đó sẽ được thay thế khi chạy thành công.</small>
                  <button type="button" onClick={() => setRetryFrom(undefined)}>
                    Hủy sửa
                  </button>
                </div>
              )}
              <textarea
                aria-label="Nội dung chạy thử"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={app.kind === 'workflow' ? 'Nhập dữ liệu đầu vào…' : 'Nhập câu hỏi của bạn…'}
                rows={2}
                maxLength={20000}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    if (!busy) e.currentTarget.form?.requestSubmit();
                  }
                }}
              />
              <div className="row spread">
                <small>{mode === 'published' ? `Đang dùng phiên bản ${app.version} đã publish` : 'Chạy cấu hình bản nháp'}</small>
                {busy ? (
                  <button type="button" onClick={() => controller.current?.abort()}>
                    Dừng ■
                  </button>
                ) : (
                  <button
                    className="primary"
                    disabled={(!input.trim() && !(mode === 'published' ? app.published?.inputs : config.inputs)?.length) || saving}
                  >
                    {app.kind === 'workflow' ? 'Chạy' : 'Gửi'} ↑
                  </button>
                )}
              </div>
            </form>
          </section>
        </div>
      )}
    </div>
  );
}
