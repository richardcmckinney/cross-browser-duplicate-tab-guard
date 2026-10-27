#!/usr/bin/env python3
"""Build the deterministic Chrome Web Store upload package."""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and validate the deterministic Chrome Web Store upload package.",
    )
    parser.add_argument(
        "--output-dir",
        default="dist",
        help="Directory for the generated ZIP (default: %(default)s).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))
    import cross_browser_duplicate_tab_guard as guard

    src_dir = repo_root / "extension"
    out_dir = Path(args.output_dir)
    if not out_dir.is_absolute():
        out_dir = repo_root / out_dir

    token = ""
    try:
        token = str(guard.load_config(create=False).get("token", ""))
    except guard.GuardError:
        pass
    try:
        destination = guard.build_webstore_package(src_dir, out_dir, [token])
    except guard.GuardError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    data = destination.read_bytes()
    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
    print(f"Chrome Web Store package for {guard.APP_NAME}:")
    print(f"  Archive: {destination}")
    print(f"  Files:   {len(names)}")
    print(f"  Size:    {len(data)} bytes")
    print(f"  SHA-256: {hashlib.sha256(data).hexdigest()}")
    print("Upload this exact archive in the Chrome Web Store Developer Dashboard; see docs/store_listing.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
