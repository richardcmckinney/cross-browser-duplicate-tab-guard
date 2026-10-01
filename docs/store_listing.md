# Chrome Web Store Listing & Store Assets Specification

## 1. Extension Details
- **Name**: Cross-Browser Duplicate Tab Guard
- **Short Name**: Tab Guard
- **Version**: 2.3.0
- **Category**: Productivity / Workflow & Planning
- **Primary Language**: English
- **Supported Platforms**: Chrome, Brave, Chromium (macOS & Linux)

---

## 2. Store Copy & Descriptions

### Short Description (Max 132 Characters)
> Prevent duplicate tabs across Chrome, Brave, and Chromium. Coordinates open URLs locally with customizable domain exceptions.

### Detailed Description (Markdown / Store Formatted)
```markdown
Cross-Browser Duplicate Tab Guard coordinates open tab state across Google Chrome, Brave, and Chromium, preventing accidental duplicate tabs across multiple windows and browsers.

When you navigate to a URL that is already open in another window or browser, Tab Guard intercepts the navigation and presents an elegant, non-intrusive in-page modal informing you where the pre-existing tab is located (e.g., "Already open in Brave"). You can choose to instantly jump to the existing tab, keep the new tab open, or exclude the domain from future checks.

KEY FEATURES:
• Cross-Browser Coordination: Synchronizes tab states across Chrome, Brave, and Chromium via a lightweight local native messaging daemon.
• Closed Shadow DOM Dialogs: In-page duplicate modals use isolated closed Shadow DOM containers to prevent website style bleed or script interference.
• Powerful Exception Engine: Easily whitelist websites by exact URL, domain, wildcard, or regular expression with in-memory memoized matching.
• Local-First & Zero Telemetry: 100% of coordination occurs locally on your machine over Unix domain sockets and loopback WebSockets (127.0.0.1). Zero remote telemetry, zero tracking, and no external server requests.
• Incognito & Private Support: Split context support allows private tabs to be managed safely without leaking browsing history across profiles.

SETUP:
1. Load this extension in your Chromium-based browser(s).
2. Run the lightweight local companion coordinator daemon.
3. Enjoy duplicate-free browsing across your entire desktop workflow!
```

---

## 3. Permissions Justifications (Chrome Web Store Review)

| Permission | Justification for Chrome Web Store Reviewers |
| :--- | :--- |
| `tabs` | Required to query open tab URLs across the browser to detect duplicates and switch focus to an existing tab upon user confirmation. |
| `webNavigation` | Required to detect URL navigations at `onBeforeNavigate` and `onCommitted` before rendering, enabling early duplicate interception. |
| `storage` | Required to store user-configured URL exception rules, whitelist patterns, and pause durations locally on the device. |
| `alarms` | Required for periodic background service worker heartbeats to maintain connectivity with the local coordinator daemon across sleep/wake transitions. |
| `nativeMessaging` | Required for bidirectional communication with the local coordinator daemon on the host machine to coordinate tab state across browser families. |

---

## 4. Privacy & Data Handling Declarations
- **Single Purpose**: Cross-browser tab deduplication and window navigation.
- **Data Collection**: No user data, browsing history, or personal information is collected, stored remotely, or transmitted to any third party.
- **Network Boundaries**: The extension communicates exclusively with `localhost` (`127.0.0.1`) over native messaging and local loopback sockets.

---

## 5. Graphic Assets Checklist
- [x] **16x16 PNG**: Toolbar / favicon (`extension/icons/icon16.png`)
- [x] **32x32 PNG**: Windows / high-DPI display icon (`extension/icons/icon32.png`)
- [x] **48x48 PNG**: Extensions management page (`extension/icons/icon48.png`)
- [x] **128x128 PNG**: Chrome Web Store listing & installation icon (`extension/icons/icon128.png`)
- [x] **SVG Master**: Vector source icon (`extension/icons/icon.svg`)
- [ ] **Small Promo Tile**: 440x280 px PNG (for Web Store carousel / category listing)
- [ ] **Store Screenshots**: 1280x800 px PNG (1–5 screenshots showing in-page modal and popup exception manager)
