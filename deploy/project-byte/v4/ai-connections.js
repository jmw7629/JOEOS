// Provider setup extends the existing model hub; credentials never enter browser storage.
(() => {
  'use strict';
  const host = document.querySelector('#models');
  if (!host || window.__PROJECT_BYTE_AI_CONNECTIONS__) return;
  window.__PROJECT_BYTE_AI_CONNECTIONS__ = true;
  const el = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let blockedIdentity = null;
  const owner = () => who() !== blockedIdentity && !!accessKey && session?.ok && session?.level === 4;
  const who = () => JSON.stringify([accessKey, session?.subject, session?.level, session?.ok]);
  const active = () => host.classList.contains('active') && !document.hidden;
  let identity = '', epoch = 0, snapshot = null, loading = false, working = false, last = 0, feedback = '', returnFocus = null;
  const controllers = new Set();
  const style = document.createElement('style');
  style.textContent = `
    .ai-connections{margin:0 0 18px;padding:18px;border:1px solid var(--line);border-radius:14px;background:linear-gradient(120deg,rgba(28,83,124,.17),var(--panel));min-width:0}
    .ai-connections-head,.ai-connections-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.ai-connections-head>div{flex:1;min-width:180px}.ai-connections h3{margin:0 0 4px}.ai-connections p{margin:10px 0 0}.ai-connections .small{overflow-wrap:anywhere}.ai-connections-actions{margin-top:12px}.ai-connections button,.ai-connection-dialog button{min-height:44px}
    .ai-connection-dialog{width:min(720px,100%);max-height:calc(100dvh - 32px);overflow:auto}.ai-connection-dialog h2{margin:0}.ai-connection-dialog .ai-dialog-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.ai-connection-dialog .ai-connection-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}.ai-connection-dialog .wide{grid-column:1/-1}.ai-connection-dialog label{min-width:0}.ai-connection-dialog input,.ai-connection-dialog select{width:100%;min-width:0}.ai-connection-dialog p,.ai-connection-dialog a{overflow-wrap:anywhere}.ai-connection-dialog [hidden]{display:none!important}.ai-connection-note{padding:12px;border:1px solid var(--line);border-radius:9px;background:rgba(10,26,42,.6);margin:12px 0}.ai-connection-note p{margin:6px 0 0}.ai-connection-status{min-height:22px;margin-top:12px;overflow-wrap:anywhere}.ai-connection-check{display:flex;gap:10px;align-items:flex-start;margin-top:14px}.ai-connection-check input{width:auto;margin-top:4px}.ai-connection-dialog .row{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}.ai-model-default{margin:10px 0 0}.ai-device-code{font-size:22px;letter-spacing:.12em;user-select:all}.ai-connection-dialog fieldset{border:0;margin:0;padding:0;min-width:0}.ai-connection-dialog fieldset:disabled{opacity:.7}
    @media(max-width:540px){.ai-connection-dialog .ai-connection-grid{grid-template-columns:1fr}.ai-connection-dialog .wide{grid-column:auto}.ai-connection-dialog{padding:16px!important}.ai-connections{padding:14px}.ai-connections-head>div{min-width:0;flex-basis:100%}}
  `;
  document.head.appendChild(style);
  const summary = document.createElement('section');
  summary.className = 'ai-connections'; summary.id = 'aiConnections'; summary.setAttribute('aria-label', 'AI connections');
  host.insertBefore(summary, el('modelGrid'));
  const dialog = document.createElement('div');
  dialog.id = 'aiConnectionModal'; dialog.className = 'modalwrap';
  dialog.innerHTML = `<form id="aiConnectionForm" class="modal ai-connection-dialog" role="dialog" aria-modal="true" aria-labelledby="aiConnectionTitle" tabindex="-1">
    <div class="ai-dialog-head"><h2 id="aiConnectionTitle">Connect your AI</h2><button type="button" class="btn" id="aiConnectionClose" aria-label="Close AI connections">Close</button></div>
    <p class="small">Private owner connections · use your existing Codex sign-in, connect a provider, or add your own model and agent.</p>
    <fieldset id="aiConnectionFields"><div class="ai-connection-grid">
      <label class="label wide">Provider<select id="aiConnectionProvider" required></select></label>
      <label class="label wide" id="aiConnectionAuthLabel">Connection method<select id="aiConnectionAuth"></select></label>
    </div>
    <div id="aiConnectionProviderNote" class="ai-connection-note small"></div>
    <details id="aiConnectionSubscriptions" class="ai-connection-note small" hidden><summary>Other subscription agents</summary><div id="aiConnectionSubscriptionList"></div></details>
    <div id="aiConnectionCodex" class="ai-connection-note" hidden><b id="aiConnectionCodexStatus"></b><p id="aiConnectionCodexNote" class="small"></p><div class="ai-connections-actions"><button id="aiConnectionLogin" class="btn" type="button">Sign in with ChatGPT</button><button id="aiConnectionLoginRefresh" class="btn" type="button">Check sign-in</button></div><div id="aiConnectionDevice" hidden></div></div>
    <div class="ai-connection-grid">
      <label class="label wide" id="aiConnectionEndpointLabel">Endpoint<input id="aiConnectionEndpoint" type="url" placeholder="https://your-provider.example/v1" autocomplete="off" maxlength="600"></label>
      <label class="label wide" id="aiConnectionSecretLabel">API key<input id="aiConnectionSecret" type="password" autocomplete="new-password" placeholder="Stored only on this server" maxlength="8192"></label>
      <label class="label wide" id="aiConnectionEnvLabel">Server environment variable<input id="aiConnectionEnv" autocomplete="off" placeholder="PROJECT_BYTE_AI_PROVIDER_KEY" maxlength="128"></label>
      <div class="wide ai-connections-actions" id="aiConnectionDiscoverRow"><button id="aiConnectionDiscover" class="btn" type="button">Detect available models</button><span id="aiConnectionDiscoveryStatus" class="small" role="status"></span></div>
      <label class="label wide" id="aiConnectionDetectedLabel" hidden>Available models<select id="aiConnectionDetected"><option value="">Choose a detected model</option></select></label>
      <label class="label wide">Model ID<input id="aiConnectionModel" required aria-describedby="aiConnectionModelHint" autocomplete="off" placeholder="Choose above or enter any supported model ID" maxlength="140"><span id="aiConnectionModelHint" class="small">A model missing from the list can be added by its exact ID.</span></label>
      <label class="label">Display name<input id="aiConnectionName" autocomplete="off" placeholder="Optional friendly name" maxlength="120"></label>
      <label class="label">Agent<select id="aiConnectionAgent"><option value="">Use existing agents</option></select></label>
    </div>
    <p id="aiConnectionAgentNote" class="small"></p>
    <label class="ai-connection-check"><input id="aiConnectionDefault" type="checkbox" checked><span>Use this as my default AI<span class="small" style="display:block">Existing work keeps its selected model.</span></span></label>
    </fieldset>
    <div id="aiConnectionFeedback" class="ai-connection-status small" role="status" aria-live="polite"></div>
    <div class="row"><button id="aiConnectionCancel" class="btn" type="button">Cancel</button><button id="aiConnectionSave" class="btn primary" type="submit">Connect model</button></div>
  </form>`;
  document.body.appendChild(dialog);

  function same(current, turn) { return current === who() && turn === epoch && owner(); }
  function clear() {
    epoch++; for (const c of controllers) c.abort(); controllers.clear();
    snapshot = null; loading = false; working = false; feedback = ''; last = 0;
    el('aiConnectionForm').reset();
    for(const id of ['aiConnectionSecret','aiConnectionEnv','aiConnectionEndpoint','aiConnectionModel','aiConnectionName'])el(id).value='';
    for(const id of ['aiConnectionProvider','aiConnectionAuth','aiConnectionDetected','aiConnectionAgent','aiConnectionProviderNote','aiConnectionAgentNote','aiConnectionDiscoveryStatus','aiConnectionSubscriptionList'])el(id).replaceChildren();
    el('aiConnectionDevice').replaceChildren(); el('aiConnectionDevice').hidden = true;
    el('aiConnectionFeedback').textContent = '';
    close(false);
  }
  async function request(path, body, current, turn) {
    const controller = new AbortController(); controllers.add(controller);
    try {
      const data = await api(path, {signal:controller.signal, ...(body === undefined ? {} : {method:'POST', body:JSON.stringify(body)})});
      return same(current, turn) ? data : null;
    } finally { controllers.delete(controller); }
  }
  function codex() { return snapshot?.codex || snapshot?.status?.codex || {}; }
  function codexConnected() { const c=codex(); return c.state === 'connected' || c.connected === true || c.authenticated === true; }
  function defaultKey() { return snapshot?.default_model_key || snapshot?.default_model || settings?.ai?.default_model || ''; }
  function provider() { return (snapshot?.providers || []).find(p => p.id === el('aiConnectionProvider').value); }
  function isCodex(p=provider()) { return /codex/.test(p?.id || ''); }
  function authId(mode) { return typeof mode === 'string' ? mode : mode.id; }
  function authLabel(mode) {
    if (typeof mode === 'object') return mode.label || mode.id;
    return ({subscription:'Subscription / existing sign-in', api_key:'API key', api:'API key', server_env:'Server environment variable', local:'Local server', none:'No API key', chatgpt:'ChatGPT subscription', oauth:'Official account sign-in', external:'External agent connection'})[mode] || mode.replaceAll('_',' ');
  }
  function renderSummary() {
    const add = el('addModel');
    if (add) { add.textContent = '+ Connect AI'; add.hidden = !owner(); add.style.display = owner() ? '' : 'none'; }
    if (!owner()) {
      summary.innerHTML = '<h3>AI connections</h3><p class="small">The workspace owner manages provider sign-ins, model connections and the default AI. Your existing workspace access stays the same.</p>';
      for (const node of host.querySelectorAll('.ai-model-default')) node.remove();
      return;
    }
    const selected = models.find(m=>m.model_key===defaultKey());
    const label = codexConnected() ? 'Codex · ChatGPT subscription connected' : snapshot ? codex().available===false ? 'Codex · unavailable on this server' : 'Codex · sign-in required' : 'Checking AI connections';
    const description = codexConnected() ? 'Your existing ChatGPT subscription is available through Codex.' : snapshot ? (codex().detail || codex().message || 'Use the official ChatGPT sign-in to connect Codex on this server.') : 'Loading provider availability and sign-in status.';
    summary.innerHTML = `<div class="ai-connections-head"><div><h3>${escape(label)}</h3><div class="small">${escape(description)}</div></div><span class="pill">${escape(selected ? 'Default: '+selected.display_name : defaultKey() ? 'Default: '+defaultKey() : 'Choose your default AI')}</span></div><p class="small">Connect cloud providers now. When your workstation is ready, choose Ollama and detect the models installed there.</p><div class="ai-connections-actions"><button class="btn primary" data-ai-open ${loading?'disabled':''}>Manage connections</button><button class="btn" data-ai-refresh ${loading?'disabled':''}>Refresh status</button></div><p class="small" role="status">${escape(feedback)}</p>`;
    decorateModels();
  }
  function decorateModels() {
    for (const card of host.querySelectorAll('#modelGrid .modelcard')) {
      card.querySelector('.ai-model-default')?.remove();
      const key = card.querySelector('[data-test-model]')?.dataset.testModel;
      if (!key || !owner()) continue;
      const box = document.createElement('div'); box.className='ai-model-default';
      const button = document.createElement('button'); button.type='button'; button.className='mini';
      button.dataset.aiDefault=key; button.textContent=key===defaultKey()?'Default AI':'Use as default';
      button.disabled=working||key===defaultKey(); box.appendChild(button); card.appendChild(box);
    }
  }
  function renderCodex() {
    const connected=codexConnected(), c=codex();
    el('aiConnectionCodexStatus').textContent=connected?'Connected to your ChatGPT subscription':'ChatGPT sign-in required';
    el('aiConnectionCodexNote').textContent=c.detail || c.message || (connected?'Codex uses the account already signed in on this server.':'Complete the official sign-in once to authorize this server. Subscription credentials are never pasted here.');
    el('aiConnectionLogin').hidden=connected;
    el('aiConnectionLogin').disabled=c.available===false;
    if(c.login?.state==='pending')displayDevice(c.login);
    if(connected){el('aiConnectionDevice').replaceChildren();el('aiConnectionDevice').hidden=true;}
    if (c.available===false || c.state==='unavailable' || c.state==='not_installed') el('aiConnectionCodexNote').textContent=c.detail || c.message || 'Codex is not available on this server yet.';
  }
  async function refresh(force=false) {
    const current=who();
    if (identity!==current) { clear(); identity=current; renderSummary(); }
    if (!owner() || loading || (!force&&(!active()||Date.now()-last<15000))) return;
    loading=true; const turn=epoch; renderSummary();
    try {
      const data=await request('/api/ai-connections',undefined,current,turn); if(!data)return;
      snapshot=data; feedback='';
      if (dialog.classList.contains('open')) renderCodex();
    } catch(error) { if(same(current,turn))feedback=error.message || 'AI connections could not be loaded.'; }
    finally { if(same(current,turn)){loading=false;last=Date.now();renderSummary();} }
  }
  function setBusy(value) {
    working=value; el('aiConnectionFields').disabled=value; el('aiConnectionSave').disabled=value; decorateModels();
  }
  function setProvider() {
    const p=provider(); if(!p)return;
    el('aiConnectionSecret').value=''; el('aiConnectionEnv').value=''; el('aiConnectionModel').value=''; el('aiConnectionName').value='';
    el('aiConnectionEndpoint').value=p.default_endpoint||'';
    el('aiConnectionDetected').innerHTML='<option value="">Choose a detected model</option>';
    el('aiConnectionDetectedLabel').hidden=true; el('aiConnectionDiscoveryStatus').textContent='';
    el('aiConnectionFeedback').textContent=''; el('aiConnectionDevice').replaceChildren(); el('aiConnectionDevice').hidden=true;
    const modes=p.auth_modes?.length?p.auth_modes:[isCodex(p)?'subscription':p.id==='ollama'?'none':'api_key'];
    el('aiConnectionAuth').innerHTML=modes.map(mode=>`<option value="${escape(authId(mode))}">${escape(authLabel(mode))}</option>`).join('');
    el('aiConnectionAuthLabel').hidden=modes.length<2;
    el('aiConnectionProviderNote').textContent=(p.notes||'')+(isCodex(p)?' Available choices are verified for workspace chat.':'');
    el('aiConnectionCodex').hidden=!isCodex(p); el('aiConnectionEndpointLabel').hidden=isCodex(p);
    el('aiConnectionDiscoverRow').hidden=p.discovery===false;
    el('aiConnectionDiscover').textContent=p.id==='ollama'?'Detect installed models':['hermes','openclaw'].includes(p.id)?'Detect agent targets':'Detect available models';
    el('aiConnectionModel').readOnly=isCodex(p);
    el('aiConnectionModelHint').textContent=isCodex(p)?'Choose a detected model verified for workspace chat.':'A model missing from the list can be added by its exact ID.';
    el('aiConnectionModel').placeholder=isCodex(p)?'Choose a detected model':p.id==='ollama'?'Choose an installed model or enter its exact name':'Choose above or enter any supported model ID';
    el('aiConnectionDefault').checked=true;
    const matchingAgent=(snapshot?.agents||[]).find(a=>a.provider===p.id);el('aiConnectionAgent').value=matchingAgent?.id||'';
    renderCodex(); setAuth(); setAgent();
  }
  function setAuth() {
    const p=provider(), mode=el('aiConnectionAuth').value;
    const requiresKey=!isCodex(p)&&!['none','local','subscription','chatgpt','oauth'].includes(mode);
    el('aiConnectionSecretLabel').hidden=!requiresKey||mode==='server_env'; el('aiConnectionEnvLabel').hidden=!requiresKey||mode!=='server_env';
    if(el('aiConnectionSecretLabel').hidden)el('aiConnectionSecret').value='';
    if(el('aiConnectionEnvLabel').hidden)el('aiConnectionEnv').value='';
  }
  function setAgent() {
    const template=(snapshot?.agents||[]).find(a=>a.id===el('aiConnectionAgent').value);
    if(template&&template.provider!==el('aiConnectionProvider').value&&(snapshot?.providers||[]).some(p=>p.id===template.provider)){el('aiConnectionProvider').value=template.provider;setProvider();return;}
    el('aiConnectionAgentNote').textContent=template?.notes || 'Models can be used with the agents already in your workspace.';
  }
  async function open() {
    if(!owner()||working)return;
    if(!snapshot){await refresh(true);if(!snapshot||!owner())return;}
    returnFocus=document.activeElement;
    el('aiConnectionForm').reset(); setBusy(false);
    const providers=snapshot.providers||[];
    el('aiConnectionProvider').innerHTML=providers.map(p=>`<option value="${escape(p.id)}">${escape(p.label)}</option>`).join('');
    const preferred=providers.find(p=>/codex/.test(p.id)); if(preferred)el('aiConnectionProvider').value=preferred.id;
    el('aiConnectionAgent').innerHTML='<option value="">Use existing agents</option>'+(snapshot.agents||[]).map(a=>`<option value="${escape(a.id)}">${escape(a.label)}</option>`).join('');
    const subscriptions=snapshot.subscription_runtimes||[];
    el('aiConnectionSubscriptions').hidden=!subscriptions.length;el('aiConnectionSubscriptions').open=false;
    el('aiConnectionSubscriptionList').innerHTML=subscriptions.map(runtime=>`<p><b>${escape(runtime.label)}</b> · Native adapter not connected<br>${escape(runtime.notes||'Requires the provider’s official sign-in and a supported adapter on this server.')}</p>`).join('');
    setProvider(); dialog.classList.add('open'); el('aiConnectionProvider').focus();
  }
  function close(restore=true) {
    dialog.classList.remove('open'); el('aiConnectionSecret').value='';
    el('aiConnectionDevice').replaceChildren(); el('aiConnectionDevice').hidden=true;
    if(restore&&returnFocus?.isConnected)returnFocus.focus(); returnFocus=null;
  }
  function payload() {
    const p=provider();
    const data={provider:p?.id,endpoint:el('aiConnectionEndpoint').value.trim(),model_name:el('aiConnectionModel').value.trim(),display_name:el('aiConnectionName').value.trim(),set_default:el('aiConnectionDefault').checked};
    if(el('aiConnectionSecret').value)data.api_key=el('aiConnectionSecret').value;
    if(el('aiConnectionEnv').value.trim())data.secret_env=el('aiConnectionEnv').value.trim();
    if(el('aiConnectionAgent').value)data.agent_template=el('aiConnectionAgent').value;
    return data;
  }
  async function discover() {
    if(!owner()||working)return;
    const current=who(),turn=epoch,data=payload();
    delete data.model_name;delete data.display_name;delete data.set_default;delete data.agent_template;
    setBusy(true);el('aiConnectionDiscoveryStatus').textContent='Checking the provider…';
    try {
      const found=await request('/api/ai-connections/discover',data,current,turn);if(!found)return;
      const items=found.models||[];
      el('aiConnectionDetected').innerHTML='<option value="">Choose a detected model</option>'+items.map(m=>`<option value="${escape(m.id)}">${escape(m.name||m.id)}</option>`).join('');
      el('aiConnectionDetectedLabel').hidden=!items.length;
      el('aiConnectionDiscoveryStatus').textContent=items.length?`${items.length} model${items.length===1?'':'s'} available`:'No models returned. Enter a model ID manually.';
      if(items.length===1){el('aiConnectionDetected').value=items[0].id;el('aiConnectionModel').value=items[0].id;}
    } catch(error){if(same(current,turn))el('aiConnectionDiscoveryStatus').textContent=error.message;}
    finally{if(same(current,turn))setBusy(false);}
  }
  async function save(event) {
    event.preventDefault();if(!owner()||working)return;
    const current=who(),turn=epoch,data=payload();
    setBusy(true);el('aiConnectionFeedback').textContent='Connecting model…';
    try {
      const result=await request('/api/ai-connections/connect',data,current,turn);if(!result)return;
      el('aiConnectionSecret').value='';
      feedback=`${result.display_name||result.model_key} connection saved${result.default_set?' and selected as your default AI':''}. Use Test connection to verify a response.`;
      if(result.default_set){settings.ai={...(settings.ai||{}),default_model:result.model_key};if(snapshot)snapshot.default_model_key=result.model_key;}
      const modelData=await request('/api/models',undefined,current,turn);if(!modelData)return;
      models=modelData.models||[];
      if(result.agent_key){const agentData=await request('/api/agents',undefined,current,turn);if(!agentData)return;agents=agentData.agents||[];}
      syncSelectors();applySettingsForm();renderAll();close();renderSummary();
    }catch(error){if(same(current,turn))el('aiConnectionFeedback').textContent=error.message;}
    finally{if(same(current,turn))setBusy(false);}
  }
  async function chooseDefault(key) {
    if(!owner()||working)return;
    const current=who(),turn=epoch;working=true;renderSummary();
    try{
      const result=await request('/api/ai-connections/default',{model_key:key},current,turn);if(!result)return;
      settings.ai={...(settings.ai||{}),default_model:key};if(snapshot)snapshot.default_model_key=key;
      applySettingsForm();el('aiModel').value=key;feedback='Default AI updated.';
    }catch(error){if(same(current,turn))feedback=error.message;}
    finally{if(same(current,turn)){working=false;renderSummary();}}
  }
  function displayDevice(data) {
    const device=el('aiConnectionDevice');device.replaceChildren();
    const url=data.verification_url||data.verification_uri||data.login_url||data.url;
    const code=data.user_code||data.code;
    // Only official HTTPS sign-in links are offered, even if an upstream error is malformed.
    let safe;try{const parsed=new URL(url);if(parsed.protocol==='https:'&&['auth.openai.com','chatgpt.com'].includes(parsed.hostname))safe=parsed.href;}catch{}
    if(safe){const a=document.createElement('a');a.href=safe;a.target='_blank';a.rel='noopener noreferrer';a.className='btn';a.textContent='Open official ChatGPT sign-in';device.appendChild(a);}
    if(code){const p=document.createElement('p');p.className='ai-device-code';p.textContent=code;device.appendChild(p);}
    const note=document.createElement('p');note.className='small';note.textContent=data.message||(safe?'Complete sign-in in the new tab, then choose Check sign-in.':'Check sign-in again after authorizing this server.');device.appendChild(note);device.hidden=false;
  }
  async function login() {
    if(!owner()||working)return;
    const current=who(),turn=epoch;setBusy(true);el('aiConnectionFeedback').textContent='Preparing official ChatGPT sign-in…';
    try{const data=await request('/api/ai-connections/login',{},current,turn);if(!data)return;if(data.codex&&snapshot)snapshot.codex=data.codex;displayDevice(data.login||data);renderCodex();el('aiConnectionFeedback').textContent='';}
    catch(error){if(same(current,turn))el('aiConnectionFeedback').textContent=error.message;}
    finally{if(same(current,turn))setBusy(false);}
  }
  el('aiConnectionProvider').addEventListener('change',setProvider);
  el('aiConnectionAuth').addEventListener('change',setAuth);
  el('aiConnectionAgent').addEventListener('change',setAgent);
  el('aiConnectionDetected').addEventListener('change',()=>{if(el('aiConnectionDetected').value)el('aiConnectionModel').value=el('aiConnectionDetected').value;});
  el('aiConnectionDiscover').addEventListener('click',discover);
  el('aiConnectionForm').addEventListener('submit',save);
  el('aiConnectionLogin').addEventListener('click',login);
  el('aiConnectionLoginRefresh').addEventListener('click',()=>void refresh(true));
  el('aiConnectionClose').addEventListener('click',()=>close());el('aiConnectionCancel').addEventListener('click',()=>close());
  dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
  dialog.addEventListener('keydown',event=>{
    if(event.key==='Escape'){event.preventDefault();close();return;}
    if(event.key!=='Tab')return;
    const controls=[...dialog.querySelectorAll('button,input,select,a[href]')].filter(n=>!n.disabled&&n.getClientRects().length);
    const first=controls[0],lastControl=controls.at(-1);
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();lastControl?.focus();}
    else if(!event.shiftKey&&document.activeElement===lastControl){event.preventDefault();first?.focus();}
  });
  document.addEventListener('click',event=>{
    if(!(event.target instanceof Element))return;
    if(event.target.closest('#login,#logoutAllLocal')){blockedIdentity=who();clear();identity='';renderSummary();setTimeout(()=>void refresh(),0);return;}
    if(event.target.closest('#addModel,[data-ai-open]')){event.preventDefault();event.stopImmediatePropagation();void open();return;}
    if(event.target.closest('[data-ai-refresh]')){void refresh(true);return;}
    const defaultButton=event.target.closest('[data-ai-default]');if(defaultButton){void chooseDefault(defaultButton.dataset.aiDefault);return;}
    if(event.target.closest('[data-view="models"],[data-home-go="models"]'))setTimeout(()=>void refresh(),0);
  },true);
  const originalRender=renderModels;
  renderModels=function(){originalRender();renderSummary();};
  window.addEventListener('project-byte-home-render',()=>void refresh());
  window.addEventListener('hashchange',()=>void refresh());
  document.addEventListener('visibilitychange',()=>void refresh());
  setInterval(()=>void refresh(),1000);
  renderSummary();setTimeout(()=>void refresh(),0);
})();
