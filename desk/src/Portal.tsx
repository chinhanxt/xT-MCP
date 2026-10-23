// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { ToolItem, type ToolEntry } from './ToolItem';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { DocumentsPanel } from './DocumentsPanel';
import { RequestForm } from './RequestForm';
import { ReportPanel } from './ReportPanel';
import type { Area } from './portal-nav';
import { DepartmentsPanel } from './DepartmentsPanel';
import { MembersPanel } from './MembersPanel';
import { AgentHrPanel, JdRiskBlock } from './AgentHrPanel';
import { AGENT_MODEL } from './agenthr';
import { parseMilli, readToolResult } from './portal-result';
import { classifyError, type ErrorKind } from './portal-state';
import { PortalAlert } from './PortalAlert';
import { fieldLabel, hiddenFields, kindFieldLabel, primitiveLabel, valueLabel } from './portal-labels';
import { config } from './config';
import { confirmInfo } from './confirm';
import { modelOf, useEnumLabels, type EnumLabel } from './enum-labels';
import { Pill, toneOf } from './ui';
import { TabList, TabPanel } from './Tabs';
import { Building2, Check, ExternalLink, MessageSquare, History, ListChecks, Plus, RefreshCw, Search, Send, UserRound, Users, X } from 'lucide-react';

type Data = Record<string, unknown>;
const warehouseModels = ['warehouse', 'warehouse_location', 'stock_item', 'stock_request', 'stock_balance', 'stock_movement'];
const labels: Record<string, string> = { warehouse:'Kho', warehouse_location:'Vị trí', stock_item:'Hàng hóa', stock_request:'Phiếu nhập/xuất', stock_balance:'Tồn kho', stock_movement:'Biến động' };
const listFields: Record<string,string[]> = {warehouse:['code','name'],warehouse_location:['code','name','warehouse'],stock_item:['sku','name','unit','reorder_level_milli'],stock_request:['code','kind','warehouse','lines','note'],stock_balance:['item','location','quantity_milli'],stock_movement:['item','location','quantity_milli','posted_at','approved_by']};
const writeTools = ['create_record', 'submit_request', 'decide_request'];
/** Trường trạng thái hiển thị bằng chip màu (giá trị gốc quyết định màu, nhãn vẫn từ máy chủ/bảng nhãn). */
const STATUS_KEYS = new Set(['state', 'status', 'sla_status']);
const KHO_TABS = [{ id: 'stock', label: 'Tồn kho' }, { id: 'requests', label: 'Phiếu' }, { id: 'catalog', label: 'Danh mục' }, { id: 'map', label: 'Sơ đồ 3D' }] as const;
type KhoTab = (typeof KHO_TABS)[number]['id'];
const H_TABS = [{ id: 'people', label: 'Nhân viên' }, { id: 'units', label: 'Phòng ban' }, { id: 'docs', label: 'Tài liệu' }, { id: 'agents', label: 'Nhân sự AI' }, { id: 'members', label: 'Cấp quyền' }] as const;
type HTab = (typeof H_TABS)[number]['id'];
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
function rows(value: Data | null): Data[] {
  for (const key of ['items', 'people', 'rows', 'events']) if (Array.isArray(value?.[key])) return value[key] as Data[];
  return [];
}
function uuid(row: Data): string { return String(row.id || row.ref || '').split(':').at(-1) || ''; }
function label(row: Data): string { const values = obj(row.values); return String(row.name || values.name || row.title || row.label || row.code || row.sku || values.code || values.sku || row.ref || row.id || 'Chi tiết'); }
function display(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.length ? value.map(display).join(', ') : '—';
  if (typeof value === 'object') { const data = obj(value); return String(data.name || data.label || Object.entries(data).map(([k,v]) => `${k}: ${display(v)}`).join(' · ')); }
  return String(value);
}
function RecordCard({ row, select, enumLabel }: { row: Data; select?: (ref: string) => void; enumLabel?: EnumLabel }) {
  const model = modelOf(row);
  const ref = typeof row.ref === 'string' ? row.ref : '';
  const details = Object.fromEntries(Object.entries({...Object.fromEntries(Object.entries(row).filter(([key]) => !['values','ref','id','name','title','label'].includes(key))), ...obj(row.values)}).filter(([key]) => !hiddenFields.has(key)));
  return <article className="portal-entry"><strong>{label(row)}</strong><dl>{Object.entries(details).map(([key,value]) => <div key={key}><dt>{key==='kind' ? kindFieldLabel(value) : fieldLabel(key)}</dt><dd>{key.endsWith('_milli') && typeof value==='number' ? (value/1000).toFixed(3) : key==='lines' && Array.isArray(value) ? (value.length ? `${value.length} dòng` : '—') : typeof value==='string' && value==='' ? '—' : typeof value==='string' ? (STATUS_KEYS.has(key) ? <Pill tone={toneOf(value)}>{enumLabel ? enumLabel(model,key,value) : valueLabel(key,value)}</Pill> : enumLabel ? enumLabel(model,key,value) : valueLabel(key,value)) : display(value)}</dd></div>)}</dl>
    {ref && select && <button onClick={() => select(ref)}>Mở {label(row)}</button>}
  </article>;
}
/** Tồn kho theo vị trí (search_records stock_balance); tên hàng/vị trí và mức đặt lại tra từ danh mục đã tải. */
function StockTable({ balances, items, locations, loading, onRefresh }: { balances?: Data[]; items: Data[]; locations: Data[]; loading: boolean; onRefresh: () => void }) {
  const byId = (list: Data[]) => new Map(list.map(row => [uuid(row), row]));
  const itemMap = byId(items), locationMap = byId(locations);
  const refRow = (value: unknown, map: Map<string, Data>) => { const key = typeof value === 'string' ? value.split(':').at(-1)! : uuid(obj(value)); return map.get(key); };
  const qty = (milli: unknown) => typeof milli === 'number' ? (milli / 1000).toLocaleString('vi-VN', { maximumFractionDigits: 3 }) : '—';
  return <section className="panel stock-table" aria-label="Tồn kho"><div className="ph"><h3>Tồn kho</h3><div className="act"><button className="ghost sm" disabled={loading} onClick={onRefresh}>Làm mới tồn kho<RefreshCw aria-hidden="true"/></button></div></div>
    {!balances ? <p role="status">Đang tải tồn kho…</p> : !balances.length ? <p role="status" className="empty-note">Chưa có tồn kho nào bạn được xem.</p> :
      <table><thead><tr><th>Mặt hàng</th><th>Mã</th><th>Vị trí</th><th className="num">Tồn</th><th className="num">Mức đặt lại</th><th><span className="sr-only">Cảnh báo</span></th></tr></thead><tbody>{balances.map((row, i) => {
        const values = { ...row, ...obj(row.values) }, itemRow = refRow(values.item, itemMap), locRow = refRow(values.location, locationMap);
        const itemValues = itemRow ? obj(itemRow.values) : {}, unitText = itemValues.unit ? ` ${String(itemValues.unit)}` : '';
        const reorder = typeof itemValues.reorder_level_milli === 'number' ? itemValues.reorder_level_milli : null, quantity = typeof values.quantity_milli === 'number' ? values.quantity_milli : null;
        const low = reorder !== null && reorder > 0 && quantity !== null && quantity <= reorder;
        return <tr key={String(row.ref || i)}><td><strong>{itemRow ? label(itemRow) : display(values.item)}</strong></td><td className="faint">{display(itemValues.sku)}</td><td>{locRow ? label(locRow) : display(values.location)}</td><td className="num">{qty(quantity)}{quantity !== null && unitText}</td><td className="num">{reorder !== null ? `${qty(reorder)}${unitText}` : '—'}</td><td>{low && <Pill tone="held">Sắp hết</Pill>}</td></tr>;
      })}</tbody></table>}
  </section>;
}
/** Yêu cầu mở từ khung chung (ô tìm người, lối tắt): chạy đúng lời gọi sẵn có của Con người. */
export type PortalIntent = { id: number; area: Area; kind: 'people'; query: string } | { id: number; area: Area; kind: 'tasks' };
type Props = {
  intent?: PortalIntent;
  area: string; mcp: McpConnection | null; tools: McpTool[]; sandboxUrl: URL;
  onOpenLink: (url: string, frame: Element | null) => boolean;
  revealDpop: ComponentProps<typeof ToolItem>['dpop'];
  onNavigate?: (area: Area) => void;
  /** Gọi sau mỗi thao tác ghi thành công (làm mới số đếm hộp duyệt). */
  onMutated?: () => void;
};
export function Portal(props: Props) {
  const session = useRef({mcp:props.mcp, epoch:0});
  if (session.current.mcp !== props.mcp) session.current = {mcp:props.mcp, epoch:session.current.epoch+1};
  return <PortalView key={`${props.area}:${session.current.epoch}`} {...props}/>;
}
function PortalView({ area, mcp, tools, sandboxUrl, onOpenLink, revealDpop, onNavigate, intent, onMutated }: Props) {
  const [data, setData] = useState<Data | null>(null), [error, setErrorText] = useState(''), [errorKind, setErrorKind] = useState<ErrorKind>('error'), [rejecting, setRejecting] = useState(false), [reason, setReason] = useState(''), [loading, setLoading] = useState(false);
  const [query, setQuery] = useState(''), [model, setModel] = useState(''), [catalogModel, setCatalogModel] = useState('stock_item');
  const [catalog, setCatalog] = useState<Data[]>([]), [workspace, setWorkspace] = useState<Data | null>(null);
  const [code, setCode] = useState(''), [name, setName] = useState(''), [unit, setUnit] = useState('cái');
  const [requestCode, setRequestCode] = useState(''), [reorder, setReorder] = useState('0'), [quantity, setQuantity] = useState('1');
  const [warehouse, setWarehouse] = useState(''), [location, setLocation] = useState(''), [item, setItem] = useState('');
  const [kind, setKind] = useState<'receive'|'issue'>('receive'), [note, setNote] = useState('');
  const [picklists, setPicklists] = useState<Record<string, Data[]>>({});
  const [toolEntry, setToolEntry] = useState<ToolEntry | null>(null), [selected, setSelected] = useState<Data | null>(null);
  const [khoTab, setKhoTab] = useState<KhoTab>('stock'), [agentsSeen, setAgentsSeen] = useState(false);
  const [tab, setTab] = useState<HTab>('people'), [filter, setFilter] = useState(area==='Kho' ? 'stock_request' : 'approval');
  const seq = useRef(0), idempotency = useRef(new Map<string,string>()), current = useRef({active:true,mcp}); current.current.mcp = mcp;
  const available = new Set(tools.map(tool => tool.name));
  /** Nhân sự AI chỉ hiện khi gói nhan-su-ai đã cài (catalog thật có ho_so_agent); không có thì ẩn hẳn. */
  const hasAgentHr = catalog.some(row => row.id === AGENT_MODEL);
  const hTabs = H_TABS.filter(t => t.id !== 'agents' || hasAgentHr);
  useEffect(() => { if (!hasAgentHr && tab === 'agents') setTab('people'); }, [hasAgentHr, tab]);
  const fail = (text: string, kind: ErrorKind = 'error') => { setErrorText(text); setErrorKind(text ? kind : 'error'); };
  const fields = (model_id:string) => listFields[model_id];
  async function call(name: string, original: Data, target: 'main' | 'picklist' | 'catalog' = 'main') {
    if (!mcp || !available.has(name)) { fail(`Không có quyền dùng ${name}`, 'forbidden'); return null; }
    const epoch = target === 'main' ? ++seq.current : seq.current;
    const keyId = `${name}:${JSON.stringify(original)}`;
    let args = original;
    if (writeTools.includes(name) && !args.idempotency_key) {
      let key = idempotency.current.get(keyId); if (!key) { key = crypto.randomUUID(); idempotency.current.set(keyId,key); }
      args = {...args, idempotency_key:key};
    }
    if (target === 'main') { setLoading(true); fail(''); }
    const live = () => current.current.active && current.current.mcp === mcp && (target !== 'main' || epoch === seq.current);
    try {
      const result = await mcp.client.callTool({name, arguments:args}) as ToolResult;
      if (!live()) return null;
      if (writeTools.includes(name)) setToolEntry({id:`${name}-${epoch}`, name, args, result, status:result.isError ? 'error':'ok', resourceUri:tools.find(t=>t.name===name)?._meta?.ui?.resourceUri});
      const value = readToolResult(result);
      if (target === 'catalog') {
        setWorkspace(value); const models = Array.isArray(value.models) ? value.models as Data[] : [];
        setCatalog(models); setModel(previous => previous || String(models[0]?.id || ''));
      } else if (target === 'picklist') setPicklists(prev => ({...prev,[String(args.model_id)]:rows(value)}));
      else {
        setData(value);
        if (value.record) setSelected(obj(value.record));
        else if (!writeTools.includes(name) && name !== 'get_history') setSelected(null);
      }
      if (writeTools.includes(name)) {
        onMutated?.();
        for (const [key, value] of idempotency.current) if (value === args.idempotency_key) idempotency.current.delete(key);
        const record = obj(value.record || value.request);
        if (typeof record.ref === 'string') {
          setSelected(record);
          // Read the revision and allowed actions after every successful mutation.
          void call('get_record', {ref:record.ref});
        }
        if (name === 'create_record' && ['warehouse','warehouse_location','stock_item'].includes(String(args.model_id))) void call('search_records',{model_id:args.model_id,limit:100,fields:fields(String(args.model_id))},'picklist');
      }
      return value;
    } catch (e) { if (live()) { if (target === 'catalog') setWorkspace(null); fail((e as Error).message, classifyError(e)); } return null; }
    finally { if (live() && target === 'main') setLoading(false); }
  }
  useEffect(() => {
    current.current.active = true;
    if (mcp) {
      if (area === 'H' || area === 'D' || area === 'P') void call('inspect_workspace',{},'catalog');
      if (area === 'H') void call('find_people',{limit:50});
      if (area === 'P') void call('get_inbox',{kinds:['approval']});
      if (area === 'D') setData(null);
      if (area === 'Kho') {
        for (const model_id of ['warehouse','warehouse_location','stock_item','stock_balance']) void call('search_records',{model_id,limit:100,fields:fields(model_id)},'picklist');
        void call('search_records',{model_id:'stock_request',limit:50,fields:fields('stock_request')});
      }
    }
    return () => { current.current.active = false; seq.current++; };
  }, [mcp,area]);
  useEffect(() => {
    if (!mcp || !intent || area !== 'H') return;
    setTab('people');
    if (intent.kind === 'people') { setQuery(intent.query); void call('find_people',{query:intent.query,limit:50}); }
    else void call('get_inbox',{kinds:['task','notification','mention']});
  }, [mcp, intent?.id]); // eslint-disable-line
  const openRecord = (ref:string) => { void call('get_record',{ref}); };
  const title: Record<string,string> = {H:'Con người',P:'Quy trình',D:'Dữ liệu',I:'Trí tuệ',Kho:'Phiếu và tồn kho'};
  const select = (modelId:string, value:string, update:(value:string)=>void, list=picklists[modelId] || []) => <select aria-label={labels[modelId]} value={value} onChange={e=>update(e.target.value)} required><option value="">Chọn {labels[modelId]}</option>{list.map(row=><option key={uuid(row)} value={uuid(row)}>{label(row)}</option>)}</select>;
  const locationRows = (picklists.warehouse_location || []).filter(row => {
    const owner = obj(row.values).warehouse || row.warehouse;
    return warehouse && (String(owner) === warehouse || String(obj(owner).ref || owner).endsWith(`:${warehouse}`));
  });
  const selectedRef = typeof selected?.ref === 'string' ? selected.ref : '';
  useEffect(() => { setRejecting(false); setReason(''); }, [selectedRef]);
  const revision = selected?.revision;
  const selectedState = String(selected?.state || obj(selected?.values).state || '');
  const canSubmit = selectedRef.startsWith('stock_request:') && ['draft','returned'].includes(selectedState) && typeof revision === 'number';
  const canDecide = selected && ['in_review','submitted'].includes(selectedState) && typeof revision === 'number';
  const resultRows = rows(data), isList = !!data && ['items','people','rows','events'].some(key => Array.isArray(data[key]));
  const enumLabel = useEnumLabels(mcp, tools, data ? (isList ? resultRows : [obj(data.record || data.request || data.me || data)]).map(modelOf).filter(id => warehouseModels.includes(id) || catalog.some(row => row.id === id)) : []);
  const confirmCard = errorKind==='confirm' && !!toolEntry && !!confirmInfo(toolEntry.result, config.xtOrigin); // thẻ xác nhận đã nói đủ; không lặp lại ở thông báo
  const output = <>
    {selectedRef && <div className="portal-actions record-actions"><button className="ghost" disabled={loading} onClick={()=>openRecord(selectedRef)}>Làm mới bản ghi<RefreshCw aria-hidden="true"/></button><button className="ghost" disabled={loading} onClick={()=>void call('get_history',{ref:selectedRef})}>Lịch sử<History aria-hidden="true"/></button></div>}
    {area==='P' && selectedRef.startsWith('jd_agent:') && selected && <JdRiskBlock record={selected} models={catalog}/>}
    {(area==='P'||area==='Kho') && canDecide && <div className="portal-actions decide-actions"><button className="ok" disabled={loading} onClick={()=>void call('decide_request',{ref:selectedRef,decision:'approve',expected_revision:revision})}>Duyệt<Check aria-hidden="true"/></button><button className="danger" disabled={loading} aria-expanded={rejecting} onClick={()=>setRejecting(true)}>Từ chối<X aria-hidden="true"/></button></div>}
    {(area==='P'||area==='Kho') && canDecide && rejecting && <form onSubmit={e=>{e.preventDefault();const text=reason.trim();if(!text){fail('Nhập lý do từ chối');return;}setRejecting(false);setReason('');void call('decide_request',{ref:selectedRef,decision:'reject',reason:text,expected_revision:revision});}}><label htmlFor="reject-reason">Lý do từ chối</label><textarea id="reject-reason" value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000} rows={3} required autoFocus/><button className="danger" disabled={loading}>Gửi từ chối</button><button type="button" onClick={()=>{setRejecting(false);setReason('');}}>Hủy</button></form>}
    {loading && <p role="status">Đang tải…</p>}{!confirmCard && <PortalAlert text={error} kind={errorKind}/>}
    {toolEntry && mcp && <><ToolItem entry={toolEntry} client={mcp.client} allTools={mcp.tools} sandboxUrl={sandboxUrl} onOpenLink={onOpenLink} dpop={revealDpop}/>{toolEntry.status==='error' && <button className="pri" disabled={loading} onClick={()=>void call(toolEntry.name,toolEntry.args)}>Thử lại sau khi xác nhận<RefreshCw aria-hidden="true"/></button>}</>}
    {data && <div className="portal-results">{typeof data.summary==='string' && <p>{data.summary}</p>}{!!data.counts && <p>{Object.entries(obj(data.counts)).map(([key,value]) => `${valueLabel('kind',key)}: ${display(value)}`).join(' · ')}</p>}{!!data.totals && <p>Tổng: {display(data.totals)}</p>}{isList ? resultRows.length ? resultRows.map((row,i)=><RecordCard key={String(row.ref||row.id||i)} row={row} select={openRecord} enumLabel={enumLabel}/>) : <p>Không có kết quả.</p> : <RecordCard row={obj(data.record || data.request || data.me || data)} select={openRecord} enumLabel={enumLabel}/>}</div>}
  </>;
  const outputFirst = area==='P' || area==='D';
  return <section className="portal" aria-label={title[area]}><h2 tabIndex={-1}>{title[area]}</h2>
    {!mcp && <p role="status">Đang kết nối xT…</p>}
    {area==='H' && <>
      <TabList label="Mục Con người" prefix="h" tabs={hTabs} value={tab} onChange={next=>{setTab(next);if(next==='agents')setAgentsSeen(true);}}/>
      <div role="tabpanel" id="h-panel-people" aria-labelledby="h-tab-people" hidden={tab!=='people'}>
        {workspace && <div className="workspace-card"><span className="av" aria-hidden="true"><Building2/></span><span className="t"><strong>{display(obj(workspace.tenant).name || obj(workspace.workspace).name || 'Doanh nghiệp')}</strong><small>{display(obj(workspace.me).name)}</small></span></div>}
        <div className="panel">
          <form onSubmit={e=>{e.preventDefault();void call('find_people',{query,limit:50});}}><input aria-label="Tìm nhân sự" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tên, email hoặc chức danh"/><button className="pri" disabled={!mcp}>Tìm người<Search aria-hidden="true"/></button></form>
          <div className="portal-actions seg" role="group" aria-label="Lọc nhanh"><button onClick={()=>void call('find_people',{manager_of:'me'})}>Quản lý của tôi<UserRound aria-hidden="true"/></button><button onClick={()=>void call('find_people',{direct_reports_of:'me'})}>Nhóm của tôi<Users aria-hidden="true"/></button><button onClick={()=>void call('get_inbox',{kinds:['task','notification','mention']})}>Việc của tôi<ListChecks aria-hidden="true"/></button></div>
        </div>
      </div>
      {tab==='people' && output}
    </>}
    {area==='P' && <><div className="head-act"><button className="pri" onClick={()=>document.querySelector<HTMLElement>('.portal-request')?.scrollIntoView({block:'start'})}>Tạo đề xuất<Plus aria-hidden="true"/></button></div><div className="portal-actions tabs" role="group" aria-label="Hộp duyệt"><button aria-pressed={filter==='approval'} onClick={()=>{setFilter('approval');void call('get_inbox',{kinds:['approval']});}}>Chờ tôi duyệt</button><button aria-pressed={filter==='all'} onClick={()=>{setFilter('all');void call('get_inbox',{});}}>Toàn bộ inbox</button>{catalog.filter(row=>row.primitive==='request').map(row=><button key={String(row.id)} aria-pressed={filter===String(row.id)} onClick={()=>{setFilter(String(row.id));void call('search_records',{model_id:row.id,limit:50});}}>{label(row)}</button>)}</div>{workspace && Array.isArray(workspace.processes) && <p className="lead-sm">Quy trình: {(workspace.processes as Data[]).map(label).join(', ') || 'Chưa có quy trình'}</p>}</>}
    {area==='D' && <section className="panel portal-records" aria-label="Tra cứu dữ liệu"><div className="ph"><h3>Bản ghi</h3><div className="act"><button className="ghost sm" onClick={()=>void call('inspect_workspace',{},'catalog')}>Làm mới danh mục<RefreshCw aria-hidden="true"/></button></div></div><label>Loại dữ liệu <select aria-label="Loại dữ liệu" value={model} onChange={e=>setModel(e.target.value)}>{!catalog.length && <option value="">Chưa có danh mục</option>}{catalog.map(row=><option key={String(row.id)} value={String(row.id)}>{label(row)}</option>)}</select></label><form onSubmit={e=>{e.preventDefault();void call('search_records',{model_id:model,limit:50,...(query?{query}:{})});}}><input aria-label="Tìm bản ghi" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Từ khóa"/><button className="pri" disabled={!mcp||!model}>Tìm<Search aria-hidden="true"/></button></form>{!data && catalog.length>0 && <details className="portal-catalog"><summary>{`Danh mục dữ liệu (${catalog.length})`}</summary><ul className="portal-doclist">{catalog.map(row=><li key={String(row.id)}><span><strong>{label(row)}</strong> · {primitiveLabel(row.primitive)}</span><button className="sm" onClick={()=>{setModel(String(row.id));void call('search_records',{model_id:row.id,limit:50});}}>Xem dữ liệu</button></li>)}</ul></details>}</section>}
    {outputFirst && output}
    {area==='H' && <div role="tabpanel" id="h-panel-docs" aria-labelledby="h-tab-docs" hidden={tab!=='docs'}><DocumentsPanel mcp={mcp} tools={tools} models={workspace ? catalog : null}/></div>}
    {area==='H' && <div role="tabpanel" id="h-panel-units" aria-labelledby="h-tab-units" hidden={tab!=='units'}><DepartmentsPanel mcp={mcp} tools={tools}/></div>}
    {area==='H' && hasAgentHr && <div role="tabpanel" id="h-panel-agents" aria-labelledby="h-tab-agents" hidden={tab!=='agents'}>{agentsSeen && <AgentHrPanel mcp={mcp} tools={tools} models={catalog} me={workspace ? obj(workspace.me) : null} onMutated={onMutated}/>}</div>}
    {area==='H' && <div role="tabpanel" id="h-panel-members" aria-labelledby="h-tab-members" hidden={tab!=='members'}><MembersPanel mcp={mcp} tools={tools} workspace={workspace} onRefresh={()=>{void call('inspect_workspace',{},'catalog');onMutated?.();}}/></div>}
    {area==='P' && <RequestForm mcp={mcp} tools={tools} models={workspace ? catalog : null} onDone={onMutated}/>}
    {area==='D' && <ReportPanel mcp={mcp} tools={tools} models={workspace ? catalog : null}/>}
    {area==='I' && <><div className="head-act"><button onClick={()=>onNavigate?.('Desk')} disabled={!onNavigate}>Mở trợ lý Desk<MessageSquare aria-hidden="true"/></button></div><p className="lead-sm">Studio dùng phiên và quyền xT của bạn, như trợ lý Desk.</p><iframe className="portal-frame" data-xt-scope="studio" title="xT Studio" src="/studio/?integrated=1"/></>}
    {area==='Kho' && <><div className="head-act"><button className="pri" onClick={()=>setKhoTab('requests')}>Tạo phiếu<Plus aria-hidden="true"/></button></div><p className="lead-sm">Số lượng có độ chính xác 0,001 đơn vị.</p>
      <TabList label="Mục kho" prefix="kho" tabs={KHO_TABS} value={khoTab} onChange={setKhoTab}/>
      <TabPanel prefix="kho" id="stock" active={khoTab==='stock'}><StockTable balances={picklists.stock_balance} items={picklists.stock_item || []} locations={picklists.warehouse_location || []} loading={loading} onRefresh={()=>{for (const model_id of ['stock_item','warehouse_location','stock_balance']) void call('search_records',{model_id,limit:100,fields:fields(model_id)},'picklist');}}/></TabPanel>
      <TabPanel prefix="kho" id="requests" active={khoTab==='requests'}>
      <form className="panel" aria-label="Phiếu nhập/xuất" onSubmit={e=>{e.preventDefault();const milli=parseMilli(quantity);if(milli===null){fail('Số lượng phải lớn hơn 0 và có tối đa 3 chữ số thập phân');return;}if(!warehouse||!item||!locationRows.some(row=>uuid(row)===location)){fail('Chọn kho, hàng hóa và vị trí thuộc kho');return;}void call('create_record',{model_id:'stock_request',values:{code:requestCode,kind,warehouse,lines:[{item,location,quantity_milli:milli}],note}});}}><h3>Phiếu nhập/xuất</h3><label>Loại phiếu <select value={kind} onChange={e=>setKind(e.target.value as 'receive'|'issue')}><option value="receive">Nhập</option><option value="issue">Xuất</option></select></label><label>Mã phiếu <input aria-label="Mã phiếu" value={requestCode} onChange={e=>setRequestCode(e.target.value)} maxLength={100} required/></label>{select('warehouse',warehouse,value=>{setWarehouse(value);setLocation('');})}{select('stock_item',item,setItem)}{select('warehouse_location',location,setLocation,locationRows)}<label>Số lượng <input aria-label="Số lượng" type="number" min="0.001" step="0.001" value={quantity} onChange={e=>setQuantity(e.target.value)} required/></label><label>Ghi chú <input aria-label="Ghi chú" value={note} onChange={e=>setNote(e.target.value)} maxLength={2000}/></label><button className="pri" disabled={!mcp||loading}>Tạo nháp<Plus aria-hidden="true"/></button></form>
      <div className="portal-actions"><button className="pri" disabled={!mcp||loading||!canSubmit} onClick={()=>void call('submit_request',{ref:selectedRef,expected_revision:revision})}>Gửi phiếu đi duyệt<Send aria-hidden="true"/></button></div>
      </TabPanel>
      <TabPanel prefix="kho" id="catalog" active={khoTab==='catalog'}>
      <form className="panel" aria-label="Thêm danh mục" onSubmit={e=>{e.preventDefault();const milli=parseMilli(reorder,true);if(catalogModel==='stock_item' && milli===null){fail('Ngưỡng đặt lại không hợp lệ');return;}const values=catalogModel==='warehouse'?{code,name}:catalogModel==='stock_item'?{sku:code,name,unit,reorder_level_milli:milli}:{code,name,warehouse};void call('create_record',{model_id:catalogModel,values});}}><h3>Thêm danh mục</h3><label>Loại <select aria-label="Loại danh mục" value={catalogModel} onChange={e=>setCatalogModel(e.target.value)}><option value="warehouse">Kho</option><option value="warehouse_location">Vị trí</option><option value="stock_item">Hàng hóa</option></select></label><input aria-label="Mã danh mục" placeholder="Mã" value={code} onChange={e=>setCode(e.target.value)} maxLength={100} required/><input aria-label="Tên danh mục" placeholder="Tên" value={name} onChange={e=>setName(e.target.value)} maxLength={200} required/>{catalogModel==='warehouse_location' && select('warehouse',warehouse,value=>{setWarehouse(value);setLocation('');})}{catalogModel==='stock_item' && <><label>Đơn vị <input aria-label="Đơn vị" value={unit} onChange={e=>setUnit(e.target.value)} maxLength={30} required/></label><label>Ngưỡng đặt lại <input aria-label="Ngưỡng đặt lại" type="number" min="0" step="0.001" value={reorder} onChange={e=>setReorder(e.target.value)}/></label></>}<button className="pri" disabled={!mcp||loading}>Tạo<Plus aria-hidden="true"/></button></form>
      </TabPanel>
      <TabPanel prefix="kho" id="map" active={khoTab==='map'}><section className="portal-waretrack" aria-label="WareTrack 3D"><h3>Sơ đồ kho 3D</h3><p>WareTrack chạy trong cổng xT và chỉ đọc số liệu qua cầu nối của cổng; chế độ mô phỏng dùng dữ liệu minh họa và không đổi tồn kho.</p><iframe className="portal-frame" data-xt-scope="waretrack" title="xT WareTrack" src="/waretrack/?integrated=1" loading="lazy"/><a href="/waretrack/" target="_blank" rel="noopener noreferrer">Mở WareTrack ở tab mới (không có số liệu nghiệp vụ)<ExternalLink aria-hidden="true"/></a></section></TabPanel>
      {(khoTab==='stock'||khoTab==='map') && <PortalAlert text={error} kind={errorKind}/>}
      <div hidden={khoTab==='stock'||khoTab==='map'}><div className="portal-actions tabs" role="group" aria-label="Dữ liệu kho">{warehouseModels.map(x=><button key={x} aria-pressed={filter===x} onClick={()=>{setFilter(x);void call('search_records',{model_id:x,limit:50,fields:fields(x)});}}>{labels[x]}</button>)}</div>{output}</div></>}
    {!outputFirst && area!=='H' && area!=='Kho' && output}
  </section>;
}
