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
const submitted=new Map();let uncertainNext=false;
const taskRows=[{id:'task-a',title:'Alpha task',project:'Alpha',status:'Open',depends_on:''},{id:'dependency',title:'Prerequisite',project:'Alpha',status:'Open',depends_on:''},{id:'task-b',title:'Dependent task',project:'Beta',status:'Open',depends_on:'dependency'}];
const dashboardProjects=[{name:'Alpha',repo:'fixture/joeos'},{name:'Beta',repo:'fixture/memory'},{name:'Unmapped',repo:'fixture/unknown'}];
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
      if(url.pathname==='/api/codex-workspace/message'){
        if(submitted.has(body.request_id))return send(submitted.get(body.request_id),202);
        if([...runs.values()].some(r=>['working','queued','waiting_approval','stopping'].includes(r.status)))return send({error:'A Codex task is active. Stop it or wait for completion before starting another.'},409);
        const n=submitted.size+1,cid=body.conversation_id||'conversation-'+n,rid='run-'+n;
        let c=conversations.get(cid);if(c&&c.project_key!==body.project_key)return send({error:'Project mismatch'},400);
        if(!c){c={id:cid,title:'Chat '+n,project_key:body.project_key,messages:[],run_ids:[]};conversations.set(cid,c);}
        c.messages.push({id:'owner-'+n,role:'user',text:body.message,run_id:rid});c.run_ids.unshift(rid);
        runs.set(rid,{id:rid,status:'working',events:[],permissions:[],agents:[]});
        const response={conversation_id:cid,run_id:rid,project_key:body.project_key};submitted.set(body.request_id,response);
        if(uncertainNext){uncertainNext=false;return send({error:'Temporary response failure after acceptance'},503);}return send(response,202);
      }
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
    if(url.pathname==='/api/tasks')return send({tasks:taskRows});
    if(url.pathname==='/api/projects')return send({projects:dashboardProjects});
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
  await page.addInitScript(()=>localStorage.setItem('prfkt.updates.popups','off'));
  await page.goto(base+'/#ai');
  await page.waitForFunction(()=>document.querySelector('#cwWorkTabs button')&&typeof tasks!=='undefined'&&tasks.length===3);
  const selected=()=>page.locator('#cwWorkTabs [aria-selected="true"]').getAttribute('data-work-room');
  const switchTo=async id=>{await page.locator('[data-work-room="'+id+'"]').click();await page.waitForFunction(id=>document.querySelector('[data-work-room="'+id+'"]')?.getAttribute('aria-selected')==='true',id);await page.waitForTimeout(100);};
  const work=async scope=>{await page.evaluate(scope=>window.PRFKT_CODEX.openWork(scope),scope);return selected();};
  const submit=async message=>{await page.locator('#chatInput').fill(message);await page.locator('#sendChat').click();};
  const waitCount=async n=>{for(let i=0;i<120&&submitted.size<n;i++)await page.waitForTimeout(100);assert.equal(submitted.size,n);};
  const finish=async id=>{const r=runs.get(id);r.status='completed';append(r,'run_completed',{});};
  const a=await work({projectName:'Alpha'});await submit('ALPHA_REQUEST');await waitCount(1);
  assert.equal([...submitted.values()][0].project_key,'joeos');
  await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('ALPHA_REQUEST'));
  assert.doesNotMatch(await page.locator('#chatlog').innerText(),/Work context|fixture\/joeos/,'context has a dedicated disclosure, not a JSON chat bubble');
  await page.locator('#chatInput').fill('ALPHA_UNSENT_DRAFT');
  await go('home');
  assert.equal(await page.locator('#cwWorkJump').isVisible(),true);
  const b=await work({projectName:'Beta'});assert.notEqual(a,b);assert.equal(await page.locator('#chatInput').inputValue(),'');
  await submit('BETA_REQUEST');await page.waitForTimeout(300);assert.equal(submitted.size,1,'second window waits for single runner');
  append(runs.get('run-1'),'agent_message',{message_id:'alpha-progress',agent_id:'coordinator',phase:'commentary',text:'ALPHA_BACKGROUND_OUTPUT'});
  await page.waitForFunction(()=>document.querySelector('#cwWorkTabs [aria-selected="false"]')?.parentElement.textContent.includes('●'));
  assert.doesNotMatch(await page.locator('#chatlog').innerText(),/ALPHA_BACKGROUND_OUTPUT|ALPHA_REQUEST/);
  await finish('run-1');await waitCount(2);
  const bodies=requests.filter(r=>r.path.endsWith('/message')).map(r=>r.body);
  assert.equal(bodies[1].project_key,'memory');assert.equal(bodies[1].conversation_id,null);assert.match(bodies[1].message,/BETA_REQUEST/);assert.doesNotMatch(bodies[1].message,/ALPHA_REQUEST/);
  await switchTo(a);await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('ALPHA_BACKGROUND_OUTPUT'));
  assert.equal(await page.locator('#chatInput').inputValue(),'ALPHA_UNSENT_DRAFT');
  assert.doesNotMatch(await page.locator('#chatlog').innerText(),/BETA_REQUEST/);
  await switchTo(b);await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('BETA_REQUEST'));
  // Saved updates reopen the originating conversation, regardless of the selected chat.
  await page.locator('#prfktUpdatesButton').click();await page.locator('#prfktUpdateList button').filter({hasText:'ALPHA_BACKGROUND_OUTPUT'}).click();
  await page.locator('#prfktUpdateDetail button').click();await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('ALPHA_BACKGROUND_OUTPUT'));
  assert.equal(await selected(),a);
  console.log('SCOPED_CONTEXT_BACKGROUND_DRAFT_AND_NOTIFICATION=PASS');

  const dependent=await work({taskId:'task-b',kind:'task'});await submit('DEPENDENT_REQUEST');
  await page.waitForTimeout(400);await page.reload();await page.waitForFunction(()=>document.querySelector('#cwWorkState')?.textContent.includes('resume needed'));
  assert.equal(await page.locator('#chatInput').inputValue(),'DEPENDENT_REQUEST','reload preserves queued draft');
  await finish('run-2');await page.waitForTimeout(3000);assert.equal(submitted.size,2,'restored pending work never starts automatically');
  await page.locator('#cwRetryQueued').click();
  await page.waitForFunction(()=>document.querySelector('#cwWorkState')?.textContent.includes('Prerequisite'));
  const taskA=await work({taskId:'task-a',kind:'task'});await submit('INDEPENDENT_TASK_REQUEST');await waitCount(3);
  assert.match(requests.filter(r=>r.path.endsWith('/message')).at(-1).body.message,/Alpha task/);
  assert.equal([...submitted.values()][2].project_key,'joeos','blocked dependency does not prevent unrelated ready work');
  taskRows.find(t=>t.id==='dependency').status='Done';await finish('run-3');await waitCount(4);
  assert.match(requests.filter(r=>r.path.endsWith('/message')).at(-1).body.message,/DEPENDENT_REQUEST/);
  await finish('run-4');
  console.log('SCOPED_RELOAD_EXPLICIT_RESUME_AND_DEPENDENCY_SCHEDULING=PASS');

  const unmapped=await work({projectName:'Unmapped'});assert.equal(await page.locator('#codexProject').inputValue(),'');assert.equal(await page.locator('#sendChat').isDisabled(),true);
  await page.waitForTimeout(1000);assert.equal(await page.locator('#codexProject').inputValue(),'','no periodic catalog fallback to the wrong workspace');
  const retry=await work({projectName:'Alpha',kind:'planning'});uncertainNext=true;await submit('UNCERTAIN_REQUEST');await waitCount(5);
  await page.waitForFunction(()=>document.querySelector('#cwWorkState')?.textContent.includes('Check submission'));
  const uncertainBody=requests.filter(r=>r.path.endsWith('/message')).at(-1).body;
  await page.waitForTimeout(200);await page.reload();await page.waitForFunction(()=>document.querySelector('#cwWorkState')?.textContent.includes('Check submission'));assert.equal(await page.locator('#cwCancelQueued').isVisible(),false,'reload cannot treat an uncertain accepted request as safely cancellable');
  await page.locator('#cwRetryQueued').click();await page.waitForFunction(()=>document.querySelector('#chatlog')?.textContent.includes('UNCERTAIN_REQUEST'));
  assert.equal(submitted.size,5);assert.deepEqual(requests.filter(r=>r.path.endsWith('/message')).at(-1).body,uncertainBody,'ambiguous submission retries exactly the same idempotent request');
  await finish('run-5');
  console.log('SCOPED_UNMAPPED_GUARD_AND_IDEMPOTENT_RETRY=PASS');

  for(const [width,height] of [[320,740],[390,844],[1440,1000]]){
    await page.setViewportSize({width,height});await go('home');await page.waitForTimeout(1100);
    const main=await page.locator('body > main.wrap').boundingBox(),jump=await page.locator('#cwWorkJump').boundingBox(),nav=await page.locator('#prfktNavToggle').boundingBox();
    assert.ok(jump.x>=0&&jump.x+jump.width<=width);if(width<=650){assert.ok(main.y+main.height<=jump.y+1);assert.ok(jump.x>=nav.x+nav.width,'chat shortcut does not cover navigation toggle');}
    await page.locator('#cwWorkJump').click();await page.locator('#ai.active').waitFor();
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    await page.screenshot({path:path.join(out,'work-chats-'+width+'.png')});
    for(const mode of ['terminal','graph','chat']){await page.locator('[data-cw-view="'+mode+'"]').click();const composer=await page.locator('#chatInput').boundingBox(),dock=await page.locator('#prfktNavDock').boundingBox();assert.ok(composer.y+composer.height<=dock.y+1,'composer remains reachable in '+mode+' at '+width);}
  }
  // Existing AI buttons enter scoped drafts without touching tasks or legacy execution routes.
  await page.evaluate(()=>{const b=document.createElement('button');b.dataset.aiProject='Beta';b.id='fixtureProjectAI';document.body.append(b);b.click();b.remove();});
  await page.waitForFunction(()=>document.querySelector('#cwWorkScope')?.textContent.includes('Beta'));
  assert.equal(await selected(),b);
  await page.evaluate(()=>{document.querySelector('#taskId').value='task-a';document.querySelector('#taskPrompt').value='TASK_BUTTON_DRAFT';document.querySelector('#queueTask').click();});
  await page.waitForFunction(()=>document.querySelector('#cwWorkScope')?.textContent.includes('Alpha task'));
  assert.equal(await selected(),taskA);
  assert.equal(allPosts.filter(p=>!p.startsWith('/api/codex-workspace/message')).length,0,'no legacy queue, task mutation, approval or stop request');
  await page.evaluate(()=>{document.querySelector('#askHelp').click();});await page.waitForFunction(()=>document.querySelector('#cwWorkScope')?.textContent.includes('Help'));assert.match(await page.locator('#chatInput').inputValue(),/Help me/);
  const stored=await page.evaluate(()=>Object.entries(localStorage).filter(([k])=>k.startsWith('prfkt.work-chats.')).map(([,v])=>v).join(''));
  assert.doesNotMatch(stored,/ALPHA_UNSENT_DRAFT|DEPENDENT_REQUEST|UNCERTAIN_REQUEST|NATIVE_TRACE_OWNER_FIXTURE_ONLY/);
  await page.evaluate(()=>{session={ok:false,level:0};accessKey='';});await page.waitForTimeout(1500);
  assert.equal(await page.locator('#cwWorkTabs button').count(),0);assert.equal(await page.locator('#cwWorkJump').isVisible(),false);
  await page.evaluate(key=>{accessKey=key;return load();},credential);await page.waitForFunction(()=>document.querySelector('#cwWorkTabs button'));assert.equal(await page.locator('#cwCloseWork').count(),1,'work controls survive sign-out and sign-in');
  assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
  console.log('SCOPED_MOBILE_ENTRY_POINTS_ENCRYPTED_DRAFTS_LOGOUT=PASS');
  console.log('SCOPED_WORK_CHAT_BROWSER=PASS');
}finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
