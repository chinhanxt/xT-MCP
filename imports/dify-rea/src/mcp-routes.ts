import { body, type RouteContext } from './http.ts';
import { connector, connectorInput, discoverTools } from './mcp.ts';
import { HttpError } from './validation.ts';
export async function routeMcp(ctx: RouteContext): Promise<boolean> {
  const { store, workspace, path, method, req, send } = ctx;
  if (path === '/api/mcp' && method === 'GET') {
    send(
      store.db
        .prepare('SELECT id FROM mcp_connectors WHERE workspace_id=? ORDER BY name')
        .all(workspace)
        .map((row) => {
          const { token, ...item } = connector(store, workspace, String(row.id));
          return item;
        }),
    );
    return true;
  }
  const match = path.match(/^\/api\/mcp\/([\w-]+)(?:\/(refresh))?$/);
  if ((path === '/api/mcp' && method === 'POST') || (match && method === 'PUT' && !match[2])) {
    const value = await body(req);
    let parsed: ReturnType<typeof connectorInput>;
    try {
      parsed = connectorInput(value);
    } catch {
      throw new HttpError(400, 'Tên, URL hoặc token MCP không hợp lệ.');
    }
    const old = match ? connector(store, workspace, match[1]) : null;
    const token = parsed.token ?? old?.token ?? '';
    const tools = await discoverTools({ url: parsed.url, token });
    const id = old?.id ?? store.id();
    store.db
      .prepare(
        'INSERT INTO mcp_connectors VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,url=excluded.url,secret=excluded.secret,tools=excluded.tools',
      )
      .run(id, workspace, parsed.name, parsed.url, store.encrypt(token), JSON.stringify(tools), old?.enabled === false ? 0 : 1);
    const { token: secret, ...saved } = connector(store, workspace, id);
    send(saved, old ? 200 : 201);
    return true;
  }
  if (!match) return false;
  const old = connector(store, workspace, match[1]);
  if (method === 'POST' && match[2]) {
    const tools = await discoverTools(old);
    store.db.prepare('UPDATE mcp_connectors SET tools=? WHERE id=? AND workspace_id=?').run(JSON.stringify(tools), old.id, workspace);
    send({ tools });
    return true;
  }
  if (method === 'PATCH' && !match[2]) {
    const value = await body(req);
    if (typeof value.enabled !== 'boolean') throw new HttpError(400, 'enabled cần boolean.');
    store.db.prepare('UPDATE mcp_connectors SET enabled=? WHERE id=? AND workspace_id=?').run(value.enabled ? 1 : 0, old.id, workspace);
    send({ ok: true });
    return true;
  }
  if (method === 'DELETE' && !match[2]) {
    const referenced = store.db
      .prepare('SELECT config,published FROM apps WHERE workspace_id=?')
      .all(workspace)
      .some((a) =>
        [a.config, a.published].some(
          (c) => c && (JSON.parse(String(c)).mcp_tools ?? []).some((t: { connector_id: string }) => t.connector_id === old.id),
        ),
      );
    if (referenced) throw new HttpError(409, 'Connector đang được ứng dụng sử dụng; tắt connector hoặc gỡ tool khỏi app trước.');
    store.db.prepare('DELETE FROM mcp_connectors WHERE id=? AND workspace_id=?').run(old.id, workspace);
    send({ ok: true });
    return true;
  }
  return false;
}
