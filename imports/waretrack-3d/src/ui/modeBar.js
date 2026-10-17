// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 NguyenThong251 and xT contributors
//
// Thanh chuyển chế độ (Mô phỏng | Dữ liệu nghiệp vụ) và khung xem dữ liệu nghiệp vụ (danh sách,
// chỉ đọc). Mọi chuỗi từ xT Core được đưa vào DOM bằng textContent.
import './modeBar.css'
import { store } from '../state/store.js'
import { MODES, MODE_LABEL, diagramLabel } from '../state/mode.js'
import { MODELS, field, display, formatMilli } from '../portal/businessAdapter.js'

function h(tag, className, text) {
  const el = document.createElement(tag)
  if (className) el.className = className
  if (text !== undefined) el.textContent = text
  return el
}

const COLUMNS = {
  warehouse: { title: 'Kho', head: ['Mã', 'Tên'], cells: (r) => [display(field(r, 'code')), display(field(r, 'name'))] },
  stock_balance: {
    title: 'Tồn kho', head: ['Hàng hóa', 'Vị trí', 'Số lượng'],
    cells: (r) => [display(field(r, 'item')), display(field(r, 'location')), formatMilli(field(r, 'quantity_milli'))],
  },
  stock_request: {
    title: 'Phiếu nhập/xuất', head: ['Mã', 'Loại', 'Kho', 'Dòng hàng'],
    cells: (r) => [display(field(r, 'code')), field(r, 'kind') === 'receive' ? 'Nhập' : field(r, 'kind') === 'issue' ? 'Xuất' : display(field(r, 'kind')), display(field(r, 'warehouse')), display(field(r, 'lines'))],
  },
}

function table(model, rows) {
  const spec = COLUMNS[model]
  const wrap = h('section', 'bz-table')
  wrap.append(h('h3', '', `${spec.title} (${rows.length})`))
  if (!rows.length) { wrap.append(h('p', 'bz-muted', 'Không có bản ghi.')); return wrap }
  const t = h('table')
  const head = h('tr')
  for (const c of spec.head) head.append(h('th', '', c))
  t.append(head)
  for (const row of rows) {
    const tr = h('tr')
    for (const c of spec.cells(row)) tr.append(h('td', '', c))
    t.append(tr)
  }
  wrap.append(t)
  return wrap
}

function renderBusiness(root, business, controller) {
  root.replaceChildren()
  const card = h('div', 'bz-card')
  root.append(card)
  const retry = () => {
    const b = h('button', 'bz-btn', business.state === 'error' ? 'Thử lại' : 'Làm mới')
    b.type = 'button'
    b.addEventListener('click', () => { void controller.refresh() })
    return b
  }
  switch (business.state) {
    case 'loading':
      card.append(h('p', 'bz-status', 'Đang tải dữ liệu nghiệp vụ…'))
      break
    case 'not_connected':
      card.append(h('h2', '', 'Chưa kết nối'), h('p', 'bz-status', business.message))
      break
    case 'error':
      card.append(h('h2', '', 'Không đọc được dữ liệu nghiệp vụ'), h('p', 'bz-status bz-error', business.message), retry())
      break
    case 'ready': {
      card.append(h('h2', '', 'Dữ liệu nghiệp vụ (chỉ đọc)'))
      card.append(h('p', 'bz-muted', `Cập nhật lúc ${new Date(business.loadedAt).toLocaleTimeString('vi-VN')}. ${diagramLabel(business.locationMapping)}; nên chỉ hiển thị dạng danh sách.`), retry())
      for (const model of Object.keys(MODELS)) card.append(table(model, business.data[model]))
      break
    }
    default:
      card.append(h('p', 'bz-status', 'Chưa có dữ liệu.'))
  }
}

export function mountModeBar(modeRoot, businessRoot, controller) {
  modeRoot.setAttribute('role', 'group')
  modeRoot.setAttribute('aria-label', 'Chế độ dữ liệu')
  const buttons = {}
  for (const mode of Object.values(MODES)) {
    const b = h('button', 'bz-mode', MODE_LABEL[mode])
    b.type = 'button'
    b.addEventListener('click', () => { void controller.setMode(mode) })
    buttons[mode] = b
    modeRoot.append(b)
  }
  const apply = () => {
    const { mode, business } = store.state
    document.documentElement.dataset.wtMode = mode
    for (const [m, b] of Object.entries(buttons)) b.setAttribute('aria-pressed', String(m === mode))
    businessRoot.hidden = mode !== MODES.BUSINESS
    if (mode === MODES.BUSINESS) renderBusiness(businessRoot, business, controller)
    else businessRoot.replaceChildren()
  }
  apply()
  return store.subscribe((state, prev, changed) => {
    if (changed.includes('mode') || changed.includes('business')) apply()
  })
}
