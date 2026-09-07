(() => {
  if (window.__PROJECT_BYTE_HEALTH_RUNTIME__) return;
  window.__PROJECT_BYTE_HEALTH_RUNTIME__ = true;

  let lastHealth = null;
  let applying = false;

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

  const setAIState = (models) => {
    const el = document.getElementById('homeAIState');
    if (!el) return;
    const state = models?.state || 'unknown';
    // Legacy label "AI VERIFIED" is intentionally not rendered; tested reachability is evidence, not verification.
    const text = state === 'tested_ok' ? 'AI TESTED OK' : state === 'failed' ? 'AI DEGRADED' : 'AI UNKNOWN';
    if (el.textContent !== text) el.textContent = text;
    el.dataset.health = state;
  };

  const render = (health) => {
    lastHealth = health || null;
    applying = true;
    try {
      if (health?.operational) {
        setState('Systems verified operational', 'healthy');
      } else if (health?.ok) {
        setState('Core healthy · execution health incomplete', 'unknown');
      } else {
        setState('System degraded · open Agents or Help for details', 'degraded');
      }
      setAIState(health?.components?.models);
    } finally {
      applying = false;
    }
  };

  const reapply = () => {
    if (!applying && lastHealth) render(lastHealth);
  };

  const observeLegacyOverwrites = () => {
    if (typeof MutationObserver !== 'function') return;
    const targets = [
      document.getElementById('homeSystemState'),
      document.getElementById('homeAIState'),
    ].filter(Boolean);
    if (!targets.length) return;
    const observer = new MutationObserver(() => reapply());
    for (const target of targets) observer.observe(target, {childList: true, characterData: true, subtree: true});
  };

  const poll = async () => {
    try {
      const response = await fetch('/healthz', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      render(await response.json());
    } catch {
      render({ok: false, operational: false, components: {models: {state: 'unknown'}}});
      setState('System health unavailable', 'degraded');
    }
  };

  const start = () => {
    setState('Verifying system health…', 'unknown');
    setAIState({state: 'unknown'});
    observeLegacyOverwrites();
    poll();
    window.setInterval(poll, 15000);
  };

  window.__PROJECT_BYTE_HEALTH_REAPPLY__ = reapply;
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once: true});
  else start();
})();
