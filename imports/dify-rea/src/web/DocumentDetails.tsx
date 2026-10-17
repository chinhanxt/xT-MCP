import { useEffect, useState } from 'react';
import { api } from './api.ts';
import { Modal, ErrorBox } from './ui.tsx';
export function DocumentDetails({ id, close, updated }: { id: string; close: () => void; updated: () => void }) {
  const [document, setDocument] = useState<{
      name: string;
      content: string;
      chunks: { id: string; content: string }[];
      chunk_count: number;
      status: string;
    } | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [offset, setOffset] = useState(0);
  async function load(next = offset) {
    const value = await api(`/documents/${id}?offset=${next}`);
    setDocument(value);
    setOffset(next);
  }
  useEffect(() => {
    load().catch((e) => setError(e.message));
  }, [id]);
  return (
    <Modal
      title="Tài liệu & các đoạn kiến thức"
      close={() => {
        if (!busy) close();
      }}
    >
      <ErrorBox error={error} />
      {document && (
        <>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError('');
              try {
                await api(`/documents/${id}`, 'PUT', { name: document.name, content: document.content });
                await load(0);
                updated();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              Tên tài liệu
              <input value={document.name} required maxLength={200} onChange={(e) => setDocument({ ...document, name: e.target.value })} />
            </label>
            <label>
              Nội dung
              <textarea
                rows={7}
                value={document.content}
                required
                maxLength={1000000}
                onChange={(e) => setDocument({ ...document, content: e.target.value })}
              />
            </label>
            <button disabled={busy} className="primary">
              {busy ? 'Đang lập chỉ mục…' : 'Lưu & lập chỉ mục lại'}
            </button>
          </form>
          <div className="section-title" style={{ marginTop: 24 }}>
            <h2>{document.chunk_count} đoạn đã lập chỉ mục</h2>
            <small>{document.status}</small>
          </div>
          {document.chunks.map((chunk, i) => (
            <article className="chunk-preview" key={chunk.id}>
              <small>Đoạn {offset + i + 1}</small>
              <pre>{chunk.content}</pre>
            </article>
          ))}
          <div className="row">
            <button disabled={busy || offset === 0} onClick={() => load(Math.max(0, offset - 50)).catch((e) => setError(e.message))}>
              ← Trước
            </button>
            <span>
              {document.chunk_count ? offset + 1 : 0}–{Math.min(offset + 50, document.chunk_count)}
            </span>
            <button
              disabled={busy || offset + 50 >= document.chunk_count}
              onClick={() => load(offset + 50).catch((e) => setError(e.message))}
            >
              Sau →
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
