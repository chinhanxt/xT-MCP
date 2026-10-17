import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { AppKind, Citation, Trace } from '../contracts.ts';
export const kindLabel: Record<AppKind, string> = { chat: 'Chatbot', workflow: 'Workflow', agent: 'Agent' };
export const kindIcon: Record<AppKind, string> = { chat: '◌', workflow: '⑂', agent: '✦' };
export function Modal({ title, children, close }: { title: string; children: ReactNode; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    ref.current?.showModal();
  }, []);
  return (
    <dialog ref={ref} onCancel={close}>
      <div className="row spread">
        <h2>{title}</h2>
        <button className="icon-btn" onClick={close} aria-label="Đóng">
          ×
        </button>
      </div>
      {children}
    </dialog>
  );
}

export function ErrorBox({ error }: { error: string }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}

export function Citations({ items, expanded = false }: { items: Citation[]; expanded?: boolean }) {
  return items.length ? (
    <details className="citations" open={expanded}>
      <summary>▤ {items.length} đoạn tham khảo</summary>
      {items.map((c, i) => (
        <article key={`${c.chunk_id}-${i}`}>
          <strong>
            [{i + 1}] {c.document_name}
          </strong>
          <small>Điểm: {c.score.toFixed(4)}</small>
          <p>{c.content}</p>
        </article>
      ))}
    </details>
  ) : null;
}

export function TraceList({ items }: { items: Trace[] }) {
  return (
    <div className="trace-list">
      {items.map((t, i) => (
        <details key={t.node_id + i} className={'trace ' + t.status}>
          <summary>
            <span>{t.status === 'success' ? '✓' : t.status === 'error' ? '×' : '–'}</span> {t.label}
            <small>
              {t.status === 'skipped'
                ? 'Bỏ qua nhánh'
                : `${t.duration_ms} ms${t.attempts && t.attempts > 1 ? ` · ${t.attempts} lần` : ''}${t.used_fallback ? ' · Giá trị thay thế' : ''}`}
            </small>
          </summary>
          <strong>Đầu vào</strong>
          <pre>{t.input || '—'}</pre>
          <strong>Kết quả</strong>
          <pre>{t.output || '—'}</pre>
        </details>
      ))}
    </div>
  );
}
