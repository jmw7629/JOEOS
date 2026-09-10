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
const errors = [], external = [], requests = [], eventsAfter = [], allPosts = [];
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
    if(req.method==='POST')allPosts.push(url.pathname);
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
  page.setDefaultTimeout(12000);page.setDefaultNavigationTimeout(15000);
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if (!request.url().startsWith(base)&&!request.url().startsWith('data:')) external.push(request.url());});
  await page.addInitScript(key=>{sessionStorage.setItem('project_byte_access',key);localStorage.setItem('prfkt.workspace.layout','auto');window.fixturePermissionIds=[];window.addEventListener('prfkt-permission-prompt',e=>window.fixturePermissionIds.push(e.detail.id));},credential);
  async function go(view) {
    await page.locator('#pbWorkspaceButton').click();
    await page.locator('#pbWorkspaces [data-home-go="'+view+'"]').click();
    await page.waitForSelector('#'+view+'.active');
  }
  const native=(message_id,agent_id,text,phase='commentary')=>({message_id,agent_id,text,phase});
  const run={id:'conversation-run',status:'working',events:[],permissions:[],agents:[
    {id:'coordinator',role:'coordinator',status:'working',model:'gpt-6-astra',effort:'ultra'},
    {id:'specialist',role:'reviewer',status:'working',model:'gpt-6-astra',effort:'ultra'}
  ]};
  runs.set(run.id,run);
  const conversation={id:'conversation-fixture',title:'Conversation fixture',project_key:'joeos',messages:[
    {id:'owner-message',role:'user',text:'Inspect the fixture',run_id:run.id}
  ],run_ids:[run.id]};
  conversations.set(conversation.id,conversation);
  append(run,'agent_started',{id:'coordinator',role:'coordinator',status:'working'});
  append(run,'agent_started',{id:'specialist',role:'reviewer',parent_id:'coordinator',status:'working'});
  append(run,'message_delta',{...native('c','coordinator','Coordinator '),output_kind:'agent_delta'});
  append(run,'tool_output',{...native('s','specialist','Specialist '),output_kind:'agent_delta'});
  append(run,'message_delta',{...native('c','coordinator','commentary '+attack),output_kind:'agent_delta'});
  append(run,'tool_output',{...native('s','specialist','finding '+attack),output_kind:'agent_delta'});
  append(run,'agent_message',native('c','coordinator','Coordinator commentary '+attack));
  append(run,'agent_message',native('s','specialist','Specialist finding '+attack,'final_answer'));
  await page.addInitScript(()=>localStorage.setItem('prfkt.updates.popups','off'));
  await page.goto(base+'/#ai');
  await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('Specialist finding'));
  assert.equal(await page.locator('[data-cw-view="chat"]').getAttribute('aria-pressed'),'true','Chat is the default mode');
  assert.equal(await page.locator('#chatlog').isVisible(),true);
  assert.equal(await page.locator('#cwTerminal').isVisible(),false);
  assert.equal(await page.locator('#codexTraceStudio').isVisible(),false);
  assert.equal(await page.locator('.prfkt-update-card').first().isVisible(),false,'initial history does not trigger a popup');
  assert.equal(await page.locator('#chatlog [data-message-id="c"]').count(),1);
  assert.equal(await page.locator('#chatlog [data-message-id="s"]').count(),1);
  assert.match(await page.locator('#chatlog [data-message-id="c"]').innerText(),/AI_BYTE/);
  assert.match(await page.locator('#chatlog [data-message-id="s"]').innerText(),/reviewer/);
  assert.equal(await page.locator('#chatlog img').count(),0);
  assert.equal(await page.evaluate(()=>!!window.TRACE_XSS),false);


  assert.equal(await page.locator('[data-depth],[data-motion],.pb-depth-controls button').count(),0,'depth and motion toggle buttons were removed');
  const nebula=page.locator('#prfktNebula > div');
  assert.equal(await page.locator('#prfktNebula').getAttribute('aria-hidden'),'true');
  assert.match(await nebula.evaluate(e=>getComputedStyle(e).backgroundImage),/data:image\/webp;base64,/,'nebula image is embedded');
  assert.equal(await page.evaluate(()=>typeof DeviceOrientationEvent!=='undefined' && typeof DeviceOrientationEvent.requestPermission!=='function'),true,'Chromium fixture uses available orientation without the iOS permission API');
  assert.equal(await page.locator('#webbOrientationAccess').isVisible(),false);
  const dispatchOrientation=async reading=>page.evaluate(async reading=>{
    window.dispatchEvent(new DeviceOrientationEvent('deviceorientation',reading));
    await new Promise(resolve=>requestAnimationFrame(resolve));
  },reading);
  const tilt=()=>nebula.evaluate(e=>({x:parseFloat(e.style.getPropertyValue('--webb-x') || '0'),y:parseFloat(e.style.getPropertyValue('--webb-y') || '0'),transform:getComputedStyle(e).transform}));
  await page.emulateMedia({reducedMotion:'no-preference'});
  await dispatchOrientation({beta:10,gamma:5});
  assert.deepEqual(await tilt().then(({x,y})=>({x,y})),{x:0,y:0},'first reading establishes a neutral orientation');
  await dispatchOrientation({beta:25,gamma:20});
  const tilted=await tilt();
  assert.ok(Math.abs(tilted.x)>0 && Math.abs(tilted.y)>0,'subsequent real-valued readings move both background axes');
  assert.ok(Math.abs(tilted.x)<=65 && Math.abs(tilted.y)<=65,'orientation displacement is bounded');
  await page.screenshot({path:path.join(out,'conversation-chat-1440.png'),fullPage:false});
  await go('home');
  assert.equal(await page.locator('#homeAIState').isVisible(),false,'Home AI CHAT status badge is removed');
  await page.screenshot({path:path.join(out,'conversation-nebula-1440.png'),fullPage:false});
  assert.equal(await page.locator('[data-depth],[data-motion],.pb-depth-controls button').count(),0,'Home does not remount removed appearance buttons');
  await go('ai');
  await page.emulateMedia({reducedMotion:'reduce'});
  await page.waitForFunction(()=>{const e=document.querySelector('#prfktNebula > div');return parseFloat(e.style.getPropertyValue('--webb-x') || '0')===0 && parseFloat(e.style.getPropertyValue('--webb-y') || '0')===0;});
  await dispatchOrientation({beta:70,gamma:45});
  assert.deepEqual(await tilt().then(({x,y})=>({x,y})),{x:0,y:0},'reduced motion resets and ignores further tilt readings');
  assert.equal((await tilt()).transform,'none');
  await page.emulateMedia({reducedMotion:'no-preference'});
  await dispatchOrientation({beta:40,gamma:20});
  assert.deepEqual(await tilt().then(({x,y})=>({x,y})),{x:0,y:0},'re-enabling orientation establishes a fresh neutral frame');
  await dispatchOrientation({beta:55,gamma:35});
  assert.ok(Math.abs((await tilt()).x)>0);
  await page.evaluate(()=>window.dispatchEvent(new Event('orientationchange')));
  assert.deepEqual(await tilt().then(({x,y})=>({x,y})),{x:0,y:0},'device rotation resets the previous orientation origin');
  await page.emulateMedia({reducedMotion:'reduce'});
  console.log('CONVERSATION_NEBULA_SYNTHETIC_ORIENTATION=PASS');

  console.log('CONVERSATION_NATIVE_DEFAULT=PASS');
  // Only API-provided command streams and completion metadata form terminal output.
  const argumentsText=JSON.stringify({command:'npm run fixture-check'});
  append(run,'tool_started',{tool_id:'command',agent_id:'coordinator',name:'workspace_command',
    arguments:{text:argumentsText,format:'json',bytes:argumentsText.length,truncated:false},started_at:Date.now()/1000});
  append(run,'tool_output',{tool_id:'command',agent_id:'coordinator',output_kind:'stream',stream:'stdout',text:'BUILD_STDOUT_MARKER\n'});
  append(run,'tool_output',{tool_id:'command',agent_id:'coordinator',output_kind:'stream',stream:'stderr',text:'BUILD_STDERR_MARKER '+attack+'\n'});
  const commandResult=JSON.stringify({exit_code:7,stdout:'BUILD_STDOUT_MARKER\n',stderr:'BUILD_STDERR_MARKER '+attack+'\n',timed_out:true,truncated:false});
  append(run,'tool_output',{tool_id:'command',agent_id:'coordinator',output_kind:'result',text:commandResult});
  append(run,'tool_completed',{tool_id:'command',agent_id:'coordinator',name:'workspace_command',success:true,duration_ms:1400,
    result:{text:commandResult,format:'json',bytes:commandResult.length,truncated:false}});
  await page.locator('[data-cw-view="terminal"]').click();
  await page.waitForFunction(()=>document.querySelector('#cwTerminalLog')?.textContent.includes('exit 7'));
  assert.equal(await page.locator('#chatlog').isVisible(),false);
  const commandEntry=page.locator('#cwTerminalLog [data-tool-id="tool:command"]');
  assert.match(await commandEntry.innerText(),/npm run fixture-check/);
  assert.match(await commandEntry.innerText(),/exit 7.*timed out/);
  assert.equal((await commandEntry.innerText()).split('BUILD_STDOUT_MARKER').length-1,1,'aggregate result does not duplicate streamed stdout');
  assert.match(await commandEntry.locator('pre.cw-terminal-error').innerText(),/BUILD_STDERR_MARKER/);
  assert.equal(await commandEntry.locator('img').count(),0);
  await page.locator('#cwFollowOutput').click();
  assert.equal(await page.locator('#cwFollowOutput').getAttribute('aria-pressed'),'false');

  console.log('CONVERSATION_TERMINAL_STREAMS=PASS');
  // The UI must disclose its own bounded output retention as well as server limits.
  append(run,'tool_started',{tool_id:'long-command',agent_id:'coordinator',name:'workspace_command',
    arguments:{text:'{"command":"fixture-long-output"}',format:'json',truncated:false}});
  append(run,'tool_output',{tool_id:'long-command',agent_id:'coordinator',output_kind:'stream',stream:'stdout',text:'x'.repeat(40000)+'TRUNCATION_TAIL'});
  await page.waitForFunction(()=>document.querySelector('#cwTerminalLog [data-tool-id="tool:long-command"]')?.textContent.includes('TRUNCATION_TAIL'));
  assert.match(await page.locator('#cwTerminalLog [data-tool-id="tool:long-command"]').innerText(),/truncated/i);


  assert.ok((await page.locator('#cwTerminalLog [data-tool-id="tool:long-command"] pre:not(.cw-terminal-command)').allTextContents()).join('').length<=32768,'single-chunk output respects the total retention limit');
  append(run,'tool_started',{tool_id:'chunk-command',agent_id:'coordinator',name:'workspace_command'});
  for(let i=0;i<115;i++)append(run,'tool_output',{tool_id:'chunk-command',agent_id:'coordinator',output_kind:'stream',stream:i%2?'stderr':'stdout',text:'CHUNK_'+String(i).padStart(3,'0')+'\n'});
  await page.waitForFunction(()=>document.querySelector('#cwTerminalLog [data-tool-id="tool:chunk-command"]')?.textContent.includes('CHUNK_114'));
  const chunkEntry=page.locator('#cwTerminalLog [data-tool-id="tool:chunk-command"]');
  assert.ok(await chunkEntry.locator('pre').count()<=100,'stream chunk count is bounded');
  assert.doesNotMatch(await chunkEntry.innerText(),/CHUNK_000/);
  assert.match(await chunkEntry.innerText(),/truncated/i);

  await page.locator('[data-cw-view="graph"]').click();
  assert.equal(await page.locator('#codexTraceStudio').isVisible(),true);
  assert.equal(await page.locator('#cwTerminal').isVisible(),false);
  await page.locator('[data-cw-view="chat"]').click();
  assert.equal(await page.locator('#chatlog').isVisible(),true);

  console.log('CONVERSATION_GRAPH_AND_OUTPUT_BOUND=PASS');
  // Both approval entry points expose the existing authoritative context form.
  await page.setViewportSize({width:390,height:844});
  await page.waitForFunction(()=>document.querySelector('#ai').classList.contains('cw-remote'));
  assert.equal(await page.locator('#cwContextToggle').isVisible(),true);
  assert.equal(await page.locator('#codexWorkspacePanel').isVisible(),false);
  await page.locator('#cwContextToggle').click();
  assert.equal(await page.locator('#codexWorkspacePanel').isVisible(),true);
  await page.locator('#cwContextToggle').click();
  await page.screenshot({path:path.join(out,'conversation-chat-390.png'),fullPage:false});
  await page.locator('[data-cw-view="terminal"]').click();
  await page.screenshot({path:path.join(out,'conversation-terminal-390.png'),fullPage:false});
  await page.locator('[data-cw-view="chat"]').click();
  await page.evaluate(()=>localStorage.setItem('prfkt.updates.popups','on'));
  run.status='waiting_approval';
  run.permissions=[{id:'mobile-review',tool_id:'publication',tool:'publish_pull_request',summary:'Review exact mobile fixture',
    arguments:{command:attack},state:'pending',review_token:reviewToken,expires_at:Date.now()/1000+120}];
  await page.locator('.prfkt-update-card').filter({hasText:'Review exact mobile fixture'}).waitFor({state:'visible'});
  assert.match(await page.locator('.prfkt-update-card').filter({hasText:'Review exact mobile fixture'}).innerText(),/Review exact mobile fixture/);
  assert.ok(await page.evaluate(()=>window.fixturePermissionIds.includes('mobile-review')),'popup came from the actual pending API request ID');
  const beforePopupDismiss=allPosts.length;
  await page.evaluate(()=>document.querySelectorAll('.prfkt-update-card .prfkt-dismiss').forEach(b=>b.click()));
  await page.waitForTimeout(350);
  assert.equal(allPosts.length,beforePopupDismiss,'dismissing a permission popup does not decide or launch anything');
  await page.locator('#cwReviewRequest').waitFor({state:'visible'});
  await page.locator('#cwReviewRequest').click();
  assert.equal(await page.locator('#codexPermissions').isVisible(),true);
  assert.equal(await page.locator('[data-codex-decision="approve_once"]').isVisible(),true);
  assert.doesNotMatch(await page.locator('#codexTraceStudio').innerHTML(),new RegExp(reviewToken));
  await page.locator('#cwContextToggle').click();
  await page.locator('[data-cw-view="graph"]').click();
  await page.locator('#codexTraceFocus').click();
  await page.locator('#codexTraceApproval button').click();
  assert.equal(await page.locator('.cw-trace-focused').count(),0);
  assert.equal(await page.locator('#codexPermissions').isVisible(),true,'Graph review reveals collapsed remote context');
  await page.locator('[data-codex-note="mobile-review"]').fill('Reviewed in mobile fixture');
  await page.locator('[data-codex-decision="deny"]').click();
  await page.waitForFunction(()=>document.querySelector('#codexFeedback')?.textContent.includes('Permission decision accepted'));
  const decisionRequests=requests.filter(r=>r.path.endsWith('/decision'));
  assert.equal(decisionRequests.length,1);
  assert.equal(decisionRequests[0].body.review_token,reviewToken);
  assert.equal(decisionRequests[0].body.decision,'deny');
  run.permissions=[];run.status='working';
  await page.locator('[data-cw-view="chat"]').click();
  await page.locator('#cwContextToggle').click();

  console.log('CONVERSATION_MOBILE_REVIEW=PASS');
  await page.locator('#cwOptions').click();
  await page.waitForSelector('#settings.active');
  await page.screenshot({path:path.join(out,'conversation-settings-390.png'),fullPage:true});
  await page.locator('#cwLayoutPreference').selectOption('desktop');
  await go('ai');
  assert.equal(await page.locator('#ai').evaluate(e=>e.classList.contains('cw-remote')),false);
  assert.equal(await page.locator('#cwContextToggle').isVisible(),false);
  await page.locator('#cwOptions').click();
  await page.locator('#cwLayoutPreference').selectOption('mobile');
  await page.setViewportSize({width:1440,height:1000});
  await go('ai');
  assert.equal(await page.locator('#ai').evaluate(e=>e.classList.contains('cw-remote')),true,'explicit mobile mode also works at desktop width');
  await page.locator('#cwOptions').click();
  await page.locator('#cwLayoutPreference').selectOption('auto');
  await page.locator('#cwPopupPreference').uncheck();
  await go('ai');
  assert.equal(await page.locator('#ai').evaluate(e=>e.classList.contains('cw-remote')),false);
  append(run,'agent_message',native('popup-off','specialist','POPUP_DISABLED_REAL_MESSAGE'));
  await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('POPUP_DISABLED_REAL_MESSAGE'));
  assert.equal(await page.locator('.prfkt-update-card').first().isVisible(),false,'disabled popups preserve inline messages');
  await page.locator('#cwOptions').click();
  await page.locator('#cwPopupPreference').check();
  await go('ai');
  append(run,'agent_message',native('popup-on','specialist','POPUP_ENABLED_REAL_MESSAGE'));
  await page.locator('.prfkt-update-card').filter({hasText:'POPUP_ENABLED_REAL_MESSAGE'}).waitFor({state:'visible'});
  assert.match(await page.locator('.prfkt-update-card').filter({hasText:'POPUP_ENABLED_REAL_MESSAGE'}).innerText(),/POPUP_ENABLED_REAL_MESSAGE/);
  await page.evaluate(()=>document.querySelectorAll('.prfkt-update-card .prfkt-dismiss').forEach(b=>b.click()));
  await page.waitForTimeout(350);
  await page.waitForTimeout(1100);
  assert.equal(await page.locator('.prfkt-update-card').first().isVisible(),false,'dismissed completed messages do not reopen');


  await go('agents');
  run.permissions=[{id:'agents-popup',tool:'publish_pull_request',summary:'AGENTS_REAL_PENDING_REQUEST',state:'pending',
    arguments:{title:'Agents popup fixture'},review_token:reviewToken,expires_at:Date.now()/1000+120}];
  await page.locator('.prfkt-update-card').filter({hasText:'AGENTS_REAL_PENDING_REQUEST'}).waitFor({state:'visible'});
  assert.match(await page.locator('.prfkt-update-card').filter({hasText:'AGENTS_REAL_PENDING_REQUEST'}).innerText(),/AGENTS_REAL_PENDING_REQUEST/);
  assert.ok(await page.evaluate(()=>window.fixturePermissionIds.includes('agents-popup')));
  assert.equal(await page.locator('[data-cw-inbox-id="agents-popup"]').count(),1,'popup request remains in the authoritative permission inbox');
  assert.doesNotMatch(await page.locator('.prfkt-update-card').first().innerHTML(),new RegExp(reviewToken));
  const beforeAgentsDismiss=allPosts.length;
  await page.evaluate(()=>document.querySelectorAll('.prfkt-update-card .prfkt-dismiss').forEach(b=>b.click()));
  await page.waitForTimeout(350);
  assert.equal(allPosts.length,beforeAgentsDismiss);
  assert.equal(await page.locator('[data-cw-inbox-id="agents-popup"]').count(),1,'dismissal preserves the pending request');
  run.permissions=[];
  await page.locator('[data-cw-inbox-refresh]').click();
  await page.waitForFunction(()=>document.querySelectorAll('[data-cw-inbox-id]').length===0);
  await go('ai');
  console.log('CONVERSATION_PERMISSION_POPUPS_AI_AND_AGENTS=PASS');
  await go('home');
  append(run,'agent_message',native('home-message','specialist','REAL_AGENT_MESSAGE_WHILE_ON_HOME'));
  await page.locator('.prfkt-update-card').filter({hasText:'REAL_AGENT_MESSAGE_WHILE_ON_HOME'}).waitFor({state:'visible'});
  assert.equal(await page.locator('#cwAgentPopup').isVisible(),false,'one shared popup surface');
  await page.evaluate(()=>document.querySelectorAll('.prfkt-update-card .prfkt-dismiss').forEach(b=>b.click()));await page.waitForTimeout(350);
  await go('ai');
  console.log('CONVERSATION_NATIVE_OUTPUT_CONTINUES_ON_HOME=PASS');

  append(run,'message_delta',{...native('f','coordinator','FINAL_NATIVE_RESPONSE','final_answer'),output_kind:'agent_delta'});
  append(run,'agent_message',native('f','coordinator','FINAL_NATIVE_RESPONSE','final_answer'));
  append(run,'message',{message_id:'persisted-final',role:'assistant',agent_id:'coordinator',text:'FINAL_NATIVE_RESPONSE',native_message_ids:['c','f']});
  conversation.messages.push({id:'persisted-final',role:'assistant',text:'FINAL_NATIVE_RESPONSE',run_id:run.id});
  run.status='completed';
  await page.waitForFunction(()=>document.querySelector('#codexRunState')?.textContent==='Completed');
  const completeText=await page.locator('#chatlog').innerText();
  assert.equal(completeText.split('FINAL_NATIVE_RESPONSE').length-1,1,'persisted final replaces its native duplicate');
  assert.equal(completeText.split('Coordinator commentary').length-1,1,'commentary survives final native-message IDs');
  assert.equal(completeText.split('Specialist finding').length-1,1);
  await page.evaluate(()=>document.querySelectorAll('.prfkt-update-card .prfkt-dismiss').forEach(b=>b.click()));
  await page.waitForTimeout(350);

  console.log('CONVERSATION_SETTINGS_POPUPS_FINAL=PASS');
  // Reopening a newly recorded history after page load must not replay a popup.
  const replay={id:'recorded-replay',status:'completed',events:[],permissions:[],agents:run.agents};
  append(replay,'agent_started',{id:'specialist',role:'reviewer',status:'completed'});
  append(replay,'agent_message',native('replay-message','specialist','RECORDED_HISTORY_NO_POPUP'));
  runs.set(replay.id,replay);
  conversations.set('replay-conversation',{id:'replay-conversation',title:'Recorded replay fixture',project_key:'joeos',messages:[],run_ids:[replay.id]});
  await page.locator('#loadChat').click();
  await page.waitForFunction(()=>[...document.querySelector('#codexConversation').options].some(o=>o.value==='replay-conversation'),{timeout:20000});
  await page.locator('#codexConversation').selectOption('replay-conversation');
  await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('RECORDED_HISTORY_NO_POPUP'));
  assert.equal(await page.locator('.prfkt-update-card').first().isVisible(),false);

  await page.locator('[data-cw-view="graph"]').click();
  await page.locator('#codexNewConversation').click();
  assert.equal(await page.locator('[data-cw-view="chat"]').getAttribute('aria-pressed'),'true','blank conversation returns to Chat');
  assert.equal(await page.locator('#chatlog').isVisible(),true);
  await page.locator('#codexConversation').selectOption('conversation-fixture');
  await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('FINAL_NATIVE_RESPONSE'));
  await page.locator('[data-cw-view="terminal"]').click();
  await page.waitForFunction(()=>document.querySelector('#cwTerminalLog')?.textContent.includes('BUILD_STDOUT_MARKER'));
  await page.locator('#login').click();
  await page.waitForFunction(()=>document.querySelector('#cwTerminalLog')?.textContent==='');
  assert.equal(await page.locator('.prfkt-update-card').first().isVisible(),false);
  assert.equal(await page.locator('.cw-workspace-bar').isVisible(),false,'owner toolbar remains hidden after logout');
  assert.equal(await page.locator('#cwTerminal').isVisible(),false);
  assert.equal(await page.locator('#codexTraceStudio').isVisible(),false);
  assert.equal(await page.locator('#chatlog').isVisible(),true,'collaborator conversation area is restored');
  assert.equal(requests.filter(r=>r.path.endsWith('/message')).length,0,'presentation controls never launch work');
  assert.deepEqual(allPosts,['/api/codex-workspace/permissions/mobile-review/decision'],'only the deliberately tested permission decision mutates a fake API');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log(JSON.stringify({ok:true,checks:['default-chat','interleaved-native-text','commentary-final-dedup','terminal-stdout-stderr-exit','bounded-output','mobile-context','graph-review','device-layout','popup-preference','history-replay','blank-conversation','logout-cleanup','no-execution','embedded-nebula','synthetic-orientation','reduced-motion-reset','permission-popups-ai-agents','removed-depth-motion-controls'],screenshots:out}));

} catch(error) {
  console.error(JSON.stringify({fixtureErrors:errors}));
  if(browser)for(const p of browser.contexts().flatMap(c=>c.pages()))console.error(JSON.stringify(await p.evaluate(()=>({classes:document.querySelector('#ai')?.className,media:matchMedia('(max-width:760px)').matches,mode:document.querySelector('#cwLayoutPreference')?.value})).catch(()=>({}))));
  throw error;
} finally {
  await browser?.close();server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve));
}
