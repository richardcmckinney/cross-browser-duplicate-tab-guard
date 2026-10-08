#!/usr/bin/env python3
"""Build the Chrome Web Store upload ZIP for Cross-Browser Duplicate Tab Guard.

This is a thin wrapper around build_webstore_package() in
cross_browser_duplicate_tab_guard.py, so this script, the `pack-extension`
command and the embedded self-test all share one implementation. The build
removes everything tied to one computer (the developer key, the loopback
WebSocket port and token, the loopback-only CSP), validates the result and
writes a reproducible ZIP plus its SHA-256 next to it.
"""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import cross_browser_duplicate_tab_guard as guard  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Chrome Web Store upload ZIP.")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "dist"), help="directory for the ZIP (default: dist/)")
    args = parser.parse_args()
    # If the companion is installed on this computer, also refuse any build
    # that still contains its loopback token (it never should: the build
    # starts from the repository copy, which holds only placeholders).
    token = ""
    try:
        token = str(guard.load_config(create=False).get("token", ""))
    except guard.GuardError:
        token = ""
    try:
        destination = guard.build_webstore_package(REPO_ROOT / "extension", args.output_dir, [token])
    except guard.GuardError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    data = destination.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    checksum = destination.with_name(destination.name + ".sha256")
    checksum.write_text(f"{digest}  {destination.name}\n", encoding="utf-8")
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
    print(f"Chrome Web Store package for {guard.APP_NAME} v{guard.APP_VERSION}")
    print(f"  Archive: {destination}")
    print(f"  Files:   {len(names)}")
    for name in names:
        print(f"           {name}")
    print(f"  Size:    {len(data)} bytes")
    print(f"  SHA-256: {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
