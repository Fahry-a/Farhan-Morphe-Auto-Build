#!/usr/bin/env python3
"""Run the morphe-desktop CLI for every flavor in apps/<id>.json.

Generic so adding a new app never requires workflow changes:
  python tools/patch.py --config apps/brave.json \
    --cli morphe-cli.jar --base base.apk --workdir out/

Each flavor runs:
  java -jar <cli> patch -p <mpp> [-e "Patch A" -e "Patch B"...] --unsigned -o <out> <base>
"""
import argparse
import json
import os
import subprocess
import sys


def run_patch(cli_jar, mpp_path, base_apk, output_apk, patch_include):
    cmd = ["java", "-jar", cli_jar, "patch", "-p", mpp_path]
    for name in patch_include or []:
        cmd += ["-e", name]
    cmd += ["--unsigned", "-o", output_apk, base_apk]
    print("+ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout[-4000:] if len(proc.stdout) > 4000 else proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr[-4000:] if len(proc.stderr) > 4000 else proc.stderr, file=sys.stderr)
        raise RuntimeError(f"Patch failed for {output_apk} (exit {proc.returncode})")
    if not os.path.exists(output_apk) or os.path.getsize(output_apk) < 1_000_000:
        raise RuntimeError(f"Patch output missing/too small: {output_apk}")
    print(f"OK {output_apk} ({os.path.getsize(output_apk):,} bytes)")


def main():
    parser = argparse.ArgumentParser(description="Patch every flavor of one app")
    parser.add_argument("--config", required=True, help="Path to apps/<id>.json")
    parser.add_argument("--cli", required=True, help="Path to morphe-desktop-*-all.jar")
    parser.add_argument("--mpp", required=True, help="Path to the prebuilt patches-*.mpp")
    parser.add_argument("--base", required=True, help="Unpatched base APK")
    parser.add_argument("--workdir", default=".", help="Output directory for unsigned files")
    args = parser.parse_args()

    with open(args.config) as fh:
        cfg = json.load(fh)
    app_id = cfg["id"]
    outputs = []
    for flavor in cfg.get("flavors", []):
        out = os.path.join(args.workdir, f"{app_id}-{flavor['name']}-unsigned.apk")
        run_patch(args.cli, args.mpp, args.base, out, flavor.get("patch_include", []))
        outputs.append({"flavor": flavor["name"], "file": out,
                        "suffix": flavor.get("output_suffix", "")})
    print(json.dumps(outputs, indent=2))
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
            fh.write(f"patch_outputs={json.dumps(outputs)}\n")


if __name__ == "__main__":
    main()
