import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const {chromium}=await import(process.env.PB_PLAYWRIGHT_MODULE||'playwright');
const source=path.dirname(fileURLToPath(import.meta.url));
// UI contract fixture only. Python integration tests exercise the real provider adapters.
let defaultKey='codex/default',connected=true,failDiscovery=false;
const key='AI_CONNECTIONS_UI_FIXTURE_ONLY',requests=[],errors=[];
const model=(model_key,display_name,provider,model_name)=>({model_key,display_name,provider,model_name,endpoint:'',notes:'UI fixture only',enabled:true,local:provider==='ollama',success_count:0,failure_count:0,owner_only:true});
let models=[model(defaultKey,'Codex default','codex-chatgpt','fixture-thinking'),model('old/model','Existing model','ollama','old-installed-model')],agents=[];
const providers=[
 {id:'codex-chatgpt',label:'Codex · ChatGPT subscription',kind:'agent',auth_modes:['subscription'],protocol:'codex',default_endpoint:'',discovery:true,notes:'Uses official sign-in already present on this server.'},
 {id:'openai-responses',label:'OpenAI API',kind:'cloud',auth_modes:['api_key','server_env'],protocol:'openai-responses',default_endpoint:'https://api.openai.com/v1',discovery:true,notes:'API usage is billed separately from ChatGPT.'},
 {id:'ollama',label:'Ollama',kind:'local',auth_modes:['none','api_key'],protocol:'ollama',default_endpoint:'http://127.0.0.1:11434',discovery:true,notes:'Discover the models installed on your own server.'},
 {id:'openai-compatible',label:'Custom compatible provider',kind:'cloud',auth_modes:['api_key','server_env'],protocol:'openai-compatible',default_endpoint:'',discovery:true,notes:'Add any supported endpoint and model ID.'},
 {id:'hermes',label:'Hermes agent',kind:'agent',auth_modes:['api_key'],protocol:'openai-compatible',default_endpoint:'',discovery:true,notes:'Connect an existing Hermes agent endpoint.'},
 {id:'openclaw',label:'OpenClaw agent',kind:'agent',auth_modes:['api_key'],protocol:'openai-compatible',default_endpoint:'',discovery:true,notes:'Connect an existing OpenClaw gateway.'}
];
const settings=()=>({general:{default_view:'home',refresh_seconds:60,show_completed:true},notifications:{},ai:{default_agent:'executive',default_model:defaultKey},security:{session_minutes:480},filters:{saved_views:[]}});
const files=new Map();
let html=(await Promise.all((await fs.readdir(path.join(source,'index'))).filter(x=>x.endsWith('.part')).sort().map(x=>fs.readFile(path.join(source,'index',x),'utf8')))).join('');
for(const name of ['ai-connections.js','home.js','home-inspector.js','health-runtime.js']){
 files.set('/'+name,{type:'text/javascript',body:await fs.readFile(path.join(source,name))});
 const tag=`<script src="/${name}"></script>`;if(!html.includes(tag))html=html.replace('</body>',tag+'</body>');
}
files.set('/',{type:'text/html',body:html});
const server=http.createServer(async(req,res)=>{
 try{
  const url=new URL(req.url,'http://fixture.local');
  if(files.has(url.pathname)){const file=files.get(url.pathname);res.writeHead(200,{'Content-Type':file.type});res.end(file.body);return;}
  let body='';for await(const chunk of req)body+=chunk;
  const data=body?JSON.parse(body):{},credential=req.headers['x-access-key'];
  const level=credential===key?4:credential==='VIEWER_UI_FIXTURE'?1:credential==='ADMIN_UI_FIXTURE'?3:0;
  const send=(value,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(value));};
  if(url.pathname==='/api/session')return send({ok:level>0,level,subject:level?'fixture/'+level:'',name:level===4?'Fixture owner':'Fixture collaborator',role:level===4?'owner':level===3?'admin':level?'viewer':'public'});
  if(url.pathname.startsWith('/api/ai-connections')&&level!==4)return send({error:'Owner sign-in required'},403);
  if(url.pathname==='/api/ai-connections')return send({providers,subscription_runtimes:[{id:'claude-code',label:'Claude Code',notes:'Requires the native subscription adapter.',connectable:false,state:'adapter_not_configured'}],agents:[{id:'hermes',label:'Hermes',provider:'hermes',notes:'Connect an existing Hermes agent endpoint.'},{id:'openclaw',label:'OpenClaw',provider:'openclaw',notes:'Connect an existing OpenClaw gateway.'}],connections:models,default_model_key:defaultKey,codex:{available:true,connected,auth_mode:connected?'chatgpt':null,detail:connected?'Official server sign-in is ready.':'Authorize this server with your ChatGPT account.'}});
  if(url.pathname==='/api/ai-connections/discover'){
   requests.push({path:url.pathname,data});if(failDiscovery)return send({error:'<img src=x onerror="window.CONNECTION_XSS=true"> provider unavailable'},502);
   return send({provider:data.provider,models:data.provider==='ollama'?[{id:'workstation-model:32b',name:'Workstation model 32B'},{id:'another-installed:latest',name:'Another installed model'}]:data.provider==='codex-chatgpt'?[{id:'fixture-thinking',name:'Codex fixture model'}]:[],empty:false});
  }
  if(url.pathname==='/api/ai-connections/connect'){
   requests.push({path:url.pathname,data});const model_key='new/'+models.length,display_name=data.display_name||data.model_name;
   models.push(model(model_key,display_name,data.provider,data.model_name));if(data.set_default)defaultKey=model_key;
   if(data.agent_template)agents.push({agent_key:data.agent_template,name:data.agent_template,role:'External agent',enabled:true});
   return send({model_key,display_name,provider:data.provider,model_name:data.model_name,agent_key:data.agent_template||undefined,default_set:data.set_default,owner_only:true});
  }
  if(url.pathname==='/api/ai-connections/default'){requests.push({path:url.pathname,data});defaultKey=data.model_key;return send({model_key:defaultKey,default_set:true});}
  if(url.pathname==='/api/ai-connections/login'){requests.push({path:url.pathname,data});return send({login:{state:'pending',verification_url:'https://auth.openai.com/codex/device',user_code:'FIXTURE-CODE',message:'Complete official sign-in.'},codex:{available:true,connected:false,auth_mode:null}});}
  if(url.pathname==='/api/models')return send({models});
  if(url.pathname==='/api/agents')return send({agents});
  if(url.pathname==='/api/settings')return send({settings:settings()});
  if(url.pathname==='/api/intelligence')return send({top:[],signals:{}});
  if(url.pathname==='/api/observatory')return send({traces:[],tools:[],models:[],agents:[],summary:{}});
  if(url.pathname==='/healthz')return send({ok:true,version:'fixture',components:{},readiness:{}});
  const field=url.pathname.split('/').pop();return send({ok:true,[field]:[],items:[],repositories:{},traces:[],settings:settings()});
 }catch(error){res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:error.message}));}
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const base=`http://127.0.0.1:${server.address().port}`;
let browser;
const out=process.env.PB_SCREENSHOTS||await fs.mkdtemp(path.join(os.tmpdir(),'pb-ai-connections-ui-'));await fs.mkdir(out,{recursive:true});
try{
 browser=await chromium.launch({headless:true,...(process.env.PB_BROWSER_PATH?{executablePath:process.env.PB_BROWSER_PATH}:{})});
 const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});
 const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));page.on('dialog',dialog=>dialog.accept(dialog.type()==='prompt'?key:undefined));
 await page.addInitScript(key=>sessionStorage.setItem('project_byte_access',key),key);
 await page.goto(base+'/#models',{waitUntil:'networkidle'});await page.waitForFunction(()=>session.level===4);
 await page.locator('#addModel').click();await page.locator('#aiConnectionModal.open').waitFor();
 assert.equal(await page.locator('#modelModal.open').count(),0,'legacy model dialog does not also open');
 assert.equal(await page.locator('#aiConnectionProvider').inputValue(),'codex-chatgpt');
 assert.equal(await page.locator('#aiConnectionModel').evaluate(input=>input.readOnly),true,'Codex model IDs come only from the verified detected list');
 assert.equal(await page.locator('#aiConnectionModelHint').textContent(),'Choose a detected model verified for workspace chat.');
 assert.match(await page.locator('#aiConnectionSubscriptionList').textContent(),/Claude Code.*Native adapter not connected/);
 assert.equal(await page.locator('#aiConnectionProvider option[value="claude-code"]').count(),0,'unavailable subscription adapters are not fabricated connectable providers');
 assert.match(await page.locator('#aiConnectionCodexStatus').textContent(),/Connected/);
 assert.equal(await page.locator('#aiConnectionSecret').isVisible(),false,'subscription sign-in never asks for pasted tokens');
 await page.locator('#aiConnectionDiscover').click();await page.waitForFunction(()=>document.querySelector('#aiConnectionModel').value==='fixture-thinking');
 assert.equal(await page.locator('#aiConnectionDetected option').count(),2);
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),'page fits '+width);
  const bounds=await page.locator('.ai-connection-dialog').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width+1,'dialog fits '+width);
  await page.locator('.ai-connection-dialog').screenshot({path:path.join(out,`ai-connections-${width}.png`)});
 }
 await page.locator('#aiConnectionProvider').selectOption('ollama');
 assert.equal(await page.locator('#aiConnectionModel').evaluate(input=>input.readOnly),false,'Ollama retains manual model IDs');
 assert.equal(await page.locator('#aiConnectionDetected option').count(),1,'switching provider clears old discovered models');
 await page.locator('#aiConnectionEndpoint').fill('http://100.99.71.66:11434');await page.locator('#aiConnectionDiscover').click();
 await page.waitForFunction(()=>document.querySelector('#aiConnectionDetected option[value="workstation-model:32b"]'));
 await page.locator('#aiConnectionDetected').selectOption('workstation-model:32b');
 assert.equal(await page.locator('#aiConnectionModel').inputValue(),'workstation-model:32b');
 const discover=requests.filter(r=>r.path.endsWith('/discover')).at(-1);assert.equal(discover.data.endpoint,'http://100.99.71.66:11434');assert.equal(discover.data.provider,'ollama');
 await page.locator('#aiConnectionName').fill('My workstation');await page.locator('#aiConnectionSave').click();await page.locator('#aiConnectionModal.open').waitFor({state:'hidden'});
 await page.waitForFunction(()=>document.querySelector('#modelGrid').textContent.includes('My workstation'));
 assert.equal(await page.evaluate(()=>settings.ai.default_model),defaultKey,'new default applies to existing settings');
 await page.locator('[data-ai-default="old/model"]').click();await page.waitForFunction(()=>settings.ai.default_model==='old/model');assert.equal(defaultKey,'old/model');
 await page.locator('#addModel').click();await page.locator('#aiConnectionProvider').selectOption('openai-compatible');
 assert.equal(await page.locator('#aiConnectionModel').evaluate(input=>input.readOnly),false,'Custom providers retain manual model IDs');
 assert.equal(await page.locator('#aiConnectionModelHint').textContent(),'A model missing from the list can be added by its exact ID.');
 await page.locator('#aiConnectionEndpoint').fill('https://custom-provider.example/v1');await page.locator('#aiConnectionSecret').fill('PRIVATE_FIXTURE_API_KEY');
 await page.locator('#aiConnectionModel').fill('model-not-in-any-catalog');await page.locator('#aiConnectionName').fill('<img src=x onerror="window.CONNECTION_XSS=true">');
 await page.locator('#aiConnectionDefault').uncheck();await page.locator('#aiConnectionSave').click();await page.locator('#aiConnectionModal.open').waitFor({state:'hidden'});
 const custom=requests.filter(r=>r.path.endsWith('/connect')).at(-1);assert.equal(custom.data.api_key,'PRIVATE_FIXTURE_API_KEY');assert.equal(custom.data.model_name,'model-not-in-any-catalog');
 assert.equal(await page.locator('#aiConnectionSecret').inputValue(),'');
 assert.equal(await page.evaluate(()=>[...Object.values(sessionStorage),...Object.values(localStorage)].some(value=>String(value).includes('PRIVATE_FIXTURE_API_KEY'))),false,'API keys never stored in browser storage');
 assert.equal(await page.evaluate(()=>!!window.CONNECTION_XSS),false);assert.equal(await page.locator('#modelGrid img').count(),0);
 await page.locator('#addModel').click();await page.locator('#aiConnectionProvider').selectOption('hermes');await page.locator('#aiConnectionAgent').selectOption('hermes');
 await page.locator('#aiConnectionEndpoint').fill('https://hermes.example/v1');await page.locator('#aiConnectionSecret').fill('AGENT_FIXTURE_KEY');await page.locator('#aiConnectionModel').fill('agent');await page.locator('#aiConnectionSave').click();await page.locator('#aiConnectionModal.open').waitFor({state:'hidden'});
 assert.equal(requests.filter(r=>r.path.endsWith('/connect')).at(-1).data.agent_template,'hermes');assert.ok(await page.evaluate(()=>agents.some(a=>a.agent_key==='hermes')));
 await page.locator('#addModel').click();await page.locator('#aiConnectionProvider').selectOption('openai-responses');await page.locator('#aiConnectionSecret').fill('CLEAR_ON_METHOD_CHANGE');await page.locator('#aiConnectionAuth').selectOption('server_env');
 assert.equal(await page.locator('#aiConnectionSecret').inputValue(),'');assert.equal(await page.locator('#aiConnectionSecret').isVisible(),false);assert.equal(await page.locator('#aiConnectionEnv').isVisible(),true);
 failDiscovery=true;await page.locator('#aiConnectionDiscover').click();await page.waitForFunction(()=>document.querySelector('#aiConnectionDiscoveryStatus').textContent.includes('provider unavailable'));assert.equal(await page.locator('#aiConnectionDiscoveryStatus img').count(),0);assert.equal(await page.evaluate(()=>!!window.CONNECTION_XSS),false);failDiscovery=false;
 await page.locator('#aiConnectionClose').click();connected=false;await page.locator('[data-ai-refresh]').click();await page.waitForFunction(()=>document.querySelector('#aiConnections h3').textContent.includes('sign-in required'));
 await page.locator('#addModel').click();await page.locator('#aiConnectionLogin').click();await page.locator('#aiConnectionDevice a').waitFor();assert.equal(await page.locator('#aiConnectionDevice a').getAttribute('href'),'https://auth.openai.com/codex/device');assert.equal(await page.locator('.ai-device-code').textContent(),'FIXTURE-CODE');
 connected=true;await page.locator('#aiConnectionLoginRefresh').click();await page.waitForFunction(()=>document.querySelector('#aiConnectionCodexStatus').textContent.includes('Connected'));assert.equal(await page.locator('#aiConnectionDevice').isVisible(),false);
 await page.locator('#aiConnectionClose').click();
 // A pending response must not restore a private discovered model after logout.
 let release,arrived;const requested=new Promise(resolve=>arrived=resolve);
 await page.route(base+'/api/ai-connections/discover',async route=>{arrived();await new Promise(resolve=>release=resolve);try{await route.fulfill({contentType:'application/json',body:JSON.stringify({models:[{id:'LATE_PRIVATE_MODEL',name:'Late private model'}]})});}catch(error){if(!/closed|cancel|abort|invalid interception/i.test(error.message))throw error;}});
 await page.locator('#addModel').click();await page.locator('#aiConnectionDiscover').click();await requested;
 // Actual logout handler, triggered without bypassing the modal's visual stacking.
 await page.evaluate(()=>document.querySelector('#login').click());await page.waitForFunction(()=>session.level===0&&!accessKey);
 release();await page.waitForTimeout(150);
 assert.equal(await page.locator('#aiConnectionModal.open').count(),0);assert.equal(await page.locator('#aiConnections [data-ai-open]').count(),0);assert.ok(!(await page.locator('#aiConnectionDetected').textContent()).includes('LATE_PRIVATE_MODEL'));
 await page.unroute(base+'/api/ai-connections/discover');
 for(const [credential,level] of [['VIEWER_UI_FIXTURE',1],['ADMIN_UI_FIXTURE',3]]){
  await page.evaluate(credential=>{accessKey=credential;sessionStorage.setItem('project_byte_access',credential);return load();},credential);await page.waitForFunction(level=>session.level===level,level);
  assert.equal(await page.locator('#addModel').isVisible(),false);assert.equal(await page.locator('#aiConnections [data-ai-open]').count(),0);assert.equal(await page.locator('[data-ai-default]').count(),0);
 }
 assert.deepEqual(errors,[]);
 console.log('AI_CONNECTIONS_SUBSCRIPTION_CUSTOM_OLLAMA_AGENTS_DEFAULTS_AND_4_WIDTHS=PASS');
 console.log('AI_CONNECTIONS_OWNER_ROLES_POST_ONLY_SECRETS_XSS_AND_LATE_LOGOUT=PASS');
 console.log('Screenshots: '+out);
}finally{await browser?.close();server.closeAllConnections?.();await new Promise(resolve=>server.close(resolve));}
