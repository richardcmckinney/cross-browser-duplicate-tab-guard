# Releasing

A repository merge, a generated ZIP, a store submission and an available store update are separate states. Record each accurately.

## Prepare and validate

- Review the source changes, documentation, permissions and data-handling claims.
- Keep the extension manifest, companion version, embedded extension assets, [changelog](../CHANGELOG.md) and store listing copy consistent. Use a new valid extension version when publishing an update. Run `python3 scripts/sync_embedded_extension.py` after editing extension assets and verify `python3 scripts/sync_embedded_extension.py --check` passes.
- Run `npm ci`, install the matching Playwright Chromium if needed, and run `npm test`. Retain the commit SHA and test results.
- Test the unpacked extension with its local companion in an isolated profile. Cover duplicate resolution, every pause duration, resume, copy output, disconnect/reconnect and browser restart. Check both regular and private contexts if private access is enabled.
- Build the native-only distribution:

  ```bash
  python3 scripts/package_extension.py
  ```

- Record the archive path, version and printed SHA-256 digest. Inspect the ZIP contents; do not substitute an installer-rendered directory containing local configuration.
- Verify the store build's native messaging connection. An unpacked test using the development key does not by itself validate the store extension ID.

## Automated releases on VSH

`deploy/duplicate-tab-guard-release.timer` checks remote `main` every two minutes on VSH. No additional GitHub-hosted workflow or CodeBuild job is required. The runner accepts only the fixed repository, an unchanged checkout of freshly fetched `main`, and successful `tests` and `gitleaks` checks for that exact commit. It installs the locked test dependency, runs the full test command, checks embedded assets, builds the deterministic native-only ZIP and checks the source again before release writes.

A version creates an immutable `v<version>` tag and GitHub release containing the ZIP and `SHA256SUMS`. Reruns verify existing asset bytes; a reused version with different package bytes blocks instead of overwriting a release. A documentation-only commit with identical package bytes reuses the release. Increase the manifest and companion versions for package changes.

Chrome Web Store submission uses the [v2 API](https://developer.chrome.com/docs/webstore/using-api). Prefer an already linked [service account](https://developer.chrome.com/docs/webstore/service-accounts) and keyless impersonation by the existing authenticated Google account. Configure a mode-0600 file outside the repository at `~/.config/duplicate-tab-guard-release.env`:

```ini
CWS_PUBLISHER_ID=your-publisher-id
CWS_SERVICE_ACCOUNT_EMAIL=your-linked-publisher@your-project.iam.gserviceaccount.com
CWS_GCLOUD_ACCOUNT=your-existing-google-account
```

The Google account needs permission to impersonate that linked service account. Test authentication under the service's actual `HOME` and `PATH`; do not infer timer access from an interactive shell. For a controlled one-time call, an existing short-lived `CWS_ACCESS_TOKEN` is also accepted. Never commit credentials, print tokens, or create/rotate keys to make a release work. The default Google account is `richard@venturi.systems`; override it explicitly when operating a different account.

After this change is merged and checks pass, install the runner from the immutable merged commit and the reviewed units on **VSH only**:

```bash
release_commit=$(git rev-parse origin/main)
git show "$release_commit:deploy/vsh-release-runner.sh" > /tmp/duplicate-tab-guard-release-runner
install -Dm755 /tmp/duplicate-tab-guard-release-runner "$HOME/.local/libexec/duplicate-tab-guard-release-runner"
sudo install -m644 deploy/duplicate-tab-guard-release.service deploy/duplicate-tab-guard-release.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now duplicate-tab-guard-release.timer
sudo systemctl start duplicate-tab-guard-release.service
```

The runner's source, package, test log, lock and JSON receipts live under `~/.local/state/duplicate-tab-guard-release/`. The installed runner is fixed until deliberately updated; application release code is loaded only from current `main` and revalidated before publishing. Inspect `journalctl -u duplicate-tab-guard-release.service` and `artifacts/receipt.json`. Failures use the standard `venturi-alert` route with all six required flags.

A matching active review is left untouched. Another pending version, a staged revision, rejection/cancellation, a policy warning, or a newer published version blocks destructive replacement. The script never cancels a review or requests skipped review. It asks for immediate publication **after approval**, with validation warnings blocking submission. Existing store visibility is retained; verify public access separately. Listing text, privacy declarations and artwork still need to be maintained in the dashboard because these v2 endpoints publish packages, not listing metadata.

Upload/publish intent is saved before the corresponding request. An asynchronous upload that finishes without returning the expected `crxVersion` is preserved for dashboard reconciliation; a global upload-success status alone cannot identify its draft package. If a timeout or crash leaves an ambiguous outcome, a rerun first checks for the exact expected submitted or published version. If that cannot reconcile the attempt, it blocks instead of blindly publishing an unknown draft. Inspect the dashboard and `artifacts/cws-upload.json`, verify the intended package/version, and archive that journal outside the artifacts directory before deliberately retrying. Do not delete it merely to silence an error.

Use `python3 scripts/release_extension.py --status --output-dir ~/.local/state/duplicate-tab-guard-release/artifacts` to refresh an existing receipt without uploads or GitHub writes. `pending_review`, `staged`, `partial_rollout`, `pending_propagation` and API-reported `published` are distinct states. The receipt separately records the public listing version, Add to Chrome availability and `public_verified`; an old or unavailable public page remains pending propagation. Existing matching versions without a journal binding the expected archive digest are explicitly marked `existing_<state>_unverified`; no package upload is attempted. An API `published` result does not replace the public-listing and installed-build checks below. Unchanged commits with pending or published receipts only refresh status on subsequent timer runs; they do not repeat tests or submission.

## Publish

- Merge the reviewed change with a merge commit after required checks pass; remove the merged topic branch.
- Upload the exact validated ZIP to the [Chrome Web Store Developer Dashboard](https://chrome.google.com/webstore/devconsole/).
- Update the dashboard description and permission/data-handling declarations from [store_listing.md](store_listing.md) and [privacy.md](privacy.md). A repository documentation edit does not update the dashboard.
- Add current screenshots of the actual interface and complete the dashboard's required assets. Do not imply unimplemented controls or privacy guarantees.
- Submit for review and record whether the submission is pending, rejected or published. If rejected, resolve the stated issue and revalidate the changed package.
- After publication, check the [public listing](https://chromewebstore.google.com/detail/cross-browser-duplicate-ta/dbfkmbhkefkppdbbiifihekopelgcdgb), install/update through the store in an isolated browser profile, and verify the installed version and native companion connection.
- Repeat the user-visible smoke checks on the store-installed build. Capture concrete results before reporting deployment complete.

## Recovery

If a regression appears, preserve its reproduction and affected versions. Prepare a fix or revert the source change, then publish a corrected package with a new version; a Git revert alone does not roll back an already distributed extension. Re-run the same validation and publication checks.
