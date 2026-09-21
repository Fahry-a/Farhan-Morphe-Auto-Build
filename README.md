# Morphe Auto Build

Daily automated builds of Morphe-patched APKs for Google Photos and Brave via GitHub Actions.

The APK version **follows the version supported by the patch** (`patches-list.json`), not the latest on APKMirror.

## Supported apps

| App | Patch repo | APK source | Flavors |
| --- | --- | --- | --- |
| `google-photos` | `Akash-Sriram/morphe-google-photos` | APKMirror `google-inc/photos` (`nodpi`, exact version) | `-mod`, `-original` |
| `brave` | `kveld9/kveld-morphe-patches` | GitHub `brave/brave-browser` asset `BraveMonoarm64.apk` | default |

Release tags: `photos-v<apk_ver>`, `brave-v<apk_ver>`. Example files: `google-photos-v7.92.0.977185651-mod.apk`, `brave-v1.95.104.apk`.

## How it works

1. `tools/resolve_version.py` (stdlib only): reads `patches-bundle.json` -> `.mpp` version, then `patches-list.json` at tag `v<mpp>` -> filters by `packageName` -> picks the highest stable target (`allow_experimental: true` opts into experimental targets).
2. Downloads the prebuilt `.mpp` from `download_url`.
3. Downloads the exact-version base APK:
   - APKMirror: `tools/apkmirror.py` — fast path `curl_cffi` with Chrome impersonation, Playwright headless Chromium fallback for the Cloudflare JS challenge.
   - GitHub: `tools/github_source.py` — resolves `browser_download_url` via the API.
4. Patches with the `MorpheApp/morphe-desktop` CLI: `java -jar morphe-cli.jar patch -p patches.mpp [-e ...] --unsigned`.
5. Signs with `zipalign` + zip repack fix + `apksigner` using one shared keystore (`KEYSTORE_BASE64`).
6. Skips automatically when the release tag already exists (unless `rebuild: true`).

Schedule: `0 2 * * *` (09:00 WIB daily). Manual runs via `workflow_dispatch`.

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

Just add one `apps/<id>.json` file — no code or workflow changes needed. Minimal example:

```json
{
  "id": "tiktok",
  "display_name": "TikTok",
  "package": "com.zhiliaoapp.musically",
  "patch_repo": "kveld9/kveld-morphe-patches",
  "allow_experimental": false,
  "release_tag_prefix": "tiktok-",
  "source": {
    "type": "apkmirror",
    "variant_url": "https://www.apkmirror.com/apk/.../",
    "slug_filter": "/apk/tiktok-pte-ltd/.../",
    "version_slug": "tiktok-",
    "file_type": "apk"
  },
  "patch_bundle": { "type": "prebuilt" },
  "flavors": [{ "name": "default", "patch_include": [], "output_suffix": "" }]
}
```

For a GitHub source (like Brave):

```json
  "source": {
    "type": "github",
    "repo": "owner/repo",
    "asset": "app-arm64.apk",
    "tag_prefix": "v",
    "file_type": "apk"
  }
```

The workflow matrix reads every `apps/*.json` automatically. Fields used by the workflow: `id`, `package`, `patch_repo`, `allow_experimental`, `release_tag_prefix`, `source.*`, `flavors[].{name,patch_include,output_suffix}`.

For other/new patches: point `patch_repo` at another Morphe patch repo and adjust `package`. No workflow fork needed.

## Running locally

```bash
pip install -r requirements.txt
playwright install chromium --with-deps

# 1. Resolve
python3 tools/resolve_version.py --config apps/google-photos.json
python3 tools/resolve_version.py --config apps/brave.json

# 2. Download (Photos needs a Cloudflare-capable network)
python3 tools/apkmirror.py --config apps/google-photos.json --exact-version 7.92.0.977185651 --output base.apk
python3 tools/github_source.py --config apps/brave.json --apk-version 1.95.104 --output base.apk

# 3. Patch (needs the morphe-desktop jar + Java 21)
java -jar morphe-cli.jar patch -p patches.mpp --unsigned -o out.apk base.apk
# or every flavor at once:
python3 tools/patch.py --config apps/brave.json --cli morphe-cli.jar --mpp patches.mpp --base base.apk
```

`cuma-contoh.py` is the original Photos-only prototype; its modular replacement is `tools/apkmirror.py`.
