import json
import unittest
from unittest.mock import patch

from tools.mirror_download import (
    aptoide_link,
    _uptodown_page_version,
    _uptodown_download_url,
    download_from_mirror,
)


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

    @patch("tools.mirror_download.download_apkmirror")
    def test_download_from_mirror_apkmirror(self, mock_download):
        mock_download.return_value = "1.2.3"
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {
                "type": "mirrors",
                "mirrors": [{"type": "apkmirror"}],
                "archs": [{"name": "universal"}],
            },
        }
        self.assertEqual(
            download_from_mirror("apkmirror", cfg, "universal", "1.2.3", "out.apk"),
            "1.2.3",
        )
        mock_download.assert_called_once_with(
            cfg, "universal", "1.2.3", "out.apk"
        )

    @patch("tools.mirror_download.download_url")
    @patch("tools.mirror_download.apkpure_link", return_value="https://example.test/a.apk")
    def test_download_from_mirror_apkpure(self, mock_link, mock_download):
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {"mirrors": [{"type": "apkpure", "name": "example"}]},
        }
        self.assertEqual(
            download_from_mirror("apkpure", cfg, "universal", "1.2.3", "out.apk"),
            "1.2.3",
        )
        mock_link.assert_called_once_with("com.example.test", "example", "1.2.3")
        mock_download.assert_called_once_with("https://example.test/a.apk", "out.apk")

    @patch("tools.mirror_download.download_url")
    @patch("tools.mirror_download.uptodown_link", return_value="https://example.test/u.apk")
    def test_download_from_mirror_uptodown(self, mock_link, mock_download):
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {"mirrors": [{"type": "uptodown", "name": "example"}]},
        }
        self.assertEqual(
            download_from_mirror("uptodown", cfg, "universal", "1.2.3", "out.apk"),
            "1.2.3",
        )
        mock_link.assert_called_once_with("com.example.test", "example", "1.2.3")
        mock_download.assert_called_once_with("https://example.test/u.apk", "out.apk")

    @patch("tools.mirror_download.download_url")
    @patch("tools.mirror_download.aptoide_link", return_value="https://example.test/t.apk")
    def test_download_from_mirror_aptoide(self, mock_link, mock_download):
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {"mirrors": [{"type": "aptoide"}]},
        }
        self.assertEqual(
            download_from_mirror("aptoide", cfg, "universal", "1.2.3", "out.apk"),
            "1.2.3",
        )
        mock_link.assert_called_once_with("com.example.test", "1.2.3", "universal")
        mock_download.assert_called_once_with("https://example.test/t.apk", "out.apk")

    def test_download_from_mirror_rejects_unknown_source(self):
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {"mirrors": []},
        }
        with self.assertRaisesRegex(RuntimeError, "Unsupported mirror"):
            download_from_mirror("unknown", cfg, "universal", "1.2.3", "out.apk")


if __name__ == "__main__":
    unittest.main()
