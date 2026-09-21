"""Shared helpers for the downloader tools."""

ARCH_REQUIRED_ERROR = "--arch is required for a multi-arch source"


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
