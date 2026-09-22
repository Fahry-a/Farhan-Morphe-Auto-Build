#!/usr/bin/env python3
"""Download an exact APK version from multiple public mirrors."""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    from common import validate_package
except ModuleNotFoundError:
    from tools.common import validate_package


def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
    })
    return urllib.request.urlopen(req, timeout=timeout)


def http_read(url, headers=None, timeout=60):
    """Read a page with browser impersonation when curl_cffi is available."""
    try:
        from curl_cffi import requests
        r = requests.get(
            url, impersonate="chrome131", timeout=timeout,
            allow_redirects=True, headers=headers or {"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        return r.content
    except ImportError:
        with http_get(url, headers=headers, timeout=timeout) as r:
            return r.read()


def download_url(url, output):
    try:
        from curl_cffi import requests
        r = requests.get(url, impersonate="chrome131", timeout=300,
                         stream=True, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        with open(output, "wb") as fh:
            for chunk in r.iter_content(65536):
                if chunk:
                    fh.write(chunk)
    except ImportError:
        with http_get(url, timeout=300) as r, open(output, "wb") as fh:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                fh.write(chunk)


def apkpure_link(package, name, version):
    from bs4 import BeautifulSoup
    url = f"https://apkpure.net/{name}/{package}/download/{version}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Referer": "https://apkpure.net/",
    }
    soup = BeautifulSoup(http_read(url, headers), "html.parser")
    node = soup.find("a", id="download_link")
    if not node or not node.get("href"):
        raise RuntimeError("APKPure download link not found")
    return node["href"]


def _uptodown_download_url(page_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "html.parser")
    button = soup.find("button", id="detail-download-button")
    if not button or not button.get("data-url"):
        raise RuntimeError("Uptodown download token not found")
    data_url = button["data-url"]
    return data_url if data_url.startswith(("http://", "https://")) else         f"https://dw.uptodown.com/dwn/{data_url}"


def _uptodown_page_version(page_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "html.parser")
    version = soup.select_one("div.version")
    if version:
        return version.get_text(strip=True).lstrip("v")
    return None


def uptodown_link(package, name, version):
    from bs4 import BeautifulSoup
    base = f"https://{name}.en.uptodown.com/android"

    # Current release page. AudioRelay 0.26.1 is currently exposed here.
    page_html = http_read(f"{base}/download")
    if _uptodown_page_version(page_html) == version:
        return _uptodown_download_url(page_html)

    # Historical versions are linked from the HTML archive; the old JSON
    # endpoint used here previously has been retired.
    versions_html = http_read(f"{base}/versions")
    soup = BeautifulSoup(versions_html, "html.parser")
    for text_node in soup.find_all(string=lambda s: s and s.strip().lstrip("v") == version):
        link = text_node.find_parent("a", href=True)
        if not link:
            continue
        href = link["href"]
        if href.startswith("/"):
            href = f"https://{name}.en.uptodown.com{href}"
        version_html = http_read(href)
        if _uptodown_page_version(version_html) == version:
            return _uptodown_download_url(version_html)

    raise RuntimeError(f"Uptodown version {version} not found")


def aptoide_link(package, version, arch):
    import base64
    base = "https://ws75.aptoide.com/api/7/"
    q = ""
    if arch != "universal":
        cpu = {"arm64": "arm64-v8a,armeabi-v7a,armeabi",
               "armeabi-v7a": "armeabi-v7a,armeabi"}.get(arch, "")
        if cpu:
            encoded = base64.b64encode(f"myCPU={cpu}&leanback=0".encode()).decode()
            q = f"&q={encoded}"

    with http_get(f"{base}listAppVersions?package_name={package}&limit=50{q}") as r:
        data = json.loads(r.read())

    entries = data.get("datalist", {}).get("list")
    if entries is None:
        entries = data.get("list", [])
    vercode = next(
        (x["file"]["vercode"] for x in entries
         if x.get("file", {}).get("vername") == version), None)
    if not vercode:
        raise RuntimeError(f"Aptoide version {version} not found")

    with http_get(f"{base}getAppMeta?package_name={package}&vercode={vercode}{q}") as r:
        data = json.loads(r.read())
    try:
        return data["data"]["file"]["path"]
    except KeyError as exc:
        raise RuntimeError("Aptoide metadata response has no download path") from exc


def download_from_mirror(kind, cfg, arch, version, output):
    package = cfg["package"]
    mirror = next((m for m in cfg["source"].get("mirrors", []) if m["type"] == kind), {})
    name = mirror.get("name") or cfg.get("display_name", cfg["id"]).lower().replace(" ", "-")
    if kind == "apkpure":
        download_url(apkpure_link(package, name, version), output)
    elif kind == "uptodown":
        download_url(uptodown_link(package, name, version), output)
    elif kind == "aptoide":
        download_url(aptoide_link(package, version, arch), output)
    else:
        raise RuntimeError(f"Unsupported mirror: {kind}")
    return version


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", required=True)
    p.add_argument("--arch", required=True)
    p.add_argument("--exact-version", required=True)
    p.add_argument("--output", default="base.apk")
    args = p.parse_args()

    with open(args.config) as fh:
        cfg = json.load(fh)
    if cfg["source"].get("type") != "mirrors":
        p.error("config source.type must be mirrors")

    file_type = cfg["source"].get("file_type", "apk")
    errors = []
    for mirror in cfg["source"].get("mirrors", []):
        kind = mirror["type"]
        tmp = f"{args.output}.partial"
        try:
            print(f"== Trying {kind} for {cfg['package']} {args.exact_version} ==")
            version = download_from_mirror(kind, cfg, args.arch,
                                           args.exact_version, tmp)
            validate_package(tmp, file_type, expected_version=args.exact_version)
            os.replace(tmp, args.output)
            print(f"OK {args.output}: source={kind}, version={version}")
            if "GITHUB_OUTPUT" in os.environ:
                with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                    fh.write(f"apk_version={args.exact_version}\n")
                    fh.write(f"base_file={args.output}\n")
                    fh.write(f"download_source={kind}\n")
            return
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
            print(f"FAILED {kind}: {exc}", file=sys.stderr)
            try:
                Path(tmp).unlink()
            except FileNotFoundError:
                pass

    print("All configured APK mirrors failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
