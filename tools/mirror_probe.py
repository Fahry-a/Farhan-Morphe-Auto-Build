#!/usr/bin/env python3
"""Config-driven live audit for every mirror entry in ``apps/*.json``.

The normal build intentionally stops at the first healthy mirror.  That is
fast, but it hides provider regressions and stale entries in the fallback
chain.  This module provides a separate, read-only network audit: it resolves
the exact version supported by each application's Morphe patch, downloads the
selected mirror into a temporary file, validates the package and records a
machine-readable result.

The module is intentionally independent of GitHub Actions.  Mirror-backed
applications use one implicit ``universal`` contract; the workflow only
supplies a config/mirror triple (the architecture is still carried in the
matrix for clarity). Adding an application or a mirror therefore requires
configuration, not another script.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from .common import source_architecture_entries, validate_package
    from .mirror_download import download_from_mirror
except ImportError:  # pragma: no cover - direct ``python tools/...`` use
    from common import source_architecture_entries, validate_package
    from mirror_download import download_from_mirror

SUPPORTED_MIRRORS = frozenset({"apkpure", "apkcombo", "aptoide", "apkmirror"})


@dataclass(frozen=True)
class MirrorTarget:
    """One independently testable app/architecture/mirror combination."""

    config: str
    app: str
    package: str
    arch: str
    mirror: str
    file_type: str

    @property
    def name(self) -> str:
        return f"{self.app}/{self.arch}/{self.mirror}"

    def as_matrix(self) -> dict[str, str]:
        """Return only workflow-safe scalar fields."""
        return asdict(self)


@dataclass
class ProbeResult:
    """Outcome of one target; serialisable for CI summaries and artifacts."""

    target: MirrorTarget
    status: str
    version: str | None = None
    file_type: str | None = None
    bytes: int | None = None
    entries: int | None = None
    sha256: str | None = None
    duration_seconds: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["target"] = asdict(self.target)
        return result


def _as_filter(value: str | None) -> set[str] | None:
    if value is None or not value.strip() or value.strip().lower() == "all":
        return None
    return {part.strip().lower() for part in value.split(",") if part.strip()}


def _read_config(path: Path) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"cannot read app config {path}: {exc}") from exc
    if not isinstance(config, dict):
        raise ValueError(f"app config {path} must contain a JSON object")
    for field in ("id", "package"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            raise ValueError(f"app config {path} is missing string field {field!r}")
    return config


def _normalise_file_type(value: Any, *, default: str = "apk") -> str:
    text = str(value or default).strip().lower().lstrip(".")
    aliases = {"xapk": "xapk", "apkm": "apkm", "apks": "apks", "apk": "apk"}
    if text not in aliases:
        raise ValueError(f"unsupported package file_type {value!r}")
    return aliases[text]


def _arches(source: dict[str, Any]) -> list[dict[str, Any]]:
    return source_architecture_entries(source)


def discover_targets(
    config_dir: str | Path = "apps",
    *,
    app_filter: str | None = None,
    arch_filter: str | None = None,
    mirror_filter: str | None = None,
) -> list[MirrorTarget]:
    """Discover every enabled mirror target without making network requests.

    ``source.mirror`` entries may set ``enabled: false`` to document a mirror
    without making it part of the audit.  The default remains enabled, so old
    configurations behave exactly as before.
    """
    root = Path(config_dir)
    if not root.is_dir():
        raise ValueError(f"app config directory does not exist: {root}")
    apps = _as_filter(app_filter)
    arches = _as_filter(arch_filter)
    mirrors = _as_filter(mirror_filter)
    targets: list[MirrorTarget] = []

    for path in sorted(root.glob("*.json")):
        config = _read_config(path)
        app_id = config["id"]
        if apps is not None and app_id.lower() not in apps and path.stem.lower() not in apps:
            continue
        if config.get("enabled", True) is False:
            continue
        source = config.get("source")
        if not isinstance(source, dict) or source.get("type") != "mirrors":
            continue
        source_type = _normalise_file_type(source.get("file_type"), default="apk")
        for arch_entry in _arches(source):
            arch = str(arch_entry["name"])
            if arches is not None and arch.lower() not in arches:
                continue
            entries = source.get("mirrors") or []
            if not isinstance(entries, list):
                raise ValueError(f"{path}: source.mirrors must be a list")
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(f"{path}: mirror entry {index} must be an object")
                if entry.get("enabled", True) is False:
                    continue
                kind = str(entry.get("type") or "").strip().lower()
                if not kind:
                    raise ValueError(f"{path}: mirror entry {index} has no type")
                if kind not in SUPPORTED_MIRRORS:
                    raise ValueError(
                        f"{path}: unsupported mirror type {kind!r}; "
                        f"expected one of {sorted(SUPPORTED_MIRRORS)}"
                    )
                if mirrors is not None and kind not in mirrors:
                    continue
                file_type = _normalise_file_type(
                    entry.get("file_type"), default=source_type
                )
                targets.append(
                    MirrorTarget(
                        config=path.as_posix(),
                        app=app_id,
                        package=config["package"],
                        arch=arch,
                        mirror=kind,
                        file_type=file_type,
                    )
                )
    return targets


def _safe_version(version: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "_" for character in version)


def _output_path(directory: Path, target: MirrorTarget, version: str) -> Path:
    return directory / (
        f"{target.app}-{target.arch}-{target.mirror}-v{_safe_version(version)}."
        f"{target.file_type}"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _aapt_path(explicit: str | None = None) -> str | None:
    return explicit or os.environ.get("AAPT") or shutil.which("aapt")


def probe_target(
    target: MirrorTarget,
    version: str,
    download_dir: str | Path,
    *,
    aapt_path: str | None = None,
    timeout: float = 30.0,
) -> ProbeResult:
    """Download and validate one target; always cleans partial/output on error."""
    started = time.monotonic()
    directory = Path(download_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = _output_path(directory, target, version)
    partial = Path(f"{output}.partial")
    config = _read_config(Path(target.config))
    output.unlink(missing_ok=True)
    partial.unlink(missing_ok=True)
    succeeded = False
    try:
        returned_version = download_from_mirror(
            target.mirror,
            config,
            target.arch,
            version,
            str(output),
            timeout=timeout,
        )
        if returned_version and str(returned_version).strip() != str(version).strip():
            raise RuntimeError(
                f"downloader returned version {returned_version}, expected {version}"
            )
        entries = validate_package(
            str(output),
            target.file_type,
            expected_version=version,
            aapt_path=_aapt_path(aapt_path),
            expected_arch=target.arch,
        )
        succeeded = True
        return ProbeResult(
            target=target,
            status="pass",
            version=str(returned_version or version),
            file_type=target.file_type,
            bytes=output.stat().st_size,
            entries=entries,
            sha256=_sha256(output),
            duration_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001 - every mirror failure is reported
        return ProbeResult(
            target=target,
            status="fail",
            version=version,
            file_type=target.file_type,
            duration_seconds=round(time.monotonic() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        partial.unlink(missing_ok=True)
        if not succeeded:
            output.unlink(missing_ok=True)


def _result_line(result: ProbeResult) -> str:
    target = result.target
    if result.status == "pass":
        return (
            f"OK mirror={target.mirror} app={target.app} arch={target.arch} "
            f"version={result.version} file_type={result.file_type} "
            f"bytes={result.bytes} entries={result.entries} "
            f"sha256={result.sha256}"
        )
    return (
        f"FAIL mirror={target.mirror} app={target.app} arch={target.arch} "
        f"version={result.version or '?'} error={result.error}"
    )


def _write_report(
    report_path: str | Path,
    targets: list[MirrorTarget],
    results: list[ProbeResult],
    *,
    download_dir: str | None,
) -> None:
    passed = sum(result.status == "pass" for result in results)
    report = {
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "download_dir": download_dir,
        "targets": [target.as_matrix() for target in targets],
        "results": [result.to_dict() for result in results],
    }
    path = Path(report_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def _append_github_summary(results: list[ProbeResult]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    passed = sum(result.status == "pass" for result in results)
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write("### Live mirror validation\n\n")
        handle.write(f"**Result:** {passed}/{len(results)} passed\n\n")
        handle.write("| App | Arch | Mirror | Version | Result | Details |\n")
        handle.write("| --- | --- | --- | --- | --- | --- |\n")
        for result in results:
            target = result.target
            detail = (
                f"{result.bytes:,} bytes; {result.entries} entries"
                if result.status == "pass"
                else (result.error or "unknown error").replace("|", "\\|")
            )
            handle.write(
                f"| {target.app} | {target.arch} | {target.mirror} | "
                f"{result.version or '?'} | {result.status.upper()} | {detail} |\n"
            )
        handle.write("\n")


def _resolve_config_version(
    config: dict[str, Any], allow_experimental: bool
) -> dict[str, Any]:
    """Adapt an app config to :func:`tools.resolve_version.resolve`."""
    try:
        from .resolve_version import resolve
    except ImportError:  # direct ``python tools/mirror_probe.py`` execution
        from resolve_version import resolve
    return resolve(
        config["patch_repo"],
        config["package"],
        allow_experimental,
        os.environ.get("GITHUB_TOKEN"),
    )


def run_audit(
    targets: Iterable[MirrorTarget],
    *,
    download_dir: str | Path | None = None,
    allow_experimental: bool = False,
    timeout: float = 30.0,
    aapt_path: str | None = None,
    resolver: Callable[[dict[str, Any], bool], str | dict[str, Any]] | None = None,
    downloader: Callable[..., Any] | None = None,
) -> tuple[list[ProbeResult], str | None]:
    """Audit targets, resolving each app's patch-supported version only once.

    ``resolver`` and ``downloader`` are injectable for unit tests.  In normal
    operation they are omitted and the real network-backed implementations are
    used.  The returned directory is temporary and must be removed by the
    caller when ``download_dir`` is not supplied.
    """
    target_list = list(targets)
    if not target_list:
        return [], None

    if resolver is None:
        resolver = _resolve_config_version
    if downloader is None:
        downloader = download_from_mirror

    temporary: tempfile.TemporaryDirectory[str] | None = None
    if download_dir is None:
        temporary = tempfile.TemporaryDirectory(prefix="morphe-mirror-audit-")
        work_dir = Path(temporary.name)
        returned_dir = None
    else:
        work_dir = Path(download_dir)
        returned_dir = str(work_dir)

    versions: dict[str, str] = {}
    results: list[ProbeResult] = []
    try:
        for target in target_list:
            config = _read_config(Path(target.config))
            version_key = target.config
            try:
                if version_key not in versions:
                    resolved = resolver(
                        config, allow_experimental or bool(config.get("allow_experimental"))
                    )
                    if isinstance(resolved, dict):
                        resolved = resolved.get("apk_version")
                    if not resolved:
                        raise ValueError("version resolver returned no apk_version")
                    versions[version_key] = str(resolved)
                version = versions[version_key]
            except Exception as exc:  # noqa: BLE001
                results.append(
                    ProbeResult(
                        target=target,
                        status="fail",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                print(_result_line(results[-1]), flush=True)
                continue
            # Keep the production downloader injectable without duplicating the
            # validation/cleanup logic in tests.
            if downloader is download_from_mirror:
                result = probe_target(
                    target, version, work_dir, aapt_path=aapt_path, timeout=timeout
                )
            else:
                result = _probe_with_downloader(
                    target,
                    version,
                    work_dir,
                    downloader,
                    aapt_path=aapt_path,
                    timeout=timeout,
                )
            results.append(result)
            print(_result_line(result), flush=True)
    finally:
        if temporary is not None:
            temporary.cleanup()
    return results, returned_dir


def _probe_with_downloader(
    target: MirrorTarget,
    version: str,
    directory: Path,
    downloader: Callable[..., Any],
    *,
    aapt_path: str | None,
    timeout: float,
) -> ProbeResult:
    started = time.monotonic()
    output = _output_path(directory, target, version)
    output.unlink(missing_ok=True)
    succeeded = False
    try:
        returned = downloader(
            target.mirror,
            _read_config(Path(target.config)),
            target.arch,
            version,
            str(output),
            timeout=timeout,
        )
        if returned and str(returned).strip() != str(version).strip():
            raise RuntimeError(
                f"downloader returned version {returned}, expected {version}"
            )
        entries = validate_package(
            str(output), target.file_type, expected_version=version,
            aapt_path=_aapt_path(aapt_path), expected_arch=target.arch,
        )
        succeeded = True
        return ProbeResult(
            target=target, status="pass", version=str(returned or version),
            file_type=target.file_type, bytes=output.stat().st_size,
            entries=entries, sha256=_sha256(output),
            duration_seconds=round(time.monotonic() - started, 3),
        )
    except Exception as exc:  # noqa: BLE001
        return ProbeResult(
            target=target, status="fail", version=version,
            file_type=target.file_type,
            duration_seconds=round(time.monotonic() - started, 3),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
        Path(f"{output}.partial").unlink(missing_ok=True)
        if not succeeded:
            output.unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and validate every configured mirror source"
    )
    parser.add_argument("--config", help="single app config (for example apps/adm.json)")
    parser.add_argument("--app", default="all", help="app id, comma-separated, or all")
    parser.add_argument("--arch", default="all", help="architecture or all")
    parser.add_argument("--mirror", default="all", help="provider name, comma-separated, or all")
    parser.add_argument("--config-dir", default="apps", help=argparse.SUPPRESS)
    parser.add_argument("--report", default="mirror-report.json", help="JSON report path")
    parser.add_argument("--download-dir", help="keep downloads in this directory")
    parser.add_argument("--allow-experimental", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--matrix", action="store_true", help="print workflow matrix JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.config:
            config_path = Path(args.config)
            config = _read_config(config_path)
            if config.get("source", {}).get("type") != "mirrors":
                raise ValueError(f"{config_path}: source.type must be mirrors")
            if args.app != "all" and config["id"].lower() not in _as_filter(args.app):
                raise ValueError(f"{config_path}: app does not match --app")
            targets = discover_targets(
                config_path.parent,
                app_filter=config["id"],
                arch_filter=args.arch,
                mirror_filter=args.mirror,
            )
            # A single-config invocation must not accidentally include sibling
            # configs with the same app id.
            targets = [target for target in targets if target.config == config_path.as_posix()]
        else:
            targets = discover_targets(
                args.config_dir,
                app_filter=args.app,
                arch_filter=args.arch,
                mirror_filter=args.mirror,
            )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.matrix:
        print(json.dumps([target.as_matrix() for target in targets], separators=(",", ":")))
        return 0
    if not targets:
        print("No enabled mirror targets matched the requested filters.", file=sys.stderr)
        return 2

    results, kept_dir = run_audit(
        targets,
        download_dir=args.download_dir,
        allow_experimental=args.allow_experimental,
        timeout=args.timeout,
        aapt_path=_aapt_path(),
    )
    _write_report(
        args.report,
        targets,
        results,
        download_dir=kept_dir,
    )
    _append_github_summary(results)
    failed = [result for result in results if result.status != "pass"]
    print(
        f"SUMMARY total={len(results)} passed={len(results) - len(failed)} "
        f"failed={len(failed)}",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
