// Synthetic owner API fixtures only. No provider, sandbox, publisher, or live service is used.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PB_PLAYWRIGHT_MODULE || 'playwright');
const source = path.dirname(fileURLToPath(import.meta.url));
const credential = 'NATIVE_TRACE_OWNER_FIXTURE_ONLY', reviewToken = 'NATIVE_TRACE_REVIEW_TOKEN_MUST_STAY_PRIVATE';
const attack = '<img src=x onerror="window.TRACE_XSS=true">';
const errors = [], external = [], requests = [], eventsAfter = [];
const fileMap = new Map(), conversations = new Map(), runs = new Map(), permissionHistory = [];
const projects = ['joeos','memory'].map(key => ({key,label:key.toUpperCase()+' · trace fixture',repo:'fixture/'+key,mode:'execute',configured:true,connected:true,publication:true}));
const settings = {general:{default_view:'ai',refresh_seconds:60,show_completed:true},notifications:{},ai:{default_agent:'executive',default_model:'fixture'},security:{},filters:{saved_views:[]}};
let html = (await Promise.all((await fs.readdir(path.join(source,'index'))).filter(name => name.endsWith('.part')).sort().map(name => fs.readFile(path.join(source,'index',name),'utf8')))).join('');
for (const name of ['home.js','home-inspector.js','health-runtime.js','ai-connections.js','codex-workspace.js']) {
  fileMap.set('/'+name,{type:'text/javascript',body:await fs.readFile(path.join(source,name))});
  const tag = `<script src="/${name}"></script>`;
  if (!html.includes(tag)) html = html.replace('</body>',tag+'</body>');
}
fileMap.set('/',{type:'text/html',body:html});
function append(run,type,data) { run.events.push({seq:run.events.length+1,type,data,ts:Date.now()/1000}); }
function snapshot(run,after=0) {
  return {events:run.events.filter(event=>event.seq>after).slice(0,200),run:{id:run.id,status:run.status,model:'gpt-6-astra',effort:'ultra'},
    permissions:run.permissions,agents:run.agents,artifacts:[],usage:run.usage || [],telemetry_version:1,server_time:Date.now()/1000};
}
const server = http.createServer(async (req,res) => {
  try {
    const url = new URL(req.url,'http://fixture.local');
    if (fileMap.has(url.pathname)) { const file=fileMap.get(url.pathname);res.writeHead(200,{'Content-Type':file.type});res.end(file.body);return; }
    let raw='';for await (const chunk of req) raw+=chunk;
    const body=raw?JSON.parse(raw):{}, owner=req.headers['x-access-key']===credential;
    const send=(data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
    if (url.pathname==='/api/session') return send({ok:owner,level:owner?4:0,subject:owner?'owner':'',role:owner?'owner':'public',name:owner?'Trace fixture owner':'Public'});
    if (url.pathname.startsWith('/api/codex-workspace')) {
      if (!owner) return send({error:'Owner required'},403);
      if (req.method==='POST') requests.push({path:url.pathname,body});
      if (url.pathname==='/api/codex-workspace') return send({configured:true,connected:true,model:'gpt-6-astra',effort:'ultra',projects,execution_permissions:{runner:'codex',capable:true,server_time:Date.now()/1000,requests:[...runs.values()].flatMap(r=>r.permissions.map(p=>({...p,can_decide:true,run_id:r.id,conversation_id:'trace-conversation',conversation_title:'Review fixture',project_key:'memory',repository:'fixture/memory'}))),history:permissionHistory},conversations:[...conversations.values()].map(c=>({id:c.id,title:c.title,project_key:c.project_key,updated_at:1}))});
      const conversationMatch=url.pathname.match(/^\/api\/codex-workspace\/conversations\/([^/]+)$/);
      if (conversationMatch) {
        const c=conversations.get(conversationMatch[1]);
        return c?send({conversation:{id:c.id,title:c.title,project_key:c.project_key},messages:c.messages,runs:c.run_ids.map(id=>snapshot(runs.get(id)).run),artifacts:[]}):send({error:'No conversation'},404);
      }
      const eventsMatch=url.pathname.match(/^\/api\/codex-workspace\/runs\/([^/]+)\/events$/);
      if (eventsMatch) {
        const run=runs.get(eventsMatch[1]), after=Number(url.searchParams.get('after')||0);eventsAfter.push(after);
        return run?send(snapshot(run,after)):send({error:'No run'},404);
      }
      if (url.pathname=== '/api/codex-workspace/artifacts/'+'1'.repeat(32)) return send({content_type:'text/plain',content:'FROZEN_PATCH '+attack});
      if (/\/permissions\/[^/]+\/decision$/.test(url.pathname)) {
        const id=url.pathname.split('/').at(-2);
        if(id.startsWith('inbox-'))for(const run of runs.values()){const row=run.permissions.find(p=>p.id===id);if(row){permissionHistory.push({...row,state:body.decision==='deny'?'denied':'completed',note:body.note});run.permissions=run.permissions.filter(p=>p.id!==id);}}
        return send({accepted:true});
      }
      if (/\/runs\/[^/]+\/stop$/.test(url.pathname)) return send({accepted:true});
      return send({error:'Unexpected trace fixture route'},404);
    }
    if (url.pathname==='/api/settings') return send({settings});
    if (url.pathname==='/api/models') return send({models:[{model_key:'fixture',display_name:'Fixture',enabled:true,provider:'ollama'}]});
    if (url.pathname==='/api/agents') return send({agents:[{agent_key:'executive',name:'Executive',enabled:true,role:'Planning',success_count:0,failure_count:0}]});
    if (url.pathname==='/api/intelligence') return send({top:[],signals:{}});
    if (url.pathname==='/api/ai-connections') return send({providers:[],connections:[],codex:{available:false,connected:false}});
    if (url.pathname==='/api/execution-permissions') return send({state:'not_connected',requests:[]});
    if (url.pathname==='/healthz') return send({ok:true,version:'fixture',components:{},readiness:{}});
    return send({ok:true,[url.pathname.split('/').pop()]:[],items:[],repositories:{},traces:[],settings});
  } catch (error) { errors.push(error.stack);res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'Fixture failed'})); }
});

await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
const out=process.env.PB_SCREENSHOTS || await fs.mkdtemp(path.join(os.tmpdir(),'pb-native-trace-ui-'));
await fs.mkdir(out,{recursive:true});
let browser;
try {
  browser=await chromium.launch({headless:true,...(process.env.PB_BROWSER_PATH?{executablePath:process.env.PB_BROWSER_PATH}:{})});
  const context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}), page=await context.newPage();
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if (!request.url().startsWith(base)&&!request.url().startsWith('data:')) external.push(request.url());});
  await page.addInitScript(key=>sessionStorage.setItem('project_byte_access',key),credential);
  const run={id:'trace-run',status:'working',events:[],permissions:[],agents:[{id:'coordinator',role:'coordinator',status:'working',model:'gpt-6-astra',effort:'ultra'}]};
  runs.set(run.id,run); conversations.set('trace-conversation',{id:'trace-conversation',title:'Native trace fixture',project_key:'joeos',messages:[],run_ids:[run.id]});
  append(run,'prompt',{message_id:'prompt',text:'Read the fixture',created_at:Date.now()/1000});
  append(run,'agent_started',{id:'coordinator',role:'coordinator',status:'working'});
  append(run,'tool_started',{tool_id:'tool-a',agent_id:'coordinator',name:'workspace_read',arguments:{text:attack,bytes:attack.length,truncated:false,format:'text'},started_at:Date.now()/1000});
  append(run,'tool_started',{tool_id:'tool-b',agent_id:'coordinator',name:'workspace_list',arguments:{text:'{}',bytes:2,truncated:false,format:'json'}});
  for(let i=0;i<205;i++) append(run,'tool_output',{tool_id:'tool-a',agent_id:'coordinator',output_kind:'stream',text:'line '+i+'\n'});
  append(run,'tool_completed',{tool_id:'tool-b',agent_id:'coordinator',name:'workspace_list',success:true,duration_ms:24,result:{text:'Result B',bytes:8,truncated:false,format:'text'}});
  append(run,'tool_completed',{tool_id:'tool-a',agent_id:'coordinator',name:'workspace_read',success:true,duration_ms:42,result:{text:'Result A',bytes:8,truncated:true,format:'text'}});
  for(const count of [12,12,18]) append(run,'token_usage',{agent_id:'coordinator',native:{thread_id:'native-thread'},source:'native_codex',total:{total_tokens:count},last:{total_tokens:6}});
  await page.goto(base+'/#ai');
  await page.waitForFunction(()=>document.querySelector('[data-trace-node="tool:tool-a"][data-status="completed"]'));
  assert.ok(eventsAfter.some(after=>after>=200),'all event pages drained');
  await page.locator('#codexTraceCanvas [data-trace-node="tool:tool-a"]').click();
  assert.match(await page.locator('#codexTraceInspector').innerText(),/Result A/);
  assert.doesNotMatch(await page.locator('#codexTraceInspector').innerText(),/Result B/);
  assert.equal(await page.evaluate(()=>!!window.TRACE_XSS),false);
  assert.equal(await page.locator('#codexTraceInspector img').count(),0);
  await page.locator('#codexTraceMetric').selectOption('tokens');
  assert.match(await page.locator('#codexTraceList').innerText(),/18/);
  assert.doesNotMatch(await page.locator('#codexTraceList').innerText(),/42/);
  await page.locator('#codexTracePause').click();
  append(run,'tool_started',{tool_id:'publication',agent_id:'coordinator',name:'publish_pull_request'});
  run.permissions=[{id:'permission',tool_id:'publication',tool:'publish_pull_request',summary:'Review exact fixture patch',state:'pending',arguments:{title:'Fixture'},review_token:reviewToken,expires_at:Date.now()/1000+60}];
  await page.locator('#codexTraceApproval').waitFor({state:'visible'});
  await page.locator('[data-codex-note="permission"]').fill('Keep this note');
  const countdown=await page.locator('[data-codex-countdown="permission"]').innerText();
  await page.waitForFunction(previous=>document.querySelector('[data-codex-countdown="permission"]')?.textContent!==previous,countdown,{timeout:5000});
  assert.match(await page.locator('[data-codex-countdown="permission"]').innerText(),/remaining/);
  assert.equal(await page.locator('[data-codex-note="permission"]').inputValue(),'Keep this note');
  assert.equal(await page.locator('#codexTraceCanvas [data-trace-node="tool:publication"]').count(),0,'paused graph did not redraw');
  assert.doesNotMatch(await page.locator('#codexTraceStudio').innerText(),new RegExp(reviewToken));
  await page.locator('[data-codex-decision="deny"]').click();
  assert.equal(requests.filter(r=>r.path.endsWith('/decision')).length,1);
  assert.equal(requests.at(-1).body.decision,'deny');
  assert.equal(requests.at(-1).body.review_token,reviewToken);
  await page.locator('#codexTracePause').click();
  await page.locator('#codexTraceFocus').click();
  assert.equal(await page.locator('.cw-trace-focused').count(),1);
  await page.locator('#codexTraceFocus').press('Escape');
  assert.equal(await page.locator('.cw-trace-focused').count(),0);
  assert.equal(await page.locator('body.pb-depth').count(),1);
  assert.equal(await page.locator('body.pb-depth-motion').count(),0,'OS reduced motion respected');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.waitForFunction(()=>document.body.classList.contains('pb-depth-motion'));
  await page.locator('#codexTraceStudio [data-motion]').click();
  assert.equal(await page.locator('body.pb-depth-motion').count(),0);
  await page.screenshot({path:path.join(out,'native-trace-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  await page.locator('[data-codex-trace-tab="details"]').click();
  assert.equal(await page.locator('#codexTraceInspector').isVisible(),true);
  assert.equal(await page.locator('.cw-trace-graph-pane').isVisible(),false);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'mobile has no horizontal page overflow');
  await page.screenshot({path:path.join(out,'native-trace-mobile.png'),fullPage:true});
  await page.setViewportSize({width:1440,height:1000});
  await page.locator('[data-view="home"]').first().click();
  await page.locator('.home-hero').waitFor();
  await page.screenshot({path:path.join(out,'embossed-home-desktop.png'),fullPage:true});
  await page.setViewportSize({width:390,height:844});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'Home fits mobile');
  await page.screenshot({path:path.join(out,'embossed-home-mobile.png'),fullPage:true});
  await page.locator('.home-nav [data-home-go="ai"]').click();
  run.permissions=['inbox-approve','inbox-deny'].map(id=>({id,tool:'publish_pull_request',summary:'Frozen publication review',state:'pending',review_token:reviewToken,expires_at:Date.now()/1000+45,arguments:{review_artifact:{id:'1'.repeat(32)}}}));
  await page.locator('.home-nav [data-home-go="agents"]').click();
  await page.waitForFunction(()=>document.getElementById('codexPermissionInbox')?.textContent.includes('Codex connected'));
  assert.equal(await page.locator('[data-cw-inbox-id]').count(),2);
  assert.doesNotMatch(await page.locator('#codexPermissionInbox').innerHTML(),new RegExp(reviewToken));
  await page.locator('[data-cw-inbox-review="inbox-approve"]').click();
  await page.waitForFunction(()=>document.querySelector('[data-cw-inbox-patch="inbox-approve"]')?.textContent.includes('FROZEN_PATCH'));
  assert.equal(await page.locator('#codexPermissionInbox img').count(),0);
  await page.locator('[data-cw-inbox-note="inbox-approve"]').fill('Reviewed exact patch');
  const remaining=await page.locator('[data-cw-inbox-countdown="inbox-approve"]').innerText();
  await page.waitForFunction(previous=>document.querySelector('[data-cw-inbox-countdown="inbox-approve"]')?.textContent!==previous,remaining,{timeout:5000});
  assert.equal(await page.locator('[data-cw-inbox-note="inbox-approve"]').inputValue(),'Reviewed exact patch');
  await page.locator('[data-cw-inbox-decision="approve_once"][data-permission="inbox-approve"]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-cw-inbox-id]').length===1);
  assert.equal(await page.locator('[data-cw-inbox-id="inbox-deny"]').count(),1);
  await page.locator('[data-cw-inbox-decision="deny"][data-permission="inbox-deny"]').click();
  await page.waitForFunction(()=>document.getElementById('codexPermissionInbox')?.textContent.includes('No pending permission requests'));
  assert.deepEqual(requests.filter(r=>r.path.includes('/permissions/inbox-')).map(r=>r.body.decision),['approve_once','deny']);
  await page.screenshot({path:path.join(out,'permission-inbox-mobile.png'),fullPage:true});
  await page.locator('.home-nav [data-home-go="ai"]').click();
  run.status='completed';run.permissions=[];
  await page.waitForFunction(()=>document.getElementById('codexRunState').textContent==='Completed');
  await page.locator('#codexProject').selectOption('memory');
  assert.equal(await page.locator('#codexTraceStudio').isVisible(),false);
  assert.equal(await page.locator('#codexTraceInspector').innerText(),'');
  await page.locator('#login').click();
  assert.match(await page.locator('#codexPermissionInbox').innerText(),/Owner sign-in/);
  assert.equal(await page.locator('[data-cw-inbox-id]').count(),0);
  assert.equal(requests.filter(r=>r.path.endsWith('/message')).length,0,'graph controls never launch work');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log(JSON.stringify({ok:true,checks:['correlation','pagination','tokens','xss','live-deny-while-paused','server-countdown','focus','motion','mobile','project-clear','permission-inbox','frozen-patch','approve-once','deny-one-request','inbox-logout'],screenshots:out}));

} finally {
  await browser?.close();server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve));
}
