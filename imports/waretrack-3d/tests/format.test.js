// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
import test from 'node:test'
import assert from 'node:assert/strict'
import { pad2, fmtTime, fmtNum, fmtPct, fmtKmh, fmtMin, statusMeta, STATUS, TYPE_LABEL } from '../src/data/format.js'

test('fmtTime: phút từ nửa đêm sang HH:MM, quay vòng 24 giờ', () => {
  assert.equal(fmtTime(580), '09:40')
  assert.equal(fmtTime(0), '00:00')
  assert.equal(fmtTime(1440), '00:00')
  assert.equal(fmtTime(1500), '01:00')
  assert.equal(fmtTime(-60), '23:00')
  assert.equal(fmtTime(580.9), '09:40')
})

test('pad2, fmtNum, fmtPct, fmtMin', () => {
  assert.equal(pad2(7), '07')
  assert.equal(fmtNum(1412.4), '1,412')
  assert.equal(fmtPct(96.64), '96.6%')
  assert.equal(fmtPct(96.64, 0), '97%')
  assert.equal(fmtMin(0.2), '1 min')
  assert.equal(fmtMin(12.6), '13 min')
})

test('fmtKmh: một chữ số thập phân dưới 10, làm tròn từ 10', () => {
  assert.equal(fmtKmh(4), '4 km/h')
  assert.equal(fmtKmh(4.26), '4.3 km/h')
  assert.equal(fmtKmh(12.4), '12 km/h')
})

test('statusMeta: trạng thái đã biết và dự phòng cho trạng thái lạ', () => {
  assert.deepEqual(statusMeta('truck', 'loading'), STATUS.truck.loading)
  assert.deepEqual(statusMeta('truck', 'khong-co'), { chip: 'khong-co', tone: 'gray' })
  assert.deepEqual(statusMeta('khong-co', undefined), { chip: '—', tone: 'gray' })
})

test('mọi trạng thái có tone hợp lệ; mọi loại thực thể có nhãn', () => {
  const tones = new Set(['green', 'blue', 'orange', 'gray'])
  for (const group of Object.values(STATUS)) for (const meta of Object.values(group)) assert.ok(tones.has(meta.tone))
  for (const type of ['site', 'truck', 'forklift', 'pallet', 'dock', 'charger']) assert.ok(TYPE_LABEL[type])
})
