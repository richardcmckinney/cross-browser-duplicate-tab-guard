#!/usr/bin/env python3
"""Generate extension PNGs from the approved transparent branding master.

Pillow is needed only when regenerating artwork, not to run the companion.
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
MASTER = ROOT / "assets" / "branding" / "icon-master.png"
TARGET = ROOT / "extension" / "icons"
SIZES = (16, 32, 48, 128)


def icon_bytes(master: Image.Image, size: int) -> bytes:
    output = io.BytesIO()
    master.resize((size, size), Image.Resampling.LANCZOS).save(
        output, format="PNG", optimize=True
    )
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report stale icons without writing.")
    args = parser.parse_args()
    with Image.open(MASTER) as source:
        if source.mode != "RGBA" or source.width != source.height:
            parser.error("The branding master must be a square RGBA PNG.")
        if source.getchannel("A").getextrema() != (0, 255):
            parser.error("The branding master must include transparent and opaque pixels.")
        master = source.copy()
    stale = []
    for size in SIZES:
        destination = TARGET / f"icon{size}.png"
        data = icon_bytes(master, size)
        if args.check:
            if not destination.is_file() or destination.read_bytes() != data:
                stale.append(destination.relative_to(ROOT).as_posix())
        else:
            TARGET.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            print(f"Generated {destination.relative_to(ROOT)} ({size}x{size}, {len(data)} bytes)")
    if stale:
        print("Stale icons: " + ", ".join(stale))
        return 1
    print("Extension icons match the branding master." if args.check else "Extension icons generated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
