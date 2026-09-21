#!/usr/bin/env python3
"""Uptodown downloader (generic per-app).

Flow:
  app page -> versions JSON -> version page -> variants catalog -> -x page
  -> dw.uptodown.com direct file link -> download.

  https://<slug>.en.uptodown.com/android
  https://<slug>.en.uptodown.com/android/apps/<code>/versions/<page>  (JSON)
  https://<slug>.en.uptodown.com/android/download/<fileID>
  https://<slug>.en.uptodown.com/android/app/<code>/version/<v>/files (JSON+HTML)
  https://<slug>.en.uptodown.com/android/download/<fileID>-x
  https://dw.uptodown.com/dwn/<token>

Strategy:
  1. Fast path: curl_cffi with Chrome TLS impersonation.
  2. Fallback: plain urllib (different TLS fingerprint; Uptodown's edge
     sometimes resets one but accepts the other).
  3. Last resort: Playwright headless Chromium.
Locale hosts (.en, .de, .fr, ...) are tried in turn because Uptodown's
English edge occasionally rejects datacenter IP ranges.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

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
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

LOCALES = ("en", "de", "fr", "in", "it", "ru", "jp", "kr")


def normalize_version(ver):
    parts = re.findall(r"\d+", ver or "")
    return tuple(int(p) for p in parts) if parts else (0,)


def same_version(left, right):
    clean = lambda v: re.sub(r"[\(\[].*?[\)\]]", "", v or "").strip()
    return (left == right or clean(left) == clean(right)
            or normalize_version(left) == normalize_version(right))


def assemble_version_url(parts):
    return "/".join(str(parts.get(k, "")).strip("/")
                     for k in ("url", "extraURL", "versionID"))


def pick_variant_id(catalog_html, arch):
    """Pick a file id from the variants catalog fragment.

    Prefers a combined arm64+armeabi build for universal, else the requested
    ABI, else the first entry.
    """
    soup = BeautifulSoup(catalog_html, "html.parser")
    requested = "armeabi-v7a" if arch == "arm-v7a" else (arch or "")
    current_arch = ""
    fallback_id = None
    for node in soup.select("section.variants > .content > *"):
        classes = node.get("class", [])
        if node.name == "p":
            current_arch = node.get_text(" ", strip=True).lower()
            continue
        if "variant" not in classes:
            continue
        report = node.select_one(".v-report[data-file-id]")
        if not report:
            continue
        file_id = report.get("data-file-id")
        if not fallback_id:
            fallback_id = file_id
        if arch == "universal" and "arm64-v8a" in current_arch and "armeabi-v7a" in current_arch:
            return file_id
        if requested and requested != "universal" and requested in current_arch:
            return file_id
    return fallback_id


class Fetcher:
    """curl_cffi first, plain urllib second."""

    def __init__(self):
        self.session = (cffi_requests.Session(impersonate=IMPERSONATE)
                        if HAS_CURL_CFFI else None)

    def get(self, url, headers=None, timeout=25):
        headers = {**(headers or HEADERS)}
        last_err = None
        if self.session is not None:
            try:
                resp = self.session.get(url, headers=headers, timeout=timeout)
                if resp.status_code != 200:
                    raise RuntimeError(f"HTTP Error {resp.status_code}")
                return resp.url, resp.content
            except Exception as e:
                last_err = e
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.url, resp.read()
        except Exception as e:
            raise RuntimeError(f"All fetch methods failed for {url} "
                               f"(curl_cffi: {last_err}; urllib: {e})")


def store_bases(slug):
    return [f"https://{slug}.{loc}.uptodown.com/android" for loc in LOCALES]


def is_app_page(content):
    return (BeautifulSoup(content, "html.parser")
            .find("h1", id="detail-app-name") is not None)


def find_app_page(fetcher, slug):
    for base in store_bases(slug):
        try:
            final_url, content = fetcher.get(base)
        except Exception:
            continue
        if is_app_page(content):
            # Follow Uptodown's canonical slug (aliases redirect).
            m = re.search(r"https://([^.]+)\.([a-z]+)\.uptodown\.com(/android)?",
                          final_url)
            if m:
                return f"https://{m.group(1)}.{m.group(2)}.uptodown.com/android", content
            return base, content
    return None, None


def find_version_entry(fetcher, base, code, exact_version):
    for page in range(1, 11):
        try:
            _, raw = fetcher.get(f"{base}/apps/{code}/versions/{page}")
            entries = (json.loads(raw) or {}).get("data") or []
        except Exception:
            break
        if not entries:
            break
        for entry in entries:
            if same_version(entry.get("version", ""), exact_version):
                return entry
        target = normalize_version(exact_version)
        if target and all(normalize_version(e.get("version", "")) < target
                          for e in entries):
            break
    return None


def direct_url_from_page(soup, page_url):
    button = soup.find(id="detail-download-button")
    if button:
        data_url = button.get("data-url")
        if data_url and data_url != "apps":
            return urllib.parse.urljoin("https://dw.uptodown.com/dwn/", data_url)
    for selector in ("a#detail-download-button[href]", "a.download[href]"):
        link = soup.select_one(selector)
        if link and link.get("href"):
            href = link["href"]
            if "dw.uptodown.com" in href or href.endswith((".apk", ".xapk")):
                return urllib.parse.urljoin(page_url, href)
    return None


def resolve_pages(fetcher, slug, exact_version, arch):
    """Resolve version/x pages and any static file link using plain HTTP.

    Returns (version_str, version_page_url, x_page_url_or_None,
             static_file_link_or_None).
    """
    base, content = find_app_page(fetcher, slug)
    if not base:
        raise RuntimeError("No usable Uptodown locale host (tried %d)." % len(LOCALES))
    code = app_code(content)
    return resolve_pages_for_base(fetcher, base, code, exact_version, arch)


def resolve_pages_for_base(fetcher, base, code, exact_version, arch):
    entry = find_version_entry(fetcher, base, code, exact_version)
    if not entry:
        raise RuntimeError(f"Version {exact_version} not found (checked 10 pages).")
    version_url = assemble_version_url(entry.get("versionURL") or {})
    if not version_url.startswith("http"):
        raise RuntimeError("Version entry has no usable URL.")
    _, content = fetcher.get(version_url)
    soup = BeautifulSoup(content, "html.parser")
    print(f"[uptodown] Version page: has-download-btn="
          f"{soup.find(id='detail-download-button') is not None} "
          f"has-variants-btn={soup.select_one('.button.variants[data-version]') is not None}")

    x_page = None
    variants_btn = soup.select_one(".button.variants[data-version]")
    if variants_btn and variants_btn.get("data-version"):
        data_version = variants_btn["data-version"]
        catalog_url = (f"{base.rsplit('/android', 1)[0]}/app/{code}"
                       f"/version/{data_version}/files")
        try:
            _, raw = fetcher.get(catalog_url)
            fragment = (json.loads(raw) or {}).get("content") or ""
            file_id = pick_variant_id(fragment, arch)
            if file_id:
                x_page = f"{base}/download/{file_id}-x"
        except Exception as e:
            print(f"[uptodown] Variant catalog failed ({e}); using version page link.")

    static_link = None
    if x_page:
        try:
            _, x_content = fetcher.get(x_page)
            static_link = direct_url_from_page(
                BeautifulSoup(x_content, "html.parser"), x_page)
        except Exception:
            static_link = None
    if not static_link:
        static_link = direct_url_from_page(soup, version_url)
    return entry.get("version", exact_version), version_url, x_page, static_link


def download_file_url(fetcher, file_url, referer, output_path):
    if fetcher.session is not None:
        resp = fetcher.session.get(file_url, headers={**HEADERS, "Referer": referer},
                                   timeout=120, stream=True, allow_redirects=True)
        resp.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
        return
    req = urllib.request.Request(file_url, headers={**HEADERS, "Referer": referer})
    with urllib.request.urlopen(req, timeout=120) as resp, open(output_path, "wb") as fh:
        while True:
            buf = resp.read(8192)
            if not buf:
                break
            fh.write(buf)


def extension_for(file_url, override=None):
    if override:
        return ".xapk" if override == "xapk" else ".apk"
    return ".xapk" if file_url.endswith(".xapk") or "/xapk" in file_url.lower() else ".apk"


def get_uptodown_package(slug, output_path, exact_version, arch="universal",
                         file_type=None, check_version_only=False):
    fetcher = Fetcher()
    version_str, version_url, _, static_link = resolve_pages(
        fetcher, slug, exact_version, arch)
    if check_version_only:
        return version_str, None
    if not static_link:
        raise RuntimeError("Download endpoint needs an interactive token "
                           "(Playwright fallback will click the button).")
    ext = extension_for(static_link, file_type)
    if output_path.endswith((".apk", ".xapk", ".apkm")):
        output_path = re.sub(r"\.(apk|xapk|apkm)$", ext, output_path)
    print(f"[uptodown] Downloading file: {static_link}")
    download_file_url(fetcher, static_link, version_url, output_path)
    return version_str, output_path


def app_code(app_page_content):
    m = re.search(r'data-code="(\d+)"', app_page_content.decode("utf-8", "ignore")
                  if isinstance(app_page_content, bytes) else app_page_content)
    if not m:
        raise RuntimeError("App page has no application code.")
    return m.group(1)


def main():
    parser = argparse.ArgumentParser(description="Download a package from Uptodown (generic per-app)")
    parser.add_argument("--config", help="Path to apps/<id>.json (uptodown type)")
    parser.add_argument("--page-slug", help="e.g. block-blast")
    parser.add_argument("--exact-version", help="Exact version from the resolver, e.g. 10.4.5")
    parser.add_argument("--arch", help="Arch entry name from source.archs")
    parser.add_argument("--file-type", choices=["apk", "xapk"],
                        help="Package type (default detected from the file URL)")
    parser.add_argument("--output", default=None,
                        help="Output file (default base.apk / base.xapk by detected type)")
    parser.add_argument("--check-version", action="store_true")
    args = parser.parse_args()

    slug = args.page_slug
    if args.config:
        with open(args.config) as fh:
            cfg = json.load(fh)
        src = cfg["source"]
        if src.get("type") != "uptodown":
            parser.error(f"config {args.config} is not an uptodown source")
        try:
            entry = resolve_arch_entry(src, args.arch)
        except RuntimeError as e:
            parser.error(str(e))
        slug = slug or entry.get("page_slug", "")
    if not slug:
        parser.error("--config or --page-slug is required")
    if not args.exact_version and not args.check_version:
        parser.error("--exact-version is required")

    output_path = args.output or "base.apk"
    arch = args.arch or "universal"
    try:
        try:
            version_str, actual_path = get_uptodown_package(
                slug, output_path, args.exact_version, arch,
                args.file_type, args.check_version)
            if args.check_version:
                print(f"LATEST_VERSION={version_str}")
                return
        except Exception as e:
            print(f"Direct fetch failed ({e}); retrying with Playwright...")
            version_str, actual_path = get_uptodown_package_playwright(
                slug, output_path, args.exact_version, arch,
                args.file_type, args.check_version)
            if args.check_version:
                print(f"LATEST_VERSION={version_str}")
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


def pw_wait_for_page(page, timeout=30000):
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(2000)


def get_uptodown_package_playwright(slug, output_path, exact_version, arch="universal",
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
        try:
            last_err = None
            for base in store_bases(slug):
                try:
                    return _get_uptodown_package_playwright(
                        page, base, output_path, exact_version, arch,
                        file_type, check_version_only)
                except Exception as e:
                    print(f"[playwright] {base} failed ({e}); trying next locale...")
                    last_err = e
        finally:
            browser.close()
    raise RuntimeError(f"All Uptodown locale hosts failed. Last error: {last_err}")


def _click_download(page, url, output_path, timeout_ms=300_000):
    """Open url in the live browser, click the download button, save the file.

    Prints page diagnostics first so bot-wall responses are visible in logs.
    """
    print(f"[playwright] -> {url}")
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    pw_wait_for_page(page)
    try:
        title = page.title()
    except Exception:
        title = "<unknown>"
    html = page.content()
    print(f"[playwright] title={title!r} len={len(html)} "
          f"has-btn={'detail-download-button' in html} "
          f"has-variants={'button variants' in html}")
    with page.expect_download(timeout=timeout_ms) as dl_info:
        btn = page.query_selector("#detail-download-button")
        if btn is None:
            raise RuntimeError(
                f"Download button not found (title={title!r}, len={len(html)}).")
        btn.click()
    download = dl_info.value
    print(f"[playwright] Saving ({download.suggested_filename}) to: {output_path}")
    download.save_as(output_path)
    return download.suggested_filename


def _get_uptodown_package_playwright(page, base, output_path, exact_version,
                                     arch, file_type, check_version_only):
        print(f"[playwright] -> {base}")
        page.goto(base, wait_until="domcontentloaded", timeout=60000)
        pw_wait_for_page(page)
        soup = BeautifulSoup(page.content(), "html.parser")
        code_tag = soup.find("h1", id="detail-app-name")
        if code_tag is None:
            raise RuntimeError(f"Not an app page (title={page.title()!r}).")
        if check_version_only:
            return exact_version or "unknown", None
        code = code_tag.get("data-code")
        if not code:
            raise RuntimeError("App page has no application code.")
        fetcher = Fetcher()
        version_str, version_url, x_page, static_link = resolve_pages_for_base(
            fetcher, base, code, exact_version, arch)
        if static_link:
            # Static link known: download it inside the live session.
            ext = extension_for(static_link, file_type)
            if output_path.endswith((".apk", ".xapk", ".apkm")):
                output_path = re.sub(r"\.(apk|xapk|apkm)$", ext, output_path)
            print(f"[playwright] Downloading via the browser session: {static_link}")
            with page.expect_download(timeout=300_000) as dl_info:
                page.goto(static_link, timeout=60000)
            download = dl_info.value
            print(f"[playwright] Saving ({download.suggested_filename}) to: {output_path}")
            download.save_as(output_path)
            return version_str, output_path
        # Token-driven button: click whatever download page resolved, falling
        # back to the version page itself (the button renders in a real browser
        # even when static fetches get a degraded page).
        click_page = x_page or version_url
        if output_path.endswith((".apk", ".xapk", ".apkm")):
            output_path = re.sub(r"\.(apk|xapk|apkm)$",
                                 ".xapk" if (file_type or "xapk") == "xapk" else ".apk",
                                 output_path)
        print(f"[playwright] Clicking download on: {click_page}")
        _click_download(page, click_page, output_path)
        return version_str, output_path


if __name__ == "__main__":
    main()
