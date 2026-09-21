# Morphe Auto Build

Daily automated builds of Morphe-patched APKs for Google Photos and Brave via GitHub Actions.

The APK version **follows the version supported by the patch** (`patches-list.json`), not the latest on APKMirror. Each app config can hold **multiple archs**, and all outputs land in **one release per month** (`2026-09`).

## Supported apps

| App | Patch repo | APK source | Archs | Flavors |
| --- | --- | --- | --- | --- |
| `google-photos` | `Akash-Sriram/morphe-google-photos` | APKMirror `google-inc/photos` (`nodpi`, exact version) | `universal` | `-mod`, `-original` |
| `brave` | `kveld9/kveld-morphe-patches` | GitHub `brave/brave-browser` | `arm64` (`BraveMonoarm64.apk`), `arm32` (`BraveMonoarm.apk`) | default |

Release tags are months: `2026-09`, `2026-10`, ... Each release holds the latest build per app+arch, e.g. `brave-arm64-v1.95.104.apk`, `google-photos-universal-v7.92.0.977185651-mod.apk`. Old versions are pruned.

## How it works

1. `setup` expands every `apps/*.json` into `(app, arch)` matrix pairs.
2. `tools/resolve_version.py` (stdlib only): reads `patches-bundle.json` -> `.mpp` version, then `patches-list.json` at tag `v<mpp>` -> filters by `packageName` -> picks the highest stable target (`allow_experimental: true` opts into experimental targets).
3. Each pair checks the current month release: skip when all expected assets already exist.
4. Downloads the prebuilt `.mpp` and the exact-version base package:
   - APKMirror: `tools/apkmirror.py --arch ...` (`apk` or `apkm` bundle) — fast path `curl_cffi` with Chrome impersonation, Playwright headless Chromium fallback for the Cloudflare JS challenge.
   - APKPure: `tools/apkpure.py --arch ...` (`apk` or `xapk`) — versions page → version download page → direct file link, same dual-path strategy. Tries `apkpure.com` then `apkpure.net`.
   - Uptodown: `tools/uptodown.py --arch ...` (`apk` or `xapk`) — app page (canonical slug auto-followed, locale hosts as fallback) → versions JSON → variant catalog → `-x` page; static `dw.uptodown.com` link when present, otherwise Playwright clicks the real download button.
   - GitHub: `tools/github_source.py --arch ...` — resolves `browser_download_url` via the API.
5. Patches with the `MorpheApp/morphe-desktop` CLI via `tools/patch.py --arch ...`, which writes `manifest.json` listing every file actually produced. The Sign step reads the manifest and never reconstructs filenames by hand.
6. Signs with `zipalign` + zip repack fix + `apksigner` using one shared keystore (`KEYSTORE_BASE64`).
7. The single `publish` job merges all built bundles and runs `tools/monthly_release.py`: uploads with `--clobber`, deletes stale same-app assets, and rewrites that app's section in the release notes. Apps already up to date keep their existing sections.

Schedule: `0 2 * * *` (09:00 WIB daily). Manual runs via `workflow_dispatch` (`app`, `arch`, `version_override`, `direct_apk_url`, `allow_experimental`, `rebuild`).

## Setup secrets

* `KEYSTORE_BASE64` (required): base64 of the keystore, single line.
* `KEYSTORE_PASSWORD` (optional, defaults to `android`): keystore password.
* `KEY_PASSWORD` (optional, defaults to `android`): key password.
* `KEY_ALIAS` (optional): key alias. Leave empty when the keystore holds a single key.

Create a new keystore (replace `PASS` with any password you like, it does not have to be `android`):

```bash
keytool -genkeypair -v -keystore release.keystore \
  -alias morphe -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass 'PASS' -keypass 'PASS' \
  -dname "CN=Morphe Auto Build, OU=CI, O=Personal, C=ID"
base64 -w0 release.keystore > keystore.b64
# Do NOT commit release.keystore / keystore.b64
```

Set the secrets (via `gh` or Settings -> Secrets -> Actions):

```bash
gh secret set KEYSTORE_BASE64 < keystore.b64
gh secret set KEYSTORE_PASSWORD --body 'PASS'
gh secret set KEY_PASSWORD --body 'PASS'
gh secret set KEY_ALIAS --body 'morphe'
```

Verify a signature after a build:

```bash
apksigner verify --print-certs output.apk
```

## Adding a new app

Add one `apps/<id>.json` file — no code or workflow changes needed. The `id` is only used for matrix naming and filenames; tools read everything else from the file:

```json
{
  "id": "brave",
  "display_name": "Brave Browser",
  "package": "com.brave.browser",
  "patch_repo": "kveld9/kveld-morphe-patches",
  "allow_experimental": false,
  "source": {
    "type": "github",
    "repo": "brave/brave-browser",
    "tag_prefix": "v",
    "file_type": "apk",
    "archs": [
      {"name": "arm64", "asset": "BraveMonoarm64.apk"},
      {"name": "arm32", "asset": "BraveMonoarm.apk"}
    ]
  },
  "patch_bundle": { "type": "prebuilt" },
  "flavors": [{ "name": "default", "patch_include": [], "output_suffix": "" }]
}
```

For an APKMirror source, each arch carries its own variant filter.
`file_type` is `apk` for a monolithic APK or `apkm` for an APK bundle
(APKMirror labels these BUNDLE — the downloader saves the real `.apkm`
extension and validates the contents, so a bundle can never silently reach
the patcher as `base.apk`):

```json
  "source": {
    "type": "apkmirror",
    "file_type": "apkm",
    "archs": [
      {"name": "universal", "variant_url": "https://www.apkmirror.com/apk/.../",
       "slug_filter": "/apk/.../", "version_slug": "some-app-"}
    ]
  }
```

Every download is validated before patching: APKs must open in `aapt`
with the expected version, bundles must contain APK entries. A bad file
fails the run with a clear message instead of a cryptic patcher NPE.

For an APKPure source, each arch only needs the page slug (the package
comes from the app level). The downloader finds the exact version on the
versions listing, follows its download page to the direct file link, and
detects `apk` vs `xapk` from the URL (`/b/APK/` vs `/b/XAPK/`):

```json
  "source": {
    "type": "apkpure",
    "file_type": "xapk",
    "archs": [
      {"name": "universal", "page_slug": "block-blast"}
    ]
  }
```

Both `apkpure.com` and `apkpure.net` are tried automatically (configurable
per arch with `base_url`) if one domain blocks the CI network.

For a Uptodown source, each arch only needs the page slug (canonical slugs
are followed automatically when Uptodown redirects an alias):

```json
  "source": {
    "type": "uptodown",
    "file_type": "xapk",
    "archs": [
      {"name": "universal", "page_slug": "block-blast"}
    ]
  }
```

For other/new patches: point `patch_repo` at another Morphe patch repo and adjust `package`. No workflow fork needed.

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# 1. Resolve
python3 tools/resolve_version.py --config apps/google-photos.json
python3 tools/resolve_version.py --config apps/brave.json

# 2. Download (Photos needs a Cloudflare-capable network)
python3 tools/apkmirror.py --config apps/google-photos.json --arch universal --exact-version 7.92.0.977185651 --output base.apk
python3 tools/apkpure.py --config apps/block-blast.json --arch universal --exact-version 10.4.5 --output base.xapk
python3 tools/github_source.py --config apps/brave.json --arch arm64 --apk-version 1.95.104 --output base.apk

# 3. Patch (needs the morphe-desktop jar + Java 21)
python3 tools/patch.py --config apps/brave.json --arch arm64 --cli morphe-cli.jar --mpp patches.mpp --base base.apk
```

The original Photos-only prototype (`cuma-contoh.py`, since removed) was refactored into the modular `tools/apkmirror.py`.
