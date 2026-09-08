import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent


class WorkspaceProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'workspace.json'
        backend = types.ModuleType('backend')
        backend.H = object
        backend.ROOT = self.root
        backend.UPLOADS = self.root / 'uploads'
        backend.con = lambda: (_ for _ in ()).throw(AssertionError('No database access'))
        with mock.patch.dict(sys.modules, {'backend': backend}):
            spec = importlib.util.spec_from_file_location('workspace_runtime', HERE / 'runtime_server.py')
            self.runtime = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.runtime)

    def response(self):
        handler = object.__new__(self.runtime.SafeHandler)
        handler.path = '/api/workspace-profile'
        handler.sendj = lambda data, status=200: (status, data)
        handler.need = lambda *args: (_ for _ in ()).throw(AssertionError('Presentation must not modify auth'))
        return handler.do_GET()

    def test_absent_profile_preserves_defaults_without_creating_files(self):
        self.assertEqual(self.response(), (200, {'profile': {'schema_version':1,'display_name':'PROJECT_BYTE','assistant_name':'Joe AI','owner_shortcuts':['Joe','Mike']}}))
        self.assertEqual(list(self.root.iterdir()), [])
        self.runtime.workspace_profile()['owner_shortcuts'].append('mutation')
        self.assertEqual(self.runtime.workspace_profile()['owner_shortcuts'], ['Joe','Mike'])

    def test_valid_profile_is_projected_and_never_mutated(self):
        value = {'schema_version':1,'display_name':' Acme Operations ','assistant_name':'Atlas','owner_shortcuts':['Avery','Taylor','Avery']}
        self.profile.write_text(json.dumps(value))
        before = self.profile.read_bytes()
        status, response = self.response()
        self.assertEqual(status,200)
        self.assertEqual(response['profile'], {'schema_version':1,'display_name':'Acme Operations','assistant_name':'Atlas','owner_shortcuts':['Avery','Taylor']})
        self.assertEqual(self.profile.read_bytes(),before)
        self.assertEqual(list(self.root.iterdir()),[self.profile])

    def test_invalid_configuration_is_explicit_without_leaking_content(self):
        invalid = [None,[],{}, {'schema_version':True}, {'schema_version':2},
                   {'schema_version':1,'api_key':'DO_NOT_EXPOSE'},
                   {'schema_version':1,'display_name':'\nprivate'},
                   {'schema_version':1,'assistant_name':'x'*49},
                   {'schema_version':1,'owner_shortcuts':'Joe'},
                   {'schema_version':1,'owner_shortcuts':[' ']},
                   {'schema_version':1,'owner_shortcuts':['x']*7}]
        for value in invalid:
            with self.subTest(value=value):
                self.profile.write_text(json.dumps(value))
                self.assertEqual(self.response(),(503,{'error':'Workspace presentation configuration is unavailable'}))
        for raw in [b'bad json',b' '*16385,b'['*1200+b'0'+b']'*1200,b'\xff']:
            self.profile.write_bytes(raw)
            self.assertEqual(self.response()[0],503)

    def test_special_files_and_symlinks_are_rejected(self):
        target=self.root/'private.json'
        target.write_text('{"schema_version":1,"display_name":"private"}')
        self.profile.symlink_to(target)
        self.assertEqual(self.response()[0],503)
        self.profile.unlink()
        os.mkfifo(self.profile)
        self.assertEqual(self.response()[0],503)

    def test_empty_shortcuts_and_safe_partial_profile_are_supported(self):
        self.profile.write_text('{"schema_version":1,"owner_shortcuts":[]}')
        self.assertEqual(self.runtime.workspace_profile()['owner_shortcuts'],[])
        self.assertEqual(self.runtime.workspace_profile()['display_name'],'PROJECT_BYTE')


if __name__ == '__main__':
    unittest.main(verbosity=2)
