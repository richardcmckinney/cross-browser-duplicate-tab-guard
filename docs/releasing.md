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
