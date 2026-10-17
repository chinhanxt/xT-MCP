// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import test from 'node:test'
import assert from 'node:assert/strict'
import { db, ENTITY_TYPES } from '../src/data/db.js'
import { TRUCKS, DOCKS, FORKLIFTS, CHARGERS, PALLETS, SHIPMENTS, SITES, SKUS, CARRIERS } from '../src/data/mockData.js'
import {
  siteStats, kpis, siteSelectorSub, siteMenuSub, dockName, shipmentTracking, fleetRows, fleetCounts, search,
} from '../src/data/selectors.js'

const siteIds = new Set(SITES.map((s) => s.id))

test('mockData: khóa ngoại trỏ đúng thực thể và mã không trùng', () => {
  for (const list of [SITES, DOCKS, TRUCKS, FORKLIFTS, CHARGERS, PALLETS, SHIPMENTS]) {
    assert.equal(new Set(list.map((e) => e.id)).size, list.length)
  }
  for (const list of [DOCKS, TRUCKS, FORKLIFTS, CHARGERS, PALLETS]) for (const e of list) assert.ok(siteIds.has(e.siteId), e.id)
  for (const t of TRUCKS) {
    assert.ok(CARRIERS[t.carrier], t.id)
    assert.ok(SHIPMENTS.some((s) => s.id === t.shipmentId), t.id)
  }
  for (const p of PALLETS) assert.ok(SKUS[p.sku], p.id)
  for (const s of SITES) assert.ok(TRUCKS.some((t) => t.id === s.featuredTruckId), s.id)
})

test('db.get / list / all', () => {
  assert.equal(db.get('site', SITES[0].id), SITES[0])
  assert.equal(db.get('truck', 'KHONG-CO'), undefined)
  assert.equal(db.get('khong-co', 'x'), undefined)
  assert.equal(db.list('truck').length, TRUCKS.length)
  assert.equal(db.list('truck', 'ALL').length, TRUCKS.length)
  const sid = SITES[0].id
  assert.deepEqual(db.list('dock', sid), DOCKS.filter((d) => d.siteId === sid))
  assert.deepEqual(db.list('site', sid).map((s) => s.id), [sid])
  assert.deepEqual(db.list('khong-co'), [])
  const total = ENTITY_TYPES.reduce((n, type) => n + db.list(type).length, 0)
  assert.equal(db.all().length, total)
})

test('db.shipmentForTruck và truckAtDock', () => {
  const t = TRUCKS[0]
  assert.equal(db.shipmentForTruck(t.id).id, t.shipmentId)
  assert.equal(db.shipmentForTruck('KHONG-CO'), undefined)
  const busy = TRUCKS.find((x) => x.dockId && ['loading', 'unloading'].includes(x.status))
  if (busy) assert.equal(db.truckAtDock(busy.dockId), busy)
  assert.equal(db.truckAtDock('KHONG-CO'), undefined)
})

test('siteStats: một kho khớp dữ liệu, ALL bằng tổng các kho', () => {
  const sid = SITES[0].id
  const one = siteStats(sid)
  assert.equal(one.stock, SITES[0].stock)
  assert.equal(one.docksTotal, DOCKS.filter((d) => d.siteId === sid).length)
  assert.equal(one.trucksTotal, TRUCKS.filter((t) => t.siteId === sid).length)
  const all = siteStats('ALL')
  assert.equal(all.stock, SITES.reduce((a, s) => a + s.stock, 0))
  assert.equal(all.docksTotal, DOCKS.length)
  assert.equal(all.forkliftsTotal, FORKLIFTS.length)
  assert.equal(siteStats('KHONG-CO').docksTotal, 0)
  assert.equal(siteStats('KHONG-CO').fullPct, 0)
})

test('kpis và nhãn tóm tắt kho', () => {
  const k = kpis('ALL')
  assert.deepEqual(k.map((x) => x.key), ['stock', 'trucks', 'ontime'])
  assert.match(k[0].sub, /all sites/)
  assert.match(siteSelectorSub(SITES[0].id), /^\d+% full · \d+\/\d+ docked$/)
  assert.match(siteMenuSub(SITES[0].id), /^\d+\/\d+ docked · \d+ inbound$/)
  assert.equal(dockName('KHONG-CO'), '—')
  assert.equal(dockName(DOCKS[0].id), DOCKS[0].name)
})

test('shipmentTracking: mọi xe có đúng một bước hiện tại; xe lạ trả null', () => {
  assert.equal(shipmentTracking('KHONG-CO'), null)
  for (const t of TRUCKS) {
    const r = shipmentTracking(t.id)
    assert.ok(r, t.id)
    assert.equal(r.steps.filter((s) => s.state === 'current').length, 1, t.id)
    assert.ok(r.steps.length >= 4, t.id)
    assert.ok(r.header.startsWith(t.id))
  }
})

test('fleetRows và fleetCounts khớp số lượng thực thể', () => {
  const sid = SITES[0].id
  assert.equal(fleetRows(sid, 'forklifts').length, FORKLIFTS.filter((f) => f.siteId === sid).length)
  assert.equal(fleetRows('ALL', 'trucks').length, TRUCKS.length)
  assert.ok(fleetRows(sid, 'docks').length >= DOCKS.filter((d) => d.siteId === sid).length)
  assert.deepEqual(Object.keys(fleetCounts('ALL')), ['docks', 'forklifts', 'trucks'])
  assert.equal(fleetCounts('ALL').trucks, String(TRUCKS.length))
})

test('search: rỗng, khớp theo mã không phân biệt hoa thường, tối đa 8 kết quả', () => {
  assert.deepEqual(search('   '), [])
  const t = TRUCKS[0]
  const hit = search(t.id.toLowerCase()).find((r) => r.type === 'truck' && r.id === t.id)
  assert.ok(hit)
  assert.equal(hit.siteId, t.siteId)
  assert.deepEqual(search('zzzz-khong-co'), [])
  assert.ok(search('a').length <= 8)
})
