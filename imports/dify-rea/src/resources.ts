import { body, type RouteContext } from './http.ts';
import { HttpError, modelInput, text } from './validation.ts';
import { embed, generate } from './models.ts';
import { indexDocument, retrieve } from './knowledge.ts';
import type { AppConfig } from './contracts.ts';
import { extractDocument } from './documents.ts';
import { fetchXtDocument, xtRef } from './xt-source.ts';
function references(value: unknown, resource: 'model' | 'knowledge', id: string): boolean {
  if (!value) return false;
  const config = JSON.parse(String(value)) as AppConfig;
  return resource === 'model'
    ? config.model_id === id || config.graph.nodes.some((n) => n.type === 'llm' && n.data.model_id === id)
    : config.knowledge_ids.includes(id) || config.graph.nodes.some((n) => n.type === 'retrieval' && n.data.knowledge_id === id);
}
const rawContent = () => new HttpError(403, 'Studio tích hợp chỉ lập chỉ mục tài liệu từ bản ghi xT (xt_ref).');
export async function routeResources(ctx: RouteContext): Promise<boolean> {
  const { store, req, path, method, workspace, send } = ctx;
  if (path === '/api/models' && method === 'GET') {
    send(
      store.db
        .prepare("SELECT id,name,provider,base_url,model,kind,(secret != '') AS has_key FROM models WHERE workspace_id=? ORDER BY name")
        .all(workspace),
    );
    return true;
  }
  const modelMatch = path.match(/^\/api\/models\/([\w-]+)(\/test)?$/);
  if ((path === '/api/models' && method === 'POST') || (modelMatch && method === 'PUT')) {
    const v = modelInput(await body(req)),
      id = modelMatch?.[1] ?? store.id(),
      old = modelMatch ? store.model(workspace, id) : null;
    if (
      old &&
      (v.kind !== old.kind || v.model !== old.model || v.base_url !== old.base_url || v.provider !== old.provider) &&
      store.db.prepare('SELECT id FROM knowledge WHERE workspace_id=? AND embedding_model_id=?').get(workspace, id)
    )
      throw new HttpError(409, 'Model embedding đang được kho sử dụng; tạo model mới để thay thế.');
    if (
      old &&
      v.kind !== old.kind &&
      store.db
        .prepare('SELECT config,published FROM apps WHERE workspace_id=?')
        .all(workspace)
        .some((a) => [a.config, a.published].some((c) => references(c, 'model', id)))
    )
      throw new HttpError(409, 'Model đang được ứng dụng sử dụng; không đổi loại model.');
    const secret = store.encrypt(v.api_key === undefined ? (old?.api_key ?? '') : v.api_key);
    store.db
      .prepare(
        'INSERT INTO models VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,provider=excluded.provider,base_url=excluded.base_url,model=excluded.model,kind=excluded.kind,secret=excluded.secret',
      )
      .run(id, workspace, v.name, v.provider, v.base_url, v.model, v.kind, secret);
    send({ id });
    return true;
  }
  if (modelMatch && method === 'POST' && modelMatch[2]) {
    const m = store.model(workspace, modelMatch[1]);
    if (m.kind === 'embedding') {
      const vectors = await embed(store, workspace, m.id, ['connection test']);
      send({ ok: true, dimensions: vectors[0].length });
    } else send({ ok: true, message: (await generate(store, workspace, m.id, [{ role: 'user', content: 'Reply with OK.' }])).content });
    return true;
  }
  if (modelMatch && method === 'DELETE') {
    store.model(workspace, modelMatch[1]);
    const apps = store.db.prepare('SELECT config,published FROM apps WHERE workspace_id=?').all(workspace),
      datasets = store.db.prepare('SELECT id FROM knowledge WHERE workspace_id=? AND embedding_model_id=?').get(workspace, modelMatch[1]);
    if (datasets || apps.some((a) => [a.config, a.published].some((c) => references(c, 'model', modelMatch[1]))))
      throw new HttpError(409, 'Model đang được ứng dụng hoặc kho kiến thức sử dụng.');
    store.db.prepare('DELETE FROM models WHERE id=? AND workspace_id=?').run(modelMatch[1], workspace);
    send({ ok: true });
    return true;
  }
  if (path === '/api/knowledge' && method === 'GET') {
    send(
      store.db
        .prepare(
          'SELECT k.*, (SELECT count(*) FROM documents d WHERE d.knowledge_id=k.id) AS document_count,(SELECT count(*) FROM chunks c JOIN documents d ON c.document_id=d.id WHERE d.knowledge_id=k.id) AS chunk_count FROM knowledge k WHERE k.workspace_id=? ORDER BY k.name',
        )
        .all(workspace),
    );
    return true;
  }
  if (path === '/api/knowledge' && method === 'POST') {
    const v = await body(req),
      id = store.id();
    const embeddingId = v.embedding_model_id ? text(v.embedding_model_id, 'Embedding model', 128) : null;
    if (embeddingId && store.model(workspace, embeddingId).kind !== 'embedding') throw new HttpError(400, 'Chọn model embedding.');
    store.db
      .prepare('INSERT INTO knowledge VALUES(?,?,?,?,?)')
      .run(id, workspace, text(v.name, 'Tên kho', 100), text(v.description ?? '', 'Mô tả', 1000, false), embeddingId);
    send({ id }, 201);
    return true;
  }
  const knowledgeMatch = path.match(/^\/api\/knowledge\/([\w-]+)(?:\/(documents|search|extract))?$/);
  if (knowledgeMatch) {
    const id = knowledgeMatch[1];
    store.knowledge(workspace, id);
    if (ctx.identity && method === 'POST' && knowledgeMatch[2] === 'extract') throw rawContent();
    if (method === 'POST' && knowledgeMatch[2] === 'extract') {
      const value = await body(req);
      send(await extractDocument(value.name, value.file_base64));
      return true;
    }
    if (method === 'GET' && knowledgeMatch[2] === 'documents') {
      send(
        store.db
          .prepare(
            'SELECT d.id,d.name,d.status,d.error,d.enabled,d.xt_ref,d.xt_revision,length(d.content) AS characters,(SELECT count(*) FROM chunks c WHERE c.document_id=d.id) AS chunk_count FROM documents d WHERE knowledge_id=?',
          )
          .all(id),
      );
      return true;
    }
    if (method === 'POST' && knowledgeMatch[2] === 'documents') {
      const v = await body(req);
      if (ctx.identity) {
        if (v.content !== undefined || v.name !== undefined) throw rawContent();
        const ref = xtRef(v.xt_ref), source = await fetchXtDocument(ctx.coreTools, ref);
        send(await indexDocument(store, workspace, id, source.name, source.content, undefined, undefined, { ref, revision: source.revision }), 201);
        return true;
      }
      send(await indexDocument(store, workspace, id, text(v.name, 'Tên tài liệu', 200), text(v.content, 'Nội dung', 1000000)), 201);
      return true;
    }
    if (method === 'POST' && knowledgeMatch[2] === 'search') {
      const v = await body(req);
      send(await retrieve(store, workspace, [id], text(v.query, 'Truy vấn', 2000)));
      return true;
    }
    if (method === 'DELETE' && !knowledgeMatch[2]) {
      const apps = store.db.prepare('SELECT config,published FROM apps WHERE workspace_id=?').all(workspace);
      if (apps.some((a) => [a.config, a.published].some((c) => references(c, 'knowledge', id))))
        throw new HttpError(409, 'Kho đang được ứng dụng sử dụng.');
      store.db.prepare('DELETE FROM knowledge WHERE id=? AND workspace_id=?').run(id, workspace);
      send({ ok: true });
      return true;
    }
  }
  const docMatch = path.match(/^\/api\/documents\/([\w-]+)$/);
  if (docMatch) {
    const doc = store.db
      .prepare(
        'SELECT d.id,d.knowledge_id,d.name,d.content,d.status,d.xt_ref FROM documents d JOIN knowledge k ON k.id=d.knowledge_id WHERE d.id=? AND k.workspace_id=?',
      )
      .get(docMatch[1], workspace);
    if (!doc) throw new HttpError(404, 'Không tìm thấy tài liệu.');
    if (method === 'GET') {
      const offset = Number(ctx.url.searchParams.get('offset') ?? 0);
      if (!Number.isInteger(offset) || offset < 0) throw new HttpError(400, 'Offset không hợp lệ.');
      const chunks = store.db
        .prepare('SELECT id,content FROM chunks WHERE document_id=? ORDER BY rowid LIMIT 50 OFFSET ?')
        .all(docMatch[1], offset);
      send({
        ...doc,
        chunks,
        offset,
        chunk_count: store.db.prepare('SELECT count(*) AS n FROM chunks WHERE document_id=?').get(docMatch[1])!.n,
      });
      return true;
    }
    if (method === 'PUT') {
      const v = await body(req);
      if (ctx.identity) {
        if (Object.keys(v).length || !doc.xt_ref) throw rawContent();
        const ref = String(doc.xt_ref), source = await fetchXtDocument(ctx.coreTools, ref);
        send(await indexDocument(store, workspace, String(doc.knowledge_id), source.name, source.content, undefined, docMatch[1], { ref, revision: source.revision }));
        return true;
      }
      send(
        await indexDocument(
          store,
          workspace,
          String(doc.knowledge_id),
          text(v.name ?? doc.name, 'Tên tài liệu', 200),
          text(v.content ?? doc.content, 'Nội dung', 1000000),
          undefined,
          docMatch[1],
        ),
      );
      return true;
    }
    if (method === 'PATCH') {
      const v = await body(req);
      if (typeof v.enabled !== 'boolean') throw new HttpError(400, 'enabled cần boolean.');
      store.db.prepare('UPDATE documents SET enabled=? WHERE id=?').run(v.enabled ? 1 : 0, docMatch[1]);
      send({ ok: true });
      return true;
    }
    if (method === 'DELETE') {
      store.db.prepare('DELETE FROM documents WHERE id=?').run(docMatch[1]);
      send({ ok: true });
      return true;
    }
  }

  return false;
}
