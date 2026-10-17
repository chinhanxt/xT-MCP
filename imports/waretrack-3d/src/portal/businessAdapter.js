// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
//
// Adapter đọc dữ liệu nghiệp vụ (chỉ đọc) qua cầu nối của cổng xT.
//
// - Không giữ token, không gọi backend trực tiếp: mọi lời gọi đi qua `bridge.callTool` do cổng
//   cung cấp (phiên + DPoP nằm ở cổng). `bridge.request` không bao giờ được dùng.
// - Chỉ ba tool đọc được phép (READ_TOOLS). Adapter không có phương thức ghi.
// - Không có cầu nối -> trạng thái `not_connected`; cầu nối lỗi / phiên bị thu hồi -> `error`.
//   Không bao giờ trả dữ liệu giả.

export const READ_TOOLS = Object.freeze(['search_records', 'get_record', 'aggregate_records'])

/** Model kho được đọc và các trường hiển thị (khớp danh mục `packages/kho-hang`). */
export const MODELS = Object.freeze({
  warehouse: Object.freeze(['code', 'name']),
  stock_balance: Object.freeze(['item', 'location', 'quantity_milli']),
  stock_request: Object.freeze(['code', 'kind', 'warehouse', 'lines', 'note']),
})

const PAGE_LIMIT = 100

export const NOT_CONNECTED_MESSAGE = 'Chưa kết nối với cổng xT. Mở WareTrack từ cổng xT để xem dữ liệu nghiệp vụ.'

/**
 * Cầu nối do cổng xT (cửa sổ cha, cùng origin) cung cấp. Chỉ khi nhúng với `?integrated=1`.
 * Trả null nếu không nhúng hoặc không truy cập được.
 */
export function resolvePortalBridge(win = globalThis.window) {
  try {
    if (!win || win.parent === win) return null
    if (new URLSearchParams(win.location.search).get('integrated') !== '1') return null
    return win.parent.xtPortal ?? null
  } catch {
    return null
  }
}

const isObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v)

/** Gỡ kết quả MCP thành danh sách bản ghi; ném lỗi nếu tool báo lỗi hoặc dạng không hợp lệ. */
export function toRows(result) {
  let raw = result?.structuredContent
  if (raw === undefined && result?.content?.[0]?.type === 'text') {
    try { raw = JSON.parse(result.content[0].text || '{}') } catch { raw = { message: result.content[0].text } }
  }
  const value = isObject(raw) ? raw : {}
  if (result?.isError || value.error) {
    const err = isObject(value.error) ? value.error : {}
    throw new Error(String(err.message || value.message || err.code || 'Thao tác bị từ chối'))
  }
  const body = isObject(value.data) ? value.data : value
  const rows = Array.isArray(body.items) ? body.items : Array.isArray(body.rows) ? body.rows : null
  if (!rows) throw new Error('Phản hồi từ xT Core không có danh sách bản ghi')
  return rows
}

/** Giá trị trường của một bản ghi (trong `values` hoặc ở gốc). */
export function field(row, key) {
  const v = isObject(row?.values) && key in row.values ? row.values[key] : row?.[key]
  return v
}

/** Chuỗi hiển thị cho một giá trị trường (ref/đối tượng -> tên). */
export function display(value) {
  if (value === null || value === undefined) return '—'
  if (Array.isArray(value)) return value.length ? `${value.length} dòng` : '—'
  if (isObject(value)) return String(value.name ?? value.label ?? value.code ?? value.ref ?? value.id ?? '—')
  return String(value)
}

/** Số lượng lưu dạng phần nghìn -> chuỗi có tối đa 3 chữ số thập phân. */
export function formatMilli(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return (n / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 3 })
}

/**
 * @param {{ getBridge: () => ({ callTool?: Function } | null | undefined) }} options
 */
export function createBusinessAdapter({ getBridge }) {
  async function load() {
    const bridge = getBridge()
    if (!bridge || typeof bridge.callTool !== 'function') {
      return { state: 'not_connected', message: NOT_CONNECTED_MESSAGE }
    }
    try {
      const entries = await Promise.all(Object.entries(MODELS).map(async ([model_id, fields]) => {
        const result = await bridge.callTool('search_records', { model_id, limit: PAGE_LIMIT, fields: [...fields] })
        return [model_id, toRows(result)]
      }))
      return {
        state: 'ready',
        data: Object.fromEntries(entries),
        // Chưa có model ánh xạ vị trí kho -> toạ độ 3D; sơ đồ 3D chỉ là minh họa.
        locationMapping: false,
        loadedAt: new Date().toISOString(),
      }
    } catch (e) {
      return { state: 'error', message: e instanceof Error && e.message ? e.message : 'Không đọc được dữ liệu nghiệp vụ' }
    }
  }
  return Object.freeze({ load })
}
