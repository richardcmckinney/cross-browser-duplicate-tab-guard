"""Regression tests for the derived Chrome Web Store artifact."""

from __future__ import annotations

import json
import shutil
import sys
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

import cross_browser_duplicate_tab_guard as guard


REPO_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_DIR = REPO_ROOT / "extension"


class WebStorePackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.source = self.root / "extension"
        shutil.copytree(EXTENSION_DIR, self.source)
        self.output = self.root / "dist"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def build(self) -> Path:
        return guard.build_webstore_package(self.source, self.output, ["test-loopback-token"])

    def test_actual_zip_is_store_safe_and_matches_source_assets(self) -> None:
        archive_path = self.build()
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            self.assertEqual(names, sorted(names))
            self.assertEqual(len(names), len(set(names)))
            manifest = json.loads(archive.read("manifest.json"))
            self.assertNotIn("key", manifest)
            self.assertNotIn("update_url", manifest)
            self.assertLessEqual(len(manifest["description"]), 132)
            self.assertEqual(
                manifest["content_security_policy"]["extension_pages"],
                "script-src 'self'; object-src 'self';",
            )
            worker = archive.read("service_worker.js").decode("utf-8")
            self.assertIn('const FALLBACK_WS_URL = "";', worker)
            self.assertIn(guard.WEBSTORE_GUARD, worker)
            for name in names:
                if name.endswith(tuple(guard.WEBSTORE_TEXT_SUFFIXES)):
                    text = archive.read(name).decode("utf-8")
                    for marker in guard.WEBSTORE_FORBIDDEN_TEXT:
                        self.assertNotIn(marker, text, name)
                    self.assertNotIn("test-loopback-token", text, name)

            source_names = {
                path.relative_to(self.source).as_posix()
                for path in self.source.rglob("*")
                if path.is_file() and not path.is_symlink()
                and path.relative_to(self.source).as_posix() not in guard.WEBSTORE_SOURCE_ONLY_FILES
                and path.name not in guard.WEBSTORE_IGNORED_NAMES
            }
            self.assertEqual(set(names), source_names)
            for name in source_names - {"manifest.json", "service_worker.js"}:
                self.assertEqual(archive.read(name), (self.source / name).read_bytes(), name)

    def test_build_is_byte_for_byte_deterministic(self) -> None:
        first = self.build().read_bytes()
        second_dir = self.root / "dist-second"
        second = guard.build_webstore_package(
            self.source, second_dir, ["test-loopback-token"]
        ).read_bytes()
        self.assertEqual(first, second)

    def test_invalid_source_does_not_replace_previous_artifact(self) -> None:
        archive_path = self.build()
        original = archive_path.read_bytes()
        (self.source / "unexpected.txt").write_text("unsupported", encoding="utf-8")
        with self.assertRaises(guard.GuardError):
            guard.build_webstore_package(self.source, self.output, ["test-loopback-token"])
        self.assertEqual(archive_path.read_bytes(), original)
        self.assertEqual(list(self.output.glob("*.tmp")), [])

    def test_symlink_source_is_rejected_before_writing(self) -> None:
        archive_path = self.build()
        original = archive_path.read_bytes()
        link = self.source / "linked.js"
        try:
            link.symlink_to(self.source / "content.js")
        except (OSError, NotImplementedError):
            self.skipTest("symlinks unavailable")
        with self.assertRaises(guard.GuardError):
            guard.build_webstore_package(self.source, self.output, ["test-loopback-token"])
        self.assertEqual(archive_path.read_bytes(), original)

    def test_missing_manifest_resource_is_rejected(self) -> None:
        archive_path = self.build()
        original = archive_path.read_bytes()
        (self.source / "popup.html").unlink()
        with self.assertRaises(guard.GuardError):
            guard.build_webstore_package(self.source, self.output, ["test-loopback-token"])
        self.assertEqual(archive_path.read_bytes(), original)

    def test_manifest_traversal_resource_is_rejected(self) -> None:
        archive_path = self.build()
        original = archive_path.read_bytes()
        manifest_path = self.source / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["web_accessible_resources"] = [
            {"resources": ["../outside/*"], "matches": ["<all_urls>"]}
        ]
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(guard.GuardError):
            guard.build_webstore_package(self.source, self.output, ["test-loopback-token"])
        self.assertEqual(archive_path.read_bytes(), original)

    def test_manifest_resource_paths_are_validated_across_mv3_surfaces(self) -> None:
        resource_fields = (
            ("options_page", "../outside.html"),
            ("options_ui", {"page": "missing*.html"}),
            ("chrome_url_overrides", {"newtab": "../outside.html"}),
            ("side_panel", {"default_path": "../outside.html"}),
            ("offscreen", {"documents": ["../outside.html"]}),
            (
                "declarative_net_request",
                {"rule_resources": [{"path": "../rules.json"}]},
            ),
        )
        for field, value in resource_fields:
            with self.subTest(field=field):
                manifest_path = self.source / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest[field] = value
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(guard.GuardError):
                    guard.build_webstore_package(
                        self.source, self.output, ["test-loopback-token"]
                    )
                shutil.copy2(EXTENSION_DIR / "manifest.json", manifest_path)

    def test_packaging_help_does_not_build_or_mutate(self) -> None:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "package_extension.py"), "--help"],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--output-dir", result.stdout)
        self.assertNotIn("SHA-256:", result.stdout)

    def test_native_allowlist_contains_unpacked_and_store_ids(self) -> None:
        payload = json.loads(guard.native_manifest_payload())
        expected = {
            f"chrome-extension://{extension_id}/"
            for extension_id in guard.ALLOWED_EXTENSION_IDS
        }
        self.assertEqual(set(payload["allowed_origins"]), expected)
        self.assertIn("chrome-extension://ealdapdhgkonfamgboejhjaniepdjihk/", expected)
        self.assertIn("chrome-extension://dbfkmbhkefkppdbbiifihekopelgcdgb/", expected)

    @unittest.skipUnless(shutil.which("node"), "node is required for syntax validation")
    def test_packaged_javascript_has_valid_syntax(self) -> None:
        archive_path = self.build()
        with zipfile.ZipFile(archive_path) as archive, tempfile.TemporaryDirectory() as td:
            for name in archive.namelist():
                if not name.endswith(".js"):
                    continue
                path = Path(td) / Path(name).name
                path.write_bytes(archive.read(name))
                result = subprocess.run(
                    ["node", "--check", str(path)],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
