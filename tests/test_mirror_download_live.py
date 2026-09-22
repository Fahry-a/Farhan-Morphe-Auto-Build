import json
import os
import shutil
import tempfile
import unittest

from tools.common import validate_package
from tools.mirror_download import download_from_mirror


class LiveMirrorDownloadTests(unittest.TestCase):
    CONFIG = "apps/audiorelay.json"
    VERSION = "0.26.1"
    ARCH = "universal"

    @classmethod
    def setUpClass(cls):
        if os.environ.get("RUN_LIVE_MIRROR_TESTS") != "1":
            raise unittest.SkipTest(
                "set RUN_LIVE_MIRROR_TESTS=1 to run network-backed mirror tests"
            )
        with open(cls.CONFIG) as fh:
            cls.config = json.load(fh)
        cls.aapt = os.environ.get("AAPT") or shutil.which("aapt")
        if not cls.aapt:
            raise unittest.SkipTest("aapt is required for exact APK version validation")

    def test_each_configured_mirror_downloads_exact_version(self):
        mirrors = self.config["source"]["mirrors"]
        self.assertEqual(
            [m["type"] for m in mirrors],
            ["apkpure", "uptodown", "aptoide"],
        )

        failures = []
        for mirror in mirrors:
            kind = mirror["type"]
            with self.subTest(mirror=kind):
                with tempfile.TemporaryDirectory() as td:
                    output = os.path.join(td, f"{kind}.apk")
                    try:
                        version = download_from_mirror(
                            kind, self.config, self.ARCH, self.VERSION, output
                        )
                        self.assertEqual(version, self.VERSION)
                        entries = validate_package(
                            output,
                            "apk",
                            expected_version=self.VERSION,
                            aapt_path=self.aapt,
                        )
                        self.assertGreater(entries, 0)
                        self.assertGreater(os.path.getsize(output), 1_000_000)
                    except Exception as exc:
                        failures.append(f"{kind}: {exc}")

        if failures:
            self.fail("Live mirror failures:\n" + "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
