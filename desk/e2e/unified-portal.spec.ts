// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Explicit browser fixtures: real built Desk/Studio code, mocked OIDC/MCP/Studio services.
import { createHash } from 'node:crypto';
import { expect, test, type Page } from '@playwright/test';
const tools=['inspect_workspace','describe_model','find_people','search_records','aggregate_records','get_record','get_inbox','get_history','create_record','create_task','submit_request','transition_record','decide_request'];
const warehouse='00000000-0000-4000-8000-000000000001', item='00000000-0000-4000-8000-000000000002', location='00000000-0000-4000-8000-000000000003';
const requestRef='stock_request:00000000-0000-4000-8000-000000000004';
type Call={name:string;arguments:Record<string,unknown>};
async function fixtures(page:Page) {
  let nonce='',challenge='',client='',issuer='',requestState='in_review',revision=2;
  const calls:Call[]=[],studioRequests:string[]=[],authorization:string[]=[];
  const jwt=(claims:unknown)=>`${Buffer.from('{"alg":"none"}').toString('base64url')}.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.`;
  await page.route('**/.well-known/openid-configuration',async route=>{
    issuer=route.request().url().replace('/.well-known/openid-configuration','');
    await route.fulfill({json:{issuer,authorization_endpoint:`${issuer}/protocol/openid-connect/auth`,token_endpoint:`${issuer}/protocol/openid-connect/token`}});
  });
  await page.route('**/protocol/openid-connect/auth?**',async route=>{
    const query=new URL(route.request().url()).searchParams;
    expect(query.get('code_challenge_method')).toBe('S256');
    nonce=query.get('nonce')!;challenge=query.get('code_challenge')!;client=query.get('client_id')!;
    const callback=new URL(query.get('redirect_uri')!);callback.searchParams.set('code','explicit-fixture-code');callback.searchParams.set('state',query.get('state')!);
    await route.fulfill({status:302,headers:{location:callback.href},body:''});
  });
  await page.route('**/protocol/openid-connect/token',async route=>{
    const fields=new URLSearchParams(route.request().postData()!);
    expect(fields.get('grant_type')).toBe('authorization_code');
    expect(createHash('sha256').update(fields.get('code_verifier')!).digest('base64url')).toBe(challenge);
    expect(route.request().headers().dpop).toBeTruthy();
    await route.fulfill({json:{access_token:'fixture-access-token',token_type:'Bearer',expires_in:3600,id_token:jwt({iss:issuer,aud:client,nonce,sub:'fixture-lan'})}});
  });
  await page.route('**/mcp',async route=>{
    if(route.request().method()!=='POST') {await route.fulfill({status:405,body:''});return;}
    expect(route.request().headers().authorization).toBe('Bearer fixture-access-token');
    const rpc=route.request().postDataJSON();
    if(!('id' in rpc)) {await route.fulfill({status:202,body:''});return;}
    let result:unknown;
    const ok=(data:unknown)=>({content:[{type:'text',text:JSON.stringify({ok:true,data})}]});
    if(rpc.method==='initialize') result={protocolVersion:'2025-03-26',capabilities:{tools:{}},serverInfo:{name:'explicit-browser-fixture',version:'1'}};
    else if(rpc.method==='tools/list') result={tools:tools.map(name=>({name,inputSchema:{type:'object',properties:{},additionalProperties:true}}))};
    else if(rpc.method==='tools/call') {
      const call=rpc.params as Call;calls.push(call);
      if(call.name==='inspect_workspace') result=ok({me:{ref:'person:lan',name:'Lan',kind:'human',autonomy_ceiling:'L1'},tenant:{name:'Doanh nghiệp mẫu'},models:[{id:'advance_request',label:'Tạm ứng',primitive:'request'},{id:'stock_request',label:'Phiếu kho',primitive:'request'},{id:'stock_item',label:'Hàng hóa',primitive:'record'}],processes:[{id:'stock_approval',label:'Duyệt phiếu kho'}]});
      else if(call.name==='describe_model') result=ok({model:{id:call.arguments.model_id,label:String(call.arguments.model_id),fields:[],metrics:[]}});
      else if(call.name==='find_people') result=ok({relation:'search',summary:'Nhân sự theo quyền',people:[{ref:'person:lan',name:'Lan',title:'Kế toán',email:'lan@example.test',manager:{name:'Mai',ref:'person:mai'}}],next_cursor:null});
      else if(call.name==='aggregate_records') {expect(call.arguments.metrics).toEqual(['count']);result=ok({model_id:call.arguments.model_id,rows:[],totals:{count:2},summary:'2 bản ghi theo quyền của bạn.',next_cursor:null});}
      else if(call.name==='get_inbox') result=ok({counts:{approval:1},items:[{ref:requestRef,kind:'approval',title:'Phiếu PN-1',state:requestState,sla_status:'on_track'}],next_cursor:null});
      else if(call.name==='get_record') result=ok({record:{ref:requestRef,title:'Phiếu PN-1',model_id:'stock_request',state:requestState,revision,values:{code:'PN-1'}}});
      else if(call.name==='decide_request') result={isError:true,content:[{type:'text',text:JSON.stringify({ok:false,error:{code:'confirmation_required',message:'Cần người phụ trách xác nhận',details:{confirmation_id:'browser-fixture',confirm_url:'https://xt.freepro.online/desk/confirm/browser-fixture',plan_summary:'Duyệt PN-1'}}})}]};
      else if(call.name==='search_records') {
        const rows=call.arguments.model_id==='warehouse'?[{ref:`warehouse:${warehouse}`,title:'HN',values:{code:'HN',name:'Kho Hà Nội'}}]:call.arguments.model_id==='stock_item'?[{ref:`stock_item:${item}`,title:'VT-1',values:{sku:'VT-1',name:'Vật tư A',unit:'pcs'}}]:call.arguments.model_id==='warehouse_location'?[{ref:`warehouse_location:${location}`,title:'A-1',values:{code:'A-1',name:'Kệ A',warehouse}}]:[];
        result=ok({items:rows,next_cursor:null});
      } else if(call.name==='create_record') {requestState='draft';revision=1;result=ok({record:{ref:requestRef,state:requestState,values:{code:'PN-1'}}});}
      else if(call.name==='submit_request') {expect(call.arguments.expected_revision).toBe(1);requestState='in_review';revision=2;result=ok({request:{ref:requestRef,state:requestState,revision},route:[],process_instance:'process_instance:fixture'});}
      else throw new Error(`Unexpected fixture tool ${call.name}`);
    } else throw new Error(`Unexpected JSON-RPC ${rpc.method}`);
    await route.fulfill({json:{jsonrpc:'2.0',id:rpc.id,result}});
  });
  const published={inputs:[],model_id:'fixture-model',system_prompt:'Fixture',knowledge_ids:[],tools:[],max_steps:2,graph:{nodes:[],edges:[]}};
  await page.route('**/studio-api/**',async route=>{
    const path=new URL(route.request().url()).pathname;studioRequests.push(path);authorization.push(route.request().headers().authorization);
    if(path==='/studio-api/session') await route.fulfill({json:{integrated:true,needs_setup:false,user:{id:'fixture-user',email:'lan@example.test',role:'viewer',workspace_name:'xT Fixture'}}});
    else if(path==='/studio-api/apps') await route.fulfill({json:[{id:'fixture-app',name:'Trợ lý kho mẫu',description:'Ứng dụng fixture',kind:'chat',config:published,published,version:1,updated_at:'2026-10-17T00:00:00Z'}]});
    else if(path==='/studio-api/apps/fixture-app/conversations') await route.fulfill({json:[]});
    else if(path==='/studio-api/apps/fixture-app/run') {
      expect(route.request().postDataJSON()).toMatchObject({input:'Xin chào Studio',mode:'published',stream:true});
      const events=[{type:'delta',text:'Chào '},{type:'delta',text:'từ Studio.'},{type:'result',result:{output:'Chào từ Studio.',citations:[],trace:[],conversation_id:'fixture-conversation'}}];
      await route.fulfill({contentType:'application/x-ndjson',body:events.map(event=>JSON.stringify(event)).join('\n')+'\n'});
    } else throw new Error(`Unexpected Studio path ${path}`);
  });
  return {calls,studioRequests,authorization};
}
test('unified built portal: H/P/D/Kho, actual Studio iframe and Response bridge, logout',async({page},testInfo)=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const fixture=await fixtures(page);
  await page.goto('/');await page.locator('#login').click();
  await expect(page.locator('#box')).toBeEnabled();
  const nav=page.getByRole('navigation',{name:'Không gian xT'});
  await nav.getByRole('button',{name:'H',exact:true}).click();
  await expect(page.locator('#box')).toBeHidden();
  await expect(page.locator('.portal')).toContainText('lan@example.test');
  await expect(page.locator('.portal')).toContainText('Mai');
  await nav.getByRole('button',{name:'D',exact:true}).click();
  await expect(page.getByLabel('Loại dữ liệu',{exact:true})).toContainText('Tạm ứng');
  await page.getByRole('button',{name:'Xem báo cáo',exact:true}).click();
  await expect(page.locator('.portal')).toContainText('2 bản ghi theo quyền của bạn.');
  await page.getByRole('button',{name:'Tìm',exact:true}).click();
  await expect(page.locator('.portal')).toContainText('Không có kết quả.');
  await nav.getByRole('button',{name:'P',exact:true}).click();
  await page.getByRole('button',{name:'Mở Phiếu PN-1',exact:true}).click();
  await page.getByRole('button',{name:'Duyệt',exact:true}).click();
  await expect(page.getByTestId('confirm-card')).toContainText('Duyệt PN-1');
  await expect(page.getByTestId('confirm-card').getByRole('link')).toHaveAttribute('href','https://xt.freepro.online/desk/confirm/browser-fixture');
  await nav.getByRole('button',{name:'Kho',exact:true}).click();
  await page.getByLabel('Mã phiếu',{exact:true}).fill('PN-1');
  await page.getByLabel('Kho',{exact:true}).selectOption(warehouse);
  await page.getByLabel('Hàng hóa',{exact:true}).selectOption(item);
  await page.getByLabel('Vị trí',{exact:true}).selectOption(location);
  await page.getByLabel('Số lượng',{exact:true}).fill('1.001');
  await page.getByRole('button',{name:'Tạo nháp',exact:true}).click();
  await expect(page.getByRole('button',{name:'Gửi phiếu đi duyệt',exact:true})).toBeEnabled();
  await page.getByRole('button',{name:'Gửi phiếu đi duyệt',exact:true}).click();
  await expect(page.locator('.portal-results')).toContainText('in_review');
  const created=fixture.calls.find(call=>call.name==='create_record');
  expect(created?.arguments).toMatchObject({model_id:'stock_request',idempotency_key:expect.any(String),values:{warehouse,lines:[{item,location,quantity_milli:1001}]}});
  await nav.getByRole('button',{name:'I',exact:true}).click();
  const studio=page.frameLocator('iframe[title="xT Studio"]');
  await expect(studio.getByRole('heading',{name:'Trợ lý kho mẫu'})).toBeVisible();
  await expect(studio.locator('.local-badge')).toHaveText('Studio tích hợp xT · chia sẻ công khai và API key đã tắt');
  await studio.getByRole('button',{name:/Trợ lý kho mẫu/}).click();
  await studio.getByLabel('Nội dung',{exact:true}).fill('Xin chào Studio');
  await studio.getByRole('button',{name:'Chạy ứng dụng',exact:true}).click();
  await expect(studio.getByText('Chào từ Studio.',{exact:true})).toBeVisible();
  expect(fixture.studioRequests).toContain('/studio-api/apps/fixture-app/run');
  expect(fixture.authorization.every(header=>header==='Bearer fixture-access-token')).toBe(true);
  await page.screenshot({path:testInfo.outputPath('unified-studio.png'),fullPage:true});
  await nav.getByRole('button',{name:'Desk',exact:true}).click();
  await expect(page.locator('#box')).toBeVisible();
  await expect(page.locator('#box')).toBeEnabled();
  await page.getByRole('button',{name:'Đăng xuất',exact:true}).click();
  await expect(page.locator('#login')).toBeVisible();
  expect(await page.evaluate(()=>!!window.xtPortal)).toBe(false);
  await expect(page.locator('iframe[title="xT Studio"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});
