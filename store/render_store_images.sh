#!/usr/bin/env bash
# Renders the Chrome Web Store images from the real extension UI:
#   store/images/screenshot_1_duplicate_notice_1280x800.png   in-page duplicate dialog (content.js)
#   store/images/screenshot_2_browser_chooser_1280x800.png    chooser page (chooser.html)
#   store/images/screenshot_3_popup_exceptions_1280x800.png   toolbar popup (popup.html)
#   store/images/promo_small_440x280.png                      small promo tile
# The pages are the extension's own HTML, CSS and JavaScript; store/harness/stub.js stands in
# for the browser extension APIs with fixed, fictional sample data (example.com), so nothing
# from a real browser profile appears in the images.
# Needs a headless Chromium: set CHROME_HEADLESS to its path, or install Playwright's
# chromium-headless-shell (npx playwright install chromium-headless-shell).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/store/images"
BIN="${CHROME_HEADLESS:-}"
if [ -z "$BIN" ]; then
  BIN="$(ls -d "$HOME"/.cache/ms-playwright/chromium_headless_shell-*/*/chrome-headless-shell 2>/dev/null | sort -V | tail -1 || true)"
fi
[ -x "$BIN" ] || { echo "No headless Chromium found; set CHROME_HEADLESS." >&2; exit 1; }

WORK="$(mktemp -d)"
SERVER=""
cleanup() { [ -n "$SERVER" ] && kill "$SERVER" 2>/dev/null; rm -rf "$WORK"; }
trap cleanup EXIT

mkdir -p "$WORK/web" "$OUT"
cp "$ROOT"/extension/*.js "$ROOT"/extension/*.css "$ROOT"/extension/*.html "$WORK/web/"
cp -R "$ROOT/extension/icons" "$WORK/web/"
cp "$ROOT/store/harness/stub.js" "$WORK/web/"
cp -R "$ROOT/store/harness/docs" "$WORK/"
cp "$ROOT/store/harness/frame_popup.html" "$ROOT/store/harness/promo_small.html" "$WORK/"
# Load the stub before the extension's own scripts.
sed 's#<script src="exceptions.js"></script>#<script src="stub.js"></script>\n  <script src="exceptions.js"></script>#' "$WORK/web/popup.html" > "$WORK/web/popup_shot.html"
sed 's#<script src="chooser.js"></script>#<script src="stub.js"></script>\n  <script src="chooser.js"></script>#' "$WORK/web/chooser.html" > "$WORK/web/chooser_shot.html"
grep -q stub.js "$WORK/web/popup_shot.html" && grep -q stub.js "$WORK/web/chooser_shot.html"

PORT="$(python3 -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')"
(cd "$WORK" && exec python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1) &
SERVER=$!
sleep 1

# example.com is mapped to the local server so the pages show a documentation-only address.
shot() {
  "$BIN" --no-sandbox --hide-scrollbars --force-device-scale-factor=1 --window-size="$3" \
    --host-resolver-rules="MAP example.com 127.0.0.1:$PORT" --virtual-time-budget=4000 \
    --screenshot="$OUT/$1" "http://example.com/$2" >/dev/null 2>&1
}
shot screenshot_1_duplicate_notice_1280x800.png docs/getting-started/ 1280,800
shot screenshot_2_browser_chooser_1280x800.png web/chooser_shot.html 1280,800
shot screenshot_3_popup_exceptions_1280x800.png frame_popup.html 1280,800
shot promo_small_440x280.png promo_small.html 440,280
ls -l "$OUT"
