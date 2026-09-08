(() => {
  if (window.__PROJECT_BYTE_HEALTH_RUNTIME__) return;
  window.__PROJECT_BYTE_HEALTH_RUNTIME__ = true;

  let lastHealth = null;

  const safeText = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const componentState = value => String(value?.state || 'unknown').toLowerCase();
  const stateLabel = value => componentState(value) === 'healthy' ? 'healthy' : componentState(value) === 'failed' ? 'failed' : 'unknown';
  const bridgeSummary = value => {
    if (value?.owner_paused && !value?.external_running) return 'Paused by owner · execution disabled';
    const state = stateLabel(value);
    const progress = String(value?.progress_state || '').toLowerCase();
    const execution = String(value?.execution_state || 'unknown').toLowerCase();
    const reason = String(value?.execution_reason || '').trim();
    const process = progress && progress !== 'unknown' ? `${state}/${progress}` : state;
    const activeRef = String(value?.active_run_ref || '').trim();
    const source = String(value?.execution_source || '').trim();
    const elapsed = value?.external_elapsed_seconds;
    const elapsedText = elapsed === null || elapsed === undefined ? '' : ` · ${Math.max(0,Math.round(Number(elapsed)/60))}m`;
    const active = activeRef ? ` · Active: ${activeRef}${source ? ' · ' + source : ''}${source === 'external-bridge' ? elapsedText : ''}` : '';
    return `Process: ${process}${active} · Execution: ${execution}${reason ? ' · ' + reason : ''}`;
  };

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
    const text = state === 'tested_ok' ? 'AI CHAT TESTED OK' : state === 'failed' ? 'AI CHAT DEGRADED' : 'AI CHAT UNTESTED';
    if (el.textContent !== text) el.textContent = text;
    el.dataset.health = state;
  };

  const renderOpsHealth = health => {
    const el = document.getElementById('opsHealth');
    if (!el) return;
    if (!health || health.unavailable) {
      el.textContent = 'PRFKT_PROJECT health unavailable. Core and execution state are unknown.';
      return;
    }
    const components = health.components || {};
    const bridges = components.bridges || {};
    const modelsHealth = components.models || {};
    const headline = health.operational ? ((health.warnings || []).length ? 'Operational · warnings' : 'Operational') : health.ok ? 'Core healthy · execution incomplete' : 'Degraded';
    const modelSummary = `${Number(modelsHealth.tested_ok || 0)} tested OK · ${Number(modelsHealth.failed || 0)} failed · ${Number(modelsHealth.unknown || 0)} unknown`;
    const pieces = [
      `PRFKT_PROJECT v${safeText(health.version || 4)} · ${safeText(headline)}`,
      `SQLite ${safeText(stateLabel(components.sqlite))}`,
      `Sync ${safeText(stateLabel(components.sync))}`,
      `StickDeath ${safeText(bridgeSummary(bridges.stickdeath))}`,
      `VITROS builder ${safeText(bridgeSummary(bridges.vitros))}`,
      `VITROS verifier ${safeText(bridgeSummary(bridges.vitros_verifier))}`,
      `Models ${safeText(modelsHealth.state || 'unknown')} (${safeText(modelSummary)})`,
    ];
    el.innerHTML = pieces.join(' · ');
  };

  const render = health => {
    lastHealth = health || null;
    window.__PROJECT_BYTE_HEALTH_LAST__ = lastHealth;
    if (typeof window.dispatchEvent === 'function' && typeof CustomEvent === 'function') {
      window.dispatchEvent(new CustomEvent('project-byte-health', {detail: lastHealth}));
    }
    if (health?.unavailable) {
      setState('System health unavailable', 'degraded');
    } else if (health?.operational) {
      const count = (health.warnings || []).length;
      setState(count ? `Systems operational · ${count} warning${count === 1 ? '' : 's'}` : 'Systems verified operational', count ? 'unknown' : 'healthy');
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

  const installHealthOwnership = () => {
    if (typeof renderAll !== 'function') return;
    const originalRenderAll = renderAll;
    renderAll = function() {
      originalRenderAll();
      if (lastHealth) render(lastHealth);
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
        .home-nav{grid-template-columns:repeat(5,minmax(0,1fr));overflow:visible}
      }
    `;
    document.head.appendChild(style);
    const nav = document.querySelector('.home-nav');
    if (nav) {
      // Keep the accepted five-primary mobile navigation model. Board remains
      // reachable from Projects/Kanban; Help remains available from Troubleshoot/settings.
      nav.querySelector('[data-home-go="board"]')?.remove();
      nav.querySelector('[data-home-go="help"]')?.remove();
    }
  };

  const start = () => {
    setState('Verifying system health…', 'unknown');
    setAIState({state: 'unknown'});
    installTruthfulSettingsHealth();
    installHealthOwnership();
    installMobileUX();
    if (typeof renderModels === 'function') renderModels();
    if (typeof setRefreshTimer === 'function') setRefreshTimer();
    poll();
    window.setInterval(poll, 15000);
  };

  window.__PROJECT_BYTE_HEALTH_REAPPLY__ = () => { if (lastHealth) render(lastHealth); };
  start();
})();
