"""Adversarial invariants for the pinned publisher; no real provider writes."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("release_security_controller", ROOT / "scripts/release_extension.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def git(root, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"})
    result = subprocess.run(["git", "-c", "user.name=Release Security Fixture",
                             "-c", "user.email=fixture@example.invalid", *args],
                            cwd=root, env=env, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def fixture(root):
    source = root / "candidate"
    source.mkdir()
    shutil.copytree(ROOT / "extension", source / "extension")
    files = {p.relative_to(source / "extension").as_posix(): p.read_bytes()
             for p in (source / "extension").rglob("*") if p.is_file()}
    text = {name: data.decode() for name, data in files.items() if Path(name).suffix in {".js", ".css", ".html", ".json"}}
    binary = {name: base64.b64encode(data).decode() for name, data in files.items() if Path(name).suffix == ".png"}
    version = json.loads(files["manifest.json"])["version"]
    sentinel = root / "candidate-code-executed"
    candidate = ("from pathlib import Path\n"
                 + "Path(" + repr(str(sentinel)) + ").write_text('unsafe')\n"
                 + "APP_VERSION = " + repr(version) + "\n"
                 + "EXTENSION_FILES: dict[str, str] = " + repr(text) + "\n"
                 + "EXTENSION_BINARY_FILES: dict[str, str] = " + repr(binary) + "\n")
    (source / "cross_browser_duplicate_tab_guard.py").write_text(candidate)
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "commit", "-qm", "Fixture only")
    return source, git(source, "rev-parse", "HEAD"), sentinel


class DurablePrivateState(unittest.TestCase):
    def test_intent_persistence_failure_prevents_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "package.zip"
            archive.write_bytes(b"safe fixture")
            store = release.ChromeStore("publisher", "never-printed")
            with patch.object(store, "request", return_value={}) as request, patch.object(release.os, "fsync", side_effect=OSError("simulated disk failure")):
                with self.assertRaises(OSError):
                    store.submit(archive, "2.4.2", root / "upload.json")
            self.assertEqual([call.args[0] for call in request.call_args_list], ["fetchStatus"])
            self.assertFalse((root / "upload.json").exists())
            self.assertFalse(list(root.glob(".*.tmp")))

    def test_remote_upload_sees_durable_private_matching_intent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "package.zip"
            archive.write_bytes(b"safe fixture")
            journal = root / "upload.json"
            store = release.ChromeStore("publisher", "never-printed")
            syncs = []
            real_sync = release.os.fsync
            def sync(fd):
                syncs.append(stat.S_ISDIR(os.fstat(fd).st_mode))
                real_sync(fd)
            states = {"fetch": 0}
            def request(action, **kwargs):
                if action == "fetchStatus":
                    states["fetch"] += 1
                    if states["fetch"] == 1:
                        return {}
                    return {"submittedItemRevisionStatus": {"state": "PENDING_REVIEW", "distributionChannels": [{"crxVersion": "2.4.2", "deployPercentage": 100}]}}
                record = release.read_receipt(journal)
                self.assertEqual(record["sha256"], hashlib.sha256(archive.read_bytes()).hexdigest())
                self.assertEqual(record["item_name"], store.name)
                self.assertEqual(stat.S_IMODE(journal.stat().st_mode), 0o600)
                self.assertIn(True, syncs, "Directory entry must be fsynced before remote mutation")
                if action == "upload":
                    self.assertEqual(record["phase"], "upload_intent")
                    return {"uploadState": "SUCCEEDED", "crxVersion": "2.4.2"}
                self.assertEqual(record["phase"], "publish_intent")
                return {"state": "PENDING_REVIEW"}
            with patch.object(store, "request", side_effect=request), patch.object(release.os, "fsync", side_effect=sync):
                outcome, _ = store.submit(archive, "2.4.2", journal)
            self.assertEqual(outcome, "pending_review")
            self.assertEqual(release.read_receipt(journal)["phase"], "confirmed")

    def test_symlink_receipt_and_symlink_parent_do_not_overwrite_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "sentinel"
            target.write_text("unchanged")
            alias = root / "receipt.json"
            alias.symlink_to(target)
            with self.assertRaises(release.ReleaseError):
                release.save_receipt(alias, {"unexpected": True})
            self.assertEqual(target.read_text(), "unchanged")
            actual = root / "actual"
            actual.mkdir(mode=0o700)
            parent_alias = root / "alias"
            parent_alias.symlink_to(actual, target_is_directory=True)
            with self.assertRaises(release.ReleaseError):
                release.save_receipt(parent_alias / "receipt.json", {})
            self.assertFalse((actual / "receipt.json").exists())

    def test_shared_state_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            shared = Path(tmp) / "shared"
            shared.mkdir(mode=0o777)
            shared.chmod(0o777)
            with self.assertRaises(release.ReleaseError):
                release.save_receipt(shared / "receipt.json", {})

    def test_lock_is_private_nonblocking_and_symlink_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "release.lock"
            with release.release_lock(path):
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
                with self.assertRaises(BlockingIOError):
                    release.release_lock(path)
            target = root / "target"
            target.write_text("unchanged")
            path.unlink()
            path.symlink_to(target)
            with self.assertRaises(OSError):
                release.release_lock(path)
            self.assertEqual(target.read_text(), "unchanged")

    def test_provider_journal_cannot_be_reused_for_another_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "upload.json"
            release.save_receipt(journal, {"phase": "confirmed", "version": "2.4.2", "sha256": "right", "item_name": "publishers/other/items/other"})
            outcome = release.proven_store_outcome("pending_review", "2.4.2", "right", journal, release.LEGACY_ITEM_NAME)
            self.assertEqual(outcome, "existing_pending_review_unverified")

    def test_unknown_provider_state_never_reaches_upload(self):
        for revision in ("submittedItemRevisionStatus", "publishedItemRevisionStatus"):
            store = release.ChromeStore("publisher", "never-printed")
            with patch.object(store, "request", return_value={revision: {"state": "UNRECOGNIZED_FUTURE_STATE"}}) as calls:
                with self.assertRaises(release.ReleaseError):
                    store.submit(Path("absent.zip"), "2.4.2", Path("absent.json"))
            self.assertEqual([call.args[0] for call in calls.call_args_list], ["fetchStatus"])

    def test_known_legacy_journal_migrates_only_for_exact_item_digest_and_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "upload.json"
            record = {"phase": "confirmed", "version": release.LEGACY_RELEASE_VERSION, "sha256": release.LEGACY_RELEASE_SHA256}
            release.save_receipt(journal, record)
            outcome = release.proven_store_outcome("published", record["version"], record["sha256"], journal, release.LEGACY_ITEM_NAME)
            self.assertEqual(outcome, "published")
            self.assertEqual(release.read_receipt(journal)["item_name"], release.LEGACY_ITEM_NAME)
            release.save_receipt(journal, record)
            outcome = release.proven_store_outcome("published", record["version"], record["sha256"], journal, "publishers/other/items/other")
            self.assertEqual(outcome, "existing_published_unverified")
            self.assertNotIn("item_name", release.read_receipt(journal))

    def test_current_public_flags_reset_after_revision_state_changes(self):
        for state in ("pending_review", "upload_required", "pending_publication"):
            receipt = {"store": state, "version": "2.4.2", "public_version": "2.4.2", "public_verified": True,
                       "public_install_available": True, "store_payload_verified": True,
                       "store_payload": {"verified_at": "historical proof"}}
            release.add_public_evidence(receipt)
            release.add_store_payload_evidence(receipt, Path("unused"))
            self.assertFalse(receipt["public_verified"])
            self.assertFalse(receipt["public_install_available"])
            self.assertFalse(receipt["store_payload_verified"])
            self.assertIsNone(receipt["public_version"])
            self.assertEqual(receipt["store_payload"]["verified_at"], "historical proof")

    def test_confirmed_revision_regression_never_reuploads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, journal = root / "package.zip", root / "upload.json"
            archive.write_bytes(b"fixture")
            release.save_receipt(journal, {"phase": "confirmed", "version": "2.4.2", "sha256": hashlib.sha256(b"fixture").hexdigest()})
            store = release.ChromeStore("publisher", "never-printed")
            status = {"publishedItemRevisionStatus": {"state": "PUBLISHED", "distributionChannels": [{"crxVersion": "2.4.1", "deployPercentage": 100}]}}
            with patch.object(store, "request", return_value=status) as request:
                with self.assertRaisesRegex(release.ReleaseError, "possible rollback"):
                    store.submit(archive, "2.4.2", journal)
            self.assertEqual([call.args[0] for call in request.call_args_list], ["fetchStatus"])

    def test_new_release_blocks_when_provider_immutability_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, checksum = root / "package.zip", root / "SHA256SUMS"
            archive.write_bytes(b"fixture")
            checksum.write_text("fixture")
            with patch.object(release, "gh_json", side_effect=[[[]], {"enabled": False}]), patch.object(release, "run") as writes:
                with self.assertRaisesRegex(release.ReleaseError, "immutable releases policy"):
                    release.github_release(root, archive, checksum, "2.4.3", "a" * 40)
            writes.assert_not_called()

    def test_legacy_unprotected_release_cannot_change_its_pinned_tag_source(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive, checksum = root / "package.zip", root / "SHA256SUMS"
            archive.write_bytes(b"fixture")
            digest = hashlib.sha256(b"fixture").hexdigest()
            checksum.write_text(digest + "  package.zip\n")
            existing = {"tag_name": "v2.4.2", "draft": False, "immutable": False, "html_url": "https://github.com/example",
                        "assets": [{"name": "package.zip"}, {"name": "SHA256SUMS"}]}
            def command(args, root, **kwargs):
                if args[:3] == ["gh", "release", "download"]:
                    name = args[args.index("--pattern") + 1]
                    Path(args[-1], name).write_bytes((Path(tmp) / name).read_bytes())
                return SimpleNamespace(returncode=0, stdout="")
            with patch.object(release, "LEGACY_RELEASE_SHA256", digest), patch.object(release, "gh_json", return_value=[[existing]]), patch.object(release, "git", return_value="b"*40 + "\trefs/tags/v2.4.2"), patch.object(release, "run", side_effect=command):
                with self.assertRaisesRegex(release.ReleaseError, "pinned reviewed source"):
                    release.github_release(root, archive, checksum, "2.4.2", "a"*40)


class ImmutableSourceData(unittest.TestCase):
    def test_worktree_mutation_and_candidate_python_do_not_influence_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, commit, sentinel = fixture(root)
            output = root / "artifacts"
            output.mkdir(mode=0o700)
            archive, _, version, digest = release.prepare(source, output, commit)
            self.assertFalse(sentinel.exists(), "Candidate companion code must never execute")
            original = archive.read_bytes()
            (source / "extension/manifest.json").write_text('{"version":"999"}')
            (source / "extension/icons/icon16.png").write_bytes(b"untracked worktree replacement")
            (source / "json.py").write_text("raise RuntimeError('must not import candidate modules')")
            archive, _, after_version, after_digest = release.prepare(source, output, commit)
            self.assertEqual(archive.read_bytes(), original)
            self.assertEqual((version, digest), (after_version, after_digest))
            self.assertFalse(sentinel.exists())

    def test_pinned_packager_matches_existing_upload_policy_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, commit, _ = fixture(root)
            output = root / "artifacts"
            output.mkdir(mode=0o700)
            archive, _, _, _ = release.prepare(source, output, commit)
            spec = importlib.util.spec_from_file_location("trusted_test_companion", ROOT / "cross_browser_duplicate_tab_guard.py")
            trusted = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = trusted
            spec.loader.exec_module(trusted)
            expected = trusted.build_webstore_package(ROOT / "extension", root / "baseline", [])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), hashlib.sha256(expected.read_bytes()).hexdigest())

    def test_symlink_and_gitlink_sources_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, _, _ = fixture(root)
            icon = source / "extension/icons/icon16.png"
            icon.unlink()
            icon.symlink_to("../manifest.json")
            git(source, "add", ".")
            git(source, "commit", "-qm", "Symlink fixture only")
            with self.assertRaises(release.ReleaseError):
                release.prepare(source, root / "artifacts", git(source, "rev-parse", "HEAD"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, commit, _ = fixture(root)
            git(source, "update-index", "--add", "--cacheinfo", "160000", commit, "extension/submodule")
            git(source, "commit", "-qm", "Gitlink fixture only")
            with self.assertRaises(release.ReleaseError):
                release.prepare(source, root / "artifacts", git(source, "rev-parse", "HEAD"))

    def test_isolated_cli_does_not_import_candidate_shadow_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, commit, sentinel = fixture(root)
            (source / "json.py").write_text("raise RuntimeError('candidate json imported')")
            result = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "scripts/release_extension.py"),
                                     "--source-dir", str(source), "--expected-commit", commit,
                                     "--output-dir", str(root / "artifacts")], cwd=source,
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(sentinel.exists())

    def test_pinned_policy_rejects_unreviewed_optional_capabilities(self):
        files = {p.relative_to(ROOT / "extension").as_posix(): p.read_bytes() for p in (ROOT / "extension").rglob("*") if p.is_file()}
        original = json.loads(files["manifest.json"])
        for optional in (["debugger"], "tabs", [None]):
            manifest = dict(original)
            manifest["optional_permissions"] = optional
            changed = dict(files, **{"manifest.json": json.dumps(manifest).encode()})
            with self.subTest(optional=optional), self.assertRaises(release.PackageError):
                release.package_bytes(changed)

    def test_incomplete_or_mutable_bundle_is_rejected_before_cli_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            names = ("release_extension.py", "release_checks.py", "release_packager.py", "verify_store_package.py")
            for name in names[:-1]:
                shutil.copy2(ROOT / "scripts" / name, bundle / name)
            result = subprocess.run([sys.executable, "-I", "-B", str(bundle / "release_extension.py"), "--help"], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("bundle is incomplete", result.stderr)
            shutil.copy2(ROOT / "scripts" / names[-1], bundle / names[-1])
            (bundle / "release_checks.py").chmod(0o666)
            result = subprocess.run([sys.executable, "-I", "-B", str(bundle / "release_extension.py"), "--help"], capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("owner-controlled", result.stderr)


if __name__ == "__main__":
    unittest.main()
