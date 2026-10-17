// Formatting + status vocabulary shared by every UI module.

export const pad2 = (n) => String(n).padStart(2, '0')

/** sim minutes since midnight → "09:40" */
export function fmtTime(min) {
  const m = Math.floor(((min % 1440) + 1440) % 1440)
  return `${pad2(Math.floor(m / 60))}:${pad2(m % 60)}`
}

/** 1412 → "1.412" (vi-VN) */
export const fmtNum = (n) => Math.round(n).toLocaleString('vi-VN')

/** 4.26 → "4,3" (vi-VN, fixed decimals) */
export const fmtDec = (n, digits = 1) => n.toLocaleString('vi-VN', { minimumFractionDigits: digits, maximumFractionDigits: digits })

/** 96.64 → "96,6%" */
export const fmtPct = (n, digits = 1) => `${fmtDec(n, digits)}%`

export const fmtKmh = (v) => `${v < 10 ? fmtDec(v).replace(/,0$/, '') : Math.round(v)} km/h`

/** whole minutes → "13 phút" (min 1) */
export const fmtMin = (m) => `${Math.max(1, Math.round(m))} phút`

/**
 * Status chip vocabulary. tone ∈ green | blue | orange | gray.
 * chip    = short label used in lists/fleet panel
 * long    = label used in the right-hand detail panel chip (when different)
 */
export const STATUS = {
  truck: {
    en_route:  { chip: 'Đang trên đường', tone: 'blue' },
    at_gate:   { chip: 'Chờ ở cổng',      tone: 'orange' },
    docking:   { chip: 'Đang vào cửa',    tone: 'blue' },
    loading:   { chip: 'Đang xếp hàng',   tone: 'green' },
    unloading: { chip: 'Đang dỡ hàng',    tone: 'green' },
    departing: { chip: 'Đang rời kho',    tone: 'gray' },
  },
  forklift: {
    loading:   { chip: 'Đang xếp',  long: 'Đang xếp hàng lên xe', tone: 'green' },
    unloading: { chip: 'Đang dỡ',   long: 'Đang dỡ hàng xuống',   tone: 'green' },
    charging:  { chip: 'Đang sạc',  long: 'Đang sạc',             tone: 'orange' },
    idle:      { chip: 'Rảnh',      long: 'Đang rảnh',            tone: 'gray' },
    moving:    { chip: 'Di chuyển', long: 'Đang di chuyển',       tone: 'blue' },
  },
  pallet: {
    staged: { chip: 'Chờ xử lý',   tone: 'green' },
    moving: { chip: 'Đang chuyển', tone: 'blue' },
    stored: { chip: 'Đã lưu kho',  tone: 'gray' },
    loaded: { chip: 'Đã lên xe',   tone: 'green' },
  },
  dock: {
    available: { chip: 'Trống',         tone: 'gray' },
    booked:    { chip: 'Đã đặt chỗ',    tone: 'orange' },
    docking:   { chip: 'Đang vào cửa',  tone: 'blue' },
    loading:   { chip: 'Đang xếp hàng', tone: 'green' },
    unloading: { chip: 'Đang dỡ hàng',  tone: 'green' },
  },
  charger: {
    free:     { chip: 'Trống',    tone: 'gray' },
    charging: { chip: 'Đang sạc', tone: 'orange' },
  },
  inventory: {
    in_stock:  { chip: 'Còn hàng',  tone: 'green' },
    low_stock: { chip: 'Sắp hết',   tone: 'orange' },
  },
  site: {
    Operational: { chip: 'Đang hoạt động', tone: 'green' },
  },
}

export function statusMeta(type, status) {
  return STATUS[type]?.[status] ?? { chip: String(status ?? '—'), tone: 'gray' }
}

/** Type label shown in the detail-panel eyebrow ("XE NÂNG · WH-01"). */
export const TYPE_LABEL = {
  site: 'KHO', truck: 'XE TẢI', forklift: 'XE NÂNG', pallet: 'PALLET', dock: 'CỬA KHO', charger: 'TRẠM SẠC',
}
