// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { store, bus } from '../src/state/store.js'
import { db } from '../src/data/db.js'

const snapshot = { ...store.state }
beforeEach(() => store.set({ ...snapshot }))

test('set: chỉ thông báo khi có khóa đổi giá trị, kèm danh sách khóa đổi', () => {
  const calls = []
  const off = store.subscribe((state, prev, changed) => calls.push(changed))
  store.set({ fleetTab: 'docks' })
  store.set({ fleetTab: 'trucks', searchQuery: 'x' })
  off()
  store.set({ fleetTab: 'docks' })
  assert.deepEqual(calls, [['fleetTab', 'searchQuery']])
})

test('select: chọn xe tải đặt trackedTruckId và chuyển sang kho của xe', () => {
  const truck = db.list('truck').find((t) => t.siteId !== store.state.siteId)
  store.select({ type: 'truck', id: truck.id })
  assert.deepEqual(store.state.selection, { type: 'truck', id: truck.id })
  assert.equal(store.state.trackedTruckId, truck.id)
  assert.equal(store.state.siteId, truck.siteId)
})

test('select: thực thể không tồn tại bị bỏ qua; null xóa lựa chọn và follow', () => {
  store.select({ type: 'truck', id: 'KHONG-CO' })
  assert.equal(store.state.selection, null)
  store.set({ followId: 'FL-01' })
  store.select(null)
  assert.equal(store.state.followId, null)
})

test('setSite: đặt lại lựa chọn và theo xe nổi bật của kho', () => {
  const site = db.sites()[1]
  store.set({ selection: { type: 'dock', id: 'x' }, menu: 'site' })
  store.setSite(site.id)
  assert.equal(store.state.siteId, site.id)
  assert.equal(store.state.selection, null)
  assert.equal(store.state.menu, null)
  assert.equal(store.state.trackedTruckId, site.featuredTruckId)
})

test('setSite ALL giữ xe đang theo dõi', () => {
  const before = store.state.trackedTruckId
  store.setSite('ALL')
  assert.equal(store.state.siteId, 'ALL')
  assert.equal(store.state.trackedTruckId, before)
})

test('bus: on/off/emit và hàm hủy đăng ký', () => {
  const got = []
  const fn = (p) => got.push(p)
  const off = bus.on('t:evt', fn)
  bus.emit('t:evt', 1)
  off()
  bus.emit('t:evt', 2)
  bus.on('t:evt', fn)
  bus.off('t:evt', fn)
  bus.emit('t:evt', 3)
  assert.deepEqual(got, [1])
})
