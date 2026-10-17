// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { DatabaseSync } from 'node:sqlite';
import { mkdirSync, readFileSync, writeFileSync, chmodSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { randomBytes, createCipheriv, createDecipheriv, randomUUID } from 'node:crypto';
import type { AppConfig, StudioApp, Model } from './contracts.ts';
import { configInput, HttpError } from './validation.ts';
export class Store {
  db: DatabaseSync;
  key: Buffer;
  constructor(directory: string) {
    const root = resolve(directory);
    mkdirSync(root, { recursive: true, mode: 0o700 });
    chmodSync(root, 0o700);
    const keyPath = join(root, 'credential.key');
    try {
      this.key = readFileSync(keyPath);
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
      if (existsSync(join(root, 'studio.sqlite')))
        throw new Error('Thiếu credential.key của database hiện có. Khôi phục key cùng bản sao lưu trước khi khởi động.');
      this.key = randomBytes(32);
      writeFileSync(keyPath, this.key, { mode: 0o600, flag: 'wx' });
    }
    if (this.key.length !== 32) throw new Error('Credential key invalid');
    this.db = new DatabaseSync(join(root, 'studio.sqlite'));
    chmodSync(join(root, 'studio.sqlite'), 0o600);
    this.db.exec(`PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS workspaces(id TEXT PRIMARY KEY,name TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS xt_workspace_map(tenant_id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL UNIQUE REFERENCES workspaces(id));
      CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL REFERENCES workspaces(id),email TEXT UNIQUE NOT NULL,password TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS mcp_connectors(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,url TEXT NOT NULL,secret TEXT NOT NULL,tools TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS models(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,provider TEXT NOT NULL,base_url TEXT NOT NULL,model TEXT NOT NULL,kind TEXT NOT NULL,secret TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS apps(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,kind TEXT NOT NULL,config TEXT NOT NULL,published TEXT,version INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS knowledge(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,embedding_model_id TEXT);
      CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,knowledge_id TEXT NOT NULL REFERENCES knowledge(id) ON DELETE CASCADE,name TEXT NOT NULL,content TEXT NOT NULL,status TEXT NOT NULL,error TEXT,enabled INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,content TEXT NOT NULL,embedding TEXT);
      CREATE VIRTUAL TABLE IF NOT EXISTS chunk_search USING fts5(id UNINDEXED,content,tokenize='unicode61 remove_diacritics 2');
      CREATE TRIGGER IF NOT EXISTS chunks_delete AFTER DELETE ON chunks BEGIN DELETE FROM chunk_search WHERE id=OLD.id; END;
      CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL,title TEXT NOT NULL,messages TEXT NOT NULL,updated_at TEXT NOT NULL,mode TEXT NOT NULL,revision INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL,mode TEXT NOT NULL,input TEXT NOT NULL,result TEXT,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,config_hash TEXT NOT NULL,app_version INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS app_keys(id TEXT PRIMARY KEY,app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL,token_hash TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS app_versions(app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,version INTEGER NOT NULL,config TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(app_id,version));
      CREATE TABLE IF NOT EXISTS share_links(id TEXT PRIMARY KEY,app_id TEXT UNIQUE NOT NULL REFERENCES apps(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL,created_at TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS public_sessions(id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,share_id TEXT NOT NULL REFERENCES share_links(id) ON DELETE CASCADE,expires INTEGER NOT NULL);
    `);
    this.transaction(() => {
      const columns = this.db.prepare('PRAGMA table_info(conversations)').all();
      if (!columns.some((c) => c.name === 'audience'))
        this.db.exec("ALTER TABLE conversations ADD COLUMN audience TEXT NOT NULL DEFAULT 'studio'");
      if (!this.db.prepare('PRAGMA table_info(runs)').all().some((c) => c.name === 'person_id'))
        this.db.exec("ALTER TABLE runs ADD COLUMN person_id TEXT NOT NULL DEFAULT ''");
      this.db.exec(
        'INSERT OR IGNORE INTO app_versions SELECT id,version,published,name,description,updated_at FROM apps WHERE published IS NOT NULL AND version>0; PRAGMA user_version=1;',
      );
    });
  }
  close() {
    this.db.close();
  }
  id() {
    return randomUUID();
  }
  encrypt(value: string) {
    if (!value) return '';
    const iv = randomBytes(12),
      cipher = createCipheriv('aes-256-gcm', this.key, iv);
    const body = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
    return Buffer.concat([iv, cipher.getAuthTag(), body]).toString('base64');
  }
  decrypt(value: string) {
    if (!value) return '';
    const b = Buffer.from(value, 'base64'),
      d = createDecipheriv('aes-256-gcm', this.key, b.subarray(0, 12));
    d.setAuthTag(b.subarray(12, 28));
    return Buffer.concat([d.update(b.subarray(28)), d.final()]).toString('utf8');
  }
  app(workspace: string, id: string): StudioApp {
    const r = this.db
      .prepare('SELECT id,name,description,kind,config,published,version,updated_at FROM apps WHERE id=? AND workspace_id=?')
      .get(id, workspace) as unknown as StudioApp;
    if (!r) throw new HttpError(404, 'Không tìm thấy ứng dụng.');
    return {
      ...r,
      config: configInput(JSON.parse(r.config as unknown as string)),
      published: r.published ? configInput(JSON.parse(r.published as unknown as string)) : null,
    };
  }
  model(workspace: string, id: string): Model & { api_key: string } {
    const r = this.db.prepare('SELECT * FROM models WHERE id=? AND workspace_id=?').get(id, workspace) as
      Record<string, unknown> | undefined;
    if (!r) throw new HttpError(400, 'Chưa chọn model hoặc model đã bị xóa.');
    return {
      id: String(r.id),
      name: String(r.name),
      provider: r.provider as Model['provider'],
      base_url: String(r.base_url),
      model: String(r.model),
      kind: r.kind as Model['kind'],
      has_key: !!r.secret,
      api_key: this.decrypt(String(r.secret)),
    };
  }
  knowledge(workspace: string, id: string) {
    const r = this.db.prepare('SELECT * FROM knowledge WHERE id=? AND workspace_id=?').get(id, workspace);
    if (!r) throw new HttpError(404, 'Không tìm thấy kho kiến thức.');
    return r;
  }
  transaction<T>(work: () => T): T {
    this.db.exec('BEGIN IMMEDIATE');
    try {
      const r = work();
      this.db.exec('COMMIT');
      return r;
    } catch (e) {
      this.db.exec('ROLLBACK');
      throw e;
    }
  }
}
