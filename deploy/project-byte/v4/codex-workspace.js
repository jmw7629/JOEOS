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
  const studio = document.createElement('section'); studio.id = 'codexTraceStudio'; studio.className = 'cw-trace-studio'; studio.hidden = true; studio.dataset.tab = 'graph';
  studio.innerHTML = `<div class="cw-trace-head"><div><h2>Run graph</h2><p class="small">Sessions, messages, agents and tools from the selected run.</p></div><div class="cw-trace-actions"><label class="small">Run <select id="codexTraceRun" class="field" aria-label="Recorded run"></select></label><button type="button" class="mini" id="codexTracePause" aria-pressed="false">Pause graph</button><button type="button" class="mini" id="codexTraceFocus" aria-pressed="false">Focus</button></div></div>
    <p id="codexTraceStatus" class="cw-trace-status small" role="status"></p><div id="codexTraceApproval" class="cw-trace-approval" hidden></div>
    <div class="cw-trace-tabs" role="group" aria-label="Run graph view"><button type="button" data-codex-trace-tab="graph" aria-pressed="true">Graph</button><button type="button" data-codex-trace-tab="usage" aria-pressed="false">Usage</button><button type="button" data-codex-trace-tab="details" aria-pressed="false">Details</button></div>
    <div class="cw-trace-layout"><aside class="cw-trace-map-pane"><label class="label small">Size by<select id="codexTraceMetric" class="field"><option value="activity">Activity</option><option value="duration">Tool duration</option><option value="tokens">Tokens</option></select></label><div id="codexTraceMap" class="cw-trace-map" aria-label="Measured run activity"></div><p id="codexTraceLegend" class="small"></p><div id="codexTraceList" class="cw-trace-list" aria-label="Accessible metric entries"></div></aside>
    <div class="cw-trace-graph-pane"><div class="cw-trace-navigation"><button type="button" class="mini" id="codexTraceZoomOut" aria-label="Zoom graph out">−</button><button type="button" class="mini" id="codexTraceFit">Fit</button><button type="button" class="mini" id="codexTraceZoomIn" aria-label="Zoom graph in">+</button><span class="small">Select to inspect · drag or swipe to pan</span></div><div id="codexTraceCanvas" class="cw-trace-canvas" tabindex="0" aria-label="Live run graph; use arrow keys to navigate"></div></div>
    <aside id="codexTraceInspector" class="cw-trace-inspector" aria-label="Selected run node" data-node-id=""></aside></div>`;
  const layout = host.querySelector('.ai-layout'); if (layout) layout.after(studio); else host.appendChild(studio);
  const style = document.createElement('style');
  style.textContent = `
    #ai.cw-enabled [hidden]{display:none!important}#codexWorkspacePanel{min-width:0}#codexWorkspacePanel .label{display:block;margin:12px 0 9px}#codexWorkspacePanel select{display:block;width:100%;min-width:0;margin-top:5px}#codexWorkspacePanel button{min-height:44px}.cw-heading{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.cw-run{border-top:1px solid var(--line);margin-top:16px;padding-top:14px}.cw-run p{margin:7px 0}.cw-feedback{color:var(--warn);white-space:pre-wrap}.cw-policy{margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}#codexWorkspacePanel p,#codexWorkspacePanel b,#codexWorkspacePanel a,.cw-conversation-status{overflow-wrap:anywhere}#codexAgentDetails summary{cursor:pointer;padding:10px 0;min-height:44px}#codexAgents>div,#codexTools>div{border-top:1px solid var(--line);padding:8px 0;white-space:pre-wrap;overflow-wrap:anywhere}.cw-permission{padding:12px;margin:12px 0;border:1px solid #536888;background:var(--card);border-radius:10px}.cw-permission h3{margin:0;font-size:14px}.cw-permission p{white-space:pre-wrap}.cw-permission pre{max-height:180px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}.cw-permission textarea{width:100%;min-height:65px;margin:8px 0;resize:vertical}.cw-artifact{display:block;padding:10px 0;border-top:1px solid var(--line);overflow-wrap:anywhere}.cw-conversation-status{min-height:19px;margin-top:7px}#ai.cw-enabled #chatlog .msg{overflow-wrap:anywhere}#ai.cw-enabled .cw-stream:empty{display:none}#ai.cw-enabled #chatlog{scroll-behavior:auto}#ai.cw-enabled #chatInput{min-width:0}#ai.cw-enabled .composer{min-width:0}#ai.cw-enabled .chatbox>.between>div{min-width:0}
    @media(max-width:650px){#ai.cw-enabled #chatlog{min-height:180px;max-height:34dvh}#ai.cw-enabled .chatbox{min-height:0}.cw-conversation-status{font-size:11px}#codexWorkspacePanel{padding-bottom:5px}}
    .cw-trace-studio{margin-top:18px;border:1px solid var(--line);border-radius:16px;background:#091422;color:var(--txt);overflow:hidden;min-width:0}.cw-trace-studio *{box-sizing:border-box}.cw-trace-studio button{min-height:44px}.cw-trace-head{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:16px;border-bottom:1px solid var(--line)}.cw-trace-head h2{margin:0;font-size:18px}.cw-trace-head p{margin:5px 0 0;color:var(--muted)}.cw-trace-actions,.cw-trace-navigation,.cw-trace-tabs{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.cw-trace-status{padding:10px 16px;margin:0;color:var(--muted);border-bottom:1px solid var(--line);overflow-wrap:anywhere}.cw-trace-approval{padding:10px 16px;background:#302a19;border-bottom:1px solid #75613a;color:#eddab0;display:flex;align-items:center;gap:10px;flex-wrap:wrap}.cw-trace-layout{display:grid;grid-template-columns:minmax(170px,.7fr) minmax(0,1.8fr) minmax(230px,1fr);min-width:0}.cw-trace-map-pane,.cw-trace-inspector{padding:14px;min-width:0;max-height:620px;overflow:auto}.cw-trace-map-pane{border-right:1px solid var(--line)}.cw-trace-inspector{border-left:1px solid var(--line);background:#0c1a2b}.cw-trace-inspector h3{font-size:16px;margin:0 0 10px;overflow-wrap:anywhere}.cw-trace-inspector p,.cw-trace-inspector dd{overflow-wrap:anywhere}.cw-trace-inspector dl{display:grid;grid-template-columns:1fr 1fr;gap:9px;font-size:11px}.cw-trace-inspector dt{color:var(--muted)}.cw-trace-inspector dd{margin:0}.cw-trace-inspector pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:220px;overflow:auto;border:1px solid var(--line);background:#070e19;padding:10px;border-radius:8px;font-size:11px;line-height:1.5}.cw-trace-inspector details{margin:12px 0}.cw-trace-inspector summary{min-height:36px;cursor:pointer;padding:8px 0}.cw-trace-navigation{padding:10px;border-bottom:1px solid var(--line)}.cw-trace-navigation span{color:var(--muted)}.cw-trace-navigation button{min-width:44px}.cw-trace-graph-pane{min-width:0}.cw-trace-canvas{height:505px;overflow:auto;overscroll-behavior:contain;touch-action:pan-x pan-y;background:radial-gradient(circle,#41617b55 1px,transparent 1px);background-size:22px 22px;position:relative}.cw-trace-canvas:focus-visible{outline:2px solid #8ed3ff;outline-offset:-2px}.cw-trace-graph{position:relative;transform-origin:0 0}.cw-trace-graph svg{position:absolute;inset:0;pointer-events:none}.cw-trace-edge{fill:none;stroke:#416b8c;stroke-width:1.5}.cw-trace-edge.selected{stroke:#96d8ff;stroke-width:2.5}.cw-trace-node{position:absolute;width:178px;height:78px;padding:10px;text-align:left;border:1px solid #365b7b;border-radius:10px;background:#122840;color:#d9ecff;display:flex;flex-direction:column;gap:5px;justify-content:center;overflow:hidden}.cw-trace-node b{font-size:12px;overflow:hidden;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow-wrap:anywhere}.cw-trace-node small{font-size:10px;color:#abc1d5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.cw-trace-node[data-kind=agent]{border-color:#786bb4;background:#211f3b}.cw-trace-node[data-kind=tool]{border-color:#39756d;background:#12312f}.cw-trace-node[data-kind=prompt],.cw-trace-node[data-kind=response]{border-color:#526c9e;background:#1c2b47}.cw-trace-node[data-kind=permission]{border-color:#b49351;background:#342c1c}.cw-trace-node[data-status=failed]{border-color:#cc8490}.cw-trace-node.selected,.cw-trace-tile.selected,.cw-trace-list button.selected{outline:2px solid #9adbff;outline-offset:-2px}.cw-trace-node:focus-visible,.cw-trace-studio button:focus-visible{outline:2px solid #b5e3ff;outline-offset:1px}.cw-trace-map{height:250px;position:relative;margin:12px 0;background:#0c1728;border-radius:8px;overflow:hidden}.cw-trace-tile{position:absolute;min-width:0!important;min-height:0!important;border:1px solid #8470af;border-radius:4px;background:#332a50;color:#e9defa;padding:5px;overflow:hidden;text-align:left;font-size:10px}.cw-trace-tile b,.cw-trace-tile small{display:block;overflow-wrap:anywhere}.cw-trace-list{display:flex;flex-direction:column;gap:6px;max-height:200px;overflow:auto}.cw-trace-list button{min-width:0;text-align:left;overflow-wrap:anywhere}.cw-trace-empty{padding:22px 14px;color:var(--muted);font-size:12px;line-height:1.6}.cw-trace-tabs{display:none;padding:9px;border-bottom:1px solid var(--line)}.cw-trace-tabs button{flex:1;border:1px solid var(--line);border-radius:8px;background:#14273d;color:var(--txt)}.cw-trace-tabs button[aria-pressed=true]{border-color:#80bce8;background:#1b4061}.cw-trace-focused{position:fixed!important;inset:8px;margin:0;z-index:1700;display:flex;flex-direction:column;overflow:auto;box-shadow:0 20px 80px #000c}.cw-trace-focused .cw-trace-layout{flex:1;min-height:0}.cw-trace-focused .cw-trace-graph-pane{display:flex;flex-direction:column;min-height:0}.cw-trace-focused .cw-trace-canvas{height:auto;min-height:240px;flex:1}.cw-trace-focused .cw-trace-map-pane,.cw-trace-focused .cw-trace-inspector{max-height:100%}
    @media(max-width:1100px) and (min-width:761px){.cw-trace-layout{grid-template-columns:minmax(160px,.7fr) minmax(0,1.7fr)}.cw-trace-inspector{grid-column:1/-1;max-height:340px;border-left:0;border-top:1px solid var(--line)}}
    @media(max-width:760px){.cw-trace-layout{display:block}.cw-trace-tabs{display:flex}.cw-trace-head{align-items:flex-start;flex-wrap:wrap;padding:12px}.cw-trace-head h2{font-size:17px}.cw-trace-actions{width:100%}.cw-trace-actions button{flex:1}.cw-trace-studio[data-tab=graph] .cw-trace-map-pane,.cw-trace-studio[data-tab=graph] .cw-trace-inspector,.cw-trace-studio[data-tab=usage] .cw-trace-graph-pane,.cw-trace-studio[data-tab=usage] .cw-trace-inspector,.cw-trace-studio[data-tab=details] .cw-trace-graph-pane,.cw-trace-studio[data-tab=details] .cw-trace-map-pane{display:none}.cw-trace-canvas{height:380px}.cw-trace-map-pane,.cw-trace-inspector{border:0;max-height:520px}.cw-trace-map{height:300px}.cw-trace-studio select{font-size:16px}.cw-trace-navigation span{width:100%}.cw-trace-focused{inset:4px}.cw-trace-focused .cw-trace-layout{min-height:0}.cw-trace-focused .cw-trace-canvas{height:calc(100dvh - 280px);min-height:200px}.cw-trace-focused .cw-trace-inspector{max-height:calc(100dvh - 230px)}}
  `;
  document.head.appendChild(style);
  let identity = '', blockedIdentity = null, epoch = 0, catalog = null, catalogAt = 0, catalogBusy = false;
  let conversationId = '', conversationProjectKey = '', projectKey = '', messages = [], run = null, seq = 0, stream = '', tools = [], agents = [], artifacts = [], permissions = [];
  let loading = false, sending = false, polling = false, stopBusy = false, feedback = '', lastPoll = 0, restored = false, pendingSend = null, pendingEvents = false;
  let messagesSignature = '', permissionSignature = '', agentSignature = '', toolSignature = '', artifactSignature = '';
  const controllers = new Set(), permissionNotes = new Map(), decisions = new Map(), stopRequests = new Map(), downloads = new Set(), downloadUrls = new Set();
  const who = () => JSON.stringify([accessKey,session?.subject,session?.level,session?.ok]);
  const owner = () => who() !== blockedIdentity && !!accessKey && session?.ok && session?.level === 4;
  const visible = () => !document.hidden && host.classList.contains('active');
  const same = (current, turn) => current === who() && turn === epoch && owner();
  const uuid = () => crypto.randomUUID();
  const expires = value => Number(value) > 1e12 ? Number(value) : Number(value) * 1000;
  let serverClock = null;
  const serverNow = () => serverClock ? serverClock.time + performance.now() - serverClock.received : Date.now();
  const syncClock = value => { if (typeof value === 'number' && Number.isFinite(value) && value > 0) serverClock = {time:expires(value),received:performance.now()}; };
  const remaining = permission => Number.isFinite(expires(permission.expires_at)) ? Math.max(0,Math.ceil((expires(permission.expires_at)-serverNow())/1000)) : 0;
  const projectList = () => Array.isArray(catalog?.projects) ? catalog.projects : [];
  function projectState() {
    const project = projectList().find(item => item.key === projectKey);
    const mode = project && Object.hasOwn(project,'mode') ? project.mode : 'execute';
    const flag = name => project && Object.hasOwn(project,name) ? project[name] === true : catalog?.[name] === true;
    const configured = !!project && flag('configured'), connected = !!project && flag('connected');
    return {project,mode,configured,connected,ready:mode === 'execute' && configured && connected};
  }
  function projectStatus(state = projectState()) {
    if (!catalog) return 'Checking the Codex workspace…';
    if (state.ready) return 'Connected · agents selected automatically';
    if (!state.project && projectKey) return 'This project is no longer available in the workspace. Its recorded conversation remains open.';
    if (text(state.project?.detail)) return state.project.detail;
    if (state.mode === 'observe') return 'This project is being handled elsewhere. Its recorded history is available here.';
    if (state.mode === 'blocked') return 'Execution is blocked for this project. Its recorded history is available here.';
    if (state.mode !== 'execute') return 'Execution is unavailable for this project.';
    if (!state.configured) return 'The Codex workspace runtime has not been connected.';
    return 'Codex sign-in or runtime connection is unavailable.';
  }
  const active = () => !!run && activeStates.has(run.status);
  const resetSignatures = () => {messagesSignature = permissionSignature = agentSignature = toolSignature = artifactSignature = '';};

  // Trace state is local to the selected owner conversation. Permissions stay in
  // their existing decision path; trace nodes never contain review capabilities.
  let traceNodes = new Map(), traceUsage = new Map(), traceSelected = '', tracePaused = false, traceZoom = 1, traceRevision = 0, traceDrawn = '', conversationRuns = [];
  const metricNumber = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
  const nodeKey = (kind,id) => kind+':'+id;
  function traceClear() {
    traceNodes.clear(); traceUsage.clear(); traceSelected = ''; tracePaused = false; traceRevision++; traceDrawn = ''; serverClock = null;
    studio.classList.remove('cw-trace-focused'); studio.hidden = true;
    for (const id of ['codexTraceCanvas','codexTraceMap','codexTraceList','codexTraceInspector','codexTraceApproval','codexTraceStatus']) $(id).replaceChildren();
    $('codexTracePause').textContent = 'Pause graph'; $('codexTracePause').setAttribute('aria-pressed','false');
    $('codexTraceFocus').setAttribute('aria-pressed','false');
  }
  function tracePut(id, value) {
    if (!id) return;
    if (traceNodes.size >= 1200 && !traceNodes.has(id)) return;
    const next={...traceNodes.get(id),...value,id}; if (JSON.stringify(next)===JSON.stringify(traceNodes.get(id))) return;
    traceNodes.set(id,next); traceRevision++;
  }
  function traceRoots() {
    if (!run) return;
    tracePut(nodeKey('session',conversationId),{kind:'session',label:'Conversation',parent:null});
    tracePut(nodeKey('run',run.id),{kind:'run',label:'Codex run',status:run.status,parent:nodeKey('session',conversationId),started_at:run.created_at,model:run.model,effort:run.effort});
    for (const agent of agents) tracePut(nodeKey('agent',agent.id),{kind:'agent',label:agent.role || 'Agent',status:agent.status,model:agent.model,effort:agent.effort,parent:agent.parent_id ? nodeKey('agent',agent.parent_id) : nodeKey('run',run.id)});
    for (const message of messages.filter(m=>m.run_id===run.id && !String(m.id).startsWith('sent:') && !String(m.id).startsWith('event:') && !String(m.id).startsWith('stream:'))) {
      const key=nodeKey(message.role==='user'?'prompt':'response',message.id);
      if (!traceNodes.has(key)) tracePut(key,{kind:message.role==='user'?'prompt':'response',label:message.role==='user'?'Your request':'Final response',text:message.text,parent:nodeKey('run',run.id),started_at:message.ts});
    }
  }
  function traceAccept(event) {
    const d=event.data || {}, parent=d.agent_id ? nodeKey('agent',d.agent_id) : nodeKey('run',run.id);
    if (event.type==='prompt') tracePut(nodeKey('prompt',d.message_id || 'event-'+event.seq),{kind:'prompt',label:'Your request',text:text(d.text),parent:nodeKey('run',run.id),started_at:d.created_at});
    else if (event.type==='agent_started') tracePut(nodeKey('agent',d.id),{kind:'agent',label:d.role || 'Agent',status:d.status,model:d.model,effort:d.effort,started_at:d.started_at,parent:d.parent_id ? nodeKey('agent',d.parent_id) : nodeKey('run',run.id)});
    else if (event.type==='agent_completed') tracePut(nodeKey('agent',d.id),{kind:'agent',status:d.status,ended_at:d.ended_at,duration_ms:d.duration_ms});
    else if (event.type==='token_usage') traceUsage.set(d.agent_id+'\0'+d.native?.thread_id,d);
    else if (event.type==='agent_message' || event.type==='message_delta' || d.output_kind==='agent_delta') {
      const id=nodeKey('response',d.message_id || 'legacy-stream-'+(d.agent_id || run.id)), previous=traceNodes.get(id);
      tracePut(id,{kind:'response',label:event.type==='agent_message'?'Agent response':'Agent message',parent,native:d.native,content:d.content,
        text:event.type==='agent_message'?text(d.text):((previous?.text || '')+text(d.text)).slice(-32768),status:event.type==='agent_message'?'completed':'streaming'});
    } else if (event.type==='message') {
      tracePut(nodeKey('response',d.message_id || 'event-'+event.seq),{kind:'response',label:'Final response',parent,text:text(d.text),started_at:d.created_at,native_message_ids:d.native_message_ids});
    } else if (event.type==='tool_started' && d.tool_id) tracePut(nodeKey('tool',d.tool_id),{kind:'tool',label:d.name || 'Tool',parent,status:'working',arguments:d.arguments,native:d.native,started_at:d.started_at});
    else if (event.type==='tool_output' && d.tool_id) {
      const id=nodeKey('tool',d.tool_id), previous=traceNodes.get(id);
      tracePut(id,{kind:'tool',parent,output:d.output_kind==='stream'?((previous?.output || '')+text(d.text)).slice(-32768):previous?.output,truncated:previous?.truncated || d.truncated});
    } else if (event.type==='tool_completed' && d.tool_id) tracePut(nodeKey('tool',d.tool_id),{kind:'tool',label:d.name || 'Tool',parent,status:d.success?'completed':'failed',duration_ms:d.duration_ms,ended_at:d.ended_at,result:d.result,error:d.error});
    else if (event.type.startsWith('tool_')) tracePut(nodeKey('legacy',event.seq),{kind:'legacy',label:d.name || 'Recorded tool event',parent,text:text(d.text),status:'Not correlated',recorded_at:event.ts});
    traceRevision++;
  }
  function tracePermissions() {
    for (const permission of permissions) tracePut(nodeKey('permission',permission.id),{kind:'permission',label:permission.tool || 'Permission',parent:permission.tool_id ? nodeKey('tool',permission.tool_id) : nodeKey('run',run.id),status:permission.state,expires_at:permission.expires_at,text:permission.summary});
    const pending=permissions.filter(p=>p.state==='pending' && remaining(p)>0);
    const banner=$('codexTraceApproval'); banner.hidden=!pending.length;
    banner.replaceChildren();
    if (pending.length) {
      const label=document.createElement('span'); label.textContent=`${pending.length} permission request${pending.length===1?'':'s'} · ${Math.min(...pending.map(remaining))}s remaining`;
      const button=document.createElement('button'); button.type='button'; button.className='mini'; button.textContent='Review request';
      button.onclick=()=>{studio.classList.remove('cw-trace-focused'); $('codexTraceFocus').setAttribute('aria-pressed','false'); $('codexPermissions').scrollIntoView({block:'center'}); $('codexPermissions').querySelector('button')?.focus();};
      banner.append(label,button);
    }
  }
  function traceInspect(node) {
    const inspector=$('codexTraceInspector'); inspector.replaceChildren(); inspector.dataset.nodeId=node?.id || '';
    if (!node) {inspector.textContent='Select a node to inspect its recorded details.';return;}
    const heading=document.createElement('h3'); heading.textContent=node.label || node.kind; inspector.append(heading);
    const dl=document.createElement('dl');
    const detail=(label,value)=>{const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=value;dl.append(dt,dd);};
    detail('Type',node.kind); detail('Status',node.status || 'Recorded'); detail('ID',node.id);
    detail('Duration',metricNumber(node.duration_ms)?node.duration_ms.toLocaleString()+' ms':'Not recorded');
    detail('Started',metricNumber(node.started_at)?new Date(node.started_at*1000).toLocaleString():'Not recorded');
    if (node.model) detail('Model',[node.model,node.effort].filter(Boolean).join(' · '));
    inspector.append(dl);
    const section=(label,value)=>{const group=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent=label;pre.textContent=value;group.open=true;group.append(summary,pre);inspector.append(group);};
    if (node.text) section('Message',node.text);
    if (node.kind==='tool') for (const key of ['arguments','result','error']) {
      const payload=node[key]; if (key==='error' && !payload) continue;
      section(key[0].toUpperCase()+key.slice(1),payload ? text(payload.text)+(payload.truncated?'\n[Truncated · '+payload.bytes+' original bytes]':'') : 'Not recorded');
    }
    if (node.output) section('Stream output',node.output+(node.truncated?'\n[Output truncated]':''));
    if (node.native) section('Native IDs',JSON.stringify(node.native,null,2));
    const usage=[...traceUsage.values()].filter(u=>node.id===nodeKey('agent',u.agent_id));
    if (node.kind==='agent') section('Reported tokens',usage.length?JSON.stringify(usage.map(u=>({source:u.source,last:u.last,total:u.total})),null,2):'Not recorded');
  }
  function traceSelect(id, reveal=false) {
    traceSelected=id; traceDrawn=''; renderTrace(true);
    if (reveal) studio.dataset.tab='details';
    for (const button of studio.querySelectorAll('[data-codex-trace-tab]')) button.setAttribute('aria-pressed',String(button.dataset.codexTraceTab===studio.dataset.tab));
  }
  function renderTrace(force=false) {
    studio.hidden=!owner() || !run;
    if (studio.hidden) return;
    const options=conversationRuns.length?conversationRuns:[run];
    const markup=options.map((r,i)=>`<option value="${escape(r.id)}">Run ${i+1} · ${escape(labels[r.id===run.id?run.status:r.status] || r.status)}</option>`).join('');
    if ($('codexTraceRun').innerHTML!==markup) $('codexTraceRun').innerHTML=markup;
    $('codexTraceRun').value=run.id; $('codexTraceRun').disabled=active() || loading || sending;
    tracePermissions();
    $('codexTraceStatus').textContent=(tracePaused?'Graph paused · approvals and execution remain live. ':`${labels[run.status] || run.status} · `)+`${traceNodes.size} nodes${traceNodes.size>=1200?' · display limit reached':''} · ${pendingEvents?'Loading recorded events…':'Recorded activity only'}`;
    if (tracePaused && !force) return;
    const signature=[traceRevision,traceSelected,traceZoom,$('codexTraceMetric').value].join('|');
    if (signature===traceDrawn) return; traceDrawn=signature;
    const nodes=[...traceNodes.values()], byId=traceNodes, positions=new Map(), levels=new Map();
    const depth=(node,seen=new Set())=>{if (!node?.parent || seen.has(node.id) || seen.size>=8) return 0;seen.add(node.id);return 1+depth(byId.get(node.parent),seen);};
    for (const node of nodes) {const level=depth(node), row=levels.get(level)||0;positions.set(node.id,{x:20+level*216,y:20+row*104});levels.set(level,row+1);}
    const width=Math.max(650,...[...positions.values()].map(p=>p.x+200)),height=Math.max(460,...[...positions.values()].map(p=>p.y+100));
    const canvas=$('codexTraceCanvas'), focused=canvas.contains(document.activeElement)?document.activeElement.dataset.traceNode:null;
    const wrap=document.createElement('div');wrap.style.width=width*traceZoom+'px';wrap.style.height=height*traceZoom+'px';
    const graph=document.createElement('div');graph.className='cw-trace-graph';graph.style.width=width+'px';graph.style.height=height+'px';graph.style.transform=`scale(${traceZoom})`;
    const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('width',width);svg.setAttribute('height',height);
    for (const node of nodes) {const a=positions.get(node.parent),b=positions.get(node.id);if (!a || !b) continue;const edge=document.createElementNS(svg.namespaceURI,'path');edge.setAttribute('d',`M${a.x+178},${a.y+39} C${a.x+202},${a.y+39} ${b.x-24},${b.y+39} ${b.x},${b.y+39}`);edge.setAttribute('class','cw-trace-edge'+([node.id,node.parent].includes(traceSelected)?' selected':''));svg.append(edge);}
    graph.append(svg);
    for (const node of nodes) {const pos=positions.get(node.id),button=document.createElement('button');button.type='button';button.className='cw-trace-node'+(traceSelected===node.id?' selected':'');button.dataset.traceNode=node.id;button.dataset.kind=node.kind;button.dataset.status=node.status||'';button.style.left=pos.x+'px';button.style.top=pos.y+'px';button.setAttribute('aria-pressed',String(traceSelected===node.id));const title=document.createElement('b'),small=document.createElement('small');title.textContent=node.label || node.kind;small.textContent=node.kind+' · '+(node.status || 'Recorded');button.append(title,small);graph.append(button);}
    wrap.append(graph);canvas.replaceChildren(wrap);
    if (focused) [...canvas.querySelectorAll('button')].find(b=>b.dataset.traceNode===focused)?.focus({preventScroll:true});
    const metric=$('codexTraceMetric').value, entries=[];
    if (metric==='tokens') {
      const totals=new Map(); for (const usage of traceUsage.values()) if (metricNumber(usage.total?.total_tokens)) totals.set(usage.agent_id,(totals.get(usage.agent_id)||0)+usage.total.total_tokens);
      for (const [id,value] of totals) entries.push({id:nodeKey('agent',id),value});
      $('codexTraceLegend').textContent=`Reported tokens · ${totals.size}/${agents.length} agents reporting. Latest cumulative totals; missing reports are not zero.`;
    } else if (metric==='duration') {
      for (const node of nodes) if (node.kind==='tool' && metricNumber(node.duration_ms)) entries.push({id:node.id,value:node.duration_ms});
      $('codexTraceLegend').textContent='Measured tool duration in ms, including approval wait. Concurrent tools overlap.';
    } else {
      for (const node of nodes) if (['tool','response','prompt','legacy'].includes(node.kind)) entries.push({id:node.id,value:1});
      $('codexTraceLegend').textContent='One unit per recorded message or tool; activity is not token usage.';
    }
    const map=$('codexTraceMap'),list=$('codexTraceList');map.replaceChildren();list.replaceChildren();
    const total=entries.reduce((sum,e)=>sum+e.value,0);
    // Binary partition treemap preserves areas without unreadable single strips.
    function tile(items,x,y,w,h) {if (!items.length) return;if (items.length===1) {const entry=items[0],button=document.createElement('button');button.type='button';button.className='cw-trace-tile'+(entry.id===traceSelected?' selected':'');button.dataset.traceNode=entry.id;button.style.cssText=`left:${x}%;top:${y}%;width:${w}%;height:${h}%`;button.textContent=(byId.get(entry.id)?.label || 'Recorded item')+' · '+entry.value.toLocaleString();button.title=button.textContent;map.append(button);return;}
      const half=Math.ceil(items.length/2),a=items.slice(0,half),b=items.slice(half),sum=items.reduce((s,e)=>s+e.value,0),ratio=a.reduce((s,e)=>s+e.value,0)/sum;
      if (w>h) {tile(a,x,y,w*ratio,h);tile(b,x+w*ratio,y,w*(1-ratio),h);} else {tile(a,x,y,w,h*ratio);tile(b,x,y+h*ratio,w,h*(1-ratio));}}
    if (total>0) tile(entries.filter(e=>e.value>0),0,0,100,100);else map.textContent='Not recorded';
    for (const entry of entries) {const button=document.createElement('button');button.type='button';button.className='mini'+(entry.id===traceSelected?' selected':'');button.dataset.traceNode=entry.id;button.textContent=(byId.get(entry.id)?.label || 'Recorded item')+' · '+entry.value.toLocaleString();list.append(button);}
    traceInspect(byId.get(traceSelected));
  }
  studio.addEventListener('click',event=>{const node=event.target.closest('[data-trace-node]');if (node) traceSelect(node.dataset.traceNode);const tab=event.target.closest('[data-codex-trace-tab]');if (tab) {studio.dataset.tab=tab.dataset.codexTraceTab;for (const b of studio.querySelectorAll('[data-codex-trace-tab]')) b.setAttribute('aria-pressed',String(b===tab));}});
  $('codexTracePause').onclick=()=>{tracePaused=!tracePaused;$('codexTracePause').textContent=tracePaused?'Resume graph':'Pause graph';$('codexTracePause').setAttribute('aria-pressed',String(tracePaused));renderTrace(true);};
  $('codexTraceFocus').onclick=()=>{studio.classList.toggle('cw-trace-focused');$('codexTraceFocus').setAttribute('aria-pressed',String(studio.classList.contains('cw-trace-focused')));};
  const zoom=amount=>{traceZoom=Math.max(.35,Math.min(1.8,amount));renderTrace(true);};
  $('codexTraceZoomIn').onclick=()=>zoom(traceZoom+.15);$('codexTraceZoomOut').onclick=()=>zoom(traceZoom-.15);
  $('codexTraceFit').onclick=()=>{const graph=$('codexTraceCanvas').querySelector('.cw-trace-graph');zoom(graph?Math.min(1,$('codexTraceCanvas').clientWidth/parseFloat(graph.style.width)):1);$('codexTraceCanvas').scrollTo(0,0);};
  $('codexTraceMetric').onchange=()=>renderTrace(true);
  $('codexTraceRun').onchange=()=>{if(!active() && !loading && !sending) void selectConversation(conversationId,$('codexTraceRun').value);};
  studio.addEventListener('keydown',event=>{if (event.key==='Escape') {studio.classList.remove('cw-trace-focused');$('codexTraceFocus').setAttribute('aria-pressed','false');$('codexTraceFocus').focus();}if (event.target.closest('#codexTraceCanvas') && ['ArrowDown','ArrowUp','ArrowLeft','ArrowRight'].includes(event.key)) {event.preventDefault();const nodes=[...$('codexTraceCanvas').querySelectorAll('button')],index=nodes.indexOf(document.activeElement),next=nodes[Math.max(0,Math.min(nodes.length-1,index+(['ArrowDown','ArrowRight'].includes(event.key)?1:-1)))];next?.focus();if(next) traceSelect(next.dataset.traceNode);}});
  let pan=null;
  $('codexTraceCanvas').addEventListener('pointerdown',e=>{if(e.pointerType!=='mouse' || e.target.closest('button'))return;pan={x:e.clientX,y:e.clientY,left:e.currentTarget.scrollLeft,top:e.currentTarget.scrollTop};e.currentTarget.setPointerCapture(e.pointerId);});
  $('codexTraceCanvas').addEventListener('pointermove',e=>{if(pan){e.currentTarget.scrollLeft=pan.left+pan.x-e.clientX;e.currentTarget.scrollTop=pan.top+pan.y-e.clientY;}});
  for (const name of ['pointerup','pointercancel','lostpointercapture']) $('codexTraceCanvas').addEventListener(name,()=>{pan=null;});

  function resetContext(clearDraft = false) {
    traceClear(); conversationRuns = [];
    epoch++; for (const controller of controllers) controller.abort(); controllers.clear();
    for (const url of downloadUrls) URL.revokeObjectURL(url); downloadUrls.clear(); downloads.clear();
    conversationId = ''; conversationProjectKey = ''; messages = []; run = null; seq = 0; stream = ''; tools = []; agents = []; artifacts = []; permissions = [];
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
    const wasEnabled = host.classList.contains('cw-enabled'), enabled = owner(); host.classList.toggle('cw-enabled', enabled); panel.hidden = progress.hidden = !enabled; if (!enabled) studio.hidden = true;
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
    const list = projectList();
    const select = $('codexProject');
    let markup = list.map(p => `<option value="${escape(p.key)}">${escape(p.label || p.key)}</option>`).join('');
    if (conversationProjectKey) {
      projectKey = conversationProjectKey;
      if (!list.some(p => p.key === projectKey)) markup += `<option value="${escape(projectKey)}">Current project unavailable</option>`;
    } else if (!list.some(p => p.key === projectKey)) projectKey = list.find(p => p.key === 'joeos')?.key || list[0]?.key || '';
    if (select.innerHTML !== markup) select.innerHTML = markup || '<option value="">No execution project connected</option>';
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
        const decision = decisions.get(p.id), expired = !Number.isFinite(expires(p.expires_at)) || remaining(p) <= 0;
        const enabled = !decision && !expired && !!p.review_token && active();
        let args = ''; try { args = JSON.stringify(p.arguments ?? {},null,2); } catch { args = 'Request details unavailable'; }
        return `<article class="cw-permission" data-codex-permission="${escape(p.id)}"><h3>${escape(p.tool || 'Tool permission')}</h3><p class="small">${escape(p.summary)}</p><details><summary>Requested action</summary><pre>${escape(args)}</pre></details><p class="small" data-codex-countdown="${escape(p.id)}">${expired ? 'Review window expired. Refresh to inspect the current request.' : 'Review by '+escape(new Date(expires(p.expires_at)).toLocaleTimeString())}</p>${decision ? `<p class="small">${escape(decision.state === 'sending' ? 'Sending decision…' : decision.state === 'accepted' ? 'Decision accepted. Waiting for the tool result.' : 'Decision could not be confirmed. Refresh to inspect the request before taking further action.')}</p>` : `<label class="label small">Decision note (optional)<textarea class="field" data-codex-note="${escape(p.id)}" maxlength="1000" ${enabled?'':'disabled'}>${escape(permissionNotes.get(p.id)||'')}</textarea></label><div class="flex"><button type="button" class="btn primary" data-codex-decision="approve_once" data-permission-id="${escape(p.id)}" ${enabled?'':'disabled'}>Approve once</button><button type="button" class="btn" data-codex-decision="deny" data-permission-id="${escape(p.id)}" ${enabled?'':'disabled'}>Deny</button></div>`}</article>`;
      }).join('');
    }
    for (const p of permissions) { const el=[...$('codexPermissions').querySelectorAll('[data-codex-countdown]')].find(e=>e.dataset.codexCountdown===p.id); if(el) el.textContent=remaining(p)>0 ? remaining(p)+'s remaining · server review window' : 'Review window expired.'; }
    for (const p of permissions) if (remaining(p) <= 0) for (const button of $('codexPermissions').querySelectorAll('[data-permission-id]')) if (button.dataset.permissionId === p.id) button.disabled = true;
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
    const state = projectState();
    $('codexConnection').textContent = projectStatus(state);
    $('codexRoute').textContent = [run?.model || (state.ready ? catalog?.model : ''),run?.effort || (state.ready ? catalog?.effort : ''),state.project?.repo].filter(Boolean).join(' · ');
    const idleState = state.ready ? 'Ready' : state.mode === 'observe' ? 'Observed' : state.mode === 'blocked' ? 'Blocked' : state.configured ? 'Unavailable' : 'Not connected';
    $('codexRunState').textContent = sending ? 'Sending…' : loading ? 'Loading conversation…' : labels[run?.status] || (run?.status ? 'Recorded state: '+run.status : idleState);
    $('codexFeedback').textContent = feedback;
    progress.textContent = run ? `${labels[run.status] || run.status}${active() ? ' · updates appear here' : ''}` : sending ? 'Submitting your request…' : state.ready ? 'Choose an outcome; Codex handles the routing.' : 'Project status and recorded history are available here.';
    $('codexStop').hidden = !active(); $('codexStop').disabled = stopBusy || run?.status === 'stopping';
    $('sendChat').disabled = !state.ready || !projectKey || sending || loading || active() || (!!conversationId && conversationProjectKey !== projectKey);
    $('loadChat').textContent = 'Refresh conversation'; $('loadChat').disabled = loading;
    renderMessages(); renderPermissions(); renderEvidence(); renderTrace();
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
  async function selectConversation(id, preferredRun = null) {
    const selectedProject = projectKey;
    resetContext(); conversationId = id || ''; conversationProjectKey = id ? selectedProject : ''; restored = true;
    if (!id) { render(); return; }
    loading = true; render(); const current = who(), turn = epoch;
    try {
      const data = await request('/api/codex-workspace/conversations/'+encodeURIComponent(id),undefined,current,turn); if (!data) return;
      if (data.conversation?.id !== id || data.conversation?.project_key !== selectedProject) throw new Error('The conversation does not match the selected project. Select its history from the correct project.');
      syncClock(data.server_time);
      conversationId = id; conversationProjectKey = selectedProject;
      messages = (data.messages || []).map(m => ({id:m.id,role:m.role,text:text(m.text),run_id:m.run_id,ts:m.ts}));
      const runs = data.runs || []; conversationRuns = runs; run = [...runs].reverse().find(r => activeStates.has(r.status)) || runs.find(r=>r.id===preferredRun) || runs[runs.length-1] || null;
      artifacts = data.artifacts || []; lastPoll = 0; traceRoots();
    } catch (error) { if (same(current,turn)) { conversationId = ''; conversationProjectKey = ''; feedback = 'Could not load conversation: '+error.message; } }
    finally { if (same(current,turn)) { loading = false; render(); void poll(true); } }
  }
  function acceptEvent(event) {
    traceAccept(event);
    const data = event.data || {};
    if (event.type === 'message_delta') stream = (stream + text(data.text)).slice(-250000);
    else if (event.type === 'message') {
      const content = text(data.text), role = data.role || 'assistant';
      if (role === 'assistant') stream = '';
      if (!messages.some(m => m.run_id === run?.id && m.role === role && m.text === content)) messages.push({id:data.message_id || 'event:'+event.seq,run_id:run?.id,role,text:content});
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
      syncClock(data.server_time);
      const previous = seq;
      for (const event of (data.events || []).filter(e => Number.isSafeInteger(e.seq) && e.seq > seq).sort((a,b) => a.seq-b.seq)) { if (event.seq <= seq) continue; acceptEvent(event); seq = event.seq; }
      pendingEvents = (data.events || []).length >= 200 && seq > previous;
      if (data.run) run = data.run;
      if (Array.isArray(data.usage)) for (const usage of data.usage) traceUsage.set(usage.agent_id+'\0'+usage.native?.thread_id,usage);
      permissions = Array.isArray(data.permissions) ? data.permissions : [];
      tracePermissions();
      if (Array.isArray(data.agents)) agents = data.agents;
      if (Array.isArray(data.artifacts)) artifacts = data.artifacts;
      traceRoots();
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
    if (!owner() || sending || loading || active()) return;
    if (!projectState().ready || !projectKey) { feedback = projectStatus(); render(); return; }
    if (conversationId && conversationProjectKey !== projectKey) { feedback = 'Select a conversation belonging to this project before sending work.'; render(); return; }
    const draft = $('chatInput').value;
    if (!pendingSend || pendingSend.message !== content || pendingSend.conversation_id !== (conversationId || null) || pendingSend.project_key !== projectKey) pendingSend = {request_id:uuid(),conversation_id:conversationId || null,project_key:projectKey,message:content};
    sending = true; feedback = ''; render(); const current = who(), turn = epoch;
    try {
      const data = await request('/api/codex-workspace/message',pendingSend,current,turn); if (!data) return;
      if (!data.conversation_id || !data.run_id) throw new Error('The runtime did not return a conversation and run');
      if ($('chatInput').value === draft) $('chatInput').value = '';
      traceClear(); pendingSend = null; conversationId = data.conversation_id; conversationProjectKey = projectKey; stream = ''; seq = 0; tools = []; agents = []; permissions = []; artifacts = [];
      messages.push({id:'sent:'+data.run_id,role:'user',text:content,run_id:data.run_id}); run = {id:data.run_id,status:'queued',model:catalog.model,effort:catalog.effort}; lastPoll = 0; catalogAt = 0; conversationRuns.push(run); traceRoots();
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
    if (!owner() || !active() || !permission || permission.state !== 'pending' || !permission.review_token || decisions.has(id) || !Number.isFinite(expires(permission.expires_at)) || remaining(permission) <= 0) return;
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
  $('codexProject').addEventListener('change', () => {
    const key = $('codexProject').value;
    if (sending || loading || active() || !projectList().some(project => project.key === key)) { render(); return; }
    projectKey = key; void selectConversation('');
  });
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

// Presentation only: preserves server settings, source data and module layout.
(() => {
  const sheet=document.createElement('style');
  sheet.textContent=`
    body.pb-depth .home-hero,body.pb-depth .home-card,body.pb-depth .home-kpi,body.pb-depth .panel,body.pb-depth .project,body.pb-depth .agentcard,body.pb-depth .modelcard,body.pb-depth .teamcard,body.pb-depth .cw-trace-studio{border-top-color:#91b6d950;border-bottom-color:#020711;box-shadow:inset 0 1px 0 #d9edff21,inset 1px 0 0 #a5d5ff0b,inset 0 -2px 0 #0008,0 2px 0 #020710,0 12px 24px #0006,0 26px 55px #0003;background-image:linear-gradient(145deg,#9acbff09,transparent 45%,#0002)}
    body.pb-depth .home-card-head,body.pb-depth .cw-trace-head{background:linear-gradient(180deg,#b3d9ff06,transparent);text-shadow:0 2px 2px #000a}
    body.pb-depth .home-kpi b,body.pb-depth .home-hero h2{ text-shadow:0 1px 0 #b0d9ff40,0 3px 0 #0008,0 8px 18px #0008 }
    body.pb-depth .home-command,body.pb-depth .chatlog,body.pb-depth .cw-trace-canvas,body.pb-depth .execution-fabric{box-shadow:inset 0 3px 12px #0009,inset 0 -1px 0 #9dd5ff1f}
    body.pb-depth .home-action,body.pb-depth .mini,body.pb-depth .cw-trace-node,body.pb-depth .btn{box-shadow:inset 0 1px 0 #e3f3ff21,inset 0 -2px 0 #0005,0 3px 0 #020711,0 6px 12px #0004;background-image:linear-gradient(160deg,#b9dfff12,transparent 58%,#0002)}
    body.pb-depth .cw-trace-node{box-shadow:inset 0 1px 0 #e3f3ff35,inset 0 -2px 0 #0005,0 5px 0 #040c17,0 12px 16px #0006}
    body.pb-depth .cw-trace-node.selected{box-shadow:inset 0 1px 0 #e3f3ff35,0 4px 0 #071323,0 0 24px #6ac6ff44}
    body.pb-depth .home-hero::after{content:'';position:absolute;right:-100px;top:-180px;width:600px;height:600px;border-radius:50%;border:1px solid #8ac6ff30;box-shadow:inset 0 0 70px #4d9edc10,0 0 0 35px #81bfff05,0 0 0 36px #9fbcff14,0 0 0 90px #9fbcff04;transform:rotateX(64deg) rotate(-25deg);pointer-events:none;z-index:-1;background:conic-gradient(from 0deg,transparent 0 65%,#80c7ff19 75%,transparent 85%)}
    .pb-depth-controls{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.pb-depth-controls button{min-height:44px;font-size:11px;letter-spacing:.02em}
    body.pb-depth-motion .home-hero::after{animation:pb-orbital-drift 75s linear infinite}
    body.pb-depth-motion .home-status-dot{animation:pb-depth-beacon 5s ease-in-out infinite}
    @keyframes pb-orbital-drift{from{transform:rotateX(64deg) rotate(-25deg)}to{transform:rotateX(64deg) rotate(335deg)}}
    @keyframes pb-depth-beacon{50%{box-shadow:0 0 0 6px #79c8ff08,0 0 24px #79c8ff55}}
    @media(hover:hover) and (pointer:fine){body.pb-depth-motion .home-kpi,body.pb-depth-motion .cw-trace-node{transition:transform .2s ease,box-shadow .2s ease}body.pb-depth-motion .home-kpi:hover{transform:perspective(900px) rotateX(2deg) translateY(-3px)}body.pb-depth-motion .cw-trace-node:hover{transform:translateY(-3px)}body.pb-depth-motion .home-action:hover .pb-icon{transform:perspective(250px) rotateY(20deg);transition:transform .2s ease}}
    body.pb-depth .btn:active,body.pb-depth .mini:active,body.pb-depth .home-action:active{box-shadow:inset 0 2px 6px #0008,0 1px 0 #7c9aba25}
    body.reduced-motion .home-hero::after,body.reduced-motion .home-kpi,body.reduced-motion .cw-trace-node,body.pb-depth-suspended .home-hero::after,body.pb-depth-suspended .home-status-dot{animation:none!important;transition:none!important;transform:none!important}
    @media(prefers-reduced-motion:reduce){body.pb-depth .home-hero::after,body.pb-depth .home-status-dot,body.pb-depth .home-kpi,body.pb-depth .cw-trace-node{animation:none!important;transition:none!important;transform:none!important}}
    @media(max-width:650px){body.pb-depth .home-hero::after{width:420px;height:420px;right:-150px}.pb-depth-controls{width:100%;margin-top:4px}.pb-depth-controls button{flex:1}}
  `;
  document.head.append(sheet);
  const preference=key=>{try{return localStorage.getItem('prfkt.presentation.'+key)!=='off';}catch{return true;}};
  let depth=preference('depth'),motion=preference('motion');
  const media=matchMedia('(prefers-reduced-motion: reduce)');
  const toggle=(name,value)=>{if(document.body.classList.contains(name)!==!!value) document.body.classList.toggle(name,!!value);};
  function apply(){
    const enabled=typeof session!=='undefined' && session?.ok;
    toggle('pb-depth',!!enabled && depth);
    toggle('pb-depth-motion',!!enabled && depth && motion && !media.matches && !document.body.classList.contains('reduced-motion'));
    toggle('pb-depth-suspended',document.hidden);
    for(const control of document.querySelectorAll('.pb-depth-controls')) {
      control.querySelector('[data-depth]').textContent=depth?'Depth: embossed':'Depth: original';control.querySelector('[data-depth]').setAttribute('aria-pressed',String(depth));
      const button=control.querySelector('[data-motion]'),reduced=media.matches || document.body.classList.contains('reduced-motion');
      button.textContent=reduced?'Motion: reduced':motion?'Motion: on':'Motion: paused';button.setAttribute('aria-pressed',String(motion&&!reduced));button.disabled=reduced;
    }
  }
  function mount(){
    for(const target of [document.querySelector('.home-actions'),document.querySelector('.cw-trace-actions')]) if(target&&!target.querySelector('.pb-depth-controls')) {
      const controls=document.createElement('div');controls.className='pb-depth-controls';controls.setAttribute('role','group');controls.setAttribute('aria-label','Dashboard appearance');
      controls.innerHTML='<button type="button" class="mini" data-depth>Depth</button><button type="button" class="mini" data-motion>Motion</button>';
      controls.onclick=e=>{const key=e.target.hasAttribute('data-depth')?'depth':e.target.hasAttribute('data-motion')?'motion':null;if(!key)return;if(key==='depth')depth=!depth;else motion=!motion;try{localStorage.setItem('prfkt.presentation.'+key,(key==='depth'?depth:motion)?'on':'off');}catch{}apply();};
      target.append(controls);
    }apply();
  }
  new MutationObserver(apply).observe(document.body,{attributes:true,attributeFilter:['class']});
  media.addEventListener('change',apply);document.addEventListener('visibilitychange',apply);window.addEventListener('project-byte-home-render',mount);window.addEventListener('hashchange',mount);mount();
})();

// Observatory permission inbox: native owner requests across conversations.
// The detached legacy panel cannot submit decisions or replace this inbox.
(() => {
  const legacy=document.querySelector('#pbObservatory .obs-approval-note');
  if (!legacy || !window.PRFKT_CODEX) return;
  const panel=document.createElement('section');panel.className='obs-approval-note';panel.id='codexPermissionInbox';panel.setAttribute('aria-label','Codex execution permissions');
  const mount=()=>{if(!panel.isConnected && legacy.isConnected)legacy.replaceWith(panel);};
  const css=document.createElement('style');css.textContent=`#codexPermissionInbox{min-width:0}#codexPermissionInbox .cw-permission{margin-top:14px}#codexPermissionInbox button{min-height:44px}#codexPermissionInbox pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}#codexPermissionInbox textarea{box-sizing:border-box;width:100%;min-height:65px}#codexPermissionInbox .cw-heading{gap:10px}#codexPermissionInbox .cw-inbox-context{overflow-wrap:anywhere}`;document.head.append(css);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const who=()=>JSON.stringify([accessKey,session?.subject,session?.level,session?.ok]);
  let identity='',blocked=null,generation=0,snapshot=null,busy=false,last=0,feedback='',signature='',clock=null;
  const notes=new Map(),decisions=new Map(),controllers=new Set();
  const owner=()=>who()!==blocked && !!accessKey && session?.ok && session.level===4;
  const valid=(id,gen)=>owner() && who()===id && generation===gen;
  const seconds=row=>clock&&Number.isFinite(row.expires_at)?Math.max(0,Math.ceil(row.expires_at-clock.time-(performance.now()-clock.at)/1000)):0;
  function clear(){generation++;for(const c of controllers)c.abort();controllers.clear();snapshot=null;busy=false;last=0;feedback='';signature='';clock=null;notes.clear();decisions.clear();panel.replaceChildren();}
  async function request(path,body,id,gen){
    const controller=new AbortController();controllers.add(controller);const timeout=setTimeout(()=>controller.abort(),15000);
    try {const data=await api(path,{signal:controller.signal,...(body===undefined?{}:{method:'POST',body:JSON.stringify(body)})});return valid(id,gen)?data:null;}
    finally{clearTimeout(timeout);controllers.delete(controller);}
  }
  function render(){
    if(!owner()){panel.innerHTML='<b>Execution permissions</b><p>Owner sign-in is required to review live tool requests.</p>';signature='';return;}
    const queue=snapshot?.execution_permissions,connected=snapshot?.configured && snapshot?.connected && queue?.capable;
    const rows=queue?.requests || [], history=queue?.history || [];
    const sig=JSON.stringify([connected,!!queue,rows,history,feedback,[...decisions]]);
    if(sig!==signature){
      signature=sig;
      panel.innerHTML=`<div class="cw-heading"><b>Execution permissions · ${connected?'Codex connected':queue?'Codex connection unavailable':'Checking Codex runner…'}</b><button type="button" class="mini" data-cw-inbox-refresh>Refresh</button></div>
        <p>Review actual Codex requests from every workspace conversation here. Approve once permits the exact requested action. Deny rejects that request. Code-review decisions remain separate; nothing merges automatically.</p>
        <p class="small">Isolated workspace edits and tests use the existing task authorization. Publishing an issue and pull request requires your approval of the frozen patch.</p>
        <p class="cw-feedback" role="status">${escape(feedback)}</p>
        <div class="cw-inbox-queue">${rows.map(row=>{const decision=decisions.get(row.id),enabled=connected && row.can_decide && seconds(row)>0 && !decision;
          return `<article class="cw-permission" data-cw-inbox-id="${escape(row.id)}"><h3>${escape(row.tool)}</h3><p class="cw-inbox-context">${escape(row.repository || row.project_key)} · ${escape(row.conversation_title)}</p><p>${escape(row.summary)}</p><details><summary>Exact requested action</summary><pre>${escape(JSON.stringify(row.arguments || {},null,2))}</pre></details>${row.arguments?.review_artifact?.id?`<button type="button" class="mini" data-cw-inbox-review="${escape(row.id)}">Read frozen patch</button><pre data-cw-inbox-patch="${escape(row.id)}" hidden></pre>`:''}<p class="small" data-cw-inbox-countdown="${escape(row.id)}"></p>${decision?`<p role="status">${escape(decision.state==='sending'?'Sending decision…':decision.state==='accepted'?'Decision accepted. Waiting for the runner result.':'Decision could not be confirmed. Refresh to inspect its state; this submission will not be repeated automatically.')}</p>`:`<label>Decision note (optional)<textarea class="field" maxlength="1000" data-cw-inbox-note="${escape(row.id)}" ${enabled?'':'disabled'}>${escape(notes.get(row.id)||'')}</textarea></label><div class="flex"><button type="button" class="btn primary" data-cw-inbox-decision="approve_once" data-permission="${escape(row.id)}" ${enabled?'':'disabled'}>Approve once</button><button type="button" class="btn" data-cw-inbox-decision="deny" data-permission="${escape(row.id)}" ${enabled?'':'disabled'}>Deny</button></div>`}</article>`;}).join('') || `<p>${connected?'No pending permission requests. New requests appear here automatically.':'Live requests are unavailable until the Codex workspace connection is verified.'}</p>`}</div>
        ${history.length?`<details class="cw-inbox-history"><summary>Recent request outcomes</summary>${history.map(row=>`<p class="cw-inbox-context">${escape(row.repository || row.project_key)} · ${escape(row.tool)} · ${escape(row.state)}${row.note?' · '+escape(row.note):''}</p>`).join('')}</details>`:''}`;
    }
    for(const row of rows){
      const element=[...panel.querySelectorAll('[data-cw-inbox-countdown]')].find(el=>el.dataset.cwInboxCountdown===row.id);
      if(element)element.textContent=row.state==='pending'?(seconds(row)>0?seconds(row)+'s remaining · '+(row.can_decide?'awaiting your decision':'request unavailable'):'Review window expired.'):row.state==='approved'?'Approved · awaiting runner dispatch':row.state==='dispatching'?'Runner is executing the approved action':row.state;
      if(!row.can_decide || seconds(row)<=0 || !connected)for(const b of panel.querySelectorAll('[data-permission]'))if(b.dataset.permission===row.id)b.disabled=true;
    }
  }
  async function refresh(force=false){
    const id=who();if(identity!==id){clear();identity=id;if(blocked!==id)blocked=null;}
    if(!owner()){render();return;}
    if(busy || (!force && (document.hidden || !document.querySelector('#agents')?.classList.contains('active') || Date.now()-last<3000))){render();return;}
    busy=true;const gen=generation;
    try {const data=await request('/api/codex-workspace',undefined,id,gen);if(!data)return;snapshot=data;if(data.execution_permissions?.capable===true)mount();
      const time=data.execution_permissions?.server_time;if(typeof time==='number' && Number.isFinite(time))clock={time,at:performance.now()};
      feedback='';
    }catch(error){if(valid(id,gen)){snapshot=null;feedback='Could not verify live permission requests: '+error.message;}}
    finally{if(valid(id,gen)){busy=false;last=Date.now();render();}}
  }
  panel.addEventListener('input',e=>{if(e.target.dataset.cwInboxNote)notes.set(e.target.dataset.cwInboxNote,e.target.value);});
  panel.addEventListener('click',async e=>{
    if(e.target.closest('[data-cw-inbox-refresh]')){void refresh(true);return;}
    const review=e.target.closest('[data-cw-inbox-review]');
    if(review && owner()) {
      const row=snapshot?.execution_permissions?.requests?.find(r=>r.id===review.dataset.cwInboxReview),aid=row?.arguments?.review_artifact?.id;
      if(!/^[0-9a-f]{32}$/.test(aid || ''))return;
      const id=who(),gen=generation;review.disabled=true;
      try {const data=await request('/api/codex-workspace/artifacts/'+aid,undefined,id,gen);if(!data)return;
        const pre=[...panel.querySelectorAll('[data-cw-inbox-patch]')].find(el=>el.dataset.cwInboxPatch===row.id);
        if(pre){pre.textContent=data.content_type==='text/plain' && typeof data.content==='string'?data.content:'Patch unavailable';pre.hidden=false;}
      }catch(error){if(valid(id,gen)){feedback='Could not load the frozen patch: '+error.message;render();}}
      finally{if(valid(id,gen))review.disabled=false;}
      return;
    }
    const button=e.target.closest('[data-cw-inbox-decision]');if(!button || !owner())return;
    const row=snapshot?.execution_permissions?.requests?.find(r=>r.id===button.dataset.permission);
    if(!row?.can_decide || !row.review_token || seconds(row)<=0 || decisions.has(row.id))return;
    const id=who(),gen=generation,record={request_id:crypto.randomUUID(),state:'sending'};
    decisions.set(row.id,record);render();
    try {const data=await request('/api/codex-workspace/permissions/'+encodeURIComponent(row.id)+'/decision',{request_id:record.request_id,review_token:row.review_token,decision:button.dataset.cwInboxDecision,note:notes.get(row.id)||''},id,gen);if(!data)return;decisions.set(row.id,{...record,state:'accepted'});feedback='Decision accepted. The runner outcome will appear here.';}
    catch(error){if(valid(id,gen)){decisions.set(row.id,{...record,state:'unknown'});feedback='Decision could not be confirmed: '+error.message;}}
    finally{if(valid(id,gen)){render();void refresh(true);}}
  });
  document.addEventListener('click',e=>{if(e.target.closest('#login,#logoutAllLocal') && owner()){blocked=who();clear();render();}else if(e.target.closest('[data-view],[data-home-go],[data-home-action]'))setTimeout(()=>void refresh(),0);},true);
  window.addEventListener('hashchange',()=>void refresh());window.addEventListener('project-byte-home-render',()=>void refresh());document.addEventListener('visibilitychange',()=>void refresh());
  setInterval(()=>void refresh(),750);render();
})();
