#!/usr/bin/env python3
"""Diagnostic probe for Cloudflare challenge behavior on a GitHub Actions runner."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

CHALLENGE_MARKERS = (
    "Just a moment",
    "Performing security verification",
    "Verify you are human",
    "Enable JavaScript and cookies",
    "Attention Required",
)

DEFAULT_URLS = (
    "https://capskip.com/captcha-demo/cloudflare-challenge/",
    "https://www.apkmirror.com/apk/pinterest/pinterest-one-destination-for-a-world-of-inspiration/pinterest-14-34-0-2-android-apk-download/",
)


def detect_markers(text: str) -> list[str]:
    lowered = (text or "").lower()
    return [marker for marker in CHALLENGE_MARKERS if marker.lower() in lowered]


def page_verdict(snapshot: dict) -> str:
    title = str(snapshot.get("title") or "")
    if "just a moment" in title.lower() or snapshot.get("markers"):
        return "challenged"
    if snapshot.get("expected_content_found") is True:
        return "clear"
    return "unknown"


async def snapshot_page(
    page, *, expected_selector: str | None = None, timeout_ms: int = 5000
) -> dict:
    snapshot: dict = {}
    try:
        snapshot["url"] = page.url
    except Exception:
        pass
    try:
        snapshot["title"] = await page.title()
    except Exception:
        pass
    try:
        body = await page.locator("body").inner_text(timeout=timeout_ms)
        snapshot["markers"] = detect_markers(body)
        snapshot["body_preview"] = body[:300]
    except Exception as exc:
        snapshot["body_error"] = f"{type(exc).__name__}: {exc}"
    if expected_selector:
        snapshot["expected_content_selector"] = expected_selector
        try:
            snapshot["expected_content_found"] = await page.locator(
                expected_selector
            ).first.is_visible(timeout=timeout_ms)
        except Exception:
            snapshot["expected_content_found"] = False
    try:
        snapshot["iframes"] = await page.evaluate(
            "() => Array.from(document.querySelectorAll('iframe')).map("
            "f => (f.src || '').slice(0, 120))"
        )
    except Exception:
        pass
    snapshot["verdict"] = page_verdict(snapshot)
    return snapshot


async def probe_url(url: str, *, settle: float, expected_selector: str | None, log) -> dict:
    from playwright.async_api import async_playwright

    result: dict = {"url": url}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        page = await browser.new_page()
        log(f"[probe] opening {url}")
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(settle)
        result["before"] = await snapshot_page(
            page, expected_selector=expected_selector
        )
        log(
            f"[probe] before: {result['before'].get('title')} "
            f"verdict={result['before']['verdict']}"
        )
        await asyncio.sleep(8)
        result["after"] = await snapshot_page(
            page, expected_selector=expected_selector
        )
        log(f"[probe] after: verdict={result['after']['verdict']}")
        await browser.close()
    return result


async def run_probe(urls: list[str], *, settle: float, log) -> dict:
    results = []
    for url in urls:
        expected = (
            "a.downloadButton[href*='/download/']"
            if "apkmirror.com" in url else None
        )
        try:
            results.append(
                await probe_url(
                    url,
                    settle=settle,
                    expected_selector=expected,
                    log=log,
                )
            )
        except Exception as exc:
            results.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})
    return {"results": results}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="Target URL (repeatable; defaults to capskip demo + APKMirror variant)",
    )
    parser.add_argument("--report", default="cf-probe-report.json")
    parser.add_argument("--settle", type=float, default=25.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    urls = args.url or list(DEFAULT_URLS)
    report = asyncio.run(run_probe(urls, settle=args.settle, log=print))
    Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
    print(f"report: {args.report}")
    for entry in report["results"]:
        before = entry.get("before", {}).get("verdict")
        after = entry.get("after", {}).get("verdict")
        print(f"- {entry['url']}: before={before} after={after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
