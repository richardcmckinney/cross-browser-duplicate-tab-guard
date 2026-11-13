"""Pure provenance validation for the fixed Duplicate Tab Guard release workflows.

The caller supplies complete responses from GitHub's canonical commit/check and
Actions APIs. Check names alone are never release authorization.
"""
from __future__ import annotations

import re

REPOSITORY = "richardcmckinney/cross-browser-duplicate-tab-guard"
ACTIONS_APP_ID = 15368
ACTIONS_APP_SLUG = "github-actions"
REQUIRED_WORKFLOWS = {
    "tests": {"id": 392163611, "path": ".github/workflows/tests.yml"},
    "gitleaks": {"id": 401007811, "path": ".github/workflows/secret-scan.yml"},
}


class CheckError(RuntimeError):
    """The supplied evidence does not authorize a release."""


class ChecksPending(CheckError):
    """Required canonical CI evidence has not finished yet."""


def _rows(response, key):
    if not isinstance(response, dict):
        raise CheckError("GitHub CI evidence must be an object")
    rows = response.get(key)
    count = response.get("total_count")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise CheckError("GitHub CI evidence has invalid records")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0 or count != len(rows):
        raise CheckError("GitHub CI evidence is incomplete or inconsistent")
    return rows


def _positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _canonical_context(run, commit):
    return (
        run.get("event") == "push"
        and run.get("head_branch") == "main"
        and run.get("head_sha") == commit
        and isinstance(run.get("repository"), dict)
        and run["repository"].get("full_name") == REPOSITORY
        and isinstance(run.get("head_repository"), dict)
        and run["head_repository"].get("full_name") == REPOSITORY
    )


def _trusted_check(check, name, commit, suite):
    app = check.get("app")
    check_suite = check.get("check_suite")
    return (
        check.get("name") == name
        and check.get("head_sha") == commit
        and isinstance(app, dict)
        and app.get("id") == ACTIONS_APP_ID
        and app.get("slug") == ACTIONS_APP_SLUG
        and isinstance(check_suite, dict)
        and check_suite.get("id") == suite
        and _positive_int(check.get("id"))
    )


def verify_ci(checks, statuses, workflow_runs, expected_commit):
    """Return source-bound CI receipts, or raise CheckError/ChecksPending.

    Workflow IDs are deliberate trust pins. Recreating one of these workflows
    requires a reviewed controller update; another workflow with the same job
    names cannot substitute for it.
    """
    if not isinstance(expected_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise CheckError("Expected CI commit must be a full lowercase SHA")
    check_runs = _rows(checks, "check_runs")
    runs = _rows(workflow_runs, "workflow_runs")
    status_rows = _rows(statuses, "statuses")
    if statuses.get("sha") not in {None, expected_commit}:
        raise CheckError("Commit statuses belong to another source commit")
    if status_rows and statuses.get("sha") != expected_commit:
        raise CheckError("Commit status source SHA is missing")

    evidence = {}
    for name, required in REQUIRED_WORKFLOWS.items():
        relevant = [
            run for run in runs
            if run.get("path") == required["path"] or run.get("workflow_id") == required["id"]
        ]
        if not relevant:
            raise ChecksPending(f"Required {name} workflow has not appeared")
        # A recreated path, unexpected event/fork, or mismatched source must
        # not turn into a harmless-looking success from an earlier run.
        if any(
            run.get("path") != required["path"]
            or run.get("workflow_id") != required["id"]
            or not _canonical_context(run, expected_commit)
            or not _positive_int(run.get("id"))
            or not _positive_int(run.get("check_suite_id"))
            or not _positive_int(run.get("run_attempt", 1))
            for run in relevant
        ):
            raise CheckError(f"Required {name} workflow provenance is not trusted")
        run = max(relevant, key=lambda item: (item["id"], item.get("run_attempt", 1)))
        if run.get("status") != "completed":
            raise ChecksPending(f"Required {name} workflow is unfinished")
        if run.get("conclusion") != "success":
            raise CheckError(f"Required {name} workflow did not succeed")
        candidates = [
            check for check in check_runs
            if _trusted_check(check, name, expected_commit, run["check_suite_id"])
        ]
        if not candidates:
            if any(check.get("name") == name for check in check_runs):
                raise CheckError(f"Required {name} check issuer, source or suite is not trusted")
            raise ChecksPending(f"Required {name} check has not appeared")
        check = max(candidates, key=lambda item: item["id"])
        if check.get("status") != "completed":
            raise ChecksPending(f"Required {name} check is unfinished")
        if check.get("conclusion") != "success":
            raise CheckError(f"Required {name} check did not succeed")
        evidence[name] = {
            "workflow_id": required["id"],
            "workflow_path": required["path"],
            "workflow_run_id": run["id"],
            "run_attempt": run.get("run_attempt", 1),
            "check_suite_id": run["check_suite_id"],
            "check_run_id": check["id"],
            "app_id": ACTIONS_APP_ID,
            "conclusion": "success",
        }

    if status_rows:
        if statuses.get("state") == "pending":
            raise ChecksPending("Commit statuses are unfinished")
        if statuses.get("state") != "success":
            raise CheckError("Commit statuses did not succeed")
    return {
        "repository": REPOSITORY,
        "head_branch": "main",
        "head_sha": expected_commit,
        "event": "push",
        "required_workflows": evidence,
    }
