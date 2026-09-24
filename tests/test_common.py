import os
import stat
import tempfile
import unittest
import zipfile

from unittest.mock import patch

from tools.common import (
    _locate_aapt,
    resolve_arch_entry,
    source_architecture_entries,
    validate_package,
)


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

    def test_mirror_source_defaults_to_universal(self):
        source = {"type": "mirrors"}
        self.assertEqual(source_architecture_entries(source), [{"name": "universal"}])
        self.assertEqual(resolve_arch_entry(source, None)["name"], "universal")

    def test_mirror_source_accepts_legacy_explicit_universal(self):
        self.assertEqual(
            source_architecture_entries({
                "type": "mirrors",
                "archs": [{"name": "universal"}],
            }),
            [{"name": "universal"}],
        )

    def test_mirror_source_rejects_per_abi_matrix(self):
        with self.assertRaisesRegex(ValueError, "implicit universal"):
            source_architecture_entries({
                "type": "mirrors",
                "archs": [
                    {"name": "arm64-v8a"},
                    {"name": "armeabi-v7a"},
                ],
            })

    def test_validate_apk_shape(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            self.assertGreater(validate_package(path, "apk"), 0)

    def _fake_aapt(self, td, version, package=None):
        path = os.path.join(td, "aapt")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
            if package:
                fh.write("echo \"package: name='" + package + "'\"\n")
            fh.write("echo \"versionName='" + version + "'\"\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def _fake_aapt_with_native(self, td, version, native_codes):
        path = os.path.join(td, "aapt-native")
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
            fh.write("echo \"versionName='" + version + "'\"\n")
            values = " ".join("'" + code + "'" for code in native_codes)
            fh.write("echo \"native-code: " + values + "\"\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return path

    def test_validate_universal_requires_both_arm_abis(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            aapt = self._fake_aapt_with_native(td, "1.2.3", ["arm64-v8a"])
            with self.assertRaisesRegex(RuntimeError, "missing armeabi-v7a"):
                validate_package(
                    path, "apk", expected_version="1.2.3",
                    aapt_path=aapt, expected_arch="universal",
                )

    def test_validate_universal_accepts_both_arm_abis(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            aapt = self._fake_aapt_with_native(
                td, "1.2.3", ["arm64-v8a", "armeabi-v7a", "x86_64"]
            )
            self.assertGreater(
                validate_package(
                    path, "apk", expected_version="1.2.3",
                    aapt_path=aapt, expected_arch="universal",
                ), 0
            )

    def test_validate_universal_bundle_unions_split_abis(self):
        with tempfile.TemporaryDirectory() as td:
            base = os.path.join(td, "base.apk")
            arm64 = os.path.join(td, "arm64.apk")
            arm32 = os.path.join(td, "arm32.apk")
            for path in (base, arm64, arm32):
                with zipfile.ZipFile(path, "w") as zf:
                    zf.writestr("AndroidManifest.xml", b"x" * 100)
                    zf.writestr("classes.dex", b"x" * 1_000_000)
            bundle = os.path.join(td, "base.apkm")
            with zipfile.ZipFile(bundle, "w") as zf:
                zf.write(base, "base.apk")
                zf.write(arm64, "split_config.arm64_v8a.apk")
                zf.write(arm32, "split_config.armeabi_v7a.apk")
            # This fake aapt reports the same version for the base check and
            # both ARM ABIs for the architecture check.
            aapt = self._fake_aapt_with_native(
                td, "1.2.3", ["arm64-v8a", "armeabi-v7a"]
            )
            self.assertGreater(
                validate_package(
                    bundle, "apkm", expected_version="1.2.3",
                    aapt_path=aapt, expected_arch="universal",
                ), 0
            )

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

    def test_validate_exact_package_identity(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            aapt = self._fake_aapt(td, "1.2.3", "com.example.right")
            self.assertGreater(
                validate_package(
                    path, "apk", expected_version="1.2.3",
                    expected_package="com.example.right", aapt_path=aapt,
                ), 0
            )
            with self.assertRaisesRegex(RuntimeError, "com.example.wrong"):
                validate_package(
                    path, "apk", expected_version="1.2.3",
                    expected_package="com.example.wrong", aapt_path=aapt,
                )

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

    def _fake_sdk(self, td):
        for ver in ("34.0.0", "37.0.0"):
            d = os.path.join(td, "build-tools", ver)
            os.makedirs(d)
            path = os.path.join(d, "aapt")
            with open(path, "w") as fh:
                fh.write("#!/bin/sh\n")
                fh.write("echo \"versionName='1.2.3'\"\n")
            os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
        return td

    def test_locate_aapt_prefers_highest_build_tools(self):
        with tempfile.TemporaryDirectory() as td:
            sdk = self._fake_sdk(td)
            with patch.dict(os.environ, {"ANDROID_HOME": sdk}), \
                 patch("tools.common.shutil.which", return_value=None):
                os.environ.pop("AAPT", None)
                found = _locate_aapt()
            self.assertTrue(found.endswith(os.path.join("37.0.0", "aapt")), found)

    def test_validate_exact_version_finds_aapt_via_env(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "base.apk")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("AndroidManifest.xml", b"x" * 100)
                zf.writestr("classes.dex", b"x" * 1_000_000)
            aapt = self._fake_aapt(td, "1.2.3")
            with patch.dict(os.environ, {"AAPT": aapt}), \
                 patch("tools.common.shutil.which", return_value=None):
                self.assertGreater(
                    validate_package(path, "apk", expected_version="1.2.3"), 0
                )


if __name__ == "__main__":
    unittest.main()
