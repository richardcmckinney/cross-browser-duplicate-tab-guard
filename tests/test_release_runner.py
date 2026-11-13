"""Exercise the production runner without network access or human notifications."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "vsh-release-runner.sh"
OLD = "a" * 40
NEW = "b" * 40


class RuntimeFixture:
    def __init__(self, directory):
        self.root = Path(directory)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()
        self.state = self.home / ".local/state/duplicate-tab-guard-release"
        self.source = self.state / "source"
        self.source.mkdir(parents=True)
        self.trace = self.root / "calls.jsonl"
        self.env = {
            "PATH": f"{self.bin}:/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            "TRACE": str(self.trace),
            "FIXTURE_REMOTE_SHA": NEW,
        }
        bundle = self.home / ".local/libexec/duplicate-tab-guard-release"
        bundle.mkdir(parents=True)
        (bundle / "release_extension.py").write_text(
            """import json, os, pathlib, sys
a=sys.argv[1:]
mode="status" if "--status" in a else "gate" if "--verify-source-only" in a else "publish"
with open(os.environ["TRACE"],"a") as f:f.write(json.dumps({"tool":"controller","mode":mode,"args":a})+"\\n")
code=int(os.environ.get("FIXTURE_"+mode.upper()+"_EXIT","0"))
if mode=="publish" and code==0:
 out=pathlib.Path(a[a.index("--output-dir")+1]);out.mkdir(parents=True,exist_ok=True)
 (out/"receipt.json").write_text(json.dumps({"commit":a[a.index("--expected-commit")+1],"store":"pending_review"}))
raise SystemExit(code)
"""
        )
        self._program(
            "git",
            """import json,os,pathlib,sys
a=sys.argv[1:]
with open(os.environ["TRACE"],"a") as f:f.write(json.dumps({"tool":"git","args":a})+"\\n")
if a[0]=="remote":print(os.environ.get("FIXTURE_ORIGIN", "https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git"))
elif a[0]=="fetch":raise SystemExit(int(os.environ.get("FIXTURE_FETCH_EXIT","0")))
elif a[0]=="rev-parse":print(os.environ["FIXTURE_REMOTE_SHA"])
elif a[0]=="clone":pathlib.Path(a[-1]).mkdir(parents=True)
else:raise SystemExit(73)
""",
        )
        self._program(
            "venturi-alert",
            """import json,os,sys
with open(os.environ["TRACE"],"a") as f:f.write(json.dumps({"tool":"alert","args":sys.argv[1:]})+"\\n")
raise SystemExit(0)
""",
        )
        for name in ("npm", "npx", "node", "gh"):
            self._program(
                name,
                """import json,os,sys
with open(os.environ["TRACE"],"a") as f:f.write(json.dumps({"tool":"forbidden","name":sys.argv[0]})+"\\n")
raise SystemExit(72)
""",
            )
        # Candidate code must never run, even when its immutable CI gate succeeds.
        evil = self.source / "scripts"
        evil.mkdir()
        (evil / "release_extension.py").write_text(
            "from pathlib import Path\nPath(" + repr(str(self.root / "candidate-executed")) + ").touch()\n"
        )
        (self.source / "package.json").write_text(
            json.dumps({"scripts": {"test": "touch " + str(self.root / "candidate-executed")}})
        )

    def _program(self, name, body):
        path = self.bin / name
        path.write_text("#!/usr/bin/python3\n" + body)
        path.chmod(0o755)

    def receipt(self, commit=OLD):
        path = self.state / "artifacts/receipt.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"commit": commit, "store": "pending_review"}))
        path.chmod(0o600)
        return path

    def run(self):
        return subprocess.run(
            ["bash", str(RUNNER)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )

    def calls(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def assert_no_candidate_execution(self, case):
        case.assertFalse((self.root / "candidate-executed").exists())
        case.assertFalse(any(call["tool"] == "forbidden" for call in self.calls()))
        case.assertFalse(any(call["tool"] == "git" and call["args"][0] == "checkout" for call in self.calls()))


class ReleaseRunnerTests(unittest.TestCase):
    def test_existing_rollout_is_checked_before_github_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            fixture.env["FIXTURE_FETCH_EXIT"] = "22"
            result = fixture.run()
            self.assertEqual(result.returncode, 22, result.stderr)
            calls = fixture.calls()
            self.assertEqual(calls[0]["mode"], "status")
            self.assertFalse(any(call.get("mode") in {"gate", "publish"} for call in calls))
            fixture.assert_no_candidate_execution(self)

    def test_unchanged_commit_refreshes_status_without_rechecking_or_rebuilding(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt(NEW)
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([call["mode"] for call in fixture.calls() if call["tool"] == "controller"], ["status"])
            fixture.assert_no_candidate_execution(self)

    def test_pending_new_ci_preserves_monitoring_and_does_not_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            fixture.env["FIXTURE_GATE_EXIT"] = "2"
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([call["mode"] for call in fixture.calls() if call["tool"] == "controller"], ["status", "gate"])
            self.assertFalse(any(call["tool"] == "alert" for call in fixture.calls()))
            fixture.assert_no_candidate_execution(self)

    def test_new_release_uses_only_installed_controller_with_candidate_as_data(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [call for call in fixture.calls() if call["tool"] == "controller"]
            self.assertEqual([call["mode"] for call in calls], ["status", "gate", "publish"])
            publish = calls[-1]["args"]
            self.assertEqual(publish[publish.index("--source-dir") + 1], str(fixture.source))
            self.assertEqual(publish[publish.index("--expected-commit") + 1], NEW)
            self.assertIn("--github", publish)
            self.assertIn("--submit", publish)
            fixture.assert_no_candidate_execution(self)

    def test_new_clone_never_checks_out_candidate_code(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            shutil.rmtree(fixture.source)
            result = fixture.run()
            self.assertEqual(result.returncode, 0, result.stderr)
            clone = next(call for call in fixture.calls() if call["tool"] == "git" and call["args"][0] == "clone")
            self.assertIn("--no-checkout", clone["args"])
            fixture.assert_no_candidate_execution(self)

    def test_status_failure_blocks_new_writes_and_preserves_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            fixture.env["FIXTURE_STATUS_EXIT"] = "19"
            result = fixture.run()
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertFalse(any(call["tool"] == "git" for call in fixture.calls()))
            fixture.assert_no_candidate_execution(self)

    def test_failed_gate_records_passive_six_field_alert_without_human_escalation(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            fixture.env["FIXTURE_GATE_EXIT"] = "1"
            result = fixture.run()
            self.assertEqual(result.returncode, 1, result.stderr)
            alerts = [call for call in fixture.calls() if call["tool"] == "alert"]
            self.assertEqual(len(alerts), 1)
            args = alerts[0]["args"]
            self.assertEqual(args[0], "send")
            for flag in ("--id", "--severity", "--summary", "--impact", "--action", "--evidence", "--source"):
                self.assertIn(flag, args)
            self.assertNotIn("--needs-owner", args)
            self.assertNotIn("--page", args)
            evidence = Path(args[args.index("--evidence") + 1])
            self.assertEqual(stat.S_IMODE(evidence.stat().st_mode), 0o600)
            self.assertIn("Runner exit status: 1", evidence.read_text())
            fixture.assert_no_candidate_execution(self)

    def test_failure_evidence_replaces_symlink_without_overwriting_target(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            sentinel = fixture.root / "keep.txt"
            sentinel.write_text("untouched")
            (fixture.state / "last-failure.log").symlink_to(sentinel)
            fixture.env["FIXTURE_GATE_EXIT"] = "1"
            result = fixture.run()
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertFalse((fixture.state / "last-failure.log").is_symlink())

    def test_symlinked_runner_lock_is_rejected_without_touching_target(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            sentinel = fixture.root / "keep.txt"
            sentinel.write_text("untouched")
            (fixture.state / "runner.lock").symlink_to(sentinel)
            result = fixture.run()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertEqual(fixture.calls(), [])


    def test_wrong_origin_is_blocked_before_network_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            fixture.env["FIXTURE_ORIGIN"] = "https://example.invalid/untrusted.git"
            result = fixture.run()
            self.assertEqual(result.returncode, 1)
            self.assertFalse(any(call["tool"] == "git" and call["args"][0] == "fetch" for call in fixture.calls()))
            self.assertFalse(any(call.get("mode") in {"gate", "publish"} for call in fixture.calls()))

    def test_hardlinked_runner_lock_is_rejected_without_changing_target(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            sentinel = fixture.root / "keep.txt"
            sentinel.write_text("untouched")
            sentinel.chmod(0o644)
            os.link(sentinel, fixture.state / "runner.lock")
            result = fixture.run()
            self.assertEqual(result.returncode, 1)
            self.assertEqual(sentinel.read_text(), "untouched")
            self.assertEqual(stat.S_IMODE(sentinel.stat().st_mode), 0o644)
            self.assertEqual(fixture.calls(), [])

    def test_symlinked_source_is_rejected_before_git_uses_it(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = RuntimeFixture(temp)
            fixture.receipt()
            shutil.rmtree(fixture.source)
            outside = fixture.root / "outside"
            outside.mkdir()
            fixture.source.symlink_to(outside, target_is_directory=True)
            result = fixture.run()
            self.assertEqual(result.returncode, 1)
            self.assertFalse(any(call["tool"] == "git" for call in fixture.calls()))


if __name__ == "__main__":
    unittest.main()
