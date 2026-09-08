import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('./home.js',import.meta.url),'utf8');
const start=source.indexOf('  function syncExternalReviewIdentity');
const end=source.indexOf('\n  async function refreshHomeApprovals()',start);
assert.ok(start>0&&end>start,'review renderer source missing');
const fragment=source.slice(start,end);
const approvalEl={innerHTML:''};
const projectFilter={value:''};
const context={
  tasks:[{id:'t1',project:'DASH_BYTE / VITROS'}],
  runs:[],accessKey:'TEST_ONLY_REVIEW_KEY',session:{level:2},homeApprovalState:[],
  homeExternalReviewIdentity:JSON.stringify(['TEST_ONLY_REVIEW_KEY','','',2]),
  homeExternalReviewRequest:0,homeExternalReviewController:null,
  homeExternalReviewRefreshBusy:false,homeExternalReviewUpdatedAt:0,AbortController,
  homeExternalReviewState:{state:'fresh',items:[{
    source:'external-review',project:'DASH_BYTE / VITROS',repo:'jmw7629/vitros-web-dashboard',number:251,
    title:'Header <hardening>',updated_age_seconds:300,draft:false,review_decision:'',merge_state:'CLEAN',
    checks:{passed:4,pending:0,failed:0,unknown:0},url:'https://github.com/jmw7629/vitros-web-dashboard/pull/251',
    evidence_state:'fresh',evidence_age_seconds:0,read_only:true
  }],repositories:{}},
  document:{getElementById:id=>id==='homeApprovals'?approvalEl:id==='projectFilter'?projectFilter:null},
  escH:s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  console
};
vm.createContext(context);vm.runInContext(fragment+'\nthis.renderApprovals=renderApprovals;',context,{timeout:1000});
context.renderApprovals(context.tasks);
assert.match(approvalEl.innerHTML,/external review/);
assert.match(approvalEl.innerHTML,/PR #251/);
assert.match(approvalEl.innerHTML,/Header &lt;hardening&gt;/);
assert.match(approvalEl.innerHTML,/4 passed/);
assert.match(approvalEl.innerHTML,/CLEAN/);
assert.match(approvalEl.innerHTML,/Open GitHub PR/);
assert.match(approvalEl.innerHTML,/target="_blank" rel="noopener"/);
assert.match(approvalEl.innerHTML,/Read-only observation/);
assert.doesNotMatch(approvalEl.innerHTML,/data-home-approval|data-review-action|data-home-run/);
assert.doesNotMatch(approvalEl.innerHTML,/>Approve review<|>Request changes<|>Terminal<|>Merge<|>Close<|>Rerun<|>Deploy</);
assert.doesNotMatch(approvalEl.innerHTML,/StickDeath-Infinity-/);
projectFilter.value='STICKDEATH_BYTE';context.renderApprovals(context.tasks);
assert.doesNotMatch(approvalEl.innerHTML,/PR #251/,'project filter must scope external review cards');
context.homeExternalReviewState={state:'unavailable',items:[],repositories:{}};projectFilter.value='';context.renderApprovals(context.tasks);
assert.match(approvalEl.innerHTML,/not assuming the queue is empty/);
context.homeExternalReviewState={state:'partial',items:[],repositories:{}};context.renderApprovals(context.tasks);
assert.match(approvalEl.innerHTML,/evidence is partial/);
context.homeExternalReviewState={state:'stale',items:[],repositories:{}};context.renderApprovals(context.tasks);
assert.match(approvalEl.innerHTML,/evidence is stale/);
assert.match(source,/\.approval-item\.external-review \.approval-actions a\{min-height:44px/);
assert.match(source,/max-width:100%;overflow-wrap:anywhere/);
console.log('EXTERNAL_REVIEW_CARD_RENDER=PASS');
console.log('EXTERNAL_REVIEW_READ_ONLY_CONTROLS=PASS');
console.log('EXTERNAL_REVIEW_SCOPE_FILTER=PASS');
console.log('EXTERNAL_REVIEW_UNAVAILABLE_NOT_EMPTY=PASS');
console.log('EXTERNAL_REVIEW_MOBILE_TAP_AND_OVERFLOW_CONTRACT=PASS');

// Deferred reads deliberately ignore abort to prove callback ownership as well as cancellation.
function raceHarness(){
  const card={innerHTML:''},reads=[];
  const c={...context,accessKey:'TEST_ONLY_A',session:{level:2,subject:'fixture-a'},
    homeExternalReviewIdentity:'',homeExternalReviewRequest:0,homeExternalReviewController:null,
    homeExternalReviewRefreshBusy:false,homeExternalReviewUpdatedAt:0,
    homeExternalReviewState:{state:'unavailable',items:[],repositories:{}},
    document:{getElementById:id=>id==='homeApprovals'?card:id==='projectFilter'?{value:''}:null},
    safeVisible:()=>context.tasks,
    api:(_url,options)=>new Promise((resolve,reject)=>reads.push({resolve,reject,signal:options.signal}))};
  vm.createContext(c);vm.runInContext(fragment,c,{timeout:1000});
  return {c,card,reads};
}
function evidence(title){return {state:'fresh',repositories:{},items:[{
  project:'DASH_BYTE / VITROS',repo:'jmw7629/vitros-web-dashboard',number:251,title,
  url:'https://github.com/jmw7629/vitros-web-dashboard/pull/251',checks:{},evidence_state:'fresh'
}]};}
{
  const {c,card,reads}=raceHarness(),old=c.refreshExternalReviews(true);
  c.accessKey=''; // The actual logout handler clears this before load updates session.
  reads[0].resolve(evidence('LATE_PRIVATE_FIXTURE'));await old;c.renderApprovals(c.tasks);
  assert.equal(reads[0].signal.aborted,true);assert.doesNotMatch(card.innerHTML,/LATE_PRIVATE_FIXTURE/);
  c.session={level:0};await c.refreshExternalReviews(true);assert.equal(reads.length,1);
}
for(const settle of ['resolve','reject']){
  const {c,card,reads}=raceHarness(),old=c.refreshExternalReviews(true);
  c.accessKey='TEST_ONLY_B';c.session={level:2,subject:'fixture-b'};
  const replacement=c.refreshExternalReviews();assert.equal(reads.length,2,'new account does not inherit busy/throttle');
  assert.equal(reads[0].signal.aborted,true);
  reads[1].resolve(evidence('CURRENT_B_FIXTURE'));await replacement;
  reads[0][settle](settle==='resolve'?evidence('LATE_A_FIXTURE'):new Error('old request failed'));await old;
  assert.match(card.innerHTML,/CURRENT_B_FIXTURE/);assert.doesNotMatch(card.innerHTML,/LATE_A_FIXTURE|TEST_ONLY_/);
}
{
  const {c,reads}=raceHarness(),old=c.refreshExternalReviews(true);
  c.accessKey='TEST_ONLY_B';const replacement=c.refreshExternalReviews(true);
  reads[0].reject(new Error('old request failed'));await old;
  await c.refreshExternalReviews(true);assert.equal(reads.length,2,'old finally cannot unlock a newer request');
  reads[1].resolve(evidence('CURRENT_FIXTURE'));await replacement;
  const failed=c.refreshExternalReviews(true);reads[2].reject(new Error('current request failed'));await failed;
  assert.equal(c.homeExternalReviewState.state,'unavailable','current failure retains truthful unavailable behavior');
}
{
  const {c,card,reads}=raceHarness(),old=c.refreshExternalReviews(true);
  c.accessKey='';c.session={level:0};c.renderApprovals(c.tasks);
  c.accessKey='TEST_ONLY_A';c.session={level:2,subject:'fixture-a'};
  const replacement=c.refreshExternalReviews(true);
  reads[0].resolve(evidence('ORIGINAL_A_FIXTURE'));await old;
  assert.doesNotMatch(card.innerHTML,/ORIGINAL_A_FIXTURE/,'same-account sign-in cannot revive an earlier generation');
  reads[1].resolve(evidence('NEW_A_FIXTURE'));await replacement;
  c.accessKey='TEST_ONLY_OTHER';c.renderApprovals(c.tasks);
  assert.doesNotMatch(card.innerHTML,/NEW_A_FIXTURE/,'rendering clears cached evidence on key changes');
}
console.log('EXTERNAL_REVIEW_LOGOUT_AND_ACCOUNT_SWITCH_RESPONSES=PASS');
console.log('EXTERNAL_REVIEW_SUPERSEDED_FAILURE_AND_FINALLY=PASS');
console.log('EXTERNAL_REVIEW_CREDENTIALS_NEVER_RENDERED=PASS');
