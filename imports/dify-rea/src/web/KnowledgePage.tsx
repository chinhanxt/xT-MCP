// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useState } from 'react';
import type { FormEvent } from 'react';
import type { Model, Knowledge, Citation } from '../contracts.ts';
import { api } from './api.ts';
import { Modal, ErrorBox, Citations } from './ui.tsx';
import { DocumentDetails } from './DocumentDetails.tsx';
interface DocumentItem {
  id: string;
  name: string;
  status: string;
  enabled: number;
  chunk_count: number;
  error?: string;
}
export function KnowledgePage({
  items,
  models,
  reload,
  notify,
  integrated = false,
}: {
  integrated?: boolean;
  items: Knowledge[];
  models: Model[];
  reload: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [create, setCreate] = useState(false),
    [selected, select] = useState<Knowledge | null>(null),
    [documents, setDocuments] = useState<DocumentItem[]>([]),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [hits, setHits] = useState<Citation[]>([]),
    [upload, setUpload] = useState(false),
    [documentId, setDocumentId] = useState('');
  async function loadDocs(id: string) {
    setDocuments(await api(`/knowledge/${id}/documents`));
  }
  async function choose(k: Knowledge) {
    select(k);
    setHits([]);
    setError('');
    await loadDocs(k.id);
  }
  async function createKnowledge(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setError('');
    try {
      await api('/knowledge', 'POST', Object.fromEntries(new FormData(e.currentTarget)));
      setCreate(false);
      await reload();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function addDocument(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!selected) return;
    const data = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    try {
      await api(
        `/knowledge/${selected.id}/documents`,
        'POST',
        integrated ? { xt_ref: data.get('xt_ref') } : { name: data.get('name'), content: data.get('content') },
      );
      setUpload(false);
      await loadDocs(selected.id);
      await reload();
      notify('Tài liệu đã được lập chỉ mục');
    } catch (e) {
      setError((e as Error).message);
      await loadDocs(selected.id);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <div className="page-heading">
        <div>
          <span className="eyebrow">DỮ LIỆU CỦA BẠN</span>
          <h1>{selected?.name ?? 'Kiến thức'}</h1>
          <p>
            {selected
              ? selected.description || 'Tài liệu và thử nghiệm truy xuất.'
              : 'Đưa tài liệu vào AI, giữ lại nguồn của từng câu trả lời.'}
          </p>
        </div>
        <div className="row">
          {selected && <button onClick={() => select(null)}>← Tất cả kho</button>}
          <button className="primary" onClick={() => (selected ? setUpload(true) : setCreate(true))}>
            + {selected ? 'Thêm tài liệu' : 'Tạo kho kiến thức'}
          </button>
        </div>
      </div>
      <ErrorBox error={error} />
      {!selected ? (
        <div className="model-grid">
          {items.map((k) => (
            <article className="model-card" key={k.id}>
              <button className="bare-card" onClick={() => choose(k)}>
                <span className="app-icon knowledge">▤</span>
                <h3>{k.name}</h3>
                <p>{k.description || 'Kho tài liệu của không gian làm việc'}</p>
                <div className="card-footer">
                  <span>
                    {k.document_count} tài liệu · {k.chunk_count} đoạn
                  </span>
                  <span>{k.embedding_model_id ? 'Vector' : 'Toàn văn'}</span>
                </div>
              </button>
              <button
                className="danger text-btn"
                onClick={async () => {
                  if (!confirm('Xóa kho và tất cả tài liệu?')) return;
                  try {
                    await api(`/knowledge/${k.id}`, 'DELETE');
                    await reload();
                  } catch (e) {
                    setError((e as Error).message);
                  }
                }}
              >
                Xóa kho
              </button>
            </article>
          ))}
        </div>
      ) : (
        <div className="knowledge-layout">
          <section className="panel">
            <h2>Tài liệu ({documents.length})</h2>
            {documents.map((d) => (
              <div className="document" key={d.id}>
                <span className="doc-icon">▤</span>
                <div>
                  <button className="text-btn" onClick={() => setDocumentId(d.id)}>
                    {d.name} ↗
                  </button>
                  <small>
                    {d.chunk_count} đoạn · {d.status === 'ready' ? 'Đã lập chỉ mục' : d.status === 'error' ? d.error : 'Đang lập chỉ mục'}
                  </small>
                </div>
                <button
                  onClick={async () => {
                    await api(`/documents/${d.id}`, 'PATCH', { enabled: !d.enabled });
                    await loadDocs(selected.id);
                  }}
                >
                  {d.enabled ? 'Đang dùng' : 'Đã tắt'}
                </button>
                <button
                  className="danger text-btn"
                  onClick={async () => {
                    if (!confirm('Xóa tài liệu này?')) return;
                    await api(`/documents/${d.id}`, 'DELETE');
                    await loadDocs(selected.id);
                    await reload();
                  }}
                >
                  Xóa
                </button>
              </div>
            ))}
            {!documents.length && <p className="muted">Thêm tài liệu văn bản để bắt đầu.</p>}
          </section>
          <section className="panel">
            <h2>Thử truy xuất</h2>
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                setBusy(true);
                setError('');
                try {
                  setHits(await api(`/knowledge/${selected.id}/search`, 'POST', Object.fromEntries(new FormData(e.currentTarget))));
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                Câu hỏi
                <input name="query" required placeholder="Bạn muốn tìm điều gì?" />
              </label>
              <button disabled={busy}>{busy ? 'Đang tìm…' : 'Tìm trong tài liệu →'}</button>
            </form>
            <Citations items={hits} expanded />
          </section>
        </div>
      )}
      {!selected && !items.length && (
        <div className="empty">
          <span>▤</span>
          <h2>Kiến thức bắt đầu từ tài liệu</h2>
          <p>Tạo kho, tải văn bản lên và kiểm tra những đoạn AI tìm thấy.</p>
        </div>
      )}
      {create && (
        <Modal title="Tạo kho kiến thức" close={() => setCreate(false)}>
          <form onSubmit={createKnowledge}>
            <label>
              Tên kho
              <input name="name" required maxLength={100} autoFocus />
            </label>
            <label>
              Mô tả
              <textarea name="description" rows={2} />
            </label>
            <label>
              Phương pháp truy xuất
              <select name="embedding_model_id">
                <option value="">Tìm kiếm toàn văn — không cần embedding</option>
                {models
                  .filter((m) => m.kind === 'embedding')
                  .map((m) => (
                    <option key={m.id} value={m.id}>
                      Vector · {m.name}
                    </option>
                  ))}
              </select>
            </label>
            <ErrorBox error={error} />
            <button className="primary full">Tạo kho</button>
          </form>
        </Modal>
      )}
      {documentId && (
        <DocumentDetails
          id={documentId}
          close={() => setDocumentId('')}
          updated={() => {
            if (selected) loadDocs(selected.id);
            reload();
          }}
        />
      )}
      {upload && (
        <Modal
          title="Thêm tài liệu"
          close={() => {
            if (!busy) setUpload(false);
          }}
        >
          <form onSubmit={addDocument}>
            {integrated ? (
              <label>
                Ref bản ghi tài liệu xT (đọc theo quyền của bạn)
                <input name="xt_ref" required maxLength={200} placeholder="document:sop-xuat-kho" autoFocus />
              </label>
            ) : (
              <>
            <label>
              Chọn file TXT / Markdown / PDF / DOCX (tối đa 5 MB)
              <input
                type="file"
                accept=".txt,.md,.csv,.pdf,.docx"
                disabled={busy}
                onChange={async (e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  if (f.size > 5 * 1024 * 1024) {
                    setError('File tối đa 5 MB.');
                    return;
                  }
                  const form = e.target.form!;
                  setBusy(true);
                  setError('');
                  try {
                    const base64 = await new Promise<string>((resolve, reject) => {
                      const reader = new FileReader();
                      reader.onload = () => resolve(String(reader.result).split(',')[1]);
                      reader.onerror = () => reject(new Error('Không đọc được file.'));
                      reader.readAsDataURL(f);
                    });
                    const result = await api(`/knowledge/${selected!.id}/extract`, 'POST', { name: f.name, file_base64: base64 });
                    (form.elements.namedItem('name') as HTMLInputElement).value = result.name;
                    (form.elements.namedItem('content') as HTMLTextAreaElement).value = result.content;
                  } catch (error) {
                    setError((error as Error).message);
                  } finally {
                    setBusy(false);
                  }
                }}
              />
            </label>
            <label>
              Tên tài liệu
              <input name="name" required maxLength={200} />
            </label>
            <label>
              Nội dung
              <textarea name="content" required rows={9} maxLength={1000000} />
            </label>
              </>
            )}
            <ErrorBox error={error} />
            <button className="primary full" disabled={busy}>
              {busy ? 'Đang lập chỉ mục…' : 'Lưu và lập chỉ mục'}
            </button>
          </form>
        </Modal>
      )}
    </>
  );
}
