// SPDX-License-Identifier: Apache-2.0
// SPDX-FileCopyrightText: 2026 xT contributors
import { readFileSync, writeFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
const digest=(value)=>createHash('sha256').update(value).digest('hex');
const files=['package.json','package-lock.json','tsconfig.json'];
function visit(dir){for(const item of readdirSync(dir,{withFileTypes:true})){const path=join(dir,item.name);if(item.isDirectory())visit(path);else files.push(path);}}
for(const dir of ['src','config','tests'])visit(dir);
const hashes=files.sort().map((path)=>({path,sha256:digest(readFileSync(path))}));
const source={sha256:digest(JSON.stringify(hashes)),files:hashes};
const live=JSON.parse(readFileSync('docs/evidence/live-smoke.json','utf8'));
assert.equal(live.passed,true);assert.deepEqual(source,live.source,'Live evidence does not match current source snapshot');
const unitLog=readFileSync('docs/evidence/tests.log','utf8'),buildLog=readFileSync('docs/evidence/build.log','utf8');
const passed=Number(unitLog.match(/ℹ pass (\d+)/)?.[1]);assert.equal(passed,44);assert.match(unitLog,/ℹ fail 0/);assert.ok(!buildLog.includes('error TS'));
const browserFiles=['rea-chat-final.json','rea-typed-final.json','rea-mcp-final-repeat.json'];
const browsers=browserFiles.map((file)=>{
 const p=JSON.parse(readFileSync('docs/evidence/'+file,'utf8')),n=p.normalized_result;
 assert.equal(n.completeness.status,'complete');assert.equal(n.events.retained,0);assert.ok(n.steps.every((s)=>!s.error));
 return {case:file,evidence_id:p.evidence_id,status:'pass',page_errors:0,cleanup:n.browser.cleanup,sha256:digest(readFileSync('docs/evidence/'+file))};
});
const journeys=JSON.parse(readFileSync('docs/evidence/browser-journeys.json','utf8'));assert.equal(journeys.passed,true);
const runtimeFiles=hashes.filter((f)=>!f.path.startsWith('tests/'));
const session=await (await fetch('http://127.0.0.1:4317/api/session')).json();
const summary={
 schema:'xt-studio.reconstruction-receipt.v2',recorded_at:new Date().toISOString(),project_root:resolve('.'),
 status:'working feature prototype; full Dify reconstruction incomplete',source,
 runtime_source_sha256:digest(JSON.stringify(runtimeFiles)),
 artifacts:['dist/app.js','dist/app.css','dist/index.html'].map((path)=>({path,sha256:digest(readFileSync(path))})),
 policy:{authorization:'User requested continued local implementation of all five Dify product groups using REA; root AGENTS authorizes swarm for multi-file changes.',
 allowed_scope:'Independent local project, local QA servers/workspaces, public documentation, installed Ollama; main agent is sole writer; three read-only Sol 6 low agents reviewed.',
 decision:'proceed with local development and checks',release_authorized:false,existing_xt_and_rea_repos_modified:false},
 validation:{
  build:{command:'npm run build',status:'pass',log_sha256:digest(buildLog)},
  integration:{command:'npm test',passed,failed:0,source_sha256:source.sha256,log_sha256:digest(unitLog)},
  live_models:{command:'LIVE_PORT=4320 node tests/live-smoke.ts',status:'pass',source_sha256:source.sha256,
   models:['qwen3.5:4b','bge-m3:latest'],cases:['vector indexing/retrieval','RAG answer/citations persistence','chat streaming','shared app isolation','three-step LLM workflow','calculator agent','Qwen agent calling MCP fixture','published typed workflow','portable MCP app export/import'],
   mcp_target:'Local read-only SDK fixture, not an enterprise deployment',evidence:'live-smoke.json',sha256:digest(readFileSync('docs/evidence/live-smoke.json'))},
  browser:browsers,
  supporting_browser:{provider:'local Playwright',script:'browser-journeys.mjs',script_sha256:digest(readFileSync('docs/evidence/browser-journeys.mjs')),evidence:'browser-journeys.json',sha256:digest(readFileSync('docs/evidence/browser-journeys.json')),cases:journeys.cases,page_errors:journeys.page_errors,
   binding:'Runtime implementation unchanged; only the stateful MCP test was added and its TypeScript annotations corrected after capture.'},
 },
 tooling_limitations:[
  'Current Codex registry exposes no REA MCP tools; local REA CLI used for this round, without claiming MCP was reconnected.',
  'Initial final MCP browser scenario had a non-unique .execution summary locator; corrected .execution > summary replay passed. Product execution had completed in the first capture.',
  'Dify reference evidence is anonymous sign-in only; no authenticated parity comparison.'
 ],
 remaining:['Authenticated Dify comparison and full parity','Durable indexing jobs, hybrid/rerank and large vector index','Advanced workflow loop/iteration, code sandbox, scheduling','Multi-tenant UI, membership/RBAC/SSO, quotas and production load tests','Paid provider live checks; plugin marketplace and MCP OAuth/stdio; integration with existing xT MCP'],
 runtime:{url:'http://127.0.0.1:4317',binding:'loopback',needs_setup:session.needs_setup,qa_url:'http://127.0.0.1:4318',database_backup:'Private SQLite backup and credential key copied together before server restart'},
 release:{committed:false,pushed:false,deployed:false},
};
writeFileSync('docs/evidence/summary.json',JSON.stringify(summary,null,2)+'\n');
console.log(JSON.stringify({source_sha256:source.sha256,unit_passed:passed,live:'pass',rea_cases:browsers.length,browser_journeys:journeys.cases.length,main_needs_setup:session.needs_setup}));
