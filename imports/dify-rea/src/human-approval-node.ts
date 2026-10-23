// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Node "Người duyệt": the decision is a real xT request (create_record + submit_request), decided only by a human on
// Desk through xT's own decide_request path. Studio never records or accepts a decision; it only reads the request state.
import { createHash } from 'node:crypto';
import type { Citation, RunResult, Trace } from './contracts.ts';
import type { XtTools } from './xt-tools.ts';
import { HttpError } from './validation.ts';
import { envelopeError, xtRef } from './xt-source.ts';

export const humanApprovalNeedsXt = 'Node Người duyệt chỉ chạy khi Studio được mở từ xT.';
export type ApprovalDecision = 'approved' | 'rejected';
// Everything needed to continue the graph after the node at `index` of the topological order.
export interface WaitSnapshot {
    node_id: string;
    index: number;
    request_ref: string;
    timeout_hours?: number;
    paused_at: number;
    values: [string, string][];
    active: string[];
    branches: [string, string][];
    citations: Citation[];
    outputs: string[];
    trace: Trace[];
}
export type WorkflowOutcome = RunResult & { waiting?: WaitSnapshot };

type Envelope = { ok?: boolean; data?: any; error?: { code?: string; message?: string } };

async function call(tools: XtTools, name: string, args: Record<string, unknown>, signal?: AbortSignal) {
    const value = (await tools.call(name, args, signal)) as Envelope;
    if (value?.ok === true) return value.data ?? {};
    const code = value?.error?.code;
    if (code === 'confirmation_required')
        throw new HttpError(409, `xT yêu cầu xác nhận thêm cho ${name}; Studio không tự xác nhận thay người dùng.`);
    if (code === 'not_found' || code === 'access_denied') throw envelopeError(value);
    const message = typeof value?.error?.message === 'string' ? ': ' + value.error.message.slice(0, 300) : '';
    throw new HttpError(
        code === 'invalid_request' || code === 'rule_violation' ? 400 : 502,
        `xT từ chối ${name}${code ? ` (${code})` : ''}${message}`,
    );
}

// Deterministic per run and node so a retried request never creates a second record in xT.
const key = (runId: string, nodeId: string, step: string) =>
    `studio.${createHash('sha256').update(`${runId}\n${nodeId}`).digest('hex').slice(0, 40)}.${step}`;

export function approvalNote(title: string, summary: string, hint?: string) {
    return [title.trim(), summary.trim(), hint?.trim() ? `Người duyệt đề xuất: ${hint.trim()}` : '', 'Gửi từ xT Studio.']
        .filter(Boolean)
        .join('\n\n')
        .slice(0, 1000);
}

// Creates the request draft on a pack-bound request model and submits it; returns the request ref.
export async function requestApproval(
    tools: XtTools,
    request: { run_id: string; node_id: string; model: string; values: Record<string, unknown>; note: string },
    signal?: AbortSignal,
): Promise<string> {
    const described = await call(tools, 'describe_model', { model_id: request.model }, signal);
    if (described.model?.primitive !== 'request')
        throw new HttpError(400, `Mô hình ${request.model} không phải loại yêu cầu duyệt của xT.`);
    const created = await call(
        tools,
        'create_record',
        { model_id: request.model, values: request.values, idempotency_key: key(request.run_id, request.node_id, 'create') },
        signal,
    );
    const draft = xtRef(created.record?.ref);
    const submitted = await call(
        tools,
        'submit_request',
        { ref: draft, note: request.note, idempotency_key: key(request.run_id, request.node_id, 'submit') },
        signal,
    );
    return xtRef(submitted.request?.ref ?? draft);
}

// Reads the request with the caller's own xT session and maps its state to a branch.
export async function approvalDecision(tools: XtTools, ref: string, signal?: AbortSignal): Promise<ApprovalDecision> {
    const data = await call(tools, 'get_record', { ref }, signal);
    const record = data.record ?? {};
    const state = record.state ?? record.values?.state;
    if (state === 'approved') return 'approved';
    if (state === 'rejected') return 'rejected';
    if (state === 'returned') throw new HttpError(409, 'Yêu cầu đang bị trả lại để sửa; chưa được duyệt.');
    if (state === 'withdrawn') throw new HttpError(410, 'Yêu cầu đã bị rút.');
    if (typeof state !== 'string') throw new HttpError(502, 'Bản ghi xT thiếu trạng thái.');
    throw new HttpError(409, 'Yêu cầu chưa được duyệt.');
}
