"""Hosted-only restrictions and provider chat; no inherited owner integrations."""
from pathlib import Path
import ipaddress, os, threading, time
from urllib.parse import urlsplit

def install(app,Handler,runtime,root):
    import ai_connections
    from enterprise_chat import Chat
    from codex_tasks_runtime import install as chat_routes
    # Re-use the pinned-address HTTPS transport, disallowing private endpoint
    # exceptions for Ollama, Hermes and custom adapters in hosted instances.
    ai_connections.PRIVATE_PROVIDERS=frozenset()
    original_allowed=ai_connections._address_allowed
    def allowed(address,private):
        ip=ipaddress.ip_address(address)
        if ip.version==6 and ip.ipv4_mapped:ip=ip.ipv4_mapped
        return str(ip) != '64.202.186.3' and original_allowed(address,False)
    ai_connections._address_allowed=allowed
    original_endpoint=ai_connections.validate_endpoint
    def endpoint(value,provider):
        result=original_endpoint(value,provider)
        if (urlsplit(result).hostname or '').lower().rstrip('.').endswith('.ts.net'):
            raise ai_connections.ConnectionError('Private workspace hosts cannot be used as AI providers')
        return result
    ai_connections.validate_endpoint=endpoint
    original_catalog=ai_connections.Manager.catalog
    def catalog(manager):
        result=original_catalog(manager)
        result['providers']=[p for p in result['providers'] if p['id']!='codex-chatgpt']
        return result
    ai_connections.Manager.catalog=catalog
    quota_lock=threading.Lock()
    quota={'checked':0,'allowed':True}
    # Legacy model POST paths may reference URLs. Restrict all execution entry
    # points, and all model calls go through the same managed HTTPS transport.
    class Hosted(Handler):
        def gate(self):
            if not super().gate():return False
            if self.command not in ('GET','HEAD'):
                with quota_lock:
                    if time.monotonic()-quota['checked']>10:
                        files=[p for base in ('state','private') for p in (Path(root)/base).rglob('*') if p.is_file()]
                        size=sum(p.stat().st_size for p in files)
                        disk=os.statvfs(root)
                        quota.update(checked=time.monotonic(),allowed=len(files)<10000 and size<512*1024*1024 and disk.f_bavail*disk.f_frsize>1024**3)
                    if not quota['allowed']:
                        self.sendj({'error':'Workspace storage limit reached. Contact your host.'},507);return False
            return True
        def do_POST(self):
            path=urlsplit(self.path).path
            if path.startswith('/api/tasks/') and path.endswith('/queue') or path in ('/api/team','/api/execution-permissions/decision') or path.startswith('/api/approvals/'):
                if not self.need(4):return
                return self.sendj({'error':'This private workspace has no execution runner or shared-team access connected'},409)
            return super().do_POST()
    controller=Chat(app,Hosted.ai_manager,Path(root)/'state/provider-chat.sqlite3')
    return chat_routes(app,Hosted,runtime.PUBLIC_FILES,controller=controller)
