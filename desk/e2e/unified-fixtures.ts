// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
// Shared explicit fixtures for the unified-portal browser specs: mocked OIDC (S256 + DPoP) and an MCP endpoint whose tool results come from the spec.
import { createHash } from 'node:crypto';
import { expect, type Page } from '@playwright/test';
export const tools=['inspect_workspace','describe_model','find_people','search_records','aggregate_records','get_record','get_inbox','get_history','create_record','create_task','submit_request','transition_record','decide_request'];
export type Call={name:string;arguments:Record<string,unknown>};
/** Handler returns the tool `data`, or `{raw}` for a verbatim MCP result. */
export type Handler=(call:Call)=>unknown;
export async function mockPortal(page:Page, handle:Handler) {
  let nonce='',challenge='',client='',issuer='';
  const calls:Call[]=[];
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
    if(rpc.method==='initialize') result={protocolVersion:'2025-03-26',capabilities:{tools:{}},serverInfo:{name:'explicit-browser-fixture',version:'1'}};
    else if(rpc.method==='tools/list') result={tools:tools.map(name=>({name,inputSchema:{type:'object',properties:{},additionalProperties:true}}))};
    else if(rpc.method==='tools/call') {
      const call=rpc.params as Call;calls.push(call);
      const data=handle(call) as {raw?:unknown}|undefined;
      if(data===undefined) throw new Error(`Unexpected fixture tool ${call.name}`);
      result=data&&typeof data==='object'&&'raw' in data?data.raw:{content:[{type:'text',text:JSON.stringify({ok:true,data})}]};
    } else throw new Error(`Unexpected JSON-RPC ${rpc.method}`);
    await route.fulfill({json:{jsonrpc:'2.0',id:rpc.id,result}});
  });
  return {calls};
}
export async function loginToTier3(page:Page) {
  await page.goto('/');await page.locator('#login').click();
  await expect(page.locator('#box')).toBeEnabled();
  await page.getByRole('navigation',{name:'Không gian xT'}).getByRole('button',{name:'T3',exact:true}).click();
  await expect(page.getByRole('region',{name:'Nền tảng Kinh doanh số'})).toBeVisible();
}
