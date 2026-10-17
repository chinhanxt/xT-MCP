// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  READ_TOOLS, MODELS, NOT_CONNECTED_MESSAGE, createBusinessAdapter, resolvePortalBridge, toRows, field, display, formatMilli,
} from '../src/portal/businessAdapter.js'

const ok = (items) => ({ structuredContent: { data: { items } } })

function fakeBridge(handler) {
  const calls = []
  return {
    calls,
    requestCalls: 0,
    request() { this.requestCalls++; throw new Error('không được gọi') },
    async callTool(name, args) { calls.push([name, args]); return handler(name, args) },
  }
}

test('không có cầu nối -> not_connected, không có dữ liệu', async () => {
  for (const bridge of [null, undefined, {}, { callTool: 'x' }]) {
    const r = await createBusinessAdapter({ getBridge: () => bridge }).load()
    assert.deepEqual(r, { state: 'not_connected', message: NOT_CONNECTED_MESSAGE })
  }
})

test('có cầu nối: chỉ gọi search_records cho 3 model kho, không dùng request', async () => {
  const bridge = fakeBridge((name, args) => ok([{ id: `${args.model_id}:1`, values: { code: 'K1' } }]))
  const r = await createBusinessAdapter({ getBridge: () => bridge }).load()
  assert.equal(r.state, 'ready')
  assert.equal(r.locationMapping, false)
  assert.deepEqual(Object.keys(r.data).sort(), Object.keys(MODELS).sort())
  assert.equal(bridge.calls.length, 3)
  for (const [name, args] of bridge.calls) {
    assert.ok(READ_TOOLS.includes(name))
    assert.equal(name, 'search_records')
    assert.ok(args.model_id in MODELS)
    assert.deepEqual(args.fields, [...MODELS[args.model_id]])
  }
  assert.equal(bridge.requestCalls, 0)
})

test('cầu nối lỗi hoặc phiên bị thu hồi -> error, không trả dữ liệu', async () => {
  const bridge = fakeBridge(() => { throw new Error('Phiên đã kết thúc') })
  const r = await createBusinessAdapter({ getBridge: () => bridge }).load()
  assert.deepEqual(r, { state: 'error', message: 'Phiên đã kết thúc' })
  assert.equal('data' in r, false)
})

test('một model lỗi -> toàn bộ là error; không trả kết quả một phần', async () => {
  const bridge = fakeBridge((name, args) => (args.model_id === 'stock_balance'
    ? { isError: true, structuredContent: { error: { message: 'Thiếu quyền' } } }
    : ok([])))
  const r = await createBusinessAdapter({ getBridge: () => bridge }).load()
  assert.equal(r.state, 'error')
  assert.equal(r.message, 'Thiếu quyền')
  assert.equal('data' in r, false)
})

test('phản hồi không có danh sách -> error, không coi là rỗng', async () => {
  const bridge = fakeBridge(() => ({ structuredContent: { data: { total: 3 } } }))
  assert.equal((await createBusinessAdapter({ getBridge: () => bridge }).load()).state, 'error')
})

test('adapter chỉ có phương thức đọc và bị đóng băng', () => {
  const adapter = createBusinessAdapter({ getBridge: () => null })
  assert.deepEqual(Object.keys(adapter), ['load'])
  assert.ok(Object.isFrozen(adapter))
  assert.deepEqual([...READ_TOOLS], ['search_records', 'get_record', 'aggregate_records'])
  assert.ok(Object.isFrozen(READ_TOOLS))
  for (const name of Object.keys(adapter)) assert.doesNotMatch(name, /create|update|delete|write|submit|approve|reject|decide|post|put|patch|set/i)
})

test('resolvePortalBridge: chỉ khi nhúng cùng origin với ?integrated=1', () => {
  const portal = { callTool() {} }
  const parent = { xtPortal: portal }
  assert.equal(resolvePortalBridge({ parent, location: { search: '?integrated=1' } }), portal)
  assert.equal(resolvePortalBridge({ parent, location: { search: '' } }), null)
  assert.equal(resolvePortalBridge({ parent, location: { search: '?integrated=0' } }), null)
  const self = { location: { search: '?integrated=1' } }
  self.parent = self
  assert.equal(resolvePortalBridge(self), null)
  assert.equal(resolvePortalBridge({ parent: {}, location: { search: '?integrated=1' } }), null)
  const blocked = { location: { search: '?integrated=1' }, get parent() { throw new Error('cross-origin') } }
  assert.equal(resolvePortalBridge(blocked), null)
  assert.equal(resolvePortalBridge(null), null)
})

test('toRows đọc structuredContent hoặc JSON trong text; field/display/formatMilli', () => {
  assert.deepEqual(toRows({ content: [{ type: 'text', text: JSON.stringify({ items: [{ id: 1 }] }) }] }), [{ id: 1 }])
  assert.deepEqual(toRows({ structuredContent: { rows: [] } }), [])
  assert.throws(() => toRows({ content: [{ type: 'text', text: 'không phải JSON' }] }))
  assert.equal(field({ values: { name: 'A' } }, 'name'), 'A')
  assert.equal(field({ name: 'B' }, 'name'), 'B')
  assert.equal(display({ name: 'Kho 1' }), 'Kho 1')
  assert.equal(display(null), '—')
  assert.equal(display([1, 2]), '2 dòng')
  assert.equal(formatMilli(1500), '1,5')
  assert.equal(formatMilli('x'), '—')
})
