import json
import unittest
from unittest.mock import patch

from tools.mirror_download import (
    aptoide_link,
    apkpure_link,
    _uptodown_page_version,
    _uptodown_download_url,
    _uptodown_scrape_link,
    _uptodown_html_file_id,
    _uptodown_auth_token,
    _uptodown_client_headers,
    uptodown_link,
    _download_uptodown_cdn,
    download_from_mirror,
    mirror_file_type,
)


class MirrorDownloaderTests(unittest.TestCase):
    @patch("tools.mirror_download.http_get")
    def test_apkpure_exact_version_from_mobile_api(self, mock_get):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.payload).encode()

        mock_get.return_value = Response({
            "version_list": [
                {
                    "version_name": "0.26.1",
                    "asset": {
                        "type": "APK",
                        "url": "https://cdn.example.test/audiorelay-0.26.1.apk",
                    },
                },
            ]
        })
        self.assertEqual(
            apkpure_link("com.azefsw.audioconnect", "audiorelay-stream-audio-mic", "0.26.1"),
            "https://cdn.example.test/audiorelay-0.26.1.apk",
        )

    @patch("tools.mirror_download._uptodown_html_file_id", return_value=67890)
    @patch("tools.mirror_download._uptodown_api_get_json")
    @patch("tools.mirror_download._uptodown_auth_token", return_value="test-token")
    def test_uptodown_exact_version_from_eapi(
        self, mock_auth, mock_api, mock_file_id
    ):
        mock_api.side_effect = [
            {"data": {"appID": 12345}},
            {"data": {
                "downloadURL": "https://cdn.example.test/audiorelay-0.26.1.apk"
            }},
        ]
        self.assertEqual(
            uptodown_link("com.azefsw.audioconnect", "audiorelay", "0.26.1"),
            "https://cdn.example.test/audiorelay-0.26.1.apk",
        )
        mock_auth.assert_called()
        mock_file_id.assert_called_once_with("audiorelay", 12345, "0.26.1", "apk")
        self.assertIn("/apps/byPackagename/", mock_api.call_args_list[0].args[0])
        self.assertIn(
            "/apps/12345/file/67890/downloadUrl",
            mock_api.call_args_list[1].args[0],
        )


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

    def test_mirror_file_type_overrides_source_default(self):
        cfg = {
            "source": {
                "file_type": "apk",
                "mirrors": [
                    {"type": "apkpure", "file_type": "xapk"},
                    {"type": "uptodown", "file_type": "apk"},
                    {"type": "aptoide"},
                ],
            }
        }
        self.assertEqual(mirror_file_type(cfg, "apkpure"), "xapk")
        self.assertEqual(mirror_file_type(cfg, "uptodown"), "apk")
        self.assertEqual(mirror_file_type(cfg, "aptoide"), "apk")

    def test_uptodown_selects_requested_file_type(self):
        payload = {
            "data": [
                {
                    "version": "14.34.0",
                    "fileID": 1,
                    "kindFile": "XAPK",
                    "versionURL": {"url": "https://example.test/x", "extraURL": "a", "versionID": 1},
                },
                {
                    "version": "14.34.0",
                    "fileID": 2,
                    "kindFile": "APK",
                    "versionURL": {"url": "https://example.test/a", "extraURL": "b", "versionID": 2},
                },
            ]
        }
        with patch("tools.mirror_download.http_read", return_value=json.dumps(payload).encode()):
            self.assertEqual(
                _uptodown_html_file_id("pinterest", 123, "14.34.0", "apk"),
                2,
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


    @patch("tools.mirror_download.download_url")
    def test_uptodown_cdn_falls_back_after_526(self, mock_download):
        class HttpError(Exception):
            def __init__(self, status):
                self.response = type("Response", (), {"status_code": status})()
                super().__init__(f"HTTP Error {status}")

        mock_download.side_effect = [
            HttpError(526),
            HttpError(410),
            None,
        ]
        _download_uptodown_cdn(
            "https://dw.uptodown.com/dwn/token-123",
            "out.apk",
        )
        self.assertEqual(mock_download.call_count, 3)
        self.assertIn("dw1.uptodown.com", mock_download.call_args_list[1].args[0])
        self.assertIn("dw8.uptodown.com", mock_download.call_args_list[2].args[0])

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
        mock_link.assert_called_once_with(
            "com.example.test", "example", "1.2.3", prefer="APK")
        mock_download.assert_called_once_with("https://example.test/a.apk", "out.apk")

    @patch("tools.mirror_download.download_url")
    @patch("tools.mirror_download.apkpure_link", return_value="https://example.test/a.xapk")
    def test_download_from_mirror_apkpure_prefers_xapk_for_bundle_configs(
        self, mock_link, mock_download
    ):
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {
                "type": "mirrors",
                "file_type": "xapk",
                "mirrors": [{"type": "apkpure", "name": "example"}],
            },
        }
        self.assertEqual(
            download_from_mirror("apkpure", cfg, "universal", "1.2.3", "out.xapk"),
            "1.2.3",
        )
        mock_link.assert_called_once_with(
            "com.example.test", "example", "1.2.3", prefer="XAPK")
        mock_download.assert_called_once_with("https://example.test/a.xapk", "out.xapk")

    @patch("tools.mirror_download.http_get")
    def test_apkpure_falls_back_to_xapk_when_no_apk_asset(self, mock_get):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.payload).encode()

        mock_get.return_value = Response({
            "version_list": [
                {
                    "version_name": "1.4.1",
                    "asset": {
                        "type": "XAPK",
                        "url": "https://cdn.example.test/native-camera-1.4.1.xapk",
                    },
                },
            ]
        })
        self.assertEqual(
            apkpure_link("com.rawcam.app", "native-camera", "1.4.1"),
            "https://cdn.example.test/native-camera-1.4.1.xapk",
        )

    @patch("tools.mirror_download.http_get")
    def test_apkpure_prefers_asset_type_matching_config(self, mock_get):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps(self.payload).encode()

        mock_get.return_value = Response({
            "version_list": [
                {
                    "version_name": "1.4.1",
                    "asset": {
                        "type": "APK",
                        "url": "https://cdn.example.test/app-1.4.1.apk",
                    },
                },
                {
                    "version_name": "1.4.1",
                    "asset": {
                        "type": "XAPK",
                        "url": "https://cdn.example.test/app-1.4.1.xapk",
                    },
                },
            ]
        })
        self.assertEqual(
            apkpure_link("com.example.test", "example", "1.4.1"),
            "https://cdn.example.test/app-1.4.1.apk",
        )
        self.assertEqual(
            apkpure_link("com.example.test", "example", "1.4.1", prefer="XAPK"),
            "https://cdn.example.test/app-1.4.1.xapk",
        )

    @patch("tools.mirror_download._download_uptodown_cdn")
    @patch("tools.mirror_download._uptodown_scrape_link", return_value="https://example.test/fallback.apk")
    @patch("tools.mirror_download.uptodown_link")
    def test_download_from_mirror_uptodown_falls_back_after_auth_410(
        self, mock_link, mock_scrape, mock_download
    ):
        import urllib.error

        mock_link.side_effect = urllib.error.HTTPError(
            "https://www.uptodown.app/eapi/auth/token",
            410, "Gone", {}, None,
        )
        cfg = {
            "id": "test",
            "package": "com.example.test",
            "source": {"mirrors": [{"type": "uptodown", "name": "example"}]},
        }
        self.assertEqual(
            download_from_mirror("uptodown", cfg, "universal", "1.2.3", "out.apk"),
            "1.2.3",
        )
        mock_link.assert_called_once_with("com.example.test", "example", "1.2.3", "apk")
        mock_scrape.assert_called_once_with("example", "1.2.3", "apk")
        mock_download.assert_called_once_with("https://example.test/fallback.apk", "out.apk")

    @patch("tools.mirror_download._download_uptodown_cdn")
    @patch("tools.mirror_download.uptodown_link", return_value="https://example.test/a.apk")
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
        mock_link.assert_called_once_with("com.example.test", "example", "1.2.3", "apk")
        mock_download.assert_called_once_with("https://example.test/a.apk", "out.apk")

    @patch("tools.mirror_download.http_read")
    def test_uptodown_exact_version_on_later_history_page(self, mock_read):
        page = [{"version": f"0.{i}.0", "fileID": i} for i in range(50)]
        page2 = [{"version": "0.26.1", "fileID": 67890}]
        mock_read.side_effect = [
            json.dumps({"data": page}).encode(),
            json.dumps({"data": page2}).encode(),
        ]
        self.assertEqual(
            _uptodown_html_file_id("audiorelay", 12345, "0.26.1"),
            67890,
        )
        self.assertIn(
            "/apps/12345/versions/2", mock_read.call_args_list[1].args[0]
        )

    def test_uptodown_client_headers_use_bearer_auth(self):
        headers = _uptodown_client_headers(token="jwt-token")
        self.assertEqual(headers["Authorization"], "Bearer jwt-token")
        self.assertEqual(headers["Identificador"], "Uptodown_Android")
        self.assertEqual(headers["Identificador-Version"], "739")
        self.assertNotIn("APIKEY", headers)

    @patch("tools.mirror_download.time.sleep")
    @patch("urllib.request.urlopen")
    def test_uptodown_auth_retries_transient_410(self, mock_urlopen, mock_sleep):
        import urllib.error

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return json.dumps({"token": "a.b.c"}).encode()

        def gone():
            return urllib.error.HTTPError(
                "https://www.uptodown.app/eapi/auth/token",
                410, "Gone", {}, None,
            )

        mock_urlopen.side_effect = [gone(), gone(), Response()]
        self.assertEqual(_uptodown_auth_token(), "a.b.c")
        self.assertEqual(mock_urlopen.call_count, 3)
        self.assertEqual(mock_sleep.call_count, 2)

    @patch("tools.mirror_download.time.sleep")
    @patch("urllib.request.urlopen")
    def test_uptodown_auth_gives_up_after_retries(self, mock_urlopen, mock_sleep):
        import urllib.error

        def gone(*args, **kwargs):
            raise urllib.error.HTTPError(
                "https://www.uptodown.app/eapi/auth/token",
                410, "Gone", {}, None,
            )

        mock_urlopen.side_effect = gone
        with self.assertRaises(urllib.error.HTTPError):
            _uptodown_auth_token(retries=2)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("tools.mirror_download.time.sleep")
    @patch("tools.mirror_download.http_read")
    def test_uptodown_scrape_retries_transient_410(self, mock_read, mock_sleep):
        class HttpError(Exception):
            def __init__(self, status):
                self.response = type("Response", (), {"status_code": status})()
                super().__init__(f"HTTP Error {status}")

        mock_read.side_effect = [
            HttpError(410),
            b'<div id="detail-app-name" data-code="123"></div>',
            b'{"data":[{"version":"0.26.1","versionURL":{"url":"https://audiorelay.en.uptodown.com/android/download","extraURL":"x","versionID":26100}}]}',
            b'<div class="version">0.26.1</div><button id="detail-download-button" data-url="token-123"></button>',
        ]
        self.assertEqual(
            _uptodown_scrape_link("audiorelay", "0.26.1"),
            "https://dw.uptodown.com/dwn/token-123",
        )
        self.assertEqual(mock_sleep.call_count, 1)

    @patch("tools.mirror_download.http_read")
    def test_uptodown_scrape_exact_version(self, mock_read):
        mock_read.side_effect = [
            b'<div id="detail-app-name" data-code="123"></div>',
            b'{"data":[{"version":"0.26.1","versionURL":{"url":"https://audiorelay.en.uptodown.com/android/download","extraURL":"x","versionID":26100}}]}',
            b'<div class="version">0.26.1</div><button id="detail-download-button" data-url="token-123"></button>',
        ]
        self.assertEqual(
            _uptodown_scrape_link("audiorelay", "0.26.1"),
            "https://dw.uptodown.com/dwn/token-123",
        )

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
