# Security

## Reporting a vulnerability

Email [richard@venturi.systems](mailto:richard@venturi.systems) with the subject “Duplicate Tab Guard security report”. Include the affected version, operating system and browser, reproduction steps, expected impact and a minimal proof of concept. Do not include live credentials or unrelated browsing data.

Please avoid posting exploit details or sensitive data in public issues before a fix can be coordinated. This project does not promise a response deadline or a bug bounty.

## Security boundaries

- The extension reads tab URLs, titles and browser context for local duplicate detection. See [privacy and data handling](docs/privacy.md).
- The Web Store package uses native messaging to an installed companion. The installer-rendered unpacked build also supports an authenticated loopback WebSocket connection.
- Native-host registrations allow the known development and store extension IDs. The companion uses a per-user local socket and configuration; it is not designed to protect against a compromised operating-system account.
- A closed Shadow DOM isolates the duplicate dialog's structure and styling. It is **not** a security boundary against the webpage.
- Private/incognito participation is optional. When enabled, matching private and regular tabs can appear in the same chooser across connected profiles. It is not a private-browsing isolation mechanism.
- “Keep selected copy” can close other exact-URL copies. Reports involving unexpected closure, untrusted message handling, cross-context data exposure or credential leakage are security-relevant.

## Versions and releases

Include both the installed extension and companion versions in a report. Repository `main`, an unpacked build and the public Web Store release can differ. Check the [Web Store listing](https://chromewebstore.google.com/detail/cross-browser-duplicate-ta/dbfkmbhkefkppdbbiifihekopelgcdgb) and [repository history](https://github.com/richardcmckinney/cross-browser-duplicate-tab-guard/commits/main/) for their respective versions. No separate long-term-support release line is documented.
