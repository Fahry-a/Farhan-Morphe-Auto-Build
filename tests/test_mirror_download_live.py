"""Opt-in network regression for every configured mirror target.

The normal unit suite skips this module.  GitHub Actions uses
``tools/mirror_probe.py`` directly for per-target matrix jobs, but keeping this
entry point makes the complete audit easy to run locally as well.
"""
import os
import shutil
import unittest

from tools.mirror_probe import discover_targets, run_audit


class LiveMirrorDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("RUN_LIVE_MIRROR_TESTS") != "1":
            raise unittest.SkipTest(
                "set RUN_LIVE_MIRROR_TESTS=1 to run network-backed mirror tests"
            )
        cls.aapt = os.environ.get("AAPT") or shutil.which("aapt")
        if not cls.aapt:
            raise unittest.SkipTest("aapt is required for exact package validation")
        cls.targets = discover_targets("apps")
        if not cls.targets:
            raise unittest.SkipTest("no enabled mirror targets found")

    def test_every_configured_mirror_downloads_exact_version(self):
        results, _ = run_audit(
            self.targets,
            aapt_path=self.aapt,
            timeout=float(os.environ.get("MIRROR_TIMEOUT", "60")),
        )
        failures = [
            f"{result.target.app}/{result.target.arch}/{result.target.mirror}: "
            f"{result.error}"
            for result in results
            if result.status != "pass"
        ]
        self.assertFalse(failures, "Live mirror failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
