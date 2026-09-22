#!/usr/bin/env python3
"""Direct-URL mirror source (no scraping at all).

For apps whose stores prune old versions,
host the exact base file somewhere reachable (e.g. a GitHub release asset)
and point the config at it. Supports {version} and {package} substitution:

  {"name": "universal",
   "url": "https://github.com/OWNER/REPO/releases/download/mirror-app-1.0/app-1.0.xapk"}

The file extension decides validation: .apk needs AndroidManifest.xml,
.apkm/.xapk need APK entries inside.
"""
import argparse
import json
import os
import sys
import urllib.request

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    HAS_CURL_CFFI = False

from common import resolve_arch_entry, validate_package

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}


def main():
    parser = argparse.ArgumentParser(description="Download a base file from a mirror URL")
    parser.add_argument("--config", help="Path to apps/<id>.json (direct type)")
    parser.add_argument("--url", help="Mirror URL override")
    parser.add_argument("--exact-version", help="Fills the {version} placeholder")
    parser.add_argument("--arch", help="Arch entry name from source.archs")
    parser.add_argument("--file-type", choices=["apk", "apkm", "xapk"])
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    url = args.url
    file_type = args.file_type
    package = ""
    if args.config:
        with open(args.config) as fh:
            cfg = json.load(fh)
        src = cfg["source"]
        if src.get("type") != "direct":
            parser.error(f"config {args.config} is not a direct source")
        try:
            entry = resolve_arch_entry(src, args.arch)
        except RuntimeError as e:
            parser.error(str(e))
        package = cfg.get("package", "")
        url = url or entry.get("url", "")
        file_type = file_type or src.get("file_type")
    if not url:
        parser.error("--config or --url is required")
    if args.exact_version:
        url = url.replace("{version}", args.exact_version)
    url = url.replace("{package}", package).replace("{arch}", args.arch or "")
    if "{version}" in url or "{package}" in url or "{arch}" in url:
        parser.error("URL still has unfilled placeholders.")

    if file_type == "apkm":
        default_out, want_ext = "base.apkm", ".apkm"
    elif file_type == "xapk":
        default_out, want_ext = "base.xapk", ".xapk"
    else:
        default_out, want_ext = "base.apk", ".apk"
        if url.lower().endswith((".xapk", ".apkm")):
            want_ext = ".xapk" if url.lower().endswith(".xapk") else ".apkm"
            default_out = "base" + want_ext
    output_path = args.output or default_out
    if not output_path.endswith((".apk", ".xapk", ".apkm")):
        output_path += want_ext

    partial_path = output_path + ".partial"
    print(f"[direct] Downloading: {url}")
    try:
        if HAS_CURL_CFFI:
            from curl_cffi import requests as r
            resp = r.get(url, headers=HEADERS, impersonate="chrome131",
                         timeout=300, stream=True, allow_redirects=True)
            resp.raise_for_status()
            total = 0
            with open(partial_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        fh.write(chunk)
                        total += len(chunk)
        else:
            req = urllib.request.Request(url, headers=HEADERS)
            total = 0
            with urllib.request.urlopen(req, timeout=300) as resp, \
                    open(partial_path, "wb") as fh:
                while True:
                    buf = resp.read(65536)
                    if not buf:
                        break
                    fh.write(buf)
                    total += len(buf)
    except Exception as e:
        try:
            os.unlink(partial_path)
        except FileNotFoundError:
            pass
        print(f"Failed: {e}")
        sys.exit(1)

    check_type = ("xapk" if output_path.endswith(".xapk")
                  else "apkm" if output_path.endswith(".apkm") else "apk")
    try:
        entries = validate_package(partial_path, check_type, expected_version=args.exact_version)
    except RuntimeError as e:
        try:
            os.unlink(partial_path)
        except FileNotFoundError:
            pass
        print(f"Validation failed: {e}")
        sys.exit(1)
    os.replace(partial_path, output_path)
    print(f"OK {output_path} ({total:,} bytes, {entries} zip entries, type={check_type})")
    if "GITHUB_OUTPUT" in os.environ and args.exact_version:
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"apk_version={args.exact_version}\n")
            fh.write(f"base_file={output_path}\n")


if __name__ == "__main__":
    main()
