import assert from 'node:assert/strict';
import path from 'node:path';
const artist='6aGxmrOqjSpDGvIJdId29O',album='a'.repeat(22),track='1'.repeat(22);
const sdk=`window.Spotify={Player:class {
 constructor(o){this.o=o;this.h={};this.calls=[];this.state=null;window.__musicFixture=this;}
 addListener(k,f){this.h[k]=f;}
 connect(){this.o.getOAuthToken(()=>queueMicrotask(()=>this.h.ready({device_id:'FIXTURE_DEVICE'})));return Promise.resolve(true);}
 disconnect(){this.calls.push('disconnect');}
 activateElement(){this.calls.push('activate');return Promise.resolve();}
 emit(paused=false){this.state={paused,repeat_mode:1,position:0,track_window:{current_track:{id:'${track}',uri:'spotify:track:${track}',name:'Fixture <track> & title',artists:[{name:'Saxon Shore'}],album:{images:[]}}}};this.h.player_state_changed(this.state);}
 pause(){this.calls.push('pause');this.emit(true);return Promise.resolve();}
 seek(n){this.calls.push('seek:'+n);this.emit(true);return Promise.resolve();}
 resume(){this.calls.push('resume');this.emit(false);return Promise.resolve();}
}};window.onSpotifyWebPlaybackSDKReady();`;
export async function verifySpotify({browser,base,out,key}){
 const context=await browser.newContext({viewport:{width:390,height:844},reducedMotion:'reduce'}),page=await context.newPage();
 const errors=[],calls=[],outside=[];let heldPlay=null,holdNext=false;
 const cors={'Access-Control-Allow-Origin':base,'Access-Control-Allow-Headers':'authorization,content-type','Access-Control-Allow-Methods':'GET,PUT,POST,OPTIONS'};
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://sdk.scdn.co/spotify-player.js',r=>r.fulfill({contentType:'text/javascript',body:sdk}));
 await page.route('https://accounts.spotify.com/api/token',r=>{calls.push({path:'token',body:r.request().postData()});return r.fulfill({headers:cors,json:{access_token:'FIXTURE_ONLY_SPOTIFY',refresh_token:'FIXTURE_REFRESH',expires_in:3600}});});
 await page.route('https://api.spotify.com/**',async r=>{
  const u=new URL(r.request().url()),method=r.request().method();if(method==='OPTIONS')return r.fulfill({status:204,headers:cors});calls.push({path:u.pathname,query:u.search,method,body:r.request().postData()});
  if(method==='GET')return r.fulfill({headers:cors,json:u.pathname.includes('/artists/')?{items:[{id:album,name:'Fixture album',release_date:'2005',artists:[{id:artist}]}],next:null}:{items:[{id:track,name:'Fixture title',track_number:1,artists:[{id:artist}]}],next:null}});
  if(u.pathname.endsWith('/play')&&holdNext){heldPlay=r;return;}
  await r.fulfill({status:204,headers:cors});if(u.pathname.endsWith('/play'))await page.evaluate(()=>window.__musicFixture.emit(false));
 });
 await page.route('**/*',async r=>{const u=r.request().url();if(u.startsWith(base)||/^https:\/\/(sdk\.scdn\.co|accounts\.spotify\.com|api\.spotify\.com)\//.test(u))return r.fallback();outside.push(u);await r.abort();});
 try{
  await page.goto(base+'/#home');await page.waitForSelector('#homeMusic');
  assert.equal(await page.locator('[data-hm=play]').isDisabled(),true,'signed-out cannot start a music connection');
  assert.equal(calls.length,0,'no Spotify requests before explicit connection');
  page.on('dialog',d=>d.accept(key));await page.locator('#login').click();await page.waitForFunction(()=>document.querySelector('#login').textContent.includes('owner'));
  await page.locator('[data-hm=settings]').click();await page.locator('#hmClientId').fill('not-a-client-secret');await page.locator('#hmConnect').click();await page.waitForFunction(()=>document.querySelector('#hmDialogStatus').textContent.includes('Client ID'));
  assert.equal(calls.length,0);await page.locator('#hmClose').click();
  // Simulated Spotify response through the real public callback document, with a real PKCE binding.
  await page.evaluate(async()=>{const raw=JSON.stringify([accessKey,session.subject,session.name,session.level]);const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(raw));const bound=btoa(String.fromCharCode(...new Uint8Array(hash))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');sessionStorage.setItem('prfkt.spotify.pending',JSON.stringify({clientId:'a'.repeat(32),verifier:'v'.repeat(43),state:'TEST_STATE',bound,redirect:location.origin+'/spotify-callback',at:Date.now()}));});
  await page.goto(base+'/spotify-callback?code=FIXTURE_CODE&state=TEST_STATE');await page.waitForURL(base+'/#home');await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status')?.textContent.includes('Ready'));
  assert.equal(await page.evaluate(()=>sessionStorage.getItem('prfkt.spotify.callback')),null);assert.equal(await page.evaluate(()=>sessionStorage.getItem('prfkt.spotify.pending')),null);
  await page.locator('[data-hm=play]').click();await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent==='Playing · repeat all');
  assert.ok(calls.some(c=>c.path.endsWith('/repeat')&&c.query.includes('state=context')&&c.query.includes('device_id=FIXTURE_DEVICE')));
  assert.ok(calls.some(c=>c.path.endsWith('/shuffle')&&c.query.includes('state=false')));
  assert.deepEqual(JSON.parse(calls.find(c=>c.path.endsWith('/play')).body).uris,['spotify:track:'+track]);
  assert.equal(await page.locator('#homeMusic .hm-title').textContent(),'Fixture <track> & title');assert.equal(await page.locator('#homeMusic track').count(),0,'metadata renders as text');
  await page.locator('[data-hm=pause]').click();await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent==='Paused');
  const playCount=calls.filter(c=>c.path.endsWith('/play')).length;await page.locator('[data-hm=play]').click();await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent==='Playing · repeat all');assert.equal(calls.filter(c=>c.path.endsWith('/play')).length,playCount,'resume does not replace the queue');
  await page.locator('[data-hm=stop]').click();await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent==='Stopped');assert.deepEqual(await page.evaluate(()=>window.__musicFixture.calls.slice(-2)),['pause','seek:0']);
  for(const width of [320,390,768,1440]){await page.setViewportSize({width,height:width<768?844:1000});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),'no overflow at '+width);const box=await page.locator('#homeMusic').boundingBox(),hero=await page.locator('.home-hero-top').boundingBox();assert.ok(box.x+box.width<=hero.x+hero.width+1);await page.screenshot({path:path.join(out,'spotify-'+width+'.png'),fullPage:false});}
  await page.locator('#pbWorkspaceButton').click();await page.locator('#pbWorkspaces [data-home-go=portfolio]').click();await page.waitForSelector('#portfolio.active');assert.ok(await page.evaluate(()=>window.__musicFixture.calls.includes('seek:0')));assert.ok(!(await page.evaluate(()=>window.__musicFixture.calls.includes('disconnect'))),'navigation preserves player');
  await page.locator('#pbWorkspaceButton').click();await page.locator('#pbWorkspaces [data-home-go=home]').click();await page.waitForSelector('#home.active');
  // A late Play response must be followed by Stop, never resume after Stop completes.
  await page.evaluate(()=>window.__musicFixture.h.player_state_changed({paused:true,track_window:{current_track:{id:'9'.repeat(22),uri:'spotify:track:'+'9'.repeat(22),name:'Other song',artists:[],album:{images:[]}}}}));holdNext=true;await page.locator('[data-hm=play]').click();await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent.startsWith('Starting'));
  for(let n=0;n<50&&!heldPlay;n++)await new Promise(r=>setTimeout(r,20));assert.ok(heldPlay);await page.locator('[data-hm=stop]').click();await heldPlay.fulfill({status:204,headers:cors});await page.waitForFunction(()=>document.querySelector('#homeMusic .hm-status').textContent==='Stopped');assert.deepEqual(await page.evaluate(()=>window.__musicFixture.calls.slice(-2)),['pause','seek:0']);
  await page.locator('#login').click();await page.waitForFunction(()=>sessionStorage.getItem('prfkt.spotify.tokens')===null);assert.ok(await page.evaluate(()=>window.__musicFixture.calls.includes('disconnect')));
  assert.deepEqual(errors,[]);assert.deepEqual(outside,[]);console.log('SPOTIFY_BROWSER_FIXTURE_CONTROLS_REPEAT_CALLBACK_LOGOUT_AND_FOUR_WIDTHS=PASS');
 }catch(e){console.error('SPOTIFY_FIXTURE_STATE',await page.locator('#homeMusic').innerText().catch(()=>''),JSON.stringify(calls.map(c=>({path:c.path,method:c.method}))),errors);throw e;}finally{await context.close();}
}
