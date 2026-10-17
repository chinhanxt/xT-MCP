// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { expect, it } from 'vitest';
import { kindFieldLabel, roleLabel, toolLabel, valueLabel } from '../src/portal-labels';

it('labels known tools in Vietnamese and keeps unknown app tool names', () => {
  expect(toolLabel('decide_request')).toBe('Duyệt yêu cầu');
  expect(toolLabel('find_people')).toBe('Tìm nhân sự');
  expect(toolLabel('show_org_chart')).toBe('show_org_chart');
});
it('labels roles, falling back to the raw role', () => {
  expect(['staff', 'hr', 'accountant'].map(roleLabel)).toEqual(['Nhân viên', 'Nhân sự', 'Kế toán']);
  expect(roleLabel('custom_role')).toBe('custom_role');
});
it('inbox kind is not called "Loại", warehouse kind keeps it; units are Vietnamese', () => {
  expect(kindFieldLabel('approval')).toBe('Hộp thư');
  expect(kindFieldLabel('receive')).toBe('Loại');
  expect(valueLabel('unit', 'pcs')).toBe('cái');
});
