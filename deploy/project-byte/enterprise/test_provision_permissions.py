"""Regression checks for umask-sensitive files and scoped repair instructions."""
import contextlib, os, pwd, stat, subprocess, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import provision, repair_permissions, verify

@contextlib.contextmanager
def private_umask():
    old=os.umask(0o077)
    try:yield
    finally:os.umask(old)

class ProvisionPermissions(unittest.TestCase):
    def test_explicit_file_modes_survive_private_umask(self):
        with tempfile.TemporaryDirectory() as temp,private_umask():
            root=Path(temp)
            for mode in (0o600,0o640,0o644):
                path=root/str(mode);provision.write(path,'fixture',mode,os.getuid(),os.getgid())
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),mode)
            with self.assertRaises(FileExistsError):provision.write(root/str(0o600),'replacement')
    def test_public_directory_traversal_and_root_dns_files(self):
        with tempfile.TemporaryDirectory() as temp,private_umask():
            root=Path(temp)
            for mode in (0o711,0o750,0o755):
                path=root/str(mode);provision.mkdir_exact(path,mode)
                self.assertEqual(stat.S_IMODE(path.stat().st_mode),mode)
            with patch.object(provision.os,'chown'):
                provision.rootfs(root/'jail',pwd.getpwuid(os.getuid()))
            for name in repair_permissions.PUBLIC_ETC:
                self.assertEqual(stat.S_IMODE((root/'jail/etc'/name).stat().st_mode),0o644)
            self.assertEqual(stat.S_IMODE((root/'jail/etc').stat().st_mode),0o755)
    def test_repair_whitelist_excludes_data_and_private_keys(self):
        plan=repair_permissions.permission_plan(5,900)
        self.assertEqual(len(plan),42)
        for path,_,_,_ in plan:
            self.assertNotIn('joevps',str(path))
            self.assertNotIn('owner.key',str(path))
            self.assertNotIn('kanban.db',str(path))
            self.assertNotIn('portal-state',str(path))
            self.assertFalse(str(path).startswith('/home/'))
    def test_repair_refuses_symlinks_before_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();original=root/'file';original.write_text('unchanged');link=root/'link';link.symlink_to(original)
            with self.assertRaisesRegex(RuntimeError,'symlink'):repair_permissions.validate_plan([(link,0o644,False,0)])
            self.assertEqual(original.read_text(),'unchanged')
    def test_failed_verification_exposes_stderr_and_exit_code(self):
        with self.assertRaises(verify.VerificationError) as caught:
            verify.run(sys.executable,'-c','import sys; print("DNS probe failed",file=sys.stderr); sys.exit(7)')
        self.assertEqual(caught.exception.returncode,7)
        self.assertEqual(caught.exception.stderr,'DNS probe failed\n')
        self.assertIn('DNS probe failed',str(caught.exception))

if __name__=='__main__':unittest.main()
