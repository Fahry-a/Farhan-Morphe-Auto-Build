# Morphe Auto Build — Architecture and Contributor Guide

This document explains how the repository works, how sources are selected, how versions are resolved, how APKs are validated and patched, how releases are maintained, and how to add another application.

## 1. Project model

This repository is a configuration-driven GitHub Actions build system.

The intended flow is:

~~~text
apps/*.json
    ↓
app × architecture matrix
    ↓
resolve Morphe patch + supported APK version
    ↓
download exact base package
    ↓
validate package + exact version
    ↓
run Morphe Desktop
    ↓
manifest.json
    ↓
zipalign
    ↓
apksigner
    ↓
verify
    ↓
monthly YYYY-MM release
~~~

The repository should contain automation and configuration, not copies of upstream Morphe patch definitions.

Core invariants:

- An APK version must be supported by the selected Morphe patch.
- A downloaded package must match the requested version.
- Invalid downloads must never reach Morphe.
- manifest.json is authoritative for patch outputs.
- Rebuilding one app/architecture must not delete unrelated release assets.
- Brave ARM64 and ARM32 remain supported.
- CI security controls must not be weakened to make a build pass.

## 2. Repository layout

~~~text
apps/
  brave.json
  google-photos.json

tools/
  common.py             Shared architecture/package validation
  resolve_version.py    Morphe target selection
  apkmirror.py          APKMirror downloader
  mirror_download.py    APKMirror/APKPure/Uptodown/Aptoide fallback
  github_source.py      GitHub Release downloader
  direct.py             Direct URL downloader
  patch.py              Morphe invocation + manifest
  monthly_release.py    Cumulative monthly release maintenance

tests/
  test_common.py
  test_mirror_download.py
  test_monthly_release.py
  test_resolve_version.py

.github/workflows/
  build.yml             Build/sign/publish
  tests.yml             Unit/regression tests

docs/
  ARCHITECTURE.md       This document

requirements.txt        Python dependency pins
README.md               User-facing overview
~~~

## 3. Application configuration

Every application is normally represented by one file:

~~~text
apps/<id>.json
~~~

The workflow reads the files and automatically creates the build matrix.

A minimal configuration contains:

~~~json
{
  "id": "example",
  "display_name": "Example",
  "package": "com.example.app",
  "patch_repo": "OWNER/example-morphe-patches",
  "allow_experimental": false,
  "source": {},
  "patch_bundle": {
    "type": "prebuilt"
  },
  "flavors": []
}
~~~

Important fields:

| Field | Purpose |
| --- | --- |
| id | Stable application identifier and filename prefix |
| display_name | Human-readable name |
| package | Android package name |
| patch_repo | Morphe patch repository |
| allow_experimental | Permit experimental patch targets |
| source | Base package source |
| patch_bundle | Patch bundle strategy |
| flavors | Output variants |
| enabled | Optional; false excludes the app from the matrix |

Do not add a separate workflow for a normal application.

## 4. Architectures

Architectures are configuration data:

~~~json
"archs": [
  {
    "name": "arm64",
    "asset": "BraveMonoarm64.apk"
  },
  {
    "name": "arm32",
    "asset": "BraveMonoarm.apk"
  }
]
~~~

Multiple entries become multiple GitHub Actions matrix jobs.

### Brave

~~~text
arm64 → arm64-v8a → BraveMonoarm64.apk
arm32 → armeabi-v7a → BraveMonoarm.apk
~~~

ARM32 is a first-class target. Do not remove it when modifying the Brave configuration or workflow.

### Google Photos

Google Photos currently uses a universal source entry covering the configured APKMirror variant.

## 5. Source system

The source is selected with source.type.

Supported types:

~~~text
mirrors
apkmirror
github
direct
~~~

### mirrors

Use this when several sources can provide the same exact version.

Current Google Photos order:

~~~text
APKMirror
    ↓ failure
APKPure
    ↓ failure
Uptodown
    ↓ failure
Aptoide
~~~

Every candidate must be downloaded to a temporary .partial file and validated before it becomes the final package.

### APKMirror

tools/apkmirror.py handles the APKMirror-specific navigation:

~~~text
variant page
  → version/detail page
  → download confirmation
  → final URL
  → package
~~~

It normally uses curl_cffi and can use Playwright Chromium for browser-based Cloudflare handling.

Configuration normally supplies:

- variant_url
- slug_filter
- version_slug
- file_type

The downloader must honor the exact requested version.

### APKPure

APKPure is handled by tools/mirror_download.py.

The downloader resolves the requested application version and extracts its package URL. It must not silently replace the requested version with latest.

### Uptodown

Uptodown is handled by tools/mirror_download.py.

The downloader searches version listings, finds the exact requested version, opens its version page, extracts the download URL, then validates the resulting package.

### Aptoide

Aptoide is accessed through its public API.

The downloader finds the requested vername, obtains the corresponding vercode, requests metadata and downloads the returned package path.

### GitHub

Use:

~~~json
"source": {
  "type": "github",
  "repo": "OWNER/REPOSITORY",
  "tag_prefix": "v",
  "file_type": "apk",
  "archs": [
    {
      "name": "arm64",
      "asset": "ExampleArm64.apk"
    }
  ]
}
~~~

tools/github_source.py resolves the exact release tag and exact asset name using the GitHub API.

This is currently used for Brave.

### Direct

Use a direct source for a known URL pattern:

~~~json
"source": {
  "type": "direct",
  "file_type": "xapk",
  "archs": [
    {
      "name": "universal",
      "url": "https://example.invalid/releases/{version}/{package}-{version}.xapk"
    }
  ]
}
~~~

Supported placeholders:

- {version}
- {package}
- {arch}

tools/direct.py downloads to .partial, validates the package and exact version, then atomically moves it into place.

## 6. Version resolution

tools/resolve_version.py is the authority for selecting the application version.

Conceptually:

~~~text
patch repository
    ↓
prebuilt MPP release
    ↓
patches-list.json
    ↓
matching Android package
    ↓
stable/experimental filtering
    ↓
highest supported target
~~~

The resolver returns the MPP version, MPP URL and supported APK version.

There is deliberately no version_override workflow input.

This prevents a user from forcing an APK version that the selected Morphe patch does not declare as compatible.

Experimental targets are excluded unless enabled through configuration or the manual allow_experimental input.

## 7. Package validation

tools/common.py provides shared validation.

Validation checks:

1. Minimum file size.
2. Valid ZIP structure.
3. APK contains AndroidManifest.xml.
4. APKM/XAPK contains APK entries.
5. Exact version can be read with aapt.
6. Detected version equals the requested resolver version.

For a bundle, base.apk is preferred; otherwise the first APK entry is checked.

This is important because HTTP 200 does not mean "valid APK". A server can return HTML, Cloudflare content, a partial download or the wrong release with status 200.

Validation must happen before Morphe.

## 8. Patching and manifest

tools/patch.py invokes Morphe Desktop once for every configured flavor.

A flavor contains:

~~~json
{
  "name": "special",
  "description": "Special patch combination",
  "patch_include": [
    "Patch A",
    "Patch B"
  ],
  "output_suffix": "-special"
}
~~~

Morphe produces unsigned APKs.

tools/patch.py writes manifest.json containing the files actually produced.

Later stages use the manifest rather than reconstructing filenames.

This is important because the patcher, not the workflow's assumptions, is the authority on which files exist.

## 9. Signing

The final order is:

~~~text
Morphe unsigned APK
    ↓
zipalign
    ↓
apksigner
    ↓
apksigner verify
    ↓
final APK
~~~

The workflow must not rewrite the APK ZIP after zipalign. Repacking after alignment can invalidate the alignment.

The release keystore comes from GitHub Actions secrets and is deleted after signing.

## 10. Morphe CLI integrity

The workflow pins the Morphe Desktop version:

~~~text
MORPHE_CLI_VERSION=1.16.0
~~~

The exact JAR is SHA-256 verified before it is executed.

When upgrading Morphe:

1. Choose the intended official release.
2. Obtain the checksum for the exact JAR.
3. Update version and checksum together.
4. Run tests.
5. Review the workflow diff.
6. Verify the resulting CI run.

Do not remove checksum validation as a workaround.

## 11. Monthly release system

Release tags use:

~~~text
YYYY-MM
~~~

tools/monthly_release.py updates one app/architecture at a time.

For a new build it:

1. Uploads current APKs with --clobber.
2. Finds stale assets using the current app + architecture prefix.
3. Deletes only those stale assets.
4. Replaces only the marked release-note section for that app/architecture.
5. Preserves unrelated assets and release-note sections.

Example:

~~~text
Brave ARM64 rebuilt
    ↓
old brave-arm64 assets may be removed

Brave ARM32
Google Photos
other applications
    ↓
must remain
~~~

## 12. Existing-release optimization

Before downloading or patching, the workflow calculates the expected output names from the application configuration and resolved version.

If every expected asset already exists in the current monthly release, that matrix job is skipped.

Use:

~~~text
rebuild=true
~~~

when a manual rebuild is required.

This optimization exists to avoid expensive downloads and patch operations.

## 13. Adding a new application

### Step 1: Verify Morphe support

Confirm:

- patch repository
- prebuilt MPP release
- patches-list.json
- Android package name
- supported application versions

Do not guess compatibility.

### Step 2: Choose a source

Prefer an existing generic source type.

Use:

- github for GitHub release assets
- mirrors for fallback mirrors
- apkmirror for APKMirror-specific configuration
- direct for a known URL pattern

Only add downloader code when the existing generic sources cannot represent the source.

### Step 3: Create apps/<id>.json

Example:

~~~json
{
  "id": "example",
  "display_name": "Example",
  "package": "com.example.app",
  "patch_repo": "OWNER/example-morphe-patches",
  "allow_experimental": false,
  "source": {
    "type": "github",
    "repo": "OWNER/example",
    "tag_prefix": "v",
    "file_type": "apk",
    "archs": [
      {
        "name": "arm64",
        "asset": "ExampleArm64.apk"
      }
    ]
  },
  "patch_bundle": {
    "type": "prebuilt"
  },
  "flavors": [
    {
      "name": "default",
      "description": "Default patches",
      "patch_include": [],
      "output_suffix": ""
    }
  ]
}
~~~

### Step 4: Test version resolution

~~~bash
python3 tools/resolve_version.py   --config apps/example.json
~~~

### Step 5: Test the downloader

GitHub:

~~~bash
python3 tools/github_source.py   --config apps/example.json   --arch arm64   --apk-version <version>   --output base.apk
~~~

Mirrors:

~~~bash
python3 tools/mirror_download.py   --config apps/example.json   --arch universal   --exact-version <version>   --output base.apk
~~~

### Step 6: Add regression tests

Test exact-version behavior and failure cases for any new downloader behavior.

### Step 7: Run the complete test suite

~~~bash
python -m unittest discover -s tests -v
~~~

### Step 8: Update README

Add the application, source order, architectures and flavors to the supported-app documentation.

## 14. Adding a flavor

Normally this requires only configuration:

~~~json
{
  "name": "special",
  "description": "Special patches",
  "patch_include": [
    "Patch A"
  ],
  "output_suffix": "-special"
}
~~~

The generic workflow automatically patches, signs and publishes it.

## 15. Adding a mirror

A new mirror implementation must:

1. Resolve the exact requested version.
2. Return a package URL.
3. Download to .partial.
4. Validate package shape.
5. Validate exact version.
6. Remove partial files after failure.
7. Have regression tests.
8. Be added to configuration in the intended fallback order.
9. Be documented.

Never use "latest" as an implicit fallback for an exact-version request.

## 16. Local development with uv

Python dependencies are installed with uv.

~~~bash
uv pip install --system -r requirements.txt
~~~

Install Playwright Chromium:

~~~bash
python -m playwright install chromium --with-deps
~~~

Run tests:

~~~bash
python -m unittest discover -s tests -v
~~~

CI uses the SHA-pinned astral-sh/setup-uv action.

## 17. Local build requirements

A complete build environment needs:

- Python 3.12+
- uv
- Java 21+
- Android build tools containing aapt, zipalign and apksigner
- Chromium for the Playwright fallback
- Morphe Desktop JAR
- compatible Morphe MPP
- GitHub CLI for release maintenance

Signing secrets are only expected in GitHub Actions.

## 18. Testing strategy

Tests cover:

- architecture selection
- multi-architecture requirements
- APK shape validation
- APKM/XAPK shape validation
- exact APK version validation
- exact bundle version validation
- patch target selection
- experimental target selection
- Aptoide exact-version lookup
- stale release assets
- cumulative release-note sections

Run:

~~~bash
python -m unittest discover -s tests -v
~~~

When fixing a real bug, add a regression test before considering the fix complete.

## 19. CI security

Preserve:

- contents: read by default
- write permission only in the publish job
- SHA-pinned Actions
- signing secrets outside the repository
- package validation
- exact-version validation
- Morphe CLI checksum verification
- workflow concurrency
- separate test workflow

Do not fix CI by disabling validation, exposing secrets, removing tests or using unverified critical binaries.

## 20. Common failure modes

### HTTP 200 but Morphe fails

Check whether the downloaded file is HTML, a Cloudflare response, a partial file, the wrong package or the wrong version.

### Wrong application version

Fix source resolution. Do not accept the wrong version.

### Unsupported patch target

The selected APK version is not declared compatible by the patch. Do not invent the pairing.

### Brave ARM32 missing

Check apps/brave.json for:

~~~json
{
  "name": "arm32",
  "asset": "BraveMonoarm.apk"
}
~~~

Then confirm the matrix includes arm32.

### Release deletes another application

Check tools/monthly_release.py. Stale deletion must be limited to the current app and architecture.

### APK is not aligned

The final sequence must remain:

~~~text
zipalign → apksigner → apksigner verify
~~~

No ZIP rewrite belongs after zipalign.

## 21. Change checklist

Before opening a PR:

- Inspect current GitHub state.
- Prefer configuration over duplicated workflow code.
- Preserve patch/application compatibility.
- Preserve exact package validation.
- Preserve Brave ARM64 and ARM32.
- Keep manifest.json authoritative.
- Preserve unrelated monthly release assets.
- Keep Actions SHA-pinned.
- Keep permissions least-privilege.
- Do not commit secrets.
- Add regression tests for meaningful bugs.
- Run the full test suite.
- Update documentation for behavior changes.
- Review CI impact.

## 22. Current supported applications

### Google Photos

~~~text
Package:       com.google.android.apps.photos
Patch repo:    Akash-Sriram/morphe-google-photos
Sources:       APKMirror → APKPure → Uptodown → Aptoide
Architecture:  universal
Flavors:       mod, original
~~~

### Brave Browser

~~~text
Package:       com.brave.browser
Patch repo:    kveld9/kveld-morphe-patches
Source:        brave/brave-browser GitHub releases
Architectures: arm64, arm32

arm64 → BraveMonoarm64.apk
arm32 → BraveMonoarm.apk
~~~

## 23. Guiding principle

Keep this repository a thin automation layer around upstream Morphe metadata and application sources.

Prefer:

~~~text
new application configuration
+
generic source behavior
+
regression test
~~~

over:

~~~text
new workflow
+
copied patch definitions
+
application-specific assumptions
~~~
