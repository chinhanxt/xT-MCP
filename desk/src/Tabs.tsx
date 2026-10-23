// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Tab thật (role=tablist/tab) dùng chung: id `${prefix}-tab-${id}` điều khiển `${prefix}-panel-${id}`, mũi tên trái/phải chuyển tab.
import type { ReactNode } from 'react';

export type TabDef<T extends string> = { id: T; label: string };

export function TabList<T extends string>({ label, prefix, tabs, value, onChange }: { label: string; prefix: string; tabs: readonly TabDef<T>[]; value: T; onChange: (id: T) => void }) {
  return <div className="tabs" role="tablist" aria-label={label}>{tabs.map(t => <button key={t.id} type="button" role="tab" id={`${prefix}-tab-${t.id}`} aria-controls={`${prefix}-panel-${t.id}`} aria-selected={value === t.id} tabIndex={value === t.id ? 0 : -1} onClick={() => onChange(t.id)}
    onKeyDown={e => {
      if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
      const i = tabs.findIndex(x => x.id === value), next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length].id;
      onChange(next); document.getElementById(`${prefix}-tab-${next}`)?.focus();
    }}>{t.label}</button>)}</div>;
}

export function TabPanel({ prefix, id, active, children, className }: { prefix: string; id: string; active: boolean; children: ReactNode; className?: string }) {
  return <div role="tabpanel" id={`${prefix}-panel-${id}`} aria-labelledby={`${prefix}-tab-${id}`} hidden={!active} className={className}>{children}</div>;
}
