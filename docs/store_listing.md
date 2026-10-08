# Chrome Web Store submission sheet

Everything the Chrome Web Store Developer Dashboard asks for, in the order the dashboard asks for it. The upload ZIP comes from `python3 scripts/package_extension.py` (see the README); do not upload a ZIP made any other way, because only that build removes the per-computer settings an installed copy carries.

## 1. Package

| Field | Value |
| :--- | :--- |
| Upload file | `dist/cross-browser-duplicate-tab-guard-v2.3.1-webstore.zip` (its SHA-256 is written next to it) |
| Version | 2.3.1 (from `extension/manifest.json`) |
| Manifest | Version 3; no `key` field (the store assigns the item ID); description cut to the store's 132-character limit |
| Companion | Users also need the free companion app from this repository; the extension tells them when it is missing |

The store signs the item with its own key, so the published extension gets a new ID. After the first upload, copy that ID from the dashboard into `STORE_EXTENSION_IDS` in `cross_browser_duplicate_tab_guard.py` and release the companion, so its native messaging manifest and its identity checks accept the store build as well as the unpacked one.

The store build talks to the companion over native messaging only. Flatpak builds of Chromium and Brave cannot start a native messaging host outside their sandbox, so on those the unpacked build (with its loopback connection) remains the supported install.

## 2. Store listing tab

**Item name** (from the manifest): Cross-Browser Duplicate Tab Guard

**Summary** (from the manifest, 127 characters):
> Stops exact-URL duplicate tabs across Chrome, Brave and Chromium, with editable exceptions. Needs the free local companion app.

**Description:**

```text
Cross-Browser Duplicate Tab Guard keeps one copy of each web page open across Google Chrome, Brave and Chromium on the same computer.

When you open a URL that is already open in another tab, window or browser, the extension tells you where the existing copy is (for example "Already open in Brave") and takes you to it. When the same URL is open in more than one browser, you choose which browser keeps it and the other copies close.

What it does
- Works across browsers: Chrome, Brave and Chromium on one computer share a single view of open tabs through a small companion app that runs locally.
- Matches exact URLs, so different pages on the same site are never treated as duplicates.
- Exceptions: leave chosen pages alone by exact URL, host, domain, wildcard or regular expression, for normal windows, private windows or both.
- Pause: switch the guard off for a set time from the toolbar popup.
- Private windows: runs in Incognito only if you allow it, and labels private copies as private.
- Leaves browser pages such as the extensions page and the Chrome Web Store alone.

Privacy
Everything stays on your computer. Tab addresses and titles go only to the companion app on the same machine, which keeps them in memory and never writes them to disk. No analytics, no accounts, no remote servers.

Setup
1. Install this extension.
2. Install the companion app (macOS or Linux, Python 3.9 or later): https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard
3. Open the extension's toolbar popup to confirm it is connected.
```

| Field | Value |
| :--- | :--- |
| Category | Productivity > Workflow & Planning |
| Language | English |
| Store icon | 128 x 128 PNG (`extension/icons/icon128.png`) |
| Screenshots | `store/images/screenshot_1_duplicate_notice_1280x800.png`, `screenshot_2_browser_chooser_1280x800.png`, `screenshot_3_popup_exceptions_1280x800.png` |
| Small promo tile | `store/images/promo_small_440x280.png` |
| Marquee promo tile | Optional; none supplied |

The images are rendered from the extension's own pages by `store/render_store_images.sh`, with fictional sample data on `example.com`, so they show the real interface and nothing from a real browser profile. Re-run it whenever the popup, chooser or dialog changes.
| Homepage URL | https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard |
| Support URL | https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard/issues |

## 3. Privacy practices tab

**Single purpose:**
> Prevent the same exact URL from being open in more than one tab across Chrome, Brave and Chromium on the same computer, and take the user to the copy that is already open.

**Permission justifications:**

| Permission | Justification |
| :--- | :--- |
| `tabs` | Reads the URL, title and window of open tabs to find exact-URL duplicates; focuses the existing tab, opens the copy the user chooses to keep, and closes the duplicates the user chose to close. |
| `webNavigation` | Listens to `onCommitted`, `onHistoryStateUpdated` and `onReferenceFragmentUpdated` so a duplicate is caught as soon as a tab commits to a URL, including in-page route changes. |
| `storage` | Keeps the user's exception rules, pause setting, chosen browser name and a random profile identifier in `chrome.storage.local`, and pending duplicate prompts in `chrome.storage.session`. Nothing is synced or sent anywhere. |
| `alarms` | Runs a one-minute health check that reconnects the service worker to the local companion app after sleep, browser restarts or a companion restart. |
| `nativeMessaging` | Exchanges tab state with the local companion app (`systems.venturi.duplicate_tab_guard`), which coordinates tabs between Chrome, Brave and Chromium on the same computer. |
| Host permission `<all_urls>` and the content script | The content script draws the "already open" dialog and the browser chooser inside the page the user just opened. Duplicates can happen on any site, so it must be able to run on any page; it reads only the page address and never the page content. |

**Remote code:** No, I am not using remote code. Every script is in the package; the extension loads no external scripts, modules or `eval` input.

**Data usage** (what to tick, and why):

- Tick **Web history**: the extension reads the URLs and titles of open tabs and passes them to the companion app on the same computer. It never leaves the computer, but declaring it keeps the listing accurate about what the extension handles.
- Leave every other category unticked (personally identifiable information, health, financial and payment, authentication, personal communications, location, user activity, website content): the extension does not handle them.
- Tick all three certifications: data is not sold or transferred to third parties outside the approved use cases; not used or transferred for purposes unrelated to the single purpose; not used or transferred to determine creditworthiness or for lending.

**Privacy policy URL:** https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard/blob/main/PRIVACY.md

## 4. Distribution tab

Visibility (public or unlisted), regions and the publisher name are the account owner's choice and are made in the dashboard.

## 5. Before pressing Submit

- [ ] The ZIP was built by `scripts/package_extension.py` from a clean checkout of `main`, and its SHA-256 matches the `.sha256` file.
- [ ] `PRIVACY.md` is on `main`, so the privacy policy URL resolves.
- [ ] The images in `store/images/` were rendered from the same commit as the ZIP.
