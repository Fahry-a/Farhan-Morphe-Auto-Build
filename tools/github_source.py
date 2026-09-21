#!/usr/bin/env python3
"""Download an APK from GitHub Releases (used for upstream Brave).

Example: repo brave/brave-browser, tag v1.95.104, asset BraveMonoarm64.apk.
Uses the GitHub API to get the browser_download_url (redirect-safe),
then downloads via curl_cffi (or urllib fallback).
"""
import argparse
import json
import os
import sys
import urllib.request

from common import resolve_arch_entry, validate_package


def api_get(url, token=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "morphe-auto-build",
        "Accept": "application/vnd.github+json",
    })
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def resolve_asset_url(repo, tag, asset_name, token=None):
    data = api_get(f"https://api.github.com/repos/{repo}/releases/tags/{tag}", token)
    for asset in data.get("assets", []):
        if asset.get("name") == asset_name:
            return asset["browser_download_url"], asset.get("size", 0)
    names = [a.get("name") for a in data.get("assets", []) if a.get("name", "").lower().endswith(".apk")]
    raise RuntimeError(
        f"Asset {asset_name} not found in {repo} tag {tag}. "
        f"Available APKs: {names[:10]}"
    )


def download_url(url, output_path, token=None):
    try:
        from curl_cffi import requests as cffi_requests
        headers = {"User-Agent": "Mozilla/5.0"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        resp = cffi_requests.get(url, headers=headers, impersonate="chrome131",
                                 timeout=120, stream=True, allow_redirects=True)
        resp.raise_for_status()
        with open(output_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fh.write(chunk)
    except ImportError:
        req = urllib.request.Request(url, headers={"User-Agent": "morphe-auto-build"})
        with urllib.request.urlopen(req, timeout=120) as resp, open(output_path, "wb") as fh:
            while True:
                buf = resp.read(8192)
                if not buf:
                    break
                fh.write(buf)


def main():
    parser = argparse.ArgumentParser(description="Download an APK from GitHub Releases")
    parser.add_argument("--config", help="Path to apps/<id>.json (github type)")
    parser.add_argument("--repo", help="e.g. brave/brave-browser")
    parser.add_argument("--tag", help="e.g. v1.95.104")
    parser.add_argument("--asset", help="e.g. BraveMonoarm64.apk")
    parser.add_argument("--apk-version", help="Version without the v prefix, e.g. 1.95.104 (combined with tag_prefix)")
    parser.add_argument("--arch", help="Arch entry name from source.archs, e.g. arm64")
    parser.add_argument("--output", default="base.apk")
    args = parser.parse_args()

    repo, tag, asset = args.repo, args.tag, args.asset
    if args.config:
        with open(args.config) as fh:
            cfg = json.load(fh)
        src = cfg["source"]
        if src.get("type") != "github":
            parser.error(f"config {args.config} is not a github source")
        repo = repo or src["repo"]
        try:
            entry = resolve_arch_entry(src, args.arch)
        except RuntimeError as e:
            parser.error(str(e))
        asset = asset or entry.get("asset")
        prefix = src.get("tag_prefix", "v")
        if not tag and args.apk_version:
            tag = args.apk_version if args.apk_version.startswith(prefix) else f"{prefix}{args.apk_version}"
    if not repo or not tag or not asset:
        parser.error("--config + (--tag | --apk-version) or (--repo + --tag + --asset) is required")

    token = os.environ.get("GITHUB_TOKEN")
    print(f"[github] {repo} tag {tag} asset {asset}")
    try:
        url, size = resolve_asset_url(repo, tag, asset, token)
    except Exception as e:
        print(f"Failed to resolve asset: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"[github] Downloading: {url} ({size:,} bytes listed)")
    download_url(url, args.output, token)

    if os.path.exists(args.output) and os.path.getsize(args.output) > 1_000_000:
        file_type = "apkm" if asset.lower().endswith(".apkm") else "apk"
        try:
            entries = validate_package(args.output, file_type)
        except RuntimeError as e:
            print(f"Validation failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"OK {args.output} ({os.path.getsize(args.output):,} bytes, "
              f"{entries} zip entries, type={file_type})")
        if "GITHUB_OUTPUT" in os.environ:
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"apk_version={tag.lstrip('v')}\n")
    else:
        print("Result file is missing or too small!", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
