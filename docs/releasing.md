# Releasing

A merge, a prepared ZIP, a GitHub release, a Google review submission, public availability and a verified store payload are separate states. Record each accurately.

## Prepare changes

- Review source, permissions, data handling and [listing copy](store_listing.md). Increase the extension manifest and companion version when changing package contents.
- After editing extension assets, run `python3 scripts/sync_embedded_extension.py` and its `--check` mode.
- Run the development suites on VSH with `npm ci`, the matching Playwright browser and `npm test`. These are explicitly invoked development tests; the production publishing service does not execute repository tests or dependency installers.
- Build the development artifact with `python3 scripts/package_extension.py`. Inspect the native-only ZIP and record its version, source commit and SHA-256.
- Exercise duplicate resolution, pause/resume, exceptions and disconnect/reconnect in an isolated profile. An unpacked test using a development extension ID does not establish store-installed compatibility.

## Publishing trust boundary

The canonical repository is the personal project `richardcmckinney/cross-browser-duplicate-tab-guard`. Release work runs on VSH.

The timer invokes an installed, reviewed controller bundle. It never imports candidate companion code, checks out candidate worktree files, runs candidate Python/npm commands or installs candidate dependencies. Its packager reads regular Git blobs from the exact current remote `main` commit and treats them as data. It rejects symlinks, gitlinks, unsafe or ambiguous paths, oversized input, embedded-asset/version drift and permissions outside the installed policy, including optional permissions.

| Installed component | Responsibility |
| --- | --- |
| `release_extension.py` | Source verification, release state, provider calls and durable receipts |
| `release_checks.py` | Exact GitHub Actions workflow and check provenance |
| `release_packager.py` | Pinned data-only manifest/worker validation and deterministic ZIP creation |
| `verify_store_package.py` | Signed Google CRX3 identity and runtime-content verification |

Changing the installed packaging policy, workflow trust pins or controller code requires a deliberate installation from a reviewed merge commit. Fetching a newer application commit does not update the publisher itself. This boundary avoids running fetched repository code with the publisher's cached credentials; it is not a claim that ordinary developer shells are credential-isolated sandboxes.

The existing `tests` and `gitleaks` workflows remain the CI evidence; no additional hosted Actions or CodeBuild job is introduced. The controller binds successful evidence to the exact source SHA, canonical repository, `push` event on `main`, GitHub Actions issuer, pinned workflow ID/path, check suite and latest applicable run/check. Missing or unfinished required evidence defers publishing. Recreated workflows require a reviewed trust-pin update.

## Credentials and VSH scheduling

Use the existing linked [Chrome Web Store service account](https://developer.chrome.com/docs/webstore/service-accounts) through keyless impersonation. Configure a privately owned mode-0600 `~/.config/duplicate-tab-guard-release.env` outside the repository:

```ini
CWS_PUBLISHER_ID=your-publisher-id
CWS_SERVICE_ACCOUNT_EMAIL=your-linked-publisher@your-project.iam.gserviceaccount.com
CWS_GCLOUD_ACCOUNT=your-existing-google-account
```

The source Google account is explicitly selected; its default is `richard@venturi.systems`. It needs permission to impersonate the linked service account. A controlled invocation may instead use an existing short-lived `CWS_ACCESS_TOKEN`. Never commit credentials, print tokens, or create/rotate keys to make a release work.

The user service keeps `NoNewPrivileges=true`, a private umask and noninteractive Git/GitHub CLI settings. VSH's user manager has lingering enabled so the timer continues after logout. Verify `loginctl show-user "$USER" -p Linger` reports `Linger=yes`.

The monotonic timer checks every two minutes after service completion, with one-second timer accuracy and no added random delay. Existing release status is refreshed through the pinned controller before checking newer source/CI, so a later CI failure cannot prevent tracking an already submitted version. Normal logs contain a compact timestamped summary; detailed receipts remain private.

## Install a reviewed controller update

Perform this on VSH only, after the selected merge commit's required CI has passed. Stop the timer and allow any running publishing invocation to finish; do not interrupt an upload or publication call.

Choose the reviewed full merge SHA explicitly:

```bash
release_commit=REVIEWED_FULL_MERGE_SHA
release_stage=$(mktemp -d)
release_bundle="$HOME/.local/libexec/duplicate-tab-guard-release-bundles/$release_commit"
release_link="$HOME/.local/libexec/duplicate-tab-guard-release"

for release_file in release_extension.py release_checks.py release_packager.py verify_store_package.py; do
  git show "$release_commit:scripts/$release_file" > "$release_stage/$release_file"
done
git show "$release_commit:deploy/vsh-release-runner.sh" > "$release_stage/runner"
git show "$release_commit:deploy/duplicate-tab-guard-release.service" > "$release_stage/release.service"
git show "$release_commit:deploy/duplicate-tab-guard-release.timer" > "$release_stage/release.timer"

# This source has already been reviewed; verify its exact main/CI provenance.
python3 -I -B "$release_stage/release_extension.py" --verify-source-only \
  --source-dir "$PWD" --expected-commit "$release_commit"

install -d -m700 "$HOME/.local/libexec/duplicate-tab-guard-release-bundles"
test ! -e "$release_bundle"
install -d -m700 "$release_bundle"
install -m600 "$release_stage/"*.py "$release_bundle/"
test ! -e "$release_link" || test -L "$release_link"
ln -s "$release_bundle" "$release_link.next-$release_commit"
mv -Tf "$release_link.next-$release_commit" "$release_link"

install -Dm755 "$release_stage/runner" "$HOME/.local/libexec/duplicate-tab-guard-release-runner"
install -Dm644 "$release_stage/release.service" "$HOME/.config/systemd/user/duplicate-tab-guard-release.service"
install -Dm644 "$release_stage/release.timer" "$HOME/.config/systemd/user/duplicate-tab-guard-release.timer"

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
systemctl --user daemon-reload
systemctl --user enable --now duplicate-tab-guard-release.timer
systemctl --user start duplicate-tab-guard-release.service
```

All four helpers are required. The versioned bundle and atomic link prevent mixed controller versions. Preserve the previous reviewed bundle for deliberate recovery. The runner/source/receipts live under `~/.local/state/duplicate-tab-guard-release/`; these directories must be owned privately, without symlinked state paths. The application worktree is not package authority.

## Immutable GitHub releases

[GitHub release immutability](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases) is enabled for future releases. The controller creates a draft, attaches the ZIP and `SHA256SUMS`, then publishes and verifies GitHub's immutable flag. GitHub protects the published tag/assets and generates the release attestation.

The setting is [not retroactive](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/establish-provenance-and-integrity/prevent-release-changes). The pre-policy version 2.4.2 is a narrowly pinned compatibility exception, identified by its original tag commit and package digest. Its GitHub release is not described as server-enforced immutable.

Reusing a version with different package bytes is blocked. Existing releases are downloaded and compared before reuse; a documentation-only commit with unchanged package bytes retains the original release source identity. Never rewrite a release tag or replace an asset to resolve a failed check.

## Google submission and recovery

The [Chrome Web Store v2 API](https://developer.chrome.com/docs/webstore/using-api) uploads the validated native-only ZIP and submits it for normal review with publication after approval. Validation warnings block submission. Existing visibility is retained, active reviews are not cancelled and review is not skipped.

Upload and publication intent is persisted before each remote write using a private temporary file, file fsync, atomic replacement and parent-directory fsync. Receipts bind publisher/item, version and archive digest. State files and locks reject unsafe links and ownership. A timeout, crash or unknown provider state is not permission to retry an uncertain write.

An asynchronous upload that never returns its expected version remains pending explicit reconciliation; a global upload-success flag cannot identify the draft package. A confirmed version that disappears or regresses also requires reconciliation rather than automatic resubmission. A new, higher application version can proceed normally once the relevant provider state permits it.

Inspect `artifacts/receipt.json`, `artifacts/cws-upload.json` and the bounded user journal. Preserve the original journal when reconciling an ambiguous outcome; do not remove it merely to silence an error. Routine failure records use the central `venturi-alert` agent-triage route with all required evidence fields and no human-escalation flags. Communications remain subject to Hermes and owner approval.

## Verify public availability and the signed payload

API publication, the anonymous listing's version/Add to Chrome availability, and signed payload identity are separate receipt fields. A stale public page remains pending propagation.

After publication, the verifier downloads the official Google CRX over restricted HTTPS origins with size/time bounds. It checks CRX3 developer identity, Google's pinned publisher signature, strict ZIP inventory and every runtime file against the validated upload. Only Google's expected manifest `update_url` and `_metadata/verified_contents.json` addition are permitted. Google repacks the ZIP, so equality of the outer ZIP/CRX digest is not required.

Proofs are cached only for the matching extension ID, version, upload digest and verifier-policy version; the private record retains its verification time. This is a per-version proof, not a new signature download every two minutes. The standalone helper can repeat the check:

```bash
python3 -I -B "$HOME/.local/libexec/duplicate-tab-guard-release/verify_store_package.py" \
  "$HOME/.local/state/duplicate-tab-guard-release/artifacts/cross-browser-duplicate-tab-guard-vVERSION-webstore.zip" \
  --expected-version VERSION
```

With the service's CWS environment loaded, the installed controller's `--status --output-dir ...` mode refreshes receipts without uploads or GitHub writes. Preserve the distinctions between `pending_review`, `staged`, `partial_rollout`, `pending_propagation`, `pending_store_payload_verification`, `published` and explicitly unverified existing revisions.

The signed payload/native protocol checks do not establish an actual store-installed Chrome UI test. After a user-visible package change, install/update through the store in an isolated browser profile and verify native companion connection and the user-facing smoke cases separately.

## References

- [Chrome Web Store service accounts](https://developer.chrome.com/docs/webstore/service-accounts)
- [Chrome Web Store status API](https://developer.chrome.com/docs/webstore/api/reference/rest/v2/publishers.items/fetchStatus)
- [Chromium CRX3 format](https://raw.githubusercontent.com/chromium/chromium/main/components/crx_file/crx3.proto)
- [Chromium CRX verifier](https://raw.githubusercontent.com/chromium/chromium/main/components/crx_file/crx_verifier.cc)
- [systemd timer accuracy](https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html)
