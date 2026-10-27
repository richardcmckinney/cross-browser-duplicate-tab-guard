# Chrome Web Store Listing & Store Assets Specification

## 1. Extension Details
- **Name**: Cross-Browser Duplicate Tab Guard
- **Short Name**: Tab Guard
- **Version**: 2.4.1
- **Category**: Productivity / Workflow & Planning
- **Primary Language**: English
- **Supported Platforms**: Chrome, Brave, Chromium (macOS & Linux)

---

## 2. Store Copy & Descriptions

### Short Description (Max 132 Characters)
> Stops exact-URL duplicate tabs across Chrome, Brave and Chromium, with editable exceptions. Needs the free local companion app.

### Detailed Description (Markdown / Store Formatted)
```markdown
Cross-Browser Duplicate Tab Guard coordinates open tab state across Google Chrome, Brave, and Chromium, preventing accidental duplicate tabs across multiple windows and browsers.

When you navigate to an exact URL that is already open in another window or browser, Tab Guard presents a chooser identifying the existing copies (for example, "Already open in Brave"). Choose which browser should retain the single live copy; the other exact-URL copies close after confirmation. Configure URL and domain exceptions separately in the extension popup.

KEY FEATURES:
• Cross-Browser Coordination: Synchronizes tab states across Chrome, Brave, and Chromium via a lightweight local native messaging daemon.
• Closed Shadow DOM Dialogs: In-page duplicate modals use closed Shadow DOM containers to isolate their styling from the website.
• Powerful Exception Engine: Easily whitelist websites by exact URL, domain, wildcard, or regular expression with in-memory memoized matching.
• Local-First & Zero Telemetry: Coordination uses native messaging and local Unix domain sockets. The Web Store build requires the free local companion app and does not use a loopback WebSocket fallback. Zero remote telemetry, zero tracking, and no external server requests for coordination.
• Incognito & Private Support: Enable the extension in private/incognito windows to include their tabs in duplicate detection. The chooser can display matching regular and private tabs across connected browser profiles.

SETUP:
1. Install this extension in your supported browser(s).
2. Install the free local companion from the project repository. Native messaging must be available in each browser.
3. Confirm the companion connection in the extension popup and configure any exceptions.
```

---

## 3. Permissions Justifications (Chrome Web Store Review)

| Permission | Justification for Chrome Web Store Reviewers |
| :--- | :--- |
| `tabs` | Required to query open tab URLs across the browser to detect duplicates and switch focus to an existing tab upon user confirmation. |
| `webNavigation` | Required to detect committed navigations, history-state changes, and fragment changes so exact-URL duplicates can be checked after navigation. |
| `storage` | Required to store user-configured URL exception rules, whitelist patterns, and pause durations locally on the device. |
| `alarms` | Required for periodic background service worker heartbeats to maintain connectivity with the local coordinator daemon across sleep/wake transitions. |
| `nativeMessaging` | Required for bidirectional communication with the local coordinator daemon on the host machine to coordinate tab state across browser families. |

---

## 4. Privacy & Data Handling Declarations
- **Single Purpose**: Cross-browser tab deduplication and window navigation.
- **Data Collection**: Tab URLs, titles, and browser context are processed locally for duplicate detection. They are not sent to remote servers or third parties.
- **Network Boundaries**: The Web Store extension communicates with the installed native messaging host, which connects to the local coordinator over a Unix domain socket. The published package has no loopback WebSocket fallback. Authenticated loopback WebSocket support belongs only to the installer-rendered unpacked build.

---

## 5. Graphic Assets Checklist
- [x] **16x16 PNG**: Toolbar / favicon (`extension/icons/icon16.png`)
- [x] **32x32 PNG**: Windows / high-DPI display icon (`extension/icons/icon32.png`)
- [x] **48x48 PNG**: Extensions management page (`extension/icons/icon48.png`)
- [x] **128x128 PNG**: Chrome Web Store listing & installation icon (`extension/icons/icon128.png`)
- [x] **SVG Master**: Vector source icon (`extension/icons/icon.svg`)
- [ ] **Small Promo Tile**: 440x280 px PNG (for Web Store carousel / category listing)
- [ ] **Store Screenshots**: 1280x800 px PNG (1–5 screenshots showing in-page modal and popup exception manager)
