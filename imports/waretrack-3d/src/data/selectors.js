// Derived (computed) views over the live db. Pure functions — call them on every render.
import { db } from './db.js'
import { fmtTime, fmtMin, fmtNum, fmtDec, statusMeta } from './format.js'

const ON_SITE = new Set(['at_gate', 'docking', 'loading', 'unloading'])
const ARRIVING = new Set(['en_route', 'at_gate', 'docking'])
const WORKING_FL = new Set(['loading', 'unloading', 'moving'])

/** Aggregate numbers for one site, or the whole network when siteId === 'ALL'. */
export function siteStats(siteId) {
  const sites = siteId === 'ALL' ? db.sites() : [db.site(siteId)].filter(Boolean)
  const ids = new Set(sites.map((s) => s.id))
  const trucks = db.list('truck').filter((t) => ids.has(t.siteId))
  const docks = db.list('dock').filter((d) => ids.has(d.siteId))
  const forklifts = db.list('forklift').filter((f) => ids.has(f.siteId))
  const pallets = db.list('pallet').filter((p) => ids.has(p.siteId))

  const stock = sites.reduce((a, s) => a + s.stock, 0)
  const capacity = sites.reduce((a, s) => a + s.capacity, 0)
  const docked = trucks.filter((t) => t.status === 'loading' || t.status === 'unloading').length
  return {
    stock,
    capacity,
    stockDelta: sites.reduce((a, s) => a + s.stockDelta, 0),
    fullPct: capacity ? (stock / capacity) * 100 : 0,
    docksTotal: docks.length,
    docksBusy: docks.filter((d) => d.status !== 'available').length,
    docked,
    arriving: trucks.filter((t) => ARRIVING.has(t.status)).length,
    inbound: trucks.filter((t) => ARRIVING.has(t.status)).length,
    staged: pallets.filter((p) => p.status === 'staged').length,
    trucksOnSite: trucks.filter((t) => ON_SITE.has(t.status)).length,
    trucksDelta: sites.reduce((a, s) => a + (s.trucksDelta ?? 0), 0),
    trucksTotal: trucks.length,
    forkliftsTotal: forklifts.length,
    forkliftsWorking: forklifts.filter((f) => WORKING_FL.has(f.status)).length,
    onTime: sites.reduce((a, s) => a + s.onTime, 0) / Math.max(1, sites.length),
    onTimeDelta: 0.4,
    outboundToday: sites.reduce((a, s) => a + s.outboundToday, 0),
    putawaysToday: sites.reduce((a, s) => a + s.putawaysToday, 0),
  }
}

/** The three KPI cards under the top bar. */
export function kpis(siteId) {
  const s = siteStats(siteId)
  const scope = siteId === 'ALL' ? 'tất cả kho' : siteId
  return [
    { key: 'stock', icon: 'cube', title: 'Tồn kho', value: s.stock, format: 'num', delta: `+${fmtNum(s.stockDelta)}`, sub: `pallet · ${scope}` },
    { key: 'trucks', icon: 'truck', title: 'Xe tại kho', value: s.trucksOnSite, format: 'num', delta: `+${fmtNum(s.trucksDelta)}`, sub: `${s.inbound} xe đang đến · ${scope}` },
    { key: 'ontime', icon: 'clock', title: 'Giao đúng hẹn', value: s.onTime, format: 'pct', delta: `+${fmtDec(s.onTimeDelta)}%`, sub: `30 ngày qua · ${scope}` },
  ]
}

/** "Đầy 78% · 1/2 cửa có xe" for the site selector. */
export function siteSelectorSub(siteId) {
  const s = siteStats(siteId)
  return `Đầy ${Math.round(s.fullPct)}% · ${s.docked}/${s.docksTotal} cửa có xe`
}

/** Dropdown row sub-line: "2/2 cửa có xe · 0 xe đang đến" */
export function siteMenuSub(siteId) {
  const s = siteStats(siteId)
  return `${s.docked}/${s.docksTotal} cửa có xe · ${s.inbound} xe đang đến`
}

/** Site panel chip line: "1 đang bốc dỡ · 1 sắp đến · 3 chờ xử lý" */
export function siteActivityLine(siteId) {
  const s = siteStats(siteId)
  return `${s.docked} đang bốc dỡ · ${s.arriving} sắp đến · ${s.staged} chờ`
}

export function dockName(dockId) {
  return db.get('dock', dockId)?.name ?? '—'
}

/** Where a truck is: "WH-04 · Nhập 1" when at a dock, otherwise "Đến WH-04". */
export function truckWhere(truck) {
  if (truck.status === 'loading' || truck.status === 'unloading') return `${truck.siteId} · ${dockName(truck.dockId)}`
  if (truck.status === 'departing') return `Từ ${truck.siteId}`
  return `Đến ${truck.siteId}`
}

/** Short tag shown in the floating 3D label: { id, text } */
export function entityTag(type, e) {
  switch (type) {
    case 'site': return { id: e.id, text: e.name }
    case 'truck': return { id: e.id, text: statusMeta('truck', e.status).chip }
    case 'forklift': return { id: e.id, text: statusMeta('forklift', e.status).long }
    case 'pallet': return { id: e.id, text: db.sku(e.sku)?.name ?? e.sku }
    case 'dock': return { id: e.name, text: statusMeta('dock', e.status).chip }
    case 'charger': return { id: e.name, text: statusMeta('charger', e.status).chip }
    default: return { id: e.id, text: '' }
  }
}

/** Display title for search results / panels. */
export function entityTitle(type, e) {
  if (type === 'pallet') return db.sku(e.sku)?.name ?? e.id
  if (type === 'dock' || type === 'charger') return e.name
  if (type === 'site') return e.name
  return e.id
}

/**
 * Shipment Tracking model for the bottom-left card.
 * steps[i] = { key, label, sub, state: 'done'|'current'|'todo', icon }
 * icon ∈ 'clipboard' | 'box' | 'package' | 'truck' | 'check'
 */
export function shipmentTracking(truckId) {
  const truck = db.get('truck', truckId)
  if (!truck) return null
  const shp = db.shipment(truck.shipmentId)
  if (!shp) return null
  const carrier = db.carrier(truck.carrier)
  const p = truck.progress
  const st = truck.status
  const steps = []
  const done = (key, label, time, icon) => steps.push({ key, label, sub: fmtTime(time), state: 'done', icon })

  done('confirmed', 'Xác nhận đơn', shp.times.confirmed, 'clipboard')
  done('picked', 'Đã lấy hàng', shp.times.picked, 'box')

  if (shp.direction === 'outbound') {
    if (st === 'loading') {
      steps.push({ key: 'loading', label: `Đang xếp ${p.done}/${p.total}`, sub: `Dự kiến ${fmtTime(shp.eta.loaded)}`, state: 'current', icon: 'package' })
      steps.push({ key: 'transit', label: 'Đang vận chuyển', sub: `Dự kiến ${fmtTime(shp.eta.transit)}`, state: 'todo', icon: 'truck' })
    } else {
      done('loaded', 'Đã xếp hàng', shp.times.loaded ?? shp.eta.loaded, 'package')
      steps.push({ key: 'transit', label: 'Đang vận chuyển', sub: fmtTime(shp.times.transit ?? shp.eta.transit), state: 'current', icon: 'truck' })
    }
    steps.push({ key: 'delivered', label: 'Đã giao', sub: `Dự kiến ${fmtTime(shp.eta.delivered)}`, state: 'todo', icon: 'check' })
  } else {
    done('loaded', 'Đã xếp hàng', shp.times.loaded, 'package')
    if (st === 'unloading' || st === 'loading') {
      done('transit', 'Đang vận chuyển', shp.times.transit, 'truck')
      steps.push({ key: 'unloading', label: `Đang dỡ ${p.done}/${p.total}`, sub: `Dự kiến ${fmtTime(shp.eta.done)}`, state: 'current', icon: 'check' })
    } else {
      steps.push({ key: 'transit', label: 'Đang vận chuyển', sub: fmtTime(shp.times.transit), state: 'current', icon: 'truck' })
      steps.push({ key: 'delivered', label: 'Đã giao', sub: `Dự kiến ${fmtTime(shp.eta.done)}`, state: 'todo', icon: 'check' })
    }
  }

  const meta = statusMeta('truck', st)
  let foot
  if (st === 'loading' || st === 'unloading') foot = `${truck.siteId} · ${dockName(truck.dockId)} · còn ${fmtMin(truck.etaMin)}`
  else if (st === 'departing') foot = `Đã rời ${truck.siteId} · ${dockName(truck.dockId)}`
  else foot = `Dự kiến đến ${fmtTime(shp.eta.arrive)} · ${fmtMin(truck.etaMin)}`

  return {
    truck, shipment: shp, carrier,
    header: `${truck.id} · ${carrier.name}`,
    steps,
    card: {
      id: `#${shp.id}`,
      to: `Đến: ${shp.toLabel}`,
      chip: meta.chip, tone: meta.tone,
      foot,
      carrierKey: truck.carrier,
    },
  }
}

/** Rows for the bottom-right fleet panel. Each row: { type, id, title, titleSub, dotColor, text, chip, tone, bar: {value,max}|null, right } */
export function fleetRows(siteId, tab) {
  const scope = (arr) => (siteId === 'ALL' ? arr : arr.filter((e) => e.siteId === siteId))
  if (tab === 'docks') {
    const rows = scope(db.list('dock')).map((d) => {
      const t = d.truckId ? db.get('truck', d.truckId) : null
      const m = statusMeta('dock', d.status)
      return {
        type: 'dock', id: d.id, title: d.name, titleSub: d.siteId,
        dotColor: t ? db.carrier(t.carrier).color : null,
        text: t ? `${t.id} · ${db.carrier(t.carrier).name}` : 'Chưa có xe',
        muted: !t,
        chip: d.status === 'booked' ? statusMeta('dock', 'available').chip : m.chip, tone: d.status === 'booked' ? 'gray' : m.tone,
        bar: t ? { value: t.progress.done, max: t.progress.total } : null,
        right: t ? `${t.progress.done}/${t.progress.total}` : '',
      }
    })
    // inbound trucks not yet at a dock are listed after the docks
    for (const t of scope(db.list('truck'))) {
      if (t.status === 'en_route' || t.status === 'at_gate') {
        const m = statusMeta('truck', t.status)
        rows.push({
          type: 'truck', id: t.id, title: 'Xe đến', titleSub: t.siteId,
          dotColor: db.carrier(t.carrier).color, text: `${t.id} · ${db.carrier(t.carrier).name}`,
          chip: m.chip, tone: m.tone, bar: null, right: fmtMin(t.etaMin),
        })
      }
    }
    return rows
  }
  if (tab === 'forklifts') {
    return scope(db.list('forklift')).map((f) => {
      const m = statusMeta('forklift', f.status)
      return {
        type: 'forklift', id: f.id, title: f.id, titleSub: f.siteId, dotColor: null,
        text: f.task, chip: m.chip, tone: m.tone,
        bar: { value: f.battery, max: 100 }, right: `${Math.round(f.battery)}%`,
      }
    })
  }
  // trucks
  const order = { loading: 0, unloading: 0, docking: 1, at_gate: 2, en_route: 3, departing: 4 }
  return scope(db.list('truck'))
    .slice()
    .sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9))
    .map((t) => {
      const m = statusMeta('truck', t.status)
      const atDock = t.status === 'loading' || t.status === 'unloading'
      return {
        type: 'truck', id: t.id, title: t.id, titleSub: db.carrier(t.carrier).name,
        dotColor: db.carrier(t.carrier).color, text: truckWhere(t),
        chip: m.chip, tone: m.tone,
        bar: atDock ? { value: t.progress.done, max: t.progress.total } : null,
        right: atDock ? `${t.progress.done}/${t.progress.total}` : t.status === 'docking' ? 'vào cửa' : fmtMin(t.etaMin),
      }
    })
}

/** Tab counters: cửa bận/tổng · xe nâng đang làm/tổng · số xe tải */
export function fleetCounts(siteId) {
  const s = siteStats(siteId)
  return {
    docks: `${s.docksBusy}/${s.docksTotal}`,
    forklifts: `${s.forkliftsWorking}/${s.forkliftsTotal}`,
    trucks: `${s.trucksTotal}`,
  }
}

const SEARCH_TYPE = { truck: 'Xe tải', forklift: 'Xe nâng', pallet: 'Pallet', dock: 'Cửa kho', charger: 'Trạm sạc' }

/** Simple search across all entities. Returns [{type,id,title,sub,siteId}] (max 8). */
export function search(query) {
  const q = query.trim().toLowerCase()
  if (!q) return []
  const out = []
  for (const e of db.all()) {
    const title = entityTitle(e.type, e)
    const hay = [e.id, title, e.name, e.sku, e.driver, e.operator, e.plate, e.address, e.siteId]
      .filter(Boolean).join(' ').toLowerCase()
    if (hay.includes(q)) {
      const sub = e.type === 'site' ? e.address : `${SEARCH_TYPE[e.type] ?? e.type} · ${e.siteId}`
      out.push({ type: e.type, id: e.id, title: e.type === 'pallet' ? `${e.id} · ${title}` : title, sub, siteId: e.siteId ?? e.id })
    }
    if (out.length >= 8) break
  }
  return out
}
