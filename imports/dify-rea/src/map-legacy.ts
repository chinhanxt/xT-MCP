// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Operator tool: attach one existing standalone Studio workspace to one xT tenant.
// Dry-run by default; never maps more than the workspace named on the command line.
import { DatabaseSync } from 'node:sqlite';
import { existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';
import { Store } from './store.ts';

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
export interface MapPlan {
  status: 'would_map' | 'mapped';
  tenant: string;
  workspace: { id: string; name: string };
  counts: Record<string, number>;
}
export class MapError extends Error {}

function counts(db: DatabaseSync, workspace: string) {
  const n = (sql: string) => Number(db.prepare(sql).get(workspace)!.n);
  return {
    apps: n('SELECT count(*) AS n FROM apps WHERE workspace_id=?'),
    knowledge: n('SELECT count(*) AS n FROM knowledge WHERE workspace_id=?'),
    documents: n('SELECT count(*) AS n FROM documents d JOIN knowledge k ON k.id=d.knowledge_id WHERE k.workspace_id=?'),
    models: n('SELECT count(*) AS n FROM models WHERE workspace_id=?'),
    mcp_connectors: n('SELECT count(*) AS n FROM mcp_connectors WHERE workspace_id=?'),
    conversations: n('SELECT count(*) AS n FROM conversations WHERE workspace_id=?'),
    runs: n('SELECT count(*) AS n FROM runs WHERE workspace_id=?'),
    users: n('SELECT count(*) AS n FROM users WHERE workspace_id=?'),
  };
}

function check(db: DatabaseSync, workspace: string, tenant: string): MapPlan {
  const row = db.prepare('SELECT id,name FROM workspaces WHERE id=?').get(workspace);
  if (!row) throw new MapError(`workspace not found: ${workspace}`);
  const hasMap = !!db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='xt_workspace_map'").get();
  const byWorkspace = hasMap ? db.prepare('SELECT tenant_id FROM xt_workspace_map WHERE workspace_id=?').get(workspace) : undefined;
  const byTenant = hasMap ? db.prepare('SELECT workspace_id FROM xt_workspace_map WHERE tenant_id=?').get(tenant) : undefined;
  if (byWorkspace && byWorkspace.tenant_id === tenant) throw new MapError(`workspace ${workspace} is already mapped to this tenant; nothing to do`);
  if (byWorkspace) throw new MapError(`workspace ${workspace} is already mapped to another tenant`);
  if (byTenant) {
    const existing = String(byTenant.workspace_id);
    throw new MapError(`tenant already uses workspace ${existing} ${JSON.stringify(counts(db, existing))}; ` +
      'map legacy data before the first integrated login, or move content manually');
  }
  return { status: 'would_map', tenant, workspace: { id: String(row.id), name: String(row.name) }, counts: counts(db, workspace) };
}

export function mapLegacyWorkspace(options: { dataDir: string; workspace: string; tenant: string; apply?: boolean }): MapPlan {
  if (!uuid.test(options.tenant)) throw new MapError('tenant must be a UUID chosen by the operator');
  const file = join(resolve(options.dataDir), 'studio.sqlite');
  if (!existsSync(file)) throw new MapError(`studio.sqlite not found in ${resolve(options.dataDir)}`);
  const tenant = options.tenant.toLowerCase();
  if (!options.apply) {
    const db = new DatabaseSync(file, { readOnly: true });
    try { return check(db, options.workspace, tenant); } finally { db.close(); }
  }
  const store = new Store(options.dataDir);
  try {
    return store.transaction(() => {
      const plan = check(store.db, options.workspace, tenant);
      store.db.prepare('INSERT INTO xt_workspace_map(tenant_id,workspace_id) VALUES(?,?)').run(tenant, options.workspace);
      return { ...plan, status: 'mapped' as const };
    });
  } finally { store.close(); }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const { values } = parseArgs({ options: {
      'data-dir': { type: 'string' }, workspace: { type: 'string' }, tenant: { type: 'string' }, apply: { type: 'boolean', default: false },
    } });
    if (!values['data-dir'] || !values.workspace || !values.tenant)
      throw new MapError('usage: node src/map-legacy.ts --data-dir DIR --workspace ID --tenant UUID [--apply]');
    console.log(JSON.stringify(mapLegacyWorkspace({ dataDir: values['data-dir'], workspace: values.workspace, tenant: values.tenant, apply: values.apply }), null, 2));
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(error instanceof MapError ? 2 : 1);
  }
}
