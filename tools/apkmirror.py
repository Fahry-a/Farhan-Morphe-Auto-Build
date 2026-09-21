#!/usr/bin/env python3
"""Generic per-app APKMirror downloader (configured via apps/<id>.json).

Refactored from cuma-contoh.py (Google Photos only) into:
  --variant-url, --slug-filter, --version-slug, --exact-version

The strategy is unchanged:
  1. Fast path: curl_cffi with Chrome TLS impersonation.
  2. Fallback: Playwright headless Chromium for the Cloudflare JS challenge.

APKMirror flow (4 steps):
  variant list -> detail -download/ -> download.php confirmation -> final key= link.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

from common import resolve_arch_entry

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("beautifulsoup4 not installed. Run: pip install beautifulsoup4 curl_cffi playwright")
    sys.exit(1)

IMPERSONATE = "chrome131"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
}


def parse_version_tuple(ver_str):
    parts = re.findall(r"\d+", ver_str or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def normalize_version(ver):
    return (ver or "").replace("-", ".").rstrip(".")


def find_detail_link(soup, slug_filter, version_slug, exact_version=None):
    """Find an APKMirror detail link.

    slug_filter: e.g. "/apk/google-inc/photos/"
    version_slug: e.g. "google-photos-" (the part after the slug, before the version)
    exact_version: e.g. "7.92.0.977185651" -> exact match only (dots/dashes are equal).
    Without exact_version: pick the highest semantic version.
    """
    candidates = soup.find_all("div", class_=re.compile(r"list-widget|widget-area|table-row"))
    roots = candidates if candidates else [soup]
    found = []
    seen = set()
    want_norm = normalize_version(exact_version) if exact_version else None

    for root in roots:
        for a in root.find_all("a", href=True):
            href = a["href"]
            if slug_filter not in href:
                continue
            if not (href.endswith("-download/") or "android-apk-download" in href):
                continue
            link = urllib.parse.urljoin("https://www.apkmirror.com", href)
            if link in seen:
                continue
            seen.add(link)
            m = re.search(re.escape(version_slug) + r"([0-9\-]+)", href)
            ver = normalize_version(m.group(1)) if m else None
            if not ver:
                continue
            if want_norm and ver != want_norm:
                continue
            found.append((link, ver, parse_version_tuple(ver)))

    if not found:
        return None, None
    found.sort(key=lambda x: x[2], reverse=True)
    best_link, best_ver, _ = found[0]
    print(f"[APKMirror] Found {len(found)} candidate(s). Picked: {best_ver}")
    return best_link, best_ver


def find_download_page_link(detail_soup):
    btn = detail_soup.find("a", class_=re.compile(r"downloadButton|accent_bg"))
    if btn and btn.get("href"):
        return urllib.parse.urljoin("https://www.apkmirror.com", btn["href"])
    for a in detail_soup.find_all("a", href=True):
        if "download.php" in a["href"]:
            return urllib.parse.urljoin("https://www.apkmirror.com", a["href"])
    return None


def find_final_link(dl_soup, dl_html):
    for a in dl_soup.find_all("a", href=True):
        href = a["href"]
        if "key=" in href or "/wp-content/themes/APKMirror/" in href or "download.php" in href:
            return urllib.parse.urljoin("https://www.apkmirror.com", href)
    m = re.search(r'href="(/apk/[^"]+key=[^"]+)"', dl_html)
    if m:
        return urllib.parse.urljoin("https://www.apkmirror.com", m.group(1))
    return None


def stream_to_file(resp, output_path):
    with open(output_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def cffi_get(session_or_none, url, headers, stream=False, timeout=30):
    if session_or_none is not None:
        resp = session_or_none.get(url, headers=headers, timeout=timeout, stream=stream)
    else:
        resp = cffi_requests.get(url, headers=headers, impersonate=IMPERSONATE,
                                 timeout=timeout, stream=stream)
    resp.raise_for_status()
    return resp


def get_apkmirror_apk(variant_url, output_path, slug_filter, version_slug,
                      exact_version=None, check_version_only=False):
    print(f"[direct] Fetching: {variant_url}")
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl_cffi is required for the fast path. Install: pip install curl_cffi")
    session = cffi_requests.Session(impersonate=IMPERSONATE)

    resp = session.get(variant_url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP Error {resp.status_code}: {resp.reason}")
    soup = BeautifulSoup(resp.text, "html.parser")
    detail_link, version_str = find_detail_link(soup, slug_filter, version_slug, exact_version)
    if check_version_only:
        if not version_str:
            raise RuntimeError("Could not determine the version from the APKMirror variant page.")
        print(f"LATEST_VERSION={version_str}")
        return version_str
    if not detail_link:
        want = f" for version {exact_version}" if exact_version else ""
        raise RuntimeError(f"No download link found on the variant page{want}.")

    print(f"[direct] Detail page: {detail_link}")
    resp2 = session.get(detail_link, headers=HEADERS, timeout=30)
    if resp2.status_code != 200:
        raise RuntimeError(f"HTTP Error {resp2.status_code}: {resp2.reason}")
    dl_page = find_download_page_link(BeautifulSoup(resp2.text, "html.parser"))
    if not dl_page:
        raise RuntimeError("APK download button page not found.")

    print(f"[direct] Download page: {dl_page}")
    resp3 = session.get(dl_page, headers=HEADERS, timeout=30)
    if resp3.status_code != 200:
        raise RuntimeError(f"HTTP Error {resp3.status_code}: {resp3.reason}")
    final_link = find_final_link(BeautifulSoup(resp3.text, "html.parser"), resp3.text)
    if not final_link:
        raise RuntimeError("Could not extract the final download URL.")

    print(f"[direct] Downloading APK: {final_link}")
    dl_resp = session.get(final_link, headers={**HEADERS, "Referer": dl_page},
                          timeout=120, stream=True)
    if dl_resp.status_code != 200:
        raise RuntimeError(f"Download HTTP Error {dl_resp.status_code}")
    stream_to_file(dl_resp, output_path)
    return version_str


def pw_wait_for_cf(page, timeout=30000):
    try:
        page.wait_for_function(
            "() => !document.title.includes('Just a moment') && document.readyState === 'complete'",
            timeout=timeout,
        )
    except Exception:
        pass
    page.wait_for_timeout(2000)


def get_apkmirror_apk_playwright(variant_url, output_path, slug_filter, version_slug,
                                 exact_version=None, check_version_only=False):
    from playwright.sync_api import sync_playwright

    print("[playwright] Launching headless Chromium to bypass Cloudflare...")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox",
                  "--disable-dev-shm-usage",
                  "--disable-blink-features=AutomationControlled"],
        )
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            accept_downloads=True,
        )
        page = ctx.new_page()
        page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")

        print(f"[playwright] -> {variant_url}")
        page.goto(variant_url, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_cf(page)
        detail_link, version_str = find_detail_link(
            BeautifulSoup(page.content(), "html.parser"),
            slug_filter, version_slug, exact_version)

        if check_version_only:
            browser.close()
            if not version_str:
                raise RuntimeError("[playwright] Could not determine the version.")
            print(f"LATEST_VERSION={version_str}")
            return version_str
        if not detail_link:
            browser.close()
            raise RuntimeError("[playwright] No download link found on the variant page.")

        print(f"[playwright] -> {detail_link}")
        page.goto(detail_link, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_cf(page)
        dl_page = find_download_page_link(BeautifulSoup(page.content(), "html.parser"))
        if not dl_page:
            browser.close()
            raise RuntimeError("[playwright] Download button page not found.")

        print(f"[playwright] -> {dl_page}")
        page.goto(dl_page, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_cf(page)
        dl_html = page.content()
        final_link = find_final_link(BeautifulSoup(dl_html, "html.parser"), dl_html)
        if not final_link:
            browser.close()
            raise RuntimeError("[playwright] Could not extract the final download URL.")

        print(f"[playwright] Downloading via the browser session: {final_link}")
        with page.expect_download(timeout=300_000) as dl_info:
            a_tag = page.query_selector("a[href*='download.php']")
            if a_tag:
                a_tag.click()
            else:
                page.evaluate(f"window.location.href = {json.dumps(final_link)}")
        download = dl_info.value
        print(f"[playwright] Saving ({download.suggested_filename}) to: {output_path}")
        download.save_as(output_path)
        browser.close()
    return version_str


def main():
    parser = argparse.ArgumentParser(description="Download an APK from APKMirror (generic per-app)")
    parser.add_argument("--config", help="Path to apps/<id>.json")
    parser.add_argument("--variant-url", help="Variant URL override")
    parser.add_argument("--slug-filter", help="e.g. /apk/google-inc/photos/")
    parser.add_argument("--version-slug", help="e.g. google-photos-")
    parser.add_argument("--exact-version", help="Exact version from the resolver, e.g. 7.92.0.977185651")
    parser.add_argument("--arch", help="Arch entry name from source.archs, e.g. arm64")
    parser.add_argument("--output", default="base.apk")
    parser.add_argument("--check-version", action="store_true")
    parser.add_argument("--direct-url", help="Skip scraping, download this URL directly")
    args = parser.parse_args()

    variant_url = args.variant_url
    slug_filter = args.slug_filter
    version_slug = args.version_slug
    if args.config:
        with open(args.config) as fh:
            cfg = json.load(fh)
        src = cfg["source"]
        if src.get("type") != "apkmirror":
            parser.error(f"config {args.config} is not an apkmirror source")
        try:
            entry = resolve_arch_entry(src, args.arch)
        except RuntimeError as e:
            parser.error(str(e))
        variant_url = variant_url or entry.get("variant_url")
        slug_filter = slug_filter or entry.get("slug_filter")
        version_slug = version_slug or entry.get("version_slug")
    if not variant_url or not slug_filter or not version_slug:
        if not args.direct_url:
            parser.error("--config or (--variant-url + --slug-filter + --version-slug) is required")

    version_str = "unknown"
    try:
        if args.direct_url:
            print(f"Downloading direct URL: {args.direct_url}")
            if HAS_CURL_CFFI:
                stream_to_file(cffi_get(None, args.direct_url, HEADERS, stream=True), args.output)
            else:
                raise RuntimeError("curl_cffi is not installed")
        elif args.check_version:
            try:
                get_apkmirror_apk(variant_url, None, slug_filter, version_slug,
                                  args.exact_version, check_version_only=True)
                return
            except Exception as e:
                print(f"Direct version check failed ({e}); trying Playwright...")
                get_apkmirror_apk_playwright(variant_url, None, slug_filter, version_slug,
                                             args.exact_version, check_version_only=True)
                return
        else:
            try:
                version_str = get_apkmirror_apk(variant_url, args.output, slug_filter,
                                                version_slug, args.exact_version)
            except Exception as e:
                print(f"Direct scrape failed ({e}); retrying with Playwright...")
                version_str = get_apkmirror_apk_playwright(
                    variant_url, args.output, slug_filter, version_slug, args.exact_version)
    except Exception as e:
        print(f"Failed: {e}")
        print("Use --direct-url as a manual fallback.")
        sys.exit(1)

    if not args.check_version:
        if os.path.exists(args.output) and os.path.getsize(args.output) > 1_000_000:
            print(f"OK {args.output} ({os.path.getsize(args.output):,} bytes)")
            if "GITHUB_OUTPUT" in os.environ and version_str:
                with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                    fh.write(f"apk_version={version_str}\n")
        else:
            print("Result file is missing or too small!")
            sys.exit(1)


if __name__ == "__main__":
    main()
