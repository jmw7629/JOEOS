(() => {
  if (window.__PROJECT_BYTE_HOME_INSPECTOR__) return;
  window.__PROJECT_BYTE_HOME_INSPECTOR__ = true;

  let selectedAgentKey = '';
  let selectedRunId = '';
  let inspectorRequest = 0;

  const style = document.createElement('style');
  style.textContent = `
    .home-agent-lens{display:flex;gap:6px;overflow:auto;margin-top:9px;padding-bottom:2px;scrollbar-width:none}.home-agent-lens::-webkit-scrollbar{display:none}
    .home-run-pill{min-width:0;max-width:220px;border:1px solid rgba(107,136,166,.24);background:rgba(7,13,21,.54);color:var(--txt);border-radius:9px;padding:7px 9px;text-align:left;cursor:pointer}
    .home-run-pill b{display:block;font-size:10px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.home-run-pill span{display:block;color:var(--muted);font-size:9px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .home-run-pill[data-state="running"],.home-run-pill[data-state="queued"]{border-color:rgba(112,200,255,.42)}.home-run-pill[data-state="pr-created"]{border-color:rgba(116,217,160,.35)}
    .home-agent-inspector{display:none;margin-top:10px;border:1px solid rgba(110,142,174,.25);border-radius:13px;background:linear-gradient(155deg,rgba(10,17,27,.94),rgba(5,10,17,.91));overflow:hidden}
    .home-agent-inspector.open{display:block}.home-inspector-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;padding:11px 12px;border-bottom:1px solid rgba(103,130,158,.18)}
    .home-inspector-head h4{margin:0;font-size:13px}.home-inspector-head p{margin:2px 0 0;color:var(--muted);font-size:10px}.home-inspector-close{border:0;background:transparent;color:var(--muted);font-size:18px;min-width:32px;min-height:32px;border-radius:8px}
    .home-inspector-body{padding:11px 12px}.home-inspector-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:7px;margin-bottom:9px}.home-inspector-stat{background:rgba(255,255,255,.022);border:1px solid rgba(105,132,158,.18);border-radius:9px;padding:8px;min-width:0}.home-inspector-stat span{display:block;color:var(--muted);font-size:8px;text-transform:uppercase;letter-spacing:.08em}.home-inspector-stat b{display:block;font-size:10px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .home-inspector-actions{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.home-inspector-actions button,.home-inspector-actions a{border:1px solid var(--line);background:rgba(16,27,39,.8);color:var(--txt);border-radius:8px;padding:6px 8px;min-height:32px;font-size:10px;text-decoration:none}
    .home-event-list{display:grid;gap:5px;margin-top:8px}.home-event-row{display:grid;grid-template-columns:auto 1fr;gap:7px;padding:6px 0;border-bottom:1px solid rgba(103,130,158,.12)}.home-event-row:last-child{border-bottom:0}.home-event-row time{font:9px ui-monospace,SFMono-Regular,Menlo,monospace;color:#73899f}.home-event-row b{display:block;font-size:9px}.home-event-row span{display:block;color:var(--muted);font-size:9px;margin-top:1px}
    .home-log-preview{margin-top:8px;border:1px solid rgba(87,112,137,.20);background:#04080d;border-radius:9px;padding:8px;max-height:138px;overflow:auto;white-space:pre-wrap;font:9px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;color:#bcd2c4}
    .agent-node.inspecting{border-color:rgba(112,200,255,.78)!important;box-shadow:0 0 0 2px rgba(112,200,255,.10),0 0 24px rgba(56,139,198,.14)!important}
    @media(max-width:650px){.home-inspector-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.home-run-pill{min-width:136px}.home-log-preview{max-height:118px}}
  `;
  document.head.appendChild(style);

  function escI(value) {
    try { return typeof esc === 'function' ? esc(value) : String(value ?? ''); }
    catch { return String(value ?? ''); }
  }

  function fmt(ts) {
    if (!ts) return '—';
    const d = new Date(Number(ts) * 1000);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'});
  }

  function ensureInspector() {
    const graph = document.getElementById('homeAgentMap');
    if (!graph) return null;
    let lens = document.getElementById('homeAgentLens');
    if (!lens) {
      lens = document.createElement('div');
      lens.id = 'homeAgentLens';
      lens.className = 'home-agent-lens';
      graph.insertAdjacentElement('afterend', lens);
    }
    let panel = document.getElementById('homeAgentInspector');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'homeAgentInspector';
      panel.className = 'home-agent-inspector';
      lens.insertAdjacentElement('afterend', panel);
    }
    return panel;
  }

  function latestRunsForAgent(key) {
    return (runs || []).filter(r => r.agent_key === key).sort((a,b) => Number(b.updated_at||0)-Number(a.updated_at||0));
  }

  function renderLens() {
    ensureInspector();
    const lens = document.getElementById('homeAgentLens');
    if (!lens) return;
    const list = [...(runs || [])]
      .sort((a,b) => {
        const aa=['running','queued'].includes(a.status)?0:a.status==='pr-created'?1:2;
        const bb=['running','queued'].includes(b.status)?0:b.status==='pr-created'?1:2;
        return aa-bb || Number(b.updated_at||0)-Number(a.updated_at||0);
      })
      .slice(0,6);
    lens.innerHTML = list.length ? list.map(r => `
      <button type="button" class="home-run-pill" data-inspect-run="${escI(r.id)}" data-state="${escI(r.status)}">
        <b>${escI(r.agent_key || 'agent')} · ${escI(r.status || 'unknown')}</b>
        <span>${escI(r.project || '—')} · #${Number(r.issue_number||0) || '—'} · ${escI(r.model_key || 'default')}</span>
      </button>`).join('') : '<span class="small">No agent runs yet.</span>';
  }

  function markSelectedNode() {
    document.querySelectorAll('#homeAgentMap .agent-node').forEach(n => n.classList.toggle('inspecting', !!selectedAgentKey && n.dataset.homeAgent === selectedAgentKey));
  }

  function agentInfo(key) {
    return (agents || []).find(a => a.agent_key === key) || null;
  }

  function staticInspector(key, run) {
    const agent = agentInfo(key) || {};
    const total = Number(agent.success_count||0) + Number(agent.failure_count||0);
    const rate = total ? Math.round(Number(agent.success_count||0)/total*100) + '%' : '—';
    return `
      <div class="home-inspector-head">
        <div><h4>${escI(agent.name || key || 'Agent')}</h4><p>${escI(agent.role || 'AI agent')}${run ? ' · latest run selected' : ' · no run selected'}</p></div>
        <button type="button" class="home-inspector-close" data-inspector-close aria-label="Close agent inspector">×</button>
      </div>
      <div class="home-inspector-body">
        <div class="home-inspector-grid">
          <div class="home-inspector-stat"><span>Status</span><b>${escI(run?.status || (agent.enabled ? 'Enabled' : 'Idle'))}</b></div>
          <div class="home-inspector-stat"><span>Project</span><b>${escI(run?.project || '—')}</b></div>
          <div class="home-inspector-stat"><span>Model</span><b>${escI(run?.model_key || agent.preferred_model || 'auto')}</b></div>
          <div class="home-inspector-stat"><span>Success</span><b>${escI(rate)}</b></div>
        </div>
        <div class="home-inspector-actions">
          <button type="button" data-inspector-agent-workspace="${escI(key)}">Agent workspace</button>
          ${run ? `<button type="button" data-inspector-terminal="${escI(run.id)}">Full terminal</button>` : ''}
          ${run?.issue_url ? `<a href="${escI(run.issue_url)}" target="_blank" rel="noopener">Issue #${Number(run.issue_number||0)}</a>` : ''}
          ${run?.pr_url ? `<a href="${escI(run.pr_url)}" target="_blank" rel="noopener">Open PR</a>` : ''}
        </div>
        <div id="homeInspectorLive"><div class="small">${run ? 'Loading recent execution events…' : escI(agent.description || 'No execution history for this agent yet.')}</div></div>
      </div>`;
  }

  async function loadRunDetail(run, requestId) {
    const live = document.getElementById('homeInspectorLive');
    if (!live || !run || !session || Number(session.level||0) < 2) {
      if (live && run) live.innerHTML = '<div class="small">Execution log details require Editor or Owner access.</div>';
      return;
    }
    try {
      const detail = await api('/api/runs/' + encodeURIComponent(run.id) + '/terminal');
      if (requestId !== inspectorRequest || selectedRunId !== run.id) return;
      const events = (detail.events || []).slice(0,6);
      const lines = (detail.lines || []).slice(-12);
      live.innerHTML = `
        <div class="small">Recent execution</div>
        <div class="home-event-list">${events.length ? events.map(ev => `<div class="home-event-row"><time>${fmt(ev.ts)}</time><div><b>${escI(ev.kind || 'event')} · ${escI(ev.agent || run.agent_key || 'agent')}</b><span>${escI(ev.message || ev.detail || '')}</span></div></div>`).join('') : '<div class="small">No structured events recorded yet.</div>'}</div>
        <pre class="home-log-preview">${escI(lines.length ? lines.join('\n') : '[No bridge log lines yet. The run may still be queued.]')}</pre>`;
    } catch (err) {
      if (requestId !== inspectorRequest) return;
      live.innerHTML = `<div class="small">Inspector could not load the execution detail: ${escI(err?.message || err)}</div>`;
    }
  }

  function inspectAgent(key, preferredRunId='') {
    if (!key) return;
    selectedAgentKey = key;
    const list = latestRunsForAgent(key);
    const run = (preferredRunId && list.find(r => r.id === preferredRunId)) || list[0] || null;
    selectedRunId = run?.id || '';
    const panel = ensureInspector();
    if (!panel) return;
    panel.classList.add('open');
    panel.innerHTML = staticInspector(key, run);
    markSelectedNode();
    const req = ++inspectorRequest;
    if (run) loadRunDetail(run, req);
  }

  function inspectRun(id) {
    const run = (runs || []).find(r => r.id === id);
    if (!run) return;
    inspectAgent(run.agent_key || 'executive', id);
  }

  function refreshInspector() {
    renderLens();
    if (selectedAgentKey) inspectAgent(selectedAgentKey, selectedRunId);
    else markSelectedNode();
  }

  const priorRenderHome = typeof renderHome === 'function' ? renderHome : null;
  if (priorRenderHome) {
    renderHome = function() {
      priorRenderHome();
      refreshInspector();
    };
  }

  // Capture agent-node taps before the Home module's original navigation handler.
  document.addEventListener('click', e => {
    const node = e.target.closest('[data-home-agent]');
    if (!node || (document.body.dataset.pbView || location.hash.replace('#','')) !== 'home') return;
    e.preventDefault();
    e.stopImmediatePropagation();
    inspectAgent(node.dataset.homeAgent);
  }, true);

  document.addEventListener('click', e => {
    const target = e.target.closest('[data-inspect-run],[data-inspector-close],[data-inspector-agent-workspace],[data-inspector-terminal]');
    if (!target) return;
    if (target.dataset.inspectRun) { inspectRun(target.dataset.inspectRun); return; }
    if (target.hasAttribute('data-inspector-close')) {
      selectedAgentKey='';selectedRunId='';++inspectorRequest;
      document.getElementById('homeAgentInspector')?.classList.remove('open');
      markSelectedNode();
      return;
    }
    if (target.dataset.inspectorAgentWorkspace) {
      const f=document.getElementById('agentFilter');if(f)f.value=target.dataset.inspectorAgentWorkspace;
      if(typeof renderAll==='function')renderAll();
      const tab=document.querySelector('.tab[data-view="agents"]');if(tab)tab.click();
      return;
    }
    if (target.dataset.inspectorTerminal) {
      const sel=document.getElementById('terminalRun');if(sel)sel.value=target.dataset.inspectorTerminal;
      const tab=document.querySelector('.tab[data-view="terminalView"]');if(tab)tab.click();
      if(typeof terminalLoad==='function')terminalLoad();
    }
  });

  // Clarify the action: it creates work; actual execution still requires Queue to AI.
  const createAction=document.querySelector('[data-home-action="create"]');
  if(createAction)createAction.textContent='New work';

  setTimeout(refreshInspector, 0);
})();
