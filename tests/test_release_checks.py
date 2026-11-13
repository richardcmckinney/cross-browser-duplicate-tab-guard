"""CI provenance tests use inert GitHub-shaped data and never call GitHub."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "release_checks", Path(__file__).resolve().parents[1] / "scripts/release_checks.py"
)
checks_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks_module)
SHA = "a" * 40


def evidence():
    checks = {"total_count": 2, "check_runs": []}
    statuses = {"total_count": 0, "statuses": [], "sha": SHA, "state": "pending"}
    runs = {"total_count": 2, "workflow_runs": []}
    for number, (name, workflow) in enumerate(checks_module.REQUIRED_WORKFLOWS.items(), 1):
        runs["workflow_runs"].append({
            "id": number * 100,
            "run_attempt": 1,
            "workflow_id": workflow["id"],
            "path": workflow["path"],
            "event": "push",
            "head_branch": "main",
            "head_sha": SHA,
            "repository": {"full_name": checks_module.REPOSITORY},
            "head_repository": {"full_name": checks_module.REPOSITORY},
            "status": "completed",
            "conclusion": "success",
            "check_suite_id": number * 10,
        })
        checks["check_runs"].append({
            "id": number * 1000,
            "name": name,
            "head_sha": SHA,
            "status": "completed",
            "conclusion": "success",
            "app": {"id": checks_module.ACTIONS_APP_ID, "slug": checks_module.ACTIONS_APP_SLUG},
            "check_suite": {"id": number * 10},
        })
    return checks, statuses, runs


class ProvenanceChecks(unittest.TestCase):
    def test_real_shaped_happy_path_returns_bound_receipts(self):
        result = checks_module.verify_ci(*evidence(), SHA)
        self.assertEqual(result["head_sha"], SHA)
        self.assertEqual(result["event"], "push")
        self.assertEqual(result["required_workflows"]["tests"]["workflow_run_id"], 100)
        self.assertEqual(result["required_workflows"]["gitleaks"]["app_id"], 15368)

    def test_name_only_success_cannot_authorize_release(self):
        checks, statuses, runs = evidence()
        for check in checks["check_runs"]:
            check.pop("app")
            check.pop("head_sha")
            check.pop("check_suite")
        with self.assertRaises(checks_module.CheckError):
            checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_wrong_app_sha_or_suite_is_rejected(self):
        variants = [
            ("app", {"id": 999, "slug": "github-actions"}),
            ("app", {"id": 15368, "slug": "impostor"}),
            ("head_sha", "b" * 40),
            ("check_suite", {"id": 999}),
        ]
        for key, value in variants:
            with self.subTest(key=key, value=value):
                checks, statuses, runs = evidence()
                checks["check_runs"][0][key] = value
                with self.assertRaises(checks_module.CheckError):
                    checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_rogue_newer_same_name_does_not_replace_trusted_check(self):
        checks, statuses, runs = evidence()
        rogue = copy.deepcopy(checks["check_runs"][0])
        rogue.update(id=999999, conclusion="success", app={"id": 999, "slug": "impostor"})
        checks["check_runs"].append(rogue)
        checks["total_count"] += 1
        result = checks_module.verify_ci(checks, statuses, runs, SHA)
        self.assertEqual(result["required_workflows"]["tests"]["check_run_id"], 1000)

    def test_wrong_workflow_id_path_event_branch_repo_or_sha_is_rejected(self):
        variants = [
            ("workflow_id", 999),
            ("path", ".github/workflows/fake.yml"),
            ("event", "pull_request"),
            ("event", "workflow_dispatch"),
            ("head_branch", "topic"),
            ("head_sha", "b" * 40),
            ("repository", {"full_name": "attacker/fork"}),
            ("head_repository", {"full_name": "attacker/fork"}),
            ("check_suite_id", 999),
        ]
        for key, value in variants:
            with self.subTest(key=key):
                checks, statuses, runs = evidence()
                runs["workflow_runs"][0][key] = value
                with self.assertRaises(checks_module.CheckError):
                    checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_missing_workflow_or_check_is_pending(self):
        checks, statuses, runs = evidence()
        runs["workflow_runs"].pop()
        runs["total_count"] -= 1
        with self.assertRaises(checks_module.ChecksPending):
            checks_module.verify_ci(checks, statuses, runs, SHA)
        checks, statuses, runs = evidence()
        checks["check_runs"].pop()
        checks["total_count"] -= 1
        with self.assertRaises(checks_module.ChecksPending):
            checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_queued_workflow_and_check_are_pending(self):
        for which in ("workflow", "check"):
            checks, statuses, runs = evidence()
            target = runs["workflow_runs"][0] if which == "workflow" else checks["check_runs"][0]
            target["status"] = "in_progress"
            target["conclusion"] = None
            with self.subTest(which=which), self.assertRaises(checks_module.ChecksPending):
                checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_failed_workflow_or_required_check_blocks_release(self):
        for which in ("workflow", "check"):
            checks, statuses, runs = evidence()
            target = runs["workflow_runs"][0] if which == "workflow" else checks["check_runs"][0]
            target["conclusion"] = "failure"
            with self.subTest(which=which), self.assertRaises(checks_module.CheckError):
                checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_latest_attempt_success_supersedes_old_check_failure(self):
        checks, statuses, runs = evidence()
        checks["check_runs"][0]["conclusion"] = "failure"
        latest = copy.deepcopy(checks["check_runs"][0])
        latest.update(id=1001, conclusion="success")
        checks["check_runs"].append(latest)
        checks["total_count"] += 1
        runs["workflow_runs"][0]["run_attempt"] = 2
        result = checks_module.verify_ci(checks, statuses, runs, SHA)
        self.assertEqual(result["required_workflows"]["tests"]["check_run_id"], 1001)
        self.assertEqual(result["required_workflows"]["tests"]["run_attempt"], 2)

    def test_later_run_failure_or_pending_cannot_reuse_old_success(self):
        for state in ("failure", "pending"):
            checks, statuses, runs = evidence()
            latest = copy.deepcopy(runs["workflow_runs"][0])
            latest.update(id=101, check_suite_id=11)
            if state == "failure":
                latest["conclusion"] = "failure"
            else:
                latest.update(status="queued", conclusion=None)
            runs["workflow_runs"].append(latest)
            runs["total_count"] += 1
            with self.subTest(state=state), self.assertRaises(checks_module.CheckError):
                checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_incomplete_pages_and_invalid_counts_fail_closed(self):
        for which, key in ((0, "check_runs"), (1, "statuses"), (2, "workflow_runs")):
            for count in (1000, -1, True):
                records = list(evidence())
                records[which]["total_count"] = count
                with self.subTest(which=which, count=count), self.assertRaises(checks_module.CheckError):
                    checks_module.verify_ci(*records, SHA)

    def test_combined_statuses_bind_sha_and_success(self):
        checks, statuses, runs = evidence()
        statuses.update(total_count=1, statuses=[{"context": "extra", "state": "success"}], state="success")
        checks_module.verify_ci(checks, statuses, runs, SHA)
        statuses["sha"] = "b" * 40
        with self.assertRaises(checks_module.CheckError):
            checks_module.verify_ci(checks, statuses, runs, SHA)
        statuses["sha"] = SHA
        statuses["state"] = "pending"
        with self.assertRaises(checks_module.ChecksPending):
            checks_module.verify_ci(checks, statuses, runs, SHA)
        statuses["state"] = "failure"
        with self.assertRaises(checks_module.CheckError):
            checks_module.verify_ci(checks, statuses, runs, SHA)

    def test_invalid_expected_commit_rejected(self):
        for sha in ("main", "A" * 40, "a" * 39, None):
            with self.subTest(sha=sha), self.assertRaises(checks_module.CheckError):
                checks_module.verify_ci(*evidence(), sha)


if __name__ == "__main__":
    unittest.main()
