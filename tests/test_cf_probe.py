import unittest

from tools.cf_challenge_probe import detect_markers, page_verdict


class CfProbeTests(unittest.TestCase):
    def test_detect_markers_is_case_insensitive(self):
        self.assertEqual(
            detect_markers("JUST A MOMENT... performing SECURITY verification"),
            ["Just a moment", "Performing security verification"],
        )
        self.assertEqual(detect_markers("ordinary page content"), [])

    def test_verdict_challenged_on_title_or_markers(self):
        self.assertEqual(
            page_verdict({"title": "Just a moment...", "markers": []}),
            "challenged",
        )
        self.assertEqual(
            page_verdict({"title": "App page", "markers": ["Verify you are human"]}),
            "challenged",
        )

    def test_verdict_clear_requires_expected_content(self):
        self.assertEqual(
            page_verdict({
                "title": "App page",
                "markers": [],
                "body_preview": "hi",
                "expected_content_found": True,
            }),
            "clear",
        )
        self.assertEqual(
            page_verdict({
                "title": "App page",
                "markers": [],
                "body_preview": "hi",
                "expected_content_found": False,
            }),
            "unknown",
        )
        self.assertEqual(page_verdict({}), "unknown")


if __name__ == "__main__":
    unittest.main()
