"""Shared helpers for the downloader tools."""

import os
import re
import shutil
import subprocess
import tempfile
import zipfile

ARCH_REQUIRED_ERROR = "--arch is required for a multi-arch source"
MIRROR_DEFAULT_ARCH = "universal"

APK_MIN_SIZE = 1_000_000


def source_architecture_entries(source):
    """Return architecture entries for a source configuration.

    Mirror-backed sources have one implicit contract: ``universal``.  They do
    not need a hand-written ABI list in every app JSON file.  An explicit
    ``source.archs`` block is accepted only when it contains exactly that one
    contract, which keeps older configurations readable while preventing an
    accidental per-ABI mirror matrix.
    """
    source = source or {}
    raw_entries = source.get("archs")
    if not raw_entries:
        raw_entries = (
            [{"name": MIRROR_DEFAULT_ARCH}]
            if source.get("type") == "mirrors"
            else []
        )

    entries = []
    for entry in raw_entries:
        if isinstance(entry, str):
            entry = {"name": entry}
        if not isinstance(entry, dict):
            raise ValueError(
                f"source.archs entries must be objects, got {entry!r}"
            )
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ValueError("source.archs entries must have a non-empty name")
        entries.append({**entry, "name": name})

    if source.get("type") == "mirrors":
        names = [entry["name"].lower().replace("_", "-") for entry in entries]
        if len(entries) != 1 or names != [MIRROR_DEFAULT_ARCH]:
            raise ValueError(
                "source.type=mirrors supports only the implicit universal "
                "architecture; remove per-ABI source.archs entries"
            )
        return [{"name": MIRROR_DEFAULT_ARCH}]
    return entries


def resolve_arch_entry(src, arch):
    """Pick the arch entry from source.archs.

    Mirror sources default to the single universal contract. Other source
    types retain their explicit architecture entries and legacy top-level
    fields.
    """
    if src.get("type") == "mirrors":
        archs = source_architecture_entries(src)
    else:
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


def _locate_aapt():
    """Find aapt via the AAPT env var, PATH, or the Android SDK dir.

    GitHub's ubuntu images ship the Android SDK (ANDROID_HOME set) but
    build-tools is not on PATH, so shutil.which alone fails there.
    """
    import glob
    aapt = os.environ.get("AAPT") or shutil.which("aapt")
    if aapt and os.path.isfile(aapt) and os.access(aapt, os.X_OK):
        return aapt
    android_home = os.environ.get("ANDROID_HOME")
    if android_home:
        candidates = [
            p for p in glob.glob(
                os.path.join(android_home, "build-tools", "*", "aapt")
            )
            if os.path.isfile(p) and os.access(p, os.X_OK)
        ]
        if candidates:
            def version_key(p):
                parts = os.path.basename(os.path.dirname(p)).split(".")
                return tuple(int(x) if x.isdigit() else 0 for x in parts)
            return max(candidates, key=version_key)
    return None


def _apk_badging(path, aapt_path=None):
    aapt = aapt_path or _locate_aapt()
    if not aapt:
        raise RuntimeError("aapt is required for exact APK version validation")
    proc = subprocess.run(
        [aapt, "dump", "badging", path],
        capture_output=True, text=True, check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"aapt cannot read {path}: {proc.stderr.strip()}")
    return proc.stdout


def _apk_version(path, aapt_path=None):
    badging = _apk_badging(path, aapt_path)
    match = re.search(r"versionName='([^']+)'", badging)
    if not match:
        raise RuntimeError(f"aapt did not report versionName for {path}")
    return match.group(1)


def _apk_package(path, aapt_path=None):
    badging = _apk_badging(path, aapt_path)
    match = re.search(r"^package:\s+name='([^']+)'", badging, re.MULTILINE)
    if not match:
        raise RuntimeError(f"aapt did not report package name for {path}")
    return match.group(1)


def _native_codes(badging: str) -> set[str]:
    match = re.search(r"^native-code:\s*(.*)$", badging, re.MULTILINE)
    if not match:
        return set()
    return set(re.findall(r"'([^']+)'", match.group(1)))


def _required_architectures(arch: str) -> set[str]:
    value = str(arch or "").strip().lower().replace("_", "-")
    aliases = {
        "universal": {"arm64-v8a", "armeabi-v7a"},
        "noarch": {"arm64-v8a", "armeabi-v7a"},
        "arm64": {"arm64-v8a"},
        "aarch64": {"arm64-v8a"},
        "arm64-v8a": {"arm64-v8a"},
        "arm32": {"armeabi-v7a"},
        "arm-v7a": {"armeabi-v7a"},
        "armeabi-v7a": {"armeabi-v7a"},
    }
    return aliases.get(value, {value} if value else set())


def _validate_apk_architecture(path, arch, aapt_path=None):
    badging = _apk_badging(path, aapt_path)
    detected = _native_codes(badging)
    required = _required_architectures(arch)
    # An APK with no native-code is architecture-independent and therefore
    # valid for both ARM targets.  If native code is declared, universal means
    # both ARM ABIs, not merely whichever ABI happened to be listed first.
    if required and detected and not required.issubset(detected):
        missing = ", ".join(sorted(required - detected))
        found = ", ".join(sorted(detected)) or "none"
        raise RuntimeError(
            f"{path} does not satisfy arch={arch}: missing {missing}; "
            f"detected native-code: {found}"
        )
    return detected


def _validate_bundle_architecture(path, arch, aapt_path=None):
    required = _required_architectures(arch)
    if not required:
        return set()
    detected: set[str] = set()
    inspection_errors: list[str] = []
    with tempfile.TemporaryDirectory() as td:
        with zipfile.ZipFile(path) as archive:
            apk_names = [name for name in archive.namelist() if name.lower().endswith(".apk")]
            for index, name in enumerate(apk_names):
                extracted = os.path.join(td, f"{index}.apk")
                with open(extracted, "wb") as handle:
                    handle.write(archive.read(name))
                try:
                    detected.update(_native_codes(_apk_badging(extracted, aapt_path)))
                except RuntimeError as exc:
                    inspection_errors.append(f"{name}: {exc}")
    if inspection_errors:
        details = "; ".join(inspection_errors)
        raise RuntimeError(
            f"{path} native ABI inspection was incomplete: {details}"
        )
    if detected and not required.issubset(detected):
        missing = ", ".join(sorted(required - detected))
        found = ", ".join(sorted(detected)) or "none"
        raise RuntimeError(
            f"{path} does not satisfy arch={arch}: missing {missing}; "
            f"bundle native-code: {found}"
        )
    return detected


def validate_package(path, file_type, expected_version=None, aapt_path=None,
                     expected_arch=None, expected_package=None):
    """Fail fast when the downloaded file is not what file_type claims.

    Catches the classic mistake of a bundle (.apkm) saved as .apk, which
    otherwise dies later inside the patcher with a cryptic NPE. When
    ``expected_arch`` is ``universal``, both ARM ABIs are mandatory unless the
    package has no native code at all. ``expected_package`` additionally
    verifies the aapt-reported package identity for manual mirror handoffs.
    Returns the number of zip entries.
    """
    file_type = str(file_type or "apk").lower().lstrip(".")
    size = os.path.getsize(path)
    if size < APK_MIN_SIZE:
        raise RuntimeError(f"{path} is too small ({size:,} bytes), download incomplete?")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"{path} is not a valid zip/APK file")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if file_type in ("apkm", "xapk", "apks"):
        apks = [n for n in names if n.endswith(".apk")]
        if not apks:
            raise RuntimeError(f"{path} is not a valid bundle (no APK entries inside)")
        if expected_version is not None or expected_package is not None:
            candidate = next((n for n in apks if n == "base.apk"), apks[0])
            with tempfile.TemporaryDirectory() as td:
                extracted = os.path.join(td, "base.apk")
                with zipfile.ZipFile(path) as zf, open(extracted, "wb") as fh:
                    fh.write(zf.read(candidate))
                if expected_version is not None:
                    detected = _apk_version(extracted, aapt_path)
                    if detected != expected_version:
                        raise RuntimeError(
                            f"{path} contains APK version {detected}, expected {expected_version}")
                if expected_package is not None:
                    detected_package = _apk_package(extracted, aapt_path)
                    if detected_package != expected_package:
                        raise RuntimeError(
                            f"{path} contains package {detected_package}, expected {expected_package}")
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
        if expected_package is not None:
            detected_package = _apk_package(path, aapt_path)
            if detected_package != expected_package:
                raise RuntimeError(
                    f"{path} package {detected_package} != expected {expected_package}")
    if expected_arch:
        if file_type in ("apkm", "xapk", "apks"):
            _validate_bundle_architecture(path, expected_arch, aapt_path)
        else:
            _validate_apk_architecture(path, expected_arch, aapt_path)
    return len(names)
