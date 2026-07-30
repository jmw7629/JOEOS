import os
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LauncherSecurityTests(unittest.TestCase):
    def test_private_https_launcher_uses_serve_and_loopback_only(self):
        launcher = ROOT / "start_joeos_secure.sh"
        source = launcher.read_text(encoding="utf-8")

        self.assertTrue(os.access(launcher, os.X_OK))
        self.assertIn('JOEOS_HOST=127.0.0.1', source)
        self.assertIn('tailscale serve --bg --https=443 "$JOEOS_LOCAL_URL"', source)
        self.assertIn('"$JOEOS_LOCAL_URL/healthz"', source)
        self.assertIsNone(re.search(r"^\s*tailscale\s+funnel\b", source, flags=re.MULTILINE | re.IGNORECASE))

    def test_setup_wrapper_contains_no_retired_cloud_credential_flow(self):
        source = (ROOT / "setup_joeos.command").read_text(encoding="utf-8")

        self.assertLess(len(source.splitlines()), 20)
        self.assertIn("start_joeos.command", source)
        for retired_term in ("render", "supabase", "stripe", "github token", "ask_secret"):
            self.assertNotIn(retired_term, source.lower())


if __name__ == "__main__":
    unittest.main()
