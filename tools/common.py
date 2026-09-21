"""Shared helpers for the downloader tools."""

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


def validate_package(path, file_type):
    """Fail fast when the downloaded file is not what file_type claims.

    Catches the classic mistake of a bundle (.apkm) saved as .apk, which
    otherwise dies later inside the patcher with a cryptic NPE.
    Returns the number of zip entries.
    """
    import os
    import zipfile
    size = os.path.getsize(path)
    if size < APK_MIN_SIZE:
        raise RuntimeError(f"{path} is too small ({size:,} bytes), download incomplete?")
    if not zipfile.is_zipfile(path):
        raise RuntimeError(f"{path} is not a valid zip/APK file")
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    if file_type == "apkm":
        apks = [n for n in names if n.endswith(".apk")]
        if not apks:
            raise RuntimeError(f"{path} is not a valid APKM bundle (no APK entries inside)")
    else:
        if "AndroidManifest.xml" not in names:
            raise RuntimeError(
                f"{path} has no AndroidManifest.xml — likely a bundle saved as .apk. "
                "Set file_type to apkm in the app config.")
    return len(names)
