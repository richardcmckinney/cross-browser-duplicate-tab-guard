# Privacy and data handling

This document describes the implementation in this repository. Browser and Web Store services have their own privacy terms.

## Data used by the guard

| Data | Purpose and location |
| --- | --- |
| Tab URLs, titles, tab/window IDs, active state and private/regular context | Sent between the extension and local companion for duplicate detection and chooser display; live tab records are held in coordinator memory |
| Pending duplicate incidents, including their URLs and matching-tab context | Kept in extension memory and `chrome.storage.session` to survive service-worker restarts |
| Exception rules, enabled state and pause expiry | Kept in `chrome.storage.local` for the browser profile |
| Browser identity override and generated Guard profile ID | Kept in `chrome.storage.local` to identify the connected browser/profile |
| Guard session ID | Identifies an extension-worker lifetime; it is not a complete browsing session |
| Local companion token and loopback port | Stored in the per-user companion `config.json`; used for the unpacked build's fallback transport |
| Operational logs | Companion startup, shutdown and error types; the logging calls do not intentionally record tab URLs or titles |

The guard has no remote telemetry or analytics endpoint. It does not send tab data to a cloud coordination service. The Web Store archive uses native messaging and a per-user Unix socket. The installer-rendered unpacked build may instead use authenticated loopback WebSockets.

“Local” does not mean “visible only to this tab”: connected browsers and profiles participate in the same local coordinator. The chooser may display matching tab titles and context from other connected profiles.

## Private browsing and file access

Private/incognito participation requires the browser's explicit extension permission. When allowed, the guard can compare private tabs with regular tabs and display matching copies across profiles. Leave that permission disabled if private tabs should not participate.

File URL access is separately controlled by the browser. Enable it only if local file tabs should participate.

## Copying and exporting

Copy ID writes the selected tab reference to the system clipboard only when requested. Clipboard managers, device clipboard synchronization and applications receiving pasted text are outside the guard's control.

Exported exception rules may contain sensitive URLs or domains. Review exports, screenshots and diagnostic output before sharing them.

## Local files and removal

The companion data directory is:

- macOS: `~/Library/Application Support/CrossBrowserDuplicateTabGuard`
- Linux: `$XDG_DATA_HOME/cross-browser-duplicate-tab-guard`, defaulting to `~/.local/share/cross-browser-duplicate-tab-guard`

Operational logs are separate:

- macOS: `~/Library/Logs/CrossBrowserDuplicateTabGuard.log`
- Linux: `$XDG_STATE_HOME/cross-browser-duplicate-tab-guard/coordinator.log`, defaulting to `~/.local/state/cross-browser-duplicate-tab-guard/coordinator.log`

Running the [uninstall command](../README.md#updating-and-removing) removes the companion's application directory, native-host registrations and matching external-extension registrations created by the installer. Externally modified registrations and store-managed records are preserved. Remove the extension separately from each browser to remove its browser-managed local settings. The companion uninstaller does not delete exported files, clipboard history or the separate operational log.

Questions or suspected data exposure can be reported to [richard@venturi.systems](mailto:richard@venturi.systems); use [the security reporting process](../SECURITY.md) for vulnerabilities.
