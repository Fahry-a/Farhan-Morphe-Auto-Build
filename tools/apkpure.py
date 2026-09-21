#!/usr/bin/env python3
"""APKPure downloader (generic per-app).

Flow (verified against apkpure.com):
  versions page  ->  version download page  ->  a#download_link  ->  file
  https://apkpure.com/<slug>/<package>/versions
  https://apkpure.com/<slug>/<package>/download/<version>
  https://d.apkpure.com/b/XAPK/<package>?versionCode=<code>  (or /b/APK/ for plain APKs)

Strategy matches the other downloaders:
  1. Fast path: curl_cffi with Chrome TLS impersonation.
  2. Fallback: Playwright headless Chromium.

Use --exact-version (from tools/resolve_version.py) to pin the version the
patch bundle supports instead of grabbing whatever is newest.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

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

from common import resolve_arch_entry, validate_package

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

BASE = "https://apkpure.com"


def parse_version_tuple(ver_str):
    parts = re.findall(r"\d+", ver_str or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def normalize_version(ver):
    return (ver or "").strip()


def find_version_link(soup, package, exact_version=None):
    """Find the .../download/<version> link on a versions page."""
    found = []
    seen = set()
    want = normalize_version(exact_version) if exact_version else None
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(re.escape(f"/{package}/download/") + r"([^/?#]+)", href)
        if not m:
            continue
        ver = normalize_version(urllib.parse.unquote(m.group(1)))
        if want and ver != want:
            continue
        link = urllib.parse.urljoin(BASE, href)
        if link in seen:
            continue
        seen.add(link)
        found.append((link, ver, parse_version_tuple(ver)))
    if not found:
        return None, None
    found.sort(key=lambda x: x[2], reverse=True)
    best_link, best_ver, _ = found[0]
    print(f"[APKPure] Found {len(found)} version(s). Picked: {best_ver}")
    return best_link, best_ver


def find_file_link(soup):
    """Extract the direct file URL from a version download page."""
    a_tag = soup.find("a", id="download_link")
    if a_tag and a_tag.get("href"):
        return urllib.parse.urljoin(BASE, a_tag["href"])
    for sel in ("a.download-start-btn", "a.download-btn"):
        a_tag = soup.select_one(sel)
        if a_tag and a_tag.get("href"):
            return urllib.parse.urljoin(BASE, a_tag["href"])
    return None


def detect_type(file_url, override=None):
    if override:
        return override
    if "/b/APK/" in file_url:
        return "apk"
    return "xapk"


def stream_to_file(resp, output_path):
    with open(output_path, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=8192):
            if chunk:
                fh.write(chunk)


def download_file_url(session, file_url, referer, output_path):
    headers = {**HEADERS, "Referer": referer}
    resp = session.get(file_url, headers=headers, timeout=120, stream=True,
                       allow_redirects=True)
    resp.raise_for_status()
    stream_to_file(resp, output_path)


def get_apkpure_package(versions_url, output_path, exact_version=None,
                        file_type=None, check_version_only=False):
    print(f"[direct] Fetching: {versions_url}")
    if not HAS_CURL_CFFI:
        raise RuntimeError("curl_cffi is required for the fast path. Install: pip install curl_cffi")
    m = re.search(r"apkpure\.com/([^/]+)/([^/]+)/versions", versions_url)
    package = m.group(2) if m else ""
    session = cffi_requests.Session(impersonate=IMPERSONATE)

    resp = session.get(versions_url, headers=HEADERS, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP Error {resp.status_code}: {resp.reason}")
    version_link, version_str = find_version_link(
        BeautifulSoup(resp.text, "html.parser"), package, exact_version)
    if check_version_only:
        if not version_str:
            raise RuntimeError("Could not determine the version from the APKPure versions page.")
        print(f"LATEST_VERSION={version_str}")
        return version_str, None
    if not version_link:
        want = f" version {exact_version}" if exact_version else ""
        raise RuntimeError(f"No download page found on the versions page{want}.")

    print(f"[direct] Version page: {version_link}")
    resp2 = session.get(version_link, headers=HEADERS, timeout=30)
    if resp2.status_code != 200:
        raise RuntimeError(f"HTTP Error {resp2.status_code}: {resp2.reason}")
    file_url = find_file_link(BeautifulSoup(resp2.text, "html.parser"))
    if not file_url:
        raise RuntimeError("Could not extract the file URL from the version page.")

    resolved_type = detect_type(file_url, file_type)
    if output_path.endswith((".apk", ".xapk", ".apkm")):
        output_path = re.sub(r"\.(apk|xapk|apkm)$",
                             ".xapk" if resolved_type == "xapk" else ".apk",
                             output_path)
    print(f"[direct] Downloading file ({resolved_type}): {file_url}")
    download_file_url(session, file_url, version_link, output_path)
    return version_str, output_path


def pw_wait_for_page(page, timeout=30000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(2000)


def get_apkpure_package_playwright(versions_url, output_path, exact_version=None,
                                   file_type=None, check_version_only=False):
    from playwright.sync_api import sync_playwright

    print("[playwright] Launching headless Chromium...")
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

        m = re.search(r"apkpure\.com/([^/]+)/([^/]+)/versions", versions_url)
        package = m.group(2) if m else ""

        print(f"[playwright] -> {versions_url}")
        page.goto(versions_url, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_page(page)
        version_link, version_str = find_version_link(
            BeautifulSoup(page.content(), "html.parser"), package, exact_version)
        if check_version_only:
            browser.close()
            if not version_str:
                raise RuntimeError("[playwright] Could not determine the version.")
            print(f"LATEST_VERSION={version_str}")
            return version_str, None
        if not version_link:
            browser.close()
            raise RuntimeError("[playwright] No download page found on the versions page.")

        print(f"[playwright] -> {version_link}")
        page.goto(version_link, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_page(page)
        file_url = find_file_link(BeautifulSoup(page.content(), "html.parser"))
        if not file_url:
            browser.close()
            raise RuntimeError("[playwright] Could not extract the file URL.")

        resolved_type = detect_type(file_url, file_type)
        if output_path.endswith((".apk", ".xapk", ".apkm")):
            output_path = re.sub(r"\.(apk|xapk|apkm)$",
                                 ".xapk" if resolved_type == "xapk" else ".apk",
                                 output_path)
        print(f"[playwright] Downloading via the browser session: {file_url}")
        with page.expect_download(timeout=300_000) as dl_info:
            btn = page.query_selector("#download_link, a.download-start-btn, a.download-btn")
            if btn:
                btn.click()
            else:
                page.goto(file_url, timeout=60000)
        download = dl_info.value
        print(f"[playwright] Saving ({download.suggested_filename}) to: {output_path}")
        download.save_as(output_path)
        browser.close()
    return version_str, output_path


def main():
    parser = argparse.ArgumentParser(description="Download a package from APKPure (generic per-app)")
    parser.add_argument("--config", help="Path to apps/<id>.json (apkpure type)")
    parser.add_argument("--versions-url", help="Versions page override")
    parser.add_argument("--page-slug", help="e.g. block-blast")
    parser.add_argument("--package", help="e.g. com.block.juggle")
    parser.add_argument("--exact-version", help="Exact version from the resolver, e.g. 10.4.5")
    parser.add_argument("--arch", help="Arch entry name from source.archs")
    parser.add_argument("--file-type", choices=["apk", "xapk"],
                        help="Package type (default detected from the file URL)")
    parser.add_argument("--output", default=None,
                        help="Output file (default base.apk / base.xapk by detected type)")
    parser.add_argument("--check-version", action="store_true")
    args = parser.parse_args()

    versions_url = args.versions_url
    if args.config:
        with open(args.config) as fh:
            cfg = json.load(fh)
        src = cfg["source"]
        if src.get("type") != "apkpure":
            parser.error(f"config {args.config} is not an apkpure source")
        try:
            entry = resolve_arch_entry(src, args.arch)
        except RuntimeError as e:
            parser.error(str(e))
        versions_url = versions_url or entry.get("versions_url")
        package = args.package or cfg.get("package", "")
        slug = args.page_slug or entry.get("page_slug", "")
        if not versions_url:
            if not slug or not package:
                parser.error("apkpure arch entry needs versions_url or (page_slug + package)")
            versions_url = f"{BASE}/{slug}/{package}/versions"
    else:
        package = args.package or ""
        if not versions_url:
            parser.error("--config or --versions-url is required")

    output_path = args.output or "base.apk"
    version_str, actual_path = "unknown", output_path
    try:
        try:
            version_str, actual_path = get_apkpure_package(
                versions_url, output_path, args.exact_version,
                args.file_type, args.check_version)
            if args.check_version:
                return
        except Exception as e:
            print(f"Direct scrape failed ({e}); retrying with Playwright...")
            version_str, actual_path = get_apkpure_package_playwright(
                versions_url, output_path, args.exact_version,
                args.file_type, args.check_version)
            if args.check_version:
                return
    except Exception as e:
        print(f"Failed: {e}")
        sys.exit(1)

    try:
        file_type = "xapk" if actual_path.endswith(".xapk") else "apk"
        entries = validate_package(actual_path, file_type)
    except RuntimeError as e:
        print(f"Validation failed: {e}")
        sys.exit(1)
    print(f"OK {actual_path} ({os.path.getsize(actual_path):,} bytes, "
          f"{entries} zip entries, type={file_type})")
    if "GITHUB_OUTPUT" in os.environ and version_str:
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"apk_version={version_str}\n")
            fh.write(f"base_file={actual_path}\n")


if __name__ == "__main__":
    main()
