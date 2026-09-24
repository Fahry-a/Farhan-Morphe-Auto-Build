#!/usr/bin/env python3
"""Download an exact APK version from multiple public mirrors via apkd.

Thin wrapper around https://github.com/Fahry-a/apkd. Each configured
mirror maps 1:1 to an apkd provider (apkpure, aptoide, apkcombo,
apkmirror). Exact-version + package validation still happens here via
tools.common.validate_package before a .partial file is promoted. A
mirror-level file_type override also selects the final extension/path, so
bundle bytes are never published under an APK filename.
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


SUPPORTED_MIRRORS = ("apkpure", "aptoide", "apkcombo", "apkmirror", "uptodown")

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


def _mirror_entry(cfg, kind):
    return next(
        (m for m in cfg.get("source", {}).get("mirrors", [])
         if str(m.get("type") or "").lower() == str(kind or "").lower()),
        {},
    )


def _build_request(package, version, arch, prefer_xapk, dpi=None, min_sdk=None,
                   timeout=30.0, app_slug=None, app_id=None):
    from dataclasses import fields
    from apkd.models import DownloadRequest

    values = {
        "package": package,
        "version": version,
        "arch": arch,
        "dpi": dpi,
        "min_sdk": min_sdk,
        "prefer_xapk": prefer_xapk,
        "timeout": timeout,
    }
    # Keep the wrapper compatible with the currently pinned apkd release while
    # allowing a newer provider release to use the optional page hint.
    if app_slug and any(field.name == "app_slug" for field in fields(DownloadRequest)):
        values["app_slug"] = app_slug
    if app_id and any(field.name == "app_id" for field in fields(DownloadRequest)):
        values["app_id"] = str(app_id)
    return DownloadRequest(**values)


def _effective_arch(cfg, kind, arch):
    """Resolve a provider architecture hint without weakening ``universal``.

    ``universal`` is a contract: the selected artifact must support both ARM
    ABIs (arm64-v8a and armeabi-v7a).  A provider-specific ABI override is only
    allowed for a concrete matrix architecture; it must never silently turn a
    universal job into an arm64-only or arm32-only build.
    """
    from apkd.providers.base import normalize_arch

    normalized = (normalize_arch(arch) or "universal").lower()
    if normalized in ("universal", "noarch"):
        return "universal"
    mirror = _mirror_entry(cfg, kind)
    source = cfg.get("source", {})
    return (
        mirror.get("arch")
        or mirror.get("mirror_arch")
        or source.get("default_arch")
        or arch
    )


def _apkmirror_request_params(cfg, mirror, arch):
    """Resolve (arch, dpi, min_sdk) hints for an apkmirror mirror entry.

    Optional per-mirror overrides (``arch``/``dpi``/``min_sdk``) win for a
    concrete matrix architecture. A universal matrix entry always stays
    universal. dpi defaults to ``"*"`` (any density): APKMirror
    variants are usually density-scoped (e.g. ``120-640dpi``), so the old
    ``nodpi``-only default filtered out every row.
    """
    from apkd.providers.base import normalize_arch

    requested = (normalize_arch(arch) or "universal").lower()
    effective = arch if requested in ("universal", "noarch") else (
        mirror.get("arch") or arch
    )
    return (
        effective,
        mirror.get("dpi") or "*",
        mirror.get("min_sdk"),
    )


def _arch_candidates(arch):
    """Return provider filter values without downgrading universal.

    A concrete ABI may fall back to a universal provider row, but a universal
    matrix entry must never fall back to an arm64-only row.
    """
    return [arch]


_EXTENSION_FILE_TYPES = {
    ".apk": "apk",
    ".xapk": "xapk",
    ".apkm": "apkm",
    ".apks": "apks",
}


def _file_type_matches(actual, expected):
    """Allow equivalent bundle containers while keeping APK strict.

    Providers do not agree whether a split release is labelled XAPK or APKM.
    Both contain APK entries and are consumed by the same bundle-aware
    validator, so rejecting the equivalent label creates false mirror failures.
    A monolithic APK is never treated as a bundle (or vice versa).
    """
    if not actual or not expected:
        return False
    actual = str(actual).lower().lstrip(".")
    expected = str(expected).lower().lstrip(".")
    return actual == expected or (
        actual in _BUNDLE_FILE_TYPES and expected in _BUNDLE_FILE_TYPES
    )


def _output_for_type(output: str | Path, file_type: str) -> Path:
    """Give a mirror result a path matching its declared container type.

    A source-level ``file_type`` is only a default.  A mirror may explicitly
    publish a different container (for example XAPK while the source default
    is APK), so writing those bytes to ``base.apk`` would make the later
    validator and patcher guess from a misleading extension.
    """
    normalized = str(file_type or "apk").lower().lstrip(".")
    if normalized not in {"apk", "apkm", "apks", "xapk"}:
        raise ValueError(f"unsupported package file_type {file_type!r}")
    path = Path(output)
    suffix = f".{normalized}"
    if path.suffix.lower() == suffix:
        return path
    return path.with_suffix(suffix)


def _get_provider(kind, slug_map=None):
    from apkd.providers import get_provider

    if kind == "apkmirror":
        return get_provider(kind, slug_map=slug_map or {})
    return get_provider(kind)


def download_from_mirror(kind, cfg, arch, version, output, *, timeout=30.0):
    """Resolve exact version via apkd and download it to output.

    ``timeout`` is part of the request rather than a process-level setting, so
    the same helper is useful for both CI jobs and long-running local audits.
    """
    normalized = str(kind or "").lower()
    if normalized not in SUPPORTED_MIRRORS:
        raise RuntimeError(f"Unsupported mirror: {kind}")

    package = cfg["package"]
    source = cfg.get("source", {})
    if source.get("type") == "mirrors":
        requested_arch = str(arch or "universal").strip().lower().replace("_", "-")
        if requested_arch != "universal":
            raise RuntimeError(
                "mirror sources support only the universal architecture; "
                f"requested {arch}"
            )
        arch = "universal"
    prefer_xapk = _prefer_xapk(cfg, normalized)
    effective_arch = _effective_arch(cfg, normalized, arch)

    try:
        from apkd.models import DownloadRequest  # noqa: F401 (import check)
        provider = _get_provider(
            normalized,
            slug_map=_apkmirror_slug_map(cfg, _mirror_entry(cfg, normalized))
            if normalized == "apkmirror" else None,
        )
    except ImportError as exc:
        raise RuntimeError(
            "apkd is not installed. Install with: "
            "pip install 'apkd @ git+https://github.com/Fahry-a/apkd'"
        ) from exc

    mirror_config = _mirror_entry(cfg, normalized)
    app_slug = mirror_config.get("slug") or mirror_config.get("name")
    app_id = mirror_config.get("app_id")

    if normalized == "apkmirror":
        mirror = mirror_config
        _, dpi, min_sdk = _apkmirror_request_params(cfg, mirror, effective_arch)
        errors = []
        for candidate in _arch_candidates(effective_arch):
            request = _build_request(package, version, candidate, prefer_xapk,
                                     dpi=dpi, min_sdk=min_sdk, timeout=timeout,
                                     app_slug=app_slug, app_id=app_id)
            try:
                artifact = provider.resolve_request(request)
            except Exception as exc:
                errors.append(f"arch={candidate}: {exc}")
                continue
            if (
                version is not None
                and artifact.version is not None
                and str(artifact.version).strip() != str(version).strip()
            ):
                errors.append(
                    f"arch={candidate}: resolved {artifact.version}, "
                    f"expected {version}"
                )
                continue
            break
        else:
            raise RuntimeError(
                f"apkmirror could not resolve {package} {version}: "
                + "; ".join(errors)
            )
    else:
        request = _build_request(package, version, effective_arch, prefer_xapk,
                                 timeout=timeout, app_slug=app_slug, app_id=app_id)
        try:
            artifact = provider.resolve_request(request)
        except ImportError:
            raise
        except Exception as exc:
            raise RuntimeError(f"{normalized} failed for {package} {version}: {exc}") from exc
        if (
            version is not None
            and artifact.version is not None
            and str(artifact.version).strip() != str(version).strip()
        ):
            raise RuntimeError(
                f"{normalized} resolved version {artifact.version}, expected {version}"
            )

    actual = _EXTENSION_FILE_TYPES.get(str(artifact.extension or "").lower())
    expected = str(mirror_file_type(cfg, normalized) or "apk").lower()
    if not _file_type_matches(actual, expected):
        raise RuntimeError(
            f"{normalized} returned {artifact.extension} ({artifact.extra.get('variant_type', 'bundle')}) "
            f"for {package} {artifact.version}, but config expects file_type={expected}; "
            f"exact {version} is not published as {expected} on this mirror"
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
        if mirror.get("enabled", True) is False:
            continue
        kind = mirror["type"]
        mirror_type = str(mirror_file_type(cfg, kind) or "apk").lower().lstrip(".")
        tmp = None
        try:
            output = _output_for_type(args.output, mirror_type)
            tmp = f"{output}.partial"
            Path(tmp).unlink(missing_ok=True)
            output.unlink(missing_ok=True)
            print(f"== Trying {kind} for {cfg['package']} {args.exact_version} ==")
            version = download_from_mirror(kind, cfg, args.arch,
                                           args.exact_version, tmp)
            validate_package(
                tmp,
                mirror_type,
                expected_version=args.exact_version,
                expected_arch=args.arch,
                expected_package=cfg["package"],
            )
            os.replace(tmp, output)
            print(f"OK {output}: source={kind}, version={version}")
            if "GITHUB_OUTPUT" in os.environ:
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                    fh.write(f"apk_version={args.exact_version}\n")
                    fh.write(f"base_file={output}\n")
                    fh.write(f"file_type={mirror_type}\n")
                    fh.write(f"download_source={kind}\n")
            return
        except Exception as exc:
            errors.append(f"{kind}: {exc}")
            print(f"FAILED {kind}: {exc}", file=sys.stderr)
            if tmp is not None:
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
