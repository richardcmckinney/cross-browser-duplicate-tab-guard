# Privacy Policy: Cross-Browser Duplicate Tab Guard

Last updated: 2026-10-08

This policy covers the Cross-Browser Duplicate Tab Guard browser extension ("the extension") and its companion app, `cross_browser_duplicate_tab_guard.py` ("the companion"). Questions: contact@venturi.systems.

## Summary

The extension and the companion run entirely on your computer. Nothing they handle is sent to the developer or to anyone else. There are no analytics, no advertising, no accounts and no remote servers.

## What the extension handles, and why

The extension has one purpose: stop the same exact URL from being open in more than one tab across Chrome, Brave and Chromium on the same computer, and take you to the copy that is already open.

To do that it handles:

- **Open tabs**: the URL and title of each open tab, its tab and window number, whether it is in a private (Incognito) window, and which browser it is in. This is web browsing data. It is used only to recognise when a URL you open is already open somewhere else and to tell you where.
- **Your settings**: the exception rules you add (URLs, sites or patterns to leave alone), a pause you set, and the browser name you choose for this profile. They are stored in the browser's extension storage on your computer (`chrome.storage.local`) and stay there until you delete them or remove the extension.
- **A random profile identifier**: generated on your computer so the companion can tell your browser profiles apart. It contains nothing about you.
- **Pending duplicate prompts**: kept in the browser's session storage (`chrome.storage.session`), which the browser clears when it closes.

The extension does not read the content of the pages you visit. Its content script reads only the address of the page it runs in and draws the "already open" dialog inside that page.

## Where the data goes

Tab information goes from the extension to the companion on the same computer through the browser's native messaging channel. In the unpacked developer build only, a loopback connection to `127.0.0.1`, protected by a secret generated on your computer, is also used. Neither channel leaves your computer.

The companion keeps tab information in memory only and discards it when it stops. It does not write URLs or page titles to disk and does not log them; its log file holds start, stop and error reports.

## What is never done

- No data is sold or transferred to third parties.
- No data is used or transferred for any purpose other than the single purpose above.
- No data is used or transferred to determine creditworthiness or for lending.
- No remote code is loaded; everything the extension runs is inside its package.

## Private (Incognito) windows

The extension runs in private windows only if you allow it on the browser's extensions page. Private-window tabs are handled the same way as other tabs, in memory on your computer, and are labelled as private when the extension tells you where an existing copy is.

## Your choices

You can delete exception rules in the extension's popup, turn the extension off or remove it on the browser's extensions page, and stop and remove the companion with `python3 cross_browser_duplicate_tab_guard.py uninstall`. When you remove the extension, the browser deletes the extension's stored data.

## Changes

Changes to this policy are published in this file, with a new date above.
