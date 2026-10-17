// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
import test, { beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { store } from '../src/state/store.js'
import { MODES, MODE_LABEL, MODE_BADGE, BUSINESS_IDLE, diagramLabel, runFrame, createModeController } from '../src/state/mode.js'

const snapshot = { ...store.state }
beforeEach(() => store.set({ ...snapshot }))

function spyAdapter(result = { state: 'ready', data: {}, locationMapping: false }) {
  let loads = 0
  let release
  const gate = new Promise((r) => { release = r })
  return {
    get loads() { return loads },
    release,
    adapter: { async load() { loads++; await gate; return result } },
  }
}

test('mặc định là Mô phỏng; nhãn và huy hiệu đúng, có từ "minh họa"', () => {
  assert.equal(store.state.mode, MODES.SIMULATION)
  assert.deepEqual(store.state.business, BUSINESS_IDLE)
  assert.equal(MODE_LABEL.simulation, 'Mô phỏng')
  assert.equal(MODE_LABEL.business, 'Dữ liệu nghiệp vụ')
  assert.equal(MODE_BADGE.simulation, 'Mô phỏng — dữ liệu minh họa')
})

test('chưa có ánh xạ vị trí -> "sơ đồ minh họa", không gọi là digital twin thời gian thực', () => {
  const label = diagramLabel(false)
  assert.match(label, /^Sơ đồ minh họa/)
  for (const text of [label, ...Object.values(MODE_BADGE)]) assert.doesNotMatch(text, /digital twin|thời gian thực|real-?time|live/i)
})

test('giao diện không còn chữ "Live" (top bar)', () => {
  const src = readFileSync(new URL('../src/ui/topbar.js', import.meta.url), 'utf8')
  assert.doesNotMatch(src, /\bLive\b/)
  assert.doesNotMatch(src, />\s*Live\s*</)
})

test('runFrame: chỉ chạy các bước ở chế độ mô phỏng', () => {
  let n = 0
  const steps = [{ update: () => n++ }, { update: () => n++ }]
  assert.equal(runFrame({ mode: MODES.SIMULATION }, steps, 0.016, 1), true)
  assert.equal(n, 2)
  assert.equal(runFrame({ mode: MODES.BUSINESS }, steps, 0.016, 1), false)
  assert.equal(n, 2)
})

test('vòng lặp mô phỏng không bao giờ gọi adapter', () => {
  const spy = spyAdapter()
  createModeController({ store, adapter: spy.adapter })
  const steps = [{ update() {} }]
  for (let i = 0; i < 100; i++) runFrame(store.state, steps, 0.016, i)
  assert.equal(spy.loads, 0)
})

test('chuyển sang nghiệp vụ gọi adapter đúng một lần, kết quả vào store', async () => {
  const spy = spyAdapter({ state: 'not_connected', message: 'x' })
  const ctl = createModeController({ store, adapter: spy.adapter })
  const p = ctl.setMode(MODES.BUSINESS)
  assert.equal(store.state.mode, MODES.BUSINESS)
  assert.equal(store.state.business.state, 'loading')
  spy.release()
  await p
  assert.equal(spy.loads, 1)
  assert.deepEqual(store.state.business, { state: 'not_connected', message: 'x' })
  await ctl.setMode(MODES.BUSINESS)
  assert.equal(spy.loads, 1)
})

test('quay lại mô phỏng không gọi adapter, xóa dữ liệu nghiệp vụ và bỏ kết quả đến muộn', async () => {
  const spy = spyAdapter({ state: 'ready', data: { warehouse: [] }, locationMapping: false })
  const ctl = createModeController({ store, adapter: spy.adapter })
  const p = ctl.setMode(MODES.BUSINESS)
  await ctl.setMode(MODES.SIMULATION)
  assert.equal(spy.loads, 1)
  assert.equal(store.state.mode, MODES.SIMULATION)
  assert.deepEqual(store.state.business, BUSINESS_IDLE)
  spy.release()
  await p
  assert.deepEqual(store.state.business, BUSINESS_IDLE)
  assert.equal(spy.loads, 1)
})

test('refresh ngoài chế độ nghiệp vụ không làm gì; chế độ lạ bị từ chối', async () => {
  const spy = spyAdapter()
  const ctl = createModeController({ store, adapter: spy.adapter })
  await ctl.refresh()
  assert.equal(spy.loads, 0)
  assert.throws(() => ctl.setMode('khac'))
})

test('mã mô phỏng và dữ liệu không tham chiếu adapter/cầu nối/mạng', () => {
  const roots = ['src/state/simulation.js', 'src/data', 'src/three']
  const files = []
  const walk = (p) => {
    const abs = new URL(`../${p}`, import.meta.url)
    if (statSync(abs).isDirectory()) for (const f of readdirSync(abs)) walk(join(p, f))
    else if (p.endsWith('.js')) files.push(p)
  }
  roots.forEach(walk)
  assert.ok(files.length > 10)
  for (const f of files) {
    const src = readFileSync(new URL(`../${f}`, import.meta.url), 'utf8')
    assert.doesNotMatch(src, /businessAdapter|xtPortal|callTool|\bfetch\s*\(|XMLHttpRequest|WebSocket/, f)
  }
})
