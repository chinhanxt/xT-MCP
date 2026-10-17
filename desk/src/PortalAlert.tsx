// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import type { ReactNode } from 'react';
import { stateHint, stateTitle, type ErrorKind } from './portal-state';

/** Thông báo lỗi dùng chung cho mọi panel: thiếu quyền, hết phiên hoặc lỗi chung (role=alert). */
export function PortalAlert({ text, kind, children }: { text: string; kind: ErrorKind; children?: ReactNode }) {
  if (!text) return null;
  return <div role={kind === 'confirm' ? 'status' : 'alert'} className={`note portal-${kind}`} data-state={kind}>
    {kind !== 'error' && <strong>{stateTitle[kind]}. </strong>}{text}{children}
    {stateHint[kind] && <p>{stateHint[kind]}</p>}
  </div>;
}
