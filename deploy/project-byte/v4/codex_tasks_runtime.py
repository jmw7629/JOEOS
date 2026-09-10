"""Minimal authenticated HTTP overlay for the separate Codex task controller."""
import os
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from codex_tasks import Controller, TaskError, require_owner

ROOT = '/api/codex-workspace'
CID = r'([0-9a-f]{32})'
_APP_WAL_LOCK = threading.Lock()


def install(app, BaseHandler, public_files, controller=None):
    public_files['/codex-workspace.js'] = ('codex-workspace.js', 'application/javascript; charset=utf-8')
    guard = threading.Lock()

    def ensure_app_wal():
        # The public gateway cannot create WAL sidecars in the app directory.
        # Keep one app-owned connection alive so collecting other connections
        # during native runtime initialization cannot remove those sidecars.
        with _APP_WAL_LOCK:
            if getattr(app, '_codex_workspace_wal_anchor', None) is not None:
                return
            if not Path(app.DB).is_file():
                raise TaskError('Application database is unavailable', 503)
            anchor = app.con()
            try:
                anchor.execute('PRAGMA query_only=ON').close()
                cursor = anchor.execute('SELECT name FROM sqlite_schema LIMIT 1')
                try:
                    cursor.fetchall()
                finally:
                    cursor.close()
                if anchor.in_transaction:
                    raise RuntimeError('Application database anchor must remain idle')
            except BaseException:
                anchor.close()
                raise
            app._codex_workspace_wal_anchor = anchor

    initialize = getattr(app, 'init_db', None)
    if callable(initialize) and not getattr(initialize, '_codex_workspace_wal_init', False):
        def initialize_with_wal(*args, **kwargs):
            result = initialize(*args, **kwargs)
            ensure_app_wal()
            return result
        initialize_with_wal._codex_workspace_wal_init = True
        app.init_db = initialize_with_wal

    def manager():
        nonlocal controller
        if controller is None:
            with guard:
                if controller is None:
                    state = os.getenv('PRFKT_CODEX_STATE')
                    repo = os.getenv('PRFKT_CODEX_REPO')
                    if not state or not repo or not Path(state).is_absolute() or not Path(repo).is_absolute():
                        raise TaskError('Codex task execution is not configured', 503)
                    ensure_app_wal()
                    from codex_projects import default_project, load_projects
                    from codex_publisher import Publisher
                    default = default_project(repo, publication=os.getenv('PRFKT_CODEX_PUBLISH') == '1',
                                              base_branch=os.getenv('PRFKT_CODEX_BASE_BRANCH', 'project-byte-deploy'))
                    projects = load_projects(os.getenv('PRFKT_CODEX_PROJECTS'), default, state=state)
                    publishers = {}
                    for project in projects:
                        if not project.publication:
                            continue
                        # The original publisher ledger remains at its existing
                        # path, so uncertain operations are never forgotten.
                        if project.key == 'joeos':
                            directory = Path(state) / 'publisher'
                        else:
                            parent = Path(state) / 'publishers'
                            parent.mkdir(mode=0o700, exist_ok=True)
                            directory = parent / project.key
                        publishers[project.key] = Publisher(directory, project.source, repo=project.repo,
                                                            registered_repo=project.repo, base_branch=project.base_branch)
                    controller = Controller(state, repo, projects=projects, project_publishers=publishers)
        return controller

    class CodexWorkspaceHandler(BaseHandler):
        def _workspace_error(self, error):
            if isinstance(error, TaskError):
                return self.sendj({'error': str(error)}, error.status)
            return self.sendj({'error': 'Codex task workspace is unavailable'}, 503)

        def do_GET(self):
            parsed = urlsplit(self.path)
            if not parsed.path.startswith(ROOT):
                return super().do_GET()
            actor = self.need(4)
            if not actor:
                return
            try:
                require_owner(actor)
                if parsed.path == ROOT and not parsed.query and self.path == ROOT:
                    try:
                        return self.sendj(manager().catalog(actor))
                    except TaskError as error:
                        if error.status != 503:
                            raise
                        return self.sendj({'configured': False, 'connected': False, 'model': 'gpt-6-astra', 'effort': 'ultra', 'projects': [], 'conversations': [], 'detail': str(error)})
                match = re.fullmatch(ROOT + '/runs/' + CID + r'/events(?:\?after=(0|[1-9][0-9]{0,18}))?', self.path)
                if match:
                    return self.sendj(manager().events(actor, match[1], int(match[2] or 0)))
                if parsed.path == ROOT + '/conversations':
                    try:
                        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=3) if parsed.query else {}
                    except ValueError:
                        raise TaskError('Invalid conversation query') from None
                    if set(query) - {'project', 'search', 'cursor'} or any(len(v) != 1 for v in query.values()):
                        raise TaskError('Invalid conversation query')
                    return self.sendj(manager().conversation_history(actor, **{k: v[0] for k, v in query.items()}))
                match = re.fullmatch(ROOT + '/conversations/' + CID, self.path)
                if match:
                    return self.sendj(manager().conversation(actor, match[1]))
                match = re.fullmatch(ROOT + '/artifacts/' + CID, self.path)
                if match:
                    return self.sendj(manager().artifact(actor, match[1]))
                raise TaskError('Workspace route was not found', 404)
            except Exception as error:
                return self._workspace_error(error)

        def do_POST(self):
            if not urlsplit(self.path).path.startswith(ROOT):
                return super().do_POST()
            actor = self.need(4)
            if not actor:
                return
            try:
                require_owner(actor)
                # Existing AI handler rejects duplicate keys, ambiguous framing,
                # cross-origin requests and oversized/slow JSON before dispatch.
                body = self._ai_body(limit=64 * 1024)
                if self.path == ROOT + '/message':
                    return self.sendj(manager().message(actor, body), 202)
                match = re.fullmatch(ROOT + '/runs/' + CID + '/stop', self.path)
                if match:
                    return self.sendj(manager().stop(actor, match[1], body))
                match = re.fullmatch(ROOT + '/permissions/' + CID + '/decision', self.path)
                if match:
                    return self.sendj(manager().decide(actor, match[1], body))
                raise TaskError('Workspace route was not found', 404)
            except Exception as error:
                # Preserve the existing parser's public HTTP errors, never paths.
                if hasattr(error, 'http_status'):
                    return self.sendj({'error': 'Invalid Codex workspace request'}, error.http_status)
                return self._workspace_error(error)

    return CodexWorkspaceHandler
