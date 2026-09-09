"""Verify the trace release cannot overwrite live code or stage an unknown baseline."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import stage_codex_trace_overlay as overlay


class TraceStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'live'; self.source.mkdir()
        self.features = self.root / 'features'; self.features.mkdir()
        self.destination = self.root / 'stage'
        self.old = {'codex_tasks.py': b'# previous controller\n', 'codex-workspace.js': b'// previous UI\n'}
        for name, data in self.old.items():
            (self.source/name).write_bytes(data)
            (self.features/name).write_bytes(b'# updated controller\n' if name.endswith('.py') else b'// updated UI\n')
        (self.source/'admin.secret').write_text('fixture-secret-never-copied')
        (self.source/'index.html').write_text('owner design')
        pins = {name: hashlib.sha256(data).hexdigest() for name, data in self.old.items()}
        self.patcher = patch.object(overlay, 'BASE', pins); self.patcher.start(); self.addCleanup(self.patcher.stop)

    def test_private_code_only_stage_preserves_source_and_excludes_data(self):
        before = {p.name:p.read_bytes() for p in self.source.iterdir()}
        result = overlay.stage(self.source,self.destination,self.features)
        self.assertEqual(set(self.destination.iterdir()), {self.destination/name for name in (*self.old,'codex-trace-manifest.json')})
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.source.iterdir()})
        self.assertEqual(result['operation'],'stage-native-trace-code-only')
        for p in self.destination.iterdir(): self.assertEqual(p.stat().st_mode & 0o777,0o600)

    def test_changed_live_baseline_and_linked_code_fail_before_destination(self):
        (self.source/'codex_tasks.py').write_text('# drift\n')
        with self.assertRaises(ValueError): overlay.stage(self.source,self.destination,self.features)
        self.assertFalse(self.destination.exists())
        (self.source/'codex_tasks.py').unlink()
        (self.source/'codex_tasks.py').symlink_to(self.features/'codex_tasks.py')
        with self.assertRaises((ValueError,OSError)): overlay.stage(self.source,self.destination,self.features)
        self.assertFalse(self.destination.exists())

    def test_existing_or_inside_live_destination_is_never_overwritten(self):
        self.destination.mkdir(); sentinel=self.destination/'keep'; sentinel.write_text('existing work')
        with self.assertRaises(ValueError): overlay.stage(self.source,self.destination,self.features)
        self.assertEqual(sentinel.read_text(),'existing work')
        with self.assertRaises(ValueError): overlay.stage(self.source,self.source/'nested',self.features)
        self.assertFalse((self.source/'nested').exists())


if __name__ == '__main__': unittest.main()
