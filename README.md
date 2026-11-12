# Cross-Browser Duplicate Tab Guard

**Cross-Browser Duplicate Tab Guard** is an open-source, local-first browser extension and background coordinator daemon that detects, prevents, and manages duplicate tabs across multiple browser families (Google Chrome, Brave, and Chromium) on macOS and Linux.

[Install from the Chrome Web Store](https://chromewebstore.google.com/detail/cross-browser-duplicate-ta/dbfkmbhkefkppdbbiifihekopelgcdgb) · [Setup](#installation--setup) · [Troubleshooting](#troubleshooting) · [Contribute](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Privacy](docs/privacy.md) · [Security](SECURITY.md)

**The local Python companion is required, including for the Web Store extension.** Supported targets are macOS and Linux with Chrome, Brave or Chromium. Windows and Firefox are not currently supported.

---

## Features

- **Current Tab Reference**: The popup immediately shows the active tab ID and title, with one-click copying of a readable browser/window/context reference. This works even when the local coordinator is disconnected.
- **Cross-Browser State Coordination**: Synchronizes tab states across Chrome, Brave, and Chromium via local native messaging and a per-user Unix domain socket.
- **Native Messaging in the Web Store Build**: The published package requires the local companion and uses native messaging only. The installer-rendered unpacked build additionally supports an authenticated loopback WebSocket fallback when native messaging is unavailable; its port is configured during installation (default `127.0.0.1:49473`).
- **Intelligent Duplicate Chooser**: When you navigate to a URL that is already open in another window or browser, an in-page modal appears informing you exactly where the pre-existing tab is located (e.g., *"Already open in Brave"*, *"Already open in another Chrome window"*), with color-coded browser indicators and window/tab IDs.
- **Closed Shadow DOM Overlay**: In-page chooser modals use a closed Shadow DOM container (`attachShadow({ mode: "closed" })`) to isolate chooser styling from the host page. A closed shadow root is not a security boundary against the page.
- **High-Performance Exception Caching**: URL exception patterns are parsed and cached in an internal regular expression memoization map, eliminating repeated regex recompilations during high-frequency tab navigation events.
- **Local Data Handling**: No remote telemetry or cloud coordination service. Connected browser profiles share matching-tab context locally; optional private/incognito participation can expose matching private tabs in the chooser. See [privacy and data handling](docs/privacy.md) for storage, clipboard and removal details.

---

## Architecture

The project consists of two core components:

1. **Browser Extension (Manifest V3)**:
   - `service_worker.js`: Handles tab navigation events, transient URL filtering, and bidirectional communication with the local coordinator.
   - `content.js`: Injects the closed-shadow-DOM notification modal when a duplicate URL is detected.
   - `chooser.html / chooser.js / chooser.css`: Fallback chooser page for selecting which browser retains the single live copy; the other exact-URL copies are closed after confirmation.
   - `exceptions.js`: Exact URL, hostname, domain, wildcard, and regular-expression matching for rules configured in the popup. Duplicate detection compares the full URL, including query parameters and fragments.
   - `popup.html / popup.js / popup.css`: Browser action popup displaying daemon connection status and tab statistics.

2. **Coordinator Daemon (`cross_browser_duplicate_tab_guard.py`)**:
   - Single-file Python coordinator daemon and native host bridge.
   - Registers Native Messaging host manifests in the supported browser installation locations.
   - Sets up user systemd service units (Linux) or launchd LaunchAgents (macOS) for automatic background startup.

---

## Installation & Setup

### Prerequisites

- Python 3.9+
- One or more supported Chromium-based browsers: Google Chrome, Brave, or Chromium, based on Chromium 116 or newer.
- On Linux, a desktop user session with user systemd support for automatic startup. Browser sandboxing may restrict native messaging.

### Quick Start

1. **Clone the repository**:
   ```bash
   git clone https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard.git
   cd cross-browser-duplicate-tab-guard
   ```

2. **Install the daemon and register native messaging hosts**:
   ```bash
   python3 cross_browser_duplicate_tab_guard.py install
   ```

The installer starts the coordinator and configures automatic startup through launchd on macOS or user systemd on Linux. The Web Store extension requires this companion installation too.

3. **Load the Unpacked Extension in Your Browsers**:
   - Open your browser's extension manager:
     - Chrome: `chrome://extensions`
     - Brave: `brave://extensions`
     - Chromium: `chrome://extensions`
   - Enable **Developer mode** (toggle in upper right).
   - Click **Load unpacked** and select the **Extension folder** printed by the installer. This rendered folder contains the local fallback configuration; the repository's `extension/` directory is source for packaging.
   - For a Web Store installation, [install the published extension](https://chromewebstore.google.com/detail/cross-browser-duplicate-ta/dbfkmbhkefkppdbbiifihekopelgcdgb) instead of loading the unpacked build. Native messaging must be available in that browser; the Web Store build does not use the unpacked build's loopback fallback.

Use one installation method per browser profile to avoid running both copies. Private/incognito windows and file URLs participate only if you enable the corresponding permissions on the extension's Details page. Private-tab participation is optional; read the [privacy implications](docs/privacy.md#private-browsing-and-file-access) first.

---

## Verification

To verify that the coordinator and native messaging manifests are active across your browsers:

```bash
python3 cross_browser_duplicate_tab_guard.py doctor
```

---

## Duplicate detection and pause controls

Duplicate detection compares the full URL, including query parameters and fragments. The chooser identifies existing copies and lets you select the browser that should retain one live copy; confirming can close other exact-URL copies. Use harmless pages when trying it for the first time.

Both the duplicate alert and the toolbar popup offer pauses of **15 minutes, one hour or 24 hours**. In the duplicate alert, select a duration and choose **Pause** to keep the current copies open and dismiss the alert; the fallback chooser returns to the original page. Use **Resume** in the toolbar popup to end a pause early.

Pause applies to the current browser profile's guard. It does not suspend page execution or pause every connected browser. Saved exception rules are retained, and duplicate detection resumes when the selected duration expires. Use the enabled switch to disable guarding until you turn it back on.

## Current tab reference

The current-tab card stays visible above the settings and follows the active tab in the popup's
owning window. Copy ID includes the tab ID, browser name, browser API window ID, title and
regular/private context. When available, it also includes the existing Guard profile ID and
Guard session ID. The Guard session identifies an extension-worker lifetime, **not** a complete
browser session; tab/window IDs are browser-scoped and are **not** operating-system window IDs.
Recheck a copied reference after closing its tab or restarting the browser.

The title and tab ID render directly from the browser's tab API without waiting for the coordinator,
settings or exception matching. Countdown updates only repaint the guard status, preserving saved-rule
controls and keyboard focus. Failed settings writes keep the last confirmed state, and clipboard
fallback uses a temporary buffer without replacing an unfinished rules import.

### Automated behavior checks

With Python and Node.js 20 or newer available, install the locked test dependency and Chromium once:

```bash
npm ci
npx playwright install chromium
npm test
```

The aggregate command runs the coordinator self-test and regression tests, Web Store packaging checks, transport lifecycle
checks, and popup tests. Missing dependencies or a missing Chromium browser fail the command.
To run one suite, use `npm run test:coordinator`, `npm run test:regressions`, `npm run test:webstore`,
`npm run test:transport`, or `npm run test:popup`.

Set PLAYWRIGHT_MODULE to the installed Playwright module path if it is outside the repository's
module search path. The popup tests use mocked extension APIs and real rendering in Chromium,
covering unavailable status/settings, owning-window selection, stale replies, title updates,
clipboard failure, private tabs, storage-write rollback and keyboard-focus preservation.
They do not read or modify any user browser profile.

---

## Packaging for Chrome Web Store

To build a clean, validated distribution archive ready for submission to the Chrome Web Store Developer Dashboard:

```bash
python3 scripts/package_extension.py
```
This derives a native-messaging-only package, removes the developer key and loopback configuration,
validates the manifest and its packaged resources, and checks that the declared 16px, 32px, 48px,
and 128px icon files are present. It excludes known development and OS artifacts, rejects unexpected
files and unsafe paths, and writes a deterministic
`dist/cross-browser-duplicate-tab-guard-v<version>-webstore.zip`. The command prints the exact
archive path, file count, size, and SHA-256 digest.

Use `python3 scripts/package_extension.py --help` for options, including `--output-dir`.
The source extension and installer-rendered unpacked build retain their local development transport;
only the derived Web Store archive disables that fallback.

See [the release checklist](docs/releasing.md) for validation, submission and post-publication checks, and [store listing copy](docs/store_listing.md) for descriptions and permission justifications. Building an archive or merging a change does not publish a store update.

---

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Popup says the companion is disconnected | Run `doctor` from the cloned repository, confirm the companion installation completed, then reload the extension. The store build requires native messaging; it cannot use the unpacked build's WebSocket fallback. |
| Snap/Flatpak browser cannot connect | Browser confinement can block native-host execution. Check the browser's native-messaging support and the `doctor` results. An installer-rendered unpacked build supports the authenticated local fallback. |
| A duplicate produces no chooser | Check that guarding is enabled, the pause has expired, no exception matches, and both browser profiles are connected. Exact URLs must match. |
| Private or local-file tab is ignored | Confirm the corresponding browser-controlled extension permission is enabled, if you want that context included. |
| Chooser becomes a separate extension page | Some pages prohibit content scripts; the fallback chooser handles those cases. |
| A copied tab reference no longer works | Tab/window IDs are browser-scoped and can change after tabs close or browsers restart. Copy a fresh reference. |

For a bug report, include versions, install method and minimal reproduction steps. Use [the issue form](https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard/issues/new/choose) and remove private URLs, titles and tokens. See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

## Updating and removing

To update an unpacked installation, update the cloned source and rerun the installer, then reload the extension in each browser:

```bash
git pull --ff-only
python3 cross_browser_duplicate_tab_guard.py install
python3 cross_browser_duplicate_tab_guard.py doctor
```

Web Store extension updates are distributed through the browser; update the local companion separately when needed. After updating, verify the installed versions and connection status.

To remove the companion:

```bash
python3 cross_browser_duplicate_tab_guard.py uninstall
```

Remove the extension separately from each browser's extension manager. The companion's operational logs and any manually exported files are retained; see [local files and removal](docs/privacy.md#local-files-and-removal).

## Contributing and support

Read [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, repository layout and the review workflow. Report vulnerabilities privately as described in [SECURITY.md](SECURITY.md).

## License

MIT License. See [LICENSE](LICENSE) for details.

## Extension-management pages are never tracked

The guard never tracks or redirects extension-management surfaces in any browser, so they can be open in
several tabs and browsers at once: `chrome://extensions`, `brave://extensions`, `edge://extensions`,
`arc://extensions` (and the Opera and Vivaldi equivalents) with every sub-page, `brave://settings/extensions`,
the Chrome Web Store (`chromewebstore.google.com`, `chrome.google.com/webstore`), Edge Add-ons and Opera add-ons.
