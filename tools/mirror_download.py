#!/usr/bin/env python3
"""Download an exact APK version from multiple public mirrors."""
import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from common import resolve_arch_entry, validate_package
except ModuleNotFoundError:
    from tools.common import resolve_arch_entry, validate_package


def http_get(url, headers=None, timeout=60):
    req = urllib.request.Request(url, headers=headers or {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"
    })
    return urllib.request.urlopen(req, timeout=timeout)


def http_read(url, headers=None, timeout=60):
    """Read a page with browser impersonation when curl_cffi is available."""
    try:
        from curl_cffi import requests
        r = requests.get(
            url, impersonate="chrome131", timeout=timeout,
            allow_redirects=True, headers=headers or {"User-Agent": "Mozilla/5.0"},
        )
        r.raise_for_status()
        return r.content
    except ImportError:
        with http_get(url, headers=headers, timeout=timeout) as r:
            return r.read()


def download_url(url, output, headers=None):
    try:
        from curl_cffi import requests
        r = requests.get(url, impersonate="chrome131", timeout=300,
                         stream=True, allow_redirects=True,
                         headers=headers or {"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        with open(output, "wb") as fh:
            for chunk in r.iter_content(65536):
                if chunk:
                    fh.write(chunk)
    except ImportError:
        with http_get(url, headers=headers, timeout=300) as r, open(output, "wb") as fh:
            while True:
                chunk = r.read(65536)
                if not chunk:
                    break
                fh.write(chunk)


def _apkpure_headers():
    # Match the public APKPure mobile API client rather than the web download
    # page, which is protected by anti-bot checks on GitHub-hosted runners.
    return {
        "User-Agent": (
            "Dalvik/2.1.0 (Linux; U; Android 15; Pixel 4a (5G) "
            "Build/BP1A.250505.005); APKPure/3.20.53 (Aegon)"
        ),
        "Ual-Access-Businessid": "projecta",
        "Ual-Access-ProjectA": json.dumps({
            "device_info": {
                "abis": [
                    "arm64-v8a", "armeabi-v7a", "armeabi", "x86", "x86_64"
                ],
                "language": "en-US",
                "os_ver": "35",
            }
        }, separators=(",", ":")),
    }


def apkpure_link(package, name, version):
    # APKPure's mobile API exposes historical versions and their CDN asset
    # URLs without requiring the web page's Cloudflare/anti-bot flow.
    del name
    url = (
        "https://tapi.pureapk.com/v3/get_app_his_version"
        f"?hl=en&package_name={urllib.parse.quote(package, safe='')}"
    )
    with http_get(url, headers=_apkpure_headers()) as r:
        data = json.loads(r.read())

    entries = data.get("version_list", [])
    target = next(
        (
            item for item in entries
            if item.get("version_name") == version
            and item.get("asset", {}).get("url")
            and str(item.get("asset", {}).get("type", "")).upper() == "APK"
        ),
        None,
    )
    if not target:
        raise RuntimeError(f"APKPure version {version} not found")

    return target["asset"]["url"]


def _uptodown_download_url(page_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "html.parser")
    button = soup.find("button", id="detail-download-button")
    if not button or not button.get("data-url"):
        raise RuntimeError("Uptodown download token not found")
    data_url = button["data-url"]
    if data_url.startswith(("http://", "https://")):
        return data_url
    return f"https://dw.uptodown.com/dwn/{data_url}"


def _uptodown_page_version(page_html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(page_html, "html.parser")
    version = soup.select_one("div.version")
    if version:
        return version.get_text(strip=True).lstrip("v")
    return None


# Uptodown Android client auth (mirrors Obtainium's current implementation).
# The old hourly APIKEY flow now returns HTTP 526 from www.uptodown.app;
# the current client uses HMAC-signed auth to obtain a short-lived Bearer JWT.
_UPTODOWN_HMAC_KEY = "MDGMXUMdvHJBG/vjdFgmqX6LUdy7ecfwvYNd0gyfOCs="
_UPTODOWN_CLIENT_VERSION = "739"
_UPTODOWN_ANDROID_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 16; Pixel 8 Pro "
    "Build/BP4A.260205.001)"
)
_UPTODOWN_CDN_UA = (
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-G955F "
    "Build/AP2A.240805.005)"
)


def _uptodown_client_headers(token=None, for_auth=False):
    headers = {
        "User-Agent": _UPTODOWN_ANDROID_UA,
        "Identificador": "Uptodown_Android",
        "Identificador-Version": _UPTODOWN_CLIENT_VERSION,
    }
    if for_auth:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _uptodown_auth_token(retries=3):
    """Obtain a Bearer JWT, retrying transient endpoint failures.

    The auth endpoint intermittently returns HTTP 410/429/5xx from
    GitHub-hosted runner IPs and succeeds minutes later with identical
    code, so retry with backoff instead of failing fast. Other 4xx
    errors fail immediately.
    """
    import hmac as hmac_mod
    import secrets
    retry_status = (410, 429, 500, 502, 503, 504)
    for attempt in range(retries):
        identifier = secrets.token_hex(8)
        timestamp = str(int(time.time()))
        signature = hmac_mod.new(
            _UPTODOWN_HMAC_KEY.encode(),
            timestamp.encode(),
            hashlib.sha256,
        ).hexdigest()
        body = urllib.parse.urlencode({
            "identifier": identifier,
            "id_plataforma": "13",
            "lang": "en",
            "unixtime": timestamp,
            "hmac": signature,
        }).encode()
        url = (
            "https://www.uptodown.app/eapi/auth/token"
            f"?identifier={urllib.parse.quote(identifier, safe='')}"
        )
        req = urllib.request.Request(
            url, data=body, headers=_uptodown_client_headers(for_auth=True),
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as exc:
            if exc.code not in retry_status or attempt == retries - 1:
                raise
            time.sleep(2 ** attempt)
            continue
        token = data.get("token") if isinstance(data, dict) else None
        if not token or not isinstance(token, str) or token.count(".") != 2:
            raise RuntimeError("Uptodown auth response has no token")
        return token
    raise RuntimeError("Uptodown auth failed after retries")


def _uptodown_api_get(path, token):
    req = urllib.request.Request(
        f"https://www.uptodown.app/eapi{path}",
        headers=_uptodown_client_headers(token=token),
    )
    return urllib.request.urlopen(req, timeout=60)


def _uptodown_api_get_json(path, token):
    with _uptodown_api_get(path, token) as r:
        return json.loads(r.read())


def _uptodown_html_file_id(name, app_id, version):
    """Resolve an exact fileID through the public HTML versions endpoint.

    The eAPI compatible/versions endpoint currently returns
    "Device not found" for device/0 and device/1, so exact-version
    resolution goes through the same versions JSON used by the website,
    which needs no auth.
    """
    base = f"https://{name}.en.uptodown.com/android"
    for page in range(1, 21):
        endpoint = f"{base}/apps/{app_id}/versions/{page}"
        data = json.loads(http_read(endpoint))
        items = data.get("data", [])
        if not isinstance(items, list) or not items:
            break
        for item in items:
            if item.get("version") != version:
                continue
            file_id = item.get("fileID") or item.get("fileid")
            if not file_id:
                raise RuntimeError(
                    f"Uptodown version {version} has no file ID"
                )
            return file_id
    raise RuntimeError(f"Uptodown version {version} not found")


def uptodown_link(package, name, version):
    token = _uptodown_auth_token()
    try:
        data = _uptodown_api_get_json(
            f"/apps/byPackagename/{urllib.parse.quote(package, safe='')}",
            token,
        )
    except urllib.error.HTTPError as exc:
        # Bearer tokens are short-lived; retry once with a fresh token.
        if getattr(exc, "code", None) != 401:
            raise
        token = _uptodown_auth_token()
        data = _uptodown_api_get_json(
            f"/apps/byPackagename/{urllib.parse.quote(package, safe='')}",
            token,
        )
    detail = data.get("data", data)
    app_id = detail.get("appID") or detail.get("id")
    if not app_id:
        raise RuntimeError(f"Uptodown app {package} not found")

    # Exact version is mandatory; never fall back to the closest release.
    # Resolved via the public HTML versions JSON (no auth, paginated).
    file_id = _uptodown_html_file_id(name, app_id, version)

    try:
        data = _uptodown_api_get_json(
            f"/apps/{app_id}/file/{file_id}/downloadUrl",
            token,
        )
    except urllib.error.HTTPError as exc:
        if getattr(exc, "code", None) != 401:
            raise
        token = _uptodown_auth_token()
        data = _uptodown_api_get_json(
            f"/apps/{app_id}/file/{file_id}/downloadUrl",
            token,
        )
    try:
        body = data if isinstance(data, dict) else {}
        if body.get("success") not in (None, 1):
            raise RuntimeError("Uptodown API reported failure")
        payload = body.get("data", body)
        return payload["downloadURL"]
    except KeyError as exc:
        raise RuntimeError("Uptodown API response has no download URL") from exc


_UPTODOWN_CDN_HOSTS = (
    "dw1.uptodown.com",
    "dw8.uptodown.com",
    "dw10.uptodown.com",
    "dw19.uptodown.com",
    "dw23.uptodown.com",
    "dw44.uptodown.com",
    "dw58.uptodown.com",
    "dw60.uptodown.com",
    "dw63.uptodown.com",
    "dw69.uptodown.com",
    "dw85.uptodown.com",
    "dw92.uptodown.com",
    "dw106.uptodown.com",
)


def _http_status(exc):
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        return int(status)
    code = getattr(exc, "code", None)
    if code:
        try:
            return int(code)
        except (TypeError, ValueError):
            pass
    text = str(exc)
    for code in (526, 410):
        if str(code) in text:
            return code
    return None


def _download_uptodown_cdn(url, output):
    """Download a CDN asset, falling back to known Uptodown CDN aliases."""
    try:
        download_url(
            url,
            output,
            headers={"User-Agent": _UPTODOWN_CDN_UA},
        )
        return
    except Exception as exc:
        if _http_status(exc) not in (526, 410):
            raise

    parsed = urllib.parse.urlsplit(url)
    if parsed.hostname != "dw.uptodown.com":
        raise

    last_exc = None
    for host in _UPTODOWN_CDN_HOSTS:
        candidate = urllib.parse.urlunsplit(
            (parsed.scheme, host, parsed.path, parsed.query, parsed.fragment)
        )
        try:
            download_url(
                candidate,
                output,
                headers={"User-Agent": _UPTODOWN_CDN_UA},
            )
            return
        except Exception as exc:
            if _http_status(exc) not in (526, 410):
                raise
            last_exc = exc

    if last_exc:
        raise last_exc
    raise RuntimeError("No Uptodown CDN fallback hosts configured")

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

    entries = data.get("datalist", {}).get("list")
    if entries is None:
        entries = data.get("list", [])
    vercode = next(
        (x["file"]["vercode"] for x in entries
         if x.get("file", {}).get("vername") == version), None)
    if not vercode:
        raise RuntimeError(f"Aptoide version {version} not found")

    with http_get(f"{base}getAppMeta?package_name={package}&vercode={vercode}{q}") as r:
        data = json.loads(r.read())
    try:
        return data["data"]["file"]["path"]
    except KeyError as exc:
        raise RuntimeError("Aptoide metadata response has no download path") from exc


def download_apkmirror(cfg, arch, version, output):
    from apkmirror import get_apkmirror_apk
    entry = resolve_arch_entry(cfg["source"], arch)
    return get_apkmirror_apk(
        entry["variant_url"], output, entry["slug_filter"],
        entry["version_slug"], exact_version=version,
    )


def _uptodown_scrape_link(name, version):
    """Resolve an exact Uptodown version using its current version API + HTML."""
    from bs4 import BeautifulSoup

    base = f"https://{name}.en.uptodown.com/android"
    app_html = http_read(base)
    soup = BeautifulSoup(app_html, "html.parser")
    app = soup.find(id="detail-app-name")
    app_id = app.get("data-code") if app else None
    if not app_id:
        raise RuntimeError("Uptodown app ID not found")

    version_url = None
    for page in range(1, 21):
        endpoint = f"{base}/apps/{app_id}/versions/{page}"
        data = json.loads(http_read(endpoint))
        for item in data.get("data", []):
            if item.get("version") != version:
                continue
            info = item.get("versionURL")
            if not isinstance(info, dict):
                raise RuntimeError(f"Uptodown version {version} has no version URL")
            parts = [info.get("url"), info.get("extraURL"), info.get("versionID")]
            if not all(part is not None and str(part) for part in parts):
                raise RuntimeError(f"Uptodown version {version} has incomplete version URL")
            version_url = "/".join(str(part).strip("/") for part in parts)
            break
        if version_url:
            break
    if not version_url:
        raise RuntimeError(f"Uptodown HTML version {version} not found")

    page = http_read(version_url)
    page_version = _uptodown_page_version(page)
    if page_version and page_version != version:
        raise RuntimeError(f"Uptodown HTML resolved {page_version}, expected {version}")
    return _uptodown_download_url(page)


def _download_uptodown(name, version, output):
    url = _uptodown_scrape_link(name, version)
    download_url(
        url,
        output,
        headers={
            "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 14; SM-G955F Build/AP2A.240805.005)"
        },
    )

def download_from_mirror(kind, cfg, arch, version, output):
    package = cfg["package"]
    mirror = next((m for m in cfg["source"].get("mirrors", []) if m["type"] == kind), {})
    name = mirror.get("name") or cfg.get("display_name", cfg["id"]).lower().replace(" ", "-")
    if kind == "apkmirror":
        return download_apkmirror(cfg, arch, version, output)
    if kind == "apkpure":
        download_url(apkpure_link(package, name, version), output)
    elif kind == "uptodown":
        # Prefer Uptodown's eAPI: it returns an exact fileID and an official
        # CDN URL without depending on the web page's download token markup.
        _download_uptodown_cdn(uptodown_link(package, name, version), output)
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
            validate_package(tmp, file_type, expected_version=args.exact_version)
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
