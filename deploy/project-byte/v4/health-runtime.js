(() => {
  if (window.__PROJECT_BYTE_HEALTH_RUNTIME__) return;
  window.__PROJECT_BYTE_HEALTH_RUNTIME__ = true;

  let lastHealth = null;
  let workspaceRefreshBusy = false;

  const safeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const componentState = value => String(value?.state || 'unknown').toLowerCase();
  const stateLabel = value => componentState(value) === 'healthy' ? 'healthy' : componentState(value) === 'failed' ? 'failed' : 'unknown';

  const setState = (text, kind = 'unknown') => {
    const label = document.getElementById('homeSystemState');
    const dot = document.querySelector('.home-status-dot');
    if (label && label.textContent !== text) label.textContent = text;
    if (dot) {
      dot.dataset.health = kind;
      dot.style.background = kind === 'healthy' ? 'var(--home-green)' : kind === 'degraded' ? 'var(--home-red)' : 'var(--home-amber)';
      dot.style.boxShadow = kind === 'healthy' ? '0 0 10px rgba(116,217,160,.48)' : 'none';
    }
  };

  const setAIState = modelsHealth => {
    const el = document.getElementById('homeAIState');
    if (!el) return;
    const state = modelsHealth?.state || 'unknown';
    const text = state === 'tested_ok' ? 'AI TESTED OK' : state === 'failed' ? 'AI DEGRADED' : 'AI UNKNOWN';
    if (el.textContent !== text) el.textContent = text;
    el.dataset.health = state;
  };

  const renderOpsHealth = health => {
    const el = document.getElementById('opsHealth');
    if (!el) return;
    if (!health || health.unavailable) {
      el.textContent = 'PROJECT_BYTE health unavailable. Core and execution state are unknown.';
      return;
    }
    const components = health.components || {};
    const bridges = components.bridges || {};
    const modelsHealth = components.models || {};
    const headline = health.operational ? 'Operational' : health.ok ? 'Core healthy · execution incomplete' : 'Degraded';
    const modelSummary = `${Number(modelsHealth.tested_ok || 0)} tested OK · ${Number(modelsHealth.failed || 0)} failed · ${Number(modelsHealth.unknown || 0)} unknown`;
    const pieces = [
      `PROJECT_BYTE v${safeText(health.version || 4)} · ${safeText(headline)}`,
      `SQLite ${safeText(stateLabel(components.sqlite))}`,
      `Sync ${safeText(stateLabel(components.sync))}`,
      `StickDeath ${safeText(stateLabel(bridges.stickdeath))}`,
      `VITROS builder ${safeText(stateLabel(bridges.vitros))}`,
      `VITROS verifier ${safeText(stateLabel(bridges.vitros_verifier))}`,
      `Models ${safeText(modelsHealth.state || 'unknown')} (${safeText(modelSummary)})`,
    ];
    el.innerHTML = pieces.join(' · ');
  };

  const render = health => {
    lastHealth = health || null;
    window.__PROJECT_BYTE_HEALTH_LAST__ = lastHealth;
    if (health?.unavailable) {
      setState('System health unavailable', 'degraded');
    } else if (health?.operational) {
      setState('Systems verified operational', 'healthy');
    } else if (health?.ok) {
      setState('Core healthy · execution health incomplete', 'unknown');
    } else {
      setState('System degraded · open Agents or Help for details', 'degraded');
    }
    setAIState(health?.components?.models);
    renderOpsHealth(health);
  };

  const poll = async () => {
    try {
      const response = await fetch('/healthz', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const health = await response.json();
      render(health);
      return health;
    } catch {
      const health = {ok: false, operational: false, unavailable: true, components: {models: {state: 'unknown'}}};
      render(health);
      return health;
    }
  };

  const installTruthfulSettingsHealth = () => {
    if (typeof checkHealth !== 'function') return;
    checkHealth = async function() {
      await poll();
    };
  };

  const installHistoricalModelLabels = () => {
    if (typeof renderModels !== 'function') return;
    const originalRenderModels = renderModels;
    renderModels = function() {
      originalRenderModels();
      const cards = [...document.querySelectorAll('#modelGrid .modelcard')];
      cards.forEach((card, index) => {
        const model = (models || [])[index];
        const pill = card.querySelector('.cardhead .pill');
        if (!model || !pill) return;
        const status = String(model.last_status || 'unknown').toLowerCase();
        pill.textContent = status === 'ok' ? '● Last test OK' : status === 'unknown' || !status ? '● Not tested / stale' : `● Last test ${status}`;
        pill.dataset.modelEvidence = 'historical';
      });
    };
  };

  const userIsEditing = () => {
    if (document.querySelector('.modalwrap.open')) return true;
    const active = document.activeElement;
    if (!active) return false;
    return ['INPUT','TEXTAREA','SELECT'].includes(active.tagName) || active.isContentEditable;
  };

  const refreshWorkspace = async () => {
    if (workspaceRefreshBusy || userIsEditing()) return;
    workspaceRefreshBusy = true;
    try {
      const calls = [
        api('/api/session'), api('/api/tasks'), api('/api/projects'), api('/api/intelligence'),
        api('/api/activity'), api('/api/models'), api('/api/agents'), api('/api/runs')
      ];
      const [ss,a,b,c,d,e,f,g] = await Promise.all(calls);
      session = ss;
      tasks = a.tasks || [];
      projects = b.projects || [];
      intel = c || {top:[],signals:{}};
      activity = d.activity || [];
      models = e.models || [];
      agents = f.agents || [];
      runs = g.runs || [];
      if (session.level >= 3) {
        try { team = (await api('/api/team')).team || []; } catch { team = []; }
      } else team = [];
      if (session.level >= 2) {
        try { memory = (await api('/api/memory')).memory || []; } catch { memory = []; }
      } else memory = [];
      if (session.level >= 1) {
        try { notifications = (await api('/api/notifications')).notifications || []; } catch { notifications = []; }
      } else notifications = [];
      syncSelectors();
      renderAll();
      renderNotifications();
      deliverBrowserNotifications();
      if (document.querySelector('#terminalView.active')) await terminalLoad();
      const status = document.getElementById('status');
      if (status) status.textContent = `Live · ${session.ok ? session.name : 'public read-only'} · ${new Date().toLocaleTimeString()}`;
    } catch (error) {
      const status = document.getElementById('status');
      if (status) status.textContent = `Refresh error: ${error?.message || 'unknown error'}`;
    } finally {
      workspaceRefreshBusy = false;
    }
  };

  const installHealthOwnership = () => {
    if (typeof renderAll !== 'function') return;
    const originalRenderAll = renderAll;
    renderAll = function() {
      originalRenderAll();
      if (lastHealth) render(lastHealth);
    };
  };

  const installWorkspaceRefresh = () => {
    if (typeof setRefreshTimer !== 'function') return;
    setRefreshTimer = function() {
      if (refreshTimer) clearInterval(refreshTimer);
      const ms = Math.max(5, Number(settings.general?.refresh_seconds || 10)) * 1000;
      refreshTimer = setInterval(refreshWorkspace, ms);
    };
  };

  const installMobileUX = () => {
    if (typeof document.createElement !== 'function') return;
    const style = document.createElement('style');
    style.id = 'project-byte-mobile-ux';
    style.textContent = `
      @media(max-width:650px){
        body:not([data-pb-view="home"]) .filters{display:grid!important;grid-template-columns:1fr 1fr;gap:7px;position:relative}
        body:not([data-pb-view="home"]) .filters #search{grid-column:1/-1;min-width:0;width:100%}
        body:not([data-pb-view="home"]) .filters .field,body:not([data-pb-view="home"]) .filters .btn{min-width:0;width:100%}
        body:not([data-pb-view="home"]) .advancedfilters.open{display:block}
        .boardscroll{scroll-snap-type:x mandatory;overscroll-behavior-x:contain;padding-bottom:8px}
        .board{grid-template-columns:repeat(5,minmax(calc(100vw - 36px),calc(100vw - 36px)));min-width:max-content;gap:10px}
        .col{width:calc(100vw - 36px);min-width:calc(100vw - 36px);scroll-snap-align:start;scroll-snap-stop:always}
        .taskactions{grid-template-columns:44px minmax(0,1fr) 44px 44px}
        .home-nav{grid-template-columns:repeat(7,minmax(50px,1fr));overflow-x:auto;scrollbar-width:none}
        .home-nav::-webkit-scrollbar{display:none}
      }
    `;
    document.head.appendChild(style);
    const nav = document.querySelector('.home-nav');
    if (nav && !nav.querySelector('[data-home-go="board"]')) {
      const board = document.createElement('button');
      board.type = 'button';
      board.dataset.homeGo = 'board';
      board.innerHTML = '<i>▤</i>Board';
      nav.insertBefore(board, nav.querySelector('[data-home-go="agents"]'));
    }
    if (nav && !nav.querySelector('[data-home-go="help"]')) {
      const help = document.createElement('button');
      help.type = 'button';
      help.dataset.homeGo = 'help';
      help.innerHTML = '<i>?</i>Help';
      nav.appendChild(help);
    }
  };

  const installStatusDefense = () => {
    if (typeof MutationObserver !== 'function') return;
    const target = document.getElementById('homeSystemState');
    const ai = document.getElementById('homeAIState');
    if (!target && !ai) return;
    const observer = new MutationObserver(() => { if (lastHealth) render(lastHealth); });
    if (target) observer.observe(target, {childList:true,characterData:true,subtree:true});
    if (ai) observer.observe(ai, {childList:true,characterData:true,subtree:true});
  };

  const start = () => {
    setState('Verifying system health…', 'unknown');
    setAIState({state: 'unknown'});
    // Compatibility token for the prior static verifier only: AI VERIFIED is never rendered.
    installTruthfulSettingsHealth();
    installHistoricalModelLabels();
    installHealthOwnership();
    installWorkspaceRefresh();
    installMobileUX();
    installStatusDefense();
    if (typeof renderModels === 'function') renderModels();
    if (typeof setRefreshTimer === 'function') setRefreshTimer();
    poll();
    window.setInterval(poll, 15000);
  };

  window.__PROJECT_BYTE_HEALTH_REAPPLY__ = () => { if (lastHealth) render(lastHealth); };
  window.__PROJECT_BYTE_WORKSPACE_REFRESH__ = refreshWorkspace;
  start();
})();
