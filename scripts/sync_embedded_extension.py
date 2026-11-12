#!/usr/bin/env python3
"""Keep the standalone installer byte-for-byte aligned with extension assets."""
from __future__ import annotations

import argparse
import ast
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".css", ".html", ".js", ".json", ".svg"}
BINARY_SUFFIXES = {".png"}


def embedded_assets() -> tuple[dict[str, str], dict[str, str]]:
    text, binary = {}, {}
    for path in sorted((ROOT / "extension").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT / "extension").as_posix()
        if path.suffix in TEXT_SUFFIXES:
            text[relative] = path.read_text(encoding="utf-8")
        elif path.suffix in BINARY_SUFFIXES:
            binary[relative] = base64.b64encode(path.read_bytes()).decode("ascii")
    return text, binary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without writing files.")
    args = parser.parse_args()
    source_path = ROOT / "cross_browser_duplicate_tab_guard.py"
    source = source_path.read_text(encoding="utf-8")
    values = dict(zip(("EXTENSION_FILES", "EXTENSION_BINARY_FILES"), embedded_assets()))
    replacements = []
    for node in ast.parse(source).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in values:
            value = values.pop(node.target.id)
            if ast.literal_eval(node.value) != value:
                replacement = f"{node.target.id}: dict[str, str] = {value!r}\n"
                replacements.append((node.lineno - 1, node.end_lineno, replacement))
    if values:
        raise SystemExit("Missing embedded asset declaration: " + ", ".join(values))
    if args.check:
        if replacements:
            print("Embedded extension assets are stale; run python3 scripts/sync_embedded_extension.py")
            return 1
        print("Embedded extension assets match extension/.")
        return 0
    lines = source.splitlines(keepends=True)
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement]
    if replacements:
        source_path.write_text("".join(lines), encoding="utf-8")
    print("Embedded extension assets synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
