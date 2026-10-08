# Cross-Browser Duplicate Tab Guard

**Cross-Browser Duplicate Tab Guard** is an open-source, local-first browser extension and background coordinator daemon that detects, prevents, and manages duplicate tabs across multiple browser families (Google Chrome, Brave, and Chromium) on macOS and Linux.

---

## Features

- **Cross-Browser State Coordination**: Synchronizes tab states across Chrome, Brave, and Chromium via local native messaging and Unix domain sockets (`/tmp/cbdtg-$UID/coordinator.sock` on Linux, persistent `Application Support` on macOS).
- **Graceful Local Fallback**: Automatically falls back to a loopback WebSocket connection (`127.0.0.1:49473`) in sandboxed environments (such as Flatpak runtimes) where native messaging sockets are isolated.
- **Intelligent Duplicate Chooser**: When you navigate to a URL that is already open in another window or browser, an in-page modal appears informing you exactly where the pre-existing tab is located (e.g., *"Already open in Brave"*, *"Already open in another Chrome window"*), with color-coded browser indicators and window/tab IDs.
- **Closed Shadow DOM Overlay**: In-page chooser modals use a closed Shadow DOM container (`attachShadow({ mode: "closed" })`) to prevent host page CSS/JavaScript collisions, styling bleed, or unauthorized inspection.
- **High-Performance Exception Caching**: URL exception patterns are parsed and cached in an internal regular expression memoization map, eliminating repeated regex recompilations during high-frequency tab navigation events.
- **Full Privacy & Zero Data Exfiltration**: Runs 100% locally on your machine. Zero cloud telemetry, zero remote tracking, and no external API dependencies.

---

## Architecture

The project consists of two core components:

1. **Browser Extension (Manifest V3)**:
   - `background / service_worker.js`: Handles tab navigation events, transient URL filtering, and bidirectional communication with the local coordinator.
   - `content.js`: Injects the closed-shadow-DOM notification modal when a duplicate URL is detected.
   - `chooser.html / chooser.js / chooser.css`: Interactive modal UI enabling you to switch to the existing tab, open anyway, or whitelist the domain.
   - `exceptions.js`: Domain and URL matching logic for whitelists and ignored URL parameters.
   - `popup.html / popup.js / popup.css`: Browser action popup displaying daemon connection status and tab statistics.

2. **Coordinator Daemon (`cross_browser_duplicate_tab_guard.py`)**:
   - Single-file Python coordinator daemon and native host bridge.
   - Automatically registers Chrome Native Messaging manifests across all detected browser profiles.
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

3. **Start the coordinator daemon**:
   ```bash
   python3 cross_browser_duplicate_tab_guard.py daemon --persistent &
   ```
   *(On macOS, the daemon is automatically managed via launchd; on Linux, via user systemd.)*

4. **Load the Unpacked Extension in Your Browsers**:
   - Open your browser's extension manager:
     - Chrome: `chrome://extensions`
     - Brave: `brave://extensions`
     - Chromium: `chrome://extensions`
   - Enable **Developer mode** (toggle in upper right).
   - Click **Load unpacked** and select the `extension/` directory.

---

## Verification

To verify that the coordinator and native messaging manifests are active across your browsers:

```bash
python3 cross_browser_duplicate_tab_guard.py doctor
```

To run the embedded test suite (coordinator, native messaging framing, exception rules and the Chrome Web Store build):

```bash
python3 cross_browser_duplicate_tab_guard.py self-test
```

---

## Packaging for Chrome Web Store

To build the upload ZIP for the Chrome Web Store Developer Dashboard:

```bash
python3 scripts/package_extension.py
```

The unpacked extension is tied to the computer it is installed on, so the store build changes it before packaging:

- removes the manifest `key` (the store assigns its own extension ID) and uses the store-length description (132 characters at most);
- turns off the loopback WebSocket fallback, whose port and secret token `install` writes into the service worker on each computer, and drops the loopback-only content security policy, so the store build talks to the companion over native messaging only;
- leaves out dot files, backups and the SVG master icon;
- refuses to write the ZIP if anything computer-specific is still in it (loopback addresses, home-directory paths, this computer's token).

It writes `dist/cross-browser-duplicate-tab-guard-v<version>-webstore.zip` with fixed timestamps, so the same source always gives the same bytes, and its SHA-256 next to it. `python3 cross_browser_duplicate_tab_guard.py pack-extension` runs the same build. CI builds the ZIP on every pull request and keeps it as the `chrome-web-store-package` artifact.

Flatpak builds of Chromium and Brave cannot start a native messaging host outside their sandbox, so on those the unpacked build remains the supported install.

See [docs/store_listing.md](docs/store_listing.md) for every field the dashboard asks for (listing copy, permission justifications, privacy practices answers) and [PRIVACY.md](PRIVACY.md) for the privacy policy.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

## Extension-management pages are never tracked

The guard never tracks or redirects extension-management surfaces in any browser, so they can be open in
several tabs and browsers at once: `chrome://extensions`, `brave://extensions`, `edge://extensions`,
`arc://extensions` (and the Opera and Vivaldi equivalents) with every sub-page, `brave://settings/extensions`,
the Chrome Web Store (`chromewebstore.google.com`, `chrome.google.com/webstore`), Edge Add-ons and Opera add-ons.
