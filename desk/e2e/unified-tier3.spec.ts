// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Tầng 3 on the real built bundle: groups + role-gated lifecycle hint, generic apps, WareTrack module bridge, logout invalidation.
import { expect, test, type Page } from '@playwright/test';
import { loginToTier3, mockPortal, type Call } from './unified-fixtures';
const warehouse='00000000-0000-4000-8000-000000000001', item='00000000-0000-4000-8000-000000000002';
const models=[
  {id:'warehouse',label:'Kho',primitive:'record'},{id:'warehouse_location',label:'Vị trí',primitive:'record'},{id:'stock_item',label:'Hàng hóa',primitive:'record'},
  {id:'stock_balance',label:'Tồn kho',primitive:'record'},{id:'stock_request',label:'Phiếu kho',primitive:'request'},
  {id:'crm_contact',label:'Khách hàng',primitive:'record'},{id:'crm_quote',label:'Báo giá',primitive:'request'},
];
const apps=[
  {id:'kho-hang',title:'Kho hàng (WMS)',group:'hang-hoa',status:'enabled',version:'1.0.0',ui:[{kind:'module',path:'/waretrack/'},{kind:'generic'}],tools:[],models:['warehouse','warehouse_location','stock_item','stock_balance','stock_request']},
  {id:'cham-cong',title:'Chấm công',group:'thoi-gian-du-an',status:'enabled',version:'1.0.0',ui:[{kind:'generic'}],tools:[],models:[]},
  {id:'crm',title:'CRM',group:'trai-nghiem-thue-bao',status:'enabled',version:'1.0.0',ui:[{kind:'generic'}],tools:[],models:['crm_contact','crm_quote']},
  {id:'bao-hanh',title:'Bảo hành',group:'trai-nghiem-thue-bao',status:'disabled',version:'1.0.0',ui:[{kind:'generic'}],tools:[],models:[]},
  {id:'tai-san',title:'Tài sản',group:'tai-san-khong-gian',status:'enabled',version:'1.0.0',ui:[{kind:'generic'}],tools:[],models:[]},
];
const workspace=(roles:string[])=>({me:{ref:'person:lan',name:'Lan',kind:'human',autonomy_ceiling:'L1',roles},tenant:{name:'Doanh nghiệp mẫu'},models,processes:[],apps});
const contacts=[{ref:'crm_contact:00000000-0000-4000-8000-0000000000c1',title:'Nguyễn Văn A',values:{name:'Nguyễn Văn A',phone:'0900000001'}}];
const stockRows:Record<string,unknown[]>={
  warehouse:[{ref:`warehouse:${warehouse}`,title:'HN',values:{code:'HN',name:'Kho Hà Nội'}}],
  stock_item:[{ref:`stock_item:${item}`,title:'VT-1',values:{sku:'VT-1',name:'Vật tư A',unit:'pcs'}}],
  stock_balance:[{ref:'stock_balance:1',values:{item:{name:'Vật tư A'},location:{name:'Kệ A'},quantity_milli:2500}}],
  stock_request:[{ref:'stock_request:1',values:{code:'PN-9',kind:'receive',warehouse:{name:'Kho Hà Nội'},lines:[{}]}}],
};
const fieldsOf:Record<string,unknown[]>={
  crm_contact:[{id:'name',label:'Họ tên',type:'string',required:true},{id:'phone',label:'Điện thoại',type:'string',required:false}],
  crm_quote:[{id:'customer',label:'Khách',type:'string',required:true},{id:'amount',label:'Số tiền',type:'money',required:true}],
};
function handler(roles:string[],extra?:(call:Call)=>unknown) {
  return (call:Call):unknown=>{
    const custom=extra?.(call);if(custom!==undefined) return custom;
    switch(call.name) {
      case 'inspect_workspace': return workspace(roles);
      case 'describe_model': return {model:{id:call.arguments.model_id,label:String(call.arguments.model_id),fields:fieldsOf[String(call.arguments.model_id)]??[],metrics:[]}};
      case 'get_inbox': return {counts:{task:0,approval:0,notification:0,overdue:0},items:[],next_cursor:null}; // số đếm thanh bên/chuông khi mở Desk
      case 'search_records': {
        const id=String(call.arguments.model_id);
        return {items:id==='crm_contact'?contacts:stockRows[id]??[],next_cursor:null};
      }
      default: return undefined;
    }
  };
}
async function groupItems(page:Page, heading:string) {
  const section=page.locator('section.tier3-group').filter({has:page.getByRole('heading',{name:heading,exact:true})});
  await expect(section).toHaveCount(1);
  return section;
}
const groups=['Dòng chảy Hàng hóa','Thời gian & Dự án','Trải nghiệm & Thuê bao','Tài sản & Không gian'];

test('T3 staff: only enabled apps, grouped under the 4 headings, no CLI hint',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await mockPortal(page,handler(['staff']));
  await loginToTier3(page);
  const headings=page.locator('section.tier3-group > h3');
  await expect(headings).toHaveText(groups);
  await expect(await groupItems(page,groups[0])).toContainText('Kho hàng (WMS)');
  await expect(await groupItems(page,groups[1])).toContainText('Chấm công');
  const experience=await groupItems(page,groups[2]);
  await expect(experience).toContainText('CRM');
  await expect(experience).not.toContainText('Bảo hành');
  await expect(await groupItems(page,groups[3])).toContainText('Tài sản');
  await expect(page.getByText('Bảo hành')).toHaveCount(0);
  await expect(page.locator('.tier3-disabled')).toHaveCount(0);
  await expect(page.locator('.tier3-lifecycle')).toHaveCount(0);
  expect(await page.locator('body').innerText()).not.toContain('xt pack');
  // hộp duyệt rỗng (get_inbox thật trả 0): không có huy hiệu hay chấm đỏ
  await expect(page.getByTestId('nav-approvals')).toHaveCount(0);
  await expect(page.getByTestId('bell-dot')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('T3 admin: disabled app greyed with `xt pack enable` CLI hint, enabled app shows disable hint',async({page})=>{
  await mockPortal(page,handler(['admin']));
  await loginToTier3(page);
  const experience=await groupItems(page,groups[2]);
  const disabled=experience.locator('li.tier3-disabled');
  await expect(disabled).toHaveCount(1);
  await expect(disabled).toContainText('Bảo hành');
  await expect(disabled).toContainText('Đã tắt');
  await expect(disabled).toContainText('Không mở được khi đã tắt');
  await expect(disabled.getByRole('button',{name:'Mở Bảo hành'})).toHaveCount(0);
  expect(await disabled.locator('> span').first().evaluate(el=>getComputedStyle(el).opacity)).toBe('0.65');
  expect(await disabled.evaluate(el=>getComputedStyle(el).opacity)).toBe('1'); // gợi ý CLI của quản trị viên không bị làm mờ
  await disabled.locator('summary').click();
  await expect(disabled.locator('code')).toContainText('xt pack enable --tenant');
  await expect(disabled.locator('code')).toHaveText("xt pack enable --tenant 'Doanh nghiệp mẫu' bao-hanh --proposed-by <người đề xuất> --approved-by <người duyệt>");
  const crm=experience.locator('li').filter({hasText:'CRM'});
  await crm.locator('summary').click();
  await expect(crm.locator('code')).toContainText('xt pack disable --tenant');
  await expect(page.getByRole('button',{name:'Mở CRM',exact:true})).toBeEnabled();
});

test('generic app (CRM): list from search_records; record create sends exact create_record; request model adds submit_request',async({page})=>{
  let revision=0;
  const fixture=await mockPortal(page,handler(['staff'],call=>{
    if(call.name==='get_record') return {record:{ref:contacts[0].ref,title:'Nguyễn Văn A',model_id:'crm_contact',values:contacts[0].values}};
    if(call.name==='create_record') {
      revision=1;const id=call.arguments.model_id;
      return {record:{ref:`${id}:00000000-0000-4000-8000-0000000000d1`,state:'draft',revision,values:call.arguments.values}};
    }
    if(call.name==='submit_request') return {request:{ref:call.arguments.ref,state:'in_review',revision:2},route:[],process_instance:'process_instance:fixture'};
    return undefined;
  }));
  await loginToTier3(page);
  await page.getByRole('button',{name:'Mở CRM',exact:true}).click();
  await expect(page.getByRole('heading',{name:'CRM',exact:true})).toBeVisible();
  // list
  const list=page.getByRole('region',{name:'Bản ghi'});
  await expect(list.locator('.portal-doclist')).toContainText('Nguyễn Văn A');
  expect(fixture.calls.find(call=>call.name==='search_records'&&call.arguments.model_id==='crm_contact')?.arguments).toEqual({model_id:'crm_contact',limit:20});
  await list.getByRole('button',{name:'Mở Nguyễn Văn A',exact:true}).click();
  await expect(list.getByRole('article',{name:'Chi tiết bản ghi'})).toContainText('0900000001');
  // record model: create_record only
  const form=page.locator('.portal-request');
  await expect(form.getByLabel('Loại bản ghi')).toHaveValue('crm_contact');
  await expect(form.getByLabel('Họ tên *')).toBeVisible();
  await form.getByLabel('Họ tên *').fill('Trần Thị B');
  await form.getByLabel('Điện thoại').fill('0901234567');
  await form.getByRole('button',{name:'Tạo',exact:true}).click();
  await expect(form.getByTestId('request-done')).toContainText('Đã tạo Khách hàng');
  const creates=()=>fixture.calls.filter(call=>call.name==='create_record');
  expect(creates()).toHaveLength(1);
  expect(creates()[0].arguments).toEqual({model_id:'crm_contact',values:{name:'Trần Thị B',phone:'0901234567'},idempotency_key:expect.any(String)});
  expect(fixture.calls.some(call=>call.name==='submit_request')).toBe(false);
  // request model: create_record, then submit_request with the draft's ref + revision
  await form.getByLabel('Loại bản ghi').selectOption('crm_quote');
  await expect(form.getByLabel('Khách *')).toBeVisible();
  await form.getByLabel('Khách *').fill('Công ty X');
  await form.getByLabel('Số tiền *').fill('1500000.50');
  await form.getByRole('button',{name:'Tạo và gửi duyệt',exact:true}).click();
  await expect(form.getByTestId('request-done')).toContainText('Đã gửi duyệt Báo giá');
  expect(creates()).toHaveLength(2);
  expect(creates()[1].arguments).toEqual({model_id:'crm_quote',values:{customer:'Công ty X',amount:'1500000.50'},idempotency_key:expect.any(String)});
  const submits=fixture.calls.filter(call=>call.name==='submit_request');
  expect(submits).toHaveLength(1);
  expect(submits[0].arguments).toEqual({ref:'crm_quote:00000000-0000-4000-8000-0000000000d1',expected_revision:1,idempotency_key:expect.any(String)});
  expect(fixture.calls.indexOf(creates()[1])).toBeLessThan(fixture.calls.indexOf(submits[0]));
  expect(submits[0].arguments.idempotency_key).not.toBe(creates()[1].arguments.idempotency_key);
});

test('WareTrack module: scoped read-only bridge, business-mode read via connect(window), writes and Studio proxy refused, logout invalidates',async({page})=>{
  test.setTimeout(120_000); // iframe 3D nặng: tránh vượt ngân sách 45 s khi máy bận
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const fixture=await mockPortal(page,handler(['staff']));
  await loginToTier3(page);
  await page.getByRole('button',{name:'Mở Kho hàng (WMS)',exact:true}).click();
  await expect(page.getByRole('group',{name:'Kiểu hiển thị'}).getByRole('button')).toHaveText(['Sơ đồ 3D','Danh sách']);
  const view3d=page.getByRole('button',{name:'Sơ đồ 3D',exact:true});
  await view3d.click();
  await expect(view3d).toHaveAttribute('aria-pressed','true');
  // Kho có tab con; tab "Sơ đồ 3D" (role=tab) khác nút kiểu hiển thị "Sơ đồ 3D" (role=button) ở trên
  const mapTab=page.getByRole('tab',{name:'Sơ đồ 3D',exact:true});
  await mapTab.click();
  await expect(mapTab).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('button',{name:'Sơ đồ 3D',exact:true})).toHaveCount(1);
  const iframe=page.locator('iframe[title="xT WareTrack"]');
  await expect(iframe).toBeVisible();
  await expect(iframe).toHaveAttribute('data-xt-scope','waretrack');
  await expect(iframe).toHaveAttribute('src','/waretrack/?integrated=1');
  const wt=page.frameLocator('iframe[title="xT WareTrack"]');
  // business mode reads through connect(window).callTool('search_records', ...) and renders the read-only list
  await expect(wt.getByRole('button',{name:'Mô phỏng',exact:true})).toHaveAttribute('aria-pressed','true',{timeout:30_000}); // bundle 3D ~880 KB: lần tải đầu chậm khi máy bận
  await iframe.scrollIntoViewIfNeeded();
  const beforeBusiness=fixture.calls.length; // portal Kho tự đọc cùng danh mục/tồn kho: chỉ tính lời gọi phát sinh sau khi bật chế độ nghiệp vụ
  await wt.getByRole('button',{name:'Dữ liệu nghiệp vụ',exact:true}).click();
  await expect(wt.getByRole('heading',{name:'Dữ liệu nghiệp vụ (chỉ đọc)'})).toBeVisible();
  const table=(title:string)=>wt.locator(`.bz-table:has(h3:text-is("${title}"))`);
  await expect(table('Kho (1)')).toContainText('Kho Hà Nội');
  await expect(table('Tồn kho (1)')).toContainText('2,5');
  await expect(table('Phiếu nhập/xuất (1)')).toContainText('PN-9');
  const read=(model_id:string,fields:string[])=>({name:'search_records',arguments:{model_id,limit:100,fields}});
  const businessCalls=fixture.calls.slice(beforeBusiness);
  expect(businessCalls).toContainEqual(read('warehouse',['code','name']));
  expect(businessCalls).toContainEqual(read('stock_balance',['item','location','quantity_milli']));
  expect(businessCalls).toContainEqual(read('stock_request',['code','kind','warehouse','lines','note']));
  // the frame's own bridge: scoped, frozen, no Studio proxy, writes rejected
  const frame=page.frame({url:/\/waretrack\/\?integrated=1/})!;
  const probe=await frame.evaluate(async()=>{
    const bridge=window.parent.xtPortal!.connect(window) as unknown as Record<string,unknown>&{callTool:(name:string,args:Record<string,unknown>)=>Promise<unknown>};
    const outcome=(promise:Promise<unknown>)=>promise.then(()=>'resolved',(error:Error)=>error.message);
    const host=window.parent.xtPortal as unknown as Record<string,unknown>;
    (window.parent as unknown as Record<string,unknown>).__frameBridge=bridge;
    return {
      scope:bridge.scope, keys:Object.keys(bridge).sort(), frozen:Object.isFrozen(bridge), hasRequest:'request' in bridge, requestType:typeof bridge.request,
      hostKeys:Object.keys(host).sort(), hostLeaks:['request','callTool'].filter(key=>key in host),
      createRecord:await outcome(bridge.callTool('create_record',{model_id:'stock_request',values:{},idempotency_key:'x'})),
      submitRequest:await outcome(bridge.callTool('submit_request',{ref:'stock_request:1',expected_revision:1})),
      decideRequest:await outcome(bridge.callTool('decide_request',{ref:'stock_request:1'})),
      nonStockRead:await outcome(bridge.callTool('search_records',{model_id:'crm_contact'})),
    };
  });
  expect(probe).toMatchObject({scope:'waretrack',keys:['callTool','scope'],frozen:true,hasRequest:false,requestType:'undefined',hostLeaks:[]});
  expect(probe.hostKeys).toEqual(['connect','invalidate']);
  expect(probe.createRecord).toBe('Công cụ không được phép');
  expect(probe.submitRequest).toBe('Công cụ không được phép');
  expect(probe.decideRequest).toBe('Công cụ không được phép');
  expect(probe.nonStockRead).toBe('Chỉ hỗ trợ dữ liệu kho');
  // refused client-side: nothing reached the server, and no Studio traffic was issued
  expect(fixture.calls.filter(call=>['create_record','submit_request','decide_request'].includes(call.name))).toEqual([]);
  expect(fixture.calls.some(call=>call.arguments.model_id==='crm_contact')).toBe(false);
  // logout invalidates every bridge: the old frame window no longer connects, the issued bridge is dead, the frame is gone
  await page.evaluate(()=>{
    const w=window as unknown as Record<string,unknown>;
    w.__host=window.xtPortal;w.__win=document.querySelector<HTMLIFrameElement>('iframe[data-xt-scope="waretrack"]')!.contentWindow;
  });
  expect(await page.evaluate(()=>(window as unknown as {__host:{connect(win:Window):unknown}}).__host.connect((window as unknown as {__win:Window}).__win)!==null)).toBe(true);
  await page.getByRole('navigation',{name:'Không gian xT'}).getByRole('button',{name:'Desk',exact:true}).click();
  await page.getByRole('button',{name:'Đăng xuất',exact:true}).click();
  await expect(page.locator('#login')).toBeVisible();
  await expect(page.locator('iframe[data-xt-scope]')).toHaveCount(0);
  const after=await page.evaluate(async()=>{
    const w=window as unknown as {__host:{connect(win:Window):unknown};__win:Window;__frameBridge:{callTool(name:string,args:Record<string,unknown>):Promise<unknown>}};
    const dead=await w.__frameBridge.callTool('search_records',{model_id:'warehouse',limit:1}).then(()=>'resolved',(error:Error)=>error.message);
    return {portal:!!window.xtPortal,connect:w.__host.connect(w.__win),connectNull:w.__host.connect(null as unknown as Window),dead};
  });
  expect(after).toEqual({portal:false,connect:null,connectNull:null,dead:'Công cụ không được phép'});
  expect(errors).toEqual([]);
});
