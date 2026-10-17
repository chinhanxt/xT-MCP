// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { body, now, type RouteContext } from './http.ts';
import { configInput, HttpError, kind, object, text } from './validation.ts';
import { validateAppReferences } from './apps.ts';
import { randomBytes } from 'node:crypto';
import { connector } from './mcp.ts';
import type { AppConfig } from './contracts.ts';
function resourceIds(config: AppConfig) {
  return {
    models: [...new Set([config.model_id, ...config.graph.nodes.map((n) => n.data.model_id)].filter(Boolean))] as string[],
    connectors: [...new Set((config.mcp_tools ?? []).map((t) => t.connector_id))],
    knowledge: [...new Set([...config.knowledge_ids, ...config.graph.nodes.map((n) => n.data.knowledge_id)].filter(Boolean))] as string[],
  };
}
export async function routeAppManagement(ctx: RouteContext): Promise<boolean> {
  const { path, method, req, workspace, store, send } = ctx;
  if (path === '/api/apps/import' && method === 'POST') {
    const v = await body(req),
      manifest = object(v.manifest);
    if (manifest.format !== 'xt-studio.app' || manifest.version !== 1) throw new HttpError(400, 'Định dạng app không được hỗ trợ.');
    const source = object(manifest.app),
      config = configInput(source.config),
      bindings = v.bindings === undefined ? {} : object(v.bindings);
    const models = bindings.models === undefined ? {} : object(bindings.models),
      knowledge = bindings.knowledge === undefined ? {} : object(bindings.knowledge),
      connectors = bindings.connectors === undefined ? {} : object(bindings.connectors);
    const map = (id: string, values: Record<string, unknown>) =>
      values[id] === undefined ? id : text(values[id], 'Resource binding', 128);
    config.mcp_tools = (config.mcp_tools ?? []).map((t) => ({ ...t, connector_id: map(t.connector_id, connectors) }));
    config.model_id = config.model_id ? map(config.model_id, models) : '';
    config.knowledge_ids = config.knowledge_ids.map((id) => map(id, knowledge));
    for (const node of config.graph.nodes) {
      if (node.data.model_id) node.data.model_id = map(node.data.model_id, models);
      if (node.data.knowledge_id) node.data.knowledge_id = map(node.data.knowledge_id, knowledge);
    }
    const appKind = kind(source.kind);
    validateAppReferences(store, workspace, config, appKind === 'workflow');
    const id = store.id();
    store.db
      .prepare('INSERT INTO apps(id,workspace_id,name,description,kind,config,updated_at) VALUES(?,?,?,?,?,?,?)')
      .run(
        id,
        workspace,
        text(source.name, 'Tên', 100),
        text(source.description ?? '', 'Mô tả', 1000, false),
        appKind,
        JSON.stringify(config),
        now(),
      );
    send(store.app(workspace, id), 201);
    return true;
  }
  const match = path.match(/^\/api\/apps\/([\w-]+)\/(export|versions|share)(?:\/(\d+))?(?:\/(restore))?$/);
  if (!match) return false;
  const app = store.app(workspace, match[1]);
  if (match[2] === 'export' && method === 'GET') {
    const ids = resourceIds(app.config);
    send({
      format: 'xt-studio.app',
      version: 1,
      app: { name: app.name, description: app.description, kind: app.kind, config: app.config },
      dependencies: {
        connectors: ids.connectors.map((id) => ({ ref: id, name: connector(store, workspace, id).name })),
        models: ids.models.map((id) => {
          const m = store.model(workspace, id);
          return { ref: id, name: m.name, model: m.model, kind: m.kind, provider: m.provider };
        }),
        knowledge: ids.knowledge.map((id) => ({ ref: id, name: store.knowledge(workspace, id).name })),
      },
    });
    return true;
  }
  if (match[2] === 'versions') {
    if (method === 'GET') {
      send(
        store.db.prepare('SELECT version,name,description,created_at FROM app_versions WHERE app_id=? ORDER BY version DESC').all(app.id),
      );
      return true;
    }
    if (method === 'POST' && match[3] && match[4]) {
      const version = store.db
        .prepare('SELECT config,name,description FROM app_versions WHERE app_id=? AND version=?')
        .get(app.id, Number(match[3]));
      if (!version) throw new HttpError(404, 'Không tìm thấy phiên bản.');
      const config = configInput(JSON.parse(String(version.config)));
      validateAppReferences(store, workspace, config, app.kind === 'workflow');
      store.db
        .prepare('UPDATE apps SET config=?,name=?,description=?,updated_at=? WHERE id=?')
        .run(JSON.stringify(config), String(version.name), String(version.description), now(), app.id);
      send(store.app(workspace, app.id));
      return true;
    }
  }
  if (match[2] === 'share') {
    if (method === 'GET') {
      send(store.db.prepare('SELECT id,created_at FROM share_links WHERE app_id=? AND workspace_id=?').get(app.id, workspace) ?? null);
      return true;
    }
    if (method === 'POST') {
      if (!app.published) throw new HttpError(409, 'Publish trước khi tạo link.');
      let link = store.db.prepare('SELECT id FROM share_links WHERE app_id=?').get(app.id);
      if (!link) {
        const id = randomBytes(24).toString('hex');
        store.db.prepare('INSERT INTO share_links VALUES(?,?,?,?)').run(id, app.id, workspace, now());
        link = { id };
      }
      send(link);
      return true;
    }
    if (method === 'DELETE') {
      store.db.prepare('DELETE FROM share_links WHERE app_id=? AND workspace_id=?').run(app.id, workspace);
      send({ ok: true });
      return true;
    }
  }
  return false;
}
