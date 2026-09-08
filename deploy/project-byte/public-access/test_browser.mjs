import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {fileURLToPath} from 'node:url';
import {spawn} from 'node:child_process';
import {randomBytes} from 'node:crypto';
const {chromium}=await import(process.env.PB_PLAYWRIGHT_MODULE||'playwright');
const here=path.dirname(fileURLToPath(import.meta.url)),source=path.resolve(here,'../v4');
const root=await fs.mkdtemp(path.join(os.tmpdir(),'pb-public-browser-'));
const owner='1234',publicOwner=randomBytes(32).toString('base64url'),children=[];let browser,logs='';
async function port(){return new Promise(resolve=>{const server=net.createServer();server.listen(0,'127.0.0.1',()=>{const p=server.address().port;server.close(()=>resolve(p));});});}
async function concat(dir){return (await Promise.all((await fs.readdir(path.join(source,dir))).filter(x=>x.endsWith('.part')).sort().map(x=>fs.readFile(path.join(source,dir,x),'utf8')))).join('');}
function start(args){const child=spawn('python3',args,{cwd:root,env:{...process.env,HOME:root,XDG_CONFIG_HOME:path.join(root,'config'),XDG_DATA_HOME:path.join(root,'data'),XDG_CACHE_HOME:path.join(root,'cache'),PATH:path.join(root,'bin')+path.delimiter+process.env.PATH,KANBAN_HOST:'127.0.0.1',KANBAN_PORT:String(backendPort),PYTHONDONTWRITEBYTECODE:'1'},stdio:['ignore','pipe','pipe']});children.push(child);for(const stream of [child.stdout,child.stderr])stream.on('data',b=>logs=(logs+b.toString()).slice(-3000));return child;}
const backendPort=await port(),gatewayPort=await port(),upstream=`http://127.0.0.1:${backendPort}`,base=`http://127.0.0.1:${gatewayPort}`;
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function ready(url){for(let i=0;i<100;i++){try{const r=await fetch(url);if(r.ok)return;}catch{}await sleep(100);}throw new Error('Fixture did not start: '+logs);}
try{
 await fs.writeFile(path.join(root,'backend.py'),await concat('server'));
 await fs.copyFile(path.join(source,'runtime_server.py'),path.join(root,'server.py'));
 await fs.copyFile(path.join(source,'execution_permissions.py'),path.join(root,'execution_permissions.py'));
 let html=await concat('index');
 for(const name of ['home.js','home-inspector.js','health-runtime.js']){await fs.copyFile(path.join(source,name),path.join(root,name));if(!html.includes(`<script src="/${name}"></script>`))html=html.replace('</body>',`<script src="/${name}"></script></body>`);}
 await fs.writeFile(path.join(root,'index.html'),html);await fs.writeFile(path.join(root,'admin.secret'),owner,{mode:0o600});await fs.mkdir(path.join(root,'bin'));
 await fs.writeFile(path.join(root,'public-owner.secret'),publicOwner,{mode:0o600});
 await fs.writeFile(path.join(root,'bin','gh'),'#!/bin/sh\nexit 2\n',{mode:0o700});
 start([path.join(root,'server.py')]);await ready(upstream+'/healthz');
 const viewerResponse=await fetch(upstream+'/api/team',{method:'POST',headers:{'X-Access-Key':owner,'Content-Type':'application/json'},body:JSON.stringify({name:'Public viewer fixture',role:'viewer'})});assert.equal(viewerResponse.status,201);const viewer=(await viewerResponse.json()).access_key;
 const launcher=`import sys\nsys.path.insert(0,${JSON.stringify(here)})\nfrom gateway import Access,BoundedServer,handler_for\nserver=BoundedServer(('127.0.0.1',${gatewayPort}),handler_for(Access(${JSON.stringify(root)},public_owner_key_file=${JSON.stringify(path.join(root,'public-owner.secret'))}),${JSON.stringify(base)},('127.0.0.1',${backendPort})))\nserver.serve_forever()\n`;
 await fs.writeFile(path.join(root,'gateway_fixture.py'),launcher);start([path.join(root,'gateway_fixture.py')]);await ready(base+'/signin');
 assert.equal((await fetch(base+'/api/tasks')).status,401);
 assert.equal((await fetch(base+'/_gateway/login',{method:'POST',headers:{'Origin':base,'Content-Type':'application/json','X-Project-Byte-Gateway':'1'},body:JSON.stringify({key:owner})})).status,401,'private four-character owner key cannot sign in publicly');
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'});const page=await context.newPage(),errors=[],external=[];
 context.on('page',p=>p.on('pageerror',e=>errors.push({url:p.url(),stack:e.stack||e.message})));page.on('pageerror',e=>errors.push({url:page.url(),stack:e.stack||e.message}));
 // The sign-in document remains active while its asynchronous request is pending.
 await context.route(base+'/_gateway/login',async route=>{await sleep(100);await route.continue();});
 context.on('request',request=>{if(!request.url().startsWith(base)&&!request.url().startsWith('data:'))external.push(request.url());});
 await page.goto(base);await page.waitForSelector('#signin');
 const out=process.env.PB_SCREENSHOTS||path.join(root,'screenshots');await fs.mkdir(out,{recursive:true});
 for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:900});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1));await page.screenshot({path:path.join(out,`public-signin-${width}.png`)});}
 await page.locator('#key').fill(publicOwner);await page.getByRole('button',{name:'Sign in',exact:true}).click();await page.waitForSelector('#home.active');await page.waitForFunction(()=>typeof session!=='undefined'&&session.ok&&session.level===4);
 assert.notEqual(await page.evaluate(()=>sessionStorage.getItem('project_byte_access')),owner,'raw owner key never retained in public browser storage');
 assert.notEqual(await page.evaluate(()=>sessionStorage.getItem('project_byte_access')),publicOwner,'public owner key is replaced by a session nonce');
 const originalStarted=await page.evaluate(()=>sessionStorage.getItem('pb_login_at'));
 assert.equal((await page.evaluate(async()=>{const r=await fetch('/healthz');return r.status;})),200,'headerless health request acquires session nonce');
 await page.reload();await page.waitForFunction(()=>typeof session!=='undefined'&&session.level===4);assert.equal(await page.evaluate(()=>sessionStorage.getItem('pb_login_at')),originalStarted,'reload does not reset TTL');
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});
  for(const view of ['portfolio','board','agents','ai','settings','models','team','terminalView','activity','intelligence','help','home']){
   await page.locator('#pbWorkspaceButton').click();await page.locator(`#pbWorkspaces [data-home-go="${view}"]`).click();await page.waitForSelector(`#${view}.active`);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth+1),`${view}/${width} fits`);
  }
 }
 const created=await page.evaluate(async()=>{const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({title:'Public gateway fixture only',project:'Fixture',owner:'Joe',status:'Backlog',priority:'Medium'})});return {status:r.status,value:await r.json()};});assert.equal(created.status,201);
 const taskId=created.value.id;
 const upload=await page.evaluate(async taskId=>{const r=await fetch('/api/upload',{method:'POST',headers:{'Content-Type':'text/html','X-Filename':'fixture.txt','X-Task-ID':taskId},body:new Blob(['<script>not executable</script>'],{type:'text/html'})});return {status:r.status,value:await r.json()};},taskId);assert.equal(upload.status,201);
 const download=await page.evaluate(async url=>{const r=await fetch(url);return {status:r.status,type:r.headers.get('Content-Type'),disposition:r.headers.get('Content-Disposition'),csp:r.headers.get('Content-Security-Policy'),body:await r.text()};},upload.value.url);
 assert.equal(download.status,200);assert.equal(download.type,'application/octet-stream');assert.match(download.disposition,/attachment;/);assert.match(download.csp,/sandbox/);assert.match(download.body,/not executable/);
 assert.equal((await fetch(base+upload.value.url)).url,base+'/signin','anonymous attachment redirects to sign in');
 const tab2=await context.newPage();await tab2.goto(base);await tab2.waitForFunction(()=>typeof session!=='undefined'&&session.level===4);assert.equal(await page.evaluate(()=>session.level),4,'new tab does not log out original');
 const oldNonce=await page.evaluate(()=>sessionStorage.getItem('project_byte_access'));
 await tab2.goto(base+'/signin');await tab2.locator('#key').fill(viewer);await tab2.getByRole('button',{name:'Sign in',exact:true}).click();await tab2.waitForFunction(()=>typeof session!=='undefined'&&session.level===1);
 await page.waitForFunction(()=>typeof session!=='undefined'&&session.level===1);assert.equal(await tab2.evaluate(()=>session.role),'viewer');
 const cookies=await context.cookies();const cookie=cookies.find(c=>c.name==='__Host-project_byte_session');
 assert.equal((await fetch(base+'/api/tasks',{headers:{Cookie:cookie.name+'='+cookie.value,'X-Access-Key':oldNonce}})).status,401);
 assert.equal((await tab2.evaluate(async()=>{const r=await fetch('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});return r.status;})),403,'viewer role still enforced by unchanged app');
 assert.equal((await tab2.evaluate(async()=>{const r=await fetch('/api/tasks');return r.status;})),200,'stale tab cannot revoke current account');
 await tab2.locator('#login').click();await tab2.waitForSelector('#signin');await page.waitForSelector('#signin');
 assert.equal((await fetch(base+upload.value.url,{headers:{Cookie:cookie.name+'='+cookie.value},redirect:'manual'})).status,303,'captured old cookie is revoked after logout');
 assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
 console.log('PUBLIC_SIGNIN_12_WORKSPACES_4_WIDTHS_CREATE_UPLOAD_DOWNLOAD=PASS');
 console.log('PUBLIC_HEALTH_SESSION_TTL_NEW_TAB_ACCOUNT_SWITCH_VIEWER_LOGOUT=PASS');
 console.log('PUBLIC_PRIVATE_DATA_AND_ATTACHMENTS_REQUIRE_AUTH=PASS');
 console.log('PUBLIC_STRONG_OWNER_ALIAS_PRESERVES_PRIVATE_SHORT_KEY=PASS');
}catch(error){console.error('PUBLIC_BROWSER_FAILED',error,logs);process.exitCode=1;}
finally{if(browser)await browser.close();for(const child of children)child.kill('SIGTERM');await Promise.all(children.map(child=>new Promise(resolve=>child.exitCode!==null||child.signalCode?resolve():child.once('exit',resolve))));}
