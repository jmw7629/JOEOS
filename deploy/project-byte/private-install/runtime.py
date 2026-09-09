#!/usr/bin/env python3
"""Run one prepared private install on loopback, without inherited integrations."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent

def regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
        raise ValueError('Installation file must be a private regular file')
    return path.read_bytes()

def load_manifest():
    for name in ('app', 'state', 'state/uploads', 'private', 'private/home', 'private/config', 'private/cache', 'private/state', 'private/ai-credentials'):
        info = (ROOT / name).lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077: raise ValueError('Installation directory must be private and cannot be a symlink')
    manifest = json.loads(regular(ROOT / 'installation.json'))
    if manifest.get('schema_version') != 1 or len(manifest.get('installation_id', '')) != 32:
        raise ValueError('Invalid installation manifest')
    for group, base in (('assets', ROOT / 'app'), ('launchers', ROOT)):
        for name, expected in manifest[group].items():
            if Path(name).name != name: raise ValueError('Invalid asset path')
            if hashlib.sha256(regular(base / name)).hexdigest() != expected: raise ValueError('Installation code checksum mismatch')
    regular(ROOT / 'private' / 'owner.key')
    return manifest

def serve(port):
    os.umask(0o077)
    manifest = load_manifest()
    lock = os.open(ROOT / '.runtime.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise RuntimeError('Installation is running or locked for recovery')
    # No SSH agent, billing keys, owner Codex HOME, runtime bus or worker config
    # is inherited from the launching shell. Connections belong to this install.
    os.environ.clear()
    os.environ.update({'PATH': os.defpath, 'LANG': 'C.UTF-8', 'HOME': str(ROOT / 'private/home'),
                       'XDG_CONFIG_HOME': str(ROOT / 'private/config'), 'XDG_CACHE_HOME': str(ROOT / 'private/cache'),
                       'XDG_STATE_HOME': str(ROOT / 'private/state'), 'PYTHONDONTWRITEBYTECODE': '1'})
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT / 'app'))
    import backend as app
    app.DB = ROOT / 'state/kanban.db'; app.SECRET = ROOT / 'private/owner.key'; app.UPLOADS = ROOT / 'state/uploads'
    app.BRIDGES = {}; app.BRIDGE_STATE = {}
    app.DEFAULT_SETTINGS['general']['timezone'] = 'UTC'
    app.DEFAULT_SETTINGS['ai']['default_agent'] = ''
    original_auth = app.auth
    def auth(headers, ip=''):
        actor = original_auth(headers, ip)
        if actor.get('subject') == 'owner': actor['name'] = manifest['owner_name']
        return actor
    app.auth = auth
    # A missing key must never silently generate a new identity on a GET.
    def owner_secret(): return regular(ROOT / 'private/owner.key').decode().strip()
    app.owner_secret = owner_secret
    import runtime_server as runtime
    runtime.BRIDGE_SERVICES = {}; runtime.EXTERNAL_REVIEW_REPOS = (); runtime.OBS_REPOS = ()
    runtime.WORKSPACE_PROFILE_DEFAULTS = manifest['profile']
    import ai_runtime
    manager_type = ai_runtime.Manager
    ai_runtime.Manager = lambda application, codex: manager_type(application, codex, secret_dir=ROOT / 'private/ai-credentials')
    app.init_db()
    def health():
        database = runtime._sqlite_health()
        ok = database.get('state') == 'healthy'
        return {'ok': ok, 'operational': False, 'status': 'core-healthy' if ok else 'degraded', 'version': 4,
                'ai_ops': False, 'ai_chat_ready': False, 'settings': ok, 'notifications': True,
                'warnings': ['execution-not-configured'], 'components': {'sqlite': database,
                'sync': {'state': 'disabled', 'last_attempt': 0, 'last_ok': 0}, 'bridges': {}, 'models': runtime._model_health()}}
    runtime.health_snapshot = health
    class Handler(runtime.SafeHandler):
        def gate(self):
            path = unquote(urlsplit(self.path).path)
            if path == '/healthz' or path == '/api/workspace-profile' or path in runtime.PUBLIC_FILES: return True
            if not self.who().get('ok'):
                self.sendj({'error': 'Sign in to this private workspace'}, 401)
                return False
            if self.command not in ('GET', 'HEAD'):
                origin = self.headers.get('Origin')
                if origin and urlsplit(origin).netloc != self.headers.get('Host'):
                    self.sendj({'error': 'Origin does not match this workspace'}, 403)
                    return False
            return True
        def do_GET(self):
            if self.gate(): return super().do_GET()
        def do_HEAD(self):
            if self.gate(): return super().do_HEAD()
        def do_POST(self):
            if self.gate(): return super().do_POST()
        def do_PATCH(self):
            if self.gate(): return super().do_PATCH()
        def do_DELETE(self):
            if self.gate(): return super().do_DELETE()
        def log_message(self, *_args): pass
    from http.server import ThreadingHTTPServer
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    print(json.dumps({'state': 'listening', 'url': f'http://127.0.0.1:{server.server_port}/', 'installation_id': manifest['installation_id']}), flush=True)
    try: server.serve_forever()
    finally: server.server_close(); os.close(lock)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args()
    serve(args.port)
