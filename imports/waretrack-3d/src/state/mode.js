// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
//
// Hai chế độ tách bạch:
//  - "Mô phỏng": chạy hoàn toàn trong trình duyệt, dữ liệu minh họa, không bao giờ gọi adapter/ghi.
//  - "Dữ liệu nghiệp vụ": chỉ đọc qua adapter; mô phỏng dừng, không hiển thị dữ liệu giả.

export const MODES = Object.freeze({ SIMULATION: 'simulation', BUSINESS: 'business' })

export const MODE_LABEL = Object.freeze({
  [MODES.SIMULATION]: 'Mô phỏng',
  [MODES.BUSINESS]: 'Dữ liệu nghiệp vụ',
})

export const MODE_BADGE = Object.freeze({
  [MODES.SIMULATION]: 'Mô phỏng — dữ liệu minh họa',
  [MODES.BUSINESS]: 'Dữ liệu nghiệp vụ — chỉ đọc',
})

export const BUSINESS_IDLE = Object.freeze({ state: 'idle' })

/** Nhãn cho sơ đồ 3D: chưa có ánh xạ vị trí thực tế thì chỉ là sơ đồ minh họa. */
export function diagramLabel(hasLocationMapping = false) {
  return hasLocationMapping
    ? 'Sơ đồ kho theo vị trí đã ánh xạ'
    : 'Sơ đồ minh họa — chưa ánh xạ vị trí thực tế'
}

/**
 * Một khung hình: chỉ chạy các bước (mô phỏng, thế giới 3D, ...) ở chế độ mô phỏng.
 * @returns {boolean} true nếu đã chạy
 */
export function runFrame(state, steps, dt, elapsed) {
  if (state.mode !== MODES.SIMULATION) return false
  for (const step of steps) step.update(dt, elapsed)
  return true
}

/**
 * @param {{ store: { state: object, set: Function }, adapter: { load: () => Promise<object> } }} deps
 */
export function createModeController({ store, adapter }) {
  let token = 0

  async function refresh() {
    if (store.state.mode !== MODES.BUSINESS) return
    const mine = ++token
    store.set({ business: { state: 'loading' } })
    const result = await adapter.load()
    if (mine !== token || store.state.mode !== MODES.BUSINESS) return
    store.set({ business: result })
  }

  function setMode(mode) {
    if (!Object.values(MODES).includes(mode)) throw new Error(`Chế độ không hợp lệ: ${mode}`)
    if (mode === store.state.mode) return Promise.resolve()
    token++
    if (mode === MODES.SIMULATION) {
      store.set({ mode, business: BUSINESS_IDLE })
      return Promise.resolve()
    }
    store.set({ mode })
    return refresh()
  }

  return { setMode, refresh }
}
