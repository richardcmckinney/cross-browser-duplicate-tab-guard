#!/usr/bin/env python3
"""Prepare reproducible releases and submit reviewed main to Chrome Web Store v2.

No external writes without --github and/or --submit. Google review is asynchronous;
a successful submission is recorded as pending, never reported as a public rollout.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime, timezone
import fcntl
import hashlib
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPO = "richardcmckinney/cross-browser-duplicate-tab-guard"
ITEM_ID = "dbfkmbhkefkppdbbiifihekopelgcdgb"
API = "https://chromewebstore.googleapis.com"
ACTIVE_STATES = {"PENDING_REVIEW", "STAGED"}
SECRET_NAMES = {"CWS_ACCESS_TOKEN", "CWS_SERVICE_ACCOUNT_EMAIL"}
MAX_BLOB_BYTES = 8 * 1024 * 1024
MAX_TREE_BYTES = 32 * 1024 * 1024
MAX_TREE_FILES = 128
APP_SLUG = "cross-browser-duplicate-tab-guard"
LEGACY_ITEM_NAME = "publishers/941c144b-6b41-435e-8089-b2c59ed3fb4e/items/dbfkmbhkefkppdbbiifihekopelgcdgb"
LEGACY_RELEASE_COMMIT = "032205838a2a81148643dd43a5420ab6ae9b9c51"
LEGACY_RELEASE_VERSION = "2.4.2"
LEGACY_RELEASE_SHA256 = "aee800cac0ed50b874da072c9a7d459b8f4b792dbb854feb36701ed1a83cca74"



class ReleaseError(RuntimeError):
    pass


# -I is used by the service; only the complete owner-controlled pinned bundle is imported.
BUNDLE_DIR = Path(__file__).resolve().parent
for bundle_path in [BUNDLE_DIR, *(BUNDLE_DIR / name for name in ("release_extension.py", "release_checks.py", "release_packager.py", "verify_store_package.py"))]:
    try:
        info = bundle_path.lstat()
    except FileNotFoundError:
        raise ReleaseError("Publisher bundle is incomplete") from None
    correct_type = stat.S_ISDIR(info.st_mode) if bundle_path == BUNDLE_DIR else stat.S_ISREG(info.st_mode)
    if not correct_type or info.st_uid != os.getuid() or info.st_mode & 0o022 or (bundle_path != BUNDLE_DIR and info.st_nlink != 1):
        raise ReleaseError("Publisher bundle must be owner-controlled regular files in a safe directory")
sys.path.insert(0, str(BUNDLE_DIR))
from release_checks import CheckError, ChecksPending, verify_ci
from release_packager import PackageError, package_bytes, _safe_webstore_path, WEBSTORE_FILE_SUFFIXES, WEBSTORE_IGNORED_NAMES, WEBSTORE_SOURCE_ONLY_FILES

def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run(args, root, *, env=None, check=True):
    try:
        result = subprocess.run(args, cwd=root, env=env, text=True, capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise ReleaseError(f"{Path(args[0]).name} operation timed out") from None
    if check and result.returncode:
        # Do not print subprocess environment, commands, or arbitrary server bodies.
        raise ReleaseError(f"{Path(args[0]).name} operation failed (exit {result.returncode})")
    return result


def _git_process(root, *args, binary=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_") and k not in SECRET_NAMES | {"GH_TOKEN", "GITHUB_TOKEN"}}
    env.update({"GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_SYSTEM": "/dev/null", "GIT_CONFIG_GLOBAL": "/dev/null"})
    command = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
               "-c", "credential.helper=", "-c", "protocol.ext.allow=never", "-c", "protocol.file.allow=never", *args]
    try:
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=not binary, timeout=60)
    except subprocess.TimeoutExpired:
        raise ReleaseError("Git data operation timed out") from None
    if result.returncode:
        raise ReleaseError("Git data operation failed")
    return result.stdout if binary else result.stdout.strip()


def git(root, *args):
    return _git_process(root, *args)


def gh_json(root, *args):
    output = run(["gh", *args], root).stdout
    try:
        return json.loads(output)
    except ValueError as exc:
        raise ReleaseError("GitHub returned invalid JSON") from exc


def verify_source(root, expected_commit):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_commit or ""):
        raise ReleaseError("--expected-commit must be a full commit SHA")
    root = Path(root).absolute()
    info = root.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ReleaseError("Source object store must be an owner-controlled real directory")
    remote = git(root, "remote", "get-url", "origin")
    if remote not in {f"https://github.com/{REPO}.git", f"https://github.com/{REPO}"}:
        raise ReleaseError("Unexpected origin repository")
    # Explicit refspec: a stale or locally reconfigured origin/main is not source evidence.
    git(root, "fetch", "--quiet", "origin", "refs/heads/main:refs/remotes/origin/main")
    if git(root, "rev-parse", "origin/main") != expected_commit or git(root, "rev-parse", "FETCH_HEAD") != expected_commit:
        raise ReleaseError("Expected commit must be freshly fetched remote main")
    checks = gh_json(root, "api", f"repos/{REPO}/commits/{expected_commit}/check-runs?per_page=100")
    statuses = gh_json(root, "api", f"repos/{REPO}/commits/{expected_commit}/status?per_page=100")
    runs = gh_json(root, "api", f"repos/{REPO}/actions/runs?head_sha={expected_commit}&event=push&branch=main&per_page=100")
    return verify_ci(checks, statuses, runs, expected_commit)




def _source_blobs(root, commit):
    """Read only immutable regular Git blobs; no worktree or candidate code is executed."""
    raw = _git_process(root, "ls-tree", "-rlz", commit, "--", "extension", "cross_browser_duplicate_tab_guard.py", binary=True)
    records = [record for record in raw.split(b"\0") if record]
    if len(records) > MAX_TREE_FILES:
        raise ReleaseError("Release source contains too many files")
    files, companion, total, folded = {}, None, 0, set()
    for record in records:
        try:
            metadata, encoded_path = record.split(b"\t", 1)
            mode, object_type, oid, size = metadata.split()
            path = encoded_path.decode("utf-8")
            length = int(size)
        except (ValueError, UnicodeDecodeError):
            raise ReleaseError("Invalid Git tree entry") from None
        if mode not in {b"100644", b"100755"} or object_type != b"blob" or not re.fullmatch(rb"[0-9a-f]{40}", oid):
            raise ReleaseError("Release source must contain regular blobs, never symlinks or gitlinks")
        if length < 0 or length > MAX_BLOB_BYTES:
            raise ReleaseError("Release source blob exceeds its size limit")
        total += length
        if total > MAX_TREE_BYTES:
            raise ReleaseError("Release source exceeds its total size limit")
        data = _git_process(root, "cat-file", "blob", oid.decode("ascii"), binary=True)
        if len(data) != length:
            raise ReleaseError("Git blob length differs from its tree metadata")
        if path == "cross_browser_duplicate_tab_guard.py":
            companion = data
            continue
        if not path.startswith("extension/"):
            raise ReleaseError("Unexpected release source path")
        rel = path[len("extension/"):]
        if not _safe_webstore_path(rel) or rel.casefold() in folded:
            raise ReleaseError("Unsafe or duplicate release asset path")
        folded.add(rel.casefold())
        if rel in WEBSTORE_SOURCE_ONLY_FILES or Path(rel).name in WEBSTORE_IGNORED_NAMES:
            continue
        if Path(rel).suffix.lower() not in WEBSTORE_FILE_SUFFIXES:
            raise ReleaseError("Unsupported release asset type")
        files[rel] = data
    if companion is None or "manifest.json" not in files:
        raise ReleaseError("Release source is missing the manifest or companion")
    return files, companion


def _verify_embedded_assets(files, companion):
    """Inspect literal declarations as data instead of importing the candidate installer."""
    try:
        tree = ast.parse(companion.decode("utf-8"))
        values = {}
        for node in tree.body:
            name = None
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
            elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
            if name in {"EXTENSION_FILES", "EXTENSION_BINARY_FILES", "APP_VERSION"}:
                if name in values:
                    raise ValueError("Duplicate embedded declaration")
                values[name] = ast.literal_eval(node.value)
        text = {name: data.decode("utf-8") for name, data in files.items() if Path(name).suffix.lower() in {".css", ".html", ".js", ".json"}}
        binary = {name: base64.b64encode(data).decode("ascii") for name, data in files.items() if Path(name).suffix.lower() == ".png"}
        if values.get("EXTENSION_FILES") != text or values.get("EXTENSION_BINARY_FILES") != binary:
            raise ValueError("Embedded asset drift")
        version = json.loads(files["manifest.json"])["version"]
        if values.get("APP_VERSION") != version:
            raise ValueError("Companion version drift")
    except (ValueError, TypeError, KeyError, UnicodeDecodeError, SyntaxError, RecursionError):
        raise ReleaseError("Embedded assets/version do not match the immutable extension source") from None


def prepare(root, output, commit=None):
    commit = commit or git(root, "rev-parse", "HEAD")
    files, companion = _source_blobs(root, commit)
    _verify_embedded_assets(files, companion)
    version, payload = package_bytes(files)
    archive = output / f"{APP_SLUG}-v{version}-webstore.zip"
    atomic_write(archive, payload)
    digest = hashlib.sha256(payload).hexdigest()
    checksum = output / "SHA256SUMS"
    atomic_write(checksum, f"{digest}  {archive.name}\n".encode())
    return archive, checksum, version, digest




def require_immutable_releases(root):
    policy = gh_json(root, "api", f"repos/{REPO}/immutable-releases")
    if not isinstance(policy, dict) or policy.get("enabled") is not True:
        raise ReleaseError("Repository immutable releases policy must be enabled")


def github_release(root, archive, checksum, version, commit):
    tag = f"v{version}"
    # An API listing avoids treating a transient error as a missing release.
    releases = gh_json(root, "api", "--paginate", "--slurp", f"repos/{REPO}/releases?per_page=100")
    release = next((r for page in releases for r in page if r["tag_name"] == tag), None)
    expected = checksum.read_bytes()
    if release:
        legacy = version == LEGACY_RELEASE_VERSION and hashlib.sha256(archive.read_bytes()).hexdigest() == LEGACY_RELEASE_SHA256
        if not release.get("draft") and not release.get("immutable") and not legacy:
            raise ReleaseError("Existing published release lacks provider immutability")
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
        if not release.get("immutable") and legacy and tag_commit != LEGACY_RELEASE_COMMIT:
            raise ReleaseError("Known pre-policy release tag differs from its pinned reviewed source")
        if release.get("draft") and tag_commit != commit:
            raise ReleaseError("Draft release tag points at another source commit")
        if run(["git", "merge-base", "--is-ancestor", tag_commit, commit], root, check=False).returncode:
            raise ReleaseError("Existing release tag is not reviewed main ancestry")
        if release.get("draft"):
            require_immutable_releases(root)
            run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false"], root)
            release = gh_json(root, "api", f"repos/{REPO}/releases/tags/{tag}")
            if release.get("draft") or not release.get("immutable"):
                raise ReleaseError("GitHub did not confirm immutable release publication")
        return {"url": release["html_url"], "source_commit": tag_commit, "reused": True, "immutable": bool(release.get("immutable"))}
    require_immutable_releases(root)
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
    release = gh_json(root, "api", f"repos/{REPO}/releases/tags/{tag}")
    if release.get("draft") or not release.get("immutable"):
        raise ReleaseError("GitHub did not confirm immutable release publication")
    return {"url": release["html_url"], "source_commit": commit, "reused": False, "immutable": True}


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
            return proven_store_outcome(outcome, version, hashlib.sha256(archive.read_bytes()).hexdigest(), journal, self.name), status
        if journal.exists():
            previous = read_receipt(journal)
            if previous.get("phase") != "confirmed":
                raise ReleaseError("Previous upload/submission outcome is ambiguous; reconcile the dashboard and journal before retrying")
            if previous.get("version") == version:
                raise ReleaseError("Previously confirmed revision is no longer active; reconcile a possible rollback before any new upload")
        record = {"version": version, "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "phase": "upload_intent", "item_name": self.name, "created_at": utc_now()}
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


def proven_store_outcome(outcome, version, digest, journal, item_name):
    if journal.exists():
        record = read_receipt(journal)
        if record.get("version") == version and record.get("sha256") == digest and record.get("phase") in {"publish_intent", "confirmed"}:
            if record.get("item_name") == item_name:
                return outcome
            # One known pre-hardening confirmed receipt is migrated without an upstream write.
            if (record.get("item_name") is None and record.get("phase") == "confirmed"
                    and item_name == LEGACY_ITEM_NAME and version == LEGACY_RELEASE_VERSION and digest == LEGACY_RELEASE_SHA256):
                record["item_name"] = item_name
                record["migrated_at"] = utc_now()
                save_receipt(journal, record)
                return outcome
    return "existing_" + outcome + "_unverified"


def revision_versions(revision):
    channels = revision.get("distributionChannels", [])
    if not isinstance(channels, list):
        raise ReleaseError("Invalid Chrome Web Store distribution channels")
    versions = set()
    for channel in channels:
        if not isinstance(channel, dict) or not isinstance(channel.get("crxVersion"), str):
            raise ReleaseError("Invalid Chrome Web Store revision version")
        if not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}", channel["crxVersion"]):
            raise ReleaseError("Invalid Chrome Web Store revision version")
        percentage = channel.get("deployPercentage")
        if type(percentage) is not int or not 0 <= percentage <= 100:
            raise ReleaseError("Invalid Chrome Web Store deployment percentage")
        versions.add(channel["crxVersion"])
    return versions


def store_outcome(status, version):
    if not isinstance(status, dict):
        raise ReleaseError("Invalid Chrome Web Store status response")
    if status.get("takenDown") or status.get("warned"):
        raise ReleaseError("Chrome Web Store policy warning/takedown requires dashboard resolution")
    submitted = status.get("submittedItemRevisionStatus", {})
    published = status.get("publishedItemRevisionStatus", {})
    known = {"PENDING_REVIEW", "STAGED", "PUBLISHED", "PUBLISHED_TO_TESTERS", "REJECTED", "CANCELLED"}
    if not isinstance(submitted, dict) or not isinstance(published, dict):
        raise ReleaseError("Chrome Web Store returned invalid revision status")
    for revision in (submitted, published):
        if revision and revision.get("state") not in known:
            raise ReleaseError("Unrecognized Chrome Web Store item state; refusing upload")
        if revision.get("state") == "PUBLISHED_TO_TESTERS":
            raise ReleaseError("Item is tester-only; public publication needs explicit dashboard resolution")
    if published and (published.get("state") != "PUBLISHED" or not revision_versions(published)):
        raise ReleaseError("Unconfirmed published revision status; refusing upload")
    if submitted.get("state") == "PUBLISHED" and version not in revision_versions(published):
        if version in revision_versions(submitted):
            return "pending_publication"
        raise ReleaseError("Another revision is still being published")
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
    # These describe the current observation, not a historical successful proof.
    receipt.update(public_version=None, public_install_available=False, public_verified=False, store_payload_verified=False)
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


def private_directory(path):
    """Reject symlink components and writable ancestors; require a private owned leaf."""
    path = Path(os.path.abspath(path))
    chain = [path, *path.parents]
    for component in reversed(chain):
        try:
            info = component.lstat()
        except FileNotFoundError:
            component.mkdir(mode=0o700)
            info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ReleaseError("Release state paths must be real directories")
        if info.st_mode & 0o022 and not (info.st_mode & stat.S_ISVTX and info.st_uid == 0):
            raise ReleaseError("Release state has a writable ancestor")
    info = path.lstat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ReleaseError("Release state directory must be privately owned (0700)")
    return path


def _file_info(info, *, private=True):
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise ReleaseError("Release state files must be owned regular files with one link")
    if private and info.st_mode & 0o077:
        raise ReleaseError("Release state file must be private (0600)")


def read_receipt(path):
    path = Path(path)
    private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    with os.fdopen(fd, "r") as stream:
        _file_info(os.fstat(stream.fileno()))
        result = json.load(stream)
    if not isinstance(result, dict):
        raise ReleaseError("Invalid release receipt")
    return result


def atomic_write(path, data):
    """Durably persist bytes before remote writes: private temp, fsync, replace, dir fsync."""
    path = Path(path)
    private_directory(path.parent)
    try:
        _file_info(path.lstat())
    except FileNotFoundError:
        pass
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def save_receipt(path, receipt):
    atomic_write(Path(path), (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode())


def release_lock(path):
    path = Path(path)
    private_directory(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _file_info(os.fstat(fd), private=False)
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        os.close(fd)
        raise
    return os.fdopen(fd, "r+")


def _receipt_archive(receipt, output):
    version = receipt.get("version", "")
    if not re.fullmatch(r"[0-9]{1,5}(?:\.[0-9]{1,5}){0,3}", version):
        raise ReleaseError("Invalid release receipt version")
    name = f"{APP_SLUG}-v{version}-webstore.zip"
    if receipt.get("archive") != name or not re.fullmatch(r"[0-9a-f]{64}", receipt.get("sha256", "")):
        raise ReleaseError("Invalid release receipt archive identity")
    path = output / name
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        _file_info(os.fstat(stream.fileno()))
        digest = hashlib.sha256(stream.read()).hexdigest()
    if digest != receipt["sha256"]:
        raise ReleaseError("Stored upload archive digest differs from its release receipt")
    return path


def add_store_payload_evidence(receipt, output):
    if receipt.get("store") not in {"published", "existing_published_unverified"} or not receipt.get("public_verified"):
        return
    from verify_store_package import VERIFIER_POLICY_VERSION, VerificationError, verify_published_package
    archive = _receipt_archive(receipt, output)
    proof = receipt.get("store_payload", {})
    if not (proof.get("store_payload_verified") is True and proof.get("policy_version") == VERIFIER_POLICY_VERSION
            and proof.get("item_id") == ITEM_ID and proof.get("version") == receipt["version"]
            and proof.get("upload_sha256") == receipt["sha256"]):
        try:
            proof = verify_published_package(archive, receipt["version"], ITEM_ID)
        except VerificationError:
            receipt["store_payload_verified"] = False
            receipt["store"] = "pending_store_payload_verification"
            return
        receipt["store_payload"] = proof
    receipt["store_payload_verified"] = bool(proof.get("store_payload_verified"))
    if receipt["store_payload_verified"]:
        receipt["store"] = "published"


def print_summary(receipt):
    fields = ("version", "store", "checked_at", "public_version", "public_verified", "store_payload_verified")
    print(json.dumps({key: receipt.get(key) for key in fields}, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="Canonical repository object store; candidate code is never executed")
    parser.add_argument("--output-dir", type=Path, default=Path("dist/release"))
    parser.add_argument("--expected-commit")
    parser.add_argument("--status-only", "--status", action="store_true")
    parser.add_argument("--verify-source-only", action="store_true", help="Verify current main and exact CI provenance without packaging or publishing")
    parser.add_argument("--github", action="store_true")
    parser.add_argument("--submit", action="store_true")
    args = parser.parse_args(argv)
    if (args.status_only or args.verify_source_only) and (args.github or args.submit) or args.status_only and args.verify_source_only:
        parser.error("Read-only verification modes cannot be combined with release writes")
    root = args.source_dir.absolute()
    try:
        if args.verify_source_only:
            proof = verify_source(root, args.expected_commit)
            print(json.dumps({"source_verified": args.expected_commit, "ci_evidence": proof}, sort_keys=True))
            return 0
        output = private_directory(args.output_dir.absolute())
        lock_path = Path(os.environ.get("CWS_RELEASE_LOCK", str(Path.home() / ".local/state/duplicate-tab-guard-release/release.lock")))
        with release_lock(lock_path):
            if args.status_only:
                receipt = read_receipt(output / "receipt.json")
                _receipt_archive(receipt, output)
                store = ChromeStore.from_environment()
                receipt["store_status"] = store.status()
                outcome = store_outcome(receipt["store_status"], receipt["version"])
                receipt["store"] = outcome if outcome == "upload_required" else proven_store_outcome(outcome, receipt["version"], receipt["sha256"], output / "cws-upload.json", store.name)
                receipt["checked_at"] = utc_now()
                add_public_evidence(receipt)
                add_store_payload_evidence(receipt, output)
                save_receipt(output / "receipt.json", receipt)
                print_summary(receipt)
                return 3 if receipt["store"] == "upload_required" else 0
            proof = None
            if args.github or args.submit:
                if not args.expected_commit:
                    raise ReleaseError("External release writes require --expected-commit")
                proof = verify_source(root, args.expected_commit)
            commit = args.expected_commit or git(root, "rev-parse", "HEAD")
            archive, checksum, version, digest = prepare(root, output, commit)
            receipt = {"version": version, "sha256": digest, "commit": commit, "archive": archive.name,
                       "store": "not_submitted", "github": "not_released", "checked_at": utc_now()}
            if (output / "receipt.json").exists():
                previous = read_receipt(output / "receipt.json")
                if previous.get("version") == version and previous.get("sha256") == digest and isinstance(previous.get("store_payload"), dict):
                    receipt["store_payload"] = previous["store_payload"]
            if args.github or args.submit:
                proof = verify_source(root, commit)
                if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
                    raise ReleaseError("Package changed during release validation")
                receipt["ci_evidence"] = proof
            store = ChromeStore.from_environment() if args.submit else None
            if args.github:
                receipt["github"] = github_release(root, archive, checksum, version, commit)
                save_receipt(output / "receipt.json", receipt)
            if store:
                receipt["store"], receipt["store_status"] = store.submit(archive, version, output / "cws-upload.json")
            receipt["checked_at"] = utc_now()
            add_public_evidence(receipt)
            add_store_payload_evidence(receipt, output)
            save_receipt(output / "receipt.json", receipt)
            print_summary(receipt)
            return 0
    except ChecksPending:
        print("Release deferred: current main CI evidence is pending", file=sys.stderr)
        return 2
    except BlockingIOError:
        print("Release deferred: another publisher holds the lock", file=sys.stderr)
        return 75
    except (ReleaseError, CheckError, PackageError, OSError, ValueError, RecursionError) as exc:
        print(f"Release blocked: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
