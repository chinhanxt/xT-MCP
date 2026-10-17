// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { expect, it } from 'vitest';
import { reportOptions } from '../src/report-presets';

const f = (id: string, type: string, extra: Record<string, unknown> = {}) => ({ id, label: id.toUpperCase(), type, multiple: false, pii: false, sensitive: false, ...extra });
const crm = { id: 'co_hoi', fields: [f('title', 'string'), f('customer', 'ref', { ref_model: 'khach_hang' }), f('stage', 'enum'), f('value_milli', 'decimal'), f('owner_person', 'person'), f('expected_close', 'date')] };
const chamCong = { id: 'cham_cong', fields: [f('person', 'person'), f('project', 'ref', { ref_model: 'du_an' }), f('date', 'date'), f('hours_milli', 'decimal'), f('note', 'text'), f('timesheet', 'ref')] };
const baoTri = { id: 'phieu_bao_tri', fields: [f('asset', 'ref'), f('priority', 'enum'), f('est_cost', 'money'), f('state', 'enum')] };

it('CRM: sums the milli value by the stage enum, with no time bucket', () => {
  const o = reportOptions(crm);
  expect(o.presets[0]).toMatchObject({ metric: 's:value_milli', group: 'stage', bucket: '' });
  expect(o.metrics.map((m) => m.value)).toEqual(['count', 's:value_milli']);
  expect(o.groups.map((g) => g.value)).toEqual(['customer', 'stage']);
  expect(o.dates).toEqual([{ id: 'expected_close', label: 'EXPECTED_CLOSE' }]);
});

it('timesheet: sums hours_milli by the project ref per week on the date field', () => {
  const week = reportOptions(chamCong).presets.find((p) => p.bucket === 'week')!;
  expect(week).toMatchObject({ metric: 's:hours_milli', group: 'project', bucket: 'week', dateField: 'date' });
});

it('asset maintenance: counts tickets by state (status-like enum wins over priority)', () => {
  const o = reportOptions(baoTri);
  expect(o.presets.find((p) => p.metric === 'count')).toMatchObject({ group: 'state', bucket: '' });
  expect(o.presets[0]).toMatchObject({ metric: 's:est_cost', group: 'state' });
});

it('uses named metrics, drops pii/multiple/non-numeric fields and falls back to a plain count', () => {
  const o = reportOptions({ fields: [f('salary', 'money', { pii: true }), f('tags', 'enum', { multiple: true }), f('name', 'string')], metrics: [{ id: 'open_total', label: 'Đang mở' }, { label: 'no id' }] });
  expect(o.metrics.map((m) => m.value)).toEqual(['count', 'm:open_total']);
  expect(o.groups).toEqual([]);
  expect(o.presets).toHaveLength(1);
  expect(o.presets[0]).toMatchObject({ metric: 'count', group: '', bucket: '' });
  expect(reportOptions(null).presets).toHaveLength(1);
});
