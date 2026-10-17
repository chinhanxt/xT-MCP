// SPDX-License-Identifier: Apache-2.0
import { useEffect, useRef, useState, type ComponentProps } from 'react';
import { ToolItem, type ToolEntry } from './ToolItem';
import type { McpConnection } from './mcp';
import type { McpTool, ToolResult } from './types';
import { parseMilli, readToolResult } from './portal-result';
import { classifyError, stateHint, stateTitle, type ErrorKind } from './portal-state';

type Data = Record<string, unknown>;
const warehouseModels = ['warehouse', 'warehouse_location', 'stock_item', 'stock_request', 'stock_balance', 'stock_movement'];
const labels: Record<string, string> = { warehouse:'Kho', warehouse_location:'Vị trí', stock_item:'Hàng hóa', stock_request:'Phiếu nhập/xuất', stock_balance:'Tồn kho', stock_movement:'Biến động' };
const fieldLabels: Record<string,string> = {state:'Trạng thái', revision:'Phiên bản', code:'Mã', sku:'Mã hàng', unit:'Đơn vị', warehouse:'Kho', item:'Hàng hóa', location:'Vị trí', quantity_milli:'Số lượng', reorder_level_milli:'Ngưỡng đặt lại', kind:'Loại', title:'Chức danh', email:'Email', phone:'Điện thoại', org_unit:'Bộ phận', manager:'Quản lý', due_at:'Hạn', sla_status:'SLA', received_at:'Nhận lúc', note:'Ghi chú', lines:'Dòng hàng'};
const listFields: Record<string,string[]> = {warehouse:['code','name'],warehouse_location:['code','name','warehouse'],stock_item:['sku','name','unit','reorder_level_milli'],stock_request:['code','kind','warehouse','lines','note'],stock_balance:['item','location','quantity_milli'],stock_movement:['item','location','quantity_milli','posted_at','approved_by']};
const writeTools = ['create_record', 'submit_request', 'decide_request'];
function obj(value: unknown): Data { return value && typeof value === 'object' && !Array.isArray(value) ? value as Data : {}; }
function rows(value: Data | null): Data[] {
  for (const key of ['items', 'people', 'rows', 'events']) if (Array.isArray(value?.[key])) return value[key] as Data[];
  return [];
}
function uuid(row: Data): string { return String(row.id || row.ref || '').split(':').at(-1) || ''; }
function label(row: Data): string { const values = obj(row.values); return String(row.name || values.name || row.title || row.label || row.code || row.sku || values.code || values.sku || row.ref || row.id || 'Chi tiết'); }
function display(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) return value.map(display).join(', ');
  if (typeof value === 'object') { const data = obj(value); return String(data.name || data.label || Object.entries(data).map(([k,v]) => `${k}: ${display(v)}`).join(' · ')); }
  return String(value);
}
function RecordCard({ row, select }: { row: Data; select?: (ref: string) => void }) {
  const ref = typeof row.ref === 'string' ? row.ref : '';
  const details = {...Object.fromEntries(Object.entries(row).filter(([key]) => !['values','ref','id','name','title','label'].includes(key))), ...obj(row.values)};
  return <article className="portal-entry"><strong>{label(row)}</strong><dl>{Object.entries(details).map(([key,value]) => <div key={key}><dt>{fieldLabels[key] || key}</dt><dd>{key.endsWith('_milli') && typeof value==='number' ? (value/1000).toFixed(3) : display(value)}</dd></div>)}</dl>
    {ref && select && <button onClick={() => select(ref)}>Mở {label(row)}</button>}
  </article>;
}
type Props = {
  area: string; mcp: McpConnection | null; tools: McpTool[]; sandboxUrl: URL;
  onOpenLink: (url: string, frame: Element | null) => boolean;
  revealDpop: ComponentProps<typeof ToolItem>['dpop'];
};
export function Portal(props: Props) {
  const session = useRef({mcp:props.mcp, epoch:0});
  if (session.current.mcp !== props.mcp) session.current = {mcp:props.mcp, epoch:session.current.epoch+1};
  return <PortalView key={`${props.area}:${session.current.epoch}`} {...props}/>;
}
function PortalView({ area, mcp, tools, sandboxUrl, onOpenLink, revealDpop }: Props) {
  const [data, setData] = useState<Data | null>(null), [error, setErrorText] = useState(''), [errorKind, setErrorKind] = useState<ErrorKind>('error'), [rejecting, setRejecting] = useState(false), [reason, setReason] = useState(''), [loading, setLoading] = useState(false);
  const [query, setQuery] = useState(''), [model, setModel] = useState(''), [catalogModel, setCatalogModel] = useState('stock_item');
  const [catalog, setCatalog] = useState<Data[]>([]), [workspace, setWorkspace] = useState<Data | null>(null);
  const [code, setCode] = useState(''), [name, setName] = useState(''), [unit, setUnit] = useState('pcs');
  const [requestCode, setRequestCode] = useState(''), [reorder, setReorder] = useState('0'), [quantity, setQuantity] = useState('1');
  const [warehouse, setWarehouse] = useState(''), [location, setLocation] = useState(''), [item, setItem] = useState('');
  const [kind, setKind] = useState<'receive'|'issue'>('receive'), [note, setNote] = useState('');
  const [picklists, setPicklists] = useState<Record<string, Data[]>>({});
  const [toolEntry, setToolEntry] = useState<ToolEntry | null>(null), [selected, setSelected] = useState<Data | null>(null);
  const seq = useRef(0), idempotency = useRef(new Map<string,string>()), current = useRef({active:true,mcp}); current.current.mcp = mcp;
  const available = new Set(tools.map(tool => tool.name));
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
    } catch (e) { if (live()) fail((e as Error).message, classifyError(e)); return null; }
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
        for (const model_id of ['warehouse','warehouse_location','stock_item']) void call('search_records',{model_id,limit:100,fields:fields(model_id)},'picklist');
        void call('search_records',{model_id:'stock_request',limit:50,fields:fields('stock_request')});
      }
    }
    return () => { current.current.active = false; seq.current++; };
  }, [mcp,area]);
  const openRecord = (ref:string) => { void call('get_record',{ref}); };
  const title: Record<string,string> = {H:'Con người',P:'Quy trình',D:'Dữ liệu',I:'Trí tuệ',Kho:'WareTrack'};
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
  return <section className="portal" aria-label={title[area]}><h2>{title[area]}</h2>
    {!mcp && <p role="status">Đang kết nối xT…</p>}
    {area==='H' && <>{workspace && <article className="portal-entry"><strong>{display(obj(workspace.tenant).name || obj(workspace.workspace).name || 'Doanh nghiệp')}</strong><p>{display(obj(workspace.me).name)}</p></article>}<p>Nhân sự, trách nhiệm, công việc và tài liệu theo quyền của bạn.</p><form onSubmit={e=>{e.preventDefault();void call('find_people',{query,limit:50});}}><input aria-label="Tìm nhân sự" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Tên, email hoặc chức danh"/><button disabled={!mcp}>Tìm người</button></form><div className="portal-actions"><button onClick={()=>void call('find_people',{manager_of:'me'})}>Quản lý của tôi</button><button onClick={()=>void call('find_people',{direct_reports_of:'me'})}>Nhóm của tôi</button><button onClick={()=>void call('get_inbox',{kinds:['task','notification','mention']})}>Việc của tôi</button><button onClick={()=>void call('search_records',{query:'SOP'})}>Tài liệu/SOP</button></div></>}
    {area==='P' && <><p>Yêu cầu và bước duyệt theo quyền của bạn.</p><div className="portal-actions"><button onClick={()=>void call('get_inbox',{kinds:['approval']})}>Chờ tôi duyệt</button><button onClick={()=>void call('get_inbox',{})}>Toàn bộ inbox</button>{catalog.filter(row=>row.primitive==='request').map(row=><button key={String(row.id)} onClick={()=>void call('search_records',{model_id:row.id,limit:50})}>{label(row)}</button>)}</div>{workspace && Array.isArray(workspace.processes) && <p>Quy trình: {(workspace.processes as Data[]).map(label).join(', ') || 'Chưa có quy trình'}</p>}</>}
    {area==='D' && <><p>Danh mục, tìm kiếm và số liệu từ xT Core.</p><label>Loại dữ liệu <select aria-label="Loại dữ liệu" value={model} onChange={e=>setModel(e.target.value)}>{!catalog.length && <option value="">Chưa có danh mục</option>}{catalog.map(row=><option key={String(row.id)} value={String(row.id)}>{label(row)}</option>)}</select></label><form onSubmit={e=>{e.preventDefault();void call('search_records',{model_id:model,limit:50,...(query?{query}:{})});}}><input aria-label="Tìm bản ghi" value={query} onChange={e=>setQuery(e.target.value)} placeholder="Từ khóa"/><button disabled={!mcp||!model}>Tìm</button></form><div className="portal-actions"><button disabled={!model} onClick={()=>void call('aggregate_records',{model_id:model,metrics:['count']})}>Thống kê</button><button onClick={()=>void call('inspect_workspace',{},'catalog')}>Làm mới danh mục</button></div>{!data && <div className="portal-results">{catalog.map(row=><article className="portal-entry" key={String(row.id)}><strong>{label(row)}</strong><p>{String(row.primitive)}</p><button onClick={()=>{setModel(String(row.id));void call('search_records',{model_id:row.id,limit:50});}}>Xem dữ liệu</button></article>)}</div>}</>}
    {area==='I' && <><p>Studio dùng phiên xT của bạn. Trợ lý Desk ở tab bên cạnh.</p><iframe className="portal-frame" title="xT Studio" src="/studio/?integrated=1"/></>}
    {area==='Kho' && <><p>Phiếu và tồn kho nghiệp vụ từ xT Core. Số lượng có độ chính xác 0,001 đơn vị.</p><div className="portal-actions">{warehouseModels.map(x=><button key={x} onClick={()=>void call('search_records',{model_id:x,limit:50,fields:fields(x)})}>{labels[x]}</button>)}</div>
      <form onSubmit={e=>{e.preventDefault();const milli=parseMilli(reorder,true);if(catalogModel==='stock_item' && milli===null){fail('Ngưỡng đặt lại không hợp lệ');return;}const values=catalogModel==='warehouse'?{code,name}:catalogModel==='stock_item'?{sku:code,name,unit,reorder_level_milli:milli}:{code,name,warehouse};void call('create_record',{model_id:catalogModel,values});}}><h3>Thêm danh mục</h3><label>Loại <select aria-label="Loại danh mục" value={catalogModel} onChange={e=>setCatalogModel(e.target.value)}><option value="warehouse">Kho</option><option value="warehouse_location">Vị trí</option><option value="stock_item">Hàng hóa</option></select></label><input aria-label="Mã danh mục" placeholder="Mã" value={code} onChange={e=>setCode(e.target.value)} maxLength={100} required/><input aria-label="Tên danh mục" placeholder="Tên" value={name} onChange={e=>setName(e.target.value)} maxLength={200} required/>{catalogModel==='warehouse_location' && select('warehouse',warehouse,value=>{setWarehouse(value);setLocation('');})}{catalogModel==='stock_item' && <><input aria-label="Đơn vị" value={unit} onChange={e=>setUnit(e.target.value)} maxLength={30} required/><input aria-label="Ngưỡng đặt lại" type="number" min="0" step="0.001" value={reorder} onChange={e=>setReorder(e.target.value)}/></>}<button disabled={!mcp||loading}>Tạo</button></form>
      <form onSubmit={e=>{e.preventDefault();const milli=parseMilli(quantity);if(milli===null){fail('Số lượng phải lớn hơn 0 và có tối đa 3 chữ số thập phân');return;}if(!warehouse||!item||!locationRows.some(row=>uuid(row)===location)){fail('Chọn kho, hàng hóa và vị trí thuộc kho');return;}void call('create_record',{model_id:'stock_request',values:{code:requestCode,kind,warehouse,lines:[{item,location,quantity_milli:milli}],note}});}}><h3>Phiếu nhập/xuất</h3><label>Loại phiếu <select value={kind} onChange={e=>setKind(e.target.value as 'receive'|'issue')}><option value="receive">Nhập</option><option value="issue">Xuất</option></select></label><input aria-label="Mã phiếu" value={requestCode} onChange={e=>setRequestCode(e.target.value)} maxLength={100} required/>{select('warehouse',warehouse,value=>{setWarehouse(value);setLocation('');})}{select('stock_item',item,setItem)}{select('warehouse_location',location,setLocation,locationRows)}<input aria-label="Số lượng" type="number" min="0.001" step="0.001" value={quantity} onChange={e=>setQuantity(e.target.value)} required/><input aria-label="Ghi chú" value={note} onChange={e=>setNote(e.target.value)} maxLength={2000}/><button disabled={!mcp||loading}>Tạo nháp</button></form>
      <button disabled={!mcp||loading||!canSubmit} onClick={()=>void call('submit_request',{ref:selectedRef,expected_revision:revision})}>Gửi phiếu đi duyệt</button><a href="/waretrack/" target="_blank" rel="noopener noreferrer">Mở mô phỏng kho 3D (dữ liệu minh họa)</a></>}
    {selectedRef && <div className="portal-actions"><button disabled={loading} onClick={()=>openRecord(selectedRef)}>Làm mới bản ghi</button><button disabled={loading} onClick={()=>void call('get_history',{ref:selectedRef})}>Lịch sử</button></div>}
    {(area==='P'||area==='Kho') && canDecide && <div className="portal-actions"><button disabled={loading} onClick={()=>void call('decide_request',{ref:selectedRef,decision:'approve',expected_revision:revision})}>Duyệt</button><button disabled={loading} aria-expanded={rejecting} onClick={()=>setRejecting(true)}>Từ chối</button></div>}
    {(area==='P'||area==='Kho') && canDecide && rejecting && <form onSubmit={e=>{e.preventDefault();const text=reason.trim();if(!text){fail('Nhập lý do từ chối');return;}setRejecting(false);setReason('');void call('decide_request',{ref:selectedRef,decision:'reject',reason:text,expected_revision:revision});}}><label htmlFor="reject-reason">Lý do từ chối</label><textarea id="reject-reason" value={reason} onChange={e=>setReason(e.target.value)} maxLength={2000} rows={3} required autoFocus/><button disabled={loading}>Gửi từ chối</button><button type="button" onClick={()=>{setRejecting(false);setReason('');}}>Hủy</button></form>}
    {loading && <p role="status">Đang tải…</p>}{error && <div role="alert" className={`note portal-${errorKind}`} data-state={errorKind}>{errorKind!=='error' && <strong>{stateTitle[errorKind]}. </strong>}{error}{stateHint[errorKind] && <p>{stateHint[errorKind]}</p>}</div>}
    {toolEntry && mcp && <><ToolItem entry={toolEntry} client={mcp.client} allTools={mcp.tools} sandboxUrl={sandboxUrl} onOpenLink={onOpenLink} dpop={revealDpop}/>{toolEntry.status==='error' && <button disabled={loading} onClick={()=>void call(toolEntry.name,toolEntry.args)}>Thử lại sau khi xác nhận</button>}</>}
    {data && <div className="portal-results">{typeof data.summary==='string' && <p>{data.summary}</p>}{!!data.counts && <p>{display(data.counts)}</p>}{!!data.totals && <p>Tổng: {display(data.totals)}</p>}{isList ? resultRows.length ? resultRows.map((row,i)=><RecordCard key={String(row.ref||row.id||i)} row={row} select={openRecord}/>) : <p>Không có kết quả.</p> : <RecordCard row={obj(data.record || data.request || data.me || data)} select={openRecord}/>}</div>}
  </section>;
}
