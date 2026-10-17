// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState } from 'react';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { readToolResult } from './portal-result';

type Data = Record<string, unknown>;
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
const text = (value: unknown) => (value === null || value === undefined || value === '' ? '—' : String(value));
type Unit = { key: string; name: string; count: number };

/** Phòng ban lấy từ trường org_unit của danh bạ đã được phép xem (find_people); không có danh sách riêng nên không bịa. */
export function departmentsOf(people: Data[]): Unit[] {
  const units = new Map<string, Unit>();
  for (const person of people) {
    const unit = obj(person.org_unit), key = String(unit.ref || unit.name || '');
    if (!key) continue;
    const found = units.get(key);
    if (found) found.count++; else units.set(key, { key, name: String(unit.name || unit.ref), count: 1 });
  }
  return [...units.values()].sort((a, b) => a.name.localeCompare(b.name, 'vi'));
}

type Props = { mcp: McpConnection | null; tools: McpTool[] };

export function DepartmentsPanel({ mcp, tools }: Props) {
  const [units, setUnits] = useState<Unit[] | null>(null), [active, setActive] = useState<Unit | null>(null);
  const [members, setMembers] = useState<Data[] | null>(null), [error, setError] = useState(''), [loading, setLoading] = useState(false);
  const seq = useRef(0), live = useRef(true);
  const allowed = tools.some((tool) => tool.name === 'find_people');

  async function load(args: Data): Promise<Data[] | null> {
    if (!mcp || !allowed) { setError('Thiếu quyền: không có công cụ find_people.'); return null; }
    const mine = ++seq.current;
    setLoading(true); setError('');
    try {
      const value = readToolResult(await mcp.client.callTool({ name: 'find_people', arguments: args }) as ToolResult);
      if (!live.current || mine !== seq.current) return null;
      return Array.isArray(value.people) ? value.people as Data[] : [];
    } catch (e) {
      if (live.current && mine === seq.current) setError((e as Error).message);
      return null;
    } finally { if (live.current && mine === seq.current) setLoading(false); }
  }
  useEffect(() => {
    live.current = true;
    void load({ limit: 100 }).then((people) => { if (people) setUnits(departmentsOf(people)); });
    return () => { live.current = false; seq.current++; };
  }, [mcp]); // eslint-disable-line
  async function open(unit: Unit) {
    setActive(unit); setMembers(null);
    const people = await load({ org_unit: unit.key, limit: 100 });
    if (people) setMembers(people);
  }
  return <section className="portal-departments" aria-label="Phòng ban">
    <h3>Phòng ban</h3>
    {units === null && !error && <p role="status">Đang tải danh bạ…</p>}
    {units && !units.length && <p role="status">Danh bạ bạn được xem chưa ghi nhận phòng ban nào.</p>}
    {units && units.length > 0 && <div className="portal-actions" role="group" aria-label="Chọn phòng ban">{units.map((unit) =>
      <button key={unit.key} aria-pressed={active?.key === unit.key} disabled={loading} onClick={() => void open(unit)}>{`${unit.name} (${unit.count})`}</button>)}</div>}
    {active && members && <div className="portal-results" aria-label={`Thành viên ${active.name}`}>
      {members.length === 0 ? <p role="status">Phòng ban này chưa có thành viên bạn được xem.</p> : members.map((person, i) => <article className="portal-entry" key={String(person.ref || i)}>
        <strong>{text(person.name)}</strong><dl><div><dt>Chức danh</dt><dd>{text(person.title)}</dd></div><div><dt>Email</dt><dd>{text(person.email)}</dd></div></dl></article>)}
    </div>}
    {loading && <p role="status">Đang tải…</p>}
    {error && <p role="alert" className="note portal-error" data-state="error">{error}</p>}
  </section>;
}
