#!/usr/bin/env python3
"""Prepare reproducible releases and submit reviewed main to Chrome Web Store v2.

No external writes without --github and/or --submit. Google review is asynchronous;
a successful submission is recorded as pending, never reported as a public rollout.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "richardcmckinney/cross-browser-duplicate-tab-guard"
ITEM_ID = "dbfkmbhkefkppdbbiifihekopelgcdgb"
API = "https://chromewebstore.googleapis.com"
ACTIVE_STATES = {"PENDING_REVIEW", "STAGED"}
SECRET_NAMES = {"CWS_ACCESS_TOKEN", "CWS_SERVICE_ACCOUNT_EMAIL"}


class ReleaseError(RuntimeError):
    pass


def run(args, root, *, env=None, check=True):
    try:
        result = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise ReleaseError(f"{Path(args[0]).name} operation timed out") from None
    if check and result.returncode:
        # Do not print subprocess environment, commands, or arbitrary server bodies.
        raise ReleaseError(f"{Path(args[0]).name} operation failed (exit {result.returncode})")
    return result


def git(root, *args):
    return run(["git", *args], root).stdout.strip()


def gh_json(root, *args):
    output = run(["gh", *args], root).stdout
    try:
        return json.loads(output)
    except ValueError as exc:
        raise ReleaseError("GitHub returned invalid JSON") from exc


def verify_source(root, expected_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit):
        raise ReleaseError("--expected-commit must be a full commit SHA")
    if git(root, "rev-parse", "HEAD") != expected_commit:
        raise ReleaseError("HEAD differs from the expected reviewed commit")
    if git(root, "status", "--porcelain", "--untracked-files=normal"):
        raise ReleaseError("Release source must have no uncommitted or untracked files")
    remote = git(root, "remote", "get-url", "origin")
    if remote not in {f"https://github.com/{REPO}.git", f"https://github.com/{REPO}", f"git@github.com:{REPO}.git"}:
        raise ReleaseError("Unexpected origin repository")
    run(["git", "fetch", "--quiet", "origin", "main"], root)
    if git(root, "rev-parse", "origin/main") != expected_commit:
        raise ReleaseError("Expected commit must be the current remote main")
    # Successful checks must apply to this exact commit, not to an earlier PR head.
    checks = gh_json(root, "api", f"repos/{REPO}/commits/{expected_commit}/check-runs?per_page=100")
    statuses = gh_json(root, "api", f"repos/{REPO}/commits/{expected_commit}/status?per_page=100")
    validate_checks(checks, statuses)


def validate_checks(checks, statuses):
    runs = checks.get("check_runs", [])
    if checks.get("total_count", 0) > len(runs) or statuses.get("total_count", 0) > len(statuses.get("statuses", [])):
        raise ReleaseError("Check pagination requires operator review; refusing incomplete evidence")
    # Only the newest run for each check name counts after a rerun.
    newest = {}
    for item in sorted(runs, key=lambda x: x.get("id", 0), reverse=True):
        newest.setdefault(item.get("name"), item)
    required = {"tests", "gitleaks"}
    if not required <= newest.keys() or any(newest[name].get("conclusion") != "success" for name in required):
        raise ReleaseError("Required tests and gitleaks checks must both succeed")
    if any(x.get("status") != "completed" or x.get("conclusion") not in {"success", "neutral", "skipped"} for x in newest.values()):
        raise ReleaseError("CI checks are unfinished or unsuccessful")
    if statuses.get("total_count", 0) and statuses.get("state") != "success":
        raise ReleaseError("Commit status is not successful")


def prepare(root, output):
    sys.path.insert(0, str(root))
    import cross_browser_duplicate_tab_guard as guard
    run([sys.executable, "scripts/sync_embedded_extension.py", "--check"], root)
    archive = guard.build_webstore_package(root / "extension", output, [])
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    version = json.loads((root / "extension/manifest.json").read_text())["version"]
    checksum = output / "SHA256SUMS"
    checksum.write_text(f"{digest}  {archive.name}\n")
    return archive, checksum, version, digest


def run_tests(root, log):
    env = {k: v for k, v in os.environ.items() if k not in SECRET_NAMES | {"GH_TOKEN", "GITHUB_TOKEN"}}
    with log.open("w") as stream:
        try:
            result = subprocess.run(["npm", "test"], cwd=root, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=180)
        except subprocess.TimeoutExpired:
            raise ReleaseError(f"Release tests timed out; inspect {log}") from None
    if result.returncode:
        raise ReleaseError(f"Release tests failed; inspect {log}")


def github_release(root, archive, checksum, version, commit):
    tag = f"v{version}"
    # An API listing avoids treating a transient error as a missing release.
    releases = gh_json(root, "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100")
    release = next((r for page in releases for r in page if r["tag_name"] == tag), None)
    expected = checksum.read_bytes()
    if release:
        assets = {a["name"]: a for a in release.get("assets", [])}
        if not {archive.name, checksum.name} <= assets.keys():
            raise ReleaseError("Existing release is incomplete; repair explicitly without replacing assets")
        with tempfile.TemporaryDirectory() as tmp:
            for name in (checksum.name, archive.name):
                run(["gh", "release", "download", tag, "--repo", REPO, "--pattern", name, "--dir", tmp], root)
            if (Path(tmp) / checksum.name).read_bytes() != expected:
                raise ReleaseError("Version already released with a different package checksum")
            if hashlib.sha256((Path(tmp) / archive.name).read_bytes()).hexdigest() != expected.decode().split()[0]:
                raise ReleaseError("Existing release archive does not match its checksum")
        target = git(root, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
        if not target:
            raise ReleaseError("Existing release has no immutable remote tag")
        refs = [line.split() for line in target.splitlines()]
        tag_commit = next((sha for sha, ref in refs if ref.endswith("^{}")), refs[0][0])
        if release.get("draft") and tag_commit != commit:
            raise ReleaseError("Draft release tag points at another source commit")
        if run(["git", "merge-base", "--is-ancestor", tag_commit, commit], root, check=False).returncode:
            raise ReleaseError("Existing release tag is not reviewed main ancestry")
        if release.get("draft"):
            run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false"], root)
        return {"url": release["html_url"], "source_commit": tag_commit, "reused": True}
    # Refuse a preexisting tag pointing anywhere else. Never rewrite release tags.
    refs = git(root, "ls-remote", "--tags", "origin", f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}")
    if refs:
        lines = [line.split() for line in refs.splitlines()]
        target = next((sha for sha, ref in lines if ref.endswith("^{}")), lines[0][0])
        if target != commit:
            raise ReleaseError("Existing release tag points at another commit")
    if not refs:
        run(["gh", "api", f"repos/{REPO}/git/refs", "-f", f"ref=refs/tags/{tag}", "-f", f"sha={commit}"], root)
    notes = archive.parent / "release-notes.md"
    notes.write_text(f"Version {version}, source commit `{commit}`.\n\n"
                     "The ZIP is the validated native-messaging-only Chrome Web Store package. "
                     "The local companion is required. A GitHub release does not establish store publication.\n\n"
                     f"Install: https://chromewebstore.google.com/detail/{ITEM_ID}\n"
                     f"Setup: https://github.com/{REPO}#installation--setup\n")
    run(["gh", "release", "create", tag, str(archive), str(checksum), "--repo", REPO,
         "--verify-tag", "--title", f"Cross-Browser Duplicate Tab Guard {version}",
         "--notes-file", str(notes), "--draft"], root)
    # Publish only after the immutable assets are present; reruns verify draft assets above.
    run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false"], root)
    return {"url": f"https://github.com/{REPO}/releases/tag/{tag}", "source_commit": commit, "reused": False}


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ChromeStore:
    def __init__(self, publisher, token):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", publisher):
            raise ReleaseError("CWS_PUBLISHER_ID is missing or invalid")
        self.name = f"publishers/{publisher}/items/{ITEM_ID}"
        self.token = token
        self.opener = build_opener(NoRedirects())

    @staticmethod
    def request_json(opener, url, *, data=None, headers=None, method=None):
        request = Request(url, data=data, headers=headers or {}, method=method)
        try:
            with opener.open(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as exc:
            # OAuth and server responses may contain sensitive request metadata.
            raise ReleaseError(f"Chrome Web Store request failed (HTTP {exc.code}); inspect the dashboard") from None
        except (URLError, ValueError, TimeoutError, OSError):
            raise ReleaseError("Chrome Web Store request failed; retry using the saved release receipt") from None

    @classmethod
    def from_environment(cls):
        publisher = os.environ.get("CWS_PUBLISHER_ID", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", publisher):
            raise ReleaseError("CWS_PUBLISHER_ID is missing or invalid")
        token = os.environ.get("CWS_ACCESS_TOKEN", "")
        if not token:
            service_account = os.environ.get("CWS_SERVICE_ACCOUNT_EMAIL", "")
            if not re.fullmatch(r"[A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+\.iam\.gserviceaccount\.com", service_account):
                raise ReleaseError("CWS_SERVICE_ACCOUNT_EMAIL is missing or invalid")
            token = run(["gcloud", "auth", "print-access-token", "--quiet",
                         "--account=" + os.environ.get("CWS_GCLOUD_ACCOUNT", "richard@venturi.systems"),
                         "--impersonate-service-account=" + service_account,
                         "--scopes=https://www.googleapis.com/auth/chromewebstore"], Path.cwd()).stdout.strip()
        if not token:
            raise ReleaseError("Chrome Web Store impersonation returned no access token")
        return cls(publisher, token)

    def request(self, action, *, data=None, upload=False):
        url = f"{API}/{'upload/' if upload else ''}v2/{self.name}:{action}"
        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = "application/zip" if upload else "application/json"
        return self.request_json(self.opener, url, data=data, headers=headers)

    def status(self):
        return self.request("fetchStatus")

    def submit(self, archive, version, journal):
        status = self.status()
        outcome = store_outcome(status, version)
        if outcome != "upload_required":
            return proven_store_outcome(outcome, version, hashlib.sha256(archive.read_bytes()).hexdigest(), journal), status
        if journal.exists():
            previous = json.loads(journal.read_text())
            if previous.get("phase") != "confirmed":
                raise ReleaseError("Previous upload/submission outcome is ambiguous; reconcile the dashboard and journal before retrying")
        record = {"version": version, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "phase": "upload_intent"}
        save_receipt(journal, record)
        response = self.request("upload", data=archive.read_bytes(), upload=True)
        state = response.get("uploadState")
        if state not in {"SUCCEEDED", "IN_PROGRESS", "UPLOAD_SUCCESS", "UPLOAD_IN_PROGRESS"}:
            raise ReleaseError("Chrome Web Store package upload did not succeed")
        if state in {"IN_PROGRESS", "UPLOAD_IN_PROGRESS"}:
            for _ in range(6):
                time.sleep(5)
                state = self.status().get("lastAsyncUploadState")
                if state == "SUCCEEDED":
                    break
                if state not in {"IN_PROGRESS", "UPLOAD_IN_PROGRESS"}:
                    raise ReleaseError("Chrome Web Store asynchronous upload failed")
            else:
                raise ReleaseError("Chrome Web Store upload is still processing; retry later")
        if response.get("crxVersion") != version:
            raise ReleaseError("Chrome Web Store did not confirm the uploaded version; reconcile the saved journal before publication")
        record["phase"] = "publish_intent"
        save_receipt(journal, record)
        response = self.request("publish", data=json.dumps({"publishType": "DEFAULT_PUBLISH", "skipReview": False,
                                                          "blockOnWarnings": True}).encode())
        # Re-fetch authoritative revision version and state rather than interpreting HTTP 200 as published.
        status = self.status()
        outcome = store_outcome(status, version)
        if outcome == "upload_required":
            raise ReleaseError("Submission was not confirmed in Chrome Web Store status; retry later")
        record["phase"] = "confirmed"
        record["outcome"] = outcome
        save_receipt(journal, record)
        return outcome, status


def proven_store_outcome(outcome, version, digest, journal):
    if journal.exists():
        record = json.loads(journal.read_text())
        if record.get("version") == version and record.get("sha256") == digest and record.get("phase") in {"publish_intent", "confirmed"}:
            return outcome
    return "existing_" + outcome + "_unverified"


def revision_versions(revision):
    return {channel.get("crxVersion") for channel in revision.get("distributionChannels", [])}


def store_outcome(status, version):
    if status.get("takenDown") or status.get("warned"):
        raise ReleaseError("Chrome Web Store policy warning/takedown requires dashboard resolution")
    submitted = status.get("submittedItemRevisionStatus", {})
    published = status.get("publishedItemRevisionStatus", {})
    if submitted.get("state") in ACTIVE_STATES:
        if version in revision_versions(submitted):
            return "pending_review" if submitted["state"] == "PENDING_REVIEW" else "staged"
        raise ReleaseError("Another version has an active review; refusing to replace or cancel it")
    if submitted.get("state") in {"REJECTED", "CANCELLED"}:
        raise ReleaseError("A rejected or cancelled submission requires dashboard review before retrying")
    if version in revision_versions(published) and published.get("state") == "PUBLISHED":
        if any(c.get("crxVersion") == version and c.get("deployPercentage") != 100 for c in published.get("distributionChannels", [])):
            return "partial_rollout"
        return "published"
    # Never roll an already published newer version back to the requested version.
    desired = tuple(int(x) for x in version.split("."))
    for current in revision_versions(published) - {None}:
        actual = tuple(int(x) for x in current.split("."))
        length = max(len(actual), len(desired))
        if actual + (0,) * (length-len(actual)) > desired + (0,) * (length-len(desired)):
            raise ReleaseError("Store already has a newer version; refusing downgrade")
    if status.get("lastAsyncUploadState") in {"IN_PROGRESS", "UPLOAD_IN_PROGRESS"}:
        raise ReleaseError("An existing store upload is processing; retry after it finishes")
    return "upload_required"


class ListingParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.text = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden and data.strip():
            self.text.append(data.strip())


def parse_public_listing(html, version):
    parser = ListingParser()
    parser.feed(html)
    public_version = None
    for index, token in enumerate(parser.text[:-1]):
        if token == "Version" and re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", parser.text[index + 1]):
            public_version = parser.text[index + 1]
            break
    available = "Add to Chrome" in parser.text
    title = any("Cross-Browser Duplicate Tab Guard" in token for token in parser.text)
    return {"public_version": public_version, "public_install_available": available,
            "public_verified": title and available and public_version == version}


def add_public_evidence(receipt):
    if receipt["store"] not in {"published", "partial_rollout", "existing_published_unverified", "existing_partial_rollout_unverified"}:
        return
    try:
        request = Request(f"https://chromewebstore.google.com/detail/{ITEM_ID}?hl=en", headers={"User-Agent": "Mozilla/5.0"})
        with build_opener().open(request, timeout=15) as response:
            evidence = parse_public_listing(response.read().decode("utf-8", errors="replace"), receipt["version"])
    except (URLError, TimeoutError, OSError):
        evidence = {"public_version": None, "public_install_available": False, "public_verified": False}
    receipt.update(evidence)
    if receipt["store"] == "published" and not evidence["public_verified"]:
        receipt["store"] = "pending_propagation"


def save_receipt(path, receipt):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    temporary.chmod(0o600)
    temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--expected-commit")
    parser.add_argument("--status-only", "--status", action="store_true", help="Reconcile an existing receipt without upload, tests or GitHub writes")
    parser.add_argument("--github", action="store_true", help="Create or verify immutable GitHub release")
    parser.add_argument("--submit", action="store_true", help="Upload and submit through Chrome Web Store v2")
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock_path = Path(os.environ.get("CWS_RELEASE_LOCK", str(Path.home() / ".local/state/duplicate-tab-guard-release/release.lock")))
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if args.status_only:
                if args.github or args.submit:
                    raise ReleaseError("--status-only cannot be combined with write options")
                receipt_path = output / "receipt.json"
                receipt = json.loads(receipt_path.read_text())
                store = ChromeStore.from_environment()
                receipt["store_status"] = store.status()
                outcome = store_outcome(receipt["store_status"], receipt["version"])
                receipt["store"] = outcome if outcome == "upload_required" else proven_store_outcome(outcome, receipt["version"], receipt["sha256"], output / "cws-upload.json")
                add_public_evidence(receipt)
                save_receipt(receipt_path, receipt)
                print(json.dumps(receipt, sort_keys=True))
                return 3 if receipt["store"] == "upload_required" else 0
            if args.github or args.submit:
                if not args.expected_commit:
                    raise ReleaseError("External release writes require --expected-commit")
                verify_source(root, args.expected_commit)
            archive, checksum, version, digest = prepare(root, output)
            receipt = {"version": version, "sha256": digest, "commit": git(root, "rev-parse", "HEAD"),
                       "archive": archive.name, "store": "not_submitted", "github": "not_released"}
            if args.github or args.submit:
                run_tests(root, output / "tests.log")
                verify_source(root, args.expected_commit)
                if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                    raise ReleaseError("Package changed during release validation")
            # Check auth before creating an externally visible release when both operations requested.
            store = ChromeStore.from_environment() if args.submit else None
            if args.github:
                receipt["github"] = github_release(root, archive, checksum, version, args.expected_commit)
                save_receipt(output / "receipt.json", receipt)
            if store:
                receipt["store"], receipt["store_status"] = store.submit(archive, version, output / "cws-upload.json")
            add_public_evidence(receipt)
            save_receipt(output / "receipt.json", receipt)
            print(json.dumps(receipt, sort_keys=True))
            return 0
        except (ReleaseError, OSError, ValueError) as exc:
            print(f"Release blocked: {exc}", file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
