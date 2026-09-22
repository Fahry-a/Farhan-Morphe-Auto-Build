import unittest
from unittest.mock import patch

from tools.mirror_download import aptoide_link


class MirrorDownloaderTests(unittest.TestCase):
    @patch("tools.mirror_download.http_get")
    def test_aptoide_exact_version(self, mock_get):
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                import json
                return json.dumps(self.payload).encode()

        mock_get.side_effect = [
            Response({"datalist": {"list": [{"file": {"vername": "1.2.3", "vercode": 123}}]}}),
            Response({"data": {"file": {"path": "https://example.test/app.apk"}}}),
        ]
        self.assertEqual(
            aptoide_link("com.example.app", "1.2.3", "universal"),
            "https://example.test/app.apk",
        )


if __name__ == "__main__":
    unittest.main()
