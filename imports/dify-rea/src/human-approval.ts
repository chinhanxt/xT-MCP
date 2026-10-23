// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Pausing a run at "Người duyệt" and resuming it. Resume is user-initiated with a live xT session: Studio reads the request
// state through that user's xT tools and continues the saved graph position; it never decides the request itself.
import { hashToken, now, type RouteContext } from './http.ts';
import type { Store } from './store.ts';
import type { AppConfig, RunResult } from './contracts.ts';
import { HttpError } from './validation.ts';
import { runWorkflow } from './workflow.ts';
import { approvalDecision, humanApprovalNeedsXt, type WaitSnapshot, type WorkflowOutcome } from './human-approval-node.ts';

export const expiredMessage = 'Đã quá hạn duyệt.';

// Persists the resumable snapshot and marks the run waiting; returns the result without the snapshot.
export function holdForApproval(
    store: Store,
    runId: string,
    workspace: string,
    config: AppConfig,
    outcome: WorkflowOutcome,
    durationMs: number,
): RunResult {
    const { waiting, ...result } = outcome;
    const snapshot = waiting!;
    result.run_id = runId;
    const expires = snapshot.timeout_hours ? snapshot.paused_at + snapshot.timeout_hours * 3600000 : null;
    store.transaction(() => {
        store.db
            .prepare(
                `INSERT INTO run_waits(run_id,workspace_id,node_id,request_ref,config,snapshot,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)
         ON CONFLICT(run_id) DO UPDATE SET node_id=excluded.node_id,request_ref=excluded.request_ref,config=excluded.config,
         snapshot=excluded.snapshot,expires_at=excluded.expires_at,created_at=excluded.created_at,resumed_at=NULL`,
            )
            .run(
                runId,
                workspace,
                snapshot.node_id,
                snapshot.request_ref,
                JSON.stringify(config),
                JSON.stringify(snapshot),
                expires,
                now(),
            );
        store.db
            .prepare('UPDATE runs SET status=?,result=?,duration_ms=?,trace=? WHERE id=?')
            .run('waiting', JSON.stringify(result), durationMs, JSON.stringify(result.trace), runId);
    });
    return result;
}

export async function routeRunResume(ctx: RouteContext): Promise<boolean> {
    const { store, path, method, workspace, send, busy } = ctx;
    const match = path.match(/^\/api\/runs\/([\w-]+)\/resume$/);
    if (!match || method !== 'POST') return false;
    const runId = match[1];
    const row = store.db
        .prepare(
            'SELECT r.status,r.person_id,r.config_hash,r.duration_ms,w.config,w.snapshot,w.expires_at FROM runs r JOIN run_waits w ON w.run_id=r.id WHERE r.id=? AND r.workspace_id=?',
        )
        .get(runId, workspace);
    if (!row) throw new HttpError(404, 'Không tìm thấy lượt chạy chờ duyệt.');
    if (ctx.identity && ctx.identity.role !== 'admin' && row.person_id !== ctx.identity.person)
        throw new HttpError(403, 'Chỉ người chạy hoặc quản trị viên được chạy tiếp lượt này.');
    if (row.status !== 'waiting') throw new HttpError(409, 'Lượt chạy không còn chờ duyệt.');
    if (!ctx.coreTools) throw new HttpError(400, humanApprovalNeedsXt);
    if (busy.has(runId)) throw new HttpError(409, 'Lượt chạy không còn chờ duyệt.');
    if (busy.size >= 3) throw new HttpError(429, 'Đã có 3 lượt chạy đồng thời; thử lại sau.');
    // Compare-and-set: exactly one resume claims the waiting run.
    if (store.db.prepare("UPDATE runs SET status='running' WHERE id=? AND status='waiting'").run(runId).changes !== 1)
        throw new HttpError(409, 'Lượt chạy không còn chờ duyệt.');
    busy.add(runId);
    const started = Date.now(),
        previous = Number(row.duration_ms ?? 0);
    const snapshot = JSON.parse(String(row.snapshot)) as WaitSnapshot,
        config = JSON.parse(String(row.config)) as AppConfig;
    const fail = (error: unknown, trace: unknown) => {
        store.db
            .prepare('UPDATE runs SET status=?,error=?,result=?,duration_ms=?,trace=? WHERE id=?')
            .run(
                'error',
                error instanceof Error ? error.message : 'Lỗi',
                JSON.stringify({ trace }),
                previous + Date.now() - started,
                JSON.stringify(trace),
                runId,
            );
        store.db.prepare('UPDATE run_waits SET resumed_at=? WHERE run_id=?').run(now(), runId);
    };
    try {
        if (row.expires_at !== null && Date.now() > Number(row.expires_at)) {
            const error = new HttpError(410, expiredMessage);
            fail(error, snapshot.trace);
            throw Object.assign(error, { trace: snapshot.trace });
        }
        if (hashToken(JSON.stringify(config)) !== row.config_hash) {
            const error = new HttpError(409, 'Cấu hình của lượt chạy không khớp; không chạy tiếp được.');
            fail(error, snapshot.trace);
            throw error;
        }
        const controller = new AbortController();
        ctx.res.once('close', () => {
            if (!ctx.res.writableEnded) controller.abort();
        });
        const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(180000)]);
        let decision;
        try {
            decision = await approvalDecision(ctx.coreTools, snapshot.request_ref, signal);
        } catch (error) {
            // Still pending, unreadable or a transport failure: the run keeps waiting for a later check.
            if (error instanceof HttpError && error.status === 410) fail(error, snapshot.trace);
            else store.db.prepare("UPDATE runs SET status='waiting' WHERE id=? AND status='running'").run(runId);
            throw error;
        }
        let outcome: WorkflowOutcome;
        try {
            outcome = await runWorkflow(
                store,
                workspace,
                config,
                new Map(snapshot.values).get('input') ?? '',
                signal,
                {},
                ctx.coreTools,
                !!ctx.identity,
                {
                    id: runId,
                    resume: { snapshot, decision },
                },
            );
        } catch (error) {
            fail(error, (error as { trace?: unknown }).trace ?? []);
            throw error;
        }
        if (outcome.waiting) {
            send(holdForApproval(store, runId, workspace, config, outcome, previous + Date.now() - started));
            return true;
        }
        const result: RunResult = { ...outcome, run_id: runId };
        store.transaction(() => {
            store.db
                .prepare('UPDATE runs SET status=?,result=?,error=NULL,duration_ms=?,trace=? WHERE id=?')
                .run('success', JSON.stringify(result), previous + Date.now() - started, JSON.stringify(result.trace), runId);
            store.db.prepare('UPDATE run_waits SET resumed_at=? WHERE run_id=?').run(now(), runId);
        });
        send(result);
        return true;
    } finally {
        busy.delete(runId);
    }
}
