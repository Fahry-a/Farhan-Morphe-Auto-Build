#!/usr/bin/env python3
"""Download an exact APK version from multiple public mirrors via apkd.

Thin wrapper around https://github.com/Fahry-a/apkd. Each configured
mirror maps 1:1 to an apkd provider (apkpure, aptoide, apkcombo,
apkmirror). Exact-version + package validation still happens here via
tools.common.validate_package before a .partial file is promoted.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

try:
    from common import validate_package
except ModuleNotFoundError:
    from tools.common import validate_package


SUPPORTED_MIRRORS = ("apkpure", "aptoide", "apkcombo", "apkmirror")

_BUNDLE_FILE_TYPES = ("xapk", "apkm", "apks")


def mirror_file_type(cfg, kind):
    """Return the package type expected from a specific mirror."""
    source = cfg["source"]
    mirror = next(
        (m for m in source.get("mirrors", []) if m["type"] == kind),
        {},
    )
    return mirror.get("file_type") or source.get("file_type", "apk")


def _prefer_xapk(cfg, kind):
    return str(mirror_file_type(cfg, kind) or "apk").lower() in _BUNDLE_FILE_TYPES


def _parse_apk_slug(text):
    """Extract (org, repo) from an APKMirror /apk/<org>/<repo>/ URL fragment.

    Legacy fallback only: mirrors-type configs should declare the slug
    explicitly on the mirror entry instead.
    """
    if not text:
        return None
    match = re.search(r"/apk/([^/]+)/([^/]+)/?", str(text))
    if not match:
        return None
    return match.group(1), match.group(2)


def _derive_apkmirror_slug(cfg):
    """Derive (org, repo) from legacy arch entries (slug_filter/variant_url)."""
    for entry in (cfg.get("source", {}).get("archs") or []):
        for key in ("slug_filter", "variant_url"):
            slug = _parse_apk_slug(entry.get(key))
            if slug:
                return slug
    return None


def _resolve_apkmirror_slug(cfg, mirror):
    """Resolve (org, repo) for a mirrors-type apkmirror entry.

    Preferred: explicit ``{"type": "apkmirror", "org": ..., "repo": ...}``
    or ``{"type": "apkmirror", "slug": "org/repo"}`` on the mirror entry.
    Fallback: legacy arch hints (slug_filter/variant_url), then None which
    lets apkd try APKD_APKMIRROR_SLUGS env / auto-search.
    """
    mirror = mirror or {}
    slug = mirror.get("slug")
    if isinstance(slug, str) and "/" in slug:
        org, repo = slug.split("/", 1)
        if org.strip() and repo.strip():
            return org.strip(), repo.strip()
    org, repo = mirror.get("org"), mirror.get("repo")
    if org and repo:
        return str(org).strip(), str(repo).strip()
    return _derive_apkmirror_slug(cfg)


def _apkmirror_slug_map(cfg, mirror=None):
    if mirror is None:
        mirror = next(
            (m for m in cfg.get("source", {}).get("mirrors", [])
             if str(m.get("type") or "").lower() == "apkmirror"),
            {},
        )
    slug = _resolve_apkmirror_slug(cfg, mirror)
    if slug:
        return {cfg["package"]: slug}
    return {}


def _build_request(package, version, arch, prefer_xapk):
    from apkd.models import DownloadRequest

    return DownloadRequest(
        package=package,
        version=version,
        arch=arch,
        prefer_xapk=prefer_xapk,
    )


def _get_provider(kind, slug_map=None):
    from apkd.providers import get_provider

    if kind == "apkmirror":
        return get_provider(kind, slug_map=slug_map or {})
    return get_provider(kind)


def download_from_mirror(kind, cfg, arch, version, output):
    """Resolve exact version via apkd and download it to output."""
    normalized = str(kind or "").lower()
    if normalized == "uptodown":
        raise RuntimeError(
            "uptodown is no longer supported (apkd dropped it: Cloudflare "
            "Turnstile). Migrate apps/*.json mirror type to apkcombo."
        )
    if normalized not in SUPPORTED_MIRRORS:
        raise RuntimeError(f"Unsupported mirror: {kind}")

    package = cfg["package"]
    prefer_xapk = _prefer_xapk(cfg, normalized)
    slug_map = None
    if normalized == "apkmirror":
        mirror = next(
            (m for m in cfg.get("source", {}).get("mirrors", [])
             if str(m.get("type") or "").lower() == "apkmirror"),
            {},
        )
        slug_map = _apkmirror_slug_map(cfg, mirror)

    try:
        provider = _get_provider(normalized, slug_map=slug_map)
        request = _build_request(package, version, arch, prefer_xapk)
        artifact = provider.resolve_request(request)
    except ImportError as exc:
        raise RuntimeError(
            "apkd is not installed. Install with: "
            "pip install 'apkd @ git+https://github.com/Fahry-a/apkd'"
        ) from exc

    if (
        version is not None
        and artifact.version is not None
        and str(artifact.version).strip() != str(version).strip()
    ):
        raise RuntimeError(
            f"{normalized} resolved version {artifact.version}, expected {version}"
        )

    tmp = Path(output)
    tmp.parent.mkdir(parents=True, exist_ok=True)
    provider.download(artifact, tmp)
    return artifact.version or version


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

    errors = []
    for mirror in cfg["source"].get("mirrors", []):
        kind = mirror["type"]
        tmp = f"{args.output}.partial"
        try:
            print(f"== Trying {kind} for {cfg['package']} {args.exact_version} ==")
            version = download_from_mirror(kind, cfg, args.arch,
                                           args.exact_version, tmp)
            validate_package(
                tmp,
                mirror_file_type(cfg, kind),
                expected_version=args.exact_version,
            )
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
