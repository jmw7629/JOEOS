"""Disposable code-only fixtures for the exact-baseline Codex overlay stager.

No live app, credentials, GitHub, model, worker, or service is accessed. BASE is
patched to hashes of these explicit synthetic files, never relaxed in production.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

import stage_codex_overlay as overlay


class CodexOverlayTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='pb-codex-overlay-fixture-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.source = self.root / 'source-app'
        self.features = self.root / 'feature-code'
        self.output = self.root / 'staged-code'
        self.source.mkdir()
        self.features.mkdir()
        self.baseline = {
            'server.py': (b'# synthetic runtime; preserve every byte\n'
                          b'PRESERVED = "fixture"\n\n'
                          b'if __name__ == "__main__":\n    pass\n'),
            'index.html': (b'<!doctype html>\n<html><body>\n'
                           b'<main data-preserve="yes">Synthetic fixture</main>\n'
                           b'<script src="/ai-connections.js"></script>\n'
                           b'</body>\n</html>\n'),
            'ai_runtime.py': b'# synthetic old AI integration\nBASELINE = True\n',
        }
        self.payload = {
            name: (b'(() => { "use strict"; })();\n' if name.endswith('.js')
                   else b'# synthetic feature; compiling must never execute it\n'
                        b'raise RuntimeError("FIXTURE_CODE_MUST_NOT_EXECUTE")\n')
            for name in overlay.FILES
        }
        for directory, files in ((self.source, self.baseline), (self.features, self.payload)):
            for name, data in files.items():
                (directory / name).write_bytes(data)
        self.pins = {name: hashlib.sha256(data).hexdigest() for name, data in self.baseline.items()}
        patcher = patch.object(overlay, 'BASE', self.pins)
        patcher.start()
        self.addCleanup(patcher.stop)

    def stage(self, *, source=None, output=None, features=None):
        return overlay.stage(source or self.source, output or self.output, features or self.features)

    def assert_no_output(self):
        self.assertFalse(self.output.exists())
        self.assertFalse(self.output.is_symlink())

    def test_project_upgrade_replaces_only_pinned_modules_and_preserves_live_data(self):
        baseline = {}
        for name in overlay.PROJECT_BASE:
            data = b'// old fixture\n' if name.endswith('.js') else b'# old fixture\n'
            (self.source / name).write_bytes(data)
            baseline[name] = hashlib.sha256(data).hexdigest()
        (self.source / 'kanban.db').write_bytes(b'PRIVATE_DATA_FIXTURE')
        (self.source / 'projects.json').write_bytes(b'PRIVATE_REGISTRY_FIXTURE')
        original = {p.name: p.read_bytes() for p in self.source.iterdir()}
        with patch.object(overlay, 'PROJECT_BASE', baseline):
            manifest = overlay.stage(self.source, self.output, self.features, project_upgrade=True)
        self.assertEqual(manifest['operation'], 'stage-project-routing-code-only')
        self.assertEqual(set(manifest['output_sha256']), set(overlay.PROJECT_FILES))
        self.assertEqual(manifest['baseline_sha256'], baseline)
        self.assertEqual({p.name: p.read_bytes() for p in self.source.iterdir()}, original)
        self.assertFalse((self.output / 'index.html').exists())
        self.assertFalse((self.output / 'server.py').exists())
        self.assertFalse((self.output / 'projects.json').exists())

    def test_project_upgrade_rejects_drift_and_already_present_registry_module(self):
        baseline = {}
        for name in overlay.PROJECT_BASE:
            data = b'// old fixture\n' if name.endswith('.js') else b'# old fixture\n'
            (self.source / name).write_bytes(data)
            baseline[name] = hashlib.sha256(data).hexdigest()
        with patch.object(overlay, 'PROJECT_BASE', baseline):
            (self.source / 'codex_projects.py').write_text('# installed already\n')
            with self.assertRaisesRegex(ValueError, 'already present'):
                overlay.stage(self.source, self.output, self.features, project_upgrade=True)
            (self.source / 'codex_projects.py').unlink()
            (self.source / 'codex_tasks.py').write_text('# drift\n')
            with self.assertRaisesRegex(ValueError, 'baseline differs'):
                overlay.stage(self.source, self.output, self.features, project_upgrade=True)
        self.assert_no_output()

    def test_manifest_and_allowlisted_output_preserve_every_unmodified_byte(self):
        # Both app state and unexpected feature files remain unread and uncopied.
        for directory in (self.source, self.features):
            (directory / 'admin.secret').write_bytes(b'DO_NOT_READ_OR_COPY_FIXTURE_SECRET')
            (directory / 'kanban.db').write_bytes(b'DO_NOT_READ_OR_COPY_FIXTURE_DATABASE')
            (directory / 'data').mkdir()
            (directory / 'data' / 'private.json').write_text('DO_NOT_COPY_FIXTURE_DATA')
        (self.features / 'runtime_server.py').write_text('raise RuntimeError("wrong inherited runtime")')
        (self.features / 'index.html').write_text('wrong inherited UI')
        reads = []
        reader = overlay._read_code

        def recording_read(fd, name):
            reads.append(name)
            self.assertIn(name, set(self.baseline) | set(overlay.FILES))
            return reader(fd, name)

        with patch.object(overlay, '_read_code', side_effect=recording_read):
            manifest = self.stage()
        self.assertCountEqual(reads, list(self.baseline) + list(overlay.FILES))
        expected_names = set(overlay.FILES) | {'server.py', 'index.html', 'codex-overlay-manifest.json'}
        self.assertEqual({path.name for path in self.output.iterdir()}, expected_names)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for path in self.output.iterdir():
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertFalse(path.is_symlink())
            self.assertNotIn(b'DO_NOT_', path.read_bytes())
        self.assertEqual(manifest['operation'], 'stage-codex-code-only')
        self.assertEqual(manifest['baseline_sha256'], self.pins)
        self.assertEqual(manifest, json.loads((self.output / 'codex-overlay-manifest.json').read_bytes()))
        self.assertEqual(set(manifest['output_sha256']), expected_names - {'codex-overlay-manifest.json'})
        for name, expected_hash in manifest['output_sha256'].items():
            self.assertEqual(hashlib.sha256((self.output / name).read_bytes()).hexdigest(), expected_hash)
        for name, insertion in (('server.py', overlay.HOOK), ('index.html', overlay.SCRIPT)):
            result = (self.output / name).read_bytes()
            self.assertEqual(result.count(insertion), 1)
            self.assertEqual(result.replace(insertion, b'', 1), self.baseline[name])
        for name, data in self.payload.items():
            self.assertEqual((self.output / name).read_bytes(), data)
        for name, data in self.baseline.items():
            self.assertEqual((self.source / name).read_bytes(), data)

    def test_each_exact_baseline_hash_is_required_before_reading_features_or_writing(self):
        for name, original in self.baseline.items():
            with self.subTest(name=name):
                (self.source / name).write_bytes(original + b' ')
                with patch.object(overlay, '_directory', wraps=overlay._directory) as directories:
                    with self.assertRaisesRegex(ValueError, 'baseline differs'):
                        self.stage()
                    self.assertEqual(directories.call_args_list, [unittest.mock.call(self.source)])
                self.assert_no_output()
                (self.source / name).write_bytes(original)

    def test_missing_baseline_and_feature_files_fail_before_destination_creation(self):
        for directory, files in ((self.source, self.baseline), (self.features, self.payload)):
            for name, original in files.items():
                with self.subTest(directory=directory.name, name=name):
                    (directory / name).unlink()
                    with self.assertRaises(FileNotFoundError):
                        self.stage()
                    self.assert_no_output()
                    (directory / name).write_bytes(original)

    def test_symlink_source_and_feature_directories_are_rejected(self):
        for argument, target in (('source', self.source), ('features', self.features)):
            with self.subTest(argument=argument):
                alias = self.root / (argument + '-alias')
                alias.symlink_to(target, target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'non-symlink'):
                    self.stage(**{argument: alias})
                self.assert_no_output()

    def test_every_symlink_baseline_and_feature_file_is_rejected(self):
        for directory, files in ((self.source, self.baseline), (self.features, self.payload)):
            for name, original in files.items():
                with self.subTest(directory=directory.name, name=name):
                    external = self.root / 'regular-file-outside-input'
                    external.write_bytes(original)
                    target = directory / name
                    target.unlink()
                    target.symlink_to(external)
                    with self.assertRaises(OSError):
                        self.stage()
                    self.assert_no_output()
                    target.unlink()
                    target.write_bytes(original)

    def test_nonregular_feature_file_is_rejected_without_blocking(self):
        target = self.features / 'codex_tasks.py'
        target.unlink()
        os.mkfifo(target)
        with self.assertRaisesRegex(ValueError, 'regular code file'):
            self.stage()
        self.assert_no_output()

    def test_existing_destination_file_directory_and_dangling_symlink_are_untouched(self):
        self.output.write_text('KEEP_EXISTING_FILE')
        with self.assertRaisesRegex(ValueError, 'must be new'):
            self.stage()
        self.assertEqual(self.output.read_text(), 'KEEP_EXISTING_FILE')
        self.output.unlink()
        self.output.mkdir()
        sentinel = self.output / 'keep.txt'
        sentinel.write_text('KEEP_EXISTING_DIRECTORY')
        with self.assertRaisesRegex(ValueError, 'must be new'):
            self.stage()
        self.assertEqual(sentinel.read_text(), 'KEEP_EXISTING_DIRECTORY')
        sentinel.unlink()
        self.output.rmdir()
        missing = self.root / 'must-remain-missing'
        self.output.symlink_to(missing)
        with self.assertRaisesRegex(ValueError, 'must be new'):
            self.stage()
        self.assertTrue(self.output.is_symlink())
        self.assertFalse(missing.exists())

    def test_direct_and_parent_symlink_destinations_inside_source_are_rejected(self):
        alias = self.root / 'source-parent-alias'
        alias.symlink_to(self.source, target_is_directory=True)
        for output in (self.source / 'new-output', alias / 'new-output'):
            with self.subTest(output=output):
                with self.assertRaisesRegex(ValueError, 'outside the live app'):
                    self.stage(output=output)
                self.assertFalse((self.source / 'new-output').exists())

    def test_every_existing_overlay_file_refuses_reinstallation(self):
        for name in overlay.FILES:
            if name == 'ai_runtime.py':
                continue  # This exact pinned existing integration is explicitly replaced.
            with self.subTest(name=name):
                existing = self.source / name
                existing.write_bytes(b'KEEP_EXISTING_OVERLAY')
                with self.assertRaisesRegex(ValueError, 'already present'):
                    self.stage()
                self.assertEqual(existing.read_bytes(), b'KEEP_EXISTING_OVERLAY')
                self.assert_no_output()
                existing.unlink()

    def test_missing_repeated_and_existing_hook_markers_fail_before_destination(self):
        for name, marker, insertion in (('server.py', b'if __name__ == "__main__":\n', overlay.HOOK),
                                        ('index.html', b'</body>', overlay.SCRIPT)):
            original = self.baseline[name]
            for label, content in (('missing', original.replace(marker, b'')),
                                   ('repeated', original + marker),
                                   ('already inserted', original.replace(marker, insertion + marker))):
                with self.subTest(name=name, case=label):
                    (self.source / name).write_bytes(content)
                    pins = {**self.pins, name: hashlib.sha256(content).hexdigest()}
                    with patch.object(overlay, 'BASE', pins):
                        with self.assertRaisesRegex(ValueError, 'overlay marker'):
                            self.stage()
                    self.assert_no_output()
            (self.source / name).write_bytes(original)

    def test_each_python_syntax_failure_precedes_destination_creation(self):
        for name, original in self.payload.items():
            if not name.endswith('.py'):
                continue
            with self.subTest(name=name):
                (self.features / name).write_bytes(b'def invalid syntax:\n')
                with self.assertRaises(SyntaxError):
                    self.stage()
                self.assert_no_output()
                (self.features / name).write_bytes(original)


if __name__ == '__main__':
    unittest.main()
