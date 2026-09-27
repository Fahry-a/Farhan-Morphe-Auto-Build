import unittest

from tools.monthly_release import (
    normalize_mpp_version,
    rebuild_release_body,
    render_section,
    stale_assets,
    stored_mpp_for_section,
    upsert_section,
)


class MonthlyReleaseTests(unittest.TestCase):
    def test_upsert_existing_section(self):
        body = "intro\n\n<!-- app:brave:arm64 -->\nold\n<!-- /app:brave:arm64 -->\n\nfooter\n"
        result = upsert_section(body, "brave:arm64", "<!-- app:brave:arm64 -->\nnew\n<!-- /app:brave:arm64 -->")
        self.assertIn("\nnew\n", result)
        self.assertNotIn("\nold\n", result)
        self.assertIn("footer", result)

    def test_upsert_missing_section(self):
        result = upsert_section("intro\n", "brave:arm64", "<!-- app:brave:arm64 -->\nnew\n<!-- /app:brave:arm64 -->")
        self.assertIn("intro", result)
        self.assertIn("new", result)
    def test_render_section_has_download_links(self):
        build = {
            "app": "brave",
            "display": "Brave Browser",
            "arch": "arm32",
            "apk_version": "1.95.104",
            "mpp_version": "1.0.0",
            "build_date": "2026-09-22",
            "files": ["brave-arm32-v1.95.104.apk"],
        }
        result = render_section(build, "Fahry-a/Farhan-Morphe-Auto-Build", "2026-09")
        self.assertIn("<details open>", result)
        self.assertIn("[brave-arm32-v1.95.104.apk]", result)
        self.assertIn("/releases/download/2026-09/brave-arm32-v1.95.104.apk", result)

    def test_rebuild_release_body_sorts_sections(self):
        body = (
            "<!-- app:x:universal -->\nX\n<!-- /app:x:universal -->\n\n"
            "<!-- app:adm:universal -->\nA\n<!-- /app:adm:universal -->\n"
        )
        build = {
            "app": "native-camera",
            "display": "Native Camera",
            "arch": "universal",
            "apk_version": "1.4.1",
            "mpp_version": "1.0.0",
            "build_date": "2026-09-22",
            "files": ["native-camera-universal-v1.4.1.apk"],
        }
        result = rebuild_release_body(
            body, [build], "Fahry-a/Farhan-Morphe-Auto-Build", "2026-09"
        )
        self.assertLess(result.index("app:adm:universal"), result.index("app:native-camera:universal"))
        self.assertLess(result.index("app:native-camera:universal"), result.index("app:x:universal"))


    def test_stale_assets(self):
        existing = [
            "brave-arm64-v1.0.0.apk",
            "brave-arm64-v1.1.0.apk",
            "brave-arm32-v1.1.0.apk",
            "google-photos-universal-v1.0.0.apk",
        ]
        self.assertEqual(
            stale_assets(existing, "brave", "arm64", ["brave-arm64-v1.1.0.apk"]),
            ["brave-arm64-v1.0.0.apk"],
        )

    def test_stored_mpp_for_section(self):
        build = {
            "app": "adm",
            "display": "Advanced Download Manager",
            "arch": "universal",
            "apk_version": "14.0.39",
            "mpp_version": "1.53.0",
            "build_date": "2026-09-24",
            "files": ["adm-universal-v14.0.39.apk"],
        }
        body = rebuild_release_body(
            "", [build], "Fahry-a/Farhan-Morphe-Auto-Build", "2026-09"
        )
        self.assertEqual(stored_mpp_for_section(body, "adm", "universal"), "1.53.0")
        # Other sections must not leak into this app/arch lookup.
        self.assertIsNone(stored_mpp_for_section(body, "brave", "arm64"))

    def test_stored_mpp_missing_section(self):
        self.assertIsNone(stored_mpp_for_section("intro\n", "brave", "arm64"))

    def test_stored_mpp_section_without_build_table(self):
        body = "<!-- app:brave:arm64 -->\nno table here\n<!-- /app:brave:arm64 -->"
        self.assertIsNone(stored_mpp_for_section(body, "brave", "arm64"))

    def test_normalize_mpp_version(self):
        self.assertEqual(normalize_mpp_version("v1.53.0"), "1.53.0")
        self.assertEqual(normalize_mpp_version("V1.53.0"), "1.53.0")
        self.assertEqual(normalize_mpp_version(" 1.53.0 "), "1.53.0")
        self.assertEqual(normalize_mpp_version(None), "")
        self.assertNotEqual(
            normalize_mpp_version("v1.45.0"), normalize_mpp_version("2.0.0")
        )


if __name__ == "__main__":
    unittest.main()
