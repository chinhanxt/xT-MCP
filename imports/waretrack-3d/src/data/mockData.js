// WareTrack — mock ERP / WMS dataset.
// Seeded from the reference video (docs/ref). Everything here is fake business data.
// Entities are plain mutable objects: the simulation mutates them live, the UI reads them.
// Times are "sim minutes since midnight" (e.g. 9:40 → 580). Use format.js → fmtTime().

const t = (hhmm) => {
  const [h, m] = hhmm.split(':').map(Number)
  return h * 60 + m
}

export const SIM_START_MINUTES = t('09:40')

export const USER = {
  name: 'Nguyễn Minh Anh',
  role: 'Quản lý vận hành',
  email: 'minh.anh@waretrack.example',
}

// ───────────────────────────── Carriers ─────────────────────────────
export const CARRIERS = {
  waretrack: { key: 'waretrack', name: 'WareTrack', fullName: 'WareTrack Freight', color: '#2f5bea' },
  bluepeak:  { key: 'bluepeak',  name: 'Bluepeak',  fullName: 'Bluepeak Foods',   color: '#1f2a78' },
  nordline:  { key: 'nordline',  name: 'Nordline',  fullName: 'Nordline Logistics', color: '#16a08b' },
  cargoviva: { key: 'cargoviva', name: 'Cargoviva', fullName: 'Cargoviva',        color: '#f26b1d' },
}

// ───────────────────────────── SKUs (item master) ─────────────────────────────
// kind → how a pallet of this SKU looks in 3D: 'cardboard' | 'blue' | 'white'
// icon → UI illustration key (see SPEC §UI icons)
export const SKUS = {
  'BOX-M-001':  { sku: 'BOX-M-001',  name: 'Thùng carton (M)',   category: 'Bao bì', kind: 'cardboard', icon: 'box',       unitKg: 0.42 },
  'PLC-CNT-B':  { sku: 'PLC-CNT-B',  name: 'Thùng nhựa',         category: 'Lưu trữ',   kind: 'blue',      icon: 'container', unitKg: 1.1 },
  'PPE-HLM-Y':  { sku: 'PPE-HLM-Y',  name: 'Mũ bảo hộ',         category: 'Bảo hộ',       kind: 'cardboard', icon: 'helmet',    unitKg: 0.45 },
  'PKG-TAPE-48':{ sku: 'PKG-TAPE-48',name: 'Băng keo đóng gói',  category: 'Bao bì', kind: 'cardboard', icon: 'tape',      unitKg: 0.12 },
  'LED-6060':   { sku: 'LED-6060',   name: 'Đèn panel LED 60×60', category: 'Chiếu sáng',  kind: 'cardboard', icon: 'panel',     unitKg: 2.6 },
  'FUR-CHR-01': { sku: 'FUR-CHR-01', name: 'Ghế văn phòng',     category: 'Nội thất', kind: 'cardboard', icon: 'chair',     unitKg: 14 },
  'PPE-GLV-N':  { sku: 'PPE-GLV-N',  name: 'Găng tay nitrile',   category: 'Bảo hộ',       kind: 'blue',      icon: 'gloves',    unitKg: 0.9 },
  'FRZ-PEA-10': { sku: 'FRZ-PEA-10', name: 'Đậu Hà Lan đông lạnh 10 kg', category: 'Đông lạnh',    kind: 'white',     icon: 'frozen',    unitKg: 10 },
  'BEV-WTR-24': { sku: 'BEV-WTR-24', name: 'Nước suối thùng 24 chai', category: 'Đồ uống', kind: 'white',    icon: 'water',     unitKg: 12.4 },
}

// ───────────────────────────── Sites ─────────────────────────────
// world: site lot centre in world metres (Y up). variant: building style (see SPEC §3D buildings).
// badgeTone: colour of the "WH-0x" badge in the top bar.
export const SITES = [
  {
    id: 'WH-01', type: 'site', name: 'Kho Bắc Ninh', kind: 'KHO TỔNG', variant: 'depot',
    address: '12 Đường Từ Sơn, Bắc Ninh', status: 'Operational', badgeTone: 'blue',
    capacity: 1800, stock: 1412, stockDelta: 15,
    onTime: 96.6, onTimeDelta: 0.4, outboundToday: 23, putawaysToday: 15,
    trucksDelta: 1,
    inventory: [
      { sku: 'BOX-M-001', qty: 1906, status: 'in_stock' },
      { sku: 'PLC-CNT-B', qty: 297, status: 'in_stock' },
      { sku: 'PPE-HLM-Y', qty: 212, status: 'low_stock' },
      { sku: 'PKG-TAPE-48', qty: 4448, status: 'in_stock' },
    ],
    featuredTruckId: 'TRK-2051',
    world: { x: 0, z: 0 },
  },
  {
    id: 'WH-02', type: 'site', name: 'Trung tâm Sóng Thần', kind: 'TRUNG TÂM PHÂN PHỐI', variant: 'dc',
    address: '480 Quốc lộ 1K, Dĩ An, Bình Dương', status: 'Operational', badgeTone: 'blue',
    capacity: 4200, stock: 3310, stockDelta: 35,
    onTime: 98.2, onTimeDelta: 0.4, outboundToday: 21, putawaysToday: 35,
    trucksDelta: 2,
    inventory: [
      { sku: 'LED-6060', qty: 1787, status: 'in_stock' },
      { sku: 'FUR-CHR-01', qty: 174, status: 'in_stock' },
      { sku: 'BOX-M-001', qty: 993, status: 'low_stock' },
      { sku: 'PPE-GLV-N', qty: 5793, status: 'in_stock' },
    ],
    featuredTruckId: 'TRK-2104',
    world: { x: 300, z: -40 },
  },
  {
    id: 'WH-03', type: 'site', name: 'Kho Lạnh Hải Phòng', kind: 'KHO LẠNH', variant: 'cold',
    address: '7 Đường Hải Cảng, Hải Phòng', status: 'Operational', badgeTone: 'cyan',
    capacity: 1400, stock: 990, stockDelta: 20,
    onTime: 96.0, onTimeDelta: 0.4, outboundToday: 31, putawaysToday: 20,
    trucksDelta: 1,
    inventory: [
      { sku: 'FRZ-PEA-10', qty: 334, status: 'in_stock' },
      { sku: 'BEV-WTR-24', qty: 364, status: 'in_stock' },
      { sku: 'PPE-GLV-N', qty: 495, status: 'low_stock' },
      { sku: 'PLC-CNT-B', qty: 208, status: 'in_stock' },
    ],
    featuredTruckId: 'TRK-2148',
    world: { x: 600, z: 20 },
  },
  {
    id: 'WH-04', type: 'site', name: 'Kho Chuyển tiếp Biên Hòa', kind: 'CHUYỂN TIẾP', variant: 'crossdock',
    address: '2200 Đại lộ Đồng Khởi, Biên Hòa', status: 'Operational', badgeTone: 'blue',
    capacity: 1600, stock: 610, stockDelta: 20,
    onTime: 96.2, onTimeDelta: 0.4, outboundToday: 29, putawaysToday: 20,
    trucksDelta: 2,
    inventory: [
      { sku: 'BEV-WTR-24', qty: 288, status: 'in_stock' },
      { sku: 'PKG-TAPE-48', qty: 1922, status: 'in_stock' },
      { sku: 'LED-6060', qty: 73, status: 'low_stock' },
      { sku: 'PPE-HLM-Y', qty: 320, status: 'in_stock' },
    ],
    featuredTruckId: 'TRK-2205',
    world: { x: 120, z: 300 },
  },
  {
    id: 'WH-05', type: 'site', name: 'Trung tâm Tự động Củ Chi', kind: 'TRUNG TÂM TỰ ĐỘNG', variant: 'robotics',
    address: '300 Quốc lộ 22, Củ Chi, TP.HCM', status: 'Operational', badgeTone: 'blue',
    capacity: 2400, stock: 1730, stockDelta: 25,
    onTime: 95.9, onTimeDelta: 0.4, outboundToday: 33, putawaysToday: 25,
    trucksDelta: 3,
    inventory: [
      { sku: 'LED-6060', qty: 934, status: 'in_stock' },
      { sku: 'BOX-M-001', qty: 1817, status: 'in_stock' },
      { sku: 'PPE-GLV-N', qty: 865, status: 'low_stock' },
      { sku: 'PPE-HLM-Y', qty: 908, status: 'in_stock' },
    ],
    featuredTruckId: 'TRK-2339',
    world: { x: 460, z: 320 },
  },
]

// ───────────────────────────── Docks ─────────────────────────────
// status: 'available' | 'booked' | 'docking' | 'loading' | 'unloading'
// direction: 'bay' (generic) | 'in' | 'out'
const dock = (siteId, code, name, direction, extra = {}) => ({
  id: `${siteId}-${code}`, type: 'dock', siteId, name, direction,
  kind: 'Cửa dock có cầu nâng', status: 'available', truckId: null,
  bookedTruckId: null, bookedInMin: null, rearDoors: '—', trucksToday: 0,
  ...extra,
})

export const DOCKS = [
  dock('WH-01', 'B1', 'Cửa 1', 'bay', { status: 'loading', truckId: 'TRK-2051', rearDoors: 'Mở', trucksToday: 6 }),
  dock('WH-01', 'B2', 'Cửa 2', 'bay', { status: 'booked', bookedTruckId: 'TRK-2287', bookedInMin: 3, trucksToday: 4 }),

  dock('WH-02', 'B1', 'Cửa 1', 'bay', { status: 'unloading', truckId: 'TRK-2104', rearDoors: 'Mở', trucksToday: 7 }),
  dock('WH-02', 'B2', 'Cửa 2', 'bay', { status: 'booked', bookedTruckId: 'TRK-2658', bookedInMin: 1, trucksToday: 5 }),
  dock('WH-02', 'B3', 'Cửa 3', 'bay', { status: 'loading', truckId: 'TRK-2127', rearDoors: 'Mở', trucksToday: 6 }),
  dock('WH-02', 'B4', 'Cửa 4', 'bay', { status: 'booked', bookedTruckId: 'TRK-2526', bookedInMin: 2, trucksToday: 3 }),

  dock('WH-03', 'B1', 'Cửa 1', 'bay', { status: 'unloading', truckId: 'TRK-2471', rearDoors: 'Mở', trucksToday: 9, kind: 'Cửa dock cách nhiệt có gioăng' }),
  dock('WH-03', 'B2', 'Cửa 2', 'bay', { status: 'unloading', truckId: 'TRK-2148', rearDoors: 'Mở', trucksToday: 11, kind: 'Cửa dock cách nhiệt có gioăng' }),
  dock('WH-03', 'B3', 'Cửa 3', 'bay', { status: 'booked', bookedTruckId: 'TRK-2603', bookedInMin: 1, trucksToday: 8, kind: 'Cửa dock cách nhiệt có gioăng' }),

  dock('WH-04', 'IN1', 'Nhập 1', 'in', { status: 'unloading', truckId: 'TRK-2205', rearDoors: 'Mở', trucksToday: 7 }),
  dock('WH-04', 'IN2', 'Nhập 2', 'in', { status: 'booked', bookedTruckId: 'TRK-2481', bookedInMin: 3, trucksToday: 5 }),
  dock('WH-04', 'IN3', 'Nhập 3', 'in', { status: 'booked', bookedTruckId: 'TRK-2307', bookedInMin: 1, trucksToday: 4 }),
  dock('WH-04', 'OUT1', 'Xuất 1', 'out', { trucksToday: 6 }),
  dock('WH-04', 'OUT2', 'Xuất 2', 'out', { status: 'loading', truckId: 'TRK-2229', rearDoors: 'Mở', trucksToday: 5 }),
  dock('WH-04', 'OUT3', 'Xuất 3', 'out', { trucksToday: 2 }),

  dock('WH-05', 'B1', 'Cửa 1', 'bay', { status: 'loading', truckId: 'TRK-2339', rearDoors: 'Mở', trucksToday: 8 }),
  dock('WH-05', 'B2', 'Cửa 2', 'bay', { status: 'loading', truckId: 'TRK-2574', rearDoors: 'Mở', trucksToday: 6 }),
  dock('WH-05', 'B3', 'Cửa 3', 'bay', { status: 'booked', bookedTruckId: 'TRK-2372', bookedInMin: 1, trucksToday: 7 }),
  dock('WH-05', 'B4', 'Cửa 4', 'bay', { status: 'booked', bookedTruckId: 'TRK-2412', bookedInMin: 2, trucksToday: 5 }),
]

// ───────────────────────────── Trucks ─────────────────────────────
// status: 'en_route' | 'at_gate' | 'docking' | 'loading' | 'unloading' | 'departing'
// progress: pallets moved {done,total}; cargo: what's physically on board.
// position/heading are owned by the simulation (world metres, heading = rotation.y, model faces +Z).
const truck = (o) => ({
  type: 'truck', speedKmh: 0, etaMin: 0, distanceLeftM: 0,
  position: { x: 0, y: 0, z: 0 }, heading: 0, path: null, target: null,
  ...o,
})

export const TRUCKS = [
  // WH-01
  truck({ id: 'TRK-2051', siteId: 'WH-01', carrier: 'waretrack', driver: 'Lê Văn Hùng', plate: 'NJX-4471',
    status: 'loading', dockId: 'WH-01-B1', shipmentId: 'SHP-78442',
    progress: { done: 2, total: 6 }, etaMin: 13, cargo: { pallets: 2, capacity: 6, weightT: 0.8 } }),
  truck({ id: 'TRK-2287', siteId: 'WH-01', carrier: 'bluepeak', driver: 'Trần Thu Hà', plate: 'PAX-2093',
    status: 'en_route', dockId: 'WH-01-B2', shipmentId: 'SHP-78446',
    progress: { done: 0, total: 7 }, etaMin: 3, distanceLeftM: 640, speedKmh: 24, cargo: { pallets: 7, capacity: 8, weightT: 2.9 } }),

  // WH-02
  truck({ id: 'TRK-2127', siteId: 'WH-02', carrier: 'bluepeak', driver: 'Phạm Quốc Bảo', plate: 'BLU-7712',
    status: 'loading', dockId: 'WH-02-B3', shipmentId: 'SHP-78441',
    progress: { done: 4, total: 7 }, etaMin: 9, cargo: { pallets: 4, capacity: 8, weightT: 1.7 } }),
  truck({ id: 'TRK-2104', siteId: 'WH-02', carrier: 'nordline', driver: 'Nguyễn Ngọc Ánh', plate: 'NRD-3380',
    status: 'unloading', dockId: 'WH-02-B1', shipmentId: 'SHP-78444',
    progress: { done: 1, total: 5 }, etaMin: 13, cargo: { pallets: 4, capacity: 6, weightT: 1.9 } }),
  truck({ id: 'TRK-2658', siteId: 'WH-02', carrier: 'cargoviva', driver: 'Đỗ Minh Khôi', plate: 'CGV-5521',
    status: 'docking', dockId: 'WH-02-B2', shipmentId: 'SHP-78452',
    progress: { done: 0, total: 6 }, etaMin: 1, distanceLeftM: 31, speedKmh: 6, cargo: { pallets: 6, capacity: 8, weightT: 2.4 } }),
  truck({ id: 'TRK-2526', siteId: 'WH-02', carrier: 'waretrack', driver: 'Kim Thùy Linh', plate: 'WTF-1186',
    status: 'en_route', dockId: 'WH-02-B4', shipmentId: 'SHP-78453',
    progress: { done: 0, total: 5 }, etaMin: 2, distanceLeftM: 420, speedKmh: 22, cargo: { pallets: 5, capacity: 6, weightT: 2.0 } }),

  // WH-03
  truck({ id: 'TRK-2148', siteId: 'WH-03', carrier: 'bluepeak', driver: 'Vũ Đức Anh', plate: 'BLU-2148',
    status: 'unloading', dockId: 'WH-03-B2', shipmentId: 'SHP-78448', reefer: true,
    progress: { done: 3, total: 4 }, etaMin: 3, cargo: { pallets: 1, capacity: 4, weightT: 0.6 } }),
  truck({ id: 'TRK-2471', siteId: 'WH-03', carrier: 'bluepeak', driver: 'Bùi Tuấn Kiệt', plate: 'BLU-2471',
    status: 'unloading', dockId: 'WH-03-B1', shipmentId: 'SHP-78449', reefer: true,
    progress: { done: 0, total: 4 }, etaMin: 14, cargo: { pallets: 4, capacity: 4, weightT: 2.2 } }),
  truck({ id: 'TRK-2603', siteId: 'WH-03', carrier: 'bluepeak', driver: 'Hoàng Gia Huy', plate: 'HUL-7099',
    status: 'docking', dockId: 'WH-03-B3', shipmentId: 'SHP-78472', reefer: true,
    progress: { done: 0, total: 8 }, etaMin: 1, distanceLeftM: 24, speedKmh: 22, cargo: { pallets: 8, capacity: 8, weightT: 3.6 } }),

  // WH-04
  truck({ id: 'TRK-2205', siteId: 'WH-04', carrier: 'waretrack', driver: 'Trần Văn Sơn', plate: 'ZBY-5648',
    status: 'unloading', dockId: 'WH-04-IN1', shipmentId: 'SHP-78450',
    progress: { done: 2, total: 6 }, etaMin: 13, cargo: { pallets: 3, capacity: 7, weightT: 1.2 } }),
  truck({ id: 'TRK-2229', siteId: 'WH-04', carrier: 'nordline', driver: 'Lý Thị Lan', plate: 'NRD-2229',
    status: 'loading', dockId: 'WH-04-OUT2', shipmentId: 'SHP-78455',
    progress: { done: 3, total: 6 }, etaMin: 11, cargo: { pallets: 3, capacity: 6, weightT: 1.3 } }),
  truck({ id: 'TRK-2307', siteId: 'WH-04', carrier: 'nordline', driver: 'Đặng Ngọc Diệp', plate: 'EKT-2897',
    status: 'docking', dockId: 'WH-04-IN3', shipmentId: 'SHP-78456',
    progress: { done: 0, total: 8 }, etaMin: 1, distanceLeftM: 26, speedKmh: 6, cargo: { pallets: 8, capacity: 8, weightT: 3.2 } }),
  truck({ id: 'TRK-2481', siteId: 'WH-04', carrier: 'cargoviva', driver: 'Phan Anh Tuấn', plate: 'WKH-3157',
    status: 'en_route', dockId: 'WH-04-IN2', shipmentId: 'SHP-78466',
    progress: { done: 0, total: 10 }, etaMin: 1, distanceLeftM: 380, speedKmh: 23, cargo: { pallets: 10, capacity: 10, weightT: 4.1 } }),

  // WH-05
  truck({ id: 'TRK-2339', siteId: 'WH-05', carrier: 'cargoviva', driver: 'Võ Thị Nhung', plate: 'CGV-2339',
    status: 'loading', dockId: 'WH-05-B1', shipmentId: 'SHP-78458',
    progress: { done: 1, total: 5 }, etaMin: 13, cargo: { pallets: 1, capacity: 6, weightT: 0.5 } }),
  truck({ id: 'TRK-2574', siteId: 'WH-05', carrier: 'nordline', driver: 'Ngô Đình Phong', plate: 'NRD-2574',
    status: 'loading', dockId: 'WH-05-B2', shipmentId: 'SHP-78459',
    progress: { done: 0, total: 4 }, etaMin: 15, cargo: { pallets: 0, capacity: 6, weightT: 0 } }),
  truck({ id: 'TRK-2372', siteId: 'WH-05', carrier: 'waretrack', driver: 'Dương Minh Luân', plate: 'TTC-2870',
    status: 'docking', dockId: 'WH-05-B3', shipmentId: 'SHP-78460',
    progress: { done: 0, total: 6 }, etaMin: 1, distanceLeftM: 5, speedKmh: 11, cargo: { pallets: 6, capacity: 6, weightT: 2.5 } }),
  truck({ id: 'TRK-2412', siteId: 'WH-05', carrier: 'nordline', driver: 'Mai Thảo Vy', plate: 'NRD-2412',
    status: 'at_gate', dockId: 'WH-05-B4', shipmentId: 'SHP-78462',
    progress: { done: 0, total: 5 }, etaMin: 2, distanceLeftM: 60, speedKmh: 0, cargo: { pallets: 5, capacity: 6, weightT: 2.1 } }),
]

// ───────────────────────────── Forklifts ─────────────────────────────
// status: 'loading' | 'unloading' | 'charging' | 'idle' | 'moving'
const forklift = (o) => ({
  type: 'forklift', speedKmh: 0, carryingPalletId: null, carryingKind: null, forkHeight: 0.15,
  position: { x: 0, y: 0, z: 0 }, heading: 0,
  ...o,
})

export const FORKLIFTS = [
  forklift({ id: 'FL-01', siteId: 'WH-01', operator: 'Lê Quang Sáng', model: 'Toyota 8FBE18', status: 'loading', task: 'Đang xếp TRK-2051 tại Cửa 1', truckId: 'TRK-2051', battery: 75, movesToday: 28, chargerId: 'WH-01-C1' }),
  forklift({ id: 'FL-02', siteId: 'WH-01', operator: 'Trần Mỹ Duyên', model: 'Linde E20', status: 'idle', task: 'Chờ việc', truckId: null, battery: 64, movesToday: 19, chargerId: 'WH-01-C2' }),

  forklift({ id: 'FL-03', siteId: 'WH-02', operator: 'Phạm Gia Bình', model: 'Toyota 8FBE18', status: 'loading', task: 'Đang xếp TRK-2127 tại Cửa 3', truckId: 'TRK-2127', battery: 78, movesToday: 41, chargerId: 'WH-02-C1' }),
  forklift({ id: 'FL-04', siteId: 'WH-02', operator: 'Nguyễn Khánh Chi', model: 'Jungheinrich EFG 216', status: 'unloading', task: 'Đang dỡ TRK-2104 tại Cửa 1', truckId: 'TRK-2104', battery: 66, movesToday: 33, chargerId: 'WH-02-C2' }),
  forklift({ id: 'FL-05', siteId: 'WH-02', operator: 'Đỗ Thanh Bình', model: 'Toyota 8FBE18', status: 'charging', task: 'Đang sạc tại trạm', truckId: null, battery: 41, movesToday: 36, chargerId: 'WH-02-C3' }),
  forklift({ id: 'FL-06', siteId: 'WH-02', operator: 'Hồ Thu Sương', model: 'Linde E20', status: 'idle', task: 'Chờ việc', truckId: null, battery: 97, movesToday: 12, chargerId: 'WH-02-C4' }),

  forklift({ id: 'FL-07', siteId: 'WH-03', operator: 'Mạc Văn Kiên', model: 'Toyota 8FBE18', status: 'unloading', task: 'Đang dỡ TRK-2148 tại Cửa 2', truckId: 'TRK-2148', battery: 78, movesToday: 31, chargerId: 'WH-03-C1' }),
  forklift({ id: 'FL-08', siteId: 'WH-03', operator: 'Lưu Bảo Yến', model: 'Jungheinrich EFG 216', status: 'unloading', task: 'Đang dỡ TRK-2471 tại Cửa 1', truckId: 'TRK-2471', battery: 70, movesToday: 27, chargerId: 'WH-03-C2' }),
  forklift({ id: 'FL-09', siteId: 'WH-03', operator: 'Trịnh Công Phúc', model: 'Toyota 8FBE18', status: 'charging', task: 'Đang sạc tại trạm', truckId: null, battery: 58, movesToday: 22, chargerId: 'WH-03-C3' }),

  forklift({ id: 'FL-10', siteId: 'WH-04', operator: 'Tạ Hồng Nhung', model: 'Jungheinrich EFG 216', status: 'unloading', task: 'Đang dỡ TRK-2205 tại Nhập 1', truckId: 'TRK-2205', battery: 84, movesToday: 38, chargerId: 'WH-04-C1', carryingPalletId: 'PAL-1060' }),
  forklift({ id: 'FL-11', siteId: 'WH-04', operator: 'Đoàn Hải Nam', model: 'Linde E20', status: 'loading', task: 'Đang xếp TRK-2229 tại Xuất 2', truckId: 'TRK-2229', battery: 82, movesToday: 35, chargerId: 'WH-04-C2' }),
  forklift({ id: 'FL-12', siteId: 'WH-04', operator: 'Lâm Tuyết Mai', model: 'Toyota 8FBE18', status: 'charging', task: 'Đang sạc tại trạm', truckId: null, battery: 86, movesToday: 24, chargerId: 'WH-04-C3' }),
  forklift({ id: 'FL-13', siteId: 'WH-04', operator: 'Cao Đức Thịnh', model: 'Toyota 8FBE18', status: 'charging', task: 'Đang sạc tại trạm', truckId: null, battery: 91, movesToday: 21, chargerId: 'WH-04-C4' }),
  forklift({ id: 'FL-14', siteId: 'WH-04', operator: 'Châu Thị Phượng', model: 'Jungheinrich EFG 216', status: 'idle', task: 'Chờ việc', truckId: null, battery: 100, movesToday: 17, chargerId: 'WH-04-C5' }),

  forklift({ id: 'FL-15', siteId: 'WH-05', operator: 'Âu Thị Hoa', model: 'Toyota 8FBE18', status: 'loading', task: 'Đang xếp TRK-2339 tại Cửa 1', truckId: 'TRK-2339', battery: 94, movesToday: 34, chargerId: 'WH-05-C1' }),
  forklift({ id: 'FL-16', siteId: 'WH-05', operator: 'Văn Công Tâm', model: 'Linde E20', status: 'charging', task: 'Đang sạc tại trạm', truckId: null, battery: 52, movesToday: 29, chargerId: 'WH-05-C2' }),
  forklift({ id: 'FL-17', siteId: 'WH-05', operator: 'Sa Thị Hạnh', model: 'Toyota 8FBE18', status: 'idle', task: 'Chờ việc', truckId: null, battery: 88, movesToday: 15, chargerId: 'WH-05-C3' }),
]

// ───────────────────────────── Chargers ─────────────────────────────
// status: 'free' | 'charging'
const charger = (siteId, n, o = {}) => ({
  id: `${siteId}-C${n}`, type: 'charger', siteId, name: `C${n}`,
  spec: 'Bộ sạc xe nâng lithium-ion 48 V', status: 'free', forkliftId: null,
  sessionsToday: 4, energyKwh: 22.4, chargeRatePctMin: 8,
  ...o,
})

export const CHARGERS = [
  charger('WH-01', 1, { forkliftId: 'FL-01', sessionsToday: 5, energyKwh: 31.2 }),
  charger('WH-01', 2, { forkliftId: 'FL-02', sessionsToday: 3, energyKwh: 18.6 }),

  charger('WH-02', 1, { forkliftId: 'FL-03', sessionsToday: 6, energyKwh: 36.8 }),
  charger('WH-02', 2, { forkliftId: 'FL-04', sessionsToday: 5, energyKwh: 29.5 }),
  charger('WH-02', 3, { status: 'charging', forkliftId: 'FL-05', sessionsToday: 7, energyKwh: 41.3 }),
  charger('WH-02', 4, { forkliftId: 'FL-06', sessionsToday: 2, energyKwh: 12.1 }),

  charger('WH-03', 1, { forkliftId: 'FL-07', sessionsToday: 6, energyKwh: 33.0 }),
  charger('WH-03', 2, { forkliftId: 'FL-08', sessionsToday: 4, energyKwh: 24.7 }),
  charger('WH-03', 3, { status: 'charging', forkliftId: 'FL-09', sessionsToday: 8, energyKwh: 44.9 }),

  charger('WH-04', 1, { forkliftId: 'FL-10', sessionsToday: 7, energyKwh: 40.1 }),
  charger('WH-04', 2, { forkliftId: 'FL-11', sessionsToday: 5, energyKwh: 28.3 }),
  charger('WH-04', 3, { status: 'charging', forkliftId: 'FL-12', sessionsToday: 6, energyKwh: 35.6 }),
  charger('WH-04', 4, { status: 'charging', forkliftId: 'FL-13', sessionsToday: 4, energyKwh: 21.9 }),
  charger('WH-04', 5, { forkliftId: 'FL-14', sessionsToday: 3, energyKwh: 15.2 }),

  charger('WH-05', 1, { forkliftId: 'FL-15', sessionsToday: 6, energyKwh: 34.4 }),
  charger('WH-05', 2, { status: 'charging', forkliftId: 'FL-16', sessionsToday: 5, energyKwh: 27.8 }),
  charger('WH-05', 3, { forkliftId: 'FL-17', sessionsToday: 2, energyKwh: 11.5 }),
]

// ───────────────────────────── Pallets (LPN) ─────────────────────────────
// status: 'staged' | 'moving' | 'stored' | 'loaded'
// slot: yard/staging slot code (layout.js gives its position). pinned: show a blue map pin above it.
const pallet = (o) => ({
  type: 'pallet', status: 'staged', carriedBy: null, pinned: false,
  position: { x: 0, y: 0, z: 0 }, heading: 0,
  ...o,
})

export const PALLETS = [
  // WH-01
  pallet({ id: 'PAL-1026', siteId: 'WH-01', sku: 'PPE-HLM-Y', slot: 'S01', quantity: 82, grossKg: 56, lot: 'L2501-870', received: '19/04, 16:29' }),
  pallet({ id: 'PAL-1027', siteId: 'WH-01', sku: 'BOX-M-001', slot: 'S02', quantity: 120, grossKg: 64, lot: 'L2501-871', received: '19/04, 16:41', pinned: true }),
  pallet({ id: 'PAL-1028', siteId: 'WH-01', sku: 'PKG-TAPE-48', slot: 'S03', quantity: 360, grossKg: 52, lot: 'L2501-874', received: '20/04, 07:12', pinned: true }),
  pallet({ id: 'PAL-1029', siteId: 'WH-01', sku: 'BOX-M-001', slot: 'S04', quantity: 120, grossKg: 64, lot: 'L2501-871', received: '20/04, 07:30', pinned: true, status: 'stored' }),
  pallet({ id: 'PAL-1031', siteId: 'WH-01', sku: 'PLC-CNT-B', slot: 'S05', quantity: 48, grossKg: 61, lot: 'L2501-902', received: '20/04, 08:05', status: 'stored' }),
  pallet({ id: 'PAL-1032', siteId: 'WH-01', sku: 'BOX-M-001', slot: 'S06', quantity: 120, grossKg: 64, lot: 'L2501-871', received: '20/04, 08:16', status: 'stored' }),

  // WH-02
  pallet({ id: 'PAL-1040', siteId: 'WH-02', sku: 'LED-6060', slot: 'S01', quantity: 24, grossKg: 71, lot: 'L2502-114', received: '20/04, 06:58', pinned: true }),
  pallet({ id: 'PAL-1041', siteId: 'WH-02', sku: 'PPE-GLV-N', slot: 'S02', quantity: 60, grossKg: 58, lot: 'L2502-120', received: '20/04, 07:21', pinned: true }),
  pallet({ id: 'PAL-1042', siteId: 'WH-02', sku: 'FUR-CHR-01', slot: 'S03', quantity: 6, grossKg: 92, lot: 'L2502-131', received: '20/04, 07:44' }),
  pallet({ id: 'PAL-1043', siteId: 'WH-02', sku: 'PPE-GLV-N', slot: 'S04', quantity: 60, grossKg: 58, lot: 'L2502-120', received: '20/04, 08:02' }),
  pallet({ id: 'PAL-1044', siteId: 'WH-02', sku: 'BOX-M-001', slot: 'S05', quantity: 120, grossKg: 64, lot: 'L2502-140', received: '20/04, 08:30' }),

  // WH-03
  pallet({ id: 'PAL-1050', siteId: 'WH-03', sku: 'FRZ-PEA-10', slot: 'S01', quantity: 40, grossKg: 412, lot: 'L2503-018', received: '20/04, 06:40', pinned: true }),
  pallet({ id: 'PAL-1051', siteId: 'WH-03', sku: 'PLC-CNT-B', slot: 'S02', quantity: 48, grossKg: 61, lot: 'L2503-022', received: '20/04, 07:05', pinned: true }),
  pallet({ id: 'PAL-1052', siteId: 'WH-03', sku: 'BEV-WTR-24', slot: 'S03', quantity: 36, grossKg: 455, lot: 'L2503-030', received: '20/04, 07:52' }),

  // WH-04
  pallet({ id: 'PAL-1060', siteId: 'WH-04', sku: 'LED-6060', slot: 'S01', quantity: 24, grossKg: 71, lot: 'L2504-310', received: '20/04, 09:38', status: 'moving', carriedBy: 'FL-10' }),
  pallet({ id: 'PAL-1061', siteId: 'WH-04', sku: 'BEV-WTR-24', slot: 'S02', quantity: 36, grossKg: 455, lot: 'L2504-288', received: '20/04, 08:12' }),
  pallet({ id: 'PAL-1062', siteId: 'WH-04', sku: 'PKG-TAPE-48', slot: 'S03', quantity: 360, grossKg: 52, lot: 'L2504-290', received: '20/04, 08:20', pinned: true }),
  pallet({ id: 'PAL-1063', siteId: 'WH-04', sku: 'PPE-GLV-N', slot: 'S04', quantity: 60, grossKg: 58, lot: 'L2504-296', received: '20/04, 08:47', pinned: true }),
  pallet({ id: 'PAL-1064', siteId: 'WH-04', sku: 'BEV-WTR-24', slot: 'S05', quantity: 36, grossKg: 455, lot: 'L2504-301', received: '20/04, 09:02' }),
  pallet({ id: 'PAL-1065', siteId: 'WH-04', sku: 'PPE-HLM-Y', slot: 'S06', quantity: 82, grossKg: 56, lot: 'L2504-305', received: '20/04, 09:15' }),

  // WH-05
  pallet({ id: 'PAL-1090', siteId: 'WH-05', sku: 'BOX-M-001', slot: 'S01', quantity: 120, grossKg: 64, lot: 'L2505-401', received: '20/04, 07:10', pinned: true }),
  pallet({ id: 'PAL-1091', siteId: 'WH-05', sku: 'LED-6060', slot: 'S02', quantity: 24, grossKg: 71, lot: 'L2505-412', received: '20/04, 07:55' }),
  pallet({ id: 'PAL-1092', siteId: 'WH-05', sku: 'PPE-GLV-N', slot: 'S03', quantity: 60, grossKg: 58, lot: 'L2505-420', received: '20/04, 08:25', pinned: true }),
]

// ───────────────────────────── Shipments (orders) ─────────────────────────────
// direction 'outbound': loaded here, leaves to `toLabel`.  'inbound': arrives here and is unloaded.
// times: completed milestone clock times (sim minutes). eta*: forecast clock times (sim minutes).
export const SHIPMENTS = [
  { id: 'SHP-78442', truckId: 'TRK-2051', siteId: 'WH-01', dockId: 'WH-01-B1', direction: 'outbound', customer: 'Công ty Thương mại Phú Gia', toLabel: 'Hải Dương',
    times: { confirmed: t('06:49'), picked: t('08:19') }, eta: { loaded: t('09:53'), transit: t('10:01'), delivered: t('10:33') } },
  { id: 'SHP-78446', truckId: 'TRK-2287', siteId: 'WH-01', dockId: 'WH-01-B2', direction: 'inbound', customer: 'Bluepeak Foods', toLabel: 'WH-01 Kho Bắc Ninh',
    times: { confirmed: t('06:31'), picked: t('07:58'), loaded: t('08:47'), transit: t('09:05') }, eta: { arrive: t('09:43'), done: t('10:05') } },

  { id: 'SHP-78441', truckId: 'TRK-2127', siteId: 'WH-02', dockId: 'WH-02-B3', direction: 'outbound', customer: 'Chuỗi bán lẻ Hải Long', toLabel: 'Nam Định',
    times: { confirmed: t('06:22'), picked: t('08:02') }, eta: { loaded: t('09:49'), transit: t('09:55'), delivered: t('12:10') } },
  { id: 'SHP-78444', truckId: 'TRK-2104', siteId: 'WH-02', dockId: 'WH-02-B1', direction: 'inbound', customer: 'Điện máy Sáng Việt', toLabel: 'WH-02 Trung tâm Sóng Thần',
    times: { confirmed: t('06:43'), picked: t('08:20'), loaded: t('09:15'), transit: t('09:41') }, eta: { arrive: t('09:44'), done: t('10:00') } },
  { id: 'SHP-78452', truckId: 'TRK-2658', siteId: 'WH-02', dockId: 'WH-02-B2', direction: 'inbound', customer: 'Cargoviva', toLabel: 'WH-02 Trung tâm Sóng Thần',
    times: { confirmed: t('06:55'), picked: t('08:11'), loaded: t('09:02'), transit: t('09:20') }, eta: { arrive: t('09:41'), done: t('10:08') } },
  { id: 'SHP-78453', truckId: 'TRK-2526', siteId: 'WH-02', dockId: 'WH-02-B4', direction: 'inbound', customer: 'Dược phẩm Đông Á', toLabel: 'WH-02 Trung tâm Sóng Thần',
    times: { confirmed: t('07:05'), picked: t('08:26'), loaded: t('09:10'), transit: t('09:24') }, eta: { arrive: t('09:42'), done: t('10:12') } },

  { id: 'SHP-78448', truckId: 'TRK-2148', siteId: 'WH-03', dockId: 'WH-03-B2', direction: 'inbound', customer: 'Siêu thị Tươi Xanh', toLabel: 'WH-03 Kho Lạnh Hải Phòng',
    times: { confirmed: t('06:40'), picked: t('08:19'), loaded: t('09:16'), transit: t('09:42') }, eta: { arrive: t('09:44'), done: t('09:52') } },
  { id: 'SHP-78449', truckId: 'TRK-2471', siteId: 'WH-03', dockId: 'WH-03-B1', direction: 'inbound', customer: 'Thực phẩm Bắc Cực', toLabel: 'WH-03 Kho Lạnh Hải Phòng',
    times: { confirmed: t('06:52'), picked: t('08:30'), loaded: t('09:18'), transit: t('09:31') }, eta: { arrive: t('09:39'), done: t('10:04') } },
  { id: 'SHP-78472', truckId: 'TRK-2603', siteId: 'WH-03', dockId: 'WH-03-B3', direction: 'inbound', customer: 'Công ty Cảng Việt', toLabel: 'WH-03 Kho Lạnh Hải Phòng',
    times: { confirmed: t('07:07'), picked: t('08:34'), loaded: t('09:18'), transit: t('09:32') }, eta: { arrive: t('09:52'), done: t('10:32') } },

  { id: 'SHP-78450', truckId: 'TRK-2205', siteId: 'WH-04', dockId: 'WH-04-IN1', direction: 'inbound', customer: 'Chợ đầu mối Sơn Hà', toLabel: 'WH-04 Kho Chuyển tiếp Biên Hòa',
    times: { confirmed: t('06:57'), picked: t('08:25'), loaded: t('09:10'), transit: t('09:26') }, eta: { arrive: t('09:35'), done: t('09:56') } },
  { id: 'SHP-78455', truckId: 'TRK-2229', siteId: 'WH-04', dockId: 'WH-04-OUT2', direction: 'outbound', customer: 'Thời trang Lạc Hồng', toLabel: 'Thái Bình',
    times: { confirmed: t('06:48'), picked: t('08:40') }, eta: { loaded: t('09:54'), transit: t('10:00'), delivered: t('11:25') } },
  { id: 'SHP-78456', truckId: 'TRK-2307', siteId: 'WH-04', dockId: 'WH-04-IN3', direction: 'inbound', customer: 'Tạp hóa Nắng Mai', toLabel: 'WH-04 Kho Chuyển tiếp Biên Hòa',
    times: { confirmed: t('06:37'), picked: t('08:16'), loaded: t('09:12'), transit: t('09:26') }, eta: { arrive: t('09:45'), done: t('10:25') } },
  { id: 'SHP-78466', truckId: 'TRK-2481', siteId: 'WH-04', dockId: 'WH-04-IN2', direction: 'inbound', customer: 'Bán lẻ Bình Minh', toLabel: 'WH-04 Kho Chuyển tiếp Biên Hòa',
    times: { confirmed: t('07:03'), picked: t('08:29'), loaded: t('09:12'), transit: t('09:26') }, eta: { arrive: t('09:45'), done: t('10:25') } },

  { id: 'SHP-78458', truckId: 'TRK-2339', siteId: 'WH-05', dockId: 'WH-05-B1', direction: 'outbound', customer: 'Robot Sao Bắc', toLabel: 'Hưng Yên',
    times: { confirmed: t('06:53'), picked: t('08:28') }, eta: { loaded: t('10:05'), transit: t('10:13'), delivered: t('10:45') } },
  { id: 'SHP-78459', truckId: 'TRK-2574', siteId: 'WH-05', dockId: 'WH-05-B2', direction: 'outbound', customer: 'Phòng khám Duyên Hải', toLabel: 'Vĩnh Phúc',
    times: { confirmed: t('07:12'), picked: t('08:51') }, eta: { loaded: t('10:07'), transit: t('10:12'), delivered: t('11:58') } },
  { id: 'SHP-78460', truckId: 'TRK-2372', siteId: 'WH-05', dockId: 'WH-05-B3', direction: 'inbound', customer: 'Tạp hóa Nắng Mai', toLabel: 'WH-05 Trung tâm Tự động Củ Chi',
    times: { confirmed: t('07:10'), picked: t('08:32'), loaded: t('09:21'), transit: t('09:35') }, eta: { arrive: t('09:53'), done: t('10:33') } },
  { id: 'SHP-78462', truckId: 'TRK-2412', siteId: 'WH-05', dockId: 'WH-05-B4', direction: 'inbound', customer: 'Văn phòng phẩm Thủ Đô', toLabel: 'WH-05 Trung tâm Tự động Củ Chi',
    times: { confirmed: t('07:18'), picked: t('08:44'), loaded: t('09:24'), transit: t('09:38') }, eta: { arrive: t('09:54'), done: t('10:30') } },
]

// ───────────────────────────── Notifications ─────────────────────────────
export const NOTIFICATIONS = [
  { id: 'N1', tone: 'orange', title: 'Sắp hết hàng · Mũ bảo hộ', body: 'WH-01 Kho Bắc Ninh còn 212 đơn vị (tối thiểu 300).', at: t('09:36'), unread: true },
  { id: 'N2', tone: 'blue', title: 'TRK-2481 sắp đến', body: 'Xe Cargoviva đã đặt chỗ tại WH-04 · Nhập 2.', at: t('09:38'), unread: true },
  { id: 'N3', tone: 'orange', title: 'Sắp hết hàng · Đèn panel LED 60×60', body: 'WH-04 Kho Chuyển tiếp Biên Hòa còn 73 đơn vị.', at: t('09:31'), unread: true },
  { id: 'N4', tone: 'green', title: 'SHP-78439 đã giao', body: 'Đã giao tới Bắc Giang — sớm 4 phút.', at: t('09:22'), unread: false },
  { id: 'N5', tone: 'gray', title: 'FL-05 pin yếu', body: 'Đã chuyển tới trạm sạc C3 tại WH-02 Trung tâm Sóng Thần.', at: t('09:12'), unread: false },
]
