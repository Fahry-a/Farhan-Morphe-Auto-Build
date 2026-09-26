#!/usr/bin/env python3
"""Manually download one exact Uptodown artifact through a visible browser.

This is intentionally not used by unattended CI. Uptodown currently issues the
final file URL only after an interactive Cloudflare Turnstile flow; the operator
completes that flow in the Chromium window and the script then validates the
result with the same exact-version/universal checks used by the build.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

from apkd.models import DownloadRequest, ProviderError
from apkd.providers.uptodown import UptodownProvider

try:
    from .common import validate_package
except ImportError:  # direct ``python tools/uptodown_browser.py`` execution
    from common import validate_package


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve and manually download an exact Uptodown package"
    )
    parser.add_argument("package", help="Android package name")
    parser.add_argument("--version", required=True, help="exact Uptodown version")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--app-slug", help="Uptodown slug, e.g. pinterest")
    parser.add_argument("--app-id", help="known Uptodown numeric app ID")
    parser.add_argument(
        "--cdp-url",
        help="CDP endpoint of a normal Chrome started by the operator, e.g. http://127.0.0.1:9222",
    )
    parser.add_argument(
        "--browser-channel",
        help="Playwright browser channel, e.g. chrome (CDP is recommended for Turnstile)",
    )
    parser.add_argument(
        "--manual-click",
        action="store_true",
        help="wait for you to click Uptodown's Download button manually",
    )
    parser.add_argument("--arch", default="universal")
    parser.add_argument("--prefer-xapk", action="store_true")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="use headless Chromium (cannot complete a human Turnstile)",
    )
    parser.add_argument(
        "--initial-wait", type=float, default=5.0,
        help="seconds to wait after page readiness before the first click",
    )
    parser.add_argument(
        "--retry-wait", type=float, default=5.0,
        help="seconds to wait before a bounded retry",
    )
    parser.add_argument(
        "--max-attempts", type=int, default=2,
        help="maximum normal click attempts (1-3)",
    )
    parser.add_argument(
        "--response-timeout", type=float, default=30.0,
        help="seconds to wait for each download-url response",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="resolve version/file metadata without opening a browser",
    )
    return parser


def _detect_container_type(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"Downloaded file is not a readable ZIP package: {exc}") from exc
    if any(name.lower().endswith(".apk") for name in names):
        return "bundle"
    if "AndroidManifest.xml" in names:
        return "apk"
    raise RuntimeError("Downloaded ZIP has neither an APK nor AndroidManifest.xml")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.metadata_only and args.output is None:
        parser.error("--output is required unless --metadata-only is used")
    if args.headless and args.cdp_url:
        parser.error("--headless cannot be combined with --cdp-url")
    if args.headless and args.manual_click:
        parser.error("--headless cannot be combined with --manual-click")
    if args.max_attempts < 1 or args.max_attempts > 3:
        parser.error("--max-attempts must be between 1 and 3")
    if args.headless:
        print(
            "Headless mode cannot solve an interactive Turnstile; this is a "
            "bounded diagnostic retry, not a challenge bypass.",
            file=sys.stderr,
        )
    provider = UptodownProvider(
        browser=True,
        cdp_url=args.cdp_url,
        channel=args.browser_channel,
        manual_click=args.manual_click,
        headless=args.headless,
        initial_wait=args.initial_wait,
        retry_wait=args.retry_wait,
        max_attempts=args.max_attempts,
        response_timeout=args.response_timeout,
    )
    request = DownloadRequest(
        package=args.package,
        version=args.version,
        arch=args.arch,
        prefer_xapk=args.prefer_xapk,
        timeout=args.timeout,
        app_slug=args.app_slug,
        app_id=args.app_id,
    )
    try:
        if args.metadata_only:
            target = provider.resolve_target(request)
            print(
                f"METADATA app_id={target.app_id} version={target.version} "
                f"kind={target.kind} file_id={target.file_id} "
                f"page={target.page_url}"
            )
            return 0
        artifact = provider.resolve_request(request)
        output = args.output
        if output.suffix.lower() != artifact.extension.lower():
            output = output.with_suffix(artifact.extension)
        output.parent.mkdir(parents=True, exist_ok=True)
        provider.download(artifact, output)
        actual_type = _detect_container_type(output)
        expected_type = artifact.extension.lstrip(".").lower()
        validation_type = actual_type
        if actual_type == "bundle":
            validation_type = (
                expected_type
                if expected_type in {"apkm", "xapk", "apks"}
                else "apkm"
            )
        entries = validate_package(
            str(output),
            validation_type,
            expected_version=args.version,
            expected_arch=args.arch,
            expected_package=args.package,
        )
        if actual_type != expected_type:
            raise RuntimeError(
                f"Uptodown returned a {actual_type} package, but the requested "
                f"file type is {expected_type}; re-run with the matching "
                "--prefer-xapk/--output option"
            )
    except (ProviderError, RuntimeError, ValueError) as exc:
        print(f"Uptodown browser download failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"OK provider={artifact.provider} version={artifact.version} "
        f"file={output} entries={entries} url={artifact.url}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
