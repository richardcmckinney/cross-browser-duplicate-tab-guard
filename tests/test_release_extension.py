"""Release safety tests: no real GitHub or Chrome Web Store requests."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

spec = importlib.util.spec_from_file_location("release_extension", Path(__file__).resolve().parents[1] / "scripts/release_extension.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def revision(version, state="PUBLISHED", percent=100):
    return {"state": state, "distributionChannels": [{"crxVersion": version, "deployPercentage": percent}]}


class StoreDecisions(unittest.TestCase):
    def test_public_pending_staged_and_rollout_are_distinct(self):
        for state, expected in [("PENDING_REVIEW", "pending_review"), ("STAGED", "staged")]:
            with self.subTest(state=state):
                self.assertEqual(release.store_outcome({"submittedItemRevisionStatus": revision("2.4.2", state)}, "2.4.2"), expected)
        self.assertEqual(release.store_outcome({"publishedItemRevisionStatus": revision("2.4.2")}, "2.4.2"), "published")
        self.assertEqual(release.store_outcome({"publishedItemRevisionStatus": revision("2.4.2", percent=20)}, "2.4.2"), "partial_rollout")
        self.assertEqual(release.store_outcome({"publishedItemRevisionStatus": revision("2.4.1")}, "2.4.2"), "upload_required")

    def test_pending_other_or_unknown_version_blocks(self):
        for pending in [revision("2.4.3", "PENDING_REVIEW"), {"state": "PENDING_REVIEW"}]:
            with self.assertRaises(release.ReleaseError):
                release.store_outcome({"submittedItemRevisionStatus": pending}, "2.4.2")

    def test_terminal_policy_and_downgrade_states_block(self):
        statuses = [{"submittedItemRevisionStatus": revision("2.4.2", state)} for state in ("REJECTED", "CANCELLED")]
        statuses += [{"warned": True}, {"takenDown": True}, {"publishedItemRevisionStatus": revision("2.4.10")}, {"lastAsyncUploadState": "IN_PROGRESS"}]
        for status in statuses:
            with self.subTest(status=status), self.assertRaises(release.ReleaseError):
                release.store_outcome(status, "2.4.2")
        self.assertEqual(release.store_outcome({"publishedItemRevisionStatus": revision("2.4.1.0")}, "2.4.2"), "upload_required")

    def test_ambiguous_upload_never_blindly_publishes(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, journal = Path(tmp) / "package.zip", Path(tmp) / "upload.json"
            archive.write_bytes(b"archive")
            release.save_receipt(journal, {"phase": "upload_intent", "version": "2.4.2"})
            store = release.ChromeStore("publisher", "secret")
            with patch.object(store, "request", return_value={}) as request, self.assertRaises(release.ReleaseError):
                store.submit(archive, "2.4.2", journal)
            self.assertEqual([x.args[0] for x in request.call_args_list], ["fetchStatus"])

    def test_existing_same_version_pending_is_not_reuploaded(self):
        store = release.ChromeStore("publisher", "secret")
        with patch.object(store, "request", return_value={"submittedItemRevisionStatus": revision("2.4.2", "PENDING_REVIEW")}) as request:
            with tempfile.TemporaryDirectory() as temporary:
                archive = Path(temporary) / "package.zip"
                archive.write_bytes(b"archive")
                outcome, _ = store.submit(archive, "2.4.2", Path(temporary) / "absent-journal")
        self.assertEqual(outcome, "existing_pending_review_unverified")
        self.assertEqual(request.call_count, 1)

    def test_async_upload_waits_for_success_and_confirms_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, journal = Path(tmp) / "package.zip", Path(tmp) / "upload.json"
            archive.write_bytes(b"archive")
            store = release.ChromeStore("publisher", "secret")
            replies = [{}, {"uploadState": "IN_PROGRESS", "crxVersion": "2.4.2"}, {"lastAsyncUploadState": "IN_PROGRESS"},
                       {"lastAsyncUploadState": "SUCCEEDED"}, {"state": "PENDING_REVIEW"},
                       {"submittedItemRevisionStatus": revision("2.4.2", "PENDING_REVIEW")}]
            with patch.object(store, "request", side_effect=replies) as request, patch.object(release.time, "sleep"):
                outcome, _ = store.submit(archive, "2.4.2", journal)
            self.assertEqual(outcome, "pending_review")
            self.assertEqual([x.args[0] for x in request.call_args_list], ["fetchStatus", "upload", "fetchStatus", "fetchStatus", "publish", "fetchStatus"])
            payload = json.loads(request.call_args_list[4].kwargs["data"])
            self.assertTrue(payload["blockOnWarnings"])
            self.assertFalse(payload["skipReview"])
            self.assertEqual(json.loads(journal.read_text())["phase"], "confirmed")

    def test_upload_failure_preserves_intent_for_manual_reconciliation(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, journal = Path(tmp) / "package.zip", Path(tmp) / "upload.json"
            archive.write_bytes(b"archive")
            store = release.ChromeStore("publisher", "secret")
            with patch.object(store, "request", side_effect=[{}, release.ReleaseError("timeout")]), self.assertRaises(release.ReleaseError):
                store.submit(archive, "2.4.2", journal)
            self.assertEqual(json.loads(journal.read_text())["phase"], "upload_intent")

    def test_wrong_upload_version_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "package.zip"
            archive.write_bytes(b"archive")
            store = release.ChromeStore("publisher", "secret")
            with patch.object(store, "request", side_effect=[{}, {"uploadState": "SUCCEEDED", "crxVersion": "2.4.1"}]) as request, self.assertRaises(release.ReleaseError):
                store.submit(archive, "2.4.2", Path(tmp) / "upload.json")
            self.assertNotIn("publish", [x.args[0] for x in request.call_args_list])

    def test_async_upload_without_version_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, journal = Path(tmp) / "package.zip", Path(tmp) / "upload.json"
            archive.write_bytes(b"archive")
            store = release.ChromeStore("publisher", "secret")
            with patch.object(store, "request", side_effect=[{}, {"uploadState": "IN_PROGRESS"}, {"lastAsyncUploadState": "SUCCEEDED"}]) as request, patch.object(release.time, "sleep"), self.assertRaises(release.ReleaseError):
                store.submit(archive, "2.4.2", journal)
            self.assertNotIn("publish", [x.args[0] for x in request.call_args_list])
            self.assertEqual(json.loads(journal.read_text())["phase"], "upload_intent")

    def test_matching_version_needs_matching_package_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "upload.json"
            release.save_receipt(journal, {"version": "2.4.2", "sha256": "right", "phase": "publish_intent"})
            self.assertEqual(release.proven_store_outcome("pending_review", "2.4.2", "right", journal), "pending_review")
            self.assertEqual(release.proven_store_outcome("pending_review", "2.4.2", "wrong", journal), "existing_pending_review_unverified")
            release.save_receipt(journal, {"version": "2.4.2", "sha256": "right", "phase": "upload_intent"})
            self.assertEqual(release.proven_store_outcome("pending_review", "2.4.2", "right", journal), "existing_pending_review_unverified")

    def test_public_listing_parser_ignores_scripts_and_checks_install_control(self):
        html = '<title>Cross-Browser Duplicate Tab Guard - Chrome Web Store</title><script>Version 9.9.9</script><div>Version</div><div>2.4.2</div><button>Add to Chrome</button>'
        self.assertTrue(release.parse_public_listing(html, "2.4.2")["public_verified"])
        self.assertFalse(release.parse_public_listing(html, "2.4.3")["public_verified"])
        self.assertFalse(release.parse_public_listing(html.replace("Add to Chrome", "Unavailable"), "2.4.2")["public_verified"])
        self.assertIsNone(release.parse_public_listing('<script>Version 2.4.2</script>', "2.4.2")["public_version"])

    def test_api_error_never_contains_token_or_body(self):
        class Opener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 401, "access_token=do-not-print", {}, None)
        with self.assertRaises(release.ReleaseError) as caught:
            release.ChromeStore.request_json(Opener(), "https://chromewebstore.googleapis.com/v2/test")
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("do-not-print", str(caught.exception))


class GitHubSafety(unittest.TestCase):
    def test_required_checks_must_exist_and_succeed(self):
        def checks(test="success", scan="success"):
            return {"total_count": 2, "check_runs": [{"id": 1, "name": "tests", "status": "completed", "conclusion": test},
                     {"id": 2, "name": "gitleaks", "status": "completed", "conclusion": scan}]}
        release.validate_checks(checks(), {"total_count": 0})
        for runs in [{"total_count": 0, "check_runs": []}, checks("skipped"), checks(scan="failure"), {"total_count": 3, "check_runs": checks()["check_runs"]}]:
            with self.assertRaises(release.ReleaseError):
                release.validate_checks(runs, {"total_count": 0})

    def test_latest_rerun_supersedes_old_failure(self):
        checks = {"total_count": 3, "check_runs": [
            {"id": 1, "name": "tests", "status": "completed", "conclusion": "failure"},
            {"id": 2, "name": "tests", "status": "completed", "conclusion": "success"},
            {"id": 3, "name": "gitleaks", "status": "completed", "conclusion": "success"}]}
        release.validate_checks(checks, {"total_count": 0})

    def test_conflicting_existing_release_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, sums = Path(tmp) / "package.zip", Path(tmp) / "SHA256SUMS"
            archive.write_bytes(b"new")
            sums.write_text("new-checksum  package.zip\n")
            existing = {"tag_name": "v2.4.2", "assets": [{"name": "package.zip"}, {"name": "SHA256SUMS"}]}
            def download(args, root):
                Path(args[-1], args[args.index("--pattern") + 1]).write_text("old-checksum")
            with patch.object(release, "gh_json", return_value=[[existing]]), patch.object(release, "run", side_effect=download) as command, self.assertRaises(release.ReleaseError):
                release.github_release(Path(tmp), archive, sums, "2.4.2", "a" * 40)
            self.assertTrue(all(x.args[0][1:3] == ["release", "download"] for x in command.call_args_list))

    def test_published_package_reuse_retains_original_tag_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, sums = Path(tmp) / "package.zip", Path(tmp) / "SHA256SUMS"
            archive.write_bytes(b"same-package")
            sums.write_text(release.hashlib.sha256(archive.read_bytes()).hexdigest() + "  package.zip\n")
            existing = {"tag_name": "v2.4.2", "assets": [{"name": "package.zip"}, {"name": "SHA256SUMS"}], "html_url": "https://github.com/release", "draft": False}
            def command(args, root, **kwargs):
                if args[:3] == ["gh", "release", "download"]:
                    name = args[args.index("--pattern") + 1]
                    Path(args[-1], name).write_bytes((Path(tmp) / name).read_bytes())
                return SimpleNamespace(returncode=0, stdout="")
            with patch.object(release, "gh_json", return_value=[[existing]]), patch.object(release, "git", return_value="b" * 40 + "\trefs/tags/v2.4.2"), patch.object(release, "run", side_effect=command) as calls:
                result = release.github_release(Path(tmp), archive, sums, "2.4.2", "a" * 40)
            self.assertEqual(result["source_commit"], "b" * 40)
            self.assertTrue(result["reused"])
            self.assertFalse(any(call.args[0][:3] == ["gh", "release", "edit"] for call in calls.call_args_list))

    def test_draft_tag_mismatch_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, sums = Path(tmp) / "package.zip", Path(tmp) / "SHA256SUMS"
            archive.write_bytes(b"same-package")
            sums.write_text(release.hashlib.sha256(archive.read_bytes()).hexdigest() + "  package.zip\n")
            existing = {"tag_name": "v2.4.2", "assets": [{"name": "package.zip"}, {"name": "SHA256SUMS"}], "html_url": "https://github.com/release", "draft": True}
            def command(args, root, **kwargs):
                if args[:3] == ["gh", "release", "download"]:
                    name = args[args.index("--pattern") + 1]
                    Path(args[-1], name).write_bytes((Path(tmp) / name).read_bytes())
                return SimpleNamespace(returncode=0, stdout="")
            with patch.object(release, "gh_json", return_value=[[existing]]), patch.object(release, "git", return_value="b" * 40 + "\trefs/tags/v2.4.2"), patch.object(release, "run", side_effect=command) as calls, self.assertRaises(release.ReleaseError):
                release.github_release(Path(tmp), archive, sums, "2.4.2", "a" * 40)
            self.assertFalse(any(call.args[0][:3] == ["gh", "release", "edit"] for call in calls.call_args_list))

    def test_existing_tag_cannot_be_repointed(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive, sums = Path(tmp) / "package.zip", Path(tmp) / "SHA256SUMS"
            archive.write_bytes(b"new")
            sums.write_text("checksum")
            with patch.object(release, "gh_json", return_value=[[]]), patch.object(release, "git", return_value="b" * 40 + "\trefs/tags/v2.4.2"), patch.object(release, "run") as command, self.assertRaises(release.ReleaseError):
                release.github_release(Path(tmp), archive, sums, "2.4.2", "a" * 40)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
