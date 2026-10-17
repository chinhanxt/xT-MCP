// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ReactNode } from 'react';
import { EXPIRED_TEXT, stateHint, stateTitle, type ErrorKind } from './portal-state';

/** Thông báo lỗi dùng chung cho mọi panel: thiếu quyền, hết phiên hoặc lỗi chung (role=alert). */
export function PortalAlert({ text, kind, children }: { text: string; kind: ErrorKind; children?: ReactNode }) {
  if (!text) return null;
  // Hết phiên: câu thống nhất kèm hành động; lời gốc của máy chủ giữ trong tooltip.
  const shown = kind === 'expired' ? EXPIRED_TEXT : text;
  return <div role={kind === 'confirm' ? 'status' : 'alert'} className={`note portal-${kind}`} data-state={kind} title={shown !== text ? text : undefined}>
    {kind !== 'error' && <strong>{stateTitle[kind]}. </strong>}{shown}{children}
    {stateHint[kind] && <p>{stateHint[kind]}</p>}
  </div>;
}
