// UI contract fixture only: no real model, GitHub issue, tool, or runner is used.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium} = await import(process.env.PB_PLAYWRIGHT_MODULE || 'playwright');
const source = path.dirname(fileURLToPath(import.meta.url)), credential = 'CODEX_WORKSPACE_UI_FIXTURE_ONLY';
const requests = [], errors = [], external = [], conversations = new Map(), runs = new Map(), dedupe = new Map();
let failNextMessage = false, failNextPoll = false, messageNumber = 0;
let catalogConfigured = true, catalogConnected = true;
let projects = [{key:'joeos',label:'PROJECT_BYTE · fixture',repo:'fixture/JOEOS'}];
const fileMap = new Map();
let html = (await Promise.all((await fs.readdir(path.join(source,'index'))).filter(name => name.endsWith('.part')).sort().map(name => fs.readFile(path.join(source,'index',name),'utf8')))).join('');
for (const name of ['home.js','home-inspector.js','health-runtime.js','ai-connections.js','codex-workspace.js']) {
  fileMap.set('/'+name,{type:'text/javascript',body:await fs.readFile(path.join(source,name))});
  const tag = `<script src="/${name}"></script>`; if (!html.includes(tag)) html = html.replace('</body>',tag+'</body>');
}
fileMap.set('/',{type:'text/html',body:html});
const settings = {general:{default_view:'ai',refresh_seconds:60,show_completed:true},notifications:{},ai:{default_agent:'executive',default_model:'fixture'},security:{},filters:{saved_views:[]}};
const currentRun = () => [...runs.values()].at(-1);
function append(run,type,data) { run.events.push({seq:run.events.length+1,type,data,ts:Date.now()/1000}); }
function snapshot(run,after = 0) { return {events:run.events.filter(event => event.seq>after).slice(0,200),run:{id:run.id,status:run.status,model:'gpt-6-astra',effort:'Ultra'},permissions:run.permissions,agents:run.agents,artifacts:run.artifacts}; }
const server = http.createServer(async (req,res) => {
  try {
    const url = new URL(req.url,'http://fixture.local');
    if (fileMap.has(url.pathname)) { const file = fileMap.get(url.pathname); res.writeHead(200,{'Content-Type':file.type}); res.end(file.body); return; }
    let raw = ''; for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {}, level = req.headers['x-access-key'] === credential ? 4 : req.headers['x-access-key'] === 'ADMIN_FIXTURE_ONLY' ? 3 : 0;
    const send = (data,status = 200) => { res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data)); };
    if (url.pathname === '/api/session') return send({ok:level>0,level,subject:level?'fixture/'+level:'',role:level===4?'owner':level===3?'admin':'public',name:level===4?'Fixture owner':level===3?'Fixture admin':'Public'});
    if (url.pathname.startsWith('/api/codex-workspace')) {
      if (level !== 4) return send({error:'Owner required'},403);
      if (req.method === 'POST') requests.push({path:url.pathname,body});
      if (url.pathname === '/api/codex-workspace') return send({configured:catalogConfigured,connected:catalogConnected,model:'gpt-6-astra',effort:'Ultra',projects,conversations:[...conversations.values()].reverse().map(c => ({id:c.id,title:c.title,project_key:c.project_key,status:runs.get(c.run_ids.at(-1))?.status,updated_at:1}))});
      if (url.pathname === '/api/codex-workspace/artifacts/'+'a'.repeat(32)) { requests.push({path:url.pathname,body}); return send({name:'fixture-change.patch',content:'Fixture UTF-8 patch: π <script>not executed</script>\n',content_type:'text/plain'}); }
      if (url.pathname === '/api/codex-workspace/message') {
        assert.match(body.request_id,/^[a-f0-9-]{36}$/); assert.equal(Object.hasOwn(body,'model'),false); assert.equal(Object.hasOwn(body,'agent'),false);
        const project = projects.find(project => project.key === body.project_key);
        assert.ok(project); assert.equal(project.mode || 'execute','execute');
        assert.equal(Object.hasOwn(project,'configured') ? project.configured : catalogConfigured,true);
        assert.equal(Object.hasOwn(project,'connected') ? project.connected : catalogConnected,true);
        if (body.conversation_id) assert.equal(conversations.get(body.conversation_id)?.project_key,body.project_key);
        let result = dedupe.get(body.request_id);
        if (!result) {
          const id = body.conversation_id || 'conversation-'+(++messageNumber), runId = 'run-'+(runs.size+1);
          const conversation = conversations.get(id) || {id,title:body.message,project_key:body.project_key,messages:[],run_ids:[]};
          conversation.messages.push({id:'user-'+runId,role:'user',text:body.message,run_id:runId}); conversation.run_ids.push(runId); conversations.set(id,conversation);
          const run = {id:runId,status:'working',events:[],permissions:[],agents:[{id:'agent-'+runId,role:'Builder',model:'gpt-6-astra',effort:'Ultra',status:'working',parent_id:null}],artifacts:[]}; append(run,'status',{status:'working'}); runs.set(runId,run);
          result = {conversation_id:id,run_id:runId}; dedupe.set(body.request_id,result);
        }
        if (failNextMessage) { failNextMessage=false; return send({error:'Synthetic response lost after acceptance'},503); }
        return send(result,202);
      }
      const conversationMatch = url.pathname.match(/^\/api\/codex-workspace\/conversations\/([^/]+)$/);
      if (conversationMatch) { const c = conversations.get(conversationMatch[1]); return c ? send({conversation:{id:c.id,title:c.title,project_key:c.responseProjectKey || c.project_key},messages:c.messages,runs:c.run_ids.map(id => snapshot(runs.get(id)).run),artifacts:runs.get(c.run_ids.at(-1))?.artifacts||[]}) : send({error:'No conversation'},404); }
      const eventsMatch = url.pathname.match(/^\/api\/codex-workspace\/runs\/([^/]+)\/events$/);
      if (eventsMatch) { if (failNextPoll) { failNextPoll=false; return send({error:'Synthetic poll interruption'},503); } const run = runs.get(eventsMatch[1]); return run ? send(snapshot(run,Number(url.searchParams.get('after')||0))) : send({error:'No run'},404); }
      const stopMatch = url.pathname.match(/^\/api\/codex-workspace\/runs\/([^/]+)\/stop$/);
      if (stopMatch) { const run = runs.get(stopMatch[1]); run.status='stopping'; return send({accepted:true}); }
      if (/\/permissions\/[^/]+\/decision$/.test(url.pathname)) return send({accepted:true});
      return send({error:'Unknown native endpoint'},404);
    }
    if (url.pathname === '/api/chat' || /\/queue$/.test(url.pathname)) { requests.push({path:url.pathname,body}); return send({messages:[],answer:'LEGACY_FIXTURE'}); }
    if (url.pathname === '/api/settings') return send({settings});
    if (url.pathname === '/api/models') return send({models:[{model_key:'fixture',display_name:'Fixture model',enabled:true,provider:'ollama'}]});
    if (url.pathname === '/api/agents') return send({agents:[{agent_key:'executive',name:'Executive',enabled:true,role:'Planning',success_count:0,failure_count:0}]});
    if (url.pathname === '/api/intelligence') return send({top:[],signals:{}});
    if (url.pathname === '/api/ai-connections') return send({providers:[],connections:[],codex:{available:false,connected:false}});
    if (url.pathname === '/api/execution-permissions') return send({state:'not_connected',requests:[]});
    if (url.pathname === '/healthz') return send({ok:true,version:'fixture',components:{},readiness:{}});
    const field = url.pathname.split('/').pop(); return send({ok:true,[field]:[],items:[],repositories:{},traces:[],settings});
  } catch (error) { res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:error.message})); }
});
await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
const base = `http://127.0.0.1:${server.address().port}`, out = process.env.PB_SCREENSHOTS || await fs.mkdtemp(path.join(os.tmpdir(),'pb-codex-workspace-ui-'));
await fs.mkdir(out,{recursive:true});
let browser;
try {
  browser = await chromium.launch({headless:true,...(process.env.PB_BROWSER_PATH ? {executablePath:process.env.PB_BROWSER_PATH} : {})});
  const context = await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'}), page = await context.newPage();
  page.on('pageerror',error => errors.push(error.message)); page.on('request',request => { if (!request.url().startsWith(base) && !request.url().startsWith('data:')) external.push(request.url()); });
  page.on('dialog',dialog => dialog.accept(dialog.type()==='prompt'?credential:undefined));
  await page.addInitScript(key => {sessionStorage.setItem('project_byte_access',key);localStorage.setItem('prfkt.workspace.layout','desktop');},credential);
  await page.goto(base+'/#ai',{waitUntil:'networkidle'}); await page.waitForFunction(() => document.querySelector('#codexConnection')?.textContent.includes('Connected'));
  assert.equal(await page.locator('#pbChatContext').isVisible(),false,'owner does not have to select agents or models');
  assert.match(await page.locator('#codexRoute').textContent(),/gpt-6-astra.*Ultra/);
  for (const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'workspace fits '+width);
    await page.screenshot({path:path.join(out,`codex-workspace-${width}.png`),fullPage:true});
  }
  await page.setViewportSize({width:390,height:844});
  await page.locator('#chatInput').fill('Inspect the implementation — UI fixture');
  await page.evaluate(() => { document.querySelector('#sendChat').click(); document.querySelector('#sendChat').click(); });
  await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Working');
  assert.equal(requests.filter(r => r.path.endsWith('/message')).length,1,'double click dispatches once');
  assert.equal(requests.filter(r => r.path==='/api/chat'||r.path.endsWith('/queue')).length,0,'owner uses only native workspace route');
  assert.equal(await page.locator('#chatInput').inputValue(),'');
  const first = currentRun(), attack = '<img src=x onerror="window.CW_XSS=true">';
  append(first,'message_delta',{text:'Inspecting '+attack}); append(first,'tool_started',{name:'Read',summary:attack}); append(first,'tool_output',{text:'Read output '+attack});
  await page.waitForFunction(() => document.querySelector('#chatlog').textContent.includes('Inspecting <img'));
  assert.equal(await page.locator('#chatlog img,#codexTools img').count(),0); assert.equal(await page.evaluate(() => !!window.CW_XSS),false);
  first.status='waiting_approval'; first.permissions=[{id:'permission-1',tool:'Bash',summary:'Run the bounded fixture check',arguments:{command:attack},review_token:'REVIEW_FIXTURE_ONLY',expires_at:Date.now()/1000+120,state:'pending'}];
  await page.locator('[data-codex-decision="approve_once"]').waitFor();
  await page.locator('[data-codex-note="permission-1"]').fill('Proceed with this fixture');
  await page.evaluate(() => { const button=document.querySelector('[data-codex-decision="approve_once"]');button.click();button.click(); });
  await page.waitForFunction(() => document.querySelector('#codexFeedback').textContent.includes('Permission decision accepted'));
  const decisions = requests.filter(r => r.path.endsWith('/decision')); assert.equal(decisions.length,1); assert.equal(decisions[0].body.review_token,'REVIEW_FIXTURE_ONLY'); assert.equal(decisions[0].body.note,'Proceed with this fixture');
  assert.match(await page.locator('#codexPermissions').textContent(),/Waiting for the tool result/);
  assert.equal(await page.locator('#codexPermissions img').count(),0);
  first.permissions=[]; first.status='working'; append(first,'tool_completed',{name:'Read',success:true});
  append(first,'message',{role:'assistant',text:'Review result '+attack});
  conversations.get('conversation-1').messages.push({id:'assistant-first',role:'assistant',text:'Review result '+attack,run_id:first.id});
  first.artifacts=[{id:'pr',name:'Fixture PR #1',url:'https://github.com/fixture/JOEOS/pull/1'},{id:'a'.repeat(32),name:'Download fixture patch',url:'/api/codex-workspace/artifacts/'+'a'.repeat(32)},{id:'unsafe',name:'Unsafe fixture',url:'javascript:window.CW_XSS=true'},{id:'missing',name:'Missing URL'}];
  await page.waitForFunction(() => document.querySelector('#codexArtifacts').textContent.includes('Fixture PR'));
  assert.equal(await page.locator('#codexArtifacts a').count(),1,'only explicit safe URLs are linked');
  assert.equal(await page.locator('#codexArtifacts a').getAttribute('rel'),'noopener noreferrer');
  const downloadEvent=page.waitForEvent('download'); await page.locator('[data-codex-download]').click(); const downloaded=await downloadEvent;
  assert.equal(downloaded.suggestedFilename(),'fixture-change.patch'); assert.equal(await fs.readFile(await downloaded.path(),'utf8'),'Fixture UTF-8 patch: π <script>not executed</script>\n');
  assert.equal(requests.filter(r=>r.path.includes('/artifacts/')).length,1,'artifact download uses authenticated API');
  await page.locator('#codexStop').click(); await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Stopping');
  assert.match(await page.locator('#codexFeedback').textContent(),/Waiting for the runtime to confirm/);
  for(let i=0;i<250;i++)append(first,'tool_output',{text:'Recorded fixture event '+i});
  append(first,'tool_output',{text:'FINAL_EVENT_AFTER_TERMINAL_PAGE'});
  first.status='stopped'; append(first,'status',{status:'stopped'});
  await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Stopped'); assert.equal(await page.locator('#codexStop').isVisible(),false);
  await page.waitForFunction(() => document.querySelector('#codexTools').textContent.includes('FINAL_EVENT_AFTER_TERMINAL_PAGE'));
  await page.locator('#cwNewWork').click(); failNextMessage=true;
  await page.locator('#chatInput').fill('Retry without duplicating work'); await page.locator('#sendChat').click();
  await page.waitForFunction(() => document.querySelector('#cwWorkState').textContent.includes('Check submission'));
  assert.equal(await page.locator('#chatInput').inputValue(),'Retry without duplicating work');
  await page.locator('#cwRetryQueued').click(); await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Working');
  const submissions=requests.filter(r=>r.path.endsWith('/message')); assert.equal(submissions[1].body.request_id,submissions[2].body.request_id,'uncertain submission retry keeps its idempotency key'); assert.equal(runs.size,2);
  failNextPoll=true;
  await page.waitForFunction(() => document.querySelector('#codexFeedback').textContent.includes('Updates interrupted'));
  assert.equal(await page.locator('#codexRunState').textContent(),'Working','poll failure retains last recorded run state');
  await page.waitForFunction(() => !document.querySelector('#codexFeedback').textContent.includes('Updates interrupted'));
  currentRun().status='completed'; append(currentRun(),'message',{role:'assistant',text:'PERSISTED_FIXTURE_RESULT'});
  conversations.get('conversation-2').messages.push({id:'final-second',role:'assistant',text:'PERSISTED_FIXTURE_RESULT',run_id:currentRun().id});
  await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Completed');
  await page.reload({waitUntil:'networkidle'}); await page.waitForFunction(() => document.querySelector('#chatlog').textContent.includes('PERSISTED_FIXTURE_RESULT'));
  assert.equal(await page.locator('#codexConversation').inputValue(),'conversation-2','selected work window restores its server conversation');
  assert.equal(await page.evaluate(() => [...Object.values(localStorage),...Object.values(sessionStorage)].some(value => /PERSISTED_FIXTURE_RESULT|conversation-2|REVIEW_FIXTURE_ONLY/.test(value))),false);
  // Per-project readiness overrides the legacy aggregate without inventing a
  // connected worker for externally owned or blocked projects.
  catalogConfigured=false; catalogConnected=false;
  projects = [...projects,
    {key:'memory',label:'MEMORY_BYTE · fixture',repo:'fixture/MEMORY_BYTE',mode:'execute',configured:true,connected:true,publication:false},
    {key:'vitros',label:'VITROS · blocked fixture',repo:'fixture/vitros',mode:'blocked',configured:true,connected:true,publication:false,detail:'Review the existing checkout before work. '+attack},
    {key:'stickdeath',label:'StickDeath · external fixture',repo:'fixture/stickdeath',mode:'observe',configured:true,connected:true,publication:false,detail:'An existing task owns this project. Follow its recorded work here.'},
    {key:'kalshi',label:'KALSHI_BYTE · setup fixture',repo:null,mode:'execute',configured:false,connected:false,publication:false,detail:'The project checkout is not connected yet.'}
  ];
  for (const [id,project_key,body,responseProjectKey] of [
    ['blocked-history','vitros','BLOCKED_PROJECT_RECORDED_HISTORY',undefined],
    ['external-history','stickdeath','EXTERNAL_PROJECT_RECORDED_HISTORY',undefined],
    ['mismatched-history','vitros','WRONG_PROJECT_HISTORY_MUST_NOT_RENDER','memory']
  ]) conversations.set(id,{id,title:id,project_key,responseProjectKey,messages:[{id:'message-'+id,role:'assistant',text:body}],run_ids:[]});
  await page.locator('#codexRefresh').click(); await page.waitForFunction(() => document.querySelector('#codexProject option[value="memory"]'));
  await page.locator('#cwNewWork').click();
  await page.locator('#codexProject').selectOption('memory');
  assert.equal(await page.locator('#sendChat').isEnabled(),true,'available selected project overrides unavailable aggregate');
  assert.match(await page.locator('#codexConnection').textContent(),/Connected/);
  await page.locator('#chatInput').fill('Work in MEMORY_BYTE — fixture only'); await page.locator('#sendChat').click();
  await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Working');
  const memorySubmission=requests.filter(r=>r.path.endsWith('/message')).at(-1);
  assert.equal(memorySubmission.body.project_key,'memory'); assert.equal(memorySubmission.body.conversation_id,null,'project change starts with its own conversation');
  await page.evaluate(() => { const select=document.querySelector('#codexProject');select.value='vitros';select.dispatchEvent(new Event('change')); });
  assert.equal(await page.locator('#codexProject').inputValue(),'memory','an active run cannot be rebound by a project-change event');
  const memoryRun=currentRun(); memoryRun.status='completed'; append(memoryRun,'message',{role:'assistant',text:'MEMORY_PROJECT_RESULT'});
  [...conversations.values()].find(c=>c.run_ids.includes(memoryRun.id)).messages.push({id:'memory-result',role:'assistant',text:'MEMORY_PROJECT_RESULT',run_id:memoryRun.id});
  await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Completed');
  await page.reload({waitUntil:'networkidle'});await page.waitForFunction(()=>document.querySelector('#chatlog').textContent.includes('MEMORY_PROJECT_RESULT'));
  assert.equal(await page.locator('#codexProject').inputValue(),'memory','reload preserves selected workspace');
  await page.evaluate(()=>window.PRFKT_CODEX.openConversation('conversation-2'));await page.waitForFunction(()=>document.querySelector('#chatlog').textContent.includes('PERSISTED_FIXTURE_RESULT'));
  catalogConfigured=true; catalogConnected=true; await page.locator('#codexRefresh').click();
  await page.waitForFunction(() => !document.querySelector('#sendChat').disabled);
  const beforeUnavailable=requests.filter(r=>r.path.endsWith('/message')).length;
  for (const [key,history,expected] of [
    ['vitros','blocked-history','BLOCKED_PROJECT_RECORDED_HISTORY'],
    ['stickdeath','external-history','EXTERNAL_PROJECT_RECORDED_HISTORY'],
    ['kalshi',null,null]
  ]) {
    await page.locator('#cwNewWork').click();await page.locator('#codexProject').selectOption(key);
    assert.equal(await page.locator('#sendChat').isDisabled(),true,key+' cannot submit');
    assert.equal(await page.locator('#codexRunState').textContent(),{vitros:'Blocked',stickdeath:'Observed',kalshi:'Not connected'}[key],'idle status must not claim execution is ready');
    assert.equal(await page.locator('#codexConnection').textContent(),projects.find(p=>p.key===key).detail);
    assert.ok(!(await page.locator('#codexRoute').textContent()).includes('gpt-6-astra'),'unavailable project is not presented as a routed model');
    await page.locator('#chatInput').fill('Do not dispatch this unavailable project');
    await page.evaluate(() => window.PRFKT_CODEX.send());
    assert.equal(requests.filter(r=>r.path.endsWith('/message')).length,beforeUnavailable,'programmatic submission also respects project state');
    if (history) {
      await page.locator('#codexConversation').selectOption(history);
      await page.waitForFunction(expected=>document.querySelector('#chatlog').textContent.includes(expected),expected);
      assert.equal(await page.locator('#codexProject').inputValue(),key);
      assert.equal(await page.locator('#sendChat').isDisabled(),true,'recorded history does not enable execution');
    }
  }
  assert.equal(await page.locator('#sendChat').isDisabled(),true,'project false overrides true aggregate readiness');
  await page.locator('#cwNewWork').click();await page.locator('#codexProject').selectOption('vitros');
  for (const width of [320,390,768,1440]) {
    await page.setViewportSize({width,height:900});
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'project status fits '+width);
    await page.screenshot({path:path.join(out,`codex-project-status-${width}.png`),fullPage:true});
  }
  assert.equal(await page.locator('#codexConnection img').count(),0); assert.equal(await page.evaluate(() => !!window.CW_XSS),false);
  await page.locator('#codexConversation').selectOption('mismatched-history');
  await page.waitForFunction(() => document.querySelector('#codexFeedback').textContent.includes('does not match the selected project'));
  assert.equal(await page.locator('#codexProject').inputValue(),'vitros','mismatched history cannot retarget the project');
  assert.equal(await page.locator('#codexConversation').inputValue(),'');
  assert.ok(!(await page.locator('#chatlog').textContent()).includes('WRONG_PROJECT_HISTORY_MUST_NOT_RENDER'));
  assert.equal(requests.filter(r=>r.path.endsWith('/message')).length,beforeUnavailable);
  await page.locator('#cwNewWork').click();await page.locator('#codexProject').selectOption('joeos'); await page.locator('#codexConversation').selectOption('conversation-2');
  await page.waitForFunction(() => document.querySelector('#chatlog').textContent.includes('PERSISTED_FIXTURE_RESULT'));
  assert.equal(await page.locator('#sendChat').isEnabled(),true,'legacy per-project field defaults remain usable');
  const allProjects=projects; projects=projects.filter(project=>project.key!=='joeos');
  await page.locator('#codexRefresh').click();
  await page.waitForFunction(() => document.querySelector('#codexConnection').textContent.includes('no longer available'));
  assert.equal(await page.locator('#codexProject').inputValue(),'joeos','catalog removal cannot silently retarget existing history');
  assert.equal(await page.locator('#codexConversation').inputValue(),'conversation-2');
  assert.equal(await page.locator('#sendChat').isDisabled(),true);
  projects=allProjects; await page.locator('#codexRefresh').click();
  await page.waitForFunction(() => !document.querySelector('#sendChat').disabled);
  await page.setViewportSize({width:390,height:844});
  runs.get('run-2').status='working'; await page.evaluate(()=>window.PRFKT_CODEX.openConversation('conversation-2')); await page.waitForFunction(() => document.querySelector('#codexRunState').textContent==='Working');
  let release,arrived; const requested=new Promise(resolve=>arrived=resolve);
  await page.route('**/api/codex-workspace/runs/*/events?after=*',async route => { arrived();await new Promise(resolve=>release=resolve);try { await route.fulfill({contentType:'application/json',body:JSON.stringify({run:{id:'run-2',status:'working'},events:[{seq:999,type:'message_delta',data:{text:'LATE_PRIVATE_FIXTURE'}}],permissions:[{id:'private',tool:'PRIVATE_TOOL',summary:'PRIVATE_PERMISSION',state:'pending',review_token:'PRIVATE_TOKEN',expires_at:Date.now()/1000+120}],agents:[],artifacts:[]})}); } catch(error) { if (!/closed|cancel|abort|invalid interception/i.test(error.message)) throw error; } });
  await requested; await page.evaluate(() => document.querySelector('#login').click()); await page.waitForFunction(() => session.level===0&&!accessKey); release(); await page.waitForTimeout(250);
  assert.equal(await page.locator('#codexWorkspacePanel').isVisible(),false);
  assert.ok(!(await page.locator('#ai').textContent()).includes('LATE_PRIVATE_FIXTURE')); assert.ok(!(await page.locator('#ai').textContent()).includes('PRIVATE_PERMISSION')); assert.equal(await page.locator('#chatlog').textContent(),'');
  await page.unroute('**/api/codex-workspace/runs/*/events?after=*');
  await page.evaluate(() => { accessKey='ADMIN_FIXTURE_ONLY';sessionStorage.setItem('project_byte_access',accessKey);return load(); }); await page.waitForFunction(() => session.level===3);
  assert.equal(await page.locator('#codexWorkspacePanel').isVisible(),false,'administrator cannot use owner runner');
  assert.equal(await page.locator('#pbChatContext').isVisible(),true,'legacy collaborator controls remain available');
  assert.equal(await page.locator('#sendChat').isEnabled(),true,'owner run cannot leave collaborator chat disabled');
  assert.deepEqual(errors,[]); assert.deepEqual(external,[]);
  console.log('CODEX_WORKSPACE_NATIVE_ROUTING_STREAM_PERMISSION_STOP_AND_4_WIDTHS=PASS');
  console.log('CODEX_WORKSPACE_DRAFT_IDEMPOTENCY_RECONNECT_XSS_LOGOUT_OWNER_BOUNDARY=PASS');
  console.log('CODEX_WORKSPACE_MULTIPROJECT_READINESS_HISTORY_AND_BINDING=PASS');
  console.log('Screenshots: '+out);
} finally { await browser?.close(); server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve)); }
