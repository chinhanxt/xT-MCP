import { createRequire } from 'node:module';
import { readFile, writeFile } from 'node:fs/promises';
import assert from 'node:assert/strict';
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || '/home/chinhan/projects/reclaw/repos/rea/node_modules/playwright-core');
const login = JSON.parse(await readFile(process.argv[2], 'utf8'));
const browser = await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
const context = await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true});
await context.addCookies([{name:'studio_session',value:login.cookie.split('=')[1],url:login.origin,httpOnly:true,sameSite:'Strict'}]);
const page = await context.newPage();
const errors=[];
page.on('pageerror',(error) => errors.push(error.message));
const evidence={observed_at:new Date().toISOString(),provider:'local Playwright supporting QA; REA captures retained separately',cases:[],page_errors:errors};
async function api(path,body,method=body === undefined ? 'GET' : 'POST') {
 const response=await context.request.fetch(login.origin+'/api'+path,{method,data:body});
 assert.ok(response.ok(),await response.text());return response.json();
}
function pdf(text) {
 const stream=`BT /F1 12 Tf 50 750 Td (${text}) Tj ET`,parts=['%PDF-1.4\n'],offsets=[0];
 const objects=['<< /Type /Catalog /Pages 2 0 R >>','<< /Type /Pages /Kids [3 0 R] /Count 1 >>','<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>','<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',`<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`];
 for(let i=0;i<objects.length;i++){offsets.push(Buffer.byteLength(parts.join('')));parts.push(`${i+1} 0 obj\n${objects[i]}\nendobj\n`);}
 const xref=Buffer.byteLength(parts.join(''));parts.push(`xref\n0 6\n0000000000 65535 f \n`+offsets.slice(1).map((n)=>`${String(n).padStart(10,'0')} 00000 n \n`).join('')+`trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`);return Buffer.from(parts.join(''));
}
try {
 await page.goto(login.origin);
 await page.locator('nav button').filter({hasText:'Kiến thức'}).click();
 await page.getByRole('button',{name:'+ Tạo kho kiến thức',exact:true}).click();
 await page.locator('dialog input[name=name]').fill('Kho PDF QA');
 await page.locator('dialog button.primary').click();
 await page.locator('.bare-card').filter({hasText:'Kho PDF QA'}).click();
 await page.getByRole('button',{name:'+ Thêm tài liệu',exact:true}).click();
 await page.locator('dialog input[type=file]').setInputFiles({name:'policy-qa.pdf',mimeType:'application/pdf',buffer:pdf('Company policy limit 7500000 VND')});
 await page.waitForFunction(()=>document.querySelector('dialog textarea[name=content]')?.value.includes('7500000'));
 await page.locator('dialog button.primary').click();
 await page.locator('.document button.text-btn').filter({hasText:'policy-qa.pdf'}).click();
 await page.locator('.chunk-preview').filter({hasText:'7500000'}).waitFor();
 const updated='Company policy updated limit 8500000 VND';
 await page.locator('dialog textarea').fill(updated);
 await page.locator('dialog button.primary').click();
 await page.locator('.chunk-preview').filter({hasText:'8500000'}).waitFor();
 const k=(await api('/knowledge')).find((k)=>k.name==='Kho PDF QA');
 const hits=await api('/knowledge/'+k.id+'/search',{query:'8500000'});assert.ok(hits.some((h)=>h.content===updated));
 await page.screenshot({path:'docs/evidence/browser-document-final.png'});
 evidence.cases.push({name:'PDF upload/extraction preview, indexing, chunk preview, edit/reindex and search',status:'pass'});
 await page.getByRole('button',{name:'Đóng',exact:true}).click();
 await page.locator('nav button').filter({hasText:'Ứng dụng'}).click();
 const chat=(await api('/apps')).find((a)=>a.name==='Trợ lý công ty');
 await page.locator('.app-card').filter({hasText:'Trợ lý công ty'}).click();
 const system=page.locator('.config-panel textarea').first();
 await system.fill(chat.config.system_prompt+'\nKiểm thử phiên bản 2.');
 await page.getByRole('button',{name:'Publish ↗',exact:true}).click();
 await page.locator('.toast').filter({hasText:'Đã publish phiên bản 2'}).waitFor();
 await page.getByRole('button',{name:'Phiên bản & xuất',exact:true}).click();
 await page.locator('.document').filter({hasText:'Phiên bản 2'}).waitFor();
 page.once('dialog',(dialog)=>dialog.accept());
 await page.locator('.document').filter({hasText:'Phiên bản 1'}).getByRole('button',{name:'Khôi phục vào bản nháp'}).click();
 await page.locator('.toast').filter({hasText:'Đã khôi phục vào bản nháp'}).waitFor();
 const restored=await api('/apps/'+chat.id);assert.equal(restored.config.system_prompt,chat.config.system_prompt);assert.equal(restored.version,2);assert.notEqual(restored.published.system_prompt,restored.config.system_prompt);
 await page.screenshot({path:'docs/evidence/browser-versions-final.png'});
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Xuất cấu hình ↓',exact:true}).click();
 const download=await downloaded;const manifest=JSON.parse(await readFile(await download.path(),'utf8'));
 assert.equal(manifest.format,'xt-studio.app');assert.ok(!JSON.stringify(manifest).includes('secret'));
 evidence.cases.push({name:'UI publish v2, restore v1 to draft preserving published v2, JSON download',status:'pass'});
 manifest.app.name='Bản nhập qua giao diện';
 await page.locator('nav button').filter({hasText:'Ứng dụng'}).click();
 await page.getByRole('button',{name:'Nhập cấu hình ↑',exact:true}).click();
 await page.locator('dialog input[type=file]').setInputFiles({name:'app.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(manifest))});
 await page.locator('dialog h3').filter({hasText:'Bản nhập qua giao diện'}).waitFor();
 await page.locator('dialog button.primary').click();
 await page.locator('.editor-header').filter({hasText:'Bản nhập qua giao diện'}).waitFor();
 const imported=(await api('/apps')).find((a)=>a.name==='Bản nhập qua giao diện');assert.ok(imported);assert.equal(imported.published,null);assert.equal(imported.version,0);
 await page.screenshot({path:'docs/evidence/browser-import-final.png'});
 evidence.cases.push({name:'UI import JSON with model/knowledge bindings into fresh draft',status:'pass'});
 assert.deepEqual(errors,[]);evidence.passed=true;
} catch(error) {evidence.error=error.stack;evidence.passed=false;throw error;}
finally {await browser.close();evidence.cleanup='owned browser closed';await writeFile('docs/evidence/browser-journeys.json',JSON.stringify(evidence,null,2));}
console.log(JSON.stringify({passed:true,cases:evidence.cases,page_errors:errors}));
