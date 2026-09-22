"""Shared helpers for the downloader tools."""

import os
import shutil
import subprocess
import tempfile
import zipfile

ARCH_REQUIRED_ERROR = "--arch is required for a multi-arch source"

APK_MIN_SIZE = 1_000_000


def resolve_arch_entry(src, arch):
    """Pick the arch entry from source.archs.

    Falls back to the single entry when only one exists and --arch is omitted,
    or to the legacy top-level fields when no archs list exists.
    """
    archs = src.get("archs") or []
    if not archs:
        return {"name": arch or "default", "asset": src.get("asset"),
                "variant_url": src.get("variant_url"),
                "slug_filter": src.get("slug_filter"),
                "version_slug": src.get("version_slug")}
    if arch:
        for entry in archs:
            if entry.get("name") == arch:
                return entry
        raise RuntimeError(
            f"Arch '{arch}' not found. Available: {[a.get('name') for a in archs]}")
    if len(archs) == 1:
        return archs[0]
    raise RuntimeError(ARCH_REQUIRED_ERROR)


def _apk_version(path, aapt_path=None):
    aapt = aapt_path or shutil.which("aapt")
    if not aapt:
        raise RuntimeError("aapt is required for exact APK version validation")
    proc = subprocess.run(
        [aapt, "dump", "badging", path],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"aapt cannot read {path}: {proc.stderr.strip()}")
    import re
    match = re.search(r"versionName='([^']+)'", proc.stdout)
    if not match:
        raise RuntimeError(f"aapt did not report versionName for {path}")
    return match.group(1)


def validate_package(path, file_type, expected_version=None, aapt_path=None):
    """Fail fast when the downloaded file is not what file_type claims.

    Catches the classic mistake of a bundle (.apkm) saved as .apk, which
    otherwise dies later inside the patcher with a cryptic NPE.
    Returns the number of zip entries.
    """
    size = os.path.getsize(path)
    if size < APK_MIN_SIZE:
        raise RuntimeError(f"{path} is too small ({size:,} bytes), download incomplete?")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"{path} is not a valid zip/APK file")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if file_type in ("apkm", "xapk"):
        apks = [n for n in names if n.endswith(".apk")]
        if not apks:
            raise RuntimeError(f"{path} is not a valid bundle (no APK entries inside)")
        if expected_version is not None:
            candidate = next((n for n in apks if n == "base.apk"), apks[0])
            with tempfile.TemporaryDirectory() as td:
                extracted = os.path.join(td, "base.apk")
                with zipfile.ZipFile(path) as zf, open(extracted, "wb") as fh:
                    fh.write(zf.read(candidate))
                detected = _apk_version(extracted, aapt_path)
            if detected != expected_version:
                raise RuntimeError(
                    f"{path} contains APK version {detected}, expected {expected_version}")
    else:
        if "AndroidManifest.xml" not in names:
            raise RuntimeError(
                f"{path} has no AndroidManifest.xml — likely a bundle saved as .apk. "
                "Set file_type to apkm in the app config.")
        if expected_version is not None:
            detected = _apk_version(path, aapt_path)
            if detected != expected_version:
                raise RuntimeError(
                    f"{path} version {detected} != expected {expected_version}")
    return len(names)
