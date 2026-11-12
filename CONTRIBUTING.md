# Contributing

Bug reports, documentation fixes, accessibility improvements and focused pull requests are welcome.

## Before opening an issue

Read the [setup and troubleshooting guide](README.md#troubleshooting). Search [existing issues](https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard/issues) before filing a new report.

Include the extension version, companion version, operating system, browser version, installation method (Web Store or unpacked), minimal reproduction steps and expected versus actual behavior. Use harmless example URLs and a disposable browser profile. Remove private URLs, tab titles, profile paths and tokens from screenshots or logs.

Report vulnerabilities privately using [SECURITY.md](SECURITY.md), not a public issue.

## Development setup

The runtime uses Python's standard library. Node.js and the locked Playwright development dependency are needed only for the browser tests.

```bash
git clone https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git
cd cross-browser-duplicate-tab-guard
npm ci
npx playwright install chromium
npm test
```

Use Python 3.9 or newer and Node.js 20 or newer. On Linux, Playwright may also require system libraries; its installation output identifies missing dependencies. The tests launch isolated Chromium contexts with mocked extension APIs rather than using personal browser profiles.

## Source layout

| Path | Responsibility |
| --- | --- |
| `extension/` | Extension source: service worker, popup, duplicate chooser and exception matching |
| `cross_browser_duplicate_tab_guard.py` | Installer, coordinator, native messaging bridge, embedded extension fallback and coordinator self-tests |
| `scripts/package_extension.py` | Deterministic Web Store archive creation and validation |
| `scripts/sync_embedded_extension.py` | Synchronize the standalone installer's embedded text and binary assets |
| `tests/` | Browser behavior, transport lifecycle and packaging regression tests |
| `docs/` | Privacy details, release instructions and store listing copy |

The installer prefers the adjacent `extension/` source directory. The Python file also embeds extension text for its standalone fallback. After changing extension assets, run `python3 scripts/sync_embedded_extension.py` to synchronize the embedded text and binary assets, then `python3 scripts/sync_embedded_extension.py --check` to check for drift. Verify both installation paths. Do not edit a rendered installed extension directory as the source of a contribution.

## Making a change

- Keep the change focused and describe the user-visible problem it solves.
- Preserve exact-URL matching, explicit confirmation before closing duplicates, and the distinction between native-only Web Store transport and the unpacked build's local fallback.
- Keep browser-provided titles and URLs as text, never executable HTML.
- Preserve keyboard access, visible focus and status announcements when changing controls.
- Add a regression test for behavior changes. Include failure cases such as disconnected transport, stale tabs and rejected settings writes when relevant.
- Update documentation when setup, permissions, data handling or user-visible behavior changes.
- Run the relevant suites and then `npm test`. Build with `python3 scripts/package_extension.py` when extension resources, the manifest or packaging change.
- State exactly what was tested, along with any limitations. Mocked APIs do not establish that the native companion or Web Store update works on a user's machine.

Do not commit local configuration, generated archives, dependency directories, credentials, browser profiles or screenshots containing private browsing information. The manifest's public development key is an identifier, not a private signing key.

## Pull requests

Explain the problem, resulting behavior, validation and any compatibility or release steps. Small, reviewable changes are easier to validate. A green test run is evidence about the tested code, not proof of a live store release; follow the [release checklist](docs/releasing.md).

Keep discussion respectful and specific. Critique code and behavior rather than people, welcome questions, and avoid harassment or disclosure of others' private information.

Contributions are distributed under the repository's [MIT license](LICENSE).
