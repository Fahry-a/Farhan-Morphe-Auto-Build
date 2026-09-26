import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.mirror_probe import (
    MirrorTarget,
    discover_targets,
    run_audit,
)


class MirrorProbeTests(unittest.TestCase):
    def test_discovery_covers_every_enabled_mirror_app(self):
        targets = discover_targets("apps")

        # Derive the expectation from apps/*.json instead of hardcoding it. A
        # snapshot of the app set and target count goes stale the moment a
        # config is added, and then the test fails for the wrong reason.
        # Targets are archs x mirrors: most apps are a single implicit
        # universal arch, while an app whose mirrors publish no universal
        # package declares explicit per-ABI archs.
        from tools.common import source_architecture_entries

        expected: dict[str, int] = {}
        expected_archs: dict[str, list[str]] = {}
        for path in sorted(Path("apps").glob("*.json")):
            with open(path, encoding="utf-8") as handle:
                config = json.load(handle)
            if not config.get("enabled", True):
                continue
            source = config.get("source") or {}
            if source.get("type") != "mirrors":
                continue
            archs = [e["name"] for e in source_architecture_entries(source)]
            live = [m for m in source.get("mirrors") or [] if m.get("enabled", True)]
            expected[config["id"]] = expected.get(config["id"], 0) + len(archs) * len(live)
            expected_archs[config["id"]] = archs

        actual: dict[str, int] = {}
        for target in targets:
            actual[target.app] = actual.get(target.app, 0) + 1

        self.assertEqual(actual, expected)
        self.assertTrue(expected, "expected at least one mirror-backed app config")
        self.assertEqual(len(targets), sum(expected.values()))
        for target in targets:
            self.assertIn(target.arch, expected_archs[target.app])
        for path in {target.config for target in targets}:
            with open(path, encoding="utf-8") as handle:
                config = json.load(handle)
            # Mirror-backed configs either take the implicit universal
            # contract (no archs key) or declare explicit per-ABI archs;
            # universal must never be mixed with concrete ABIs.
            archs = (config["source"] or {}).get("archs")
            if archs is not None:
                names = [
                    (e if isinstance(e, str) else e.get("name", "")).lower().replace("_", "-")
                    for e in archs
                ]
                self.assertNotIn("universal", names)
        # A github-sourced app is not a mirror target.
        self.assertNotIn("brave", {target.app for target in targets})
        pinterest_apkpure = next(
            target for target in targets
            if target.app == "pinterest" and target.mirror == "apkpure"
        )
        self.assertEqual(pinterest_apkpure.file_type, "xapk")
        self.assertEqual(
            [(t.mirror, t.arch) for t in targets if t.app == "native-camera"],
            [("apkpure", "arm64"), ("apkpure", "arm32")],
        )

    def test_discovery_filters_are_case_insensitive_and_preserve_config_order(self):
        targets = discover_targets("apps", app_filter="ADM", mirror_filter="apkpure,apkcombo")
        self.assertEqual([target.mirror for target in targets], ["apkpure", "apkcombo"])
        self.assertTrue(all(target.app == "adm" for target in targets))

    def test_discovery_honours_disabled_mirror_entry(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "example.json"
            path.write_text(
                '{"id":"example","package":"com.example",'
                '"source":{"type":"mirrors","file_type":"apk","mirrors":['
                '{"type":"apkpure"},{"type":"apkcombo","enabled":false}]}}\n'
            )
            targets = discover_targets(td)
        self.assertEqual([target.mirror for target in targets], ["apkpure"])

    def test_run_audit_downloads_each_target_and_reports_version(self):
        target = MirrorTarget(
            config="apps/audiorelay.json",
            app="audiorelay",
            package="com.azefsw.audioconnect",
            arch="universal",
            mirror="apkpure",
            file_type="apk",
        )

        def fake_downloader(kind, config, arch, version, output, **kwargs):
            del kind, config, arch, kwargs
            Path(output).write_bytes(b"downloaded package")
            return version

        with tempfile.TemporaryDirectory() as td:
            with patch(
                "tools.mirror_probe.validate_package", return_value=7
            ) as validate:
                results, kept_dir = run_audit(
                    [target], download_dir=td,
                    resolver=lambda config, experimental: {"apk_version": "0.26.1"},
                    downloader=fake_downloader,
                )
            self.assertEqual(kept_dir, td)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].status, "pass")
            self.assertEqual(results[0].version, "0.26.1")
            self.assertEqual(results[0].entries, 7)
            self.assertEqual(validate.call_args.kwargs["expected_arch"], "universal")

    def test_run_audit_keeps_one_failure_from_hiding_other_targets(self):
        targets = [
            MirrorTarget("apps/audiorelay.json", "audiorelay", "pkg", "universal", "apkpure", "apk"),
            MirrorTarget("apps/audiorelay.json", "audiorelay", "pkg", "universal", "apkcombo", "apk"),
        ]

        def fake_downloader(kind, config, arch, version, output, **kwargs):
            del config, arch, kwargs
            if kind == "apkpure":
                raise RuntimeError("provider unavailable")
            Path(output).write_bytes(b"downloaded package")
            return version

        with tempfile.TemporaryDirectory() as td:
            with patch("tools.mirror_probe.validate_package", return_value=1):
                results, _ = run_audit(
                    targets, download_dir=td,
                    resolver=lambda config, experimental: "1.0",
                    downloader=fake_downloader,
                )
        self.assertEqual([result.status for result in results], ["fail", "pass"])
        self.assertIn("provider unavailable", results[0].error)


if __name__ == "__main__":
    unittest.main()
