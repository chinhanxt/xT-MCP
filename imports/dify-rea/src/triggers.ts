// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Schedule triggers for workflow apps, the in-process scheduler and the run-log API.
// Scheduling rules: only the PUBLISHED version runs; a slot missed while the server was down or the trigger was disabled
// is never backfilled (a slot later than `graceMs` past its time is skipped); `last_fired_at` is persisted with a
// compare-and-set so a restart or second process never fires the same slot twice; one run per trigger at a time.
import { body, hashToken, type RouteContext } from './http.ts';
import type { Store } from './store.ts';
import type { RunResult } from './contracts.ts';
import { allNodes } from './contracts.ts';
import { HttpError, choice, object } from './validation.ts';
import { runInputs } from './input-fields.ts';
import { runWorkflow } from './workflow.ts';
import { validateAppReferences } from './apps.ts';
import { nextFire, scheduleInput, type ScheduleConfig } from './schedule.ts';

export const xtTriggerDenied = 'Kích hoạt tự động chưa được cấp quyền công cụ xT.';
export interface TriggerRow {
  id: string; app_id: string; workspace_id: string; kind: 'schedule'; config: ScheduleConfig;
  enabled: boolean; created_by: string; last_fired_at: number | null; created_at: string;
}
const rowOf = (r: Record<string, unknown>): TriggerRow => ({
  id: String(r.id), app_id: String(r.app_id), workspace_id: String(r.workspace_id), kind: 'schedule',
  config: JSON.parse(String(r.config)), enabled: !!r.enabled, created_by: String(r.created_by),
  last_fired_at: r.last_fired_at === null ? null : Number(r.last_fired_at), created_at: String(r.created_at),
});
const nextOf = (t: TriggerRow) => nextFire(t.config, t.last_fired_at ?? Date.parse(t.created_at));

export interface TriggerRunner {
  now: () => number;
  integrated: boolean;
  fire(trigger: TriggerRow): { run_id: string; done: Promise<RunResult> };
  idle(): Promise<void>;
  close(): void;
}
export function createTriggerRunner(store: Store, options: { integrated?: boolean; now?: () => number } = {}): TriggerRunner {
  const now = options.now ?? Date.now, integrated = options.integrated ?? false;
  const running = new Set<string>(), active = new Set<Promise<unknown>>(), stop = new AbortController();
  return {
    now, integrated,
    fire(trigger) {
      if (running.has(trigger.id)) throw new HttpError(409, 'Lượt chạy trước của kích hoạt vẫn đang chạy.');
      const app = store.app(trigger.workspace_id, trigger.app_id);
      const config = app.published;
      if (!config) throw new HttpError(409, 'Ứng dụng chưa publish.');
      const runId = store.id(), started = Date.now();
      store.db
        .prepare('INSERT INTO runs(id,app_id,workspace_id,mode,input,status,created_at,config_hash,app_version,person_id,"trigger") VALUES(?,?,?,?,?,?,?,?,?,?,?)')
        .run(runId, app.id, trigger.workspace_id, 'published', '', 'running', new Date(now()).toISOString(), hashToken(JSON.stringify(config)), app.version, trigger.created_by, 'schedule');
      running.add(trigger.id);
      const done = (async () => {
        try {
          const needsXt = integrated && (allNodes(config.graph).some((n) => n.type === 'retrieval' || n.type === 'human_approval') || !!config.xt_tools?.length);
          if (needsXt) throw new HttpError(403, xtTriggerDenied);
          validateAppReferences(store, trigger.workspace_id, config, true, true);
          const inputs = runInputs(config.inputs ?? [], undefined);
          const signal = AbortSignal.any([stop.signal, AbortSignal.timeout(180000)]);
          let result: RunResult;
          try {
            result = await runWorkflow(store, trigger.workspace_id, config, '', signal, inputs, undefined, integrated);
          } catch (error) {
            if (integrated && error instanceof HttpError && error.status === 503 && /xT/.test(error.message)) throw new HttpError(403, xtTriggerDenied);
            throw error;
          }
          result.run_id = runId;
          store.db.prepare('UPDATE runs SET status=?,result=?,duration_ms=?,trace=? WHERE id=?')
            .run('success', JSON.stringify(result), Date.now() - started, JSON.stringify(result.trace ?? []), runId);
          return result;
        } catch (error) {
          const trace = (error as { trace?: unknown }).trace ?? [];
          store.db.prepare('UPDATE runs SET status=?,error=?,result=?,duration_ms=?,trace=? WHERE id=?')
            .run('error', error instanceof Error ? error.message : 'Lỗi', JSON.stringify({ trace }), Date.now() - started, JSON.stringify(trace), runId);
          throw error;
        } finally {
          running.delete(trigger.id);
        }
      })();
      const tracked = done.catch(() => {}).finally(() => active.delete(tracked));
      active.add(tracked);
      return { run_id: runId, done };
    },
    async idle() { while (active.size) await Promise.all([...active]); },
    close() { stop.abort(); },
  };
}

export interface Scheduler { tick(): number; stop(): void }
export function startScheduler(store: Store, runner: TriggerRunner, options: { intervalMs?: number; graceMs?: number; autostart?: boolean } = {}): Scheduler {
  const grace = options.graceMs ?? 300000;
  function tick() {
    const now = runner.now();
    let fired = 0;
    const rows = store.db.prepare("SELECT * FROM app_triggers WHERE kind='schedule' AND enabled=1").all();
    for (const raw of rows) {
      const t = rowOf(raw);
      const due = nextOf(t);
      if (due === null || due > now) continue;
      // Claim the slot first; losing the compare-and-set means another tick/process already owns it.
      const claimed = store.db.prepare('UPDATE app_triggers SET last_fired_at=? WHERE id=? AND COALESCE(last_fired_at,-1)=?')
        .run(now, t.id, t.last_fired_at ?? -1).changes === 1;
      if (!claimed || now - due > grace) continue;
      try {
        runner.fire(t).done.catch(() => {});
        fired++;
      } catch {
        // Busy or unpublished: the slot is dropped, never queued.
      }
    }
    return fired;
  }
  const timer = options.autostart === false ? undefined : setInterval(tick, options.intervalMs ?? 15000);
  return { tick, stop: () => clearInterval(timer) };
}

const statuses = ['running', 'waiting', 'success', 'error'] as const;
const sources = ['manual', 'api', 'schedule', 'share'] as const;
export async function routeTriggers(ctx: RouteContext, runner: TriggerRunner): Promise<boolean> {
  const { store, path, method, workspace, send, url } = ctx;
  const own = (r: Record<string, unknown>) => !ctx.identity || ctx.identity.role === 'admin' || r.person_id === ctx.identity.person;
  const trig = path.match(/^\/api\/apps\/([\w-]+)\/triggers(?:\/([\w-]+))?$/);
  if (trig) {
    const [, appId, tid] = trig;
    const app = store.app(workspace, appId);
    const publicRow = (t: TriggerRow) => ({ id: t.id, kind: t.kind, config: t.config, enabled: t.enabled, last_fired_at: t.last_fired_at,
      next_fire_at: t.enabled ? nextOf(t) : null, created_at: t.created_at });
    if (!tid && method === 'GET') {
      send(store.db.prepare('SELECT * FROM app_triggers WHERE app_id=? AND workspace_id=? ORDER BY created_at').all(appId, workspace).map((r) => publicRow(rowOf(r))));
      return true;
    }
    if (!tid && method === 'POST') {
      const v = await body(ctx.req);
      choice(v.kind, ['schedule'] as const, 'Loại kích hoạt');
      if (app.kind !== 'workflow') throw new HttpError(400, 'Chỉ workflow hỗ trợ kích hoạt.');
      const config = scheduleInput(object(v.schedule ?? v)), id = store.id(), at = runner.now();
      if (store.db.prepare('SELECT count(*) AS n FROM app_triggers WHERE app_id=?').get(appId)!.n as number >= 20)
        throw new HttpError(400, 'Tối đa 20 kích hoạt cho mỗi ứng dụng.');
      store.db.prepare('INSERT INTO app_triggers(id,app_id,workspace_id,kind,config,enabled,created_by,last_fired_at,created_at) VALUES(?,?,?,?,?,1,?,?,?)')
        .run(id, appId, workspace, 'schedule', JSON.stringify(config), ctx.identity?.person ?? '', at, new Date(at).toISOString());
      send(publicRow(rowOf(store.db.prepare('SELECT * FROM app_triggers WHERE id=?').get(id)!)), 201);
      return true;
    }
    if (tid && method === 'PATCH') {
      const v = await body(ctx.req);
      if (typeof v.enabled !== 'boolean') throw new HttpError(400, 'enabled cần boolean.');
      // Enabling restarts the clock so the disabled period is not backfilled.
      const changed = store.db.prepare('UPDATE app_triggers SET enabled=?,last_fired_at=CASE WHEN ? THEN ? ELSE last_fired_at END WHERE id=? AND app_id=? AND workspace_id=?')
        .run(v.enabled ? 1 : 0, v.enabled ? 1 : 0, runner.now(), tid, appId, workspace).changes;
      if (!changed) throw new HttpError(404, 'Không tìm thấy kích hoạt.');
      send(publicRow(rowOf(store.db.prepare('SELECT * FROM app_triggers WHERE id=?').get(tid)!)));
      return true;
    }
    if (tid && method === 'DELETE') {
      store.db.prepare('DELETE FROM app_triggers WHERE id=? AND app_id=? AND workspace_id=?').run(tid, appId, workspace);
      send({ ok: true });
      return true;
    }
    return false;
  }
  const list = path.match(/^\/api\/apps\/([\w-]+)\/runs$/);
  if (list && method === 'GET') {
    const appId = list[1];
    store.app(workspace, appId);
    const q = url.searchParams, where = ['app_id=?', 'workspace_id=?'], args: (string | number)[] = [appId, workspace];
    if (q.get('status')) { where.push('status=?'); args.push(choice(q.get('status'), statuses, 'Trạng thái')); }
    if (q.get('trigger')) { where.push('"trigger"=?'); args.push(choice(q.get('trigger'), sources, 'Nguồn')); }
    const limit = Math.min(100, Math.max(1, Number(q.get('limit') ?? 50) || 50));
    if (q.get('cursor')) {
      const [at, id] = Buffer.from(q.get('cursor')!, 'base64url').toString('utf8').split('|');
      if (!at || !id) throw new HttpError(400, 'Cursor không hợp lệ.');
      where.push('(created_at<? OR (created_at=? AND id<?))');
      args.push(at, at, id);
    }
    const rows = store.db.prepare(`SELECT id,app_id,mode,"trigger",status,duration_ms,error,created_at,app_version,person_id,substr(input,1,200) AS input
      FROM runs WHERE ${where.join(' AND ')} ORDER BY created_at DESC,id DESC LIMIT ?`).all(...args, limit + 1);
    const page = rows.slice(0, limit), last = page.at(-1);
    send({
      items: page.map((r) => (own(r) ? r : { ...r, input: '', error: r.error ? 'Lượt chạy thất bại (chi tiết đã ẩn).' : r.error })),
      next_cursor: rows.length > limit && last ? Buffer.from(`${last.created_at}|${last.id}`).toString('base64url') : null,
    });
    return true;
  }
  const one = path.match(/^\/api\/runs\/([\w-]+)$/);
  if (one && method === 'GET') {
    const r = store.db.prepare('SELECT r.*,a.name AS app_name FROM runs r JOIN apps a ON a.id=r.app_id WHERE r.id=? AND r.workspace_id=?').get(one[1], workspace);
    if (!r) throw new HttpError(404, 'Không tìm thấy lượt chạy.');
    const result = r.result ? JSON.parse(String(r.result)) : null;
    const base = { id: r.id, app_id: r.app_id, app_name: r.app_name, mode: r.mode, trigger: r.trigger, status: r.status,
      duration_ms: r.duration_ms, created_at: r.created_at, app_version: r.app_version };
    send(own(r)
      ? { ...base, input: r.input, error: r.error, result, trace: r.trace ? JSON.parse(String(r.trace)) : (result?.trace ?? []) }
      : { ...base, input: '', error: r.error ? 'Lượt chạy thất bại (chi tiết đã ẩn).' : null, result: null, trace: [] });
    return true;
  }
  return false;
}
