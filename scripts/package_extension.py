#!/usr/bin/env python3
"""Build and package Cross-Browser Duplicate Tab Guard for Chrome Web Store distribution."""

import json
import os
import sys
import zipfile
from pathlib import Path

def main():
    repo_root = Path(__file__).resolve().parent.parent
    src_dir = repo_root / "extension"
    out_dir = repo_root / "dist"

    manifest_path = src_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"Error: manifest.json not found in {src_dir}", file=sys.stderr)
        return 1

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Error: Failed to parse manifest.json: {e}", file=sys.stderr)
        return 1

    version = manifest.get("version", "0.0.0")
    name = manifest.get("name", "extension")

    if manifest.get("manifest_version") != 3:
        print("Error: Chrome Web Store requires manifest_version 3", file=sys.stderr)
        return 1

    icons = manifest.get("icons", {})
    for req_size in ("16", "48", "128"):
        icon_rel = icons.get(req_size)
        if not icon_rel:
            print(f"Warning: icon size {req_size} is not declared in manifest.json icons")
        elif not (src_dir / icon_rel).exists():
            print(f"Error: Icon file referenced in manifest does not exist: {src_dir / icon_rel}", file=sys.stderr)
            return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    zip_name = f"cross-browser-duplicate-tab-guard-v{version}.zip"
    zip_dest = out_dir / zip_name

    ignore_patterns = {".DS_Store", "Thumbs.db", "__pycache__", ".git", ".gitignore"}

    file_count = 0
    with zipfile.ZipFile(zip_dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for root_dir, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d not in ignore_patterns and not d.startswith((".", "_"))]
            for file in sorted(files):
                if file in ignore_patterns or file.endswith(("~", ".bak", ".tmp", ".swp")):
                    continue
                file_path = Path(root_dir) / file
                arcname = file_path.relative_to(src_dir)
                zf.write(file_path, arcname)
                file_count += 1

    size_bytes = zip_dest.stat().st_size
    size_kb = size_bytes / 1024.0
    print(f"Successfully packaged {name} v{version}:")
    print(f"  Archive: {zip_dest}")
    print(f"  Files:   {file_count}")
    print(f"  Size:    {size_kb:.1f} KB ({size_bytes} bytes)")
    print("Ready for Chrome Web Store Developer Dashboard upload.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
