#!/usr/bin/env python3
"""Local, opt-in APKMirror download transport backed by invisible_playwright.

The normal CI/build path remains the native ``apkd`` HTTP provider.  This
module is deliberately separate: it performs one headed browser navigation,
closes only Google's visible ad-vignette control when present, clicks the
APKMirror download control once, and watches the browser's filesystem download
because patched Firefox does not reliably expose ``expect_download`` events.

It does not solve, replay, or fabricate CAPTCHA/Cloudflare tokens.  A visible
window may be used for a normal operator-completed challenge.  The downloaded
bytes are only staged; the caller must run its normal exact package/version/
architecture validator before publishing the file.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


class APKMirrorBrowserError(RuntimeError):
    """Raised when the browser transport cannot produce a complete package."""


VIGNETTE_CLOSE_SELECTOR = "#dismiss-button-element"
DOWNLOAD_SELECTOR = "a.downloadButton[href*='/download/']"


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _active_page(context: Any):
    """Choose the APKMirror page without holding a stale page object."""
    try:
        pages = [page for page in context.pages if not page.is_closed()]
    except Exception:
        return context.new_page()
    for page in reversed(pages):
        try:
            if "apkmirror.com" in (page.url or ""):
                return page
        except Exception:
            continue
    return pages[-1] if pages else context.new_page()


def _frames(page: Any) -> list[Any]:
    try:
        return list(page.frames)
    except Exception:
        return [page]


CHALLENGE_MARKERS = (
    "Just a moment",
    "Performing security verification",
    "Verify you are human",
    "Enable JavaScript and cookies",
    "Attention Required",
)


def describe_page(page: Any) -> dict[str, Any]:
    """Best-effort snapshot of what the browser actually shows.

    Read-only: title, URL and challenge markers only. Never interacts
    with any challenge. Every probe is guarded because the patched
    Firefox may dispose the session mid-transition.
    """
    state: dict[str, Any] = {}
    try:
        state["url"] = page.url
    except Exception:
        pass
    try:
        state["title"] = page.title()
    except Exception:
        pass
    try:
        text = page.locator("body").inner_text(timeout=3000)
        state["markers"] = [m for m in CHALLENGE_MARKERS if m.lower() in text.lower()]
        state["body_preview"] = text[:300]
    except Exception as exc:
        state["body_error"] = f"{type(exc).__name__}: {exc}"
    return state


def dismiss_google_vignette(page: Any, *, log: Callable[[str], None] = print) -> bool:
    """Close one visible Google vignette, including inside child frames."""
    for frame in _frames(page):
        try:
            buttons = frame.locator(VIGNETTE_CLOSE_SELECTOR)
            count = buttons.count()
        except Exception:
            continue
        for index in range(min(count, 4)):
            try:
                candidate = buttons.nth(index)
                if not candidate.is_visible():
                    continue
                candidate.scroll_into_view_if_needed(timeout=2000)
                # The ad is a top-layer overlay; force avoids a second click on
                # the underlying APKMirror control if actionability is delayed.
                candidate.click(force=True, timeout=5000)
                log("[APKMirror browser] closed Google vignette")
                return True
            except Exception:
                continue
    return False


def find_download_href(page: Any, *, exact_fragment: str | None = None) -> str | None:
    """Find the single APKMirror download anchor on a variant page."""
    selectors = [DOWNLOAD_SELECTOR]
    if exact_fragment:
        selectors.insert(
            0,
            f"a.downloadButton[href*='/download/'][href*='{exact_fragment}']",
        )
    for selector in selectors:
        try:
            buttons = page.locator(selector)
            count = buttons.count()
        except Exception:
            continue
        for index in range(count):
            try:
                candidate = buttons.nth(index)
                href = candidate.get_attribute("href") or ""
            except Exception:
                continue
            if not href or (exact_fragment and exact_fragment not in href):
                continue
            return href
    return None


def _click_download_once(page: Any, href: str, *, timeout_ms: int = 20_000) -> None:
    """Re-query and click the previously identified button exactly once."""
    exact_fragment = None
    marker = "android-apk-download"
    if marker in href:
        exact_fragment = href.split("/android-apk-download", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    selectors = [DOWNLOAD_SELECTOR]
    if exact_fragment:
        selectors.insert(
            0,
            f"a.downloadButton[href*='/download/'][href*='{exact_fragment}']",
        )
    button = None
    for selector in selectors:
        try:
            buttons = page.locator(selector)
            count = buttons.count()
        except Exception:
            continue
        for index in range(count):
            try:
                candidate = buttons.nth(index)
                candidate_href = candidate.get_attribute("href") or ""
            except Exception:
                continue
            if candidate_href == href or (exact_fragment and exact_fragment in candidate_href):
                button = candidate
                break
        if button is not None:
            break
    if button is None:
        raise APKMirrorBrowserError("APKMirror download button disappeared before click")
    try:
        button.scroll_into_view_if_needed(timeout=5000)
        button.click(timeout=timeout_ms)
    except Exception as exc:
        raise APKMirrorBrowserError(f"APKMirror download click failed: {exc}") from exc


def _package_shape(path: Path, expected_extension: str) -> bool:
    """Check only enough ZIP shape to avoid staging a partial response."""
    try:
        if not zipfile.is_zipfile(path):
            return False
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
    except (OSError, zipfile.BadZipFile):
        return False
    normalized = str(expected_extension or ".apk").lower().lstrip(".")
    if normalized == "apk":
        return "AndroidManifest.xml" in names
    return any(name.lower().endswith(".apk") for name in names)


def _download_files(directory: Path) -> list[Path]:
    result: list[Path] = []
    try:
        paths = list(directory.iterdir())
    except OSError:
        return result
    for path in paths:
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.endswith((".apk", ".apkm", ".apks", ".xapk", ".part", ".crdownload")):
            try:
                if path.stat().st_size > 1_000_000:
                    result.append(path)
            except OSError:
                continue
    return sorted(result, key=lambda item: item.stat().st_size, reverse=True)


def _wait_for_download(
    directory: Path,
    destination: Path,
    *,
    expected_extension: str,
    timeout: float,
    poll_interval: float,
    sleep: Callable[[float], None],
    log: Callable[[str], None],
) -> tuple[Path, int, int]:
    deadline = time.monotonic() + timeout
    previous: tuple[str, int] | None = None
    stable = 0
    while time.monotonic() < deadline:
        sleep(poll_interval)
        candidates = _download_files(directory)
        if not candidates:
            previous = None
            stable = 0
            continue
        candidate = candidates[0]
        try:
            size = candidate.stat().st_size
        except OSError:
            continue
        current = (str(candidate), size)
        if current == previous:
            stable += 1
        else:
            previous = current
            stable = 0
        log(f"[APKMirror browser] download candidate: {candidate.name} ({size:,} bytes)")
        if stable < 3 or not _package_shape(candidate, expected_extension):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(candidate, destination)
        return candidate, destination.stat().st_size, stable
    raise APKMirrorBrowserError(
        f"APKMirror browser download did not stabilize within {timeout:g}s"
    )


def download_with_invisible_playwright(
    page_url: str,
    destination: str | Path,
    *,
    expected_extension: str = ".apk",
    headless: bool = False,
    profile_dir: str | Path | None = None,
    seed: int | None = None,
    humanize: bool = True,
    initial_wait: float = 45.0,
    page_timeout: float = 60.0,
    download_timeout: float = 420.0,
    poll_interval: float = 4.0,
    close_vignette: bool = True,
    browser_factory: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Download one APKMirror variant page into ``destination``.

    ``browser_factory`` is injectable for unit tests.  Production callers
    leave it unset so ``invisible_playwright`` is imported lazily.
    """
    parsed = urlparse(page_url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith("apkmirror.com"):
        raise APKMirrorBrowserError(f"refusing non-APKMirror browser URL: {page_url}")

    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)

    if browser_factory is None:
        try:
            from invisible_playwright import InvisiblePlaywright
        except ImportError as exc:
            raise APKMirrorBrowserError(
                "invisible-playwright is not installed; install the pinned "
                "dependency and run `python -m invisible_playwright fetch`"
            ) from exc
        browser_factory = InvisiblePlaywright

    temporary_profile: tempfile.TemporaryDirectory[str] | None = None
    if profile_dir is None:
        temporary_profile = tempfile.TemporaryDirectory(prefix="apkmirror-profile-")
        profile_path = Path(temporary_profile.name)
    else:
        profile_path = Path(profile_dir)
        profile_path.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="apkmirror-download-") as download_dir:
            download_path = Path(download_dir)
            browser = browser_factory(
                seed=seed,
                headless=headless,
                humanize=humanize,
                profile_dir=profile_path,
                extra_prefs={
                    "browser.download.folderList": 2,
                    "browser.download.dir": str(download_path),
                    "browser.download.useDownloadDir": True,
                },
            )
            vignette_closed = False
            with browser as context:
                page = context.new_page()
                try:
                    page.bring_to_front()
                except Exception:
                    pass
                log(f"[APKMirror browser] opening {page_url}")
                page.goto(
                    page_url,
                    wait_until="domcontentloaded",
                    timeout=int(page_timeout * 1000),
                )
                if initial_wait > 0:
                    sleep(initial_wait)

                # Wait for the normal page transition without reloading it.
                # This is not a challenge retry; the operator may complete a
                # challenge in the headed window during this bounded wait.
                deadline = time.monotonic() + max(page_timeout, 30.0)
                href = None
                while time.monotonic() < deadline:
                    page = _active_page(context)
                    if close_vignette and not vignette_closed:
                        vignette_closed = dismiss_google_vignette(page, log=log)
                    href = find_download_href(page)
                    if href:
                        break
                    sleep(min(5.0, poll_interval))
                if not href:
                    detail = describe_page(page)
                    log(f"[APKMirror browser] page state on failure: {detail}")
                    raise APKMirrorBrowserError(
                        "APKMirror download button was not found in the "
                        f"browser page (state={detail})"
                    )

                if close_vignette and not vignette_closed:
                    vignette_closed = dismiss_google_vignette(page, log=log)
                log(f"[APKMirror browser] clicking download once: {href.split('?', 1)[0]}")
                _click_download_once(page, href)

                # Do not call page APIs after the click.  The patched Firefox
                # can dispose the Playwright session while the file downloads.
                source, size, _ = _wait_for_download(
                    download_path,
                    destination,
                    expected_extension=expected_extension,
                    timeout=download_timeout,
                    poll_interval=poll_interval,
                    sleep=sleep,
                    log=log,
                )
            return {
                "path": str(destination),
                "source_path": str(source),
                "bytes": size,
                "vignette_dismissed": vignette_closed,
                "headless": headless,
                "seed": seed,
            }
    except APKMirrorBrowserError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise APKMirrorBrowserError(f"invisible_playwright APKMirror flow failed: {exc}") from exc
    finally:
        if temporary_profile is not None:
            temporary_profile.cleanup()
