import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import {mkdir,writeFile,mkdtemp,rm} from 'node:fs/promises';
import path from 'node:path';import os from 'node:os';import assert from 'node:assert/strict';
const require=createRequire(import.meta.url),{chromium}=require(process.env.PB_PLAYWRIGHT_MODULE);
const here=path.dirname(fileURLToPath(import.meta.url));const python=process.env.PB_PYTHON||'python3';
const output=process.env.PB_EVIDENCE||await mkdtemp(path.join(os.tmpdir(),'enterprise-browser-'));await mkdir(output,{recursive:true});
const child=spawn(python,['-B','-u','-c',`import sys,json,signal\nsys.path.insert(0,${JSON.stringify(here)})\nfrom test_enterprise import Enterprise\nx=Enterprise();x.setUp()\nprint(json.dumps({'origin':x.origin}),flush=True)\ntry: signal.pause()\nexcept KeyboardInterrupt: pass\nfinally: x.tearDown()\n`],{stdio:['ignore','pipe','pipe']});
let logs='',buffer='';child.stderr.on('data',x=>logs+=x);const fixture=await new Promise((resolve,reject)=>{const timer=setTimeout(()=>reject(Error('Fixture timeout '+logs)),30000);child.stdout.on('data',x=>{buffer+=x;const line=buffer.split('\n').find(l=>l.startsWith('{'));if(line){clearTimeout(timer);resolve(JSON.parse(line));}});child.on('exit',c=>{clearTimeout(timer);reject(Error('Fixture exited '+c+' '+logs));});});
const browser=await chromium.launch({headless:true,executablePath:process.env.PB_BROWSER_PATH});const evidence={errors:[],viewports:[],fixtureProviderCalls:true};
try{
 const adminContext=await browser.newContext({viewport:{width:1440,height:1000}});const admin=await adminContext.newPage();admin.on('pageerror',e=>evidence.errors.push(e.message));
 await admin.goto(fixture.origin+'/signin');await admin.locator('#username').fill('owner');await admin.locator('#password').fill('correct-admin-password');await admin.locator('#signin button').click();await admin.waitForURL('**/admin');await admin.locator('#capacity').waitFor();
 for(const width of [390,1440]){
  await admin.locator('#label').fill('Browser '+width);await admin.locator('#invite button').first().click();await admin.waitForFunction(()=>document.querySelector('#inviteLink').value.includes('#invite='));const invitation=await admin.locator('#inviteLink').inputValue();await admin.evaluate(()=>document.querySelector('#inviteLink').value='');
  const context=await browser.newContext({viewport:{width,height:844}});const page=await context.newPage();page.on('pageerror',e=>evidence.errors.push(e.message));
  await page.goto(invitation);await page.locator('#joinName').fill('Reviewer '+width);await page.locator('#joinWorkspace').fill('Private '+width);await page.locator('#joinUsername').fill('reviewer'+width);await page.locator('#joinPassword').fill('correct-review-password');await page.locator('#join button').click();await page.waitForURL('**/#home');await page.waitForFunction(()=>typeof session!=='undefined'&&session.level===4);
  await page.waitForFunction(w=>document.title==='Private '+w,width);
  const api=async(route,body)=>page.evaluate(async({route,body})=>{const r=await fetch(route,{...(body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});return {status:r.status,data:await r.json()};},{route,body});
  const tasks=await api('/api/tasks');assert.deepEqual(tasks.data.tasks,[]);
  await page.getByRole('button',{name:'New work',exact:true}).click();await page.locator('#taskTitle').fill('Launch my first project');await page.locator('#taskProject').fill('My project '+width);await page.locator('#taskForm').getByRole('button',{name:'Save',exact:true}).click();await page.locator('#taskModal').waitFor({state:'hidden'});assert.equal((await api('/api/tasks')).data.tasks.length,1);
  await page.screenshot({path:path.join(output,`home-${width}.png`),fullPage:true});
  // Connect the disposable provider through the real Models dialog.
  await page.evaluate(()=>{location.hash='models';});await page.locator('#aiConnections').waitFor({state:'visible'});
  await page.locator('#aiConnections [data-ai-open]').click();
  await page.locator('#aiConnectionProvider').selectOption('openai-responses');await page.locator('#aiConnectionSecret').fill('disposable-fake-key');await page.locator('#aiConnectionModel').fill('fixture-model');await page.locator('#aiConnectionName').fill('Review provider');
  await page.locator('#aiConnectionSave').click();await page.locator('#aiConnectionModal').waitFor({state:'hidden'});
  await page.evaluate(()=>{location.hash='ai';});await page.locator('#chatInput').waitFor({state:'visible'});await page.evaluate(()=>document.querySelector('#codexRefresh').click());
  await page.waitForFunction(()=>!document.querySelector('#sendChat').disabled);
  await page.locator('#chatInput').fill('Remember this project plan');await page.locator('#sendChat').click();
  await page.evaluate(()=>{location.hash='projects';});await page.waitForTimeout(1000);await page.evaluate(()=>{location.hash='ai';});
  await page.waitForFunction(()=>document.querySelector('#chatlog').textContent.includes('Fixture response:'));
  await page.getByRole('button',{name:'History',exact:true}).click();
  await page.locator('#cwHistoryList button').first().waitFor();await page.locator('#cwHistoryDialog [data-close]').click();
  const chatText=await page.locator('#chatlog').innerText();assert.ok(chatText.includes('Fixture response:'));assert.equal(await page.locator('#chatlog .msg.assistant').count(),1,'one response, no duplicated native message');
  const native=(await api('/api/codex-workspace')).data;assert.equal(native.runtime_kind,'provider-chat');assert.equal(native.execution_permissions.capable,false);
  await page.screenshot({path:path.join(output,`chat-${width}.png`),fullPage:true});
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2),true,'no page overflow');
  evidence.viewports.push({width,invite:true,blank:true,projectCreated:true,providerConnected:true,durableChat:true,history:true,executionConnected:false});await context.close();
 }
 await admin.locator('#refresh').click();await admin.screenshot({path:path.join(output,'admin.png'),fullPage:true});assert.deepEqual(evidence.errors,[]);
}finally{await browser.close();child.kill('SIGINT');await new Promise(resolve=>child.once('exit',resolve));}
await writeFile(path.join(output,'browser.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(evidence));
