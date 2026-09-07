// Deterministic DOM-fixture interaction tests; no production requests or timers.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source=fs.readFileSync(new URL('./home-inspector.js',import.meta.url),'utf8');
const nodes=[];
class Element {
  constructor(tag='div'){
    this.tagName=tag;this.id='';this.innerHTML='';this.children=[];this.dataset={};
    const values=new Set();
    this.classList={add:k=>values.add(k),remove:k=>values.delete(k),contains:k=>values.has(k),toggle:(k,on)=>on?values.add(k):values.delete(k)};
    nodes.push(this);
  }
  appendChild(child){this.children.push(child);return child;}
  insertAdjacentElement(_where,child){this.children.push(child);}
  prepend(child){this.children.unshift(...(child.tagName==='fragment'?child.children:[child]));}
  querySelectorAll(){return this.children.map(child=>({remove:()=>{this.children=this.children.filter(x=>x!==child);}}));}
}
const graph=new Element();graph.id='homeAgentMap';
const runList=new Element();runList.id='runList';
const docEvents=[],windowEvents=new Map();
const holdHealth={components:{bridges:{stickdeath:{owner_paused:true,external_running:false,external_evidence_complete:true,private_path:'DO_NOT_RENDER'}}}};
const context={
  document:{head:new Element('head'),body:{dataset:{pbView:'home'}},
    createElement:tag=>new Element(tag),createDocumentFragment:()=>new Element('fragment'),
    getElementById:id=>nodes.find(x=>x.id===id)||null,querySelector:()=>null,querySelectorAll:()=>[],
    addEventListener:(_name,handler)=>docEvents.push(handler)},
  window:{__PROJECT_BYTE_HEALTH_LAST__:holdHealth,addEventListener:(name,fn)=>windowEvents.set(name,fn)},
  location:{hash:'#home'},runs:[],agents:[],models:[],tasks:[],session:{level:2},
  esc:value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
  setTimeout:fn=>fn(),console,
  api:()=>{throw new Error('Network API must not be called by owner-hold inspection');},
  fetch:()=>{throw new Error('No fetch allowed');}
};
vm.runInNewContext(source,context,{timeout:2000});
const panel=()=>nodes.find(x=>x.id==='homeAgentInspector');
const lens=()=>nodes.find(x=>x.id==='homeAgentLens');
function click(attr,value=''){
  const dataKey=attr.replace(/^data-/,'').replace(/-([a-z])/g,(_,c)=>c.toUpperCase());
  const target={dataset:{[dataKey]:value},closest:selector=>selector.includes(`[${attr}]`)?target:null,hasAttribute:name=>name===attr};
  const event={target,preventDefault(){},stopImmediatePropagation(){}};
  docEvents.forEach(fn=>fn(event));
}
function health(value){windowEvents.get('project-byte-health')({detail:value});}

assert.match(lens().innerHTML,/Paused by owner/);
assert.match(lens().innerHTML,/data-inspect-hold="stickdeath"/);
assert.doesNotMatch(lens().innerHTML,/data-inspect-run=/);
click('data-inspect-hold','stickdeath');
assert.equal(panel().classList.contains('open'),true);
assert.match(panel().innerHTML,/Queue and execution are disabled/);
assert.match(panel().innerHTML,/No active external executor is observed/);
assert.equal((panel().innerHTML.match(/<button /g)||[]).length,1,'Only Close is actionable in a hold inspector');
assert.doesNotMatch(panel().innerHTML,/DO_NOT_RENDER|data-inspector-terminal|href=/);
click('data-inspector-close');
assert.equal(panel().classList.contains('open'),false);
health(holdHealth);
assert.equal(runList.children.length,1);
assert.match(runList.children[0].innerHTML,/Paused by owner/);
assert.match(runList.children[0].innerHTML,/Inspect hold/);
click('data-inspect-hold','stickdeath');
health({components:{bridges:{stickdeath:{owner_paused:true,external_running:true,execution_source:'external-bridge',external_issue_number:3}}}});
assert.match(panel().innerHTML,/executor is still observed/);
assert.doesNotMatch(panel().innerHTML,/No active external executor is observed/);
health({components:{bridges:{stickdeath:{owner_paused:true,external_running:false,external_evidence_complete:false}}}});
assert.match(panel().innerHTML,/activity is not observable/);
assert.doesNotMatch(panel().innerHTML,/No active external executor is observed/);
health({components:{bridges:{}}});
assert.equal(panel().classList.contains('open'),false,'Lost hold evidence must not leave a stale inspector');
assert.doesNotMatch(lens().innerHTML,/Paused by owner/);
assert.equal(runList.children.length,0);
health({components:{bridges:{vitros_verifier:{external_running:true,execution_source:'external-bridge',external_issue_number:9,external_elapsed_seconds:60}}}});
assert.match(lens().innerHTML,/running \(external\)/);
assert.doesNotMatch(lens().innerHTML,/Paused by owner/);
assert.match(source,/@media\(max-width:650px\)\{\.home-inspector-grid\{grid-template-columns:repeat\(2,minmax\(0,1fr\)\)/);
assert.match(source,/\.home-inspector-close\{min-width:44px;min-height:44px\}/);
console.log('OWNER_HOLD_INSPECTOR_OPEN_CLOSE=PASS');
console.log('OWNER_HOLD_AGENTS_STATUS_READ_ONLY=PASS');
console.log('OWNER_HOLD_RUNNING_CHILD_NOT_HIDDEN=PASS');
console.log('OWNER_HOLD_STALE_PANEL_REMOVED=PASS');
console.log('VITROS_EXTERNAL_INSPECTOR_PRESERVED=PASS');
console.log('OWNER_HOLD_MOBILE_LAYOUT_CONTRACT=PASS (DOM fixture, not a device screenshot)');
