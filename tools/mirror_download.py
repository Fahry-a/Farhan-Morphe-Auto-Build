#!/usr/bin/env python3
"""Download an exact APK version from multiple public mirrors.

The downloader is deliberately a thin fallback layer: each mirror must return
the requested version, and every downloaded file is validated before it is
accepted. Existing APKMirror handling is reused so its Playwright fallback
continues to work.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

try:\n    from common import resolve_arch_entry, validate_package\nexcept ModuleNotFoundError:\n    from tools.common import resolve_arch_entry, validate_package


def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
    })
    return urllib.request.urlopen(req, timeout=timeout)


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
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
               "Referer": "https://apkpure.net/"}
    with http_get(url, headers) as r:
        soup = BeautifulSoup(r.read(), "html.parser")
    node = soup.find("a", id="download_link")
    if not node or not node.get("href"):
        raise RuntimeError("APKPure download link not found")
    return node["href"]


def uptodown_link(package, name, version):
    from bs4 import BeautifulSoup
    slug_candidates = [name, package.replace(".", "-")]
    if package.startswith("com."):
        parts = package.split(".")
        slug_candidates += [parts[1], f"com-{parts[1]}", parts[-1]]
    seen = set()
    for slug in slug_candidates:
        if not slug or slug in seen:
            continue
        seen.add(slug)
        base = f"https://{slug}.en.uptodown.com/android"
        try:
            with http_get(f"{base}/versions") as r:
                soup = BeautifulSoup(r.read(), "html.parser")
            title = soup.find("h1", id="detail-app-name")
            if not title or not title.get("data-code"):
                continue
            code = title["data-code"]
            page = 1
            while page <= 50:
                with http_get(f"{base}/apps/{code}/versions/{page}") as r:
                    data = json.loads(r.read())
                entries = data.get("data", [])
                if not entries:
                    break
                for entry in entries:
                    if entry.get("version") != version:
                        continue
                    v = entry["versionURL"]
                    version_url = f"{v['url']}/{v['extraURL']}/{v['versionID']}"
                    with http_get(version_url) as r:
                        page_html = r.read()
                    soup = BeautifulSoup(page_html, "html.parser")
                    button = soup.find("button", id="detail-download-button")
                    if not button:
                        continue
                    data_url = button.get("data-url")
                    if data_url:
                        return f"https://dw.uptodown.com/dwn/{data_url}"
                page += 1
        except Exception as exc:
            print(f"[Uptodown] {slug}: {exc}", file=sys.stderr)
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
    vercode = next((x["file"]["vercode"] for x in data["datalist"]["list"]
                     if x["file"]["vername"] == version), None)
    if not vercode:
        raise RuntimeError(f"Aptoide version {version} not found")
    with http_get(f"{base}getAppMeta?package_name={package}&vercode={vercode}{q}") as r:
        data = json.loads(r.read())
    return data["data"]["file"]["path"]


def download_apkmirror(config, arch, version, output):
    from apkmirror import get_apkmirror_apk, resolve_arch_entry
    entry = resolve_arch_entry(config["source"], arch)
    return get_apkmirror_apk(entry["variant_url"], output, entry["slug_filter"],
                             entry["version_slug"], exact_version=version)


def download_from_mirror(kind, cfg, arch, version, output):
    package = cfg["package"]
    mirror = next((m for m in cfg["source"].get("mirrors", []) if m["type"] == kind), {})
    name = mirror.get("name") or cfg.get("display_name", cfg["id"]).lower().replace(" ", "-")
    if kind == "apkmirror":
        return download_apkmirror(cfg, arch, version, output)
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
            validate_package(tmp, file_type)
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
