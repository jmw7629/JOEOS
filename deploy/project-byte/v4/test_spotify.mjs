import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
const source=fs.readFileSync(new URL('./home.js',import.meta.url),'utf8').split('// PRFKT_SPOTIFY_PLAYER_BEGIN')[1];
const context={module:{exports:{}},crypto:webcrypto,TextEncoder,URL,URLSearchParams,AbortController,btoa,fetch,setTimeout,clearTimeout};
vm.runInNewContext('// PRFKT_SPOTIFY_PLAYER_BEGIN'+source,context);
const {SpotifySession,ARTIST,sha}=context.module.exports;
const memory=()=>{const m=new Map();return {getItem:k=>m.get(k)||null,setItem:(k,v)=>m.set(k,v),removeItem:k=>m.delete(k)};};
const response=(data,status=200,headers={})=>({ok:status>=200&&status<300,status,headers:new Headers(headers),json:async()=>data});
const make=(fetcher,extra={})=>new SpotifySession({fetcher,storage:memory(),origin:'https://workspace.example',clock:()=>1000000,...extra});
const authorize=s=>{s.tokens={access_token:'TEST_ONLY',refresh_token:'TEST_REFRESH',expiresAt:2000000,clientId:'a'.repeat(32),bound:'bound'};};
test('PKCE binds a random challenge and callback to the signed-in workspace without a client secret',async()=>{
 const s=make(()=>assert.fail('authorization should only navigate'));
 const url=new URL(await s.authorize('a'.repeat(32),'owner-session'));
 assert.equal(url.origin,'https://accounts.spotify.com');assert.equal(url.searchParams.get('code_challenge_method'),'S256');
 const p=s.read('pending');assert.equal(url.searchParams.get('code_challenge'),await sha(p.verifier));assert.equal(p.bound,await sha('owner-session'));assert.equal(p.redirect,'https://workspace.example/spotify-callback');assert.ok(!url.searchParams.has('client_secret'));
 const first=p.state;await s.authorize('a'.repeat(32),'owner-session');assert.notEqual(s.read('pending').state,first);
});
test('invalid app IDs and insecure non-loopback origins are rejected before storing authorization',async()=>{
 const s=make(()=>{});await assert.rejects(s.authorize('secret','owner'),/Client ID/);
 const insecure=make(()=>{},{origin:'http://100.99.71.65:8094'});await assert.rejects(insecure.authorize('a'.repeat(32),'owner'),/HTTPS/);assert.equal(insecure.read('pending'),null);
});
for(const defect of ['state','owner','expired','future','denied'])test('OAuth rejects '+defect+' before exchanging code',async()=>{
 const s=make(()=>assert.fail('must not exchange rejected code'));await s.authorize('a'.repeat(32),'owner');const p=s.read('pending');
 if(defect==='expired')p.at-=600001;if(defect==='future')p.at+=1;s.save('pending',p);
 s.save('callback',{state:defect==='state'?'other':p.state,code:'TEST_CODE',error:defect==='denied'?'access_denied':null});
 await assert.rejects(s.restore(defect==='owner'?'other':'owner'));assert.equal(s.read('callback'),null);assert.equal(s.read('pending'),null);
});
test('OAuth uses a one-time code and verifier, stores only a tab-bound session, and clears all credentials',async()=>{
 let call;const s=make(async(u,o)=>{call={u,o};return response({access_token:'TEST_ACCESS',refresh_token:'TEST_REFRESH',expires_in:3600});});
 await s.authorize('a'.repeat(32),'owner');const p=s.read('pending');s.save('callback',{state:p.state,code:'TEST_CODE'});assert.equal(await s.restore('owner'),true);
 assert.equal(call.o.body.get('code_verifier'),p.verifier);assert.equal(call.o.credentials,'omit');assert.equal(call.o.referrerPolicy,'no-referrer');assert.equal(s.read('pending'),null);assert.equal(s.read('tokens').bound,await sha('owner'));
 s.clear();assert.equal(s.tokens,null);assert.equal(s.read('tokens'),null);
});
test('another workspace owner cannot recover the saved music session',async()=>{
 const s=make(()=>assert.fail('no token exchange'));s.save('tokens',{bound:await sha('ownerA'),clientId:'a'.repeat(32),access_token:'TEST_ONLY',expiresAt:2000000});assert.equal(await s.restore('ownerB'),false);assert.equal(s.read('tokens'),null);
});
test('concurrent token refresh is single-flight, rotating the refresh token',async()=>{
 let count=0,finish;const s=make(()=>{count++;return new Promise(r=>finish=r);});authorize(s);s.tokens.expiresAt=0;
 const a=s.token(),b=s.token();assert.equal(count,1);finish(response({access_token:'NEW_TEST_ACCESS',refresh_token:'NEW_TEST_REFRESH',expires_in:3600}));assert.deepEqual(await Promise.all([a,b]),['NEW_TEST_ACCESS','NEW_TEST_ACCESS']);assert.equal(s.tokens.refresh_token,'NEW_TEST_REFRESH');
});
test('sign-out during token exchange cannot resurrect credentials',async()=>{
 let finish;const s=make(()=>new Promise(r=>finish=r));authorize(s);s.tokens.expiresAt=0;const pending=s.token();s.clear();finish(response({access_token:'LATE_TEST',expires_in:3600}));await assert.rejects(pending,/session changed/);assert.equal(s.read('tokens'),null);assert.equal(s.tokens,null);
});
test('429 is surfaced, never blindly replaying a playback mutation',async()=>{
 let calls=0;const s=make(async()=>{calls++;return response({},429,{'Retry-After':'20'});});authorize(s);await assert.rejects(s.api('/me/player/play?device_id=test','PUT',{uris:[]}),/rate limiting/);await assert.rejects(s.api('/me/player/play?device_id=test','PUT',{}),/rate limiting/);assert.equal(calls,1);
});
test('catalog pagination refuses an external continuation without sending it a bearer token',async()=>{
 let calls=0;const s=make(async()=>{calls++;return response({items:[],next:'https://attacker.example/v1/artists/'+ARTIST+'/albums'});});authorize(s);await assert.rejects(s.discography(),/Unexpected Spotify/);assert.equal(calls,1);
});
test('catalog pagination rejects cycles and incomplete responses instead of playing a partial discography',async()=>{
 const s=make(async()=>response({items:[],next:'https://api.spotify.com/v1/artists/'+ARTIST+'/albums?include_groups=album,single&limit=50'}));authorize(s);await assert.rejects(s.discography(),/pagination/);
 const malformed=make(async()=>response({error:'malformed'}));authorize(malformed);await assert.rejects(malformed.discography(),/unavailable/);
});
test('discography follows release and track order, pages albums and skips unavailable/other-artist/duplicate tracks',async()=>{
 const a='a'.repeat(22),b='b'.repeat(22),one='1'.repeat(22),two='2'.repeat(22),three='3'.repeat(22);
 const album=(id,date)=>({id,name:id,release_date:date,artists:[{id:ARTIST}]});
 const track=(id,number,extra={})=>({id,name:id,track_number:number,disc_number:1,artists:[{id:ARTIST}],...extra});
 const s=make(async u=>{
  const url=new URL(u);
  if(url.pathname.includes('/artists/'))return url.searchParams.has('offset')?response({items:[album(a,'2003')],next:null}):response({items:[album(b,'2009')],next:'https://api.spotify.com/v1/artists/'+ARTIST+'/albums?offset=1'});
  if(url.pathname.includes(a))return response({items:[track(two,2),track(one,1)],next:null});
  return response({items:[track(two,1),track(three,2,{is_playable:false}),track('4'.repeat(22),3,{artists:[{id:'other'}]}),track('5'.repeat(22),4)],next:null});
 });authorize(s);const list=await s.discography();assert.deepEqual(Array.from(list,x=>x.uri),[one,two,'5'.repeat(22)].map(x=>'spotify:track:'+x));
});
test('oversized catalog fails explicitly rather than silently truncating',async()=>{
 const s=make(async u=>response(u.includes('/artists/')?{items:[{id:'a'.repeat(22),name:'Album',artists:[{id:ARTIST}]}]}:{items:Array.from({length:101},(_,i)=>({id:String(i).padStart(22,'0'),name:'Track',artists:[{id:ARTIST}]}))}));authorize(s);await assert.rejects(s.discography(),/not truncated/);
});

test('bounded network timeout reports uncertainty and does not retry a playback write',async()=>{
 let calls=0;const s=make((u,o)=>{calls++;return new Promise((resolve,reject)=>o.signal.addEventListener('abort',()=>reject(new Error('aborted'))));},{timeoutMs:5});authorize(s);
 await assert.rejects(s.api('/me/player/play?device_id=test','PUT',{}),/Check playback before retrying/);assert.equal(calls,1);
});
