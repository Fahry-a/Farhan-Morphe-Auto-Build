import os
import tempfile
import unittest
from unittest.mock import patch

from tools.github_source import download_and_validate


class GitHubSourceTests(unittest.TestCase):
    @patch("tools.github_source.validate_package", return_value=42)
    @patch("tools.github_source.download_url")
    def test_download_and_validate_promotes_only_after_validation(
        self, mock_download, mock_validate
    ):
        with tempfile.TemporaryDirectory() as td:
            output = os.path.join(td, "base.apk")

            def write_partial(url, path, token=None):
                with open(path, "wb") as fh:
                    fh.write(b"partial")

            mock_download.side_effect = write_partial

            entries = download_and_validate(
                "https://example.test/base.apk",
                output,
                "apk",
                "1.2.3",
                "token",
            )

            self.assertEqual(entries, 42)
            with open(output, "rb") as fh:
                self.assertEqual(fh.read(), b"partial")
            self.assertFalse(os.path.exists(output + ".partial"))
            mock_validate.assert_called_once_with(
                output + ".partial",
                "apk",
                expected_version="1.2.3",
                expected_arch=None,
                expected_package=None,
            )

    @patch("tools.github_source.validate_package")
    @patch("tools.github_source.download_url")
    def test_download_and_validate_removes_partial_after_validation_failure(
        self, mock_download, mock_validate
    ):
        with tempfile.TemporaryDirectory() as td:
            output = os.path.join(td, "base.apk")

            def write_partial(url, path, token=None):
                with open(path, "wb") as fh:
                    fh.write(b"invalid")

            mock_download.side_effect = write_partial
            mock_validate.side_effect = RuntimeError("wrong version")

            with self.assertRaisesRegex(RuntimeError, "wrong version"):
                download_and_validate(
                    "https://example.test/base.apk",
                    output,
                    "apk",
                    "1.2.3",
                )

            self.assertFalse(os.path.exists(output))
            self.assertFalse(os.path.exists(output + ".partial"))


if __name__ == "__main__":
    unittest.main()
