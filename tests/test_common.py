import os
import tempfile
import unittest
import zipfile

from tools.common import resolve_arch_entry, validate_package


class CommonTests(unittest.TestCase):
    def test_resolve_multi_arch(self):
        src = {"archs": [{"name": "arm64", "asset": "a"}, {"name": "arm32", "asset": "b"}]}
        self.assertEqual(resolve_arch_entry(src, "arm32")["asset"], "b")

    def test_resolve_single_arch_without_name(self):
        src = {"archs": [{"name": "universal", "asset": "u"}]}
        self.assertEqual(resolve_arch_entry(src, None)["name"], "universal")

    def test_multi_arch_requires_arch(self):
        src = {"archs": [{"name": "arm64"}, {"name": "arm32"}]}
        with self.assertRaises(RuntimeError):
            resolve_arch_entry(src, None)

    def test_validate_apk_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            self.assertGreater(validate_package(path, "apk"), 0)

    def test_validate_bundle_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apkm")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("base.apk", b"x" * 1_000_000)
                zf.writestr("split_config.arm64_v8a.apk", b"x")
            self.assertGreater(validate_package(path, "apkm"), 0)


if __name__ == "__main__":
    unittest.main()
