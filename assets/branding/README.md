# Branding artwork

The repository banner and browser icon artwork were supplied by the project owner for this release on November 12, 2026.

- `banner.jpg`: original supplied repository banner, preserved without modification.
- `icon-original.jpg`: original supplied icon JPEG, including its baked checkerboard background, retained for provenance.
- `icon-master.png`: transparent PNG derived from the supplied icon using background removal. The gray-and-white checkerboard exterior was removed; the dark rounded-square artwork, layered browser windows and cyan shield remain the icon design.

The PNG is the canonical icon source. Regenerate the packaged toolbar and installation icons with:

```bash
python3 scripts/generate_icons.py
python3 scripts/sync_embedded_extension.py
```

Pillow is a development-only requirement for artwork regeneration. Use `python3 scripts/generate_icons.py --check` and `python3 scripts/sync_embedded_extension.py --check` to check for drift. All four packaged sizes (16, 32, 48 and 128 pixels) are downsampled from the same master with Lanczos resampling. PNG encoding is reproducible with the same Pillow runtime; no timestamps or random data are added.

The original banner links to the public Chrome Web Store listing at the top of the project README. Repository branding assets are excluded from the extension upload package.
