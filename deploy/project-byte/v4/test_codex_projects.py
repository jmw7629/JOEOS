"""Private configuration admission; no network, models or host repositories."""
from dataclasses import FrozenInstanceError, asdict, replace
import json
import os
from pathlib import Path
import tempfile
import unittest

from codex_projects import Project, ProjectError, default_project, load_projects


class ProjectRegistryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='prfkt-registry-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'joeos'
        self.source.mkdir()
        self.other = self.root / 'other'
        self.other.mkdir()
        self.state = self.root / 'state'
        self.state.mkdir(mode=0o700)
        self.path = self.root / 'projects.json'
        self.default = default_project(self.source, publication=True)
        self.second = Project('memory', 'Second Brain', 'fixture/MEMORY', self.other, 'main',
                              'execute', False, 'Source tasks available', 'Historical reference only.')

    def write(self, projects=None, data=None):
        if data is None:
            data = {'schema_version': 1, 'projects': [asdict(p) for p in (projects or [self.default, self.second])]}
        self.path.write_text(json.dumps(data, default=str))
        self.path.chmod(0o600)
        return self.path

    def read(self):
        return load_projects(self.path, self.default, state=self.state)

    def test_explicit_absence_preserves_legacy_and_mixed_registry_is_immutable(self):
        self.assertEqual(load_projects(None, self.default, state=self.state), (self.default,))
        observe = Project('vitros', 'VITROS', 'fixture/vitros', None, 'main', 'observe', False,
                          'Existing coordinator owns this project')
        missing = Project('mario', 'Mario', None, None, None, 'blocked', False, 'Source not found')
        self.write([self.default, self.second, observe, missing])
        projects = self.read()
        self.assertEqual(projects, (self.default, self.second, observe, missing))
        with self.assertRaises(FrozenInstanceError):
            projects[1].source = self.source

    def test_identity_binds_source_repository_and_base_but_not_status_notes(self):
        project = self.second
        for field, value in [('repo', 'fixture/changed'), ('source', self.source), ('base_branch', 'release'), ('key', 'other')]:
            self.assertNotEqual(project.fingerprint, replace(project, **{field: value}).fingerprint)
        self.assertEqual(project.fingerprint, replace(project, detail='Changed note', context='new reference',
                                                     mode='observe').fingerprint)

    def test_private_file_and_alias_boundaries(self):
        self.write()
        self.path.chmod(0o644)
        with self.assertRaises(ProjectError): self.read()
        self.path.chmod(0o600)
        link = self.root / 'alias.json'
        link.symlink_to(self.path)
        with self.assertRaises(ProjectError): load_projects(link, self.default, state=self.state)
        hard = self.root / 'hard.json'
        os.link(self.path, hard)
        with self.assertRaises(ProjectError): self.read()
        hard.unlink()
        self.assertEqual(len(self.read()), 2)

    def test_invalid_source_paths_and_task_storage_are_rejected(self):
        alias = self.root / 'source-alias'
        alias.symlink_to(self.other, target_is_directory=True)
        for source in (Path('relative'), self.root / 'missing', alias):
            with self.subTest(source=source), self.assertRaises(ProjectError):
                replace(self.second, source=source)
        workspace = self.state / 'workspaces' / 'pretend-source'
        workspace.mkdir(parents=True)
        self.write([self.default, replace(self.second, source=workspace)])
        with self.assertRaises(ProjectError): self.read()

    def test_unknown_fields_duplicate_json_and_versions_fail_closed(self):
        self.write()
        original = json.loads(self.path.read_text())
        variants = [dict(original, extra=True), dict(original, schema_version=True),
                    dict(original, schema_version=2), dict(original, projects=[])]
        for value in variants:
            self.write(data=value)
            with self.assertRaises(ProjectError): self.read()
        self.write()
        self.path.write_text(self.path.read_text().replace('"schema_version": 1', '"schema_version": 1, "schema_version": 1'))
        with self.assertRaises(ProjectError): self.read()
        value = original
        value['projects'][1]['command'] = 'must never execute'
        self.write(data=value)
        with self.assertRaises(ProjectError): self.read()

    def test_competing_keys_repositories_sources_and_legacy_rebinding_are_rejected(self):
        for projects in ([self.second], [self.default, self.default],
                         [self.default, replace(self.second, source=self.source)],
                         [self.default, replace(self.second, repo='JMW7629/joeos')],
                         [replace(self.default, base_branch='main'), self.second]):
            self.write(projects)
            with self.assertRaises(ProjectError): self.read()

    def test_publication_never_follows_a_browser_style_repository_or_disabled_project(self):
        for repo in ('https://github.com/fixture/repo', 'fixture/repo\n--help', 'fixture/repo/extra',
                     '../repo', '-option/repo', 'fixture/repo?key=value'):
            with self.subTest(repo=repo), self.assertRaises(ProjectError): replace(self.second, repo=repo)
        for changes in ({'mode': 'observe'}, {'repo': None}, {'base_branch': None}, {'mode': 'blocked'}):
            with self.subTest(changes=changes), self.assertRaises(ProjectError):
                replace(self.second, publication=True, **changes)
        with self.assertRaises(ProjectError): replace(self.second, source=None)

    def test_text_and_size_limits_and_invalid_json(self):
        for changes in ({'label': ''}, {'label': 'bad\nlabel'}, {'detail': 'x' * 1201},
                        {'context': 'x' * 20001}, {'key': '../escape'}, {'base_branch': 'x/../main'}):
            with self.subTest(changes=changes), self.assertRaises(ProjectError): replace(self.second, **changes)
        self.write()
        self.path.write_bytes(b'\xff')
        with self.assertRaises(ProjectError): self.read()
        self.path.write_bytes(b' ' * (256 * 1024 + 1))
        with self.assertRaises(ProjectError): self.read()


if __name__ == '__main__':
    unittest.main()
