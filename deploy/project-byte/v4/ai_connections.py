"""Owner-managed AI connections; credentials never enter model rows or responses.

HTTP protocols are implemented with the standard library. Codex authentication and
completion belong to the injected official-runtime adapter, never this module.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import ssl
import stat
import threading
import time
from urllib.parse import quote, unquote, urlsplit

MAX_RESPONSE = 2 * 1024 * 1024
MAX_REQUEST = 256 * 1024
DISCOVERY_TIMEOUT = 12
CHAT_TIMEOUT = 120
MODEL_RE = re.compile(r"[A-Za-z0-9_.:/@+\-]{1,140}")
ENV_RE = re.compile(r"(?:PROJECT_BYTE_AI_[A-Z0-9_]{1,80}|PROJECT_BYTE_OPENAI_API_KEY)")
PRIVATE_PROVIDERS = frozenset({'ollama', 'openai-compatible', 'openclaw', 'hermes'})
EXTERNAL_PROVIDERS = frozenset({'codex-chatgpt', 'openclaw', 'hermes'})
DNS_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix='pb-ai-dns')
DNS_SLOTS = threading.BoundedSemaphore(8)


class ConnectionError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.http_status = status
        self.status = status


def _provider(key, label, protocol, endpoint, docs, kind='api', notes='', discovery=True):
    modes = ['subscription'] if kind == 'subscription' else ['api_key', 'server_env']
    if key in ('ollama', 'openai-compatible'):
        modes = ['none', 'api_key', 'server_env']
    return {'id':key, 'label':label, 'kind':kind, 'auth_modes':modes, 'protocol':protocol,
            'default_endpoint':endpoint, 'docs_url':docs, 'notes':notes, 'discovery':discovery}


PROVIDERS = [
    _provider('codex-chatgpt', 'Codex · ChatGPT subscription', 'codex', '',
              'https://developers.openai.com/codex/auth/', 'subscription',
              'Uses the official Codex runtime signed in on this server. Owner only; a ChatGPT subscription is not an API key.'),
    _provider('openai-responses', 'OpenAI API', 'responses', 'https://api.openai.com/v1',
              'https://platform.openai.com/docs/api-reference/responses'),
    _provider('anthropic', 'Anthropic', 'anthropic', 'https://api.anthropic.com/v1',
              'https://platform.claude.com/docs/en/api/messages'),
    _provider('google-gemini', 'Google Gemini', 'gemini', 'https://generativelanguage.googleapis.com/v1beta',
              'https://ai.google.dev/api/generate-content'),
    _provider('openai-compatible', 'OpenAI-compatible server', 'chat-completions', '',
              'https://platform.openai.com/docs/api-reference/chat', 'custom',
              'Enter the server API base URL and its model ID. Compatibility depends on that server.'),
    _provider('ollama', 'Ollama', 'ollama', 'http://127.0.0.1:11434',
              'https://docs.ollama.com/api/tags', 'local', 'Discover installed models from your configured server.'),
    _provider('openclaw', 'OpenClaw agent gateway', 'chat-completions', 'http://127.0.0.1:18789/v1',
              'https://docs.openclaw.ai/gateway/openai-http-api', 'agent',
              'Owner only. Requires an existing gateway with its HTTP API enabled and operator credential. Discovery lists agent targets; its own tool permissions apply.'),
    _provider('hermes', 'Hermes agent server', 'chat-completions', 'http://127.0.0.1:8642/v1',
              'https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/', 'agent',
              'Owner only. Requires an existing Hermes API server and its API_SERVER_KEY. Discovery lists agent profiles; its own tool permissions apply.'),
    _provider('xai', 'xAI', 'responses', 'https://api.x.ai/v1', 'https://docs.x.ai/docs/api-reference'),
    _provider('deepseek', 'DeepSeek', 'chat-completions', 'https://api.deepseek.com', 'https://api-docs.deepseek.com/'),
    _provider('mistral', 'Mistral', 'chat-completions', 'https://api.mistral.ai/v1', 'https://docs.mistral.ai/api/'),
    _provider('openrouter', 'OpenRouter', 'chat-completions', 'https://openrouter.ai/api/v1', 'https://openrouter.ai/docs/api-reference/overview'),
    _provider('groq', 'Groq', 'chat-completions', 'https://api.groq.com/openai/v1', 'https://console.groq.com/docs/openai'),
    _provider('together', 'Together AI', 'chat-completions', 'https://api.together.xyz/v1', 'https://docs.together.ai/reference/chat-completions-1'),
    _provider('fireworks', 'Fireworks AI', 'chat-completions', 'https://api.fireworks.ai/inference/v1',
              'https://docs.fireworks.ai/api-reference/introduction', notes='Enter the model ID from your account; management discovery uses a separate account-scoped API.', discovery=False),
    _provider('perplexity', 'Perplexity', 'chat-completions', 'https://api.perplexity.ai',
              'https://docs.perplexity.ai/api-reference/chat-completions-post', notes='Enter a model ID from your provider account.', discovery=False),
    _provider('nvidia', 'NVIDIA NIM', 'chat-completions', 'https://integrate.api.nvidia.com/v1', 'https://docs.api.nvidia.com/nim/reference/llm-apis'),
    _provider('cohere', 'Cohere', 'cohere', 'https://api.cohere.com', 'https://docs.cohere.com/reference/chat'),
]
REGISTRY = {p['id']:p for p in PROVIDERS}
AGENTS = [
    {'id':'hermes', 'label':'Hermes', 'provider':'hermes', 'notes':'Connect your existing Hermes server; this does not install or resume an agent.'},
    {'id':'openclaw', 'label':'OpenClaw', 'provider':'openclaw', 'notes':'Connect your existing OpenClaw gateway; its execution permissions remain external.'},
]
SUBSCRIPTION_RUNTIMES = [
    {'id':'claude-code', 'label':'Claude Code native sign-in',
     'docs_url':'https://code.claude.com/docs/en/legal-and-compliance',
     'notes':'Uses each user\'s sign-in in the unmodified official Claude Code runtime. PROJECT_BYTE does not collect Claude subscription tokens.'},
    {'id':'gemini-cli', 'label':'Gemini CLI · Google account',
     'docs_url':'https://geminicli.com/docs/get-started/authentication/',
     'notes':'Google-account and eligible Google AI plan access belong to the official Gemini CLI. Gemini API keys use separate API billing.'},
    {'id':'kimi-code', 'label':'Kimi Code managed sign-in',
     'docs_url':'https://github.com/MoonshotAI/kimi-code/blob/main/docs/en/configuration/providers.md',
     'notes':'Kimi Code manages its native account sign-in. Moonshot platform API keys are a separate provider connection.'},
    {'id':'copilot-cli', 'label':'GitHub Copilot CLI native sign-in',
     'docs_url':'https://github.com/github/copilot-sdk/blob/main/docs/auth/authenticate.md',
     'notes':'Requires the official Copilot runtime and the correct user\'s account. BYOK uses separate provider billing.'},
]


def require_owner(actor):
    if not isinstance(actor, dict) or actor.get('role') != 'owner' or actor.get('level') != 4 or actor.get('subject') != 'owner':
        raise ConnectionError('Owner access is required for AI connections', 403)


def _string(value, name, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ConnectionError('Invalid '+name)
    value = value.strip()
    if required and not value:
        raise ConnectionError(name+' is required')
    return value


def _model_id(value):
    value = _string(value, 'model ID', 140, True)
    if not MODEL_RE.fullmatch(value):
        raise ConnectionError('Invalid model ID')
    return value


def _key(value):
    if not isinstance(value, str) or not 8 <= len(value) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ConnectionError('Invalid API credential')
    return value


def _environment(name):
    if not isinstance(name, str) or not ENV_RE.fullmatch(name):
        raise ConnectionError('Use a configured PROJECT_BYTE_AI_ environment variable')
    value = os.getenv(name, '')
    if not value:
        raise ConnectionError('The selected server credential is not configured', 503)
    return _key(value)


def _address_allowed(address, private):
    ip = ipaddress.ip_address(address)
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if str(ip) in ('100.100.100.200', '168.63.129.16', 'fd00:ec2::254'):
        return False
    if ip.is_loopback:
        return private
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved:
        return False
    if ip.is_global:
        return True
    return private and (ip.is_loopback or ip in ipaddress.ip_network('10.0.0.0/8') or
                       ip in ipaddress.ip_network('172.16.0.0/12') or ip in ipaddress.ip_network('192.168.0.0/16') or
                       ip in ipaddress.ip_network('100.64.0.0/10') or ip in ipaddress.ip_network('fc00::/7'))


def validate_endpoint(endpoint, provider):
    endpoint = _string(endpoint, 'endpoint', 600, True).rstrip('/')
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise ConnectionError('Invalid endpoint') from None
    host = parsed.hostname or ''
    if (parsed.scheme not in ('http', 'https') or not host or parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment or '\\' in endpoint or '%' in host or
            any(c.isspace() for c in endpoint) or not (port is None or 1 <= port <= 65535)):
        raise ConnectionError('Use an HTTP(S) API base URL without credentials, query parameters or fragments')
    if host.lower().rstrip('.') in ('metadata.google.internal', 'metadata.goog', 'instance-data', 'metadata.azure.internal'):
        raise ConnectionError('Cloud metadata endpoints are not allowed')
    if any(segment in ('.','..') for segment in unquote(parsed.path).split('/')) or not parsed.path.isascii():
        raise ConnectionError('Invalid endpoint path')
    if parsed.scheme == 'http' and provider not in PRIVATE_PROVIDERS:
        raise ConnectionError('This provider requires HTTPS')
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and not _address_allowed(str(literal), provider in PRIVATE_PROVIDERS):
        raise ConnectionError('This network address is not allowed')
    return endpoint


def resolve_addresses(host, port, timeout=5):
    if not DNS_SLOTS.acquire(blocking=False):
        raise ConnectionError('AI endpoint resolution is busy', 503)
    try:future = DNS_POOL.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    except RuntimeError:
        DNS_SLOTS.release()
        raise ConnectionError('AI endpoint resolution is unavailable',503) from None
    future.add_done_callback(lambda _:DNS_SLOTS.release())
    try:
        return list(dict.fromkeys(x[4][0] for x in future.result(timeout=min(5,timeout))))
    except (OSError, concurrent.futures.TimeoutError):
        raise ConnectionError('AI endpoint could not be resolved', 502) from None


def http_json(endpoint, path, provider, headers, payload=None, timeout=DISCOVERY_TIMEOUT, resolver=None):
    if not isinstance(timeout,(int,float)) or not 0<timeout<=CHAT_TIMEOUT:
        raise ConnectionError('Invalid AI request deadline')
    deadline=time.monotonic()+timeout
    connection=None
    def remaining():
        left=deadline-time.monotonic()
        if left<=0:raise ConnectionError('AI provider request timed out',502)
        return left
    def expire():
        try:connection.sock.shutdown(socket.SHUT_RDWR)
        except (OSError,AttributeError):pass
    timer=threading.Timer(timeout,expire);timer.daemon=True;timer.start()
    try:
        endpoint = validate_endpoint(endpoint, provider)
        parsed = urlsplit(endpoint)
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        addresses = (resolve_addresses(parsed.hostname,port,remaining()) if resolver is None
                     else resolver(parsed.hostname,port))
        remaining()
        private = provider in PRIVATE_PROVIDERS
        if not addresses or any(not _address_allowed(ip, private) for ip in addresses):
            raise ConnectionError('AI endpoint resolves to a prohibited network address')
        if parsed.scheme == 'http' and any(ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ConnectionError('Public AI endpoints require HTTPS')
        body = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        if body is not None and len(body) > MAX_REQUEST:
            raise ConnectionError('AI request is too large')
        connection = http.client.HTTPConnection(parsed.hostname,port,timeout=remaining())
        # Pin the validated address; no second DNS lookup and no proxy environment.
        connection.sock = socket.create_connection((addresses[0],port),timeout=min(remaining(),10))
        if parsed.scheme == 'https':
            context = ssl.create_default_context()
            context.set_alpn_protocols(['http/1.1'])
            # Assign before the handshake so the same hard timer can interrupt it.
            connection.sock = context.wrap_socket(connection.sock,server_hostname=parsed.hostname,do_handshake_on_connect=False)
            connection.sock.settimeout(remaining())
            connection.sock.do_handshake()
        connection.sock.settimeout(remaining())
        request_headers = {'Accept':'application/json', 'Accept-Encoding':'identity', 'Content-Type':'application/json', **headers}
        connection.request('GET' if body is None else 'POST', (parsed.path.rstrip('/')+path) or '/', body, request_headers)
        response = connection.getresponse()
        if 300 <= response.status < 400:
            raise ConnectionError('AI endpoint redirected; configure its final API base URL', 502)
        if not 200 <= response.status < 300:
            raise ConnectionError('AI provider returned HTTP '+str(response.status), 502)
        content_type = response.getheader('Content-Type','').split(';')[0].strip().lower()
        if content_type != 'application/json' and not content_type.endswith('+json'):
            raise ConnectionError('AI provider returned a non-JSON response', 502)
        raw = response.read(MAX_RESPONSE+1)
        if len(raw) > MAX_RESPONSE:
            raise ConnectionError('AI response is too large', 502)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ConnectionError('AI provider returned an invalid response', 502)
        remaining()
        return data
    except ConnectionError:
        raise
    except (OSError, ValueError, TypeError, RecursionError, http.client.HTTPException):
        raise ConnectionError('AI provider request failed or timed out', 502) from None
    finally:
        timer.cancel()
        if connection:connection.close()


class SecretStore:
    def __init__(self, directory, app_root):
        self.directory = Path(directory)
        if self.directory.resolve().is_relative_to(Path(app_root).resolve()):
            raise ConnectionError('AI credential storage must be outside the public app directory', 503)

    def _directory(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.getuid():
            raise ConnectionError('Private AI credential storage is unavailable', 503)

    def put(self, value):
        self._directory();value=_key(value);ref=secrets.token_hex(24)
        fd=os.open(self.directory/ref, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd,'w') as stream:
            stream.write(value);stream.flush();os.fsync(stream.fileno())
        return ref

    def get(self, ref):
        if not isinstance(ref,str) or not re.fullmatch(r'[a-f0-9]{48}',ref):
            raise ConnectionError('AI credential is unavailable',503)
        self._directory()
        try:
            fd=os.open(self.directory/ref,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as stream:
                info=os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode)!=0o600 or info.st_uid!=os.getuid():
                    raise ConnectionError('AI credential is unavailable',503)
                raw=stream.read(8193)
            return _key(raw.decode())
        except (OSError,UnicodeError):
            raise ConnectionError('AI credential is unavailable',503) from None

    def delete(self, ref):
        if ref and re.fullmatch(r'[a-f0-9]{48}',ref):
            try:(self.directory/ref).unlink()
            except FileNotFoundError:pass


class Manager:
    def __init__(self, app, codex, secret_dir=None, transport=http_json):
        self.app=app;self.codex=codex;self.transport=transport;self.lock=threading.RLock()
        identity=hashlib.sha256(str(Path(app.ROOT).resolve()).encode()).hexdigest()[:16]
        directory=secret_dir or Path(os.getenv('XDG_STATE_HOME', str(Path.home()/'.local/state')))/'project-byte'/identity/'ai-credentials'
        self.secrets=SecretStore(directory,app.ROOT)
        with self.app.con() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS ai_connections (
                model_key TEXT PRIMARY KEY REFERENCES models(model_key), auth_mode TEXT NOT NULL,
                secret_ref TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL,
                agent_key TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL)''')

    def catalog(self):
        runtimes=[{**r,'adapter_available':False,'connectable':False,'state':'adapter_not_configured','auth_mode':'native-signin'} for r in SUBSCRIPTION_RUNTIMES]
        return {'providers':json.loads(json.dumps(PROVIDERS)), 'agents':json.loads(json.dumps(AGENTS)),
                'subscription_runtimes':runtimes}

    def managed(self, model_key):
        with self.app.con() as db:
            return db.execute('SELECT 1 FROM ai_connections WHERE model_key=?',(model_key,)).fetchone() is not None

    def _row(self, model_key):
        with self.app.con() as db:
            row=db.execute('SELECT * FROM models WHERE model_key=?',(model_key,)).fetchone()
        if row is None:raise ConnectionError('AI connection was not found',404)
        return dict(row)

    def _metadata(self, model_key):
        with self.app.con() as db:
            row=db.execute('SELECT * FROM ai_connections WHERE model_key=?',(model_key,)).fetchone()
        return dict(row) if row else None

    def connections(self, actor):
        require_owner(actor)
        with self.app.con() as db:
            rows=db.execute('''SELECT m.model_key,m.display_name,m.provider,m.endpoint,m.model_name,m.enabled,m.last_status,
                m.secret_env,a.auth_mode,a.secret_ref,a.agent_key FROM models m JOIN ai_connections a USING(model_key)
                ORDER BY m.display_name''').fetchall()
        return [{**{k:dict(r)[k] for k in ('model_key','display_name','provider','endpoint','model_name','enabled','last_status','auth_mode','agent_key')},
                 'owner_only':True,'credentials_configured':bool(r['secret_ref'] or r['secret_env'] or r['auth_mode'] in ('none','subscription'))} for r in rows]

    def _config(self, body, need_model=False, allow_saved=False):
        if not isinstance(body,dict):raise ConnectionError('Invalid AI connection request')
        fields={'provider','endpoint','model_name','display_name','model_key','api_key','secret_env','auth_mode','agent_template'}
        if set(body)-fields-{'set_default'}:raise ConnectionError('Unknown AI connection field')
        if any(field in body and not isinstance(body[field],str) for field in fields):
            raise ConnectionError('Invalid AI connection field type')
        if 'set_default' in body and not isinstance(body['set_default'],bool):
            raise ConnectionError('Invalid default selection')
        provider=body.get('provider')
        if provider not in REGISTRY:raise ConnectionError('Unknown AI provider')
        spec=REGISTRY[provider]
        endpoint=body.get('endpoint') or spec['default_endpoint']
        if provider=='codex-chatgpt':
            if endpoint or body.get('api_key') or body.get('secret_env'):
                raise ConnectionError('Codex uses its official server sign-in; do not paste subscription credentials')
        else:endpoint=validate_endpoint(endpoint,provider)
        api_key=body.get('api_key') or '';env=body.get('secret_env') or ''
        if api_key and env:raise ConnectionError('Choose an API key or a server environment credential')
        mode='subscription' if provider=='codex-chatgpt' else 'none'
        if api_key:_key(api_key);mode='api_key'
        if env:_environment(env);mode='server_env'
        if mode not in spec['auth_modes'] and not allow_saved:
            raise ConnectionError('An API credential is required for this provider')
        if body.get('auth_mode') and body['auth_mode']!=mode and not allow_saved:
            raise ConnectionError('Credential does not match the selected authentication mode')
        model=_model_id(body.get('model_name','')) if need_model else ''
        return {'provider':provider,'endpoint':endpoint,'model_name':model,'api_key':api_key,'secret_env':env,'auth_mode':mode}

    def _credential(self, model, metadata=None):
        if metadata and metadata['secret_ref']:
            return self.secrets.get(metadata['secret_ref'])
        if model.get('secret_env'):
            return _environment(model['secret_env'])
        return ''

    def _headers(self, provider, credential):
        if provider=='anthropic':return {'x-api-key':credential,'anthropic-version':'2023-06-01'}
        if provider=='google-gemini':return {'x-goog-api-key':credential}
        return {'Authorization':'Bearer '+credential} if credential else {}

    def discover(self, body, actor=None):
        require_owner(actor);config=self._config(body);provider=config['provider'];spec=REGISTRY[provider]
        if provider=='codex-chatgpt':
            if not self.codex or not self.codex.status().get('connected'):
                raise ConnectionError('Sign in to the official Codex runtime on this server first',409)
            items=self.codex.models()
            if not isinstance(items,list):raise ConnectionError('Codex returned an invalid model catalog',502)
            models=self._model_list(items,'id')
            return {'provider':provider,'models':models,'empty':not models,'truncated':len(items)>500}
        if not spec['discovery']:
            raise ConnectionError('This provider requires a model ID from its documentation',400)
        credential=config['api_key'] or (_environment(config['secret_env']) if config['secret_env'] else '')
        protocol=spec['protocol'];path='/api/tags' if protocol=='ollama' else '/v1/models' if protocol=='cohere' else '/models'
        if protocol=='anthropic':path+='?limit=500'
        if protocol=='gemini':path+='?pageSize=500'
        data=self.transport(config['endpoint'],path,provider,self._headers(provider,credential),timeout=DISCOVERY_TIMEOUT)
        field='models' if protocol in ('ollama','gemini','cohere') else 'data'
        items=data.get(field)
        if not isinstance(items,list):raise ConnectionError('AI provider returned an invalid model catalog',502)
        if protocol=='gemini':items=[x for x in items if isinstance(x,dict) and 'generateContent' in x.get('supportedGenerationMethods',[])]
        if protocol=='cohere':items=[x for x in items if isinstance(x,dict) and ('chat' in x.get('endpoints',[]) or not x.get('endpoints'))]
        models=self._model_list(items,'name' if protocol in ('ollama','gemini','cohere') else 'id')
        return {'provider':provider,'models':models,'empty':not models,
                'truncated':len(items)>500 or bool(data.get('has_more') or data.get('nextPageToken') or data.get('next_page_token'))}

    def _model_list(self, items, field):
        result=[];seen=set()
        for item in items[:500]:
            if not isinstance(item,dict):raise ConnectionError('AI provider returned an invalid model catalog',502)
            value=item.get(field)
            if not isinstance(value,str) or not MODEL_RE.fullmatch(value):
                raise ConnectionError('AI provider returned an invalid model ID',502)
            if value in seen:continue
            seen.add(value)
            name=item.get('display_name') or item.get('displayName') or item.get('name') or value
            if not isinstance(name,str) or len(name)>200 or any(ord(c)<32 for c in name):name=value
            result.append({'id':value,'name':name})
        return result

    def connect(self, body, actor):
        require_owner(actor);config=self._config(body,True)
        model_key=body.get('model_key') or 'connection/'+config['provider']+'/'+secrets.token_hex(6)
        model_key=_model_id(model_key)
        name=_string(body.get('display_name') or REGISTRY[config['provider']]['label']+' · '+config['model_name'],'display name',200,True)
        agent_template=body.get('agent_template') or ''
        if agent_template and (agent_template not in ('hermes','openclaw') or agent_template!=config['provider']):
            raise ConnectionError('Agent template must match its connected runtime')
        if config['provider']=='codex-chatgpt':
            catalog=self.discover({'provider':'codex-chatgpt'},actor)
            if config['model_name'] not in {model['id'] for model in catalog['models']}:
                raise ConnectionError('Select a model available from the signed-in Codex runtime',409)
        if not isinstance(body.get('set_default',False),bool):raise ConnectionError('Invalid default selection')
        ref='';agent_key=''
        try:
            with self.lock,self.app.con() as db:
                if db.execute('SELECT 1 FROM models WHERE model_key=?',(model_key,)).fetchone():
                    raise ConnectionError('That model key already exists; existing connections were preserved',409)
                if config['api_key']:ref=self.secrets.put(config['api_key'])
                ts=self.app.now()
                db.execute('''INSERT INTO models(model_key,display_name,provider,endpoint,model_name,secret_env,
                    capabilities,enabled,local,notes,last_status,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (model_key,name,config['provider'],config['endpoint'],config['model_name'],config['secret_env'],
                     '["chat"]',1,int(config['provider'] in PRIVATE_PROVIDERS),REGISTRY[config['provider']]['notes'],'not-tested',ts))
                if agent_template:
                    agent_key='connection-'+agent_template+'-'+secrets.token_hex(6)
                    db.execute('''INSERT INTO agents(agent_key,name,role,description,system_prompt,preferred_model,
                        fallback_models,capabilities,opencode_agent,memory_enabled,learning_enabled,enabled,updated_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',(agent_key,REGISTRY[agent_template]['label'],'External agent',
                        'Owner-connected external runtime; its own tool permissions apply.','',model_key,'[]','["chat"]','',0,0,1,ts))
                db.execute('INSERT INTO ai_connections VALUES(?,?,?,?,?,?)',(model_key,config['auth_mode'],ref,'owner',agent_key,ts))
                if body.get('set_default'):self._set_default(db,model_key,actor)
                self.app.audit(db,'ai_connection_added','','',name,actor['name'])
        except Exception:
            if ref:self.secrets.delete(ref)
            raise
        return {'model_key':model_key,'display_name':name,'provider':config['provider'],'model_name':config['model_name'],
                'agent_key':agent_key,'default_set':body.get('set_default',False),'owner_only':True}

    def _set_default(self, db, key, actor):
        row=db.execute('SELECT data FROM user_settings WHERE subject=?',('owner',)).fetchone()
        try:settings=json.loads(row['data']) if row else {}
        except (ValueError,TypeError):raise ConnectionError('Existing owner settings could not be read',503) from None
        if not isinstance(settings,dict) or not isinstance(settings.get('ai',{}),dict):
            raise ConnectionError('Existing owner settings could not be read',503)
        settings.setdefault('ai',{})['default_model']=key
        db.execute('INSERT INTO user_settings(subject,data,updated_at) VALUES(?,?,?) ON CONFLICT(subject) DO UPDATE SET data=excluded.data,updated_at=excluded.updated_at',
                   ('owner',json.dumps(settings),self.app.now()))
        self.app.audit(db,'ai_default_changed','','',key,actor['name'])

    def set_default(self, key, actor):
        require_owner(actor);key=_model_id(key)
        with self.lock,self.app.con() as db:
            row=db.execute('SELECT enabled FROM models WHERE model_key=?',(key,)).fetchone()
            if row is None or not row['enabled']:raise ConnectionError('Choose an enabled model',409)
            self._set_default(db,key,actor)
        return {'model_key':key,'default_model_key':key,'subject':'owner'}

    def disconnect(self, key, actor):
        require_owner(actor);key=_model_id(key)
        with self.lock,self.app.con() as db:
            meta=db.execute('SELECT * FROM ai_connections WHERE model_key=?',(key,)).fetchone()
            if meta is None:raise ConnectionError('Only managed connections can be disconnected here',409)
            db.execute("UPDATE models SET enabled=0,last_status='disconnected',updated_at=? WHERE model_key=?",(self.app.now(),key))
            db.execute("UPDATE ai_connections SET secret_ref='' WHERE model_key=?",(key,))
            if meta['agent_key']:db.execute('UPDATE agents SET enabled=0,updated_at=? WHERE agent_key=?',(self.app.now(),meta['agent_key']))
            self.app.audit(db,'ai_connection_disconnected','','',key,actor['name'])
        self.secrets.delete(meta['secret_ref'])
        return {'model_key':key,'enabled':False,'owner_only':True}

    def call(self, model, messages, actor=None):
        if isinstance(model,str):model=self._row(model)
        if not isinstance(model,dict) or not model.get('enabled'):raise ConnectionError('Model is not enabled',409)
        provider=model.get('provider')
        if not isinstance(provider,str) or provider not in REGISTRY:raise ConnectionError('Unsupported AI provider')
        metadata=self._metadata(_model_id(model.get('model_key','')))
        if metadata or provider in EXTERNAL_PROVIDERS:require_owner(actor)
        if not isinstance(messages,list) or not 1<=len(messages)<=64:raise ConnectionError('Invalid AI messages')
        cleaned=[]
        for message in messages:
            if not isinstance(message,dict) or message.get('role') not in ('system','developer','user','assistant'):
                raise ConnectionError('Unsupported AI message role')
            content=message.get('content')
            if not isinstance(content,str) or not content or len(content)>64000:raise ConnectionError('Invalid AI message content')
            cleaned.append({'role':message['role'],'content':content})
        if len(json.dumps(cleaned).encode())>MAX_REQUEST:raise ConnectionError('AI request is too large')
        start=time.monotonic()
        if provider=='codex-chatgpt':
            if not self.codex or not self.codex.status().get('connected'):raise ConnectionError('Codex is not signed in',409)
            text,latency=self.codex.complete(cleaned,model=model.get('model_name',''))
            if not isinstance(text,str) or not text.strip():raise ConnectionError('Codex returned no text',502)
            return text.strip(),latency
        name=model.get('model_name') or ''
        if provider=='ollama' and not name:
            data=self.transport(model['endpoint'],'/api/tags',provider,{},timeout=DISCOVERY_TIMEOUT)
            items=data.get('models')
            if not isinstance(items,list) or not items:raise ConnectionError('No Ollama model is installed or reachable',409)
            name=self._model_list(items,'name')[0]['id']
        name=_model_id(name)
        credential=self._credential(model,metadata)
        if not credential and 'none' not in REGISTRY[provider]['auth_modes']:
            raise ConnectionError('AI credential is unavailable',503)
        protocol=REGISTRY[provider]['protocol'];headers=self._headers(provider,credential)
        payload={'model':name,'messages':cleaned,'stream':False};path='/chat/completions'
        if protocol=='responses':
            path='/responses';payload={'model':name,'input':cleaned,'store':False}
        elif protocol=='anthropic':
            path='/messages';payload={'model':name,'max_tokens':4096,'messages':[m for m in cleaned if m['role'] not in ('system','developer')]}
            system='\n\n'.join(m['content'] for m in cleaned if m['role'] in ('system','developer'))
            if system:payload['system']=system
        elif protocol=='gemini':
            path='/models/'+quote(name.removeprefix('models/'),safe='')+':generateContent'
            payload={'contents':[{'role':'model' if m['role']=='assistant' else 'user','parts':[{'text':m['content']}]} for m in cleaned if m['role'] not in ('system','developer')]}
            system='\n\n'.join(m['content'] for m in cleaned if m['role'] in ('system','developer'))
            if system:payload['systemInstruction']={'parts':[{'text':system}]}
        elif protocol=='ollama':path='/api/chat'
        elif protocol=='cohere':path='/v2/chat'
        data=self.transport(model['endpoint'],path,provider,headers,payload,timeout=CHAT_TIMEOUT)
        try:
            if protocol=='responses':
                text=data.get('output_text') or '\n'.join(c.get('text','') for item in data.get('output',[]) for c in item.get('content',[]) if c.get('type')=='output_text')
            elif protocol=='anthropic':text='\n'.join(c.get('text','') for c in data.get('content',[]) if c.get('type')=='text')
            elif protocol=='gemini':text='\n'.join(c.get('text','') for c in data['candidates'][0]['content']['parts'] if not c.get('thought'))
            elif protocol=='ollama':text=data['message']['content']
            elif protocol=='cohere':text='\n'.join(c.get('text','') for c in data['message']['content'] if c.get('type')=='text')
            else:text=data['choices'][0]['message']['content']
        except (KeyError,TypeError,AttributeError,IndexError):raise ConnectionError('AI provider returned an invalid completion',502) from None
        if not isinstance(text,str) or not text.strip():raise ConnectionError('AI provider returned no text',502)
        if len(text)>MAX_RESPONSE:raise ConnectionError('AI completion is too large',502)
        return text.strip(),int((time.monotonic()-start)*1000)
