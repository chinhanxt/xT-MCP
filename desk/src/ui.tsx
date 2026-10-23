// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Thành phần trình bày dùng chung của Desk v2 (mockup docs/mockups/desk). Không gọi tool, không giữ dữ liệu.
import type { ReactNode } from 'react';
import { Ban, CircleCheck, CircleX, Hourglass, Info, type LucideIcon } from 'lucide-react';

export type Tone = 'ok' | 'held' | 'block' | 'neutral' | 'info';

const OK = new Set(['approved', 'done', 'completed', 'active', 'enabled', 'on_track', 'posted', 'closed', 'resolved', 'running', 'published', 'ok']);
const HELD = new Set(['in_review', 'submitted', 'pending', 'draft', 'returned', 'due_soon', 'at_risk', 'open', 'waiting', 'queued', 'requested', 'confirm', 'onboarding']);
const BLOCK = new Set(['rejected', 'cancelled', 'canceled', 'overdue', 'breached', 'failed', 'error', 'denied', 'blocked', 'revoked', 'expired']);

/** Màu chip theo giá trị máy chủ trả về (mã trạng thái gốc, không phải nhãn hiển thị); giá trị lạ thì trung tính. */
export function toneOf(raw: unknown): Tone {
  const value = String(raw ?? '').toLowerCase();
  if (OK.has(value)) return 'ok';
  if (HELD.has(value)) return 'held';
  if (BLOCK.has(value)) return 'block';
  return 'neutral';
}

const ICON: Record<Tone, LucideIcon> = { ok: CircleCheck, held: Hourglass, block: CircleX, neutral: Info, info: Info };

/** Chip trạng thái của mockup (ok/held/block/neutral/info). Lớp `pill` để không trùng `.chip` của tool-call. */
export function Pill({ tone, children, icon }: { tone: Tone; children: ReactNode; icon?: LucideIcon }) {
  const Icon = icon ?? ICON[tone];
  return <span className={`pill ${tone}`}><Icon aria-hidden="true" />{children}</span>;
}

export const BlockedIcon = Ban;
