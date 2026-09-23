import unittest
from pathlib import Path
from unittest.mock import patch
import tempfile

from apkd.models import Artifact, DownloadRequest

from tools.mirror_download import (
    _apkmirror_slug_map,
    _arch_candidates,
    _apkmirror_request_params,
    _derive_apkmirror_slug,
    _resolve_apkmirror_slug,
    _prefer_xapk,
    download_from_mirror,
    mirror_file_type,
)


class FakeProvider:
    def __init__(self, artifact):
        self._artifact = artifact
        self.seen_request = None
        self.downloaded_to = None

    def resolve_request(self, request):
        self.seen_request = request
        return self._artifact

    def download(self, artifact, destination):
        self.downloaded_to = Path(destination)
        self.downloaded_to.parent.mkdir(parents=True, exist_ok=True)
        self.downloaded_to.write_bytes(b"fake-apk-bytes")
        return 15


def _cfg(mirrors, file_type="apk", archs=None, package="com.example.test"):
    return {
        "id": "test",
        "package": package,
        "source": {
            "type": "mirrors",
            "file_type": file_type,
            "mirrors": mirrors,
            "archs": archs or [{"name": "universal"}],
        },
    }


class MirrorDownloaderTests(unittest.TestCase):
    def test_mirror_file_type_overrides_source_default(self):
        cfg = _cfg([
            {"type": "apkpure", "file_type": "xapk"},
            {"type": "apkcombo", "file_type": "apk"},
            {"type": "aptoide"},
        ])
        self.assertEqual(mirror_file_type(cfg, "apkpure"), "xapk")
        self.assertEqual(mirror_file_type(cfg, "apkcombo"), "apk")
        self.assertEqual(mirror_file_type(cfg, "aptoide"), "apk")

    def test_prefer_xapk_follows_effective_file_type(self):
        cfg = _cfg([{"type": "apkpure", "file_type": "xapk"}])
        self.assertTrue(_prefer_xapk(cfg, "apkpure"))
        cfg = _cfg([{"type": "apkpure"}])
        self.assertFalse(_prefer_xapk(cfg, "apkpure"))

    def test_derive_apkmirror_slug_from_arch_entries(self):
        cfg = _cfg(
            [{"type": "apkmirror"}],
            archs=[{
                "name": "universal",
                "variant_url": "https://www.apkmirror.com/apk/google-inc/photos/variant-xyz/",
                "slug_filter": "/apk/google-inc/photos/",
                "version_slug": "google-photos-",
            }],
            package="com.google.android.apps.photos",
        )
        self.assertEqual(
            _derive_apkmirror_slug(cfg), ("google-inc", "photos")
        )
        self.assertEqual(
            _apkmirror_slug_map(cfg, cfg["source"]["mirrors"][0]),
            {"com.google.android.apps.photos": ("google-inc", "photos")},
        )

    def test_resolve_apkmirror_slug_prefers_explicit_mirror_entry(self):
        cfg = _cfg(
            [{"type": "apkmirror", "org": "admtorrent",
              "repo": "advanced-download-manager"}],
            package="com.dv.adm",
        )
        mirror = cfg["source"]["mirrors"][0]
        self.assertEqual(
            _resolve_apkmirror_slug(cfg, mirror),
            ("admtorrent", "advanced-download-manager"),
        )
        self.assertEqual(
            _apkmirror_slug_map(cfg, mirror),
            {"com.dv.adm": ("admtorrent", "advanced-download-manager")},
        )

    def test_resolve_apkmirror_slug_supports_slug_string(self):
        cfg = _cfg(
            [{"type": "apkmirror", "slug": "x-corp/twitter"}],
            package="com.twitter.android",
        )
        self.assertEqual(
            _resolve_apkmirror_slug(cfg, cfg["source"]["mirrors"][0]),
            ("x-corp", "twitter"),
        )

    def test_derive_apkmirror_slug_returns_none_without_arch_hints(self):
        cfg = _cfg([{"type": "apkmirror"}])
        self.assertIsNone(_derive_apkmirror_slug(cfg))
        self.assertEqual(_apkmirror_slug_map(cfg, cfg["source"]["mirrors"][0]), {})

    def _run_provider(self, kind, file_type="apk", arch="universal",
                      version="1.2.3", artifact_version="1.2.3"):
        cfg = _cfg([{"type": kind, "file_type": file_type}])
        extension = "." + file_type
        artifact = Artifact(kind, cfg["package"], artifact_version,
                            f"https://example.test/a{extension}", extension, arch)
        fake = FakeProvider(artifact)
        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / "out.apk")
            with patch("tools.mirror_download._get_provider", return_value=fake) as mock_get:
                result = download_from_mirror(kind, cfg, arch, version, output)
            self.assertEqual(result, artifact_version)
            self.assertTrue(Path(output).is_file())
            self.assertIsInstance(fake.seen_request, DownloadRequest)
            self.assertEqual(fake.seen_request.package, cfg["package"])
            self.assertEqual(fake.seen_request.version, version)
            self.assertEqual(fake.seen_request.arch, arch)
            return fake, mock_get, cfg

    def test_download_from_mirror_apkpure(self):
        fake, _, _ = self._run_provider("apkpure")
        self.assertFalse(fake.seen_request.prefer_xapk)

    def test_download_from_mirror_prefers_xapk_for_bundle_configs(self):
        fake, _, _ = self._run_provider("apkpure", file_type="xapk")
        self.assertTrue(fake.seen_request.prefer_xapk)

    def test_download_from_mirror_apkcombo(self):
        self._run_provider("apkcombo")

    def test_download_from_mirror_aptoide(self):
        fake, _, _ = self._run_provider("aptoide")
        self.assertEqual(fake.seen_request.package, "com.example.test")

    def test_download_from_mirror_apkmirror_passes_slug_map(self):
        cfg = _cfg(
            [{"type": "apkmirror", "org": "x-corp", "repo": "twitter"}],
            package="com.twitter.android",
        )
        artifact = Artifact("apkmirror", cfg["package"], "1.2.3",
                            "https://example.test/a.apk", ".apk")
        fake = FakeProvider(artifact)
        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / "out.apk")
            with patch("tools.mirror_download._get_provider",
                       return_value=fake) as mock_get:
                download_from_mirror("apkmirror", cfg, "universal", "1.2.3", output)
            mock_get.assert_called_once_with(
                "apkmirror",
                slug_map={"com.twitter.android": ("x-corp", "twitter")},
            )
            # dpi defaults to any-density so density-scoped rows are not filtered out.
            self.assertEqual(fake.seen_request.dpi, "*")
            self.assertIsNone(fake.seen_request.min_sdk)

    def test_apkmirror_request_params_prefer_mirror_overrides(self):
        cfg = _cfg([{"type": "apkmirror", "arch": "arm64-v8a",
                     "dpi": "480dpi", "min_sdk": 28}])
        mirror = cfg["source"]["mirrors"][0]
        self.assertEqual(
            _apkmirror_request_params(cfg, mirror, "universal"),
            ("arm64-v8a", "480dpi", 28),
        )
        self.assertEqual(
            _apkmirror_request_params(cfg, {}, "universal"),
            ("universal", "*", None),
        )

    def test_arch_candidates_retry_abi_for_universal(self):
        self.assertEqual(_arch_candidates("universal"), ["universal", "arm64-v8a"])
        self.assertEqual(_arch_candidates("arm64"), ["arm64"])

    def test_download_from_mirror_apkmirror_retries_arch(self):
        from apkd.models import ProviderError

        cfg = _cfg([{"type": "apkmirror", "org": "o", "repo": "r"}],
                   package="com.example.test")
        artifact = Artifact("apkmirror", cfg["package"], "1.2.3",
                            "https://example.test/a.apk", ".apk", "arm64-v8a")

        seen = []

        class FlakyProvider(FakeProvider):
            def resolve_request(self, request):
                seen.append(request.arch)
                if request.arch == "universal":
                    raise ProviderError("no suitable variant", provider="apkmirror")
                return super().resolve_request(request)

        fake = FlakyProvider(artifact)
        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / "out.apk")
            with patch("tools.mirror_download._get_provider", return_value=fake):
                result = download_from_mirror(
                    "apkmirror", cfg, "universal", "1.2.3", output)
            self.assertEqual(result, "1.2.3")
            self.assertEqual(seen, ["universal", "arm64-v8a"])

    def test_download_from_mirror_rejects_bundle_for_apk_config(self):
        cfg = _cfg([{"type": "apkmirror", "org": "o", "repo": "r"}])
        artifact = Artifact("apkmirror", cfg["package"], "1.2.3",
                            "https://example.test/a.apkm", ".apkm", "universal",
                            {"variant_type": "bundle"})
        fake = FakeProvider(artifact)
        with tempfile.TemporaryDirectory() as td:
            output = str(Path(td) / "out.apk")
            with patch("tools.mirror_download._get_provider", return_value=fake):
                with self.assertRaisesRegex(RuntimeError, "expects file_type=apk"):
                    download_from_mirror(
                        "apkmirror", cfg, "universal", "1.2.3", output)

    def test_download_from_mirror_rejects_version_mismatch(self):
        with self.assertRaisesRegex(RuntimeError, "expected 1.2.3"):
            self._run_provider("apkpure", version="1.2.3",
                               artifact_version="9.9.9")

    def test_download_from_mirror_rejects_uptodown_with_migration_hint(self):
        cfg = _cfg([{"type": "apkcombo"}])
        with self.assertRaisesRegex(RuntimeError, "apkcombo"):
            download_from_mirror("uptodown", cfg, "universal", "1.2.3", "out.apk")

    def test_download_from_mirror_rejects_unknown_source(self):
        cfg = _cfg([])
        with self.assertRaisesRegex(RuntimeError, "Unsupported mirror"):
            download_from_mirror("unknown", cfg, "universal", "1.2.3", "out.apk")


if __name__ == "__main__":
    unittest.main()
