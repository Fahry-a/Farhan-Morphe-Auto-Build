import unittest

from tools.monthly_release import stale_assets, upsert_section


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
