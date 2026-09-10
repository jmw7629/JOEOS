"""HTTP integration for private AI connections without changing legacy app data.

Importing this module creates no files, connections, or runtimes. ``install`` adds
handler behavior; the provider manager is initialized only on the first AI use.
"""
from __future__ import annotations

from contextvars import ContextVar
import json
import re
import socket
import threading
from urllib.parse import unquote, urlparse

from ai_connections import Manager, ConnectionError, EXTERNAL_PROVIDERS, REGISTRY, require_owner
from codex_connection import Connection, CodexError

MAX_BODY = 16 * 1024
BODY_TIMEOUT = 10
POST_PATHS = frozenset('/api/ai-connections/' + action for action in
                       ('discover', 'connect', 'default', 'login', 'disconnect'))
QUEUE_PATH = re.compile(r'/api/tasks/[^/]+/queue')


def _unique(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError('Duplicate JSON key')
        obj[key] = value
    return obj


def decode(raw):
    def invalid_constant(_):
        raise ValueError('Invalid JSON number')
    value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                       parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object')
    return value


def install(app, BaseHandler, public_files, codex=None):
    """Return the app handler with owner-scoped AI routes and model dispatch."""
    public_files['/ai-connections.js'] = ('ai-connections.js', 'application/javascript; charset=utf-8')
    current_actor = ContextVar('project_byte_ai_actor', default=None)
    current_operation = ContextVar('project_byte_ai_operation', default='')
    manager = None
    manager_lock = threading.Lock()
    legacy_route = getattr(app, 'route_model', None)
    legacy_call = getattr(app, 'call_model', None)

    def get_manager():
        nonlocal manager
        if manager is None:
            with manager_lock:
                if manager is None:
                    manager = Manager(app, codex if codex is not None else Connection())
        return manager

    def row_for(key):
        return next((row for row in app.model_rows() if row.get('model_key') == key), None)

    def private_model(key, row=None):
        row = row if row is not None else row_for(key)
        return bool(row and (row.get('provider') in EXTERNAL_PROVIDERS or get_manager().managed(key)))

    def validate_model(key, operation=None):
        row = row_for(key)
        if private_model(key, row):
            require_owner(current_actor.get())
            if not row.get('enabled'):
                raise ConnectionError('The selected AI connection is disabled; choose an enabled model', 409)
            if (operation or current_operation.get()) == 'queue':
                raise ConnectionError('This AI connection supports workspace chat. It cannot be dispatched through the existing execution bridge', 409)
        return row

    def route_model(agent_key, requested=''):
        operation = current_operation.get()
        actor = current_actor.get()
        selected_default = False
        if operation == 'chat' and not requested and actor:
            subject = app.settings_subject(actor)
            requested = app.get_settings(subject).get('ai', {}).get('default_model') or ''
            selected_default = bool(requested)
        if requested:
            row = row_for(requested)
            # A configured default or private connection must never quietly use
            # another provider after it was disabled or removed.
            if (selected_default or operation in ('chat', 'queue')) and (row is None or not row.get('enabled')):
                raise ConnectionError('Your selected AI is unavailable; choose an enabled model', 409)
            validate_model(requested)
        if not callable(legacy_route):
            raise ConnectionError('AI model routing is unavailable', 503)
        selected = legacy_route(agent_key, requested)
        validate_model(selected)
        return selected

    def call_model(key, messages):
        row = validate_model(key)
        if row and row.get('provider') in REGISTRY:
            return get_manager().call(row, messages, actor=current_actor.get())
        if not callable(legacy_call):
            raise ConnectionError('AI model transport is unavailable', 503)
        return legacy_call(key, messages)

    app.route_model = route_model
    app.call_model = call_model

    class AIHandler(BaseHandler):
        @staticmethod
        def ai_manager():
            return get_manager()

        def _codex_status(self, manager, actor):
            state=manager.codex.status()
            try:
                automatic=manager.autoconnect_codex(actor)
            except (ConnectionError, CodexError):
                automatic={'state':'unavailable'}
            if state.get('connected') and automatic['state'] in ('unavailable','model_unavailable'):
                state={**state,'detail':'ChatGPT sign-in is connected. Automatic Astra setup is unavailable; manage connections to choose an available model.'}
            return state,automatic

        def body(self, limit=1024*1024):
            if hasattr(self, '_ai_cached_body'):
                return self._ai_cached_body
            return super().body(limit)

        def _ai_error(self, error):
            if isinstance(error, ConnectionError):
                return self.sendj({'error': str(error)}, error.http_status)
            if isinstance(error, CodexError):
                return self.sendj({'error': 'Codex is unavailable; refresh its connection status'}, 503)
            return self.sendj({'error': 'AI connections are unavailable'}, 503)

        def _ai_body(self, limit=MAX_BODY):
            lengths = self.headers.get_all('Content-Length', [])
            types = self.headers.get_all('Content-Type', [])
            origins = self.headers.get_all('Origin', [])
            hosts = self.headers.get_all('Host', [])
            valid_origin = len(origins) <= 1
            if origins:
                try:
                    parsed = urlparse(origins[0])
                    valid_origin = (valid_origin and len(hosts) == 1 and parsed.scheme in ('http', 'https')
                                    and origins[0] == parsed.scheme+'://'+hosts[0])
                except ValueError:
                    valid_origin = False
            if (self.headers.get_all('Transfer-Encoding', []) or len(lengths) != 1
                    or not re.fullmatch(r'[0-9]{1,6}', lengths[0])
                    or not 0 < int(lengths[0]) <= limit or len(types) != 1
                    or types[0].split(';', 1)[0].strip().lower() != 'application/json'
                    or len(hosts) != 1 or not hosts[0] or not valid_origin):
                self.close_connection = True
                raise ConnectionError('Invalid AI connection request', 400)
            previous = self.connection.gettimeout()
            def expire():
                try:
                    self.connection.shutdown(socket.SHUT_RD)
                except OSError:
                    pass
            deadline = threading.Timer(BODY_TIMEOUT, expire)
            deadline.daemon = True
            deadline.start()
            try:
                self.connection.settimeout(BODY_TIMEOUT)
                data = self.rfile.read(int(lengths[0]))
                if len(data) != int(lengths[0]):
                    raise ValueError('Incomplete body')
                return decode(data)
            except (ValueError, UnicodeError, RecursionError, OSError):
                self.close_connection = True
                raise ConnectionError('Invalid AI connection request', 400) from None
            finally:
                deadline.cancel()
                self.connection.settimeout(previous)

        def do_GET(self):
            path = unquote(urlparse(self.path).path)
            if path == '/api/ai-connections':
                actor = self.need(4)
                if not actor:
                    return
                try:
                    require_owner(actor)
                    m = get_manager()
                    result = m.catalog()
                    codex_status, automatic = self._codex_status(m, actor)
                    result.update(codex=codex_status, codex_auto_connection=automatic, connections=m.connections(actor),
                                  default_model_key=app.get_settings('owner').get('ai', {}).get('default_model', ''))
                    return self.sendj(result)
                except Exception as error:
                    return self._ai_error(error)
            if path in ('/api/models', '/api/agents'):
                actor = self.who()
                field = 'models' if path == '/api/models' else 'agents'
                if actor.get('level', 0) < 1:
                    return self.sendj({field: []})
                try:
                    rows = app.model_rows() if field == 'models' else app.agent_rows()
                    if actor.get('role') != 'owner' or actor.get('level') != 4 or actor.get('subject') != 'owner':
                        if field == 'models':
                            rows = [row for row in rows if not private_model(row.get('model_key'), row)]
                        else:
                            # External-agent rows point to their private connection.
                            rows = [row for row in rows if not private_model(row.get('preferred_model'))]
                    return self.sendj({field: rows})
                except Exception as error:
                    return self._ai_error(error)
            return super().do_GET()

        def do_POST(self):
            path = urlparse(self.path).path
            if path in POST_PATHS or path == '/api/models':
                actor = self.need(4)
                if not actor:
                    return
                try:
                    require_owner(actor)
                    if self.path != path:
                        raise ConnectionError('Invalid AI connection request', 400)
                    body = self._ai_body()
                    m = get_manager()
                    if path == '/api/models':
                        # Existing clients retain their endpoint and response shape;
                        # new records use the same private owner validation/storage.
                        legacy_body = {key: value for key, value in body.items()
                                       if key not in ('capabilities', 'local', 'notes')}
                        result = m.connect(legacy_body, actor)
                        return self.sendj({'ok': True, **result}, 201)
                    if path.endswith('/discover'):
                        result = m.discover(body, actor)
                    elif path.endswith('/connect'):
                        result = m.connect(body, actor)
                    elif path.endswith('/default'):
                        if set(body) != {'model_key'}:
                            raise ConnectionError('A model key is required', 400)
                        result = m.set_default(body['model_key'], actor)
                    elif path.endswith('/disconnect'):
                        if set(body) != {'model_key'}:
                            raise ConnectionError('A model key is required', 400)
                        result = m.disconnect(body['model_key'], actor)
                    else:
                        if body:
                            raise ConnectionError('Invalid Codex sign-in request', 400)
                        login = m.codex.login()
                        codex_status, automatic = self._codex_status(m, actor)
                        result = {'login': login, 'codex': codex_status, 'codex_auto_connection': automatic}
                    return self.sendj(result)
                except Exception as error:
                    return self._ai_error(error)
            operation = 'chat' if path == '/api/chat' else 'test' if path == '/api/models/test' else 'queue' if QUEUE_PATH.fullmatch(path) else ''
            if not operation:
                return super().do_POST()
            actor = self.need(3 if operation == 'test' else 2)
            if not actor:
                return
            actor_token = current_actor.set(actor)
            operation_token = current_operation.set(operation)
            try:
                if operation == 'test':
                    self._ai_cached_body = super().body()
                    if not isinstance(self._ai_cached_body, dict):
                        return self.sendj({'error': 'Invalid model test request'}, 400)
                    key = app.valid_hint(self._ai_cached_body.get('model_key'))
                    validate_model(key)
                # Queue validation remains inside route_model, after the original
                # project's owner-hold and dependency checks, before issue creation.
                return super().do_POST()
            except ConnectionError as error:
                return self._ai_error(error)
            except (ValueError, TypeError):
                return self.sendj({'error': 'Invalid AI request'}, 400)
            finally:
                if hasattr(self, '_ai_cached_body'):
                    del self._ai_cached_body
                current_operation.reset(operation_token)
                current_actor.reset(actor_token)

    AIHandler.__name__ = 'AIHandler'
    return AIHandler
