import os
import stat
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

    def _fake_aapt(self, td, version):
        path = os.path.join(td, "aapt")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
            fh.write("echo \"versionName='" + version + "'\"\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_validate_exact_apk_version(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            aapt = self._fake_aapt(td, "1.2.3")
            self.assertGreater(validate_package(path, "apk", expected_version="1.2.3", aapt_path=aapt), 0)
            with self.assertRaises(RuntimeError):
                validate_package(path, "apk", expected_version="9.9.9", aapt_path=aapt)

    def test_validate_exact_bundle_version(self):
        with tempfile.TemporaryDirectory() as td:
            inner = os.path.join(td, "base.apk")
            bundle = os.path.join(td, "base.apkm")
            with zipfile.ZipFile(inner, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x")
                zf.writestr("classes.dex", b"x" * 1_000_000)
            with zipfile.ZipFile(bundle, "w") as zf:
                zf.write(inner, "base.apk")
            aapt = self._fake_aapt(td, "2.3.4")
            self.assertGreater(validate_package(bundle, "apkm", expected_version="2.3.4", aapt_path=aapt), 0)
            with self.assertRaises(RuntimeError):
                validate_package(bundle, "apkm", expected_version="1.0.0", aapt_path=aapt)
    def test_validate_bundle_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apkm")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("base.apk", b"x" * 1_000_000)
                zf.writestr("split_config.arm64_v8a.apk", b"x")
            self.assertGreater(validate_package(path, "apkm"), 0)


if __name__ == "__main__":
    unittest.main()
