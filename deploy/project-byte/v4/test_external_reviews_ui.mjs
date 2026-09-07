import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('./home.js',import.meta.url),'utf8');
const start=source.indexOf('  function externalReviewAge');
const end=source.indexOf('\n  async function refreshHomeApprovals()',start);
assert.ok(start>0&&end>start,'review renderer source missing');
const fragment=source.slice(start,end);
const approvalEl={innerHTML:''};
const projectFilter={value:''};
const context={
  tasks:[{id:'t1',project:'DASH_BYTE / VITROS'}],
  runs:[],session:{level:2},homeApprovalState:[],
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
