(() => {
  if (window.__PROJECT_BYTE_HEALTH_RUNTIME__) return;
  window.__PROJECT_BYTE_HEALTH_RUNTIME__ = true;

  const setState = (text, kind = 'unknown') => {
    const label = document.getElementById('homeSystemState');
    const dot = document.querySelector('.home-status-dot');
    if (label) label.textContent = text;
    if (dot) {
      dot.dataset.health = kind;
      dot.style.background = kind === 'healthy' ? 'var(--home-green)' : kind === 'degraded' ? 'var(--home-red)' : 'var(--home-amber)';
      dot.style.boxShadow = kind === 'healthy' ? '0 0 10px rgba(116,217,160,.48)' : 'none';
    }
  };

  const setAIState = (models) => {
    const el = document.getElementById('homeAIState');
    if (!el) return;
    const state = models?.state || 'unknown';
    el.textContent = state === 'tested_ok' ? 'AI VERIFIED' : state === 'failed' ? 'AI DEGRADED' : 'AI UNKNOWN';
    el.dataset.health = state;
  };

  const render = (health) => {
    if (health?.operational) {
      setState('Systems verified operational', 'healthy');
    } else if (health?.ok) {
      setState('Core healthy · execution health incomplete', 'unknown');
    } else {
      setState('System degraded · open Health for details', 'degraded');
    }
    setAIState(health?.components?.models);
  };

  const poll = async () => {
    try {
      const response = await fetch('/healthz', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch {
      setState('System health unavailable', 'degraded');
      setAIState({state: 'unknown'});
    }
  };

  const start = () => {
    setState('Verifying system health…', 'unknown');
    setAIState({state: 'unknown'});
    poll();
    window.setInterval(poll, 15000);
  };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
