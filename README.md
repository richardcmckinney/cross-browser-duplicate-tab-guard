# Cross-Browser Duplicate Tab Guard

**Cross-Browser Duplicate Tab Guard** is an open-source, local-first browser extension and background coordinator daemon that detects, prevents, and manages duplicate tabs across multiple browser families (Google Chrome, Brave, and Chromium) on macOS and Linux.

---

## Features

- **Current Tab Reference**: The popup immediately shows the active tab ID and title, with one-click copying of a readable browser/window/context reference. This works even when the local coordinator is disconnected.
- **Cross-Browser State Coordination**: Synchronizes tab states across Chrome, Brave, and Chromium via local native messaging and Unix domain sockets (`/tmp/cbdtg-$UID/coordinator.sock` on Linux, persistent `Application Support` on macOS).
- **Native Messaging in the Web Store Build**: The published package requires the local companion and uses native messaging only. The installer-rendered unpacked build additionally supports an authenticated loopback WebSocket fallback when native messaging is unavailable; its port is configured during installation (default `127.0.0.1:49473`).
- **Intelligent Duplicate Chooser**: When you navigate to a URL that is already open in another window or browser, an in-page modal appears informing you exactly where the pre-existing tab is located (e.g., *"Already open in Brave"*, *"Already open in another Chrome window"*), with color-coded browser indicators and window/tab IDs.
- **Closed Shadow DOM Overlay**: In-page chooser modals use a closed Shadow DOM container (`attachShadow({ mode: "closed" })`) to isolate chooser styling from the host page. A closed shadow root is not a security boundary against the page.
- **High-Performance Exception Caching**: URL exception patterns are parsed and cached in an internal regular expression memoization map, eliminating repeated regex recompilations during high-frequency tab navigation events.
- **Full Privacy & Zero Data Exfiltration**: Runs 100% locally on your machine. Zero cloud telemetry, zero remote tracking, and no external API dependencies.

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
- One or more supported Chromium-based browsers: Google Chrome, Brave, or Chromium.

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
   - For a Web Store installation, install the published extension instead of loading the unpacked build. Native messaging must be available in that browser; the Web Store build does not use the unpacked build's loopback fallback.

---

## Verification

To verify that the coordinator and native messaging manifests are active across your browsers:

```bash
python3 cross_browser_duplicate_tab_guard.py doctor
```

---

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

The aggregate command runs the coordinator self-test, Web Store packaging checks, transport lifecycle
checks, and popup tests. Missing dependencies or a missing Chromium browser fail the command.
To run one suite, use `npm run test:coordinator`, `npm run test:webstore`,
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

See [docs/store_listing.md](docs/store_listing.md) for Chrome Web Store descriptions, categories, and permission justifications.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

## Extension-management pages are never tracked

The guard never tracks or redirects extension-management surfaces in any browser, so they can be open in
several tabs and browsers at once: `chrome://extensions`, `brave://extensions`, `edge://extensions`,
`arc://extensions` (and the Opera and Vivaldi equivalents) with every sub-page, `brave://settings/extensions`,
the Chrome Web Store (`chromewebstore.google.com`, `chrome.google.com/webstore`), Edge Add-ons and Opera add-ons.
