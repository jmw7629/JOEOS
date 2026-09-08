// Public-origin session coordination. Existing app credentials/roles are preserved.
(() => {
  const originalFetch=window.fetch.bind(window);
  const channel='BroadcastChannel' in window?new BroadcastChannel('project-byte-public-session'):null;
  let ending=false;
  function erase(){sessionStorage.removeItem('project_byte_access');sessionStorage.removeItem('pb_login_at');}
  async function signOut(){
    if(ending)return;ending=true;document.documentElement.style.visibility='hidden';const csrf=sessionStorage.getItem('project_byte_access')||'';erase();
    try{await originalFetch('/_gateway/logout',{method:'POST',headers:{'Content-Type':'application/json','X-Project-Byte-Gateway':'1','X-Access-Key':csrf},body:'{}',signal:AbortSignal.timeout(3000)});}catch{}
    channel?.postMessage('changed');
    location.replace('/signin');
  }
  function changed(){if(ending)return;ending=true;document.documentElement.style.visibility='hidden';erase();location.reload();}
  if(channel)channel.onmessage=()=>changed();
  document.addEventListener('click',event=>{
    if(event.target.closest('#login,#logoutAllLocal')){event.preventDefault();event.stopImmediatePropagation();void signOut();}
  },true);
  window.fetch=async(input,options)=>{
    const target=new URL(input instanceof Request?input.url:input,location.href);
    if(target.origin===location.origin&&(target.pathname.startsWith('/api/')||target.pathname==='/healthz')){
      const headers=new Headers(input instanceof Request?input.headers:undefined);
      new Headers(options?.headers).forEach((value,name)=>headers.set(name,value));
      const key=sessionStorage.getItem('project_byte_access');
      if(key&&!headers.has('X-Access-Key'))headers.set('X-Access-Key',key);
      options={...options,headers};
    }
    const response=await originalFetch(input,options);
    if(response.headers.get('X-Project-Byte-Session')==='changed')changed();
    else if(response.headers.get('X-Project-Byte-Session')==='expired')void signOut();
    return response;
  };
  async function check(){
    const key=sessionStorage.getItem('project_byte_access');
    if(!key){void signOut();return;}
    try{
      const response=await originalFetch('/_gateway/check',{headers:{'X-Access-Key':key}});
      if(response.headers.get('X-Project-Byte-Session')==='changed')changed();
      else if(response.status===401)void signOut();
    }catch{}
  }
  window.addEventListener('pageshow',()=>void check());
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)void check();});
  setInterval(check,30000);
  if(!sessionStorage.getItem('project_byte_access'))void signOut();
})();
