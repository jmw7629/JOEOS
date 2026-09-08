// Native Codex workspace controls. Every execution update comes from the owner API.
(() => {
  'use strict';
  const host = document.querySelector('#ai'), box = host?.querySelector('.chatbox');
  if (!box || window.PRFKT_CODEX) return;
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const text = value => typeof value === 'string' ? value : '';
  const activeStates = new Set(['queued','working','waiting_approval','stopping']);
  const labels = {queued:'Queued',working:'Working',waiting_approval:'Waiting for approval',completed:'Completed',failed:'Failed',stopping:'Stopping',stopped:'Stopped',interrupted:'Interrupted'};
  const legacy = {send:typeof sendChat === 'function' ? sendChat : null, load:typeof loadChat === 'function' ? loadChat : null};
  const subtitle = box.querySelector('.between > div > .small');
  const originalCopy = {subtitle:subtitle?.textContent || '',placeholder:$('chatInput').placeholder,reload:$('loadChat').textContent};
  const sidebar = host.querySelector('.ai-layout > aside');
  const sidebarChildren = sidebar ? [...sidebar.children] : [];
  const panel = document.createElement('section'); panel.id = 'codexWorkspacePanel'; panel.hidden = true;
  panel.innerHTML = `<div class="cw-heading"><h2>Your work</h2><button type="button" class="mini" id="codexRefresh">Refresh</button></div>
    <p id="codexConnection" class="small" role="status"></p>
    <label class="label">Project<select id="codexProject" class="field"></select></label>
    <label class="label">Conversation<select id="codexConversation" class="field"><option value="">New conversation</option></select></label>
    <button type="button" class="mini" id="codexNewConversation">New conversation</button>
    <div class="cw-run"><div class="cw-heading"><b id="codexRunState" role="status">Ready</b><button type="button" id="codexStop" class="btn" hidden>Stop</button></div><p id="codexRoute" class="small"></p></div>
    <p id="codexFeedback" class="cw-feedback small" role="status" aria-live="polite"></p>
    <div id="codexPermissions"></div>
    <details id="codexAgentDetails"><summary>Agents and tools</summary><div id="codexAgents" class="small"></div><div id="codexTools" class="small"></div></details>
    <div id="codexArtifacts"></div>
    <p class="small cw-policy">Describe the outcome. Codex chooses the agents and tools, and brings progress, permission requests and results here. Pull requests remain open for review; nothing merges automatically.</p>`;
  (sidebar || box).appendChild(panel);
  const progress = document.createElement('div'); progress.id = 'codexConversationStatus'; progress.className = 'small cw-conversation-status'; progress.hidden = true; progress.setAttribute('role','status');
  $('chatlog').after(progress);
  const style = document.createElement('style');
  style.textContent = `
    #ai.cw-enabled [hidden]{display:none!important}#codexWorkspacePanel{min-width:0}#codexWorkspacePanel .label{display:block;margin:12px 0 9px}#codexWorkspacePanel select{display:block;width:100%;min-width:0;margin-top:5px}#codexWorkspacePanel button{min-height:44px}.cw-heading{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.cw-run{border-top:1px solid var(--line);margin-top:16px;padding-top:14px}.cw-run p{margin:7px 0}.cw-feedback{color:var(--warn);white-space:pre-wrap}.cw-policy{margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}#codexWorkspacePanel p,#codexWorkspacePanel b,#codexWorkspacePanel a,.cw-conversation-status{overflow-wrap:anywhere}#codexAgentDetails summary{cursor:pointer;padding:10px 0;min-height:44px}#codexAgents>div,#codexTools>div{border-top:1px solid var(--line);padding:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}.cw-permission{padding:12px;margin:12px 0;border:1px solid #536888;background:var(--card);border-radius:10px}.cw-permission h3{margin:0;font-size:14px}.cw-permission p{white-space:pre-wrap}.cw-permission pre{max-height:180px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}.cw-permission textarea{width:100%;min-height:65px;margin:8px 0;resize:vertical}.cw-artifact{display:block;padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}.cw-conversation-status{min-height:19px;margin-top:7px}#ai.cw-enabled #chatlog .msg{overflow-wrap:anywhere}#ai.cw-enabled .cw-stream:empty{display:none}#ai.cw-enabled #chatlog{scroll-behavior:auto}#ai.cw-enabled #chatInput{min-width:0}#ai.cw-enabled .composer{min-width:0}#ai.cw-enabled .chatbox>.between>div{min-width:0}
    @media(max-width:650px){#ai.cw-enabled #chatlog{min-height:180px;max-height:34dvh}#ai.cw-enabled .chatbox{min-height:0}.cw-conversation-status{font-size:11px}#codexWorkspacePanel{padding-bottom:5px}}
  `;
  document.head.appendChild(style);
  let identity = '', blockedIdentity = null, epoch = 0, catalog = null, catalogAt = 0, catalogBusy = false;
  let conversationId = '', projectKey = '', messages = [], run = null, seq = 0, stream = '', tools = [], agents = [], artifacts = [], permissions = [];
  let loading = false, sending = false, polling = false, stopBusy = false, feedback = '', lastPoll = 0, restored = false, pendingSend = null, pendingEvents = false;
  let messagesSignature = '', permissionSignature = '', agentSignature = '', toolSignature = '', artifactSignature = '';
  const controllers = new Set(), permissionNotes = new Map(), decisions = new Map(), stopRequests = new Map(), downloads = new Set(), downloadUrls = new Set();
  const who = () => JSON.stringify([accessKey,session?.subject,session?.level,session?.ok]);
  const owner = () => who() !== blockedIdentity && !!accessKey && session?.ok && session?.level === 4;
  const visible = () => !document.hidden && host.classList.contains('active');
  const same = (current, turn) => current === who() && turn === epoch && owner();
  const uuid = () => crypto.randomUUID();
  const expires = value => Number(value) > 1e12 ? Number(value) : Number(value) * 1000;
  const connected = () => catalog?.configured === true && catalog?.connected === true;
  const active = () => !!run && activeStates.has(run.status);
  const resetSignatures = () => {messagesSignature = permissionSignature = agentSignature = toolSignature = artifactSignature = '';};

  function resetContext(clearDraft = false) {
    epoch++; for (const controller of controllers) controller.abort(); controllers.clear();
    for (const url of downloadUrls) URL.revokeObjectURL(url); downloadUrls.clear(); downloads.clear();
    conversationId = ''; messages = []; run = null; seq = 0; stream = ''; tools = []; agents = []; artifacts = []; permissions = [];
    loading = sending = polling = stopBusy = catalogBusy = pendingEvents = false; feedback = ''; lastPoll = 0; pendingSend = null;
    permissionNotes.clear(); decisions.clear(); stopRequests.clear(); resetSignatures();
    if (clearDraft) $('chatInput').value = '';
    $('chatlog').replaceChildren();
    for (const id of ['codexPermissions','codexAgents','codexTools','codexArtifacts','codexFeedback','codexRoute','codexConnection','codexConversationStatus']) $(id).replaceChildren();
    $('codexConversation').innerHTML = '<option value="">New conversation</option>';
    $('codexProject').replaceChildren();
    $('codexRunState').textContent = 'Ready';
  }
  function synchronizeIdentity() {
    const current = who();
    if (identity !== current) {
      resetContext(true); identity = current; catalog = null; catalogAt = 0; projectKey = ''; restored = false;
      if (blockedIdentity !== current) blockedIdentity = null;
    }
    const wasEnabled = host.classList.contains('cw-enabled'), enabled = owner(); host.classList.toggle('cw-enabled', enabled); panel.hidden = progress.hidden = !enabled;
    for (const child of sidebarChildren) child.hidden = enabled;
    if ($('pbChatContext')) $('pbChatContext').hidden = enabled;
    if (enabled) {
      $('chatInput').placeholder = 'Describe what you want to accomplish…';
      if (subtitle) subtitle.textContent = 'Describe a task and follow its progress, approvals and results here.';
    } else if (wasEnabled) {
      $('chatInput').placeholder = originalCopy.placeholder; $('loadChat').textContent = originalCopy.reload;
      $('sendChat').disabled = $('loadChat').disabled = false;
      if (subtitle) subtitle.textContent = originalCopy.subtitle;
    }
    return enabled;
  }
  async function request(path, body, current, turn) {
    const controller = new AbortController(); controllers.add(controller);
    const timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const result = await api(path, {signal:controller.signal, ...(body === undefined ? {} : {method:'POST',body:JSON.stringify(body)})});
      return same(current, turn) ? result : null;
    } finally { clearTimeout(timeout); controllers.delete(controller); }
  }
  function projectOptions() {
    const list = Array.isArray(catalog?.projects) ? catalog.projects : [];
    const select = $('codexProject'), markup = list.map(p => `<option value="${escape(p.key)}">${escape(p.label || p.key)}</option>`).join('');
    if (select.innerHTML !== markup) select.innerHTML = markup || '<option value="">No execution project connected</option>';
    if (!list.some(p => p.key === projectKey)) projectKey = list.find(p => p.key === 'joeos')?.key || list[0]?.key || '';
    select.value = projectKey; select.disabled = sending || loading || active();
    const conversations = (catalog?.conversations || []).filter(c => c.project_key === projectKey);
    let options = '<option value="">New conversation</option>' + conversations.map(c => `<option value="${escape(c.id)}">${escape(c.title || 'Conversation')}</option>`).join('');
    if (conversationId && !conversations.some(c => c.id === conversationId)) options += `<option value="${escape(conversationId)}">Current conversation</option>`;
    if ($('codexConversation').innerHTML !== options) $('codexConversation').innerHTML = options;
    $('codexConversation').value = conversationId; $('codexConversation').disabled = sending || loading;
    $('codexNewConversation').disabled = sending || loading;
  }
  function renderMessages() {
    const signature = JSON.stringify([messages,stream]); if (signature === messagesSignature) return;
    messagesSignature = signature;
    const log = $('chatlog'), nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    const html = messages.map(m => `<div class="msg ${m.role === 'user' ? 'user' : 'assistant'}"><b>${m.role === 'user' ? 'You' : 'Codex'}</b><br>${escape(m.text)}</div>`).join('');
    log.innerHTML = html + (stream ? `<div class="msg assistant cw-stream"><b>Codex</b><br>${escape(stream)}</div>` : '');
    if (nearBottom || sending || loading) log.scrollTop = log.scrollHeight;
  }
  function renderPermissions() {
    const signature = JSON.stringify([permissions,[...decisions]]);
    if (signature !== permissionSignature) {
      permissionSignature = signature;
      $('codexPermissions').innerHTML = permissions.filter(p => p.state === 'pending').map(p => {
        const decision = decisions.get(p.id), expired = !Number.isFinite(expires(p.expires_at)) || expires(p.expires_at) <= Date.now();
        const enabled = !decision && !expired && !!p.review_token && active();
        let args = ''; try { args = JSON.stringify(p.arguments ?? {},null,2); } catch { args = 'Request details unavailable'; }
        return `<article class="cw-permission" data-codex-permission="${escape(p.id)}"><h3>${escape(p.tool || 'Tool permission')}</h3><p class="small">${escape(p.summary)}</p><details><summary>Requested action</summary><pre>${escape(args)}</pre></details><p class="small">${expired ? 'Review window expired. Refresh to inspect the current request.' : 'Review by '+escape(new Date(expires(p.expires_at)).toLocaleTimeString())}</p>${decision ? `<p class="small">${escape(decision.state === 'sending' ? 'Sending decision…' : decision.state === 'accepted' ? 'Decision accepted. Waiting for the tool result.' : 'Decision could not be confirmed. Refresh to inspect the request before taking further action.')}</p>` : `<label class="label small">Decision note (optional)<textarea class="field" data-codex-note="${escape(p.id)}" maxlength="1000" ${enabled?'':'disabled'}>${escape(permissionNotes.get(p.id)||'')}</textarea></label><div class="flex"><button type="button" class="btn primary" data-codex-decision="approve_once" data-permission-id="${escape(p.id)}" ${enabled?'':'disabled'}>Approve once</button><button type="button" class="btn" data-codex-decision="deny" data-permission-id="${escape(p.id)}" ${enabled?'':'disabled'}>Deny</button></div>`}</article>`;
      }).join('');
    }
    for (const p of permissions) if (expires(p.expires_at) <= Date.now()) for (const button of $('codexPermissions').querySelectorAll('[data-permission-id]')) if (button.dataset.permissionId === p.id) button.disabled = true;
  }
  function safeUrl(value) {
    if (typeof value !== 'string' || !value.trim()) return '';
    try { const url = new URL(value,location.origin); return ['http:','https:'].includes(url.protocol) && !url.username && !url.password ? url.href : ''; } catch { return ''; }
  }
  function renderEvidence() {
    const a = JSON.stringify(agents), t = JSON.stringify(tools), f = JSON.stringify([artifacts,[...downloads]]);
    if (a !== agentSignature) { agentSignature = a; $('codexAgents').innerHTML = agents.map(agent => `<div><b>${escape(agent.role || 'Agent')}</b> · ${escape(labels[agent.status] || agent.status || 'Recorded')}<br>${escape([agent.model,agent.effort].filter(Boolean).join(' · '))}</div>`).join(''); }
    if (t !== toolSignature) { toolSignature = t; $('codexTools').innerHTML = tools.slice(-30).map(tool => `<div>${escape(tool)}</div>`).join('') || '<p>Tool activity will appear when recorded.</p>'; }
    if (f !== artifactSignature) { artifactSignature = f; $('codexArtifacts').innerHTML = artifacts.map(artifact => {
      const url = safeUrl(artifact.url), parsed = url ? new URL(url) : null;
      const match = parsed?.origin === location.origin && !parsed.search && !parsed.hash && parsed.pathname.match(/^\/api\/codex-workspace\/artifacts\/([0-9a-f]{32})$/);
      if (match) return `<button type="button" class="mini cw-artifact" data-codex-download="${match[1]}" ${downloads.has(match[1])?'disabled':''}>${escape(downloads.has(match[1]) ? 'Preparing download…' : artifact.name || 'Download result')}</button>`;
      return url ? `<a class="cw-artifact" href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(artifact.name || 'Open result')}</a>` : `<span class="cw-artifact">${escape(artifact.name || 'Result')} · link unavailable</span>`;
    }).join(''); }
  }
  function render() {
    if (!synchronizeIdentity()) return;
    projectOptions();
    $('codexConnection').textContent = connected() ? 'Connected · agents selected automatically' : catalog?.configured ? 'Codex sign-in or runtime connection is unavailable.' : catalog ? 'The Codex workspace runtime has not been connected.' : 'Checking the Codex workspace…';
    $('codexRoute').textContent = [run?.model || catalog?.model, run?.effort || catalog?.effort].filter(Boolean).join(' · ');
    $('codexRunState').textContent = sending ? 'Sending…' : loading ? 'Loading conversation…' : labels[run?.status] || (run?.status ? 'Recorded state: '+run.status : 'Ready');
    $('codexFeedback').textContent = feedback;
    progress.textContent = run ? `${labels[run.status] || run.status}${active() ? ' · updates appear here' : ''}` : sending ? 'Submitting your request…' : 'Choose an outcome; Codex handles the routing.';
    $('codexStop').hidden = !active(); $('codexStop').disabled = stopBusy || run?.status === 'stopping';
    $('sendChat').disabled = !connected() || !projectKey || sending || loading || active();
    $('loadChat').textContent = 'Refresh conversation'; $('loadChat').disabled = loading;
    renderMessages(); renderPermissions(); renderEvidence();
  }
  async function refreshCatalog(force = false) {
    if (!synchronizeIdentity() || catalogBusy || (!force && Date.now()-catalogAt<15000)) return;
    catalogBusy = true; const current = who(), turn = epoch;
    try {
      const data = await request('/api/codex-workspace',undefined,current,turn); if (!data) return;
      catalog = data; catalogAt = Date.now(); render();
      if (!restored && !conversationId && !sending) {
        restored = true;
        const latest = (catalog.conversations || []).find(c => c.project_key === projectKey);
        if (latest) await selectConversation(latest.id);
      }
    } catch (error) { if (same(current,turn)) { feedback = 'Codex workspace unavailable: '+error.message; catalogAt = Date.now(); } }
    finally { if (same(current,turn)) { catalogBusy = false; render(); } }
  }
  async function selectConversation(id) {
    resetContext(); conversationId = id || ''; restored = true;
    if (!id) { render(); return; }
    loading = true; render(); const current = who(), turn = epoch;
    try {
      const data = await request('/api/codex-workspace/conversations/'+encodeURIComponent(id),undefined,current,turn); if (!data) return;
      conversationId = data.conversation?.id || id; projectKey = data.conversation?.project_key || projectKey;
      messages = (data.messages || []).map(m => ({id:m.id,role:m.role,text:text(m.text),run_id:m.run_id}));
      const runs = data.runs || []; run = [...runs].reverse().find(r => activeStates.has(r.status)) || runs[runs.length-1] || null;
      artifacts = data.artifacts || []; lastPoll = 0;
    } catch (error) { if (same(current,turn)) feedback = 'Could not load conversation: '+error.message; }
    finally { if (same(current,turn)) { loading = false; render(); void poll(true); } }
  }
  function acceptEvent(event) {
    const data = event.data || {};
    if (event.type === 'message_delta') stream = (stream + text(data.text)).slice(-250000);
    else if (event.type === 'message') {
      const content = text(data.text), role = data.role || 'assistant';
      if (role === 'assistant') stream = '';
      if (!messages.some(m => m.run_id === run?.id && m.role === role && m.text === content)) messages.push({id:'event:'+event.seq,run_id:run?.id,role,text:content});
    } else if (event.type === 'tool_started') tools.push((data.name || 'Tool')+(data.summary ? ' · '+data.summary : '')+' · started');
    else if (event.type === 'tool_output') tools.push(text(data.text).slice(-5000));
    else if (event.type === 'tool_completed') tools.push((data.name || 'Tool')+' · '+(data.success === true ? 'completed' : data.success === false ? 'failed' : 'finished'));
    else if (event.type === 'artifact' && data.id && !artifacts.some(a => a.id === data.id)) artifacts.push(data);
    tools = tools.slice(-60);
  }
  async function poll(force = false) {
    if (!synchronizeIdentity() || !visible() || polling || loading || !run || (!force && ((!active() && !pendingEvents) || Date.now()-lastPoll<750))) return;
    polling = true; lastPoll = Date.now(); const current = who(), turn = epoch, runId = run.id;
    try {
      const data = await request('/api/codex-workspace/runs/'+encodeURIComponent(runId)+'/events?after='+seq,undefined,current,turn); if (!data || run?.id !== runId) return;
      const previous = seq;
      for (const event of (data.events || []).filter(e => Number.isSafeInteger(e.seq) && e.seq > seq).sort((a,b) => a.seq-b.seq)) { if (event.seq <= seq) continue; acceptEvent(event); seq = event.seq; }
      pendingEvents = (data.events || []).length >= 200 && seq > previous;
      if (data.run) run = data.run;
      permissions = Array.isArray(data.permissions) ? data.permissions : [];
      if (Array.isArray(data.agents)) agents = data.agents;
      if (Array.isArray(data.artifacts)) artifacts = data.artifacts;
      if (feedback.startsWith('Updates interrupted:')) feedback = '';
      if (!active() && !pendingEvents) {
        permissions = []; catalogAt = 0;
        if (stream && !messages.some(m => m.run_id === run.id && m.role === 'assistant' && m.text === stream)) { messages.push({id:'stream:'+run.id,run_id:run.id,role:'assistant',text:stream}); stream = ''; }
      }
    } catch (error) { if (same(current,turn)) feedback = 'Updates interrupted: '+error.message+'. Reconnecting; the last recorded state is retained.'; }
    finally { if (same(current,turn)) { polling = false; render(); } }
  }
  async function send(textOverride = '') {
    if (!synchronizeIdentity()) return legacy.send?.(textOverride);
    const content = (textOverride || $('chatInput').value).trim();
    if (!content || sending || loading || active()) return;
    if (!catalog) await refreshCatalog(true);
    if (!owner() || !connected() || !projectKey) { feedback = 'Connect the Codex workspace before sending work.'; render(); return; }
    const draft = $('chatInput').value;
    if (!pendingSend || pendingSend.message !== content || pendingSend.conversation_id !== (conversationId || null) || pendingSend.project_key !== projectKey) pendingSend = {request_id:uuid(),conversation_id:conversationId || null,project_key:projectKey,message:content};
    sending = true; feedback = ''; render(); const current = who(), turn = epoch;
    try {
      const data = await request('/api/codex-workspace/message',pendingSend,current,turn); if (!data) return;
      if (!data.conversation_id || !data.run_id) throw new Error('The runtime did not return a conversation and run');
      if ($('chatInput').value === draft) $('chatInput').value = '';
      pendingSend = null; conversationId = data.conversation_id; stream = ''; seq = 0; tools = []; agents = []; permissions = []; artifacts = [];
      messages.push({id:'sent:'+data.run_id,role:'user',text:content,run_id:data.run_id}); run = {id:data.run_id,status:'queued',model:catalog.model,effort:catalog.effort}; lastPoll = 0; catalogAt = 0;
    } catch (error) { if (same(current,turn)) feedback = 'Submission could not be confirmed: '+error.message+'. Your draft is kept. Sending the same draft retries the same request.'; }
    finally { if (same(current,turn)) { sending = false; render(); void poll(true); } }
  }
  async function stop() {
    if (!owner() || !active() || stopBusy) return;
    const current = who(), turn = epoch, runId = run.id;
    const requestId = stopRequests.get(runId) || uuid(); stopRequests.set(runId,requestId); stopBusy = true; feedback = ''; render();
    try {
      const data = await request('/api/codex-workspace/runs/'+encodeURIComponent(runId)+'/stop',{request_id:requestId},current,turn); if (!data || run?.id !== runId) return;
      run = {...run,status:data.run?.status || 'stopping'}; feedback = 'Stop requested. Waiting for the runtime to confirm it has stopped.';
    } catch (error) { if (same(current,turn)) feedback = 'Stop could not be confirmed: '+error.message+'. Refresh to inspect the run; Stop retries the same request.'; }
    finally { if (same(current,turn)) { stopBusy = false; render(); void poll(true); } }
  }
  async function decide(id, decision) {
    const permission = permissions.find(p => p.id === id);
    if (!owner() || !active() || !permission || permission.state !== 'pending' || !permission.review_token || decisions.has(id) || !Number.isFinite(expires(permission.expires_at)) || expires(permission.expires_at) <= Date.now()) return;
    const current = who(), turn = epoch;
    const record = {request_id:uuid(),state:'sending'}; decisions.set(id,record); render();
    try {
      const data = await request('/api/codex-workspace/permissions/'+encodeURIComponent(id)+'/decision',{request_id:record.request_id,review_token:permission.review_token,decision,note:permissionNotes.get(id)||''},current,turn); if (!data) return;
      decisions.set(id,{...record,state:'accepted'}); feedback = 'Permission decision accepted. Tool completion will appear in the run updates.';
    } catch (error) { if (same(current,turn)) { decisions.set(id,{...record,state:'unknown'}); feedback = 'Permission decision could not be confirmed: '+error.message; } }
    finally { if (same(current,turn)) { render(); void poll(true); } }
  }
  async function download(id) {
    if (!owner() || downloads.has(id) || !/^[0-9a-f]{32}$/.test(id)) return;
    const current = who(), turn = epoch; downloads.add(id); feedback = ''; render();
    try {
      const data = await request('/api/codex-workspace/artifacts/'+id,undefined,current,turn); if (!data) return;
      if (typeof data.content !== 'string' || data.content_type !== 'text/plain') throw new Error('The runtime returned an unsupported artifact');
      const name = text(data.name).split(/[\\/]/).pop().replace(/[\x00-\x1f\x7f]/g,'').slice(0,160) || 'codex-result.txt';
      const url = URL.createObjectURL(new Blob([data.content],{type:'text/plain;charset=utf-8'})); downloadUrls.add(url);
      const link = document.createElement('a'); link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove();
      setTimeout(() => { URL.revokeObjectURL(url); downloadUrls.delete(url); },1000);
      feedback = 'Download prepared: '+name;
    } catch (error) { if (same(current,turn)) feedback = 'Could not download artifact: '+error.message; }
    finally { if (same(current,turn)) { downloads.delete(id); render(); } }
  }
  function open() {
    document.querySelector('[data-view="ai"]')?.click();
    if (!host.classList.contains('active')) document.querySelector('[data-home-go="ai"]')?.click();
    $('chatInput').focus(); void refreshCatalog(); return owner();
  }
  panel.addEventListener('input', event => { if (event.target.dataset.codexNote) permissionNotes.set(event.target.dataset.codexNote,event.target.value); });
  panel.addEventListener('click', event => {
    const button = event.target.closest('[data-codex-decision]'); if (button) void decide(button.dataset.permissionId,button.dataset.codexDecision);
    const artifact = event.target.closest('[data-codex-download]'); if (artifact) void download(artifact.dataset.codexDownload);
  });
  $('codexProject').addEventListener('change', () => { projectKey = $('codexProject').value; void selectConversation(''); });
  $('codexConversation').addEventListener('change', () => void selectConversation($('codexConversation').value));
  $('codexNewConversation').addEventListener('click', () => { void selectConversation(''); $('chatInput').focus(); });
  $('codexRefresh').addEventListener('click', () => { void refreshCatalog(true); void poll(true); });
  $('codexStop').addEventListener('click', () => void stop());
  $('chatInput').addEventListener('keydown', event => { if (owner() && event.key === 'Enter' && (event.metaKey || event.ctrlKey)) { event.preventDefault(); void send(); } });
  if (legacy.send) sendChat = function(message = '') { return owner() ? send(message) : legacy.send(message); };
  if (legacy.load) loadChat = function() { return owner() ? (conversationId ? selectConversation(conversationId) : refreshCatalog(true)) : legacy.load(); };
  // Existing handlers may hold the old function reference; the owner capture stops that route.
  document.addEventListener('click', event => {
    if (event.target.closest('#login,#logoutAllLocal') && owner()) { blockedIdentity = who(); resetContext(true); catalog = null; render(); }
    const sendButton = event.target.closest('#sendChat'), reloadButton = event.target.closest('#loadChat');
    if (owner() && (sendButton || reloadButton)) { event.preventDefault(); event.stopImmediatePropagation(); if (sendButton) void send(); else if (conversationId) void selectConversation(conversationId); else void refreshCatalog(true); }
  },true);
  function tick() { if (synchronizeIdentity()) { render(); if (visible()) { void refreshCatalog(); void poll(); } } }
  window.addEventListener('project-byte-home-render',tick);
  window.addEventListener('hashchange',tick);
  document.addEventListener('visibilitychange',tick);
  setInterval(tick,750);
  window.PRFKT_CODEX = Object.freeze({open,send});
  tick();
})();
