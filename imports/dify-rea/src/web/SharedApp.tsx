// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { AppKind, ChatMessage, InputField } from '../contracts.ts';
import { streamRun } from './api.ts';
import { ErrorBox, Citations, TraceList, kindIcon } from './ui.tsx';
import { MessageContent } from './MessageContent.tsx';
import { formInputValues } from './input-values.ts';
import { TypedInputs } from './TypedInputs.tsx';
export function SharedApp({ shareId }: { shareId: string }) {
  const [app, setApp] = useState<{ name: string; description: string; kind: AppKind; version: number; inputs: InputField[] } | null>(null),
    [error, setError] = useState(''),
    [input, setInput] = useState(''),
    [inputs, setInputs] = useState<Record<string, string | number | boolean>>({}),
    [messages, setMessages] = useState<ChatMessage[]>([]),
    [busy, setBusy] = useState(false),
    [conversation, setConversation] = useState('');
  const controller = useRef<AbortController | null>(null),
    bottom = useRef<HTMLDivElement>(null);
  const endpoint = '/public/apps/' + shareId;
  useEffect(() => {
    fetch(endpoint)
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw new Error(value.error);
        setApp(value);
      })
      .catch((e) => setError(e.message));
    return () => controller.current?.abort();
  }, [endpoint]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'nearest' });
  }, [messages]);
  async function run(e: FormEvent) {
    e.preventDefault();
    if (!app || busy) return;
    setError('');
    setBusy(true);
    const value = input,
      before = messages;
    setMessages([...before, { role: 'user', content: value || JSON.stringify(inputs) }, { role: 'assistant', content: '' }]);
    setInput('');
    controller.current = new AbortController();
    try {
      const result = await streamRun(
        endpoint + '/run',
        { input: value, inputs: formInputValues(app.inputs, inputs), ...(conversation ? { conversation_id: conversation } : {}) },
        (part) =>
          setMessages((previous) => previous.map((m, i) => (i === previous.length - 1 ? { ...m, content: (m.content ?? '') + part } : m))),
        controller.current.signal,
      );
      setMessages((previous) =>
        previous.map((m, i) =>
          i === previous.length - 1 ? { role: 'assistant', content: result.output, citations: result.citations } : m,
        ),
      );
      if (result.conversation_id) setConversation(result.conversation_id);
    } catch (e) {
      setMessages(before);
      setInput(value);
      setError((e as Error).name === 'AbortError' ? 'Đã dừng yêu cầu.' : (e as Error).message);
    } finally {
      setBusy(false);
      controller.current = null;
    }
  }
  if (!app)
    return (
      <div className="shared-shell">
        <ErrorBox error={error} />
        {!error && 'Đang mở ứng dụng…'}
      </div>
    );
  return (
    <div className="shared-shell">
      <header className="shared-header">
        <div className="row">
          <span className={'app-icon ' + app.kind}>{kindIcon[app.kind]}</span>
          <div>
            <h2>{app.name}</h2>
            <small>
              {app.description} · v{app.version}
            </small>
          </div>
        </div>
        <button
          disabled={busy}
          onClick={() => {
            setMessages([]);
            setConversation('');
          }}
        >
          Cuộc trò chuyện mới ↻
        </button>
      </header>
      <ErrorBox error={error} />
      <section className="preview shared-preview">
        <div className="messages">
          {!messages.length && (
            <div className="chat-empty">
              <h2>{app.name}</h2>
              <p>Ứng dụng đã sẵn sàng. Gửi yêu cầu để bắt đầu.</p>
            </div>
          )}
          {messages.map((m, i) => (
            <article className={'message ' + m.role} key={i}>
              <small>{m.role === 'user' ? 'BẠN' : app.name}</small>
              <MessageContent content={m.content ?? ''} />
              {m.citations && <Citations items={m.citations} />}
            </article>
          ))}
          {busy && (
            <div className="thinking">
              <span />
              Đang xử lý…
            </div>
          )}
          <div ref={bottom} />
        </div>
        <form className="composer" onSubmit={run}>
          <TypedInputs fields={app.inputs} values={inputs} onChange={setInputs} disabled={busy} />
          <textarea
            aria-label="Nội dung"
            placeholder="Nhập yêu cầu…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            required={!app.inputs.length}
            maxLength={20000}
          />
          <div className="row spread">
            <small>Được tạo với xT Studio</small>
            {busy ? (
              <button type="button" onClick={() => controller.current?.abort()}>
                Dừng ■
              </button>
            ) : (
              <button className="primary">Gửi ↑</button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}
