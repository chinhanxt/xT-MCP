// @vitest-environment jsdom
// SPDX-License-Identifier: Apache-2.0
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, expect, it, vi } from 'vitest';
import type { McpConnection } from '../src/mcp';
import type { McpTool, ToolResult } from '../src/types';
vi.mock('../src/config', () => ({config:{xtOrigin:'https://xt.test',appGlobalCallsPerSec:10,appGlobalBurst:10}}));
import { Portal } from '../src/Portal';
const result = (data:unknown):ToolResult => ({content:[{type:'text',text:JSON.stringify({ok:true,data})}]});
let root:Root | undefined;
let container:HTMLDivElement;
let mcp:McpConnection;
let tools:McpTool[];
async function mount(area:string, caller: (request:{name:string;arguments:Record<string,unknown>})=>Promise<ToolResult>) {
  (globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement('div'); document.body.append(container);
  tools = ['inspect_workspace','find_people','search_records','get_record','get_inbox','aggregate_records','get_history','create_record','submit_request','decide_request'].map(name=>({name,inputSchema:{type:'object'}}));
  mcp = {client:{callTool:vi.fn(caller)},tools} as unknown as McpConnection;
  root=createRoot(container);
  await render(area);
}
async function render(area:string) { await act(async()=>{root!.render(createElement(Portal,{area,mcp,tools,sandboxUrl:new URL('https://sandbox.test'),onOpenLink:()=>true,revealDpop:undefined}));}); }
async function click(text:string) {
  const button=Array.from(container.querySelectorAll('button')).find(button=>button.textContent===text);
  expect(button, `button ${text}`).toBeTruthy();
  await act(async()=>button!.click());
}
afterEach(async()=>{if(root) await act(async()=>root!.unmount()); root=undefined; document.body.innerHTML=''; vi.restoreAllMocks();});
it('uses actual workspace model catalog, reports counts and renders an empty search', async()=>{
  await mount('D',async request=>{
    if(request.name==='inspect_workspace') return result({me:{name:'Lan'},models:[{id:'advance_request',label:'Tạm ứng',primitive:'request'},{id:'document',label:'Tài liệu',primitive:'document'}]});
    if(request.name==='aggregate_records') {expect(request.arguments).toEqual({model_id:'advance_request',metrics:['count']});return result({rows:[],totals:{count:0},summary:'Không có tạm ứng.'});}
    return result({items:[],next_cursor:null});
  });
  expect(container.querySelector('select')?.textContent).toBe('Tạm ứngTài liệu');
  await click('Thống kê'); expect(container.textContent).toContain('count: 0');
  await act(async()=>container.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(container.textContent).toContain('Không có kết quả.');
  expect(container.textContent).not.toContain('undefined');
});
it('opens an inbox request, preserves human confirmation and retries the exact idempotent decision',async()=>{
  let decisions=0;
  const request={ref:'stock_request:one',title:'Nhập vật tư',state:'in_review',revision:2,values:{code:'PN-1'}};
  await mount('P',async call=>{
    if(call.name==='inspect_workspace') return result({me:{name:'Mai'},models:[],processes:[]});
    if(call.name==='get_inbox') return result({counts:{approval:1},items:[{ref:request.ref,title:request.title,kind:'approval',state:'in_review',sla_status:'on_track',due_at:'2026-10-17'}],next_cursor:null});
    if(call.name==='get_record') return result({record:request});
    if(call.name==='decide_request') {
      decisions++;
      expect(call.arguments).toMatchObject({ref:request.ref,decision:'approve',expected_revision:2,idempotency_key:expect.any(String)});
      return decisions===1 ? {isError:true,content:[{type:'text',text:JSON.stringify({ok:false,error:{code:'confirmation_required',message:'Cần xác nhận',details:{confirm_url:'https://xt.test/desk/confirm/decision-1',confirmation_id:'decision-1',plan_summary:'Duyệt PN-1'}}})}]} : result({request:{...request,state:'approved',revision:3},outcome:'request_approved'});
    }
    throw new Error('Unexpected tool');
  });
  expect(container.textContent).toContain('on_track');
  await click('Mở Nhập vật tư'); await click('Duyệt');
  expect(container.querySelector('[data-testid="confirm-card"] a')?.getAttribute('href')).toBe('https://xt.test/desk/confirm/decision-1');
  await click('Thử lại sau khi xác nhận');
  const calls=vi.mocked(mcp.client.callTool).mock.calls.map(call=>call[0]).filter(call=>call.name==='decide_request');
  expect(calls).toHaveLength(2); expect(calls[1].arguments).toEqual(calls[0].arguments);
});
it('discards the previous area data and form state when navigating',async()=>{
  await mount('H',async call=>call.name==='find_people'?result({summary:'Một người',people:[{ref:'person:lan',name:'Lan',title:'Kế toán',email:'lan@example.test',manager:{name:'Mai',ref:'person:mai'}}],next_cursor:null}):result({me:{name:'Lan'},models:[]}));
  expect(container.textContent).toContain('lan@example.test');
  expect(container.textContent).toContain('Mai');
  await render('D'); expect(container.textContent).not.toContain('lan@example.test');
  await render('H'); expect(container.querySelector('input')?.value).toBe('');
});
it('creates a stock request using named pickers, exact milli quantities and the reviewed revision',async()=>{
  const warehouseId='00000000-0000-4000-8000-000000000001';
  const itemId='00000000-0000-4000-8000-000000000002';
  const locationId='00000000-0000-4000-8000-000000000003';
  const requestRef='stock_request:00000000-0000-4000-8000-000000000004';
  let state='draft';
  await mount('Kho',async call=>{
    if(call.name==='search_records') {
      const items=call.arguments.model_id==='warehouse'?[{ref:`warehouse:${warehouseId}`,title:'HN',values:{code:'HN',name:'Kho Hà Nội'}}]:call.arguments.model_id==='stock_item'?[{ref:`stock_item:${itemId}`,title:'SKU-1',values:{sku:'SKU-1',name:'Vật tư A',unit:'pcs'}}]:call.arguments.model_id==='warehouse_location'?[{ref:`warehouse_location:${locationId}`,title:'A-1',values:{code:'A-1',name:'Kệ A',warehouse:warehouseId}}]:[];
      return result({items,next_cursor:null});
    }
    if(call.name==='create_record') {
      expect(call.arguments).toMatchObject({model_id:'stock_request',idempotency_key:expect.any(String),values:{code:'PN-42',kind:'receive',warehouse:warehouseId,lines:[{item:itemId,location:locationId,quantity_milli:1001}]}});
      return result({record:{ref:requestRef,state:'draft',values:{code:'PN-42'}}});
    }
    if(call.name==='get_record') return result({record:{ref:requestRef,title:'PN-42',state,revision:state==='draft'?1:2,values:{code:'PN-42'}}});
    if(call.name==='submit_request') {expect(call.arguments).toMatchObject({ref:requestRef,expected_revision:1,idempotency_key:expect.any(String)});state='in_review';return result({request:{ref:requestRef,state,revision:2},route:[],process_instance:'process_instance:one'});}
    throw new Error('Unexpected tool');
  });
  async function input(label:string,value:string) {
    const element=container.querySelector<HTMLInputElement>(`input[aria-label="${label}"]`)!;
    await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(element,value);element.dispatchEvent(new Event('input',{bubbles:true}));});
  }
  async function choose(label:string,value:string) {
    const element=container.querySelector<HTMLSelectElement>(`select[aria-label="${label}"]`)!;
    await act(async()=>{element.value=value;element.dispatchEvent(new Event('change',{bubbles:true}));});
  }
  expect(container.querySelector('[aria-label="Kho"]')?.textContent).toContain('Kho Hà Nội');
  await input('Mã phiếu','PN-42'); await choose('Kho',warehouseId); await choose('Hàng hóa',itemId); await choose('Vị trí',locationId); await input('Số lượng','1.001');
  await act(async()=>container.querySelectorAll('form')[1].dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
  expect(vi.mocked(mcp.client.callTool).mock.calls.some(call=>call[0].name==='create_record')).toBe(true);
  await click('Gửi phiếu đi duyệt');
  expect(vi.mocked(mcp.client.callTool).mock.calls.some(call=>call[0].name==='submit_request')).toBe(true);
  expect(container.textContent).toContain('in_review');
});
it('remounts personal state when the MCP session changes and ignores late prior responses',async()=>{
  let finish!: (value:ToolResult)=>void;
  await mount('H',async call=>call.name==='find_people'?new Promise(resolve=>{finish=resolve;}):result({me:{name:'Old account'},models:[]}));
  const input=container.querySelector<HTMLInputElement>('[aria-label="Tìm nhân sự"]')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,'private query');input.dispatchEvent(new Event('input',{bubbles:true}));});
  mcp={client:{callTool:vi.fn(async call=>call.name==='find_people'?result({people:[],next_cursor:null}):result({me:{name:'New account'},models:[]}))},tools} as unknown as McpConnection;
  await render('H');
  await act(async()=>finish(result({people:[{name:'Prior tenant secret',email:'private@example.test'}],next_cursor:null})));
  expect(container.querySelector<HTMLInputElement>('[aria-label="Tìm nhân sự"]')!.value).toBe('');
  expect(container.textContent).not.toContain('Prior tenant secret');
  expect(container.textContent).not.toContain('Old account');
});
