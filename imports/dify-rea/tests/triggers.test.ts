// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { randomBytes, randomUUID } from 'node:crypto';
import type { AddressInfo } from 'node:net';
import { Store } from '../src/store.ts';
import { createStudio } from '../src/server.ts';
import { emptyConfig } from '../src/contracts.ts';
import type { GraphNode } from '../src/contracts.ts';
import { nextFire, scheduleInput } from '../src/schedule.ts';
import { createTriggerRunner, startScheduler, xtTriggerDenied } from '../src/triggers.ts';

const node = (id: string, type: GraphNode['type'], data: Partial<GraphNode['data']> = {}): GraphNode => ({ id, type, position: { x: 0, y: 0 }, data: { label: id, ...data } });
const at = (iso: string) => Date.parse(iso);

function seedApp(store: Store, workspace: string, graph?: ReturnType<typeof simpleGraph>, published = true) {
  const id = randomUUID(), config = { ...emptyConfig(), graph: graph ?? simpleGraph() };
  store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run(workspace, 'W');
  store.db.prepare('INSERT INTO apps(id,workspace_id,name,description,kind,config,published,version,updated_at) VALUES(?,?,?,?,?,?,?,?,?)')
    .run(id, workspace, 'Wf', '', 'workflow', JSON.stringify(config), published ? JSON.stringify(config) : null, published ? 1 : 0, new Date().toISOString());
  return id;
}
const simpleGraph = () => ({ nodes: [node('s', 'start'), node('out', 'output', { template: 'tick' })], edges: [{ id: 'e', source: 's', target: 'out' }] });
function insertTrigger(store: Store, app: string, workspace: string, config: unknown, createdAt: number, by = 'u1') {
  const id = randomUUID();
  store.db.prepare('INSERT INTO app_triggers(id,app_id,workspace_id,kind,config,enabled,created_by,last_fired_at,created_at) VALUES(?,?,?,?,?,1,?,?,?)')
    .run(id, app, workspace, 'schedule', JSON.stringify(config), by, createdAt, new Date(createdAt).toISOString());
  return id;
}
function fixture() {
  const dir = mkdtempSync(join(tmpdir(), 'xt-trig-')), store = new Store(dir);
  return { dir, store, done: () => { store.close(); rmSync(dir, { recursive: true, force: true }); } };
}

test('schedule config validation and defaults', () => {
  assert.equal(scheduleInput({ frequency: 'daily', time: '08:30' }).timezone, 'Asia/Ho_Chi_Minh');
  assert.throws(() => scheduleInput({ frequency: 'daily', time: '25:00' }), /HH:MM/);
  assert.throws(() => scheduleInput({ frequency: 'weekly', time: '08:00', weekdays: [] }), /tuần/);
  assert.throws(() => scheduleInput({ frequency: 'monthly', time: '08:00', monthly_days: [32] }), /tháng/);
  assert.throws(() => scheduleInput({ frequency: 'hourly', on_minute: 60 }), /Phút/);
  assert.throws(() => scheduleInput({ frequency: 'daily', time: '08:00', timezone: 'Mars/Base' }), /Múi giờ/);
});

test('next fire is computed in the trigger timezone', () => {
  const vn = scheduleInput({ frequency: 'daily', time: '08:30' });
  assert.equal(nextFire(vn, at('2026-10-20T00:00:00Z')), at('2026-10-20T01:30:00Z'));
  assert.equal(nextFire(vn, at('2026-10-20T01:30:00Z')), at('2026-10-21T01:30:00Z'));
  const hourly = scheduleInput({ frequency: 'hourly', on_minute: 15 });
  assert.equal(nextFire(hourly, at('2026-10-20T00:20:00Z')), at('2026-10-20T01:15:00Z'));
  const weekly = scheduleInput({ frequency: 'weekly', time: '09:00', weekdays: ['mon', 'fri'], timezone: 'UTC' });
  assert.equal(nextFire(weekly, at('2026-10-20T10:00:00Z')), at('2026-10-23T09:00:00Z')); // Tue -> Fri
  const monthly = scheduleInput({ frequency: 'monthly', time: '00:00', monthly_days: [31], timezone: 'UTC' });
  assert.equal(nextFire(monthly, at('2026-02-01T00:00:00Z')), at('2026-02-28T00:00:00Z'));
  const ny = scheduleInput({ frequency: 'daily', time: '09:00', timezone: 'America/New_York' });
  assert.equal(nextFire(ny, at('2026-03-07T12:00:00Z')), at('2026-03-07T14:00:00Z')); // EST
  assert.equal(nextFire(ny, at('2026-03-07T14:00:00Z')), at('2026-03-08T13:00:00Z')); // DST starts 8 Mar -> EDT
  assert.equal(nextFire(ny, at('2026-03-08T13:00:00Z')), at('2026-03-09T13:00:00Z'));
});

test('scheduler fires a slot once, survives restart, never backfills and runs one at a time', async () => {
  const f = fixture();
  try {
    const app = seedApp(f.store, 'w1');
    let clock = at('2026-10-20T00:00:00Z');
    const id = insertTrigger(f.store, app, 'w1', scheduleInput({ frequency: 'daily', time: '08:30' }), clock);
    const runner = createTriggerRunner(f.store, { now: () => clock });
    const scheduler = startScheduler(f.store, runner, { autostart: false });
    assert.equal(scheduler.tick(), 0);
    clock = at('2026-10-20T01:30:30Z');
    assert.equal(scheduler.tick(), 1);
    assert.equal(scheduler.tick(), 0);
    await runner.idle();
    // "restart": fresh runner and scheduler on the same store must not fire the slot again
    const again = startScheduler(f.store, createTriggerRunner(f.store, { now: () => clock }), { autostart: false });
    assert.equal(again.tick(), 0);
    const runs = f.store.db.prepare('SELECT status,"trigger",duration_ms,trace,mode FROM runs WHERE app_id=?').all(app);
    assert.equal(runs.length, 1);
    assert.deepEqual([runs[0].status, runs[0].trigger, runs[0].mode], ['success', 'schedule', 'published']);
    assert.ok(typeof runs[0].duration_ms === 'number' && JSON.parse(String(runs[0].trace)).length === 2);
    // three days down: the missed slots are not run, one more is not owed
    clock = at('2026-10-23T05:00:00Z');
    assert.equal(scheduler.tick(), 0);
    assert.equal(f.store.db.prepare('SELECT count(*) AS n FROM runs').get()!.n, 1);
    // the next real slot fires again
    clock = at('2026-10-24T01:30:10Z');
    assert.equal(scheduler.tick(), 1);
    await runner.idle();
    // disabled triggers are skipped; re-enabling restarts the clock (no backfill)
    f.store.db.prepare('UPDATE app_triggers SET enabled=0 WHERE id=?').run(id);
    clock = at('2026-10-25T01:30:10Z');
    assert.equal(scheduler.tick(), 0);
    // unpublished apps do not run
    f.store.db.prepare('UPDATE app_triggers SET enabled=1 WHERE id=?').run(id);
    f.store.db.prepare('UPDATE apps SET published=NULL WHERE id=?').run(app);
    assert.equal(scheduler.tick(), 0);
    assert.equal(f.store.db.prepare('SELECT count(*) AS n FROM runs').get()!.n, 2);
    // one concurrent run per trigger
    f.store.db.prepare('UPDATE apps SET published=config WHERE id=?').run(app);
    const row = f.store.db.prepare('SELECT * FROM app_triggers WHERE id=?').get(id)!;
    const trig = { id, app_id: app, workspace_id: 'w1', kind: 'schedule' as const, config: JSON.parse(String(row.config)), enabled: true, created_by: 'u1', last_fired_at: null, created_at: '' };
    const first = runner.fire(trig);
    assert.throws(() => runner.fire(trig), /đang chạy/);
    await first.done;
  } finally { f.done(); }
});

test('integrated scheduled runs needing xT tools record the capability error', async () => {
  const f = fixture();
  try {
    const graph = { nodes: [node('s', 'start'), node('r', 'retrieval', { knowledge_id: 'k' }), node('out', 'output', { template: 'x' })],
      edges: [{ id: 'a', source: 's', target: 'r' }, { id: 'b', source: 'r', target: 'out' }] };
    const app = seedApp(f.store, 'w1', graph);
    const runner = createTriggerRunner(f.store, { integrated: true });
    const trig = { id: 't', app_id: app, workspace_id: 'w1', kind: 'schedule' as const, config: scheduleInput({ frequency: 'hourly' }), enabled: true, created_by: 'person-1', last_fired_at: null, created_at: '' };
    const { run_id, done } = runner.fire(trig);
    await assert.rejects(done, new RegExp(xtTriggerDenied));
    const row = f.store.db.prepare('SELECT status,error,person_id FROM runs WHERE id=?').get(run_id)!;
    assert.deepEqual({ ...row }, { status: 'error', error: xtTriggerDenied, person_id: 'person-1' });
  } finally { f.done(); }
});

test('trigger and run-log HTTP API, workspace scoping, filters and cursor', async () => {
  const f = fixture();
  const server = createStudio(f.store);
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', r));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  try {
    let setup = await fetch(origin + '/api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace_name: 'A', email: 'a@example.test', password: 'long-enough-pass' }) });
    const cookie = setup.headers.get('set-cookie')!.split(';')[0];
    const call = async (path: string, method = 'GET', data?: unknown, auth = cookie) => {
      const r = await fetch(origin + '/api' + path, { method, headers: { Cookie: auth, ...(data === undefined ? {} : { 'Content-Type': 'application/json' }) }, body: data === undefined ? undefined : JSON.stringify(data) });
      return { status: r.status, body: (await r.json()) as any };
    };
    const ws = String((await call('/session')).body.user.workspace_id);
    const app = (await call('/apps', 'POST', { name: 'Wf', kind: 'workflow' })).body;
    const chat = (await call('/apps', 'POST', { name: 'Chat', kind: 'chat' })).body;
    assert.equal((await call(`/apps/${chat.id}/triggers`, 'POST', { kind: 'schedule', frequency: 'hourly' })).status, 400);
    assert.equal((await call(`/apps/${app.id}/triggers`, 'POST', { kind: 'webhook' })).status, 400);
    assert.equal((await call(`/apps/${app.id}/triggers`, 'POST', { kind: 'schedule', frequency: 'daily', time: 'bad' })).status, 400);
    const made = await call(`/apps/${app.id}/triggers`, 'POST', { kind: 'schedule', frequency: 'daily', time: '08:30' });
    assert.equal(made.status, 201);
    assert.equal(made.body.config.timezone, 'Asia/Ho_Chi_Minh');
    assert.ok(made.body.next_fire_at > Date.now());
    assert.equal((await call(`/apps/${app.id}/triggers/${made.body.id}`, 'PATCH', { enabled: false })).body.enabled, false);
    assert.equal((await call(`/apps/${app.id}/triggers`)).body.length, 1);
    assert.equal((await call(`/apps/${app.id}/triggers/${made.body.id}`, 'DELETE')).status, 200);
    assert.equal((await call(`/apps/${app.id}/triggers`)).body.length, 0);
    // run log: seed rows, newest first, filters and cursor
    const ins = f.store.db.prepare('INSERT INTO runs(id,app_id,workspace_id,mode,input,status,error,created_at,config_hash,app_version,"trigger",duration_ms,trace) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)');
    const kinds = ['manual', 'schedule', 'api', 'schedule', 'share'];
    kinds.forEach((k, i) => ins.run(`r${i}`, app.id, ws, 'published', 'in' + i, i === 3 ? 'error' : 'success', i === 3 ? 'boom' : null,
      new Date(at('2026-10-20T00:00:00Z') + i * 1000).toISOString(), 'h', 1, k, 10 + i, JSON.stringify([{ node_id: 'n', label: 'N', type: 'output', status: 'success', input: '', output: 'o', duration_ms: 1 }])));
    const page1 = await call(`/apps/${app.id}/runs?limit=2`);
    assert.deepEqual(page1.body.items.map((r: any) => r.id), ['r4', 'r3']);
    const page2 = await call(`/apps/${app.id}/runs?limit=2&cursor=${page1.body.next_cursor}`);
    assert.deepEqual(page2.body.items.map((r: any) => r.id), ['r2', 'r1']);
    const page3 = await call(`/apps/${app.id}/runs?limit=2&cursor=${page2.body.next_cursor}`);
    assert.deepEqual(page3.body.items.map((r: any) => r.id), ['r0']);
    assert.equal(page3.body.next_cursor, null);
    assert.deepEqual((await call(`/apps/${app.id}/runs?trigger=schedule`)).body.items.map((r: any) => r.id), ['r3', 'r1']);
    assert.deepEqual((await call(`/apps/${app.id}/runs?status=error`)).body.items.map((r: any) => r.id), ['r3']);
    assert.equal((await call(`/apps/${app.id}/runs?status=nope`)).status, 400);
    const detail = await call('/runs/r3');
    assert.deepEqual([detail.body.trigger, detail.body.status, detail.body.duration_ms, detail.body.error, detail.body.trace.length], ['schedule', 'error', 13, 'boom', 1]);
    // another workspace gets 404 for the same ids
    f.store.db.prepare('INSERT INTO workspaces VALUES(?,?)').run('other', 'B');
    f.store.db.prepare('INSERT INTO users VALUES(?,?,?,?)').run('u2', 'other', 'b@example.test', 'x:y');
    f.store.db.prepare('INSERT INTO sessions VALUES(?,?,?)').run((await import('../src/http.ts')).hashToken('tok2'), 'u2', Date.now() + 1e6);
    const other = 'studio_session=tok2';
    assert.equal((await call('/runs/r3', 'GET', undefined, other)).status, 404);
    assert.equal((await call(`/apps/${app.id}/runs`, 'GET', undefined, other)).status, 404);
    assert.equal((await call(`/apps/${app.id}/triggers`, 'GET', undefined, other)).status, 404);
    // manual run records trigger/duration/trace
    await call(`/apps/${app.id}`, 'PUT', { name: 'Wf', description: '', config: { ...app.config, graph: simpleGraph() } });
    const run = await call(`/apps/${app.id}/run`, 'POST', { input: 'hi', mode: 'draft' });
    assert.equal(run.status, 200);
    const logged = await call(`/runs/${run.body.run_id}`);
    assert.deepEqual([logged.body.trigger, logged.body.status, typeof logged.body.duration_ms, logged.body.trace.length], ['manual', 'success', 'number', 2]);
  } finally {
    await new Promise<void>((r) => server.close(() => r()));
    f.done();
  }
});

test('old-schema database migrates in place and keeps existing runs', () => {
  const dir = mkdtempSync(join(tmpdir(), 'xt-old-'));
  try {
    writeFileSync(join(dir, 'credential.key'), randomBytes(32), { mode: 0o600 });
    const old = new DatabaseSync(join(dir, 'studio.sqlite'));
    old.exec(`CREATE TABLE apps(id TEXT PRIMARY KEY,workspace_id TEXT NOT NULL,name TEXT NOT NULL,description TEXT NOT NULL,kind TEXT NOT NULL,config TEXT NOT NULL,published TEXT,version INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
      CREATE TABLE runs(id TEXT PRIMARY KEY,app_id TEXT NOT NULL REFERENCES apps(id) ON DELETE CASCADE,workspace_id TEXT NOT NULL,mode TEXT NOT NULL,input TEXT NOT NULL,result TEXT,status TEXT NOT NULL,error TEXT,created_at TEXT NOT NULL,config_hash TEXT NOT NULL,app_version INTEGER NOT NULL);
      INSERT INTO apps VALUES('a','w','n','','workflow','{}',NULL,0,'t');
      INSERT INTO runs VALUES('old','a','w','draft','x','{"output":"o","trace":[]}','success',NULL,'2026-01-01T00:00:00Z','h',0);`);
    old.close();
    const store = new Store(dir);
    try {
      const cols = store.db.prepare('PRAGMA table_info(runs)').all().map((c) => c.name);
      for (const c of ['trigger', 'duration_ms', 'trace', 'person_id']) assert.ok(cols.includes(c), c);
      const row = store.db.prepare('SELECT "trigger",duration_ms,trace,input FROM runs WHERE id=?').get('old')!;
      assert.deepEqual({ ...row }, { trigger: 'manual', duration_ms: null, trace: null, input: 'x' });
      assert.ok(store.db.prepare("SELECT name FROM sqlite_master WHERE name='app_triggers'").get());
    } finally { store.close(); }
    new Store(dir).close(); // reopening an already-migrated database is a no-op
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
