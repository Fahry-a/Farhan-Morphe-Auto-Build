#!/usr/bin/env python3
"""Maintain the monthly cumulative release (tag YYYY-MM).

Input: builds.json describing freshly built (app, arch) bundles:
  {"builds": [{"app": "brave", "display": "Brave Browser", "arch": "arm64",
               "apk_version": "1.95.104", "mpp_version": "1.42.2",
               "build_date": "2026-09-22",
               "files": ["brave-arm64-v1.95.104.apk"]}]}

For each build this script:
  1. Uploads the files with `gh release upload TAG --clobber`.
  2. Deletes stale assets (same app+arch, any other version).
  3. Replaces that app+arch section in the release notes (other sections kept).

Creates the month release when it does not exist yet.
"""
import argparse
import json
import os
import subprocess
import sys


def section_key(app, arch):
    return f"{app}:{arch}"


def render_section(build):
    key = section_key(build["app"], build["arch"])
    files = "\n".join(f"- `{f}`" for f in build["files"])
    return (
        f"<!-- app:{key} -->\n"
        f"### {build['app']} `{build['arch']}`\n"
        f"{build['app']} ({build['arch']}) "
        f"v{build['apk_version']} "
        f"(build {build['build_date']}, mpp {build['mpp_version']})\n"
        f"{files}\n"
        f"<!-- /app:{key} -->"
    )


def upsert_section(body, key, section):
    """Replace the marked section for key, or append it when missing."""
    start = f"<!-- app:{key} -->"
    end = f"<!-- /app:{key} -->"
    if start in body and end in body:
        pre, rest = body.split(start, 1)
        _, post = rest.split(end, 1)
        return f"{pre}{section}{post}"
    body = body.rstrip() + "\n\n" if body.strip() else ""
    return body + section + "\n"


def stale_assets(existing_names, app, arch, keep_files):
    """Assets of the same app+arch that are not part of the current build."""
    keep = set(keep_files)
    prefix = f"{app}-{arch}-v"
    return sorted(n for n in existing_names
                  if n.startswith(prefix) and n not in keep)


def header(tag):
    return (f"# Monthly builds {tag}\n\n"
            "Updated in place by the daily workflow. "
            "Each section shows the current version per app and arch.\n")


def gh(*args, repo):
    cmd = ["gh", *args, "--repo", repo]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip()}")
    return proc.stdout


def get_release(repo, tag):
    """Return the release or None only when it genuinely does not exist."""
    cmd = ["gh", "release", "view", tag, "--json", "assets,body", "--repo", repo]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode == 0:
        return json.loads(proc.stdout)
    error = proc.stderr.strip()
    if "release not found" in error.lower() or "not found" in error.lower():
        return None
    raise RuntimeError(f"{' '.join(cmd)} failed: {error}")


def main():
    parser = argparse.ArgumentParser(description="Update the monthly cumulative release")
    parser.add_argument("--tag", required=True, help="Month tag, e.g. 2026-09")
    parser.add_argument("--builds", required=True, help="builds.json from the publish job")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""),
                        help="owner/repo (defaults to GITHUB_REPOSITORY)")
    args = parser.parse_args()
    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")

    with open(args.builds) as fh:
        builds = json.load(fh).get("builds", [])
    if not builds:
        print("No builds to publish.")
        return

    release = get_release(args.repo, args.tag)
    if release is None:
        print(f"Creating release {args.tag}")
        gh("release", "create", args.tag, "--title", args.tag,
           "--notes", header(args.tag), repo=args.repo)
        release = {"assets": [], "body": header(args.tag)}

    existing = [a["name"] for a in release.get("assets", [])]
    body = release.get("body") or header(args.tag)

    for build in builds:
        key = section_key(build["app"], build["arch"])
        print(f"== Publishing {key} v{build['apk_version']}")
        subprocess.run(
            ["gh", "release", "upload", args.tag, *build["files"],
             "--clobber", "--repo", args.repo], check=True)
        for stale in stale_assets(existing, build["app"], build["arch"], build["files"]):
            print(f"Deleting stale asset: {stale}")
            subprocess.run(
                ["gh", "release", "delete-asset", args.tag, stale,
                 "--yes", "--repo", args.repo], check=True)
        existing = [n for n in existing if n not in stale_assets(
            existing, build["app"], build["arch"], build["files"])]
        existing += [f for f in build["files"] if f not in existing]
        body = upsert_section(body, key, render_section(build))

    with open("release-body.md", "w") as fh:
        fh.write(body)
    subprocess.run(
        ["gh", "release", "edit", args.tag, "--notes-file", "release-body.md",
         "--repo", args.repo], check=True)

    # Verify the remote state after every mutation. Do not report success unless
    # the expected assets and release-note sections are actually present.
    verified = get_release(args.repo, args.tag)
    if verified is None:
        raise RuntimeError(f"Release {args.tag} disappeared during publication")
    verified_assets = {asset["name"] for asset in verified.get("assets", [])}
    verified_body = verified.get("body") or ""
    missing_assets = sorted(
        file_name
        for build in builds
        for file_name in build["files"]
        if file_name not in verified_assets
    )
    if missing_assets:
        raise RuntimeError(
            "Release verification failed; missing uploaded assets: "
            + ", ".join(missing_assets)
        )
    missing_sections = [
        section_key(build["app"], build["arch"])
        for build in builds
        if f"<!-- app:{section_key(build['app'], build['arch'])} -->" not in verified_body
        or f"<!-- /app:{section_key(build['app'], build['arch'])} -->" not in verified_body
    ]
    if missing_sections:
        raise RuntimeError(
            "Release verification failed; missing release-note sections: "
            + ", ".join(missing_sections)
        )

    print(f"Release {args.tag} updated and verified with {len(builds)} app(s).")


if __name__ == "__main__":
    main()
