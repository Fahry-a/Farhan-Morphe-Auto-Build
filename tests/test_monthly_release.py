import unittest

from tools.monthly_release import rebuild_release_body, render_section, stale_assets, upsert_section


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


if __name__ == "__main__":
    unittest.main()
