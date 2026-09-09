(() => {
  if (window.__PROJECT_BYTE_HOME__) return;
  window.__PROJECT_BYTE_HOME__ = true;
  let workspaceProfile = {schema_version:1,display_name:'PRFKT_PROJECT',assistant_name:'AI_BYTE',owner_shortcuts:['Joe','Mike']};
  window.PROJECT_BYTE_WORKSPACE = workspaceProfile;
  // Original inline icons: no icon font, tracking request, or runtime dependency.
  const ICONS = {
    home:'<path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z"/>',
    projects:'<path d="M3 7V5h6l2 2h10v13H3Z"/><path d="M3 10h18"/>',
    chat:'<path d="M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l-2 2V11.5a9.5 9.5 0 0 1 19 0Z"/>',
    agents:'<circle cx="12" cy="5" r="3"/><circle cx="5" cy="19" r="3"/><circle cx="19" cy="19" r="3"/><path d="M12 8v5M5 16v-3h14v3"/>',
    user:'<circle cx="12" cy="7" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2Z"/>',
    team:'<circle cx="9" cy="7" r="3"/><path d="M3 20v-2a6 6 0 0 1 12 0v2M17 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 6"/>',
    settings:'<path d="m9 3 1-1h4l1 3 3 1 3 1v4l-2 2-1 3-1 3h-4l-2-2-3-1-3-1v-4l2-2Z"/><circle cx="12" cy="11" r="3"/>',
    plus:'<path d="M12 5v14M5 12h14"/>',
    tools:'<path d="M21 3a6 6 0 0 1-7 8L5 21l-3-3 10-9a6 6 0 0 1 8-7l-5 5 2 2Z"/>',
    sparkle:'<path d="m12 2 3 7 7 3-7 3-3 7-3-7-7-3 7-3ZM20 2v4M18 4h4"/>',
    send:'<path d="m3 3 18 7-8 3-3 8Z M3 3l10 10"/>',
    open:'<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8 12 3 3 6-6"/>',
    active:'<path d="m8 4 13 8-13 8Z"/>',
    blocked:'<path d="M8 5v14M16 5v14"/>',
    critical:'<path d="m12 3 10 18H2Z M12 9v5M12 17h.01"/>',
    ai:'<path d="M9 4a4 4 0 0 0-6 5 4 4 0 0 0 0 7 4 4 0 0 0 6 5V4Zm6 0a4 4 0 0 1 6 5 4 4 0 0 1 0 7 4 4 0 0 1-6 5V4ZM5 10h4m6 5h4"/>',
    due:'<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M7 2v6m10-6v6M3 11h18M8 15h2m4 0h2"/>',
    code:'<path d="m7 6-5 6 5 6m10-12 5 6-5 6M14 3 10 21"/>',
    review:'<circle cx="10" cy="10" r="7"/><path d="m15 15 6 6m-15-11 3 3 5-5"/>',
    layers:'<path d="m12 3 10 5-10 5L2 8Zm-10 9 10 5 10-5M2 17l10 5 10-5"/>',
    activity:'<path d="M2 12h4l3-8 6 16 3-8h4"/>',
    memory:'<rect x="4" y="2" width="16" height="20" rx="3"/><path d="M8 7h8M8 12h8M8 17h5"/>',
    arrow:'<path d="m9 5 7 7-7 7"/>', close:'<path d="m6 6 12 12M18 6 6 18"/>', menu:'<rect x="3" y="3" width="6" height="6" rx="1"/><rect x="15" y="3" width="6" height="6" rx="1"/><rect x="3" y="15" width="6" height="6" rx="1"/><rect x="15" y="15" width="6" height="6" rx="1"/>', help:'<circle cx="12" cy="12" r="9"/><path d="M9 8a3 3 0 1 1 4 3c-1 1-1 2-1 3m0 3h.01"/>'
  };
  const icon = name => `<svg class="pb-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">${ICONS[name] || ICONS.agents}</svg>`;


  let homeApprovalState = [];
  let homeApprovalRefreshBusy = false;
  let homeExternalReviewState = {state:'unavailable',items:[],repositories:{}};
  let homeExternalReviewRefreshBusy = false;
  let homeExternalReviewUpdatedAt = 0;
  let homeExternalReviewIdentity = '';
  let homeExternalReviewRequest = 0;
  let homeExternalReviewController = null;

  const style = document.createElement('style');
  style.textContent = `
/* PRFKT_PROJECT / Orbital Home. All data stays in the existing application. */
:root{--home-blue:#76c9ff;--home-blue2:#2388f5;--home-violet:#b39aff;--home-green:#69dca8;--home-red:#ff8791;--home-amber:#f1c471;--home-border:rgba(138,177,213,.22)}
body{padding-bottom:96px}body[data-pb-view="home"]{background:radial-gradient(ellipse at 80% 0,rgba(28,73,131,.18),transparent 55%),#050a12}
body[data-pb-view="home"]>header{position:relative;background:rgba(5,10,18,.85);border-bottom:0;max-width:1360px;margin:auto;padding:22px 24px 6px}
body[data-pb-view="home"]>header .brand h1{font-size:23px;letter-spacing:.08em;font-weight:700}.brand h1 .pb-wordmark{color:var(--home-blue)}
body[data-pb-view="home"]>header .brand p{font-size:10px;text-transform:uppercase;letter-spacing:.22em;margin-top:7px}
body[data-pb-view="home"] .tabs,body[data-pb-view="home"] .filters,body[data-pb-view="home"]>.wrap>.metrics{display:none}
body[data-pb-view="home"] .wrap{padding:12px 24px 36px;max-width:1360px;margin:auto}
.pb-icon{width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0;vertical-align:middle;pointer-events:none}
.home-shell{display:grid;gap:18px;max-width:1312px;margin:auto;min-width:0}.home-shell *{box-sizing:border-box}
.home-hero,.home-card,.home-kpi{border:1px solid var(--home-border);background:linear-gradient(145deg,rgba(19,31,47,.94),rgba(7,17,29,.97));box-shadow:inset 0 1px 0 rgba(239,248,255,.06),0 8px 32px rgba(0,0,0,.22);border-radius:18px;min-width:0}
.home-hero{position:relative;isolation:isolate;overflow:hidden;padding:28px 28px 24px;background:rgba(7,16,29,.58)}


.home-hero-top{display:flex;justify-content:space-between;align-items:flex-start;gap:18px}.home-hero h2{font-size:clamp(32px,4vw,48px);line-height:1.08;letter-spacing:-.045em;margin:22px 0 10px;font-weight:700}
.home-hero p{color:#bbcbdd;max-width:470px;font-size:15px;line-height:1.6;margin:0}.home-eyebrow{display:flex;gap:8px;align-items:center;color:#bed0e3;font-size:11px;letter-spacing:.02em;min-height:20px}
.home-status-dot{width:7px;height:7px;border-radius:50%;background:var(--home-amber);flex-shrink:0}.home-ai-state{font-size:10px;letter-spacing:.035em;border:1px solid rgba(241,196,113,.3);color:#dfc799;padding:7px 10px;border-radius:8px;background:rgba(7,15,25,.76);white-space:nowrap}
.home-ai-state[data-health="tested_ok"]{border-color:rgba(105,220,168,.45);color:#8ee8c1}.home-ai-state[data-health="failed"]{border-color:#754352;color:#ffb5bf}
.home-command{display:flex;align-items:center;gap:10px;margin-top:26px;border:1px solid rgba(135,185,235,.53);background:linear-gradient(135deg,rgba(36,57,85,.79),rgba(12,22,37,.9));border-radius:14px;padding:8px 9px 8px 16px;box-shadow:inset 0 1px 1px rgba(220,240,255,.12)}
.home-command>.pb-icon{color:#96ceff;width:20px}.home-command:focus-within{border-color:#8fd4ff;outline:2px solid rgba(65,151,255,.18);outline-offset:2px}
.home-command input{flex:1;min-width:0;background:transparent;color:var(--txt);border:0;outline:0;min-height:40px;font-size:16px;padding:0}.home-command input::placeholder{color:#95abc5}
.home-command button{width:44px;height:44px;flex-shrink:0;display:grid;place-items:center;border-radius:50%;color:white;border:1px solid #96d8ff;background:linear-gradient(135deg,#6fc4ff,#167aeb);box-shadow:0 0 18px rgba(51,150,252,.35),inset 0 1px 1px #c2ebff}
.home-actions{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin-top:14px}.home-action{display:flex;align-items:center;justify-content:center;gap:10px;min-height:56px;padding:10px;border:1px solid rgba(122,173,224,.44);border-radius:12px;color:#e7f2ff;background:linear-gradient(155deg,rgba(35,65,102,.87),rgba(13,30,51,.84));box-shadow:inset 0 1px rgba(232,245,255,.13);font-size:13px;font-weight:600}
.home-action .pb-icon{color:#a2d6ff}.home-action:hover,.home-command button:hover{filter:brightness(1.15)}.home-action:active,.home-kpi:active,.home-chip:active{transform:translateY(1px)}
.home-kpis{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px}.home-kpi{appearance:none;cursor:pointer;display:grid;grid-template-columns:38px 1fr;gap:2px 12px;padding:16px;text-align:left;color:var(--txt);border-radius:14px}
.home-kpi .kpi-orbit{grid-row:1/4;align-self:center;border:1px solid currentColor;box-shadow:0 0 16px rgba(73,156,241,.13);width:38px;height:38px;border-radius:50%;display:grid;place-items:center;color:var(--home-blue)}
.home-kpi b{font-size:25px;font-weight:650;line-height:1.15}.home-kpi .kpi-label{font-size:11px;color:#b5c5d7}.home-kpi small{font-size:10px;color:#829bb4}.home-kpi[aria-pressed="true"]{border-color:#55b2ff;box-shadow:inset 0 0 0 1px #55b2ff}
.home-kpi[data-kind="active"] .kpi-orbit{color:var(--home-green)}.home-kpi[data-kind="blocked"] .kpi-orbit,.home-kpi[data-kind="due"] .kpi-orbit{color:var(--home-amber)}.home-kpi[data-kind="critical"] .kpi-orbit{color:var(--home-red)}.home-kpi[data-kind="ai"] .kpi-orbit{color:var(--home-violet)}
.home-scopes{display:flex;gap:8px;overflow:auto;padding:2px 1px 6px;scrollbar-width:thin;scrollbar-color:#284360 transparent}.home-chip{white-space:nowrap;border:1px solid var(--home-border);border-radius:24px;min-height:44px;padding:9px 16px;font-size:12px;color:#becfe0;background:linear-gradient(145deg,#142235,#09131f)}
.home-chip.active{background:linear-gradient(140deg,#1696ff,#1665ce);border-color:#55baff;box-shadow:inset 0 1px rgba(245,253,255,.24),0 0 16px rgba(32,139,250,.2);color:white}
.home-grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(280px,1fr);gap:18px}.home-grid:last-of-type{grid-template-columns:1fr 1fr}.home-lower{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px}
.home-card{padding:20px}.home-card-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}.home-card-head h3{display:flex;align-items:center;gap:9px;font-size:15px;font-weight:600;margin:0;min-width:0}.home-card-head h3 .pb-icon{color:#9cd7ff;width:19px;height:19px}
.home-card-head button{min-width:44px;min-height:44px;border:0;background:transparent;color:#a2bdd9;border-radius:9px;padding:6px;font-size:11px}.home-card-head button:hover{background:#17324a}.home-card-head button .pb-icon{width:16px}
.agent-map{height:276px;position:relative;overflow:hidden;border-radius:12px;background:radial-gradient(ellipse at center,rgba(22,108,195,.13),transparent 66%)}.agent-map>.agent-lines{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}
.agent-map path{fill:none;stroke:rgba(126,193,253,.44);stroke-width:1.1;vector-effect:non-scaling-stroke}.agent-map path.live-link{stroke:#58b7ff;stroke-width:1.5}
.agent-node{position:absolute;transform:translate(-50%,-50%);width:112px;min-height:64px;border:1px solid rgba(113,172,222,.25);border-radius:14px;background:linear-gradient(155deg,#192b40,#0b1726);box-shadow:inset 0 1px rgba(240,248,255,.06);color:#d8e9fa;text-align:center;padding:9px 4px;cursor:pointer}
.agent-node .pb-icon{width:23px;height:23px;color:#a9d8ff;margin-bottom:6px}.agent-node b{display:block;font-size:11px;line-height:1.35;overflow-wrap:anywhere}.agent-node small{display:block;color:#9bb0c6;font-size:10px;margin-top:3px}
.agent-node.center{width:84px;height:84px;border-radius:50%;border-color:#79c8ff;background:radial-gradient(circle at 40% 25%,#1c538b,#0c2342 75%);box-shadow:0 0 26px rgba(16,140,255,.28),inset 0 0 14px rgba(37,149,255,.24)}.agent-node.center .pb-icon{height:25px;width:25px;margin-bottom:4px}.agent-node.center b{font-size:11px}.agent-node.center small{font-size:8px}
.agent-node.running::after{content:"";position:absolute;top:7px;right:7px;width:6px;height:6px;border-radius:50%;background:var(--home-green);box-shadow:0 0 9px rgba(80,238,166,.4)}.agent-node:hover{border-color:#75baff}.agent-node:focus-visible{outline:2px solid #a0d7ff;outline-offset:3px}
.fabric-heading{display:flex;gap:8px;align-items:center;font-size:12px;color:#c3d9ef;margin:16px 0 10px}.fabric-heading .pb-icon{width:18px}.execution-fabric{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.fabric-node{position:relative;min-width:0;min-height:76px;text-align:left;padding:12px 12px 12px 26px;color:#dae9f8;border:1px solid var(--home-border);border-radius:11px;background:linear-gradient(150deg,#15283a,#0c1928)}
.fabric-node b{display:block;font-size:11px;margin-bottom:5px}.fabric-node span{display:block;font-size:10px;line-height:1.5;color:#a6bad0;overflow-wrap:anywhere}.fabric-node::before{content:"";position:absolute;top:16px;left:11px;width:6px;height:6px;border-radius:50%;background:var(--home-amber)}
.fabric-node[data-state="healthy"]::before,.fabric-node[data-state="tested_ok"]::before{background:var(--home-green);box-shadow:0 0 8px rgba(105,220,168,.35)}.fabric-node[data-state="failed"]::before{background:var(--home-red)}.fabric-node[data-state="paused"]::before{background:var(--home-amber)}
.org-map{display:grid;gap:12px}.org-top{display:flex;align-items:center;gap:12px;text-align:left;padding:14px;border:1px solid #335778;border-radius:12px;background:linear-gradient(145deg,#192c42,#102032);color:var(--txt)}.org-top b{font-size:14px;display:block}.org-top small{display:block;font-size:11px;color:#9eb7d0;margin-top:3px}
.org-avatar{display:grid;place-items:center;width:40px;height:40px;flex-shrink:0;border-radius:50%;border:1px solid #638dae;background:#1a3550;color:#b8e1ff;font-size:14px;font-weight:700}.org-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}
.org-person{display:flex;align-items:center;flex-direction:column;gap:6px;padding:12px 5px;min-height:80px;min-width:0;text-align:center;background:linear-gradient(145deg,#152639,#0d1927);border:1px solid var(--home-border);border-radius:11px;color:#d8e8f8}.org-person b{font-size:11px;overflow-wrap:anywhere}.org-person small{font-size:10px;color:#9bb1c8}.org-person .pb-icon{width:19px;color:#adccf2}.org-person:hover{border-color:#6ba9d4}
.activity-feed,.work-list,.memory-feed,.approval-list,#homePortfolio{display:grid;gap:9px}.activity-item,.work-item,.memory-item,.approval-item{min-width:0;border:1px solid rgba(108,155,196,.23);border-radius:12px;background:linear-gradient(135deg,rgba(23,41,61,.8),rgba(10,24,38,.88));padding:12px;color:#dcecfb;text-align:left}
.activity-item{display:grid;grid-template-columns:26px minmax(0,1fr);gap:10px;align-items:start}.activity-item .pb-icon{color:#80c5ff;width:22px}.activity-item time{font-size:10px;display:block;color:#8ba5c0;margin-top:6px}.activity-item b,.work-item b,.memory-item b,.approval-item b{font-size:12px;line-height:1.45;display:block;overflow-wrap:anywhere}
.activity-item span,.work-item>span,.memory-item span,.approval-item>span{font-size:11px;line-height:1.55;display:block;color:#a5bad0;margin-top:4px;overflow-wrap:anywhere}.work-item{cursor:pointer;width:100%}.work-item:hover{border-color:#598bb6;background:#18324a}
.work-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.home-priority{font-size:9px;line-height:1.6;border:1px solid #364e66;border-radius:20px;padding:2px 7px;white-space:nowrap;color:#bdd7ed}.home-priority.Critical{background:#402331;border-color:#73414e;color:#ffb5c0}.home-priority.High{background:#3d2d24;border-color:#6e5130;color:#f2c997}.home-priority.Low{color:#90caff;background:#152d4c}
.approval-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:12px}.approval-actions a,.approval-actions button{border:1px solid #365578;border-radius:9px;min-height:44px;padding:10px;font-size:11px;text-align:center;background:#142b46;color:#e2f1ff}.approval-actions button[data-review-action="approve"]{border-color:#359568;background:#123e34;color:#aeefd1}.approval-actions button[data-review-action="request_changes"]{color:#efd4a3}.approval-note{font-size:10px!important;color:#98aec6!important;margin-top:10px!important}
.approval-item.external-review{border-color:rgba(112,200,255,.30)}.approval-item.external-review .approval-actions a{min-height:44px;display:inline-flex;align-items:center;max-width:100%;overflow-wrap:anywhere}.external-review-meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}.external-review-meta em{font-style:normal;font-size:9px;border:1px solid #365578;border-radius:999px;padding:4px 7px;color:#a9bfd5;background:#0f2134}
.approval-state{font-size:10px;color:#a7d9c4;text-transform:uppercase;letter-spacing:.07em}.home-empty{padding:18px 10px;color:#99b0c9;font-size:12px;line-height:1.6;text-align:center;border:1px dashed rgba(122,164,206,.2);border-radius:12px}.home-empty .pb-icon{display:block;width:26px;height:26px;margin:0 auto 10px;color:#7aa9d1}.home-empty button{display:block;margin:14px auto 0;min-height:44px;padding:8px 15px;background:#172e47;color:#c7e5ff;border:1px solid #33597a;border-radius:10px;font-size:12px}
.pulse-progress{height:5px;border-radius:4px;background:#203248;margin-top:12px;overflow:hidden}.pulse-progress i{height:100%;display:block;background:linear-gradient(90deg,#2283e8,#67c7f5);border-radius:4px}.home-asset-credit{font-size:10px;color:#69829e;display:flex;justify-content:space-between;gap:15px;flex-wrap:wrap;margin:2px 3px}.home-asset-credit a{color:#90aeca}
.home-nav{display:grid!important;position:fixed;left:50%;right:auto;transform:translateX(-50%);width:min(560px,calc(100% - 24px));bottom:max(12px,env(safe-area-inset-bottom));z-index:40;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;padding:7px;background:rgba(7,15,25,.96);border:1px solid rgba(130,176,215,.32);border-radius:20px;box-shadow:0 -8px 35px rgba(0,0,0,.18),inset 0 1px rgba(239,250,255,.06);backdrop-filter:blur(18px)}
.home-nav button{display:flex;align-items:center;justify-content:center;flex-direction:column;gap:5px;min-width:0;min-height:55px;border:0;border-radius:13px;background:transparent;color:#94aac2;font-size:10px;font-weight:500;padding:6px 1px}.home-nav .pb-icon{width:23px;height:23px}.home-nav button.active{background:linear-gradient(155deg,rgba(20,74,124,.4),rgba(10,32,55,.38));color:#70c3ff}.home-nav button.active .pb-icon{filter:drop-shadow(0 0 5px rgba(67,164,255,.4))}
.home-shell button:focus-visible,.home-nav button:focus-visible,.pb-workspaces button:focus-visible{outline:2px solid #a1d7ff;outline-offset:3px}.home-shell button,.home-nav button{touch-action:manipulation;transition:border-color .15s,background .15s,transform .15s}.home-shell button:disabled{opacity:.55;cursor:wait}
.pb-workspaces{width:min(540px,calc(100vw - 28px));max-height:85dvh;padding:20px;background:#0b1726;color:#e0efff;border:1px solid #345371;border-radius:22px;box-shadow:0 22px 90px #000b}.pb-workspaces::backdrop{background:rgba(0,5,12,.76);backdrop-filter:blur(8px)}.pb-drawer-head{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}.pb-drawer-head h2{margin:0;font-size:21px;letter-spacing:-.02em}.pb-drawer-close{min-width:44px;min-height:44px;border-radius:50%;border:1px solid #304c6a;background:#13263b;color:#cce4fa}
.pb-workspace-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}.pb-workspace-grid button{display:flex;gap:12px;align-items:center;min-height:65px;border-radius:12px;border:1px solid #2a425d;background:linear-gradient(140deg,#192c42,#102033);color:#dbeeff;padding:12px;text-align:left;font-size:12px}.pb-workspace-grid button .pb-icon{color:#82c8ff}.pb-workspace-grid button:hover{border-color:#6faedc}
.pb-home-toast{position:fixed;left:50%;bottom:calc(104px + env(safe-area-inset-bottom));transform:translateX(-50%);z-index:1200;max-width:min(540px,calc(100vw - 30px));padding:12px 18px;border:1px solid #426b92;background:#132d47;color:#e4f2ff;border-radius:14px;font-size:13px;box-shadow:0 12px 40px #0009}.pb-home-toast:empty{display:none}
.modalwrap{z-index:1000!important}.modalwrap .modal{max-height:calc(100dvh - 32px);overflow:auto;overscroll-behavior:contain}.modalwrap .row{position:sticky;bottom:-1px;background:var(--panel);padding-top:12px;padding-bottom:max(8px,env(safe-area-inset-bottom));z-index:1}
.home-agent-inspector{border-radius:15px!important}.home-agent-inspector .home-inspector-head h4{font-size:15px}.home-agent-inspector .home-inspector-actions button,.home-agent-inspector .home-inspector-actions a,.home-agent-inspector .home-inspector-close{min-height:44px;min-width:44px}.home-agent-inspector .home-inspector-stat span{font-size:10px}.home-agent-inspector .home-inspector-stat b{font-size:12px}.home-agent-inspector .home-log-preview{font-size:11px}
@media(min-width:1400px){.execution-fabric{grid-template-columns:repeat(4,minmax(0,1fr))}}
@media(max-width:1080px){.home-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.home-grid{grid-template-columns:1fr 1fr}.home-kpi{padding:15px}.home-card{padding:16px}.home-grid:first-of-type{grid-template-columns:1.4fr 1fr}}
@media(max-width:760px){.home-grid,.home-grid:last-of-type,.home-grid:first-of-type{grid-template-columns:1fr}.home-lower{grid-template-columns:1fr}.home-actions{gap:8px}.home-action{font-size:12px;gap:7px}.home-hero{padding:22px}.home-card{padding:17px}.home-hero p{max-width:310px}.home-shell{gap:14px}}
@media(max-width:650px){body{padding-bottom:calc(96px + env(safe-area-inset-bottom))}.tabs{display:none}.filters{display:none}.brand{flex-direction:column;gap:15px}.top{width:100%;display:flex;flex-wrap:nowrap;gap:8px}.top #login{flex:1;font-size:12px;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.top .btn{min-height:44px}.top #newTask{display:none!important}.top #refresh{font-size:0;min-width:44px}.top #refresh::after{content:'↻';font-size:22px}.top #notifyBtn{font-size:0;min-width:44px}.top #notifyBtn::before{content:'◉';font-size:18px}.top #notifyBtn .badge{font-size:10px}
body[data-pb-view="home"]>header{padding:18px 16px 3px}body[data-pb-view="home"]>header .brand h1{font-size:20px}body[data-pb-view="home"]>header .brand p{font-size:8px;letter-spacing:.18em}body[data-pb-view="home"] .wrap{padding:13px 14px 26px}.home-hero{padding:19px 16px;border-radius:18px}.home-hero h2{font-size:36px;margin-top:23px}.home-hero p{font-size:13px;max-width:270px}.home-eyebrow{font-size:10px;max-width:215px}.home-hero-top{gap:8px}.home-ai-state{font-size:8px;padding:6px 7px;max-width:102px;white-space:normal;line-height:1.4;text-align:center}
.home-command{gap:8px;padding:7px 7px 7px 12px;margin-top:22px}.home-command>.pb-icon{display:none}.home-command input{font-size:16px}.home-actions{grid-template-columns:1fr 1fr;gap:9px}.home-action{font-size:12px;min-height:49px}.home-kpis{gap:8px}.home-kpi{grid-template-columns:1fr;padding:13px 11px;gap:4px;min-height:109px}.home-kpi .kpi-orbit{grid-row:auto;width:27px;height:27px;box-shadow:none;margin-bottom:4px}.home-kpi .kpi-orbit .pb-icon{width:16px;height:16px}.home-kpi b{font-size:23px}.home-kpi .kpi-label{font-size:10px}.home-kpi small{font-size:9px}.home-chip{padding:8px 13px;font-size:11px}
.home-card{padding:14px}.home-card-head{margin-bottom:10px}.home-card-head h3{font-size:14px}.agent-map{height:275px}.agent-node{width:99px;min-height:72px;padding:9px 4px}.agent-node.center{width:70px;height:70px;min-height:70px}.agent-node.center small{display:none}.agent-node .pb-icon{width:20px;height:20px}.agent-node b{font-size:10px}.agent-node small{font-size:9px}.fabric-node{padding:11px 9px 11px 24px}.fabric-node span{font-size:10px}.home-nav{left:50%;right:auto;bottom:max(10px,env(safe-area-inset-bottom));width:calc(100% - 20px);border-radius:18px}.home-nav button{font-size:10px}.home-nav .pb-icon{width:22px}.home-asset-credit{font-size:9px}}
@media(max-width:360px){.home-kpis{grid-template-columns:repeat(3,minmax(0,1fr))}.home-kpi{padding:11px 9px}.agent-node{width:85px}.agent-node.center{width:62px;height:62px;min-height:62px}.home-card{padding:11px}.home-ai-state{max-width:92px}.home-command input{font-size:16px}.org-row{gap:6px}.org-person b{font-size:10px}.pb-workspace-grid{gap:8px}.pb-workspace-grid button{font-size:11px;padding:9px}}
@media(prefers-reduced-motion:reduce){.home-shell *,.home-nav *{transition:none!important;animation:none!important;scroll-behavior:auto!important}}body.reduced-motion .home-shell *,body.reduced-motion .home-nav *{transition:none!important;animation:none!important}
@media(prefers-reduced-transparency:reduce){.home-nav{backdrop-filter:none;background:#081321}.pb-workspaces::backdrop{backdrop-filter:none;background:#000c}}
/* Focused workspaces: executive metrics belong to Home, never above another tab. */
body > main.wrap > .metrics{display:none!important}
body > header .filters,body > header .advancedfilters{display:none!important}
body > header{position:relative;padding:12px 18px;background:rgba(5,11,20,.94);border-bottom:1px solid rgba(106,151,194,.15)}
body > header .brand{flex-direction:row;align-items:center;gap:10px}body > header .brand p{display:none}
body > header .brand h1{font-size:18px;white-space:nowrap}body > header .top{width:auto;display:flex;flex-wrap:nowrap;align-items:center;gap:7px}
body > header .top #login{flex:none;width:auto;max-width:150px;font-size:11px;min-width:48px;min-height:40px}body > header .top .btn{min-height:40px}
body[data-pb-view="home"] > header{padding:16px 24px 6px}body[data-pb-view="home"] > header .brand h1{font-size:20px}
.pb-context-bar{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:0 0 16px}.pb-context-bar h2{font-size:25px;letter-spacing:-.025em;margin:0}.pb-context-bar small{display:block;color:#9ab0c7;font-size:11px;margin-top:4px}.pb-context-bar[hidden]{display:none}
.pb-context-bar button,.pb-filter-close{min-height:44px;padding:8px 12px;border:1px solid #345676;border-radius:11px;color:#d9ebfa;background:#10263b}.pb-context-bar button[hidden]{display:none}.pb-context-bar button[aria-pressed="true"]{border-color:#62b7fa;color:#8bcbff}
.pb-filter-dialog{width:min(900px,calc(100vw - 24px));max-height:86dvh;overflow:auto;padding:18px;background:#0b1726;color:#e4effb;border:1px solid #345574;border-radius:20px;overscroll-behavior:contain}.pb-filter-dialog::backdrop{background:#020712cc;backdrop-filter:blur(6px)}
.pb-filter-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.pb-filter-head h2{margin:0;font-size:22px}.pb-filter-dialog .filters{display:grid!important;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:0}.pb-filter-dialog .filters #search{grid-column:1/-1;min-width:0;width:100%}.pb-filter-dialog .filters .field{min-width:0;width:100%}.pb-filter-dialog #filterToggle{display:none}
.pb-filter-dialog .advancedfilters{display:block!important;margin-top:12px}.pb-filter-dialog .filtergrid{grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.pb-filter-dialog select,.pb-filter-dialog input{min-width:0;max-width:100%;font-size:16px}.pb-filter-dialog .field{padding:9px}.pb-filter-dialog .pb-filter-head{position:sticky;top:-18px;z-index:2;background:#0b1726;padding:10px 0}
.home-scopes [data-open-scope]{margin-left:auto}.home-scopes .pb-icon{width:15px;height:15px;margin-right:5px}
.pb-crawl{display:flex;align-items:center;gap:9px;margin-top:16px;padding:0 7px 0 12px;border:1px solid rgba(111,173,230,.33);border-radius:11px;background:rgba(4,17,33,.86);min-width:0;overflow:hidden;height:46px}
.pb-crawl-label{display:flex;align-items:center;gap:6px;font-size:9px;letter-spacing:.09em;font-weight:700;color:#7cc6ff;flex-shrink:0}.pb-crawl-label::before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor}.pb-crawl[data-state="stale"] .pb-crawl-label{color:#e8bd72}
.pb-crawl-viewport{flex:1;min-width:0;overflow:hidden;mask-image:linear-gradient(90deg,transparent,#000 10px,#000 calc(100% - 10px),transparent)}.pb-crawl-track{display:flex;width:max-content;animation:pb-news-crawl var(--crawl-duration,70s) linear infinite;will-change:transform}.pb-crawl-group{display:flex;align-items:center;flex-shrink:0;min-width:var(--crawl-min-width,300px);gap:26px;padding-right:26px}
.pb-crawl-item{display:flex;gap:8px;align-items:center;flex-shrink:0;min-height:44px;padding:3px 0;background:transparent;border:0;font-size:12px;line-height:1.3;white-space:nowrap;color:#c7d9ee;text-align:left}.pb-crawl-item::before{content:'';width:4px;height:4px;border-radius:50%;background:#5a9acb;flex-shrink:0}.pb-crawl-item[data-kind="warning"]::before{background:#efbd6a}.pb-crawl-item[data-kind="error"]::before{background:#ff8791}.pb-crawl-item[data-kind="success"]::before{background:#69dca8}.pb-crawl-item:hover{color:#fff}.pb-crawl-item:focus-visible{outline:2px solid #81caff;outline-offset:-2px}
.pb-crawl-toggle{display:grid;place-items:center;flex-shrink:0;width:38px;min-height:44px;padding:5px;background:transparent;border:0;color:#9ac1e6}.pb-crawl-toggle .pb-icon{width:16px;height:16px}
@keyframes pb-news-crawl{from{transform:translate3d(0,0,0)}to{transform:translate3d(calc(-1 * var(--crawl-distance,1800px)),0,0)}}
.pb-crawl[data-paused="true"] .pb-crawl-track,.pb-crawl-viewport:focus-within .pb-crawl-track,body:not([data-pb-view="home"]) .pb-crawl-track,.pb-crawl[data-hidden="true"] .pb-crawl-track{animation-play-state:paused}
@media(hover:hover){.pb-crawl-viewport:hover .pb-crawl-track{animation-play-state:paused}}
/* Compact overview with the shared nebula visible through the hero. */
.home-hero{padding:22px 24px 20px}.home-hero h2{margin:15px 0 0;font-size:42px}
.home-command{margin-top:12px}.home-shell{gap:14px}.home-grid{align-items:start}.home-lower{align-items:start}.home-card{box-shadow:inset 0 1px 0 rgba(222,243,255,.09),0 8px 30px #0005}
.home-kpi{padding:13px;gap:2px 9px}.home-kpi small{display:none}.home-kpi .kpi-orbit{grid-row:1/3}.home-kpi .kpi-label{font-size:12px}.home-kpi b{font-size:24px}.home-card-head{margin-bottom:8px}.home-hero-top{min-height:66px}
@media(max-width:650px){
 body > header,body[data-pb-view="home"] > header{padding:10px 12px 7px}.brand{flex-direction:row!important;gap:6px!important}body > header .brand h1,body[data-pb-view="home"] > header .brand h1{font-size:15px;letter-spacing:.04em}body > header .top{gap:5px;width:auto!important}body > header .top #login{max-width:86px;font-size:10px;padding:7px;min-height:40px}body > header .top #refresh,body > header .top #notifyBtn,body > header .top #pbWorkspaceButton{min-width:38px;width:38px;min-height:40px;padding:6px}body > header .top .pb-icon{width:19px;height:19px}body > header .top #notifyBtn{display:none}
 body:not([data-pb-view="home"]) > main.wrap{padding:12px 14px 30px}.pb-context-bar{margin-bottom:12px}.pb-context-bar h2{font-size:24px}.pb-context-bar small{font-size:10px}.pb-context-bar button{font-size:11px;padding:8px 10px}
 .pb-filter-dialog{padding:14px}.pb-filter-dialog .filters,.pb-filter-dialog .filtergrid{grid-template-columns:repeat(2,minmax(0,1fr))}.pb-filter-dialog .filters #priorityFilter{grid-column:1/-1}.pb-filter-dialog .pb-filter-head{top:-14px}
 body[data-pb-view="home"] .wrap{padding:8px 12px 24px}.home-shell{gap:11px}.home-hero{padding:14px 13px;border-radius:16px}.home-hero h2{font-size:30px;margin-top:10px}.home-hero-top{min-height:57px;gap:9px}.home-eyebrow{font-size:9px;max-width:235px;min-height:15px}.home-ai-state{max-width:97px;font-size:8px;padding:5px 7px}
 .pb-crawl{margin-top:12px;height:42px;gap:5px;padding-left:8px}.pb-crawl-item{font-size:11px;min-height:42px}.pb-crawl-label{font-size:8px;gap:4px}.pb-crawl-toggle{width:29px;min-height:42px}.home-command{margin-top:9px;padding:4px 6px 4px 11px}.home-command input{min-height:38px}.home-command button{height:40px;width:40px}
 .home-actions{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;margin-top:10px}.home-action{flex-direction:column;gap:5px;min-height:61px;padding:7px 3px;font-size:10px;line-height:1.25}.home-action .pb-icon{width:18px;height:18px}
 .home-kpis{grid-template-columns:repeat(3,minmax(0,1fr));gap:7px}.home-kpi{grid-template-columns:27px minmax(0,1fr);gap:1px 7px;padding:11px 8px;min-height:65px;border-radius:12px}.home-kpi .kpi-orbit{width:27px;height:27px;margin:0;grid-row:1/3}.home-kpi b{font-size:20px}.home-kpi .kpi-label{font-size:10px;line-height:1.2}.home-kpi small{display:none}.home-scopes{gap:6px;padding-bottom:3px}.home-chip{min-height:40px;font-size:10px;padding:7px 12px}
 .home-card{padding:12px;border-radius:14px}.home-card-head h3{font-size:13px}.home-card-head{margin-bottom:6px}.agent-map{height:232px}.agent-node{width:94px;min-height:63px;padding:6px 3px}.agent-node .pb-icon{margin-bottom:3px}.agent-node small{font-size:8px}.agent-node.center{height:66px;width:66px;min-height:66px}
 .org-map{gap:8px}.org-top{padding:9px 12px}.org-top b{font-size:12px}.org-avatar{height:32px;width:32px}.org-row{gap:7px}.org-person{min-height:68px;padding:9px 4px;gap:4px}.home-lower{gap:11px}.execution-fabric{gap:7px}.fabric-node{min-height:65px;padding:10px 8px 10px 23px}.fabric-node b{font-size:10px}.fabric-node span{font-size:10px}
 .home-nav{bottom:max(8px,env(safe-area-inset-bottom));padding:6px;border-radius:19px}.home-nav button{min-height:49px;font-size:10px;gap:3px}.home-nav .pb-icon{width:22px;height:22px}.chatbox{min-height:0}.chatlog{min-height:220px;max-height:45dvh}.composer textarea{min-height:65px}
}
@media(max-width:360px){body > header .brand h1,body[data-pb-view="home"] > header .brand h1{font-size:13px}body > header .top #login{max-width:65px}.home-kpi{grid-template-columns:23px 1fr;gap:2px 5px;padding:9px 6px}.home-kpi .kpi-orbit{width:23px;height:23px}.home-kpi b{font-size:18px}.home-kpi .kpi-label{font-size:9px}.agent-node{width:81px}.agent-node.center{height:60px;width:60px;min-height:60px}.home-action{font-size:9px}}
@media(prefers-reduced-motion:reduce){.pb-crawl-track{animation:none!important;transform:none!important}.pb-crawl-viewport{overflow-x:auto;mask-image:none}.pb-crawl-group[aria-hidden="true"]{display:none}.pb-crawl-track{will-change:auto}}
body.reduced-motion .pb-crawl-track{animation:none!important;transform:none!important;will-change:auto}body.reduced-motion .pb-crawl-viewport{overflow-x:auto;mask-image:none}body.reduced-motion .pb-crawl-group[aria-hidden="true"]{display:none}
.pb-chat-context{margin-top:12px;border:1px solid #2b435c;border-radius:12px;background:#0c1928;padding:0 12px}.pb-chat-context summary{min-height:44px;cursor:pointer;padding:12px 0;color:#c7dced;font-size:12px}.pb-chat-context summary span{font-size:10px;color:#819db8;margin-left:8px}.pb-chat-context .grid3{padding-bottom:12px}.pb-chat-context:not([open]) .grid3{display:none}@media(max-width:650px){#ai .chatbox>.between>div>.small{display:none}#ai .chatbox>.between{margin-bottom:10px;align-items:center}#ai .chatbox #loadChat{font-size:11px;min-height:40px;padding:8px}#ai .chatbox>.chatlog{min-height:200px;max-height:38dvh}#ai .pb-chat-context .grid3{grid-template-columns:repeat(2,minmax(0,1fr))}#ai .ai-layout>*,#ai .pb-chat-context .label{min-width:0}#ai .pb-chat-context select{min-width:0;width:100%}#ai .composer{margin-top:12px}#ai .composer textarea{font-size:16px}#ai .chatbox>.between h2{font-size:15px}}
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
            <div class="home-eyebrow"><span class="home-status-dot"></span><span id="homeSystemState">Verifying system health…</span></div>
            <h2>${escH(workspaceProfile.assistant_name)}</h2>

          </div>
          <div id="homeAIState" class="home-ai-state">AI UNKNOWN</div>
        </div>
        <section id="pbLiveCrawl" class="pb-crawl" data-state="connecting" aria-label="Live activity across all projects">
          <span class="pb-crawl-label">UPDATES</span><div class="pb-crawl-viewport"><div class="pb-crawl-track" aria-live="off"></div></div>
          <button id="pbCrawlToggle" type="button" class="pb-crawl-toggle" aria-label="Pause activity crawl" aria-pressed="false">${icon('blocked')}</button>
        </section>
        <div class="home-actions">
          <button type="button" class="home-action authonly" data-home-action="create">${icon("plus")}New work</button>
          <button type="button" class="home-action" data-home-action="agents">${icon("agents")}Review agents</button>
          <button type="button" class="home-action" data-home-action="troubleshoot">${icon("tools")}Troubleshoot</button>
        </div>
      </section>
      <section id="homeKpis" class="home-kpis"></section>
      <section id="homeScopes" class="home-scopes"></section>
      <section class="home-grid">
        <article class="home-card"><div class="home-card-head"><h3>${icon("sparkle")}Live agents</h3><button type="button" data-home-go="agents" aria-label="Open Agents">›</button></div><div id="homeAgentMap" class="agent-map"></div><div class="fabric-heading">${icon("layers")}Execution Fabric</div><div id="homeExecutionFabric" class="execution-fabric" aria-label="Live execution fabric"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>${icon("team")}Team / org map</h3><button type="button" data-home-go="team" aria-label="Open Team">›</button></div><div id="homeOrgMap" class="org-map"></div></article>
      </section>
      <section class="home-lower">
        <article class="home-card"><div class="home-card-head"><h3>${icon("activity")}Current activity</h3><button type="button" data-home-go="activity" aria-label="Open Activity">›</button></div><div id="homeActivity" class="activity-feed"></div></article>
        <article class="home-card"><div class="home-card-head"><h3 id="homeWorkTitle">My work</h3><button type="button" data-home-go="board" aria-label="Open Kanban">›</button></div><div id="homeWork" class="work-list"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>${icon("review")}Ready for review</h3><button type="button" data-home-go="agents" aria-label="Open review queue">›</button></div><div id="homeApprovals" class="approval-list"></div></article>
      </section>
      <section class="home-grid">
        <article class="home-card"><div class="home-card-head"><h3>${icon("memory")}Recent memories</h3><button type="button" data-home-go="agents" aria-label="Open memory">›</button></div><div id="homeMemory" class="memory-feed"></div></article>
        <article class="home-card"><div class="home-card-head"><h3>${icon("projects")}Portfolio pulse</h3><button type="button" data-home-go="portfolio" aria-label="Open Portfolio">›</button></div><div id="homePortfolio"></div></article>
      </section>
      <div class="home-asset-credit"><span>PRFKT_PROJECT · Your executive workspace</span></div>
    </div>`;
  if (main) main.insertBefore(home, portfolio || main.firstChild);

  const nav = document.createElement('nav');
  nav.className = 'home-nav';
  nav.setAttribute('aria-label','Primary mobile navigation');
  nav.innerHTML = `
    <button type="button" data-home-go="home">${icon("home")}<span>Home</span></button>
    <button type="button" data-home-go="portfolio">${icon("projects")}<span>Projects</span></button>
    <button type="button" data-home-go="agents">${icon("agents")}<span>Agents</span></button>
    <button type="button" data-home-go="ai">${icon("chat")}<span>Chat</span></button>
    <button type="button" data-home-go="settings">${icon("settings")}<span>Settings</span></button>`;
  document.body.appendChild(nav);

  function go(view) {
    const target = [...document.querySelectorAll('.tab[data-view]')].find(t=>t.dataset.view===view);
    if(!target)return;
    document.getElementById('pbWorkspaces')?.close();
    target.click();
    document.body.dataset.pbView=view;
    syncHomeNav();
    window.scrollTo({top:0,behavior:'instant'});
  }

  function homeTaskScope(mode) {
    if(mode==='all'){if(typeof clearFilters==='function')clearFilters();renderHome();return;}
    const selfName=session?.name&&session.name!=='Public'?session.name:'';
    const map={self:['ownerFilter',selfName],ai:['executorFilter','ai'],critical:['priorityFilter','Critical'],week:['dueFilter','7d']};
    workspaceProfile.owner_shortcuts.forEach((name,i)=>map[ownerShortcutKey(name,i)]=['ownerFilter',name]);
    const pair=map[mode];if(!pair||!pair[1])return;
    const control=document.getElementById(pair[0]);
    setHomeFilter(pair[0],control?.value===pair[1]?'':pair[1]);
    if(typeof renderAll==='function')renderAll();
  }

  const kpiLabels={open:'Open',active:'Active',blocked:'Blocked',critical:'Critical',ai:'AI running',due:'Due soon'};
  const kpiDialog=document.createElement('dialog');
  kpiDialog.id='homeKpiDialog';kpiDialog.className='pb-filter-dialog home-kpi-dialog';
  kpiDialog.setAttribute('aria-labelledby','homeKpiTitle');
  document.body.appendChild(kpiDialog);
  const drilldownStyle=document.createElement('style');
  drilldownStyle.textContent='.home-kpi-dialog{width:min(780px,calc(100vw - 24px));max-height:85dvh;padding:18px}.home-kpi-dialog .pb-filter-head{gap:16px}.home-kpi-dialog h2{margin:0}.home-kpi-scope{color:#a8bfd4;font-size:12px;line-height:1.6;margin:12px 0}.home-kpi-records{display:grid;gap:10px;padding:0;list-style:none}.home-kpi-record{border:1px solid var(--home-border);border-radius:12px;padding:13px;min-width:0}.home-kpi-record h3{margin:0 0 7px;font-size:14px;overflow-wrap:anywhere}.home-kpi-record p{color:#b5c5d7;font-size:12px;margin:6px 0;overflow-wrap:anywhere}.home-kpi-record .flex{gap:8px;margin-top:10px}.home-kpi-record button{min-height:40px}.home-kpi-dialog::backdrop{background:#020810ad}';
  document.head.appendChild(drilldownStyle);

  function homeKpiRecords(kind,scoped) {
    if(kind==='ai'){const ids=new Set(scoped.map(t=>t.id));return (runs||[]).filter(r=>ids.has(r.task_id)&&r.status==='running');}
    return scoped.filter(t=>{
      if(kind==='active'||kind==='blocked')return t.status===(kind==='active'?'Active':'Blocked');
      if(t.status==='Done')return false;
      if(kind==='critical')return t.priority==='Critical';
      if(kind==='due'){const d=typeof daysUntil==='function'?daysUntil(t.due_date):null;return d!==null&&d>=0&&d<=7;}
      return kind==='open';
    });
  }

  function homeScopeDescription() {
    const criteria=typeof filterCriteria==='function'?filterCriteria():{};
    const labels={search:'Search',projectFilter:'Project',ownerFilter:'Owner',leadFilter:'Lead',priorityFilter:'Priority',statusFilter:'Status',completionFilter:'Completion',dueFilter:'Due',runFilter:'Run state'};
    return Object.entries(criteria).filter(([,value])=>value).map(([key,value])=>{
      const el=document.getElementById(key),label=labels[key]||el?.closest('label')?.firstChild?.textContent?.trim()||key.replace(/Filter$/,'');
      const selected=el?.selectedOptions?.[0]?.textContent||value;
      return `${label}: ${selected}`;
    }).join(' · ')||'All projects';
  }

  function homeKpiScope(kind) {
    if(!kpiLabels[kind])return;
    // Use the same pre-click scope and predicate as the counter, including runs
    // that are still running even if a task's last_run_id now points elsewhere.
    const scoped=safeVisible(),records=homeKpiRecords(kind,scoped),scope=homeScopeDescription();
    const byTask=new Map(scoped.map(t=>[t.id,t]));
    const rows=records.map(record=>{
      const task=kind==='ai'?byTask.get(record.task_id):record;
      const details=kind==='ai'?`${record.agent_key||'Agent'} · Running · ${record.project||task?.project||''}`:`${task.project||'No project'} · ${task.owner||'Unassigned'} · ${task.status} · ${task.priority||'No priority'}`;
      const actions=(kind==='ai'?`<button type="button" class="mini" data-home-run="${escH(record.id)}">Open run</button>`:'')+(task?`<button type="button" class="mini" data-home-task="${escH(task.id)}">Open task</button><button type="button" class="mini" data-home-board="${escH(task.project||'')}">Open board</button>`:'');
      return `<li class="home-kpi-record" data-home-record="${escH(record.id)}"><h3>${escH(task?.title||record.id)}</h3><p>${escH(details)}</p>${task?.due_date?`<p>Due ${escH(task.due_date)}</p>`:''}${kind==='ai'?`<p>Run ${escH(record.id)}</p>`:''}<div class="flex">${actions}</div></li>`;
    }).join('');
    kpiDialog.innerHTML=`<div class="pb-filter-head"><h2 id="homeKpiTitle">${kpiLabels[kind]} · ${records.length} ${kind==='ai'?'runs':'tasks'}</h2><button type="button" class="pb-filter-close" data-home-kpi-close aria-label="Close matching records">Done</button></div><p class="home-kpi-scope">${escH(scope)}</p>${records.length?`<ul class="home-kpi-records">${rows}</ul>`:'<p class="home-empty">No matching records in this scope.</p>'}`;
    // Preserve the established filter interaction for task counters. AI running
    // cannot use runFilter=active: that includes queued and only checks last_run_id.
    const pair={open:['completionFilter','open'],active:['statusFilter','Active'],blocked:['statusFilter','Blocked'],critical:['priorityFilter','Critical'],due:['dueFilter','7d']}[kind];
    if(pair)setHomeFilter(...pair);
    if(typeof renderAll==='function')renderAll();
    if(!kpiDialog.open)kpiDialog.showModal();
  }
  kpiDialog.addEventListener('click',event=>{if(event.target.closest('[data-home-kpi-close]'))kpiDialog.close();});

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
    const counts=Object.fromEntries(Object.keys(kpiLabels).map(kind=>[kind,homeKpiRecords(kind,scoped).length]));
    const c=typeof filterCriteria==='function'?filterCriteria():{};
    const data=[['Open',counts.open,'open','work items',c.completionFilter==='open'],['Active',counts.active,'active','in progress',c.statusFilter==='Active'],['Blocked',counts.blocked,'blocked','needs attention',c.statusFilter==='Blocked'],['Critical',counts.critical,'critical','highest priority',c.priorityFilter==='Critical'],['AI running',counts.ai,'ai','running records',false],['Due soon',counts.due,'due','next 7 days',c.dueFilter==='7d']];
    const el=document.getElementById('homeKpis');if(!el)return;
    el.innerHTML=data.map(([label,value,key,sub,pressed])=>`<button type="button" class="home-kpi" data-kind="${key}" data-home-kpi="${key}" aria-label="Show ${value} ${label} ${key==='ai'?'runs':'tasks'}" aria-haspopup="dialog" aria-pressed="${pressed}"><div class="kpi-orbit">${icon(key)}</div><b>${value}</b><span class="kpi-label">${label}</span><small>${sub}</small></button>`).join('');
  }

  function renderScopes() {
    const c=typeof filterCriteria==='function'?filterCriteria():{};
    const selfName=session?.name&&session.name!=='Public'?session.name:'';
    const isAll=!Object.values(c).some(Boolean);
    const chips=[['all','All projects',isAll]];
    if(selfName)chips.push(['self','My work',c.ownerFilter===selfName]);
    workspaceProfile.owner_shortcuts.forEach((name,i)=>{if(name!==selfName)chips.push([ownerShortcutKey(name,i),name,c.ownerFilter===name]);});
    chips.push(['ai','AI work',c.executorFilter==='ai'],['critical','Critical',c.priorityFilter==='Critical'],['week','This week',c.dueFilter==='7d']);
    const el=document.getElementById('homeScopes');if(!el)return;
    el.innerHTML=chips.map(([key,label,active])=>`<button type="button" class="home-chip ${active?'active':''}" data-home-scope="${key}" aria-pressed="${active}">${escH(label)}</button>`).join('')+'<button type="button" class="home-chip" data-open-scope="1">All filters</button>';
  }

  function renderAgentMap(scoped) {
    const el=document.getElementById('homeAgentMap');if(!el)return;
    const ids=new Set(scoped.map(t=>t.id)),selected=document.getElementById('agentFilter')?.value||'';
    const activeKeys=new Set((runs||[]).filter(r=>ids.has(r.task_id)&&r.status==='running').map(r=>r.agent_key));
    const preferred=['architect','verifier','builder','release','researcher','help'];
    const enabled=(agents||[]).filter(a=>a.enabled&&a.agent_key!=='executive').sort((a,b)=>{
      const rank=a=>(a.agent_key===selected?-100:0)+(activeKeys.has(a.agent_key)?-50:0)+(preferred.indexOf(a.agent_key)<0?99:preferred.indexOf(a.agent_key));
      return rank(a)-rank(b)||String(a.name).localeCompare(String(b.name));
    }).slice(0,6);
    const positions=[[18,17],[82,17],[18,50],[82,50],[18,83],[82,83]];
    const icons={architect:'memory',builder:'code',verifier:'review',release:'settings',researcher:'sparkle',help:'help'};
    let lines='<svg class="agent-lines" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">';
    enabled.forEach((a,i)=>{const [x,y]=positions[i];lines+=`<path class="${activeKeys.has(a.agent_key)?'live-link':''}" d="M50 50 C${x<50?32:68} 50,${x<50?38:62} ${y},${x} ${y}"/>`;});
    lines+='</svg>';
    const center=`<button type="button" class="agent-node center" style="left:50%;top:50%" data-home-go="ai" aria-label="Open ${escH(workspaceProfile.assistant_name)}">${icon('user')}<b>${escH(workspaceProfile.assistant_name)}</b><small>Executive</small></button>`;
    const nodes=enabled.map((a,i)=>{const [x,y]=positions[i];return `<button type="button" class="agent-node ${activeKeys.has(a.agent_key)?'running':''}" style="left:${x}%;top:${y}%" data-home-agent="${escH(a.agent_key)}" aria-label="Inspect ${escH(a.name)}">${icon(icons[a.agent_key]||'agents')}<b>${escH(a.name)}</b><small>${activeKeys.has(a.agent_key)?'Running':'Configured role'}</small></button>`;}).join('');
    el.innerHTML=lines+center+nodes+(enabled.length?'':'<span style="position:absolute;bottom:12px;left:0;right:0;text-align:center" class="small">Sign in to inspect your configured agents.</span>');
  }

  function renderExecutionFabric(health=window.__PROJECT_BYTE_HEALTH_LAST__||null) {
    const el=document.getElementById('homeExecutionFabric');if(!el)return;
    const bridges=health?.components?.bridges||{},modelsHealth=health?.components?.models||{};
    const bridgeNode=(key,label,go='agents')=>{
      const x=bridges[key]||{},state=x.state||'unknown',progress=x.progress_state||'unknown',pending=Number(x.pending_count||0),execution=x.execution_state||'unknown',reason=x.execution_reason||'',activeRef=x.active_run_ref||'',source=x.execution_source||'',externalElapsed=x.external_elapsed_seconds;
      const process=progress&&progress!=='unknown'?`${state}/${progress}`:state;
      const visual=x.owner_paused&&!x.external_running?'paused':state==='failed'||execution==='blocked'?'failed':state==='healthy'&&(execution==='ready'||execution==='running')?'healthy':'unknown';
      const elapsed=externalElapsed===null||externalElapsed===undefined?'':` · ${Math.max(0,Math.round(Number(externalElapsed)/60))}m`;
      const active=activeRef ? ' · Active: '+activeRef+(source?' · '+source:'')+(source==='external-bridge'?elapsed:'') : '';
      const detail=x.owner_paused&&!x.external_running?'Paused by owner · execution disabled':`Process: ${process}${active} · Execution: ${execution}${reason?' · '+reason:''}`;
      return `<button type="button" class="fabric-node" data-state="${escH(visual)}" data-home-go="${go}" aria-label="Open ${escH(label)} diagnostics"><b>${escH(label)}</b><span>${escH(detail)}${pending?' · '+pending+' pending':''}</span></button>`;
    };
    const modelState=modelsHealth.state||'unknown',tested=Number(modelsHealth.tested_ok||0),enabled=Number(modelsHealth.enabled||0);
    el.innerHTML=bridgeNode('stickdeath','StickDeath')+bridgeNode('vitros','VITROS builder')+bridgeNode('vitros_verifier','VITROS verifier')+`<button type="button" class="fabric-node" data-state="${escH(modelState)}" data-home-go="models" aria-label="Open Model Hub"><b>Model Hub</b><span>${escH(modelState)} · ${tested}/${enabled} tested</span></button>`;
  }

  function renderOrg() {
    const el=document.getElementById('homeOrgMap');if(!el)return;
    const who=session?.ok?session.name:'Your workspace',role=session?.ok?session.role:'Sign in to manage work';
    const initials=who.split(/\s+/).map(x=>x[0]||'').join('').slice(0,2).toUpperCase();
    const people=(team||[]).filter(p=>p.active&&p.name!==who).slice(0,4);
    const row=[...people.map(p=>({name:p.name,sub:p.role,ico:'user'})),{name:'AI Agents',sub:`${(agents||[]).filter(a=>a.enabled).length} configured`,ico:'agents'},{name:'Systems',sub:`${(projects||[]).filter(p=>p.repo).length} repositories`,ico:'layers'}];
    el.innerHTML=`<div class="org-top"><span class="org-avatar">${escH(initials)}</span><div><b>${escH(who)}</b><small>${escH(role)}</small></div></div><div class="org-row">${row.map(p=>`<button type="button" class="org-person" data-home-person="${escH(p.name)}">${icon(p.ico)}<b>${escH(p.name)}</b><small>${escH(p.sub)}</small></button>`).join('')}</div><div class="org-row">${[['Research','researcher','sparkle'],['Development','builder','code'],['Operations','release','settings']].map(([label,key,ico])=>`<button type="button" class="org-person" data-home-agent="${key}">${icon(ico)}<b>${label}</b><small>Inspect role</small></button>`).join('')}</div>`;
  }

  function renderActivity(scoped) {
    const el=document.getElementById('homeActivity');if(!el)return;
    const c=typeof filterCriteria==='function'?filterCriteria():{},filtered=typeof activeFilterCount==='function'&&activeFilterCount()>0;
    const ids=new Set(scoped.map(t=>t.id)),names=new Set(scoped.map(t=>t.project));
    const source=(activity||[]).filter(a=>!filtered||(a.task_id?ids.has(a.task_id):a.project&&names.has(a.project)&&!c.ownerFilter));
    const items=source.slice(0,5);
    el.innerHTML=items.length?items.map(a=>`<button type="button" class="activity-item" ${a.task_id?`data-home-task="${escH(a.task_id)}"`:'data-home-go="activity"'}>${icon('activity')}<div><b>${escH(a.action.replaceAll('_',' '))}</b><span>${escH(a.actor||'system')}${a.project?' · '+escH(a.project):''}${a.detail?' · '+escH(a.detail):''}</span><time>${shortTime(a.ts)}</time></div></button>`).join(''):`<div class="home-empty">${icon('activity')}No recent activity in this scope.<button type="button" data-home-go="activity">Open activity log</button></div>`;
  }

  function renderWork(scoped) {
    const el=document.getElementById('homeWork');if(!el)return;
    const title=document.getElementById('homeWorkTitle');
    const authenticated=session?.name&&session.name!=='Public',who=authenticated?session.name:'';
    const selectedOwner=document.getElementById('ownerFilter')?.value||'';
    const targetOwner=selectedOwner||(authenticated?who:'');
    if(title)title.textContent=targetOwner?(targetOwner===who?'My work':`${targetOwner}’s work`):'Open work';
    let items=targetOwner?scoped.filter(t=>t.status!=='Done'&&t.owner===targetOwner):scoped.filter(t=>t.status!=='Done');
    const rank={Critical:0,High:1,Medium:2,Low:3};
    items=items.sort((a,b)=>(rank[a.priority]??9)-(rank[b.priority]??9)).slice(0,4);
    const empty=targetOwner?`No open work assigned to ${escH(targetOwner)} in this scope.`:'No open work in this scope.';
    el.innerHTML=items.length?items.map(t=>`<button type="button" class="work-item" data-home-task="${escH(t.id)}"><div class="work-top"><b>${escH(t.title)}</b><span class="home-priority ${escH(t.priority)}">${escH(t.priority)}</span></div><span>${escH(t.project)}${t.due_date?' · due '+escH(t.due_date):''}${t.ai_state?' · AI '+escH(t.ai_state):''}</span></button>`).join(''):`<div class="small">${empty}</div>`;
  }

  function syncExternalReviewIdentity() {
    // Compare credentials only in memory; never include them in rendered evidence.
    const identity=JSON.stringify([accessKey,session?.subject||'',session?.name||'',session?.level||0]);
    if(identity!==homeExternalReviewIdentity){
      homeExternalReviewIdentity=identity;++homeExternalReviewRequest;
      homeExternalReviewController?.abort();homeExternalReviewController=null;
      homeExternalReviewRefreshBusy=false;homeExternalReviewUpdatedAt=0;
      homeExternalReviewState={state:'unavailable',items:[],repositories:{}};
    }
    return !!accessKey&&session?.level>=1;
  }

  function externalReviewAge(seconds) {
    if(seconds===null||seconds===undefined||Number.isNaN(Number(seconds)))return 'unknown age';
    const value=Math.max(0,Number(seconds));
    if(value<60)return `${Math.round(value)}s ago`;
    if(value<3600)return `${Math.round(value/60)}m ago`;
    if(value<86400)return `${Math.round(value/3600)}h ago`;
    return `${Math.round(value/86400)}d ago`;
  }

  function externalReviewsForScope(scoped) {
    if(!syncExternalReviewIdentity())return [];
    const all=tasks||[],selected=document.getElementById('projectFilter')?.value||'';
    const scopedProjects=new Set((scoped||[]).map(t=>t.project));
    return (homeExternalReviewState.items||[]).filter(item=>{
      if(selected)return item.project===selected;
      if((scoped||[]).length===all.length)return true;
      return scopedProjects.has(item.project);
    }).slice(0,6);
  }

  function renderApprovals(scoped) {
    syncExternalReviewIdentity();
    const el=document.getElementById('homeApprovals');if(!el)return;
    const ids=new Set(scoped.map(t=>t.id));
    const ready=(runs||[]).filter(r=>ids.has(r.task_id)&&(r.status==='pr-created'||r.pr_url)).slice(0,6);
    const ownedHtml=ready.map(r=>{
      const approval=homeApprovalState.find(a=>a.run_id===r.id);
      const controls=approval&&approval.status==='pending'&&session?.level>=2?`<button type="button" data-home-approval="${escH(approval.id)}" data-review-action="approve">Approve review</button><button type="button" data-home-approval="${escH(approval.id)}" data-review-action="request_changes">Request changes</button>`:'';
      const decided=approval&&approval.status!=='pending'?`<span class="approval-state">${escH(approval.status)}${approval.decided_by?' · '+escH(approval.decided_by):''}</span>`:'';
      return `<div class="approval-item"><b>${escH(r.project)} · #${r.issue_number}</b><span>${escH(r.agent_key||'agent')} produced a reviewable result.</span>${decided}<div class="approval-actions">${r.pr_url?`<a href="${escH(r.pr_url)}" target="_blank" rel="noopener">Open PR</a>`:''}<button type="button" data-home-run="${escH(r.id)}">Terminal</button>${controls}</div>${approval?'<span class="approval-note">PRFKT_PROJECT approval records your review decision only. It never merges the PR.</span>':''}</div>`;
    }).join('');
    const external=externalReviewsForScope(scoped);
    const externalHtml=external.map(item=>{
      const checks=item.checks||{},failed=Number(checks.failed||0),pending=Number(checks.pending||0),passed=Number(checks.passed||0),unknown=Number(checks.unknown||0);
      const checksText=failed?`${failed} failed`:pending?`${pending} pending`:unknown?`${unknown} unknown`:passed?`${passed} passed`:'checks unknown';
      const evidence=item.evidence_state==='stale'?`stale · ${externalReviewAge(item.evidence_age_seconds)}`:item.evidence_state==='fresh'?'fresh':'evidence unavailable';
      return `<div class="approval-item external-review"><b>${escH(item.project)} · PR #${Number(item.number||0)}</b><span>${escH(item.title||'Reviewable pull request')}</span><div class="external-review-meta"><em>external review</em>${item.draft?'<em>draft</em>':''}<em>${escH(checksText)}</em><em>${escH(item.merge_state||'UNKNOWN')}</em><em>${escH(externalReviewAge(item.updated_age_seconds))}</em><em>${escH(evidence)}</em></div><div class="approval-actions"><a href="${escH(item.url)}" target="_blank" rel="noopener">Open GitHub PR</a></div><span class="approval-note">Read-only observation. PRFKT_PROJECT cannot approve, comment, merge, close, rerun or deploy this external PR.</span></div>`;
    }).join('');
    const evidenceBanner=homeExternalReviewState.state==='unavailable'?'<div class="small">External review evidence is unavailable; PRFKT_PROJECT is not assuming the queue is empty.</div>':homeExternalReviewState.state==='partial'?'<div class="small">External review evidence is partial; unavailable repositories are not assumed clear.</div>':homeExternalReviewState.state==='stale'?'<div class="small">External review evidence is stale; verify GitHub before acting.</div>':'';
    el.innerHTML=evidenceBanner+ownedHtml+externalHtml || '<div class="home-empty">No observed review work in this scope.</div>';
  }

  async function refreshExternalReviews(force=false) {
    if(!syncExternalReviewIdentity()){renderApprovals(safeVisible());return;}
    const now=Date.now();
    if(homeExternalReviewRefreshBusy||(!force&&homeExternalReviewUpdatedAt&&now-homeExternalReviewUpdatedAt<15000))return;
    homeExternalReviewRefreshBusy=true;
    const request=++homeExternalReviewRequest,controller=new AbortController();
    homeExternalReviewController=controller;
    const current=()=>{syncExternalReviewIdentity();return request===homeExternalReviewRequest;};
    try{
      const x=await api('/api/external-reviews',{signal:controller.signal});
      if(!current())return;
      homeExternalReviewState={state:x.state||'unavailable',items:Array.isArray(x.items)?x.items:[],repositories:x.repositories||{}};
      homeExternalReviewUpdatedAt=Date.now();
      renderApprovals(safeVisible());
    }catch{
      if(!current())return;
      homeExternalReviewState={state:'unavailable',items:[],repositories:{}};
      homeExternalReviewUpdatedAt=Date.now();
      renderApprovals(safeVisible());
    }finally{if(request===homeExternalReviewRequest){homeExternalReviewRefreshBusy=false;homeExternalReviewController=null;}}
  }

  async function refreshHomeApprovals() {
    if(homeApprovalRefreshBusy)return;
    if(!(session?.level>=2)){homeApprovalState=[];renderApprovals(safeVisible());return;}
    homeApprovalRefreshBusy=true;
    try{const x=await api('/api/approvals');homeApprovalState=x.approvals||[];renderApprovals(safeVisible())}catch{homeApprovalState=[]}finally{homeApprovalRefreshBusy=false}
  }

  async function decideHomeApproval(id,action) {
    if(!id||!['approve','request_changes'].includes(action))return;
    const label=action==='approve'?'Approve this review? This will not merge the PR.':'Request changes on this review? This will not modify the PR automatically.';
    if(!confirm(label))return;
    try{
      await api('/api/approvals/'+encodeURIComponent(id)+'/decision',{method:'POST',body:JSON.stringify({action})});
      const status=document.getElementById('status');if(status)status.textContent=action==='approve'?'Review approved · PR remains unmerged':'Changes requested · PR remains unmodified';
      await refreshHomeApprovals();
    }catch(error){const status=document.getElementById('status');if(status)status.textContent='Review decision failed: '+(error?.message||'unknown error')}
  }

  function renderMemory(scoped) {
    const el=document.getElementById('homeMemory');if(!el)return;
    const allTasks=tasks||[],filtered=scoped.length!==allTasks.length,names=new Set(scoped.map(t=>t.project)),agent=document.getElementById('agentFilter')?.value||'';
    let source=memory||[];
    if(filtered)source=source.filter(m=>!m.project||names.has(m.project));
    if(agent)source=source.filter(m=>!m.agent_key||m.agent_key===agent);
    const items=source.slice(0,5);
    el.innerHTML=items.length?items.map(m=>`<div class="memory-item"><b>${escH(m.kind)} · ${escH(m.agent_key||'shared')}</b><span>${escH(m.content)}</span></div>`).join(''):'<div class="small">No agent memory available in this scope.</div>';
  }

  function renderPortfolio(scoped) {
    const el=document.getElementById('homePortfolio');if(!el)return;
    const filtered=typeof activeFilterCount==='function'?activeFilterCount()>0:scoped.length!==(tasks||[]).length;
    const names=new Set(scoped.map(t=>t.project));
    const list=(projects||[]).filter(p=>!filtered||names.has(p.name)).slice(0,5);
    el.innerHTML=list.map(p=>{
      const ts=scoped.filter(t=>t.project===p.name),done=ts.filter(t=>t.status==='Done').length,pct=ts.length?Math.round(done/ts.length*100):null;
      return `<button type="button" class="work-item" data-home-project="${escH(p.name)}"><div class="work-top"><b>${escH(p.name)}</b><span class="health" data-h="${escH(p.health)}">${escH(p.health)}</span></div><span>Lead ${escH(p.lead||'Unassigned')} · ${pct===null?'No tracked work':pct+'% complete · '+done+'/'+ts.length+' tasks'}</span><div class="pulse-progress" aria-hidden="true"><i style="width:${pct||0}%"></i></div></button>`;
    }).join('')||'<div class="home-empty">No projects match this scope.<button type="button" data-home-scope="all">Clear filters</button></div>';
  }

  function renderHome() {
    if (!document.getElementById('home')) return;
    const scoped=safeVisible();
    renderKpis(scoped); renderScopes(); renderAgentMap(scoped); renderExecutionFabric(); renderOrg(); renderActivity(scoped); renderWork(scoped); renderApprovals(scoped); renderMemory(scoped); renderPortfolio(scoped); void refreshHomeApprovals(); void refreshExternalReviews();
    renderLiveCrawl();syncHomeNav();
    if(typeof window.CustomEvent==='function')window.dispatchEvent(new CustomEvent('project-byte-home-render'));
  }

  const oldRenderAll = typeof renderAll === 'function' ? renderAll : null;
  if (oldRenderAll) renderAll = function(){ oldRenderAll(); renderHome(); };
  document.addEventListener('click',e=>{
    const b=e.target.closest('[data-view],[data-home-go],[data-home-action],[data-home-scope],[data-home-kpi],[data-home-agent],[data-home-person],[data-home-task],[data-home-run],[data-home-project],[data-home-board],[data-home-approval]');
    if(!b)return;
    if(b.dataset.view){document.body.dataset.pbView=b.dataset.view;syncHomeNav();setTimeout(renderHome,0)}
    if(b.dataset.homeGo){go(b.dataset.homeGo);e.preventDefault()}
    if(b.dataset.homeScope){homeTaskScope(b.dataset.homeScope);e.preventDefault()}
    if(b.dataset.homeKpi){homeKpiScope(b.dataset.homeKpi);e.preventDefault()}
    if(b.dataset.homeAgent){if(document.getElementById('agentFilter'))document.getElementById('agentFilter').value=b.dataset.homeAgent;if(typeof renderAll==='function')renderAll();go('agents');e.preventDefault()}
    if(b.dataset.homePerson){if(b.dataset.homePerson==='AI Agents')go('agents');else if(b.dataset.homePerson==='Systems')go('settings');else {if(document.getElementById('ownerFilter'))document.getElementById('ownerFilter').value=b.dataset.homePerson;if(typeof renderAll==='function')renderAll();go('board')}e.preventDefault()}
    if(b.dataset.homeTask){if(kpiDialog.open)kpiDialog.close();if(typeof openTask==='function')openTask(b.dataset.homeTask);e.preventDefault()}
    if(b.dataset.homeRun){if(kpiDialog.open)kpiDialog.close();if(document.getElementById('terminalRun'))document.getElementById('terminalRun').value=b.dataset.homeRun;go('terminalView');if(typeof terminalLoad==='function')terminalLoad();e.preventDefault()}
    if(b.hasAttribute('data-home-board')){if(kpiDialog.open)kpiDialog.close();setHomeFilter('projectFilter',b.dataset.homeBoard);if(typeof renderAll==='function')renderAll();go('board');e.preventDefault()}
    if(b.dataset.homeProject){if(document.getElementById('projectFilter'))document.getElementById('projectFilter').value=b.dataset.homeProject;if(typeof renderAll==='function')renderAll();go('portfolio');e.preventDefault()}
    if(b.dataset.homeApproval){void decideHomeApproval(b.dataset.homeApproval,b.dataset.reviewAction);e.preventDefault()}
    if(b.dataset.homeAction){
      if(b.dataset.homeAction==='ask'){go('ai');setTimeout(()=>document.getElementById('chatInput')?.focus(),30)}
      if(b.dataset.homeAction==='create'&&typeof openTask==='function')openTask('');
      if(b.dataset.homeAction==='agents')go('agents');
      if(b.dataset.homeAction==='troubleshoot'){go('ai');setTimeout(()=>{const a=document.getElementById('aiAgent');if(a)a.value='help';const i=document.getElementById('chatInput');if(i){i.value='Help me troubleshoot the current PRFKT_PROJECT state and tell me exactly where to go.';i.focus()}},30)}
      e.preventDefault();
    }
  });

  function setHomeFilter(id,value) {
    const el=document.getElementById(id);if(!el)return;
    if(value&&el.tagName==='SELECT'&&![...el.options].some(o=>o.value===value))el.add(new Option(value,value));
    el.value=value;
    const saved=document.getElementById('savedView');if(saved)saved.value='';
  }
  const unassignedLead='__pb_unassigned_project_lead__';
  function withoutUnassignedLead(callback) {
    const control=document.getElementById('leadFilter');
    if(control?.value!==unassignedLead)return callback(false);
    control.value='';
    try{return callback(true);}finally{control.value=unassignedLead;}
  }
  const originalVisible=typeof visible==='function'?visible:null;
  if(originalVisible)visible=function(){return withoutUnassignedLead(unassigned=>{const rows=originalVisible();return unassigned?rows.filter(t=>!projectFor(t).lead):rows;});};
  const originalProjectMatches=typeof projectMatches==='function'?projectMatches:null;
  if(originalProjectMatches)projectMatches=function(project,rows){return withoutUnassignedLead(unassigned=>(!unassigned||!project.lead)&&originalProjectMatches(project,rows));};
  document.addEventListener('click',event=>{
    const button=event.target.closest('#projects [data-lead]');
    if(!button)return;
    // Capture before the base handler opens the advanced panel inside a closed dialog.
    event.preventDefault();event.stopImmediatePropagation();
    const lead=button.getAttribute('data-lead')||'',control=document.getElementById('leadFilter');
    if(!lead&&control&&![...control.options].some(o=>o.value===unassignedLead))control.add(new Option('Unassigned lead',unassignedLead));
    setHomeFilter('leadFilter',lead||unassignedLead);
    if(typeof renderAll==='function')renderAll();
    syncWorkspaceChrome();
    document.getElementById('pbWorkspaceTitle')?.scrollIntoView({block:'nearest'});
  },true);

  function syncHomeNav() {
    syncWorkspaceChrome();
    const view=document.body.dataset.pbView||'home';
    const groups={board:'portfolio',intelligence:'portfolio',terminalView:'agents',models:'agents',team:'settings',help:'settings',activity:'home'};
    document.querySelectorAll('.home-nav button').forEach(b=>{const active=b.dataset.homeGo===(groups[view]||view);b.classList.toggle('active',active);if(active)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  }
  const brand=document.querySelector('.brand h1');
  if(brand&&brand.textContent.trim()==='PRFKT_PROJECT')brand.innerHTML='PRFKT<span class="pb-wordmark">_PROJECT</span>';
  const subtitle=document.querySelector('.brand p');if(subtitle)subtitle.textContent='People × Agents × Progress';
  const drawer=document.createElement('dialog');drawer.id='pbWorkspaces';drawer.className='pb-workspaces';drawer.setAttribute('aria-labelledby','pbWorkspacesTitle');
  const spaces=[['home','Home','home'],['portfolio','Projects','projects'],['board','Kanban board','open'],['intelligence','Work next','critical'],['agents','Agents','agents'],['ai','AI_BYTE chat','chat'],['terminalView','Execution logs','code'],['models','Model Hub','ai'],['team','Team access','team'],['activity','Activity','activity'],['settings','Settings','settings'],['help','Help / How-To','help']];
  drawer.innerHTML=`<div class="pb-drawer-head"><h2 id="pbWorkspacesTitle">Your workspace</h2><button type="button" class="pb-drawer-close" aria-label="Close workspace menu">${icon('close')}</button></div><div class="pb-workspace-grid">${spaces.map(([view,label,ico])=>`<button type="button" data-home-go="${view}">${icon(ico)}${label}</button>`).join('')}</div>`;
  document.body.appendChild(drawer);
  drawer.querySelector('.pb-drawer-close').onclick=()=>drawer.close();
  drawer.addEventListener('click',e=>{if(e.target===drawer){const r=drawer.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)drawer.close();}});
  const launcher=document.createElement('button');launcher.type='button';launcher.id='pbWorkspaceButton';launcher.className='btn';launcher.setAttribute('aria-label','Open all workspaces');launcher.setAttribute('aria-haspopup','dialog');launcher.innerHTML=icon('menu');
  launcher.onclick=()=>drawer.showModal();document.querySelector('.top')?.appendChild(launcher);
  const toast=document.createElement('div');toast.className='pb-home-toast';toast.setAttribute('role','status');toast.setAttribute('aria-live','polite');document.body.appendChild(toast);
  let toastTimer;
  function homeToast(text){toast.textContent=text;clearTimeout(toastTimer);toastTimer=setTimeout(()=>{toast.textContent='';},6500);}
  document.addEventListener('keydown',e=>{if(e.key==='Escape'){const modal=document.querySelector('.modalwrap.open');if(modal&&typeof closeModal==='function')closeModal(modal.id);}});
  document.addEventListener('click',e=>{if(e.target instanceof Element&&e.target.classList.contains('modalwrap')&&typeof closeModal==='function')closeModal(e.target.id);});
  window.addEventListener('hashchange',()=>{const view=location.hash.slice(1);if(spaces.some(([id])=>id===view)&&document.body.dataset.pbView!==view)go(view);});

  // Put the conversation and composer first; optional routing stays available.
  const chatBox=document.querySelector('#ai .chatbox'),chatOptions=chatBox?.querySelector('.grid3');
  if(chatBox&&chatOptions){
    const context=document.createElement('details');context.id='pbChatContext';context.className='pb-chat-context';
    context.innerHTML='<summary>Project, agent &amp; model <span>Optional context</span></summary>';
    context.open=window.innerWidth>=900;context.appendChild(chatOptions);chatBox.appendChild(context);
  }
  const originalSyncSelectors=typeof syncSelectors==='function'?syncSelectors:null;
  if(originalSyncSelectors)syncSelectors=function(){
    const ids=['aiProject','aiTask','aiAgent','aiModel','terminalRun','leadFilter'],kept=Object.fromEntries(ids.map(id=>[id,document.getElementById(id)?.value||'']));
    originalSyncSelectors();
    for(const id of ids){const el=document.getElementById(id);if(id==='leadFilter'&&kept[id]===unassignedLead&&el&&![...el.options].some(o=>o.value===unassignedLead))el.add(new Option('Unassigned lead',unassignedLead));if(el&&[...el.options].some(o=>o.value===kept[id]))el.value=kept[id];if(id==='aiProject'&&typeof syncAITasks==='function')syncAITasks();}
  };

  // Keep one set of real filter controls, inside an on-demand dialog.
  const contextBar=document.createElement('div');contextBar.id='pbContextBar';contextBar.className='pb-context-bar';
  contextBar.innerHTML='<div><h2 id="pbWorkspaceTitle"></h2><small id="pbWorkspaceScope"></small></div><button type="button" id="pbScopedFilters">Filters</button>';
  main?.insertBefore(contextBar,home);
  const filterDialog=document.createElement('dialog');filterDialog.id='pbFilterDialog';filterDialog.className='pb-filter-dialog';filterDialog.setAttribute('aria-labelledby','pbFilterTitle');
  filterDialog.innerHTML='<div class="pb-filter-head"><h2 id="pbFilterTitle">Filter your workspace</h2><button class="pb-filter-close" type="button" aria-label="Close filters">Done</button></div>';
  const filterControls=document.querySelector('header .filters'),advancedControls=document.getElementById('advancedFilters');
  if(filterControls)filterDialog.appendChild(filterControls);if(advancedControls)filterDialog.appendChild(advancedControls);document.body.appendChild(filterDialog);
  const scopeViews=new Set(['home','portfolio','board','intelligence','agents','activity']);let lastChromeView='';
  function openScopeFilters(){if(!filterDialog.open)filterDialog.showModal();}
  filterDialog.querySelector('.pb-filter-close').onclick=()=>filterDialog.close();
  filterDialog.addEventListener('click',e=>{if(e.target===filterDialog){const r=filterDialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)filterDialog.close();}});
  document.getElementById('pbScopedFilters').onclick=openScopeFilters;
  if(document.getElementById('openFilters'))document.getElementById('openFilters').onclick=openScopeFilters;
  document.addEventListener('click',e=>{if(e.target.closest('[data-open-scope],[data-open-filters]')){openScopeFilters();e.preventDefault();}});
  function syncWorkspaceChrome(){
    const view=document.body.dataset.pbView||'home',record=spaces.find(([id])=>id===view),count=typeof activeFilterCount==='function'?activeFilterCount():0;
    if(view!==lastChromeView){if(filterDialog.open)filterDialog.close();lastChromeView=view;if(view==='home')requestAnimationFrame(sizeLiveCrawl);}
    contextBar.hidden=view==='home';document.getElementById('pbWorkspaceTitle').textContent=view==='ai'?workspaceProfile.assistant_name+' chat':record?.[1]||'Workspace';
    const criteria=typeof filterCriteria==='function'?filterCriteria():{},leadScope=criteria.leadFilter?'Lead: '+(criteria.leadFilter===unassignedLead?'Unassigned':criteria.leadFilter):'',names=[leadScope,criteria.projectFilter,criteria.ownerFilter,criteria.priorityFilter,criteria.statusFilter].filter(Boolean);
    document.getElementById('pbWorkspaceScope').textContent=scopeViews.has(view)&&count?(names.join(' · ')||`${count} active filters`):'';
    const toggle=document.getElementById('pbScopedFilters');toggle.hidden=!scopeViews.has(view);toggle.textContent=count?`Filters · ${count}`:'Filters';toggle.setAttribute('aria-pressed',String(count>0));
    const login=document.getElementById('login');if(login&&!session?.ok)login.textContent='Sign in';
    if(syncCrawlIdentity()||(view==='home'&&Date.now()-crawlLastAttempt>15000))void pollLiveCrawl();
  }
  let crawlIdentity='',crawlSnapshot=null,crawlBusy=null,crawlRequest=0,crawlSignature='',crawlPaused=false,crawlState='connecting',crawlLastAttempt=0;
  const actorIdentity=()=>`${session?.subject||''}|${session?.name||'Public'}|${session?.level||0}`;
  function syncCrawlIdentity(){
    const next=actorIdentity();if(next===crawlIdentity)return false;
    crawlIdentity=next;++crawlRequest;if(crawlBusy)crawlBusy.abort();crawlBusy=null;crawlSnapshot=null;crawlSignature='';crawlState='connecting';return true;
  }
  const crawlText=(value,max=130)=>String(value??'').replace(/[\u0000-\u001f\u007f]/g,' ').replace(/\s+/g,' ').trim().slice(0,max);
  function collectLiveHeadlines(){
    const data=crawlSnapshot||{tasks:tasks||[],runs:runs||[],activity:activity||[],notifications:notifications||[]},items=[],seen=new Set();
    const add=(id,text,kind='info',view='activity',task='')=>{if(!text||seen.has(id))return;seen.add(id);items.push({id,text:crawlText(text,200),kind,view,task});};
    const health=window.__PROJECT_BYTE_HEALTH_LAST__,bridges=health?.components?.bridges||{};
    if(health?.unavailable)add('health','System health unavailable · inspect connection','warning','help');
    if(crawlState==='stale')add('stale','Activity connection interrupted · showing last recorded updates','warning','help');
    for(const [key,label] of [['stickdeath','StickDeath BYTE'],['vitros','VITROS builder'],['vitros_verifier','VITROS verifier']]){
      const b=bridges[key];if(!b)continue;
      if(b.owner_paused)add('bridge:'+key,`${label} · paused by owner${b.external_running?' · executor still observed':''}`,'warning','agents');
      else if(b.external_running||Number(b.active_executor_count)>0)add('bridge:'+key,`${label} · running ${crawlText(b.active_run_ref||b.external_run_ref,55)}${b.active_model?' · '+crawlText(b.active_model,55):''}`,'info','agents');
      else if(b.state==='failed'||b.execution_state==='blocked')add('bridge:'+key,`${label} · ${crawlText(b.execution_reason||'execution needs attention',110)}`,'error','agents');
      else if(b.execution_state==='unknown')add('bridge:'+key,`${label} · execution evidence not current`,'warning','agents');
    }
    if(session?.level>=1)for(const r of (data.runs||[]).slice(0,8)){
      const label={running:'running',queued:'queued, not running', 'pr-created':'PR ready for review','opencode-failed':'execution failed','bridge-error':'bridge needs attention','diff-check-failed':'verification failed'}[r.status];
      if(label)add('run:'+r.id,`${r.project} · ${r.agent_key||'agent'} · ${label} · issue #${r.issue_number}`,/failed|error/.test(r.status)?'error':'info','agents');
    }
    const open=(data.tasks||[]).filter(t=>t.status!=='Done');
    for(const t of open.filter(t=>t.status==='Blocked').slice(0,3))add('blocked:'+t.id,`${t.project} · blocked: ${t.title}`,'warning','board',t.id);
    for(const t of open.filter(t=>t.due_date&&typeof daysUntil==='function'&&daysUntil(t.due_date)<0).slice(0,2))add('due:'+t.id,`${t.project} · overdue: ${t.title}`,'warning','board',t.id);
    if(syncExternalReviewIdentity()&&homeExternalReviewState.state==='fresh')for(const r of (homeExternalReviewState.items||[]).slice(0,3))add('review:'+r.repo+':'+r.number,`${r.project} · PR #${r.number} waiting for review · ${r.title}`,'info','agents');
    for(const a of (data.activity||[]).slice(0,6)){
      const task=(data.tasks||[]).find(t=>t.id===a.task_id);
      add('activity:'+a.id,`${shortTime(a.ts)} · ${a.actor||'System'} · ${String(a.action||'update').replaceAll('_',' ')}${a.project?' · '+a.project:''}${a.detail?' · '+crawlText(a.detail,85):''}`,'info','activity',task?.id||'');
    }
    for(const t of open.filter(t=>Number(t.progress)>0).slice(0,2))add('progress:'+t.id,`${t.project} · ${t.title} · ${Math.min(100,Math.max(0,Number(t.progress)))}% recorded progress`,'info','board',t.id);
    const mh=health?.components?.models;
    if(mh)add('models',mh.state==='tested_ok'?`Model Hub · ${Number(mh.tested_ok||0)} recent connection test${mh.tested_ok===1?'':'s'} passed`:'Model Hub · '+(mh.state==='failed'?'connection test failed':'connection evidence untested or expired'),mh.state==='tested_ok'?'success':'warning','models');
    if(!(session?.level>=1))add('sign-in','Sign in to see your private agent runs and review updates','info','ai');
    if(!items.length)add('empty',crawlState==='connecting'?'Connecting to recorded activity…':'No new recorded activity · updates refresh automatically');
    return items.slice(0,22);
  }
  function sizeLiveCrawl(){
    const root=document.getElementById('pbLiveCrawl'),track=root?.querySelector('.pb-crawl-track'),group=track?.firstElementChild,viewport=root?.querySelector('.pb-crawl-viewport');if(!group||!viewport)return;
    const animation=track.getAnimations?.()[0],offset=animation?Number(animation.currentTime||0)/1000*34:0;
    track.style.setProperty('--crawl-min-width',viewport.clientWidth+'px');
    const distance=group.getBoundingClientRect().width;if(distance<=0)return;
    track.style.setProperty('--crawl-distance',distance+'px');track.style.setProperty('--crawl-duration',Math.max(1,distance/34)+'s');
    const next=track.getAnimations?.()[0];if(next)next.currentTime=(offset%distance)/34*1000;
  }
  function renderLiveCrawl(){
    if(syncCrawlIdentity())void pollLiveCrawl();const root=document.getElementById('pbLiveCrawl');if(!root)return;
    const items=collectLiveHeadlines(),signature=JSON.stringify(items),track=root.querySelector('.pb-crawl-track');
    root.dataset.state=crawlState;root.dataset.paused=String(crawlPaused);root.dataset.hidden=String(document.hidden);
    root.querySelector('.pb-crawl-label').textContent=crawlState==='fresh'?'LIVE':crawlState==='stale'?'STALE':'UPDATES';
    if(signature===crawlSignature)return;crawlSignature=signature;
    const content=(clone)=>items.map(item=>`<button type="button" class="pb-crawl-item" data-kind="${item.kind}" ${item.task?`data-home-task="${escH(item.task)}"`:`data-home-go="${item.view}"`}${clone?' tabindex="-1"':''}>${escH(item.text)}</button>`).join('');
    track.innerHTML=`<div class="pb-crawl-group">${content(false)}</div><div class="pb-crawl-group" aria-hidden="true">${content(true)}</div>`;
    requestAnimationFrame(sizeLiveCrawl);
  }
  async function pollLiveCrawl(){
    if(document.hidden||(document.body.dataset.pbView||'home')!=='home')return;
    syncCrawlIdentity();if(crawlBusy)return;
    const identity=crawlIdentity,request=++crawlRequest,controller=new AbortController();crawlBusy=controller;crawlLastAttempt=Date.now();
    const timeout=setTimeout(()=>controller.abort(),10000),routes=[['activity','/api/activity'],['tasks','/api/tasks']];
    if(session?.level>=1)routes.push(['runs','/api/runs']);
    try{
      const results=await Promise.allSettled(routes.map(async([key,url])=>{const x=await api(url,{signal:controller.signal});if(!Array.isArray(x[key]))throw new Error('Invalid activity evidence');return [key,x[key]];}));
      if(request!==crawlRequest||identity!==actorIdentity())return;
      const next={activity:[],tasks:[],runs:[],...(crawlSnapshot||{})};let failures=0;
      for(const r of results){if(r.status==='fulfilled')next[r.value[0]]=r.value[1];else failures++;}
      crawlSnapshot=next;crawlState=failures?'stale':'fresh';renderLiveCrawl();
    }catch{if(request===crawlRequest){crawlState='stale';renderLiveCrawl();}}
    finally{clearTimeout(timeout);if(request===crawlRequest)crawlBusy=null;}
  }
  const crawlToggle=document.getElementById('pbCrawlToggle');
  crawlToggle.onclick=()=>{crawlPaused=!crawlPaused;crawlToggle.setAttribute('aria-pressed',String(crawlPaused));crawlToggle.setAttribute('aria-label',crawlPaused?'Resume activity crawl':'Pause activity crawl');crawlToggle.innerHTML=icon(crawlPaused?'active':'blocked');renderLiveCrawl();};
  document.addEventListener('visibilitychange',()=>{renderLiveCrawl();if(!document.hidden)void pollLiveCrawl();});
  window.addEventListener('resize',sizeLiveCrawl);window.setInterval(pollLiveCrawl,15000);

  document.body.dataset.pbView = location.hash.replace('#','') || 'home';
  window.addEventListener('project-byte-health',e=>{renderExecutionFabric(e.detail||null);renderLiveCrawl();});
  setTimeout(()=>{renderHome();void pollLiveCrawl();},0);
  function ownerShortcutKey(name,index){return name==='Joe'?'joe':name==='Mike'?'mike':'person-'+index;}
  async function loadWorkspaceProfile(){
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const response=await fetch('/api/workspace-profile',{cache:'no-store',signal:controller.signal});
      const payload=await response.json();
      if(!response.ok)throw new Error('unavailable');
      const p=payload.profile;
      if(p?.schema_version!==1||typeof p.display_name!=='string'||typeof p.assistant_name!=='string'||!Array.isArray(p.owner_shortcuts)||p.owner_shortcuts.some(n=>typeof n!=='string'))throw new Error('invalid');
      workspaceProfile=p;window.PROJECT_BYTE_WORKSPACE=p;
      document.title=p.display_name;
      const title=document.querySelector('.brand h1');
      if(title){if(p.display_name==='PRFKT_PROJECT')title.innerHTML='PRFKT<span class="pb-wordmark">_PROJECT</span>';else title.textContent=p.display_name;}
      document.querySelector('.home-hero-top h2').textContent=p.assistant_name;
      if(p.assistant_name!=='AI_BYTE'){const heading=document.querySelector('#ai .chatbox h2');if(heading)heading.textContent=p.assistant_name;}
      document.querySelector('.home-asset-credit span').textContent=p.display_name+' · Your executive workspace';
      const chat=document.querySelector('#pbWorkspaces [data-home-go="ai"]');
      if(chat)for(const node of chat.childNodes)if(node.nodeType===Node.TEXT_NODE)node.textContent=p.assistant_name+' chat';
      document.body.classList.toggle('custom-workspace-profile',p.display_name!=='PRFKT_PROJECT'||p.assistant_name!=='AI_BYTE'||JSON.stringify(p.owner_shortcuts)!==JSON.stringify(['Joe','Mike']));
      renderHome();
    }catch{
      const note=document.createElement('p');note.className='small';note.id='workspaceProfileWarning';note.setAttribute('role','status');note.textContent='Workspace branding is unavailable. Default appearance is shown.';
      document.querySelector('#settings .settingsgrid')?.prepend(note);
    }finally{clearTimeout(timeout);window.PROJECT_BYTE_WORKSPACE_READY=true;if(typeof deliverBrowserNotifications==='function')deliverBrowserNotifications();}
  }
  const profileStyle=document.createElement('style');
  profileStyle.textContent='body.custom-workspace-profile .home-shell{grid-template-columns:minmax(0,1fr)}body.custom-workspace-profile .home-asset-credit{overflow-wrap:anywhere}body.custom-workspace-profile #ai .chatbox .between>div{min-width:0}body.custom-workspace-profile .brand>div:first-child{min-width:0}body.custom-workspace-profile .brand .top{flex-shrink:0}body.custom-workspace-profile .brand h1{max-width:45vw;overflow:hidden;text-overflow:ellipsis}body.custom-workspace-profile .home-hero-top>div:first-child,body.custom-workspace-profile .pb-context-bar>div{min-width:0}body.custom-workspace-profile #homeWorkTitle,body.custom-workspace-profile #homeWork .small,body.custom-workspace-profile #pbWorkspaceScope,body.custom-workspace-profile .pb-home-toast,body.custom-workspace-profile .home-hero-top h2,body.custom-workspace-profile #pbWorkspaceTitle,body.custom-workspace-profile #ai .chatbox h2{overflow-wrap:anywhere}body.custom-workspace-profile .agent-node.center b{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}body.custom-workspace-profile .pb-workspaces button{overflow-wrap:anywhere;min-width:0}body.custom-workspace-profile .home-chip{flex-shrink:0;max-width:100%;overflow:hidden;text-overflow:ellipsis}';
  document.head.appendChild(profileStyle);
  void loadWorkspaceProfile();
})();

(() => {
  if(window.__PB_OBSERVATORY__)return;window.__PB_OBSERVATORY__=true;
  const host=document.getElementById('agents');if(!host)return;
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const ui={data:null,trace:'',project:'',mode:'tree',tools:'pills',metric:'count',selected:'',search:'',zoom:1,paused:false,error:'',busy:false,identity:'',last:0};
  const style=document.createElement('style');style.textContent=`
  .obs-studio{border:1px solid #2c4864;border-radius:17px;background:#07101b;overflow:hidden;margin-bottom:16px;color:#dceaf9}.obs-studio *{box-sizing:border-box}
  .obs-header{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:15px 16px;border-bottom:1px solid #20354b}.obs-header h2{margin:0;font-size:18px}.obs-sub{font-size:11px;color:#97aec7;margin:5px 0 0}.obs-evidence{font-size:11px;padding:10px 15px;color:#c2a778;background:#101a25;border-bottom:1px solid #22384c;line-height:1.5}
  .obs-toolbar,.obs-lenses,.obs-tools-switch{display:flex;gap:7px;flex-wrap:wrap;align-items:center;padding:10px 14px;border-bottom:1px solid #20354b}.obs-toolbar select,.obs-toolbar input{min-width:0;max-width:100%;padding:9px 10px;background:#101f31;color:#dbeaf8;border:1px solid #2c4864;border-radius:9px;min-height:42px;font-size:12px}.obs-toolbar input{flex:1;min-width:150px}
  .obs-studio button{min-height:42px;padding:8px 11px;border:1px solid #2e4c6a;border-radius:9px;background:#12263c;color:#c8dff3;font-size:12px;cursor:pointer}.obs-studio button:disabled{opacity:.45;cursor:not-allowed}.obs-studio button[aria-pressed="true"]{color:#89d1ff;background:#163d61;border-color:#5994c3}.obs-studio button:focus-visible{outline:2px solid #a0d7ff;outline-offset:2px}
  .obs-layout{display:grid;grid-template-columns:minmax(0,1fr) 290px;min-height:390px}.obs-stage{min-width:0;position:relative}.obs-canvas{height:430px;overflow:auto;overscroll-behavior:contain;background:radial-gradient(circle at 50% 30%,#12263c66,transparent 60%),radial-gradient(circle,#44607a55 1px,transparent 1px);background-size:auto,22px 22px;position:relative}.obs-canvas:focus{outline:2px solid #77bafa;outline-offset:-2px}.obs-graph{position:relative;min-width:100%;min-height:100%;transform-origin:0 0}.obs-graph>svg{position:absolute;inset:0;pointer-events:none}.obs-edge{fill:none;stroke:#416889;stroke-width:1.4}.obs-edge.selected{stroke:#81ceff;stroke-width:2.3}
  .obs-node{position:absolute;width:165px;min-height:69px;text-align:left;display:flex;flex-direction:column;justify-content:center;gap:5px;box-shadow:inset 0 1px #ffffff0d,0 5px 14px #0005}.obs-node{height:73px;overflow:hidden}.obs-node strong{font-size:12px;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}.obs-node span{font-size:10px;color:#9eb5cc}.obs-node[data-kind="agent"]{border-color:#6274bb;background:#15243d}.obs-node[data-kind="tool"]{border-color:#366b66;background:#10292d}.obs-node.selected{outline:2px solid #80cbff;box-shadow:0 0 18px #3f98ee22}.obs-node[data-status="error"]{border-color:#ce737b}.obs-node[data-kind="trace"]{background:#29231b;border-color:#876832}
  .obs-inspector{border-left:1px solid #263d55;background:#0c1827;padding:15px;max-height:580px;overflow:auto;min-width:0}.obs-inspector h3{font-size:15px;line-height:1.4;margin:0 0 10px;overflow-wrap:anywhere}.obs-inspector dl{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 0;font-size:11px}.obs-inspector dt{color:#8da6bf}.obs-inspector dd{margin:0;color:#d9e9f8;overflow-wrap:anywhere}.obs-inspector pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#050d17;border:1px solid #233951;padding:10px;font-size:10px;line-height:1.5;border-radius:9px}.obs-inspector .obs-note{font-size:11px;color:#a5b5c8;line-height:1.5}.obs-inspector button{margin-top:10px}.obs-inspector-close{float:right}
  .obs-summary{display:flex;gap:16px;flex-wrap:wrap;font-size:11px;padding:10px 15px;color:#94b1ce;border-bottom:1px solid #20364b}.obs-summary b{color:#deedfc}.obs-legend{font-size:10px;padding:8px 14px;color:#90a6be;line-height:1.6}.obs-empty{padding:38px 18px;text-align:center;color:#9db3cc;font-size:13px;line-height:1.7}.obs-canvas .obs-map{width:100%;height:100%;min-height:350px;display:flex;flex-wrap:wrap;align-content:flex-start;gap:5px;padding:12px}.obs-map .obs-block{min-width:80px;min-height:75px;flex-grow:1;text-align:left;overflow:hidden;border-color:#6b61b3;background:#302856}.obs-block.selected{outline:2px solid #9cd8ff}.obs-block b{display:block;font-size:17px}.obs-block small{font-size:10px;display:block;margin-top:4px}
  .obs-flow{position:relative;min-width:690px;height:380px;padding:12px}.obs-flow svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.obs-flow path{fill:none;stroke:#528ccc88}.obs-flow button{position:absolute;width:142px;min-height:44px;text-align:left;font-size:11px;background:#152b40}.obs-flow button.selected{outline:2px solid #83ceff}
  .obs-recorded-activity{border-top:1px solid #294359;padding:12px 15px}.obs-recorded-activity h3{font-size:13px;margin:0 0 9px}.obs-event-feed{display:grid;gap:7px;max-height:260px;overflow:auto}.obs-event-feed button{display:block;width:100%;text-align:left;padding:10px;min-height:44px}.obs-event-feed span{display:block;white-space:normal;overflow-wrap:anywhere;line-height:1.5;font-size:12px}.obs-event-feed small{display:block;color:#93adc5;font-size:10px;margin-bottom:4px}.obs-event-feed button.selected{outline:1px solid #85caff}.obs-readable-section{margin:15px 0}.obs-readable-section header{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:7px}.obs-readable-section h4{margin:0;font-size:12px;color:#cde7fd}.obs-readable-section button{font-size:10px;min-height:32px;padding:4px 9px}.obs-event-text{white-space:pre-wrap;overflow-wrap:anywhere;max-height:330px;overflow:auto;padding:10px;border:1px solid #2a4259;border-radius:8px;background:#091623;line-height:1.65;font-size:12px}.obs-event-text[data-format="json"]{font-family:ui-monospace,monospace;font-size:11px}.obs-event-metadata{margin-top:16px}.obs-event-metadata summary{cursor:pointer;min-height:36px;font-size:12px}.obs-recorded-event{padding-top:10px;border-top:1px solid #294359}.obs-recorded-event>h4{margin:0 0 7px;font-size:13px}.obs-focused>.obs-recorded-activity{display:none}
  .obs-tool-body{padding:12px 15px;max-height:300px;overflow:auto}.obs-pills{display:flex;flex-wrap:wrap;gap:7px}.obs-pills button{font-size:11px;min-height:38px}.obs-pills button.selected{outline:2px solid #83ceff}.obs-table{border-collapse:collapse;width:100%;font-size:11px}.obs-table td,.obs-table th{padding:7px 10px;text-align:left;border-bottom:1px solid #21374e;white-space:nowrap}.obs-table th{color:#9bb6d1}.obs-table button{font-size:11px;min-height:32px;padding:4px 8px}.obs-table tr.selected{background:#19436555}.obs-bar-track{height:12px;background:#142b40;border-radius:4px;width:200px;position:relative}.obs-bar{position:absolute;height:12px;min-width:3px;background:#5cb4cf;border-radius:3px}.obs-bar.error{background:#ce7387}.obs-matrix td button{background:rgba(68,131,178,var(--intensity,.1));min-width:42px}
  .obs-roles{padding:12px 0}.obs-roles>summary{cursor:pointer;min-height:44px;padding:12px;background:#0f2134;border:1px solid #2b435c;border-radius:12px;margin-bottom:12px;font-size:13px}.obs-approval-note{border:1px solid #4d4633;border-radius:11px;padding:11px;margin:12px 15px;font-size:11px;color:#d0ba95;line-height:1.6}.obs-zoom{margin-left:auto;display:flex;gap:5px}.obs-zoom button{min-width:38px}
  @media(max-width:800px){.obs-layout{grid-template-columns:1fr}.obs-inspector{border-left:0;border-top:1px solid #294762;max-height:350px}.obs-inspector[hidden]{display:none}.obs-canvas{height:400px}.obs-toolbar{display:grid;grid-template-columns:1fr 1fr}.obs-toolbar input{grid-column:1/-1;width:100%;font-size:16px}.obs-toolbar select{font-size:14px;width:100%}.obs-lenses{gap:6px;padding:9px}.obs-lenses button{font-size:11px;padding:7px 10px}.obs-header{padding:12px}.obs-header h2{font-size:16px}.obs-summary{gap:9px}.obs-zoom{margin-left:0}.obs-tool-body{max-height:250px}.obs-flow{min-width:650px}}
.obs-toolbar[hidden]{display:none!important}.obs-stage>.obs-zoom{position:absolute;bottom:52px;left:10px;z-index:2;margin:0;padding:5px;border-radius:11px;background:#071321e8;box-shadow:0 4px 12px #0005}.obs-zoom button{min-width:32px;min-height:35px;font-size:10px;padding:5px 8px}.obs-canvas{scrollbar-width:thin;scrollbar-color:#315471 #0a1624}.obs-focused{position:fixed!important;inset:8px;z-index:1500;margin:0;display:flex;flex-direction:column;overflow:hidden;background:#07101b}.obs-focused>.obs-layout{flex:1;min-height:0}.obs-focused .obs-stage{min-height:0;display:flex;flex-direction:column}.obs-focused .obs-canvas{height:auto;flex:1;min-height:150px}.obs-focused>.obs-tools-switch,.obs-focused>.obs-tool-body,.obs-focused>.obs-approval-note{display:none}.obs-focused .obs-inspector{max-height:100%}.obs-focused>.obs-evidence{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:7px 12px}.obs-focused .obs-legend{font-size:9px;min-height:34px}.obs-focused .obs-stage>.obs-zoom{bottom:44px}@media(max-width:800px){.obs-focused .obs-layout{grid-template-rows:minmax(150px,1fr) auto}.obs-focused .obs-inspector{max-height:200px}.obs-focused .obs-summary{display:none}.obs-header button{min-height:37px;font-size:11px}.obs-evidence{font-size:10px}.obs-toolbar select{min-height:40px;font-size:12px}.obs-toolbar{padding:8px 10px}.obs-lenses{padding:7px 9px}}
.obs-linked-lens{padding:10px;min-width:0;border-right:1px solid #294359;background:#0b1624;display:none}.obs-linked-lens p{font-size:10px;color:#9eb8d2;margin:0 0 10px}.obs-linked-lens button{width:100%;font-size:10px;text-align:left;margin-bottom:5px;padding:6px;min-height:34px;border-color:#514984;background:#282449}.obs-linked-lens button.selected{outline:2px solid #7bcbff}.obs-linked-lens small{display:block;margin-top:3px;color:#a6bed5}@media(min-width:1100px){.obs-layout{grid-template-columns:150px minmax(0,1fr) 280px}.obs-linked-lens{display:block;max-height:530px;overflow:auto}.obs-focused .obs-linked-lens{max-height:100%}}
  `;document.head.appendChild(style);
  const legacy=document.createElement('details');legacy.className='obs-roles';legacy.innerHTML='<summary>Agent roles, existing runs &amp; memory</summary>';
  while(host.firstChild)legacy.appendChild(host.firstChild);
  const studio=document.createElement('section');studio.id='pbObservatory';studio.className='obs-studio';
  studio.innerHTML=`<div class="obs-header"><div><h2>Agent Observatory</h2><p class="obs-sub">Runs, agents and tools · one linked view</p></div><div><button type="button" id="obsFocus">Focus</button> <button type="button" id="obsRefresh">Refresh</button></div></div>
  <div id="obsEvidence" class="obs-evidence">Connecting to recorded execution evidence…</div>
  <div class="obs-toolbar"><select id="obsProject" aria-label="Observation project"><option value="">All observed projects</option></select><select id="obsTrace" aria-label="Observed run"><option value="">All recorded runs</option></select><input id="obsSearch" type="search" placeholder="Find agent, tool or run…" aria-label="Search graph"></div>
  <div class="obs-lenses" role="group" aria-label="Analysis lens"><button type="button" data-obs-mode="tree">Tree</button><button type="button" data-obs-mode="treemap">Treemap</button><button type="button" data-obs-mode="flow">Sankey</button><button type="button" data-obs-mode="timeline">Timeline</button><div class="obs-zoom"><button type="button" id="obsZoomOut" aria-label="Zoom out">−</button><button type="button" id="obsFit">Fit</button><button type="button" id="obsZoomIn" aria-label="Zoom in">+</button><button type="button" id="obsPause">Pause live</button></div></div>
  <div class="obs-toolbar"><select id="obsMetric" aria-label="Analysis size metric"><option value="count">Event count</option><option value="duration">Recorded tool duration</option><option value="tokens">Recorded tokens</option></select><span class="obs-sub">Select a node to link graph, lens and tools.</span></div>
  <div id="obsSummary" class="obs-summary"></div><div class="obs-layout"><aside id="obsLinkedLens" class="obs-linked-lens" aria-label="Cross-linked count lens"></aside><div class="obs-stage"><div id="obsCanvas" class="obs-canvas" tabindex="0" aria-label="Interactive execution graph"></div><div id="obsLegend" class="obs-legend"></div></div><aside id="obsInspector" class="obs-inspector" aria-label="Selected node details" hidden></aside></div>
  <section class="obs-recorded-activity" aria-label="Recorded activity"><h3>Recorded activity</h3><div id="obsEventFeed" class="obs-event-feed"></div></section>
  <div class="obs-tools-switch" role="group" aria-label="Tool grouping"><b class="obs-sub">Tool calls</b><button type="button" data-obs-tools="pills">Pill grid</button><button type="button" data-obs-tools="timeline">Swimlanes</button><button type="button" data-obs-tools="matrix">Frequency matrix</button></div><div id="obsTools" class="obs-tool-body"></div>
  <div class="obs-approval-note"><b>Execution permissions: not connected.</b> These traces are read-only. A code-review decision does not approve a tool, resume an agent or merge code. Live Approve / Deny requires a connected permission-capable runner; no such request is being fabricated here.</div>`;
  host.append(studio,legacy);
  const zoom=studio.querySelector('.obs-zoom');studio.querySelector('.obs-stage').appendChild(zoom);
  const $=id=>document.getElementById(id),number=value=>value===null||value===undefined?'Not recorded':Number(value).toLocaleString([], {maximumFractionDigits:2});
  const stamp=value=>value?new Date(value*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}):'Not recorded';
  const traces=()=>((ui.data?.traces)||[]).filter(t=>!ui.project||t.project===ui.project).filter(t=>!ui.trace||t.id===ui.trace);
  const observedEvents=()=>traces().flatMap(t=>(t.events||[]).map(e=>({...e,trace:t.id,project:t.project,role:t.role})));
  const sum=(items,fn)=>{const values=items.map(fn).filter(v=>v!==null&&v!==undefined);return values.length?values.reduce((a,b)=>a+Number(b),0):null;};
  let nodes=[],nodeMap=new Map();
  function makeNode(id,parent,kind,label,items,metadata={}){return {id,parent,kind,label,items,count:items.length,duration:sum(items,e=>e.duration_ms),tokens:sum(items,e=>e.tokens?.total),...metadata};}
  function buildNodes(){
    const result=[],list=traces();
    result.push(makeNode('workspace','', 'root','Observed execution',observedEvents()));
    for(const t of list){
      const events=(t.events||[]).map(e=>({...e,trace:t.id,project:t.project,role:t.role}));
      result.push(makeNode(t.id,'workspace','trace',t.project+' · '+t.label,events,{trace:t.id,status:t.status,metadata:t}));
      const sessions=new Map();for(const e of events){if(!sessions.has(e.session))sessions.set(e.session,{label:t.role,items:[],parent:t.id});sessions.get(e.session).items.push(e);}
      for(const e of events)if(e.child_session&&e.child_session!==e.session){if(!sessions.has(e.child_session))sessions.set(e.child_session,{label:e.delegate||'Delegated agent',items:[],parent:e.session});else {sessions.get(e.child_session).label=e.delegate||'Delegated agent';sessions.get(e.child_session).parent=e.session;}}
      for(const [sid,group] of sessions){
        let parent=group.parent,cursor=parent,seen=new Set([sid]);
        while(sessions.has(cursor)){if(seen.has(cursor)){parent=t.id;break;}seen.add(cursor);cursor=sessions.get(cursor).parent;}
        result.push(makeNode(sid,parent,'agent',group.label,group.items,{trace:t.id,referencedOnly:!group.items.length}));
        const tools=new Map();for(const e of group.items){const name=e.kind==='tool_use'?e.tool:e.kind==='step_finish'?'Model steps':e.kind==='compaction'?'Compaction':'Session events';if(!tools.has(name))tools.set(name,[]);tools.get(name).push(e);}
        for(const [name,items] of tools)result.push(makeNode(sid+':'+name,sid,'tool',name,items,{trace:t.id,status:items.some(e=>e.error_recorded)?'error':'recorded'}));
      }
    }
    nodeMap=new Map(result.map(n=>[n.id,n]));nodes=result;
    if(ui.selected&&!nodeMap.has(ui.selected))ui.selected='';
  }
  const selectedEvents=()=>ui.selected&&nodeMap.has(ui.selected)?nodeMap.get(ui.selected).items:observedEvents();
  const nodeMatches=n=>!ui.search||`${n.label} ${n.kind} ${n.items.map(e=>e.tool).join(' ')}`.toLowerCase().includes(ui.search.toLowerCase());
  function graphNodes(){
    let list=nodes;if(!ui.trace&&traces().length>1)list=list.filter(n=>n.kind==='root'||n.kind==='trace');
    if(ui.search){const ids=new Set();for(const n of list.filter(nodeMatches)){let x=n;for(let depth=0;x&&depth<20;depth++){ids.add(x.id);x=nodeMap.get(x.parent);}}list=list.filter(n=>ids.has(n.id));}
    return list.slice(0,180);
  }
  function selectNode(id){ui.selected=id;renderVisuals();const el=$('obsCanvas').querySelector(`[data-obs-node="${CSS.escape(id)}"]`);if(el)el.setAttribute('aria-selected','true');}
  function drawTree(){
    const list=graphNodes(),position=new Map(),levels=new Map();
    const depth=n=>{let value=0,seen=new Set([n.id]),parent=n.parent;while(nodeMap.has(parent)&&value<12&&!seen.has(parent)){seen.add(parent);value++;parent=nodeMap.get(parent).parent;}return value;};
    for(const n of list){const d=depth(n);if(!levels.has(d))levels.set(d,[]);levels.get(d).push(n);}
    let maximumRows=1;for(const [column,items] of levels){maximumRows=Math.max(maximumRows,items.length);items.forEach((n,index)=>position.set(n.id,{x:22+column*216,y:20+index*91}));}
    const width=Math.max(500,(Math.max(0,...levels.keys())+1)*216),height=Math.max(400,maximumRows*91+45);
    let edges='';for(const n of list){const a=position.get(n.parent),b=position.get(n.id);if(a&&b)edges+=`<path class="obs-edge ${ui.selected===n.id?'selected':''}" d="M${a.x+165} ${a.y+34} C${a.x+192} ${a.y+34},${b.x-30} ${b.y+34},${b.x} ${b.y+34}"/>`;}
    $('obsCanvas').innerHTML=`<div style="width:${width*ui.zoom}px;height:${height*ui.zoom}px"><div class="obs-graph" style="width:${width}px;height:${height}px;transform:scale(${ui.zoom})"><svg width="${width}" height="${height}" aria-hidden="true">${edges}</svg>${list.map(n=>{const p=position.get(n.id);return `<button type="button" class="obs-node ${n.id===ui.selected?'selected':''}" style="left:${p.x}px;top:${p.y}px" data-obs-node="${escape(n.id)}" data-kind="${n.kind}" data-status="${escape(n.status||'')}" aria-label="Inspect ${escape(n.label)}"><strong>${escape(n.label)}</strong><span>${n.referencedOnly?'Referenced agent · no child trace':n.count+' recorded events'}</span></button>`;}).join('')}</div></div>`;
    $('obsLegend').textContent='Tree: run → observed agent/session → grouped tools. Select a run and Open run to expand. Arrow keys navigate; drag empty canvas to pan, or swipe on your phone.';
  }
  function drawTreemap(){
    const leaves=nodes.filter(n=>n.kind==='tool'&&nodeMatches(n)&&Number(n[ui.metric])>0).sort((a,b)=>b[ui.metric]-a[ui.metric]),rects=[];
    function split(items,x,y,w,h){if(!items.length)return;if(items.length===1){rects.push({node:items[0],x,y,w,h});return;}const total=items.reduce((s,n)=>s+n[ui.metric],0);let acc=0,k=0;while(k<items.length-1&&acc<total/2){acc+=items[k][ui.metric];k++;}const ratio=acc/total;if(w>=h){split(items.slice(0,k),x,y,w*ratio,h);split(items.slice(k),x+w*ratio,y,w*(1-ratio),h);}else{split(items.slice(0,k),x,y,w,h*ratio);split(items.slice(k),x,y+h*ratio,w,h*(1-ratio));}}
    split(leaves,0,0,100,100);
    $('obsCanvas').innerHTML=rects.length?`<div class="obs-map" style="position:relative;display:block;height:400px;margin:8px;width:calc(100% - 16px)">${rects.map(({node:n,x,y,w,h})=>`<button type="button" class="obs-block ${ui.selected===n.id?'selected':''}" data-obs-node="${escape(n.id)}" style="position:absolute;left:${x}%;top:${y}%;width:${w}%;height:${h}%;min-width:0;min-height:0;padding:6px;border-radius:5px" aria-label="${escape(n.label)}: ${n[ui.metric]}"><b>${escape(n.label)}</b><small>${number(n[ui.metric])}${ui.metric==='duration'?' ms':''}</small></button>`).join('')}</div>`:'<div class="obs-empty">No measured values for this lens. Unknown token or duration values are not converted into invented estimates.</div>';
    $('obsLegend').textContent=`Rectangle area represents ${ui.metric==='count'?'recorded event count':ui.metric==='duration'?'recorded tool duration in milliseconds':'recorded model-step tokens'}. Select a rectangle to cross-highlight the tree, tool list and inspector. Unknown values are excluded.`;
  }
  function drawFlow(){
    const tools=nodes.filter(n=>n.kind==='tool'&&n.items.some(e=>e.kind==='tool_use')&&nodeMatches(n)).slice(0,24),agents=nodes.filter(n=>tools.some(t=>t.parent===n.id)),traceNodes=nodes.filter(n=>n.kind==='trace'&&agents.some(a=>a.trace===n.id)),position=new Map();
    const height=Math.max(380,Math.max(tools.length,agents.length)*53+40),groups=[traceNodes,agents,tools];
    groups.forEach((list,col)=>list.forEach((n,index)=>position.set(n.id,{x:15+col*230,y:18+index*(height-60)/Math.max(1,list.length)})));
    const max=Math.max(1,...tools.map(n=>n.items.filter(e=>e.kind==='tool_use').length));let links='';
    for(const n of [...agents,...tools]){const parent=n.kind==='agent'?n.trace:n.parent,a=position.get(parent),b=position.get(n.id);if(a&&b){const count=n.items.filter(e=>e.kind==='tool_use').length;links+=`<path d="M${a.x+142} ${a.y+22} C${a.x+194} ${a.y+22},${b.x-50} ${b.y+22},${b.x} ${b.y+22}" style="stroke-width:${Math.max(1,20*count/max)};${ui.selected===n.id?'stroke:#8ed3ff;':''}"/>`;}}
    $('obsCanvas').innerHTML=tools.length?`<div class="obs-flow" style="height:${height}px"><svg viewBox="0 0 690 ${height}" preserveAspectRatio="none" aria-hidden="true">${links}</svg>${groups.flat().map(n=>{const p=position.get(n.id);return `<button type="button" class="${ui.selected===n.id?'selected':''}" data-obs-node="${escape(n.id)}" style="left:${p.x}px;top:${p.y}px">${escape(n.label)}<br><small>${n.items.filter(e=>e.kind==='tool_use').length} tools</small></button>`;}).join('')}</div>`:'<div class="obs-empty">No observed tool calls to draw a flow.</div>';
    $('obsLegend').textContent='Sankey: observed runs → agents → tool groups. Flow width uses recorded tool-call counts, not estimated token usage. Up to 24 tool groups are shown.';
  }
  function toolNodeFor(event){return nodes.find(n=>n.kind==='tool'&&n.items.some(e=>e.id===event.id));}
  function timelineHTML(events){
    const list=events.filter(e=>e.time!==null).sort((a,b)=>a.time-b.time).slice(-100);if(!list.length)return '<div class="obs-empty">No timestamped events recorded.</div>';
    const start=Math.min(...list.map(e=>e.time)),end=Math.max(...list.map(e=>e.end||e.time)),span=Math.max(.001,end-start);
    return `<table class="obs-table"><thead><tr><th>Recorded event</th><th>Start</th><th>Duration</th><th>Relative timeline</th></tr></thead><tbody>${list.map(e=>{const n=toolNodeFor(e),left=(e.time-start)/span*96,width=e.duration_ms===null?1:Math.max(1,e.duration_ms/1000/span*96);return `<tr class="${n?.id===ui.selected?'selected':''}"><td><button type="button" data-obs-node="${escape(n?.id||e.trace)}">${escape(e.tool)}</button></td><td>${stamp(e.time)}</td><td>${e.duration_ms===null?'Not recorded':number(e.duration_ms)+' ms'}</td><td><div class="obs-bar-track"><i class="obs-bar ${e.error_recorded?'error':''}" style="left:${left}%;width:${Math.min(width,100-left)}%"></i></div></td></tr>`;}).join('')}</tbody></table>`;
  }
  function drawTimeline(){const events=observedEvents();$('obsCanvas').innerHTML=timelineHTML(events);$('obsLegend').textContent=`Timeline uses actual event timestamps. ${events.filter(e=>e.kind==='compaction').length} explicit compaction events observed; absent compaction markers are not inferred. Showing up to 100 events.`;}
  function renderTools(){
    const events=selectedEvents().filter(e=>e.kind==='tool_use');
    studio.querySelectorAll('[data-obs-tools]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.obsTools===ui.tools)));
    if(!events.length){$('obsTools').innerHTML='<div class="obs-empty">No tool events in this selection. Choose an observed run or agent.</div>';return;}
    if(ui.tools==='timeline'){$('obsTools').innerHTML=timelineHTML(events);return;}
    if(ui.tools==='pills'){$('obsTools').innerHTML=`<div class="obs-pills">${events.slice(-120).map(e=>{const n=toolNodeFor(e);return `<button type="button" data-obs-event="${escape(e.id)}" data-obs-node="${escape(n?.id||e.trace)}" class="${n?.id===ui.selected?'selected':''}">${escape(e.tool)} · ${escape(e.status)}${e.duration_ms===null?'':' · '+number(e.duration_ms)+'ms'}</button>`;}).join('')}</div>`;return;}
    const names=[...new Set(events.map(e=>e.tool))],states=['completed','error','running','pending','recorded'];
    const maximum=Math.max(1,...names.flatMap(name=>states.map(state=>events.filter(e=>e.tool===name&&e.status===state).length)));
    $('obsTools').innerHTML=`<table class="obs-table obs-matrix"><thead><tr><th>Tool / event state</th>${states.map(s=>`<th>${s}</th>`).join('')}</tr></thead><tbody>${names.map(name=>`<tr><th>${escape(name)}</th>${states.map(status=>{const matches=events.filter(e=>e.tool===name&&e.status===status),n=matches.length?toolNodeFor(matches[0]):null;return `<td><button type="button" ${n?`data-obs-node="${escape(n.id)}"`:'disabled'} style="--intensity:${.12+.8*matches.length/maximum}">${matches.length}</button></td>`;}).join('')}</tr>`).join('')}</tbody></table>`;
  }
  const evidenceLabels={prompt:'Prompt',message:'Agent update',arguments:'Arguments',response:'Result',error:'Error'};
  function renderRecordedActivity(){
    const events=selectedEvents().filter(e=>!ui.search||`${e.summary||''} ${e.tool||''}`.toLowerCase().includes(ui.search.toLowerCase())).slice(-120).reverse();
    $('obsEventFeed').innerHTML=events.length?events.map(event=>{const node=toolNodeFor(event);return `<button type="button" class="${ui.event===event.id?'selected':''}" data-obs-event="${escape(event.id)}" data-obs-node="${escape(node?.id||event.trace)}"><small>${stamp(event.time)} · ${escape(event.project||'')} · ${escape(event.status||'recorded')}</small><span>${escape(event.summary||event.tool||event.kind||'Recorded event')}</span></button>`;}).join(''):'<div class="obs-empty">No recorded activity in this selection.</div>';
  }
  function readableEvidence(event){
    const fields=Object.entries(evidenceLabels).filter(([key])=>event.evidence?.[key]&&typeof event.evidence[key].text==='string');
    if(!fields.length)return '';
    return `<article class="obs-recorded-event" data-inspected-event="${escape(event.id)}"><h4>${escape(event.tool||event.kind||'Recorded event')}</h4><p class="obs-note">${stamp(event.time)} · ${escape(event.status||'recorded')}</p>${fields.map(([key,label])=>{
      const field=event.evidence[key],notes=[field.truncated?'Truncated excerpt':null,field.redacted?'Credentials redacted':null].filter(Boolean);
      return `<section class="obs-readable-section" data-obs-evidence="${key}"><header><h4>${label}</h4><button type="button" data-obs-copy="${escape(event.id)}" data-obs-copy-field="${key}" aria-label="Copy ${label.toLowerCase()}">Copy</button></header><div class="obs-event-text" data-format="${field.format==='json'?'json':'text'}">${escape(field.text)||'No content recorded.'}</div>${notes.length?`<p class="obs-note">${notes.join(' · ')}</p>`:''}</section>`;
    }).join('')}</article>`;
  }
  function renderInspector(){
    const panel=$('obsInspector'),node=nodeMap.get(ui.selected);panel.hidden=!node;if(!node)return;
    const selectedEvent=node.items.find(e=>e.id===ui.event)||null;
    const metadata=selectedEvent?Object.fromEntries(Object.entries(selectedEvent).filter(([key])=>!['evidence','summary'].includes(key))):{node_type:node.kind,recorded_events:node.count,observed_status:node.status||'recorded',source:node.metadata?.source||'bridge-log',partial_trace:node.metadata?.partial??null};
    const ancestors=[];let p=node.parent;for(let i=0;p&&i<15;i++){const n=nodeMap.get(p);if(!n)break;ancestors.unshift(n.label);p=n.parent;}
    const readable=selectedEvent?[selectedEvent]:node.items.filter(e=>e.evidence&&Object.values(e.evidence).some(value=>value&&typeof value.text==='string')).slice(-3);
    const content=readable.map(readableEvidence).join('');
    const metrics=[['Events',node.count],['Tool duration',node.duration===null?'Not recorded':number(node.duration)+' ms'],['Tokens',number(node.tokens)],['Input tokens',number(sum(node.items,e=>e.tokens?.input))],['Output tokens',number(sum(node.items,e=>e.tokens?.output))],['Cache read',number(sum(node.items,e=>e.tokens?.cache_read))],['Cache write',number(sum(node.items,e=>e.tokens?.cache_write))]];
    panel.innerHTML=`<button type="button" class="obs-inspector-close" id="obsInspectorClose" aria-label="Close node inspector">×</button><h3>${escape(node.label)}</h3><div class="obs-note">${escape(ancestors.join(' › '))}</div><dl>${metrics.map(([label,value])=>`<dt>${label}</dt><dd>${value}</dd>`).join('')}<dt>Source</dt><dd>Recorded bridge log</dd></dl><div class="obs-note">${node.referencedOnly?'Delegation reference recorded; child tool details are not captured.':'Metrics reflect the available recorded log window.'}</div>${node.trace?`<button type="button" data-obs-open-run="${escape(node.trace)}">Open this run</button>`:''}${content?`${!selectedEvent&&readable.length>1?'<p class="obs-note">Recent recorded events. Select an activity row to inspect one event.</p>':''}${content}`:`<p class="obs-note">${ui.data?.capabilities?.owner_evidence?'No readable content recorded for this selection.':'Recorded prompts, arguments and results are available to the workspace owner.'}</p>`}<details class="obs-event-metadata"><summary>Event metadata</summary><pre>${escape(JSON.stringify(metadata,null,2))}</pre></details>`;
    $('obsInspectorClose').onclick=()=>{ui.selected='';ui.event='';renderVisuals();};
  }
  function renderVisuals(){
    buildNodes();const canvas=$('obsCanvas'),sx=canvas.scrollLeft,sy=canvas.scrollTop;
    studio.querySelectorAll('[data-obs-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.obsMode===ui.mode)));
    if(!ui.data?.traces?.length)canvas.innerHTML='<div class="obs-empty">'+(ui.error?escape(ui.error):'No supported execution traces are available yet. Real runs populate this view; sample data is not inserted.')+'</div>';
    else if(ui.mode==='tree')drawTree();else if(ui.mode==='treemap')drawTreemap();else if(ui.mode==='flow')drawFlow();else drawTimeline();
    canvas.scrollLeft=sx;canvas.scrollTop=sy;
    $('obsMetric').parentElement.hidden=ui.mode!=='treemap';$('obsMetric').disabled=!['treemap'].includes(ui.mode);for(const id of ['obsZoomOut','obsZoomIn','obsFit'])$(id).disabled=ui.mode!=='tree';
    const linked=nodes.filter(n=>n.kind==='tool'&&nodeMatches(n)).slice(0,24);
    $('obsLinkedLens').innerHTML='<p>Linked tool groups<br>Click to inspect in any lens</p>'+linked.map(n=>`<button type="button" class="${ui.selected===n.id?'selected':''}" data-obs-node="${escape(n.id)}">${escape(n.label)}<small>${n.count} events</small></button>`).join('');
    renderTools();renderRecordedActivity();renderInspector();
    const list=traces(),events=observedEvents();$('obsSummary').innerHTML=`<span><b>${list.length}</b> recorded runs</span><span><b>${events.filter(e=>e.kind==='tool_use').length}</b> tool events</span><span><b>${number(sum(list,t=>t.tokens))}</b> recorded tokens</span><span><b>${list.filter(t=>t.partial).length}</b> partial log windows</span>`;
  }
  function renderEvidence(){
    const sources=ui.data?.sources||[],failed=sources.filter(s=>s.state!=='available').length,partial=sources.some(s=>s.partial)||ui.data?.traces?.some(t=>t.partial);
    $('obsEvidence').textContent=ui.error?'Observation unavailable: '+ui.error+(ui.data?' · last successful snapshot retained':''):ui.data?`${ui.paused?'Paused snapshot':'Updated '+stamp(ui.data.observed_at)} · read-only recorded evidence${failed?' · '+failed+' source unavailable':''}${partial?' · bounded/partial history':''} · running labels inside logs are historical event states, not proof of a live process.`:'Sign in as an editor or owner to inspect execution traces.';
    $('obsPause').textContent=ui.paused?'Resume live':'Pause live';$('obsPause').setAttribute('aria-pressed',String(ui.paused));
  }
  function updateSelects(){
    const all=ui.data?.traces||[],projects=[...new Set(all.map(t=>t.project))];
    $('obsProject').innerHTML='<option value="">All observed projects</option>'+projects.map(p=>`<option>${escape(p)}</option>`).join('');$('obsProject').value=ui.project;
    const list=all.filter(t=>!ui.project||t.project===ui.project);if(!list.some(t=>t.id===ui.trace))ui.trace='';
    $('obsTrace').innerHTML='<option value="">All recorded runs</option>'+list.map(t=>`<option value="${t.id}">${escape(t.project+' · '+t.label)}</option>`).join('');$('obsTrace').value=ui.trace;
  }
  async function refresh(force=false){
    const identity=`${session?.subject||''}:${session?.level||0}`;
    if(identity!==ui.identity){ui.identity=identity;ui.data=null;ui.selected='';ui.error='';ui.last=0;ui.controller?.abort();renderVisuals();renderEvidence();}
    if(!(session?.level>=2)){ui.error='Editor or owner access is required';renderEvidence();renderVisuals();return;}
    if(document.hidden||!host.classList.contains('active')||ui.busy||ui.paused&&!force||!force&&Date.now()-ui.last<15000)return;
    ui.busy=true;const controller=new AbortController();ui.controller=controller;const timeout=setTimeout(()=>controller.abort(),10000);
    try{
      const data=await api('/api/observatory',{signal:controller.signal});if(identity!==`${session?.subject||''}:${session?.level||0}`)return;
      if(data.version!==1||!Array.isArray(data.traces))throw new Error('Invalid observation snapshot');
      const changed=JSON.stringify(ui.data?.traces)!==JSON.stringify(data.traces)||ui.error;ui.data=data;ui.error='';updateSelects();if(changed)renderVisuals();renderEvidence();
    }catch(error){if(identity===`${session?.subject||''}:${session?.level||0}`){ui.error=error.message||'Connection failed';renderEvidence();if(!ui.data)renderVisuals();}}
    finally{clearTimeout(timeout);ui.busy=false;ui.last=Date.now();}
  }
  $('obsFocus').onclick=()=>{const on=studio.classList.toggle('obs-focused');$('obsFocus').textContent=on?'Exit focus':'Focus';$('obsFocus').setAttribute('aria-pressed',String(on));};
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&studio.classList.contains('obs-focused'))$('obsFocus').click();});
  $('obsRefresh').onclick=()=>refresh(true);$('obsPause').onclick=()=>{ui.paused=!ui.paused;renderEvidence();if(!ui.paused)void refresh(true);};
  $('obsProject').onchange=e=>{ui.project=e.target.value;ui.trace='';ui.selected='';updateSelects();renderVisuals();};$('obsTrace').onchange=e=>{ui.trace=e.target.value;ui.selected='';ui.zoom=1;renderVisuals();};$('obsSearch').oninput=e=>{ui.search=e.target.value;renderVisuals();};$('obsMetric').onchange=e=>{ui.metric=e.target.value;renderVisuals();};
  $('obsZoomOut').onclick=()=>{ui.zoom=Math.max(.25,ui.zoom-.15);renderVisuals();};$('obsZoomIn').onclick=()=>{ui.zoom=Math.min(2.5,ui.zoom+.15);renderVisuals();};$('obsFit').onclick=()=>{const graph=$('obsCanvas').querySelector('.obs-graph');ui.zoom=graph?Math.max(.25,Math.min(1,$('obsCanvas').clientWidth/parseFloat(graph.style.width))):1;renderVisuals();$('obsCanvas').scrollTo(0,0);};
  studio.addEventListener('click',e=>{
    const copy=e.target.closest('[data-obs-copy]');
    if(copy){const event=observedEvents().find(item=>item.id===copy.dataset.obsCopy),field=event?.evidence?.[copy.dataset.obsCopyField];if(field&&typeof field.text==='string')navigator.clipboard.writeText(field.text).then(()=>{copy.textContent='Copied';}).catch(()=>{copy.textContent='Copy unavailable';});return;}
    const b=e.target.closest('[data-obs-mode],[data-obs-node],[data-obs-tools],[data-obs-open-run]');if(!b)return;
    if(b.dataset.obsMode){ui.mode=b.dataset.obsMode;renderVisuals();}
    if(b.dataset.obsTools){ui.tools=b.dataset.obsTools;renderTools();}
    if(b.dataset.obsNode){ui.event=b.dataset.obsEvent||'';selectNode(b.dataset.obsNode);}
    if(b.dataset.obsOpenRun){ui.trace=b.dataset.obsOpenRun;ui.selected=ui.trace;ui.zoom=1;updateSelects();renderVisuals();$('obsCanvas').scrollTo(0,0);}
  });
  $('obsCanvas').addEventListener('keydown',e=>{
    if(!['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','[',']','Escape'].includes(e.key))return;
    if(e.key==='Escape'){ui.selected='';renderVisuals();return;}
    const list=graphNodes();if(!list.length)return;e.preventDefault();
    const n=nodeMap.get(ui.selected)||list[0],siblings=list.filter(x=>x.parent===n.parent);let next;
    if(e.key==='ArrowLeft')next=nodeMap.get(n.parent);
    else if(e.key==='ArrowRight')next=list.find(x=>x.parent===n.id);
    else {const seq=e.key==='['||e.key===']'?list:siblings;const delta=e.key==='ArrowUp'||e.key==='['?-1:1;next=seq[Math.max(0,Math.min(seq.length-1,seq.findIndex(x=>x.id===n.id)+delta))];}
    if(next){selectNode(next.id);const target=$('obsCanvas').querySelector(`[data-obs-node="${CSS.escape(next.id)}"]`);target?.focus({preventScroll:true});target?.scrollIntoView({block:'nearest',inline:'nearest'});}
  });
  let drag=null;const canvas=$('obsCanvas');
  canvas.addEventListener('pointerdown',e=>{if(e.pointerType!=='mouse'||e.button!==0||e.target.closest('button'))return;drag={x:e.clientX,y:e.clientY,sx:canvas.scrollLeft,sy:canvas.scrollTop};canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(drag){canvas.scrollLeft=drag.sx-(e.clientX-drag.x);canvas.scrollTop=drag.sy-(e.clientY-drag.y);}});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])canvas.addEventListener(event,()=>{drag=null;});
  document.addEventListener('click',e=>{if(e.target.closest('[data-view],[data-home-go],[data-home-action],[data-inspector-agent-workspace]'))setTimeout(()=>{if(host.classList.contains('active'))void refresh();},0);});
  window.addEventListener('project-byte-home-render',()=>{if(ui.identity!==`${session?.subject||''}:${session?.level||0}`)void refresh(true);});
  window.addEventListener('project-byte-health',()=>{if(host.classList.contains('active'))void refresh();});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void refresh();});window.setInterval(refresh,15000);
  renderEvidence();renderVisuals();setTimeout(()=>refresh(true),0);
})();

// Live permissions are separate from the historical Observatory graph and code review.
(() => {
  const panel=document.querySelector('#pbObservatory .obs-approval-note');
  if(!panel)return;
  const style=document.createElement('style');
  style.textContent=`.permission-head,.permission-actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.permission-head b{flex:1}.permission-card{padding:12px;margin-top:12px;background:#091827;border:1px solid #31465c;border-radius:10px;min-width:0}.permission-card pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px;max-height:240px;overflow:auto}.permission-card textarea{display:block;width:100%;box-sizing:border-box;min-height:65px;margin:8px 0;padding:9px;border:1px solid #31465c;border-radius:8px;background:#0b1d2f;color:#dce9f7}.permission-card button,.permission-head button{min-height:44px}.permission-context{overflow-wrap:anywhere}.permission-feedback{margin:9px 0;color:#efcf9b}.permission-history{overflow-wrap:anywhere;margin-top:12px}.permission-card[aria-busy=true]{opacity:.7}`;
  document.head.appendChild(style);
  let identity='',epoch=0,controller=null,busy=false,last=0,rows=[],snapshot=null,feedback='',decisionFeedback='';
  const notes=new Map();
  const who=()=>JSON.stringify([accessKey,session?.subject,session?.level]);
  const owner=()=>!!accessKey&&session?.ok&&session?.level===4;
  const active=()=>!document.hidden&&document.querySelector('#agents')?.classList.contains('active');
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function clear(){epoch++;controller?.abort();controller=null;busy=false;rows=[];snapshot=null;notes.clear();feedback='';decisionFeedback='';last=0;}
  function render(){
    if(!owner()){
      panel.innerHTML='<b>Execution permissions</b><p>Owner sign-in is required to view or decide live tool requests. Historical traces and code-review decisions remain separate.</p>';
      return;
    }
    const connected=snapshot?.state==='connected';
    const title=connected?'Connected to registered runner':snapshot?.state==='not_connected'?'Not connected':'Connection unverified';
    panel.innerHTML=`<div class="permission-head"><b>Execution permissions · ${title}</b><button data-permission-refresh ${busy?'disabled':''}>Refresh</button></div><p>Approve once releases one requested tool. Deny rejects all pending tools in this session, including requests arriving before the runner processes the decision. Neither action merges code or verifies tool completion.</p><div class="permission-feedback" role="status">${escape(decisionFeedback)}${decisionFeedback&&feedback?'<br>':''}${escape(feedback)}</div>${connected?`<p class="permission-context">${escape(snapshot.repository)} · ${escape(snapshot.session_id)}</p>`:'<p>A permission-capable runner must be registered on the server. Historical traces never create approval requests.</p>'}<div class="permission-queue">${connected?rows.map(row=>{
      const r=row.request,remaining=Math.max(0,Math.ceil(row.expires_at-snapshot.server_time));
      const enabled=row.state==='pending'&&remaining>0&&!busy;
      return `<article class="permission-card" data-permission-handle="${escape(row.handle)}"><b>${escape(r.permission)} · ${escape(row.state)}</b><div class="permission-context">Tool ${escape(r.tool.callID)} · Message ${escape(r.tool.messageID)}</div><pre>${escape(JSON.stringify({patterns:r.patterns,metadata:r.metadata},null,2))}</pre><p>${row.state==='pending'?`Review window: ${remaining} seconds at last refresh. ${row.affected_count} pending in this session.`:'This request cannot be submitted again.'}</p>${enabled?`<label>Decision note (optional)<textarea maxlength="1000" data-permission-note="${escape(row.handle)}" placeholder="Stored in the audit. Denial notes are also sent to the runner.">${escape(notes.get(row.handle)||'')}</textarea></label><div class="permission-actions"><button data-permission-decision="approve_once" data-handle="${escape(row.handle)}">Approve once</button><button data-permission-decision="deny_session" data-handle="${escape(row.handle)}">Deny session’s pending tools</button></div>`:''}</article>`;
    }).join('')||'<p>No pending tool permission requests.</p>':''}</div>${snapshot?.history?.length?`<details class="permission-history"><summary>Recent permission audit</summary>${snapshot.history.map(item=>`<p>${escape(new Date(item.ts*1000).toLocaleString())} · ${escape(item.decision)} · ${escape(item.result)}${item.note?` · ${escape(item.note)}`:''}</p>`).join('')}</details>`:''}`;
  }
  async function request(path,options,current,turn){
    const response=await fetch(path,{...options,signal:controller.signal,headers:{'Content-Type':'application/json','X-Access-Key':accessKey}});
    const data=await response.json();
    if(turn!==epoch||who()!==current||!owner())return null;
    if(!response.ok){const error=new Error(data.error||'Permission runner is unavailable');error.response=data;throw error;}
    return data;
  }
  async function refresh(force=false){
    const current=who();
    if(identity!==current){clear();identity=current;render();}
    if(!panel.isConnected||!owner()||busy||(!force&&(!active()||Date.now()-last<5000)))return;
    // Keep a note being edited stable while polling; decisions always recheck server state.
    if(!force&&panel.contains(document.activeElement)&&document.activeElement.matches('textarea'))return;
    busy=true;controller=new AbortController();const turn=epoch;
    try{
      const data=await request('/api/execution-permissions',{},current,turn);
      if(!data)return;
      snapshot=data;rows=data.requests||[];feedback='';
      const keep=new Set(rows.filter(r=>r.state==='pending').map(r=>r.handle));
      for(const handle of notes.keys())if(!keep.has(handle))notes.delete(handle);
    }catch(error){if(turn===epoch&&who()===current){snapshot=error.response||null;rows=[];feedback=error.message;}}
    finally{if(turn===epoch&&who()===current){busy=false;last=Date.now();render();}}
  }
  panel.addEventListener('input',event=>{const handle=event.target.dataset.permissionNote;if(handle)notes.set(handle,event.target.value);});
  panel.addEventListener('click',async event=>{
    if(event.target.closest('[data-permission-refresh]')){feedback='';void refresh(true);return;}
    const button=event.target.closest('[data-permission-decision]');
    if(!button||busy||!owner()||who()!==identity)return;
    const row=rows.find(r=>r.handle===button.dataset.handle);
    if(!row||row.state!=='pending')return;
    const elapsed=(Date.now()-last)/1000;
    if(row.expires_at<=snapshot.server_time+elapsed){feedback='The review window expired. Refresh the queue.';void refresh(true);return;}
    const current=who(),turn=epoch;
    const body={handle:row.handle,review_token:row.review_token,decision:button.dataset.permissionDecision,message:notes.get(row.handle)||''};
    busy=true;controller=new AbortController();render();
    try{
      const data=await request('/api/execution-permissions/decision',{method:'POST',body:JSON.stringify(body)},current,turn);
      if(!data)return;
      decisionFeedback=data.message;notes.delete(row.handle);
    }catch(error){if(turn===epoch&&who()===current)decisionFeedback='Decision could not be confirmed. Refresh to inspect its state; do not retry blindly. '+error.message;}
    finally{if(turn===epoch&&who()===current){busy=false;rows=[];snapshot=null;render();void refresh(true);}}
  });
  document.addEventListener('click',event=>{
    if(event.target.closest('#login,#logoutAllLocal')){clear();render();}
    setTimeout(()=>void refresh(),0);
  },true);
  window.addEventListener('project-byte-home-render',()=>void refresh());
  window.addEventListener('hashchange',()=>void refresh());
  document.addEventListener('visibilitychange',()=>void refresh());
  setInterval(()=>void refresh(),1000);
  render();
})();

// PRFKT_SPOTIFY_PLAYER_BEGIN — isolated from workspace actions and agent execution.
(() => {
  'use strict';
  const ARTIST = '6aGxmrOqjSpDGvIJdId29O';
  const API = 'https://api.spotify.com/v1';
  const ACCOUNT = 'https://accounts.spotify.com';
  const PREFIX = 'prfkt.spotify.';
  const SCOPE = 'streaming user-read-email user-read-private user-read-playback-state user-modify-playback-state';
  const validId = value => typeof value === 'string' && /^[a-zA-Z0-9]{22}$/.test(value);
  const base64url = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=/g,'').replace(/\+/g,'-').replace(/\//g,'_');
  const sha = async value => base64url(await crypto.subtle.digest('SHA-256',new TextEncoder().encode(value)));
  const random = () => base64url(crypto.getRandomValues(new Uint8Array(32)));
  class SpotifySession {
    constructor({fetcher=(...args)=>fetch(...args),storage=sessionStorage,origin=location.origin,clock=Date.now,timeoutMs=20000}={}) {
      Object.assign(this,{fetcher,storage,origin,clock,timeoutMs});
      this.tokens=null;this.epoch=0;this.refreshing=null;this.retryAt=0;this.abort=new AbortController();
    }
    read(key) { try { const raw=this.storage.getItem(PREFIX+key);return raw&&raw.length<16000?JSON.parse(raw):null; } catch { return null; } }
    remove(key) { try { this.storage.removeItem(PREFIX+key); } catch {} }
    save(key,value) { this.storage.setItem(PREFIX+key,JSON.stringify(value)); }
    clear() {
      this.epoch++;this.abort.abort();this.abort=new AbortController();this.tokens=null;this.refreshing=null;this.retryAt=0;
      for(const key of ['tokens','pending','callback'])this.remove(key);
    }
    async authorize(clientId,identity) {
      if(!/^[a-f0-9]{32}$/i.test(clientId))throw new Error('Enter the public Client ID from your Spotify app. Never enter a client secret.');
      const u=new URL(this.origin);
      if(u.protocol!=='https:' && !(u.protocol==='http:'&&['127.0.0.1','[::1]'].includes(u.hostname)))throw new Error('Spotify needs HTTPS or an explicit loopback address.');
      const turn=this.epoch,verifier=random(),state=random(),bound=await sha(identity),challenge=await sha(verifier);
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      const redirect=this.origin+'/spotify-callback';
      this.save('pending',{clientId,verifier,state,bound,redirect,at:this.clock()});
      const params=new URLSearchParams({client_id:clientId,response_type:'code',redirect_uri:redirect,scope:SCOPE,state,code_challenge_method:'S256',code_challenge:challenge});
      return ACCOUNT+'/authorize?'+params;
    }
    async restore(identity) {
      const turn=this.epoch,bound=await sha(identity);
      if(turn!==this.epoch)return false;
      const callback=this.read('callback'),pending=this.read('pending');
      this.remove('callback');
      if(callback) {
        this.remove('pending');
        if(!pending || pending.bound!==bound || pending.state!==callback.state || pending.redirect!==this.origin+'/spotify-callback' || this.clock()-pending.at>600000 || this.clock()<pending.at)throw new Error('Spotify sign-in expired or did not match this workspace. Connect again.');
        if(callback.error || typeof callback.code!=='string'||!callback.code||callback.code.length>2048)throw new Error('Spotify sign-in was not completed. Connect again.');
        this.tokens={clientId:pending.clientId,bound};
        await this.exchange({grant_type:'authorization_code',code:callback.code,redirect_uri:pending.redirect,code_verifier:pending.verifier},turn);
        return true;
      }
      const saved=this.read('tokens');
      if(saved && saved.bound===bound && /^[a-f0-9]{32}$/i.test(saved.clientId||'') && typeof saved.access_token==='string' && Number.isFinite(saved.expiresAt)) {
        this.tokens=saved;await this.token();return true;
      }
      this.remove('tokens');return false;
    }
    async request(url,options) {
      const controller=new AbortController(),parent=this.abort.signal;
      let timedOut=false;
      const cancel=()=>controller.abort();parent.addEventListener('abort',cancel,{once:true});
      if(parent.aborted)cancel();
      const timer=setTimeout(()=>{timedOut=true;controller.abort();},this.timeoutMs);
      try{return await this.fetcher(url,{...options,signal:controller.signal});}
      catch(error){if(timedOut)throw new Error('Spotify request timed out. Check playback before retrying.');throw error;}
      finally{clearTimeout(timer);parent.removeEventListener('abort',cancel);}
    }
    async exchange(values,turn=this.epoch) {
      const current=this.tokens;
      if(!current)throw new Error('Connect Spotify first.');
      const response=await this.request(ACCOUNT+'/api/token',{method:'POST',credentials:'omit',referrerPolicy:'no-referrer',signal:this.abort.signal,headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams({...values,client_id:current.clientId})});
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      if(!response.ok) { this.remove('tokens');this.tokens=null;throw new Error('Spotify authorization could not be renewed. Connect again.'); }
      const data=await response.json();
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      if(typeof data.access_token!=='string'||data.access_token.length>10000||!Number.isFinite(data.expires_in)||data.expires_in<=0)throw new Error('Spotify returned an invalid authorization response.');
      this.tokens={...current,access_token:data.access_token,refresh_token:data.refresh_token||current.refresh_token,expiresAt:this.clock()+data.expires_in*1000};
      this.save('tokens',this.tokens);
      return this.tokens.access_token;
    }
    async token() {
      if(!this.tokens)throw new Error('Connect Spotify first.');
      if(this.tokens.expiresAt>this.clock()+60000)return this.tokens.access_token;
      if(!this.tokens.refresh_token)throw new Error('Spotify sign-in expired. Connect again.');
      if(!this.refreshing) { const pending=this.exchange({grant_type:'refresh_token',refresh_token:this.tokens.refresh_token});this.refreshing=pending;pending.finally(()=>{if(this.refreshing===pending)this.refreshing=null;}).catch(()=>{}); }
      return this.refreshing;
    }
    async api(path,method='GET',body) {
      const url=new URL(API+path);
      if(url.origin!=='https://api.spotify.com'||!/^\/v1\/(artists\/[A-Za-z0-9]{22}\/albums|albums\/[A-Za-z0-9]{22}\/tracks|me\/player(?:\/(play|repeat|shuffle))?)$/.test(url.pathname))throw new Error('Unexpected Spotify endpoint.');
      if(this.clock()<this.retryAt)throw new Error('Spotify is rate limiting requests. Wait before trying again.');
      const turn=this.epoch,token=await this.token();
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      const response=await this.request(url.href,{method,credentials:'omit',referrerPolicy:'no-referrer',signal:this.abort.signal,headers:{Authorization:'Bearer '+token,...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      if(response.status===429) { this.retryAt=this.clock()+Math.min(3600,Math.max(1,Number(response.headers.get('Retry-After'))||30))*1000;throw new Error('Spotify is rate limiting requests. Wait before trying again.'); }
      if(!response.ok)throw new Error(response.status===401?'Spotify sign-in expired. Connect again.':response.status===403?'Spotify requires Premium and access to this developer app.':response.status===404?'This Spotify device is unavailable. Reconnect the player.':'Spotify request failed. Try again.');
      const data=response.status===204?null:await response.json();
      if(turn!==this.epoch)throw new Error('Workspace session changed.');
      return data;
    }
    async pages(path) {
      const output=[],seen=new Set();let next=path;
      while(next) {
        if(seen.has(next)||seen.size>=40)throw new Error('Spotify catalog pagination could not be completed.');
        seen.add(next);const page=await this.api(next);
        if(!Array.isArray(page?.items))throw new Error('Spotify catalog is unavailable.');
        output.push(...page.items);
        if(page.next) { const url=new URL(page.next);if(url.origin!=='https://api.spotify.com'||url.pathname!==new URL(API+path).pathname)throw new Error('Unexpected Spotify catalog continuation.');next=url.pathname.slice(3)+url.search; } else next=null;
      }
      return output;
    }
    async discography() {
      const albums=await this.pages('/artists/'+ARTIST+'/albums?include_groups=album,single&limit=50');
      const unique=new Map(albums.filter(a=>validId(a?.id)&&a.artists?.some(x=>x.id===ARTIST)).map(a=>[a.id,a]));
      const ordered=[...unique.values()].sort((a,b)=>(a.release_date||'').localeCompare(b.release_date||'')||a.name.localeCompare(b.name)||a.id.localeCompare(b.id));
      const seen=new Set(),tracks=[];
      for(const album of ordered) {
        const rows=await this.pages('/albums/'+album.id+'/tracks?limit=50');
        rows.sort((a,b)=>(a.disc_number||1)-(b.disc_number||1)||(a.track_number||0)-(b.track_number||0));
        for(const t of rows) {
          if(!validId(t?.id)||t.is_playable===false||!t.artists?.some(a=>a.id===ARTIST))continue;
          const key=t.linked_from?.id||t.id;
          if(seen.has(key))continue;seen.add(key);tracks.push({uri:'spotify:track:'+t.id,name:t.name,album:album.name});
        }
      }
      if(!tracks.length)throw new Error('No playable Saxon Shore tracks are available for this Spotify account.');
      if(tracks.length>100)throw new Error('The catalog exceeds this player’s queue limit; it was not truncated.');
      return tracks;
    }
  }
  if(typeof module!=='undefined'&&module.exports){module.exports={SpotifySession,ARTIST,sha};return;}
  if(!document.querySelector('.home-hero-top')||document.getElementById('homeMusic'))return;
  const css=document.createElement('style');
  css.textContent=`
    #homeMusic{width:244px;flex:0 0 244px;min-width:0;margin-left:auto;padding:11px 12px;border:1px solid #759ec044;border-radius:14px;background:linear-gradient(145deg,#142539e8,#06111bea);box-shadow:inset 0 1px 0 #e6f4ff12,0 8px 25px #0004;color:#e8f1fb;text-align:left}
    #homeMusic *{box-sizing:border-box}#homeMusic .hm-top{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-bottom:7px}#homeMusic .hm-spotify{font-size:10px;letter-spacing:.03em;color:#88d9b0;text-decoration:none}#homeMusic .hm-top span{font-size:10px;color:#93a9bb}
    #homeMusic .hm-track{display:flex;align-items:center;gap:9px;min-width:0}#homeMusic .hm-art{width:36px;height:36px;border-radius:6px;object-fit:contain;flex:none}#homeMusic .hm-art[hidden]{display:none}#homeMusic .hm-meta{min-width:0;flex:1}#homeMusic .hm-title{display:block;color:#eff7ff;text-decoration:none;font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}#homeMusic .hm-artist{display:block;font-size:11px;color:#9eb8d0;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    #homeMusic .hm-controls{display:flex;align-items:center;gap:5px;margin-top:8px}#homeMusic button{display:grid;place-items:center;min-width:36px;width:36px;min-height:36px;height:36px;padding:0;border-radius:10px;border:1px solid #7499bd42;background:#10263a;color:#bcdbf6;cursor:pointer}#homeMusic button svg{width:16px;height:16px;fill:currentColor;pointer-events:none}#homeMusic button:disabled{opacity:.38;cursor:default}#homeMusic button:focus-visible,#homeMusic a:focus-visible{outline:2px solid #9bcfff;outline-offset:3px}#homeMusic [data-hm=play]{color:#90e6bd;background:#16352d}#homeMusic [data-hm=settings]{margin-left:auto;background:transparent;border-color:transparent}#homeMusic .hm-status{font-size:10px;line-height:1.35;color:#aac0d4;margin-top:5px;overflow-wrap:anywhere}#homeMusic[data-error=true] .hm-status{color:#efbf82}
    #homeMusicDialog{box-sizing:border-box;width:min(460px,calc(100vw - 28px));max-height:85dvh;overflow:auto;padding:22px;border:1px solid #416280;border-radius:20px;background:#0b1726;color:#e6effa}#homeMusicDialog::backdrop{background:#030914c9;backdrop-filter:blur(5px)}#homeMusicDialog h2{font-size:21px;margin:0 0 12px}#homeMusicDialog p{font-size:13px;line-height:1.6;color:#afc2d6}#homeMusicDialog label{display:block;font-size:13px;margin-top:15px}#homeMusicDialog input{box-sizing:border-box;width:100%;font-size:16px;margin:7px 0 4px;padding:12px;border:1px solid #43617b;background:#08121e;border-radius:10px;color:#edf5ff}#homeMusicDialog code{display:block;overflow-wrap:anywhere;white-space:normal;font-size:12px;color:#b6dbff;padding:10px;background:#06101a;border-radius:8px}#homeMusicDialog .hm-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:16px}#homeMusicDialog button{min-height:42px;padding:9px 14px}#homeMusicDialog a{color:#9bceff}
    @media(max-width:650px){#homeMusic{width:142px;flex-basis:142px;padding:8px}.home-hero-top>div:first-child{min-width:0;flex:1}.home-hero-top>div:first-child .home-eyebrow{overflow-wrap:anywhere}#homeMusic .hm-title{font-size:11px}#homeMusic .hm-artist{font-size:10px}#homeMusic .hm-art{width:28px;height:28px}#homeMusic .hm-controls{gap:0;justify-content:space-between}#homeMusic button{min-width:30px;width:30px;min-height:40px;height:40px}#homeMusic .hm-status{font-size:9px}#homeMusic .hm-top{margin-bottom:5px}.home-hero-top h2{font-size:clamp(22px,6vw,30px)}}
  `;document.head.append(css);
  const widget=document.createElement('section');widget.id='homeMusic';widget.setAttribute('aria-label','Saxon Shore music player');
  const glyphs={play:'<path d="M6 3l15 9L6 21z"/>',pause:'<path d="M5 3h5v18H5zm9 0h5v18h-5z"/>',stop:'<rect x="4" y="4" width="16" height="16" rx="2"/>',settings:'<circle cx="4" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="20" cy="12" r="2"/>'};
  widget.innerHTML=`<div class="hm-top"><a class="hm-spotify" href="https://open.spotify.com/artist/${ARTIST}" target="_blank" rel="noopener noreferrer">Spotify ↗</a><span id="hmRepeat" title="Repeat the discography">↻ ALL</span></div><div class="hm-track"><img class="hm-art" alt="Album artwork" hidden referrerpolicy="no-referrer"><div class="hm-meta"><a class="hm-title" href="https://open.spotify.com/artist/${ARTIST}" target="_blank" rel="noopener noreferrer">Discography</a><span class="hm-artist">Saxon Shore</span></div></div><div class="hm-controls">${Object.entries(glyphs).map(([key,svg])=>`<button type="button" data-hm="${key}" aria-label="${key==='settings'?'Spotify connection settings':key[0].toUpperCase()+key.slice(1)+' music'}" title="${key==='settings'?'Spotify connection':key}"><svg viewBox="0 0 24 24" aria-hidden="true">${svg}</svg></button>`).join('')}</div><div class="hm-status" role="status" aria-live="polite">Connect Spotify</div>`;
  document.querySelector('.home-hero-top').append(widget);
  const dialog=document.createElement('dialog');dialog.id='homeMusicDialog';dialog.setAttribute('aria-labelledby','hmDialogTitle');
  dialog.innerHTML=`<h2 id="hmDialogTitle">Spotify on Home</h2><p>Play Saxon Shore’s available albums and EPs in release order, on repeat. Playback uses your Spotify Premium account on this device.</p><div id="hmSetup"><p>One-time app setup: register the callback below in your <a href="https://developer.spotify.com/dashboard" target="_blank" rel="noopener noreferrer">Spotify developer app</a>, then enter its public Client ID. No client secret is used.</p><code id="hmCallback"></code><label for="hmClientId">Spotify Client ID</label><input id="hmClientId" autocomplete="off" spellcheck="false" maxlength="32" placeholder="32-character public Client ID"></div><p id="hmDialogStatus" role="status"></p><div class="hm-actions"><button type="button" id="hmConnect">Connect Spotify</button><button type="button" id="hmDisconnect">Disconnect</button><button type="button" id="hmClose">Done</button></div><p>Stop pauses and returns the current song to its beginning. Music continues when you navigate the dashboard. Sign-out disconnects this player. Spotify availability and browser playback restrictions apply.</p>`;document.body.append(dialog);
  dialog.querySelector('#hmCallback').textContent=location.origin+'/spotify-callback';
  const client=new SpotifySession(),button=name=>widget.querySelector('[data-hm='+name+']');
  let identity=null,player=null,device='',tracks=[],started=false,paused=true,stopped=false,busy=false,command=0,chain=Promise.resolve(),sdkPromise=null,restoring=false;
  let status='Connect Spotify',failure=false;
  const ownerIdentity=()=>typeof session!=='undefined'&&session?.ok&&session.level===4&&typeof accessKey!=='undefined'&&accessKey?JSON.stringify([accessKey,session.subject,session.name,session.level]):null;
  const message=(value,error=false)=>{status=value;failure=error;render();};
  function render(){widget.dataset.error=String(failure);widget.querySelector('.hm-status').textContent=identity?status:'Sign in to connect';dialog.querySelector('#hmDialogStatus').textContent=identity?status:'Sign in to your workspace first.';button('play').disabled=!identity||busy;button('pause').disabled=!player||!device||paused||busy;button('stop').disabled=!player||!device;dialog.querySelector('#hmConnect').disabled=!identity||busy;dialog.querySelector('#hmDisconnect').disabled=!client.tokens&&!restoring;widget.querySelector('#hmRepeat').title=started?'Repeat all requested from Spotify':'Repeat all when playback starts';}
  function reset(){command++;client.clear();player?.disconnect();player=null;device='';tracks=[];started=false;paused=true;stopped=false;busy=false;restoring=false;widget.querySelector('.hm-title').textContent='Discography';widget.querySelector('.hm-title').href='https://open.spotify.com/artist/'+ARTIST;widget.querySelector('.hm-artist').textContent='Saxon Shore';const art=widget.querySelector('.hm-art');art.hidden=true;art.removeAttribute('src');message('Connect Spotify');}
  function loadSDK(){
    if(window.Spotify?.Player)return Promise.resolve();if(sdkPromise)return sdkPromise;
    sdkPromise=new Promise((resolve,reject)=>{const script=document.createElement('script');let timer;const cleanup=()=>{clearTimeout(timer);script.onerror=null;};window.onSpotifyWebPlaybackSDKReady=()=>{cleanup();resolve();};script.src='https://sdk.scdn.co/spotify-player.js';script.async=true;script.referrerPolicy='no-referrer';script.onerror=()=>{cleanup();script.remove();sdkPromise=null;reject(new Error('Spotify player could not load. Check the connection and browser policy.'));};timer=setTimeout(()=>{script.remove();sdkPromise=null;reject(new Error('Spotify player timed out. Reconnect to retry.'));},20000);document.head.append(script);});return sdkPromise;
  }
  async function preparePlayer(){
    const bound=identity,turn=client.epoch;await loadSDK();if(bound!==identity||turn!==client.epoch)return;
    const p=new window.Spotify.Player({name:'PRFKT_PROJECT · Home',volume:.35,enableMediaSession:true,getOAuthToken:callback=>{client.token().then(token=>{if(player===p&&bound===identity)callback(token);}).catch(()=>{if(player===p)message('Spotify sign-in expired. Reconnect.',true);});}});player=p;
    p.addListener('ready',({device_id})=>{if(player!==p)return;device=device_id;message('Ready · press Play');});
    p.addListener('not_ready',()=>{if(player!==p)return;device='';paused=true;message('Spotify device offline. Reconnect.',true);});
    p.addListener('player_state_changed',state=>{if(player!==p)return;if(!state){paused=true;message('Playback moved to another device');return;}paused=state.paused;const t=state.track_window?.current_track;if(t){widget.querySelector('.hm-title').textContent=t.name||'Untitled track';widget.querySelector('.hm-title').title=t.name||'';widget.querySelector('.hm-title').href=validId(t.id)?'https://open.spotify.com/track/'+t.id:'https://open.spotify.com/artist/'+ARTIST;widget.querySelector('.hm-artist').textContent=(t.artists||[]).map(a=>a.name).join(', ');const art=widget.querySelector('.hm-art'),src=t.album?.images?.[0]?.url;try{const u=new URL(src);if(u.protocol!=='https:'||u.hostname!=='i.scdn.co')throw 0;art.src=u.href;art.hidden=false;}catch{art.hidden=true;art.removeAttribute('src');}}
      const inQueue=tracks.some(x=>x.uri===t?.uri);if(started&&!inQueue)started=false;
      message(stopped&&paused?'Stopped':paused?'Paused':state.repeat_mode===1&&inQueue?'Playing · repeat all':'Playing');});
    for(const [event,text] of Object.entries({initialization_error:'This browser cannot initialize Spotify playback.',authentication_error:'Spotify sign-in expired. Reconnect.',account_error:'Spotify Premium is required for this player.',playback_error:'Spotify could not play this song.',autoplay_failed:'Tap Play to allow audio in this browser.'}))p.addListener(event,()=>{if(player===p)message(text,true);});
    if(!await p.connect()){if(player===p)message('Spotify connection failed. Reconnect.',true);}
  }
  async function reconcile(){const next=ownerIdentity();if(next===identity)return;const old=identity;identity=next;if(old)reset();if(!next){render();return;}restoring=true;render();const turn=client.epoch;try{if(await client.restore(next)){if(identity===next&&client.epoch===turn){message('Connecting Spotify…');await preparePlayer();}}}catch(e){if(identity===next)message(e.name==='AbortError'?'Spotify connection cancelled.':e.message,true);}finally{if(identity===next){restoring=false;render();}}}
  function openSettings(){try{dialog.querySelector('#hmClientId').value=localStorage.getItem(PREFIX+'clientId')||'';}catch{}render();dialog.showModal();}
  button('settings').onclick=openSettings;dialog.querySelector('#hmClose').onclick=()=>dialog.close();dialog.onclick=e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}};
  dialog.querySelector('#hmConnect').onclick=async()=>{if(!identity||busy)return;const bound=identity;busy=true;message('Opening Spotify sign-in…');try{const id=dialog.querySelector('#hmClientId').value.trim();const url=await client.authorize(id,bound);if(bound!==identity)return;try{localStorage.setItem(PREFIX+'clientId',id);}catch{}location.assign(url);}catch(e){message(e.message,true);}finally{busy=false;render();}};
  dialog.querySelector('#hmDisconnect').onclick=()=>{reset();dialog.close();};
  function enqueue(action){const ticket=++command;busy=true;render();chain=chain.catch(()=>{}).then(async()=>{if(ticket!==command||!identity)return;try{await action(()=>ticket===command&&!!identity);}catch(e){if(ticket===command)message(e.name==='AbortError'?'Playback request cancelled.':e.message,true);}finally{if(ticket===command){busy=false;render();}}});}
  button('play').onclick=()=>{
    if(!identity||busy)return;if(!client.tokens){openSettings();return;}if(!player||!device){message('Reconnect Spotify to make this device ready.',true);openSettings();return;}
    const activation=player.activateElement();
    enqueue(async current=>{await activation;if(!current())return;stopped=false;
      if(started){await player.resume();return;}
      message('Loading Saxon Shore discography…');if(!tracks.length)tracks=await client.discography();if(!current())return;
      const target='?device_id='+encodeURIComponent(device);
      await client.api('/me/player','PUT',{device_ids:[device],play:false});if(!current())return;
      await client.api('/me/player/shuffle'+target+'&state=false','PUT');if(!current())return;
      await client.api('/me/player/repeat'+target+'&state=context','PUT');if(!current())return;
      message('Starting · '+tracks.length+' tracks');await client.api('/me/player/play'+target,'PUT',{uris:tracks.map(t=>t.uri),position_ms:0});if(current())started=true;
    });
  };
  button('pause').onclick=()=>enqueue(async()=>{await player.pause();});
  button('stop').onclick=()=>enqueue(async current=>{await player.pause();if(!current())return;await player.seek(0);stopped=true;paused=true;message('Stopped');});
  document.addEventListener('click',e=>{if(e.target.closest('#login,#logoutAllLocal')){identity=null;reset();}},true);
  window.addEventListener('project-byte-session-ended',()=>{identity=null;reset();});
  window.addEventListener('project-byte-home-render',()=>void reconcile());
  window.addEventListener('pagehide',()=>{command++;player?.disconnect();});
  setInterval(()=>void reconcile(),1000);void reconcile();render();
})();

// PRFKT direct manipulation: task status only; execution remains an explicit queue action.
(() => {
  if (window.PRFKT_DIRECT_INTERACTION || typeof card !== 'function') return;
  window.PRFKT_DIRECT_INTERACTION = true;
  const board = document.getElementById('boardGrid');
  if (!board) return;
  let gesture = null, saving = false, frame = 0, suppressClickUntil = 0, undo = null;
  const editable = () => session.ok && session.level >= 2;
  const actor = () => String(session.subject || session.name || '') + ':' + session.level;
  const style = document.createElement('style');
  style.textContent = `
    #boardGrid .task{position:relative;transition:border-color .12s,box-shadow .12s}
    #boardGrid .task[data-grabbable=true]{cursor:grab}
    #boardGrid .task-title{display:block;padding:0;border:0;background:none;color:inherit;font:inherit;font-weight:inherit;text-align:left;width:100%;cursor:pointer}
    #boardGrid .task h3{padding-right:42px}
    #boardGrid .task-grip{position:absolute;right:7px;top:7px;display:grid;place-items:center;width:40px;height:40px;padding:0;border:1px solid #78b5df38;border-radius:10px;background:#0b1a29;color:#97cbed;cursor:grab;touch-action:none;font-size:23px}
    #boardGrid .task-grip:disabled{opacity:.35;cursor:default}
    #boardGrid .task-grip:focus-visible,#boardGrid .task-title:focus-visible{outline:2px solid #77caff;outline-offset:3px}
    #boardGrid .task.is-lifted{opacity:.35;border-style:dashed}
    #boardGrid .col.drop-candidate{outline:1px dashed #8ac7ef60;outline-offset:-3px}
    #boardGrid .col.drop-target{outline:2px solid #78cfff;outline-offset:-3px;box-shadow:inset 0 0 40px #158cdc25}
    #boardGrid .col.drop-target>.colhead{color:#bce8ff;background:#133754}
    .prfkt-drag-ghost{position:fixed;left:0;top:0;z-index:10000;pointer-events:none;max-width:min(290px,75vw);padding:15px 18px;border:1px solid #7dcfff;border-radius:14px;background:#10243af5;color:#edf7ff;box-shadow:0 18px 44px #0009;overflow-wrap:anywhere}
    .prfkt-drag-ghost small{display:block;color:#9ed4f7;margin-top:6px}
    body.prfkt-grabbing,body.prfkt-grabbing *{cursor:grabbing!important;user-select:none!important}
    .boardscroll{cursor:grab;overscroll-behavior-x:contain}
    body.prfkt-grabbing .boardscroll,body.prfkt-grabbing .home-scopes{scroll-snap-type:none!important;scroll-behavior:auto!important}
    .prfkt-board-help{color:#a8bfd2;font-size:12px;margin:0 0 10px;line-height:1.6}
    .prfkt-move-toast{position:fixed;right:16px;bottom:100px;z-index:160;max-width:min(440px,calc(100vw - 32px));padding:14px 16px;background:#102237;border:1px solid #6fa7cc;border-radius:14px;box-shadow:0 12px 35px #0009;color:#ecf7ff;display:flex;align-items:center;gap:12px}
    .prfkt-move-toast[hidden]{display:none}.prfkt-move-toast span{overflow-wrap:anywhere;min-width:0}.prfkt-move-toast button{min-height:40px;flex-shrink:0;background:#183952;color:#c5eaff;border:1px solid #658eac;border-radius:9px;padding:7px 11px}.prfkt-move-toast button:focus-visible{outline:2px solid #8bd2ff;outline-offset:2px}
    body.reduced-motion #boardGrid .task{transition:none}
    @media(prefers-reduced-motion:reduce){#boardGrid .task{transition:none}}
  `;
  document.head.append(style);
  const help = document.createElement('p');
  help.className = 'prfkt-board-help'; help.id = 'prfktMoveHelp';
  help.textContent = 'Drag a card or grip to move. Space + arrows + Enter for keyboard. Grab empty space to pan.';
  board.parentElement.before(help);
  const toast = document.createElement('div'); toast.className = 'prfkt-move-toast'; toast.hidden = true;
  const message = document.createElement('span'); message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
  const undoButton = document.createElement('button'); undoButton.textContent = 'Undo'; undoButton.hidden = true;
  const dismiss = document.createElement('button'); dismiss.textContent = '×'; dismiss.setAttribute('aria-label', 'Dismiss move message');
  toast.append(message, undoButton, dismiss); document.body.append(toast);
  dismiss.onclick = () => { toast.hidden = true; undo = null; };
  function announce(text, offerUndo = false) {
    toast.hidden = false; message.textContent = text; undoButton.hidden = !offerUndo;
  }
  function focusCard(id) {
    board.querySelector('[data-task-id="' + CSS.escape(String(id)) + '"] .task-grip')?.focus({preventScroll:true});
  }
  async function saveMove(id, from, to, title, isUndo = false) {
    if (saving || !editable() || from === to || !S.includes(to)) return;
    saving = true; board.setAttribute('aria-busy','true'); board.inert = true; const owner = actor(); undo = null;
    announce(isUndo ? 'Undoing move…' : 'Saving move…');
    let persisted = false;
    try {
      const latest = await api('/api/tasks');
      const current = latest.tasks.find(t => String(t.id) === String(id));
      if (owner !== actor() || !editable()) throw new Error('Your access changed. Sign in again to move this card.');
      if (!current) throw new Error('This card is no longer available. Refresh the board.');
      if (current.status !== from) throw new Error('This card has changed since you picked it up. Refresh the board before moving it.');
      await api('/api/tasks/' + encodeURIComponent(id), {method:'PATCH', body:JSON.stringify({status:to})});
      persisted = true;
      // Never render a successful move before the server accepts it.
      const local = tasks.find(t => String(t.id) === String(id)); if (local) local.status = to;
      await load();
      if (owner !== actor() || !editable()) { toast.hidden = true; return; }
      undo = isUndo ? null : {id, from:to, to:from, title, actor:owner};
      announce(isUndo ? 'Move undone.' : '“' + title + '” moved to ' + to + '.', !!undo);
    } catch (error) {
      if (owner === actor()) announce((persisted ? 'Move saved; refresh needed. ' : 'Move not saved. ') + error.message);
    } finally { saving = false; board.inert = false; board.setAttribute('aria-busy','false'); renderBoard(); focusCard(id); }
  }
  undoButton.onclick = () => {
    const change = undo;
    if (change && change.actor === actor()) saveMove(change.id, change.from, change.to, change.title, true);
  };
  const originalCard = card;
  card = function(t) {
    const el = originalCard(t); el.dataset.taskId = t.id; el.dataset.grabbable = String(editable() && !saving);
    const heading = el.querySelector('h3');
    if (heading) {
      const title = document.createElement('button'); title.className = 'task-title'; title.textContent = t.title;
      title.onclick = () => openTask(t.id); heading.replaceChildren(title);
    }
    const grip = document.createElement('button'); grip.type = 'button'; grip.className = 'task-grip'; grip.textContent = '⠿';
    grip.disabled = !editable() || saving; grip.setAttribute('aria-label', 'Move ' + t.title);
    grip.setAttribute('aria-describedby', 'prfktMoveHelp'); grip.title = 'Drag to move · Space for keyboard controls';
    el.prepend(grip);
    return el;
  };
  const originalRender = renderBoard;
  renderBoard = function(...args) {
    if (gesture) cancel('Move cancelled because the board updated.');
    if (!editable()) { undo = null; toast.hidden = true; }
    originalRender(...args);
    board.querySelectorAll(':scope > .col').forEach((col, i) => { col.dataset.dropStatus = S[i]; });
    help.textContent = editable() ? 'Drag a card or grip to move. Space + arrows + Enter for keyboard. Grab empty space to pan.' : 'Read-only board. Open a card title to view details. Grab empty space to pan.';
  };
  const originalEditing = userIsEditing;
  userIsEditing = function(...args) { return !!gesture || saving || originalEditing(...args); };
  function setTarget(status) {
    if (!gesture || gesture.kind !== 'card') return;
    if (gesture.target === status) return;
    gesture.target = status;
    board.querySelectorAll('.col').forEach(c => c.classList.toggle('drop-target', c.dataset.dropStatus === status));
    if (gesture.label) gesture.label.textContent = status ? 'Drop in ' + status : 'Release outside a column to cancel';
    if (gesture.keyboard) announce('Move to ' + status + '. Enter to drop, Escape to cancel.');
  }
  function activate() {
    const g = gesture; if (!g || g.active) return;
    g.active = true; document.body.classList.add('prfkt-grabbing');
    if (g.kind !== 'card') return;
    undo = null; g.el.classList.add('is-lifted');
    board.querySelectorAll('.col').forEach(c => c.classList.add('drop-candidate'));
    if (!g.keyboard) {
      const ghost = document.createElement('div'); ghost.className = 'prfkt-drag-ghost'; ghost.setAttribute('aria-hidden', 'true');
      ghost.append(document.createTextNode(g.title)); g.label = document.createElement('small'); ghost.append(g.label);
      document.body.append(ghost); g.ghost = ghost;
    }
    announce('Moving “' + g.title + '”. Escape cancels.'); setTarget(g.from);
  }
  function cleanup() {
    const g = gesture; gesture = null; cancelAnimationFrame(frame); frame = 0;
    if (!g) return null;
    g.ghost?.remove(); g.el?.classList.remove('is-lifted');
    board.querySelectorAll('.col').forEach(c => c.classList.remove('drop-candidate', 'drop-target'));
    document.body.classList.remove('prfkt-grabbing');
    if (g.capture?.hasPointerCapture?.(g.pointerId)) g.capture.releasePointerCapture(g.pointerId);
    return g;
  }
  function cancel(text = 'Move cancelled.') {
    const g = cleanup(); if (!g) return;
    if (g.active && g.kind === 'card') { announce(text); focusCard(g.id); }
  }
  function hitTest() {
    const g = gesture; if (!g || g.kind !== 'card' || g.keyboard || !g.active) return;
    g.ghost.style.transform = 'translate(' + Math.min(innerWidth-g.ghost.offsetWidth-6, Math.max(4, g.x+14)) + 'px,' + Math.max(4,g.y+12) + 'px)';
    const col = document.elementFromPoint(g.x, g.y)?.closest('#boardGrid > .col');
    setTarget(col?.dataset.dropStatus || null);
  }
  function autoScroll() {
    const g = gesture; if (!g || !g.active || g.kind !== 'card' || g.keyboard) return;
    const scroller = board.parentElement, box = scroller.getBoundingClientRect();
    if (g.y >= box.top && g.y <= box.bottom) {
      const left = Math.max(0,box.left), right = Math.min(innerWidth,box.right);
      if (g.x > right-54) scroller.scrollLeft += 12;
      else if (g.x < left+54) scroller.scrollLeft -= 12;
    }
    if (g.y > innerHeight-70) window.scrollBy(0,10);
    else if (g.y < 70) window.scrollBy(0,-10);
    hitTest(); frame = requestAnimationFrame(autoScroll);
  }
  document.addEventListener('pointerdown', e => {
    suppressClickUntil = 0;
    if (!e.isPrimary || e.button !== 0 || gesture || saving) return;
    const target = e.target, el = target.closest('#boardGrid .task'), grip = target.closest('.task-grip');
    if (el) {
      if (!editable() || (e.pointerType !== 'mouse' && !grip)) return;
      if (!grip && target.closest('button:not(.task-title),select,input,a,textarea')) return;
      const t = tasks.find(t => String(t.id) === el.dataset.taskId); if (!t) return;
      gesture = {kind:'card',id:t.id,title:t.title,from:t.status,el,actor:actor(),capture:el,pointerId:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY};
    } else {
      const scroller = target.closest('.boardscroll,.home-scopes');
      if (!scroller || e.pointerType !== 'mouse' || (target.closest('button,input,select,a,textarea') && !scroller.matches('.home-scopes'))) return;
      if (scroller.scrollWidth <= scroller.clientWidth) return;
      gesture = {kind:'pan',el:scroller,capture:scroller,pointerId:e.pointerId,startX:e.clientX,startY:e.clientY,startScroll:scroller.scrollLeft};
    }
    // Capture only once movement crosses the drag threshold; a normal click stays a click.
  });
  document.addEventListener('pointermove', e => {
    const g = gesture; if (!g || g.keyboard || g.pointerId !== e.pointerId) return;
    g.x = e.clientX; g.y = e.clientY;
    if (!g.active && Math.hypot(g.x-g.startX,g.y-g.startY) >= 7) {
      g.capture.setPointerCapture(e.pointerId); activate();
      if (g.kind === 'card') frame = requestAnimationFrame(autoScroll);
    }
    if (!g.active) return;
    e.preventDefault();
    if (g.kind === 'pan') g.el.scrollLeft = g.startScroll-(g.x-g.startX); else hitTest();
  }, {passive:false});
  document.addEventListener('pointerup', e => {
    const g = gesture; if (!g || g.keyboard || g.pointerId !== e.pointerId) return;
    if (g.active) { e.preventDefault(); suppressClickUntil = performance.now()+400; hitTest(); }
    cleanup();
    if (g.kind === 'card' && g.active) {
      if (g.target && g.target !== g.from && g.actor === actor()) saveMove(g.id,g.from,g.target,g.title);
      else { announce('Move cancelled.'); focusCard(g.id); }
    }
  });
  document.addEventListener('pointercancel', () => cancel());
  document.addEventListener('lostpointercapture', e => { if (gesture?.pointerId === e.pointerId && gesture.capture === e.target) cancel(); });
  document.addEventListener('click', e => {
    if (e.detail > 0 && performance.now() < suppressClickUntil) { suppressClickUntil = 0; e.preventDefault(); e.stopImmediatePropagation(); }
    else if (gesture?.keyboard && !e.target.closest('.task-grip')) cancel();
  }, true);
  document.addEventListener('dragstart', e => { if (e.target.closest('#boardGrid .task')) e.preventDefault(); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && gesture) { e.preventDefault(); cancel(); return; }
    const grip = e.target.closest('.task-grip');
    if (!gesture && grip && editable() && !saving && [' ', 'Enter'].includes(e.key)) {
      e.preventDefault(); const el = grip.closest('.task'), t = tasks.find(t => String(t.id) === el.dataset.taskId); if (!t) return;
      gesture = {kind:'card',keyboard:true,id:t.id,title:t.title,from:t.status,actor:actor(),el}; activate(); return;
    }
    const g = gesture; if (!g?.keyboard) return;
    if (e.key === 'Tab') { cancel(); return; }
    if (['ArrowLeft','ArrowRight','Home','End'].includes(e.key)) {
      e.preventDefault(); const current = S.indexOf(g.target), index = e.key === 'Home' ? 0 : e.key === 'End' ? S.length-1 : Math.max(0,Math.min(S.length-1,current+(e.key === 'ArrowRight' ? 1 : -1)));
      setTarget(S[index]); board.querySelector('[data-drop-status="'+CSS.escape(S[index])+'"]').scrollIntoView({block:'nearest',inline:'nearest',behavior:'instant'});
    } else if ([' ', 'Enter'].includes(e.key)) {
      e.preventDefault(); cleanup();
      if (g.target !== g.from && g.actor === actor()) saveMove(g.id,g.from,g.target,g.title); else announce('Move cancelled.');
    }
  });
  window.addEventListener('blur', () => cancel());
  window.addEventListener('hashchange', () => cancel());
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancel(); });
  renderBoard();
})();
