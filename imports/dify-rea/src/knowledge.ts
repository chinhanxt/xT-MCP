import type { Citation } from './contracts.ts';
import type { Store } from './store.ts';
import { embed } from './models.ts';
import { HttpError } from './validation.ts';
export function chunkText(content: string, size = 800, overlap = 120): string[] {
  const points = Array.from(content.replace(/\r\n/g, '\n').trim()),
    chunks: string[] = [];
  if (size <= overlap || overlap < 0) throw new Error('Invalid chunk settings');
  for (let start = 0; start < points.length; start += size - overlap) {
    chunks.push(points.slice(start, start + size).join(''));
    if (start + size >= points.length) break;
  }
  return chunks;
}
export async function indexDocument(
  store: Store,
  workspace: string,
  knowledgeId: string,
  name: string,
  content: string,
  signal?: AbortSignal,
  existingId?: string,
  source?: { ref: string; revision: number },
) {
  const knowledge = store.knowledge(workspace, knowledgeId),
    id = existingId ?? store.id();
  const chunks = chunkText(content);
  if (!chunks.length || chunks.length > 1500) throw new HttpError(400, 'Tài liệu trống hoặc quá lớn.');
  let previousStatus = 'error';
  if (existingId) {
    const document = store.db.prepare('SELECT status FROM documents WHERE id=? AND knowledge_id=?').get(id, knowledgeId);
    if (!document) throw new HttpError(404, 'Không tìm thấy tài liệu.');
    previousStatus = String(document.status);
    if (previousStatus === 'indexing') throw new HttpError(409, 'Tài liệu đang được lập chỉ mục.');
    store.db.prepare("UPDATE documents SET status='indexing',error=NULL WHERE id=?").run(id);
  } else
    store.db
      .prepare('INSERT INTO documents(id,knowledge_id,name,content,status,xt_ref,xt_revision) VALUES(?,?,?,?,?,?,?)')
      .run(id, knowledgeId, name, content, 'indexing', source?.ref ?? null, source?.revision ?? null);
  try {
    const vectors: number[][] = [];
    if (knowledge.embedding_model_id)
      for (let i = 0; i < chunks.length; i += 16)
        vectors.push(...(await embed(store, workspace, String(knowledge.embedding_model_id), chunks.slice(i, i + 16), signal)));
    store.transaction(() => {
      if (!store.db.prepare('SELECT id FROM documents WHERE id=?').get(id))
        throw new HttpError(409, 'Tài liệu đã bị xóa trong lúc lập chỉ mục.');
      if (existingId) store.db.prepare('DELETE FROM chunks WHERE document_id=?').run(id);
      for (let i = 0; i < chunks.length; i++) {
        const chunkId = store.id();
        store.db.prepare('INSERT INTO chunks VALUES(?,?,?,?)').run(chunkId, id, chunks[i], vectors[i] ? JSON.stringify(vectors[i]) : null);
        store.db.prepare('INSERT INTO chunk_search(id,content) VALUES(?,?)').run(chunkId, chunks[i]);
      }
      store.db.prepare('UPDATE documents SET status=?,error=NULL,name=?,content=? WHERE id=?').run('ready', name, content, id);
      if (source) store.db.prepare('UPDATE documents SET xt_ref=?,xt_revision=? WHERE id=?').run(source.ref, source.revision, id);
    });
    return { id, status: 'ready', chunk_count: chunks.length };
  } catch (error) {
    store.db
      .prepare('UPDATE documents SET status=?,error=? WHERE id=?')
      .run(existingId && previousStatus === 'ready' ? 'ready' : 'error', error instanceof Error ? error.message : 'Index failed', id);
    throw error;
  }
}
function cosine(a: number[], b: number[]): number {
  if (a.length !== b.length) throw new HttpError(409, 'Model embedding đã đổi số chiều; tạo lại kho kiến thức.');
  let sum = 0,
    a2 = 0,
    b2 = 0;
  for (let i = 0; i < a.length; i++) {
    sum += a[i] * b[i];
    a2 += a[i] ** 2;
    b2 += b[i] ** 2;
  }
  return a2 && b2 ? sum / Math.sqrt(a2 * b2) : 0;
}
export async function retrieve(
  store: Store,
  workspace: string,
  ids: string[],
  query: string,
  limit = 5,
  signal?: AbortSignal,
): Promise<Citation[]> {
  const results: Citation[] = [];
  for (const id of [...new Set(ids)]) {
    const knowledge = store.knowledge(workspace, id);
    if (knowledge.embedding_model_id) {
      const [vector] = await embed(store, workspace, String(knowledge.embedding_model_id), [query], signal);
      // shortcut: exact cosine scan for local datasets; move to a vector index for large corpora.
      const rows = store.db
        .prepare(
          'SELECT c.id,c.content,c.embedding,d.id AS document_id,d.name FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.knowledge_id=? AND d.enabled=1 AND d.status=?',
        )
        .all(id, 'ready');
      for (const row of rows)
        if (row.embedding)
          results.push({
            chunk_id: String(row.id),
            document_id: String(row.document_id),
            document_name: String(row.name),
            content: String(row.content),
            score: cosine(vector, JSON.parse(String(row.embedding))),
          });
    } else {
      const tokens = query.match(/[\p{L}\p{N}_]+/gu)?.slice(0, 30) ?? [];
      if (!tokens.length) continue;
      const match = tokens.map((t) => `"${t}"`).join(' OR ');
      const rows = store.db
        .prepare(
          `SELECT c.id,c.content,d.id AS document_id,d.name,bm25(chunk_search) AS rank FROM chunk_search JOIN chunks c ON c.id=chunk_search.id JOIN documents d ON c.document_id=d.id WHERE chunk_search MATCH ? AND d.knowledge_id=? AND d.enabled=1 AND d.status='ready' ORDER BY rank LIMIT ?`,
        )
        .all(match, id, limit);
      for (const r of rows)
        results.push({
          chunk_id: String(r.id),
          document_id: String(r.document_id),
          document_name: String(r.name),
          content: String(r.content),
          score: -Number(r.rank),
        });
    }
  }
  return results.sort((a, b) => b.score - a.score).slice(0, limit);
}
export const contextText = (citations: Citation[]) => citations.map((c, i) => `[${i + 1}] ${c.document_name}\n${c.content}`).join('\n\n');
