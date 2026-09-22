import json
import unittest
from unittest.mock import patch

from tools.mirror_download import aptoide_link, _uptodown_page_version, _uptodown_download_url


class MirrorDownloaderTests(unittest.TestCase):
    @patch("tools.mirror_download.http_get")
    def test_aptoide_exact_version_current_response(self, mock_get):
        class Response:
            def __init__(self, payload):
                self.payload = payload
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return json.dumps(self.payload).encode()

        mock_get.side_effect = [
            Response({"info": {"status": "OK"}, "list": [
                {"file": {"vername": "0.26.1", "vercode": 26100}},
                {"file": {"vername": "0.25.5", "vercode": 25500}},
            ]}),
            Response({"data": {"file": {"path": "https://example.test/app.apk"}}}),
        ]
        self.assertEqual(
            aptoide_link("com.azefsw.audioconnect", "0.26.1", "universal"),
            "https://example.test/app.apk",
        )

    def test_uptodown_current_page_helpers(self):
        html = """
        <h1 id="detail-app-name">AudioRelay</h1>
        <div class="version">0.26.1</div>
        <button id="detail-download-button" data-url="token-123"></button>
        """
        self.assertEqual(_uptodown_page_version(html), "0.26.1")
        self.assertEqual(
            _uptodown_download_url(html),
            "https://dw.uptodown.com/dwn/token-123",
        )


if __name__ == "__main__":
    unittest.main()
