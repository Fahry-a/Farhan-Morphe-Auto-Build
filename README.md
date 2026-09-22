# Morphe Auto Build

[![Build](https://github.com/Fahry-a/Farhan-Morphe-Auto-Build/actions/workflows/build.yml/badge.svg)](https://github.com/Fahry-a/Farhan-Morphe-Auto-Build/actions/workflows/build.yml)
[![Tests](https://github.com/Fahry-a/Farhan-Morphe-Auto-Build/actions/workflows/tests.yml/badge.svg)](https://github.com/Fahry-a/Farhan-Morphe-Auto-Build/actions/workflows/tests.yml)

Configuration-driven GitHub Actions automation for building Morphe-patched Android APKs.

## Supported applications

| Application | Package | Patch repository | Sources | Architectures | Flavors |
| --- | --- | --- | --- | --- | --- |
| Google Photos | com.google.android.apps.photos | Akash-Sriram/morphe-google-photos | APKMirror → APKPure → Uptodown → Aptoide | Universal | mod, original |
| Brave Browser | com.brave.browser | kveld9/kveld-morphe-patches | GitHub | ARM64, ARM32 | Default |
| AudioRelay | com.azefsw.audioconnect | kiraio-moe/Lain-Patches | APKPure → Uptodown → Aptoide | Universal | Default |
| Native Camera | com.rawcam.app | WaggBR/Wagg13Patch_Morphe | APKPure → Uptodown → Aptoide | Universal | Default |
| Pinterest | com.pinterest | browzomje/browzomje-patches | APKPure → Uptodown → Aptoide | Universal | Default |

Brave assets:

~~~text
arm64 → BraveMonoarm64.apk
arm32 → BraveMonoarm.apk
~~~

ARM32 / armeabi-v7a is intentionally a first-class supported target.

## Build pipeline

~~~text
configuration
  → patch-supported version
  → exact package download
  → package + version validation
  → Morphe patch
  → manifest-driven signing
  → monthly release
~~~

The workflow does not accept an arbitrary application-version override. The application version is selected from versions supported by the selected Morphe patch.

Mirror sources can define a package type per mirror. A mirror-level `file_type` overrides the source-level value; if neither is set, `apk` is used. This allows configurations where one mirror provides XAPK while another provides APK for the same application.

## Manual workflow

The build workflow supports:

| Input | Description |
| --- | --- |
| app | One app or all |
| arch | One configured architecture or all |
| direct_apk_url | Manual direct URL for one app/architecture |
| allow_experimental | Allow experimental Morphe targets |
| rebuild | Force a rebuild even when release assets already exist |

## Releases

Builds are published to cumulative monthly GitHub Releases:

~~~text
YYYY-MM
~~~

Updating one app/architecture replaces only its stale assets and release-note section. Other applications and architectures remain intact.

## Development

Python dependencies use uv:

~~~bash
uv pip install --system -r requirements.txt
python -m playwright install chromium --with-deps
~~~

Run tests:

~~~bash
python -m unittest discover -s tests -v
~~~

## Documentation

See the detailed contributor and architecture guide:

- docs/ARCHITECTURE.md — application configuration, architecture handling, source types, version resolution, validation, patching, signing, releases, CI security, testing, and how to add applications.

## Security

The build workflow uses:

- least-privilege GitHub Actions permissions
- SHA-pinned Actions
- externalized signing secrets
- exact package and version validation
- checksum verification for the pinned Morphe CLI
- workflow concurrency
- a separate test workflow

Do not commit APKs, keystores, credentials or other release secrets.

## Repository layout

~~~text
apps/       Application configuration
tools/      Generic build/download/patch/release logic
tests/      Unit and regression tests
docs/       Detailed project documentation
.github/    GitHub Actions workflows and repository automation
~~~
