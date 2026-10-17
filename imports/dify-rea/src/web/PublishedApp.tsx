import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import type { StudioApp } from '../contracts.ts';
import { api, streamRun } from './api.ts';
import { ErrorBox } from './ui.tsx';
import { TypedInputs } from './TypedInputs.tsx';
import { formInputValues } from './input-values.ts';

export function PublishedApp({ app, back }: { app: StudioApp; back: () => void }) {
  const [input, setInput] = useState('');
  const [answer, setAnswer] = useState('');
  const [conversation, setConversation] = useState('');
  const [history, setHistory] = useState<{ id: string; title: string }[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState<Record<string, string | number | boolean>>({});
  useEffect(() => {
    api<{ id: string; title: string }[]>(`/apps/${app.id}/conversations?mode=published`).then(setHistory).catch((e) => setError(e.message));
  }, [app.id, busy]);
  async function run(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true); setError(''); setAnswer('');
    try {
      const result = await streamRun(`/api/apps/${app.id}/run`,
        { input, inputs: formInputValues(app.published?.inputs ?? [], values), mode: 'published', ...(conversation ? { conversation_id: conversation } : {}) },
        (part) => setAnswer((current) => current + part));
      setAnswer(result.output);
      if (result.conversation_id) setConversation(result.conversation_id);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="page">
    <button onClick={back}>← Ứng dụng</button>
    <h1>{app.name}</h1><p>{app.description}</p>
    <div className="row">
      <button onClick={() => { setConversation(''); setAnswer(''); }}>Hội thoại mới</button>
      <select aria-label="Hội thoại" value={conversation} onChange={async (event) => {
        const id = event.target.value; setConversation(id);
        if (!id) { setAnswer(''); return; }
        try {
          const prior = await api<{ messages: { role: string; content: string }[] }>(`/apps/${app.id}/conversations/${id}`);
          setAnswer(prior.messages.filter((message) => message.role === 'assistant').at(-1)?.content ?? '');
        } catch (cause) { setError((cause as Error).message); }
      }}><option value="">Chọn hội thoại</option>{history.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select>
    </div>
    <form onSubmit={run}>
      <TypedInputs fields={app.published?.inputs ?? []} values={values} onChange={setValues} disabled={busy} />
      <label>Nội dung<textarea value={input} onChange={(event) => setInput(event.target.value)} rows={4} /></label>
      <button className="primary" disabled={busy}>Chạy ứng dụng</button>
    </form>
    <ErrorBox error={error} />
    {answer && <div className="card" aria-live="polite">{answer}</div>}
  </div>;
}
