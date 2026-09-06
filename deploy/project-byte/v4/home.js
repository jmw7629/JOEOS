(() => {
  if (window.__PROJECT_BYTE_HOME__) return;
  window.__PROJECT_BYTE_HOME__ = true;

  const style = document.createElement('style');
  style.textContent = `
  :root{--home-blue:#70c8ff;--home-blue2:#3f78ff;--home-violet:#8f7cff;--home-green:#74d9a0;--home-red:#ff7e87;--home-amber:#efbd6a}
  body{padding-bottom:76px}
  body::after{content:"";position:fixed;inset:0;pointer-events:none;z-index:-1;background:radial-gradient(circle at 72% 12%,rgba(72,102,190,.11),transparent 27%),radial-gradient(circle at 18% 42%,rgba(57,83,145,.08),transparent 31%)}
  .home-shell{display:grid;gap:14px;max-width:1480px;margin:auto}
  .home-hero,.home-card,.home-kpi,.home-mini-card{background:linear-gradient(155deg,rgba(18,27,40,.91),rgba(10,16,25,.87));border:1px solid rgba(116,145,179,.22);box-shadow:inset 0 1px rgba(255,255,255,.035),0 18px 44px rgba(0,0,0,.20);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
  .home-hero{position:relative;overflow:hidden;border-radius:20px;padding:18px}
  .home-hero::after{content:"";position:absolute;width:260px;height:260px;right:-105px;top:-120px;border-radius:50%;border:1px solid rgba(112,200,255,.12);box-shadow:0 0 0 34px rgba(112,200,255,.018),0 0 0 68px rgba(143,124,255,.012);pointer-events:none}
  .home-eyebrow{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.12em;font-weight:700}
  .home-status-dot{width:8px;height:8px;border-radius:50%;background:var(--home-green);box-shadow:0 0 10px rgba(116,217,160,.48)}
  .home-hero-top{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}
  .home-hero h2{font-size:25px;letter-spacing:-.02em;margin:7px 0 4px}.home-hero p{margin:0;color:#b8c7d6}
  .home-ai-state{font-size:11px;border:1px solid rgba(116,217,160,.26);border-radius:999px;padding:6px 9px;color:#b9ebcb;background:rgba(56,112,78,.10);white-space:nowrap}
  .home-command{display:grid;grid-template-columns:1fr auto;gap:8px;margin-top:15px}.home-command input{width:100%;min-width:0;background:rgba(4,9,16,.62);border:1px solid rgba(112,200,255,.26);border-radius:13px;min-height:48px;padding:0 14px;color:var(--txt);font-size:16px;outline:none}.home-command input:focus{border-color:rgba(112,200,255,.72);box-shadow:0 0 0 3px rgba(112,200,255,.10)}
  .home-command button,.home-action,.home-nav button,.home-chip{border:1px solid var(--line);color:var(--txt);background:linear-gradient(180deg,rgba(26,38,53,.86),rgba(13,21,31,.84));box-shadow:inset 0 1px rgba(255,255,255,.04)}
  .home-command button{width:48px;border-radius:13px;font-size:20px}.home-actions{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.home-action{border-radius:11px;min-height:43px;padding:8px;font-size:12px;font-weight:650}.home-action:hover,.home-command button:hover,.home-chip:hover{border-color:#486c89}
  .home-kpis{display:grid;grid-template-columns:repeat(6,1fr);gap:9px}.home-kpi{border-radius:14px;padding:12px;min-width:0}.home-kpi span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.1em;font-weight:700}.home-kpi b{display:block;font-size:23px;margin-top:4px;letter-spacing:-.03em}.home-kpi small{display:block;color:var(--muted);font-size:10px;margin-top:2px}
  .home-kpi[data-kind="blocked"] b,.home-kpi[data-kind="critical"] b{color:#ffb1b7}.home-kpi[data-kind="ai"] b{color:#abd8ff}.home-kpi[data-kind="due"] b{color:#f1d39c}
  .home-scopes{display:flex;gap:7px;overflow:auto;padding:1px 1px 4px;scrollbar-width:none}.home-scopes::-webkit-scrollbar{display:none}.home-chip{border-radius:999px;padding:8px 11px;white-space:nowrap;font-size:11px}.home-chip.active{background:#173a54;border-color:#3678a2;color:#d5efff}
  .home-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:12px}.home-card{border-radius:17px;padding:14px;min-width:0}.home-card-head{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:11px}.home-card-head h3{font-size:14px;margin:0}.home-card-head button{background:transparent;border:0;color:var(--muted);min-width:32px;min-height:32px;border-radius:8px}.home-card-head button:hover{background:rgba(255,255,255,.04);color:var(--txt)}
  .agent-map{position:relative;height:238px;overflow:hidden;border-radius:13px;background:radial-gradient(circle at center,rgba(52,91,133,.13),transparent 44%),linear-gradient(rgba(255,255,255,.018) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.018) 1px,transparent 1px);background-size:auto,28px 28px,28px 28px}.agent-map svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.agent-map line{stroke:rgba(112,200,255,.22);stroke-width:1.2;stroke-dasharray:4 5}.agent-node{position:absolute;transform:translate(-50%,-50%);width:76px;min-height:58px;padding:8px 5px;border:1px solid rgba(123,151,181,.28);border-radius:13px;background:rgba(15,24,35,.94);color:var(--txt);text-align:center;font-size:10px;box-shadow:inset 0 1px rgba(255,255,255,.035)}.agent-node b{display:block;font-size:11px;margin-bottom:2px}.agent-node small{font-size:9px;color:var(--muted)}.agent-node.center{width:88px;min-height:68px;border-color:rgba(112,200,255,.50);box-shadow:0 0 24px rgba(56,139,198,.16),inset 0 1px rgba(255,255,255,.05)}.agent-node.running::before{content:"";position:absolute;top:7px;right:7px;width:6px;height:6px;background:var(--home-green);border-radius:50%}
  .org-map{display:grid;gap:9px}.org-top{text-align:center;padding:10px;border:1px solid rgba(112,200,255,.25);border-radius:12px;background:rgba(17,35,50,.36)}.org-top b{display:block}.org-row{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}.org-person{padding:10px 6px;border:1px solid var(--line);border-radius:11px;background:rgba(7,13,21,.46);text-align:center;font-size:10px;min-width:0}.org-person b{display:block;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.org-person small{color:var(--muted)}
  .home-lower{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px}.activity-feed,.work-list,.memory-feed,.approval-list{display:grid;gap:7px}.activity-item,.work-item,.memory-item,.approval-item{border:1px solid rgba(91,116,143,.22);background:rgba(6,12,20,.45);border-radius:11px;padding:9px;min-width:0}.activity-item{display:grid;grid-template-columns:auto 1fr;gap:9px;align-items:start}.activity-item time{font:10px ui-monospace,SFMono-Regular,Menlo,monospace;color:#7990a6}.activity-item b,.work-item b,.memory-item b,.approval-item b{font-size:11px;display:block}.activity-item span,.work-item span,.memory-item span,.approval-item span{font-size:10px;color:var(--muted);display:block;margin-top:2px}.work-item{cursor:pointer}.work-item:hover{border-color:#456a89}.work-top{display:flex;justify-content:space-between;gap:8px}.home-priority{font-size:9px;border-radius:999px;padding:2px 6px;border:1px solid var(--line);height:max-content}.home-priority.Critical{color:#ffb7bd;border-color:#633d42}.home-priority.High{color:#f1d09b;border-color:#5e4b2e}
  .approval-actions{display:flex;gap:6px;margin-top:7px}.approval-actions a,.approval-actions button{font-size:10px;min-height:31px;padding:5px 8px;border:1px solid var(--line);border-radius:8px;background:rgba(17,28,40,.75);color:var(--txt)}
  .home-nav{display:none;position:fixed;left:10px;right:10px;bottom:9px;z-index:40;background:rgba(8,13,20,.94);border:1px solid rgba(102,128,156,.28);border-radius:17px;padding:6px;grid-template-columns:repeat(5,1fr);box-shadow:0 16px 40px rgba(0,0,0,.4);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px)}.home-nav button{border:0;border-radius:11px;min-height:49px;padding:5px 3px;background:transparent;color:var(--muted);font-size:9px}.home-nav button i{display:block;font-style:normal;font-size:17px;line-height:20px}.home-nav button.active{background:rgba(66,111,150,.19);color:#d9f1ff}
  body[data-pb-view="home"]>.wrap>.metrics{display:none}
  @media(max-width:980px){.home-grid,.home-lower{grid-template-columns:1fr}.home-kpis{grid-template-columns:repeat(3,1fr)}.home-actions{grid-template-columns:repeat(2,1fr)}}
  @media(max-width:650px){body{padding-bottom:84px}header{position:relative;padding-bottom:9px}.tabs{display:none}.filters{display:none}.advancedfilters{margin-top:5px}.wrap{padding-top:10px}.home-shell{gap:10px}.home-hero{padding:14px;border-radius:17px}.home-hero h2{font-size:22px}.home-ai-state{display:none}.home-kpis{gap:6px}.home-kpi{padding:10px}.home-kpi b{font-size:20px}.home-card{padding:11px;border-radius:15px}.agent-map{height:224px}.home-nav{display:grid}.brand p{max-width:250px}.top{width:100%;display:grid;grid-template-columns:1fr auto auto}.top #newTask{display:none!important}.top #refresh{min-width:44px;font-size:0}.top #refresh::after{content:"↻";font-size:18px}.top #notifyBtn{font-size:0;min-width:44px}.top #notifyBtn::before{content:"◉";font-size:15px}.top #login{font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.home-lower{gap:10px}.org-row{gap:5px}.org-person{padding:8px 4px}}
  @media(max-width:380px){.home-kpis{grid-template-columns:repeat(2,1fr)}.home-command{grid-template-columns:1fr 46px}.home-actions{grid-template-columns:1fr 1fr}.agent-node{width:68px}.agent-node.center{width:78px}}
  `;
  document.head.appendChild(style);

  const tabs = document.querySelector('.tabs');
  const portfolioTab = tabs?.querySelector('[data-view="portfolio"]');
  if (tabs && !tabs.querySelector('[data-view="home"]')) {
    const homeTab = document.createElement('button');
    homeTab.className = 'tab';
    homeTab.dataset.view = 'home';
    homeTab.textContent = 'Home';
    tabs.insertBefore(homeTab, portfolioTab || tabs.firstChild);
  }

  const main = document.querySelector('main.wrap');
  const portfolio = document.getElementById('portfolio');
  const home = document.createElement('section');
  home.id = 'home';
  home.className = 'view';
  home.innerHTML = `
    <div class="home-shell">
      <section class="home-hero">
        <div class="home-hero-top">
          <div>
            <div class="home-eyebrow"><span class="home-status-dot"></span><span id="homeSystemState">All systems operational</span></div>
            <h2>Joe AI</h2>
            <p>Your executive AI command layer across projects, people, agents and infrastructure.</p>
          </div>
          <div id="homeAIState" class="home-ai-state">AI READY</div>
        </div>
        <div class="home-command"><input id="homeCommandInput" type="text" maxlength="1200" placeholder="Ask, create work, get status, or troubleshoot…"><button id="homeCommandSend" type="button" aria-label="Send to PROJECT_BYTE AI">→</button></div>
        <div class="home-actions">
          <button type="button" class="home-action" data-home-action="ask">Ask AI</button>
          <button type="button" class="home-action authonly" data-home-action="create">Queue work</button>
          <button type="button" class="home-action" data-home-action="agents">Review agents</button>
          <button type="button" class="home-action" data-home-action="troubleshoot">Troubleshoot</button>
        </div>
      </section>
      <section id="homeKpis" class="home-kpis"></section>
      <section id="homeScopes" class="home-scopes"></section>
      <section class="home-grid">
        <article class="home-card"><div class="home-card-head"><h3>Live agents</h3><button type="button" data-home-go="agents" aria-label="Open Agents">›</button></div><div id="homeAgentMap" class="agent-map"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>Team / org map</h3><button type="button" data-home-go="team" aria-label="Open Team">›</button></div><div id="homeOrgMap" class="org-map"></div></article>
      </section>
      <section class="home-lower">
        <article class="home-card"><div class="home-card-head"><h3>Current activity</h3><button type="button" data-home-go="activity" aria-label="Open Activity">›</button></div><div id="homeActivity" class="activity-feed"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>My work</h3><button type="button" data-home-go="board" aria-label="Open Kanban">›</button></div><div id="homeWork" class="work-list"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>Ready for review</h3><button type="button" data-home-go="agents" aria-label="Open review queue">›</button></div><div id="homeApprovals" class="approval-list"></div></article>
      </section>
      <section class="home-grid">
        <article class="home-card"><div class="home-card-head"><h3>Recent memories</h3><button type="button" data-home-go="agents" aria-label="Open memory">›</button></div><div id="homeMemory" class="memory-feed"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>Portfolio pulse</h3><button type="button" data-home-go="portfolio" aria-label="Open Portfolio">›</button></div><div id="homePortfolio"></div></article>
      </section>
    </div>`;
  if (main) main.insertBefore(home, portfolio || main.firstChild);

  const nav = document.createElement('nav');
  nav.className = 'home-nav';
  nav.setAttribute('aria-label','Primary mobile navigation');
  nav.innerHTML = `
    <button type="button" data-home-go="home"><i>⌂</i>Home</button>
    <button type="button" data-home-go="portfolio"><i>▣</i>Projects</button>
    <button type="button" data-home-go="agents"><i>◎</i>Agents</button>
    <button type="button" data-home-go="ai"><i>◇</i>Chat</button>
    <button type="button" data-home-go="settings" class="authonly"><i>⚙</i>Settings</button>`;
  document.body.appendChild(nav);

  function go(view) {
    const target = document.querySelector(`.tab[data-view="${view}"]`);
    if (target) target.click();
    else {
      document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === view));
      document.body.dataset.pbView = view;
    }
  }

  function homeTaskScope(mode) {
    if (typeof clearFilters === 'function') clearFilters();
    const map = {
      joe: ['ownerFilter', session?.name && session.name !== 'Public' ? session.name : 'Joe'],
      mike: ['ownerFilter','Mike'],
      ai: ['executorFilter','ai'],
      critical: ['priorityFilter','Critical'],
      week: ['dueFilter','7d']
    };
    if (mode === 'all') { renderHome(); return; }
    const pair = map[mode];
    if (pair && document.getElementById(pair[0])) document.getElementById(pair[0]).value = pair[1];
    if (typeof renderAll === 'function') renderAll();
    renderHome();
  }

  function safeVisible() {
    try { return typeof visible === 'function' ? visible() : (tasks || []); } catch { return tasks || []; }
  }

  function escH(s) { return typeof esc === 'function' ? esc(s) : String(s ?? ''); }
  function shortTime(ts) {
    if (!ts) return '—';
    const d = new Date(Number(ts) * 1000);
    return d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
  }

  function renderKpis(scoped) {
    const open = scoped.filter(t => t.status !== 'Done');
    const nowDate = new Date(); nowDate.setHours(0,0,0,0);
    const dueSoon = open.filter(t => { if (!t.due_date) return false; const d=new Date(t.due_date+'T12:00:00'); const diff=Math.ceil((d-nowDate)/86400000); return diff>=0&&diff<=7; }).length;
    const ids = new Set(scoped.map(t => t.id));
    const running = (runs || []).filter(r => ids.has(r.task_id) && ['queued','running'].includes(r.status)).length;
    const data = [
      ['Open',open.length,'open','company work'],
      ['Active',scoped.filter(t=>t.status==='Active').length,'active','in progress'],
      ['Blocked',scoped.filter(t=>t.status==='Blocked').length,'blocked','needs action'],
      ['Critical',open.filter(t=>t.priority==='Critical').length,'critical','highest priority'],
      ['AI running',running,'ai','live runs'],
      ['Due soon',dueSoon,'due','next 7 days']
    ];
    const el=document.getElementById('homeKpis'); if(!el) return;
    el.innerHTML=data.map(([l,v,k,s])=>`<div class="home-kpi" data-kind="${k}"><span>${l}</span><b>${v}</b><small>${s}</small></div>`).join('');
  }

  function renderScopes() {
    const c = typeof filterCriteria === 'function' ? filterCriteria() : {};
    const active = c.ownerFilter==='Mike'?'mike':c.executorFilter==='ai'?'ai':c.priorityFilter==='Critical'?'critical':c.dueFilter==='7d'?'week':c.ownerFilter?'joe':'all';
    const el=document.getElementById('homeScopes'); if(!el)return;
    const chips=[['all','All projects'],['joe',session?.name&&session.name!=='Public'?session.name:'Joe'],['mike','Mike'],['ai','AI work'],['critical','Critical'],['week','This week']];
    el.innerHTML=chips.map(([k,l])=>`<button type="button" class="home-chip ${active===k?'active':''}" data-home-scope="${k}">${escH(l)}</button>`).join('');
  }

  function renderAgentMap() {
    const el=document.getElementById('homeAgentMap'); if(!el)return;
    const enabled=(agents||[]).filter(a=>a.enabled).slice(0,5);
    const positions=[[50,13],[16,42],[84,42],[28,82],[72,82]];
    let svg='<svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">';
    positions.forEach(([x,y])=>{svg+=`<line x1="50" y1="50" x2="${x}" y2="${y}"></line>`}); svg+='</svg>';
    const center=`<button type="button" class="agent-node center" style="left:50%;top:50%" data-home-go="ai"><b>${escH(session?.name&&session.name!=='Public'?session.name:'Joe')}</b><small>Executive</small></button>`;
    const nodes=enabled.map((a,i)=>{const [x,y]=positions[i];const active=(runs||[]).some(r=>r.agent_key===a.agent_key&&['queued','running'].includes(r.status));return `<button type="button" class="agent-node ${active?'running':''}" style="left:${x}%;top:${y}%" data-home-agent="${escH(a.agent_key)}"><b>${escH(a.name)}</b><small>${active?'Running':escH(a.role)}</small></button>`}).join('');
    el.innerHTML=svg+center+nodes;
  }

  function renderOrg() {
    const el=document.getElementById('homeOrgMap');if(!el)return;
    const people=(team||[]).filter(p=>p.active).slice(0,4);
    const who=session?.name&&session.name!=='Public'?session.name:'Joe';
    const row=[...people.map(p=>({name:p.name,sub:p.role})),{name:'AI Agents',sub:`${(agents||[]).filter(a=>a.enabled).length} enabled`},{name:'Systems',sub:`${(projects||[]).filter(p=>p.repo).length} repos`}].slice(0,3);
    el.innerHTML=`<div class="org-top"><b>${escH(who)}</b><small>Executive operator</small></div><div class="org-row">${row.map(p=>`<button type="button" class="org-person" data-home-person="${escH(p.name)}"><b>${escH(p.name)}</b><small>${escH(p.sub)}</small></button>`).join('')}</div><div class="org-row">${['Research','Development','Operations'].map((x,i)=>`<div class="org-person"><b>${x}</b><small>${i===0?'Evidence':i===1?'Build':'Release'}</small></div>`).join('')}</div>`;
  }

  function renderActivity() {
    const el=document.getElementById('homeActivity');if(!el)return;
    const items=(activity||[]).slice(0,5);
    el.innerHTML=items.length?items.map(a=>`<button type="button" class="activity-item" ${a.task_id?`data-home-task="${escH(a.task_id)}"`:''}><time>${shortTime(a.ts)}</time><div><b>${escH(a.action)}</b><span>${escH(a.actor||'system')}${a.project?' · '+escH(a.project):''}${a.detail?' · '+escH(a.detail):''}</span></div></button>`).join(''):'<div class="small">No recent activity.</div>';
  }

  function renderWork(scoped) {
    const el=document.getElementById('homeWork');if(!el)return;
    const who=session?.name&&session.name!=='Public'?session.name:'Joe';
    let items=scoped.filter(t=>t.status!=='Done'&&t.owner===who);
    if(!items.length) items=scoped.filter(t=>t.status!=='Done').sort((a,b)=>({Critical:0,High:1,Medium:2,Low:3}[a.priority]-({Critical:0,High:1,Medium:2,Low:3}[b.priority]))).slice(0,4);
    else items=items.slice(0,4);
    el.innerHTML=items.length?items.map(t=>`<button type="button" class="work-item" data-home-task="${escH(t.id)}"><div class="work-top"><b>${escH(t.title)}</b><span class="home-priority ${escH(t.priority)}">${escH(t.priority)}</span></div><span>${escH(t.project)}${t.due_date?' · due '+escH(t.due_date):''}${t.ai_state?' · AI '+escH(t.ai_state):''}</span></button>`).join(''):'<div class="small">No open work in this scope.</div>';
  }

  function renderApprovals() {
    const el=document.getElementById('homeApprovals');if(!el)return;
    const ready=(runs||[]).filter(r=>r.status==='pr-created'||r.pr_url).slice(0,4);
    el.innerHTML=ready.length?ready.map(r=>`<div class="approval-item"><b>${escH(r.project)} · #${r.issue_number}</b><span>${escH(r.agent_key||'agent')} finished work and produced a reviewable result.</span><div class="approval-actions">${r.pr_url?`<a href="${escH(r.pr_url)}" target="_blank" rel="noopener">Open PR</a>`:''}<button type="button" data-home-run="${escH(r.id)}">Terminal</button></div></div>`).join(''):'<div class="small">No agent work is waiting for review.</div>';
  }

  function renderMemory() {
    const el=document.getElementById('homeMemory');if(!el)return;
    const items=(memory||[]).slice(0,5);
    el.innerHTML=items.length?items.map(m=>`<div class="memory-item"><b>${escH(m.kind)} · ${escH(m.agent_key||'shared')}</b><span>${escH(m.content)}</span></div>`).join(''):'<div class="small">No agent memory available for this access level.</div>';
  }

  function renderPortfolio() {
    const el=document.getElementById('homePortfolio');if(!el)return;
    const list=(projects||[]).slice(0,5);
    el.innerHTML=list.map(p=>{const t=(tasks||[]).filter(x=>x.project===p.name),done=t.filter(x=>x.status==='Done').length,pct=t.length?Math.round(done/t.length*100):0;return `<button type="button" class="work-item" data-home-project="${escH(p.name)}"><div class="work-top"><b>${escH(p.name)}</b><span class="health" data-h="${escH(p.health)}">${escH(p.health)}</span></div><span>${escH(p.stage||'Build')} · Lead ${escH(p.lead||'—')} · ${pct}% complete</span></button>`}).join('')||'<div class="small">No projects yet.</div>';
  }

  function renderHome() {
    if (!document.getElementById('home')) return;
    const scoped=safeVisible();
    renderKpis(scoped); renderScopes(); renderAgentMap(); renderOrg(); renderActivity(); renderWork(scoped); renderApprovals(); renderMemory(); renderPortfolio();
    const active=(runs||[]).filter(r=>['queued','running'].includes(r.status)).length;
    const state=document.getElementById('homeSystemState'); if(state) state.textContent=active?`${active} AI run${active===1?'':'s'} active`:'All systems operational';
    const aiState=document.getElementById('homeAIState'); if(aiState) aiState.textContent=(models||[]).some(m=>m.last_status==='ok')?'AI CONNECTED':'AI READY';
    document.querySelectorAll('.home-nav button').forEach(b=>b.classList.toggle('active',b.dataset.homeGo===(document.body.dataset.pbView||'home')));
  }

  const oldRenderAll = typeof renderAll === 'function' ? renderAll : null;
  if (oldRenderAll) renderAll = function(){ oldRenderAll(); renderHome(); };
  const oldApplyInitialLanding = typeof applyInitialLanding === 'function' ? applyInitialLanding : null;
  if (oldApplyInitialLanding) applyInitialLanding = function(){
    const hash=location.hash.replace('#','');
    if(!hash){
      const h=document.querySelector('.tab[data-view="home"]'); if(h) h.click();
      if(session?.level>=1){ const def=settings.filters?.default_saved_view; if(def&&typeof applySavedView==='function')applySavedView(def); }
      if(typeof checkHealth==='function')checkHealth();
      return;
    }
    oldApplyInitialLanding();
  };

  document.addEventListener('click',e=>{
    const b=e.target.closest('[data-view],[data-home-go],[data-home-action],[data-home-scope],[data-home-agent],[data-home-person],[data-home-task],[data-home-run],[data-home-project]');
    if(!b)return;
    if(b.dataset.view){document.body.dataset.pbView=b.dataset.view;setTimeout(renderHome,0)}
    if(b.dataset.homeGo){go(b.dataset.homeGo);e.preventDefault()}
    if(b.dataset.homeScope){homeTaskScope(b.dataset.homeScope);e.preventDefault()}
    if(b.dataset.homeAgent){if(document.getElementById('agentFilter'))document.getElementById('agentFilter').value=b.dataset.homeAgent;if(typeof renderAll==='function')renderAll();go('agents');e.preventDefault()}
    if(b.dataset.homePerson){if(b.dataset.homePerson==='AI Agents')go('agents');else if(b.dataset.homePerson==='Systems')go('settings');else {if(document.getElementById('ownerFilter'))document.getElementById('ownerFilter').value=b.dataset.homePerson;if(typeof renderAll==='function')renderAll();go('board')}e.preventDefault()}
    if(b.dataset.homeTask){if(typeof openTask==='function')openTask(b.dataset.homeTask);e.preventDefault()}
    if(b.dataset.homeRun){if(document.getElementById('terminalRun'))document.getElementById('terminalRun').value=b.dataset.homeRun;go('terminalView');if(typeof terminalLoad==='function')terminalLoad();e.preventDefault()}
    if(b.dataset.homeProject){if(document.getElementById('projectFilter'))document.getElementById('projectFilter').value=b.dataset.homeProject;if(typeof renderAll==='function')renderAll();go('portfolio');e.preventDefault()}
    if(b.dataset.homeAction){
      if(b.dataset.homeAction==='ask'){go('ai');setTimeout(()=>document.getElementById('chatInput')?.focus(),30)}
      if(b.dataset.homeAction==='create'&&typeof openTask==='function')openTask('');
      if(b.dataset.homeAction==='agents')go('agents');
      if(b.dataset.homeAction==='troubleshoot'){go('ai');setTimeout(()=>{const a=document.getElementById('aiAgent');if(a)a.value='help';const i=document.getElementById('chatInput');if(i){i.value='Help me troubleshoot the current PROJECT_BYTE state and tell me exactly where to go.';i.focus()}},30)}
      e.preventDefault();
    }
  });

  const send=()=>{
    const input=document.getElementById('homeCommandInput');const text=input?.value.trim();if(!text)return;
    go('ai');
    setTimeout(()=>{const p=document.getElementById('chatInput');if(p)p.value=text;if(typeof sendChat==='function')sendChat(text);if(input)input.value=''},40);
  };
  document.getElementById('homeCommandSend')?.addEventListener('click',send);
  document.getElementById('homeCommandInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();send()}});

  document.body.dataset.pbView = location.hash.replace('#','') || 'home';
  setTimeout(()=>{renderHome(); if(!location.hash){const h=document.querySelector('.tab[data-view="home"]');if(h)h.click()}},0);
})();
