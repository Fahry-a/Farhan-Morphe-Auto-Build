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
    parser.add_argument("--arch", default="universal")
    parser.add_argument("--prefer-xapk", action="store_true")
    parser.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="resolve version/file metadata without opening a browser",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.metadata_only and args.output is None:
        build_parser().error("--output is required unless --metadata-only is used")
    if args.headless:
        print(
            "Headless mode is not recommended for Uptodown Turnstile; "
            "use a visible browser window.",
            file=sys.stderr,
        )
    provider = UptodownProvider(browser=True)
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
        entries = validate_package(
            str(output),
            artifact.extension.lstrip("."),
            expected_version=args.version,
            expected_arch=args.arch,
        )
    except (ProviderError, RuntimeError, ValueError) as exc:
        print(f"Uptodown browser download failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"OK provider={artifact.provider} version={artifact.version} "
        f"file={args.output} entries={entries} url={artifact.url}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
