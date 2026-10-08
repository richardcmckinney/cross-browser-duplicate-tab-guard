#!/usr/bin/env python3
"""Render the extension icon set (16, 32, 48 and 128 px) into extension/icons. Needs Pillow."""
import os
from PIL import Image, ImageDraw

def render_master_icon(size=1024):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    
    # 1. Base squircle / rounded rect background
    pad = int(size * 0.06)
    radius = int(size * 0.22)
    bg_box = [pad, pad, size - pad, size - pad]
    
    # Gradient simulation from top-left (#1e1b4b) to bottom-right (#0f172a)
    for y in range(pad, size - pad):
        factor = (y - pad) / (size - 2 * pad)
        r = int(24 + (15 - 24) * factor)
        g = int(32 + (23 - 32) * factor)
        b = int(68 + (42 - 68) * factor)
        draw.line([(pad, y), (size - pad, y)], fill=(r, g, b, 255))
        
    # Mask to rounded rect
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(bg_box, radius=radius, fill=255)
    
    # Apply mask to background
    bg_img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_img.paste(img, (0, 0), mask=mask)
    
    draw = ImageDraw.Draw(bg_img)
    # Subtle inner border
    draw.rounded_rectangle(bg_box, radius=radius, outline=(99, 102, 241, 120), width=int(size * 0.02))
    
    # 2. Draw Back Tab (representing other browser, slightly shifted right-up)
    back_x0 = int(size * 0.30)
    back_y0 = int(size * 0.22)
    back_x1 = int(size * 0.78)
    back_y1 = int(size * 0.62)
    back_rad = int(size * 0.08)
    
    draw.rounded_rectangle(
        [back_x0, back_y0, back_x1, back_y1],
        radius=back_rad,
        fill=(99, 102, 241, 160),
        outline=(165, 180, 252, 220),
        width=int(size * 0.022)
    )
    # Back tab header notch
    draw.line(
        [(back_x0 + int(size * 0.08), back_y0 + int(size * 0.10)), (back_x1 - int(size * 0.08), back_y0 + int(size * 0.10))],
        fill=(199, 210, 254, 180),
        width=int(size * 0.02)
    )
    
    # 3. Draw Front Tab (representing primary/active tab, shifted left-down)
    front_x0 = int(size * 0.20)
    front_y0 = int(size * 0.38)
    front_x1 = int(size * 0.70)
    front_y1 = int(size * 0.78)
    front_rad = int(size * 0.08)
    
    draw.rounded_rectangle(
        [front_x0, front_y0, front_x1, front_y1],
        radius=front_rad,
        fill=(30, 41, 59, 255),
        outline=(56, 189, 248, 255),
        width=int(size * 0.028)
    )
    # Front tab header accent
    draw.line(
        [(front_x0 + int(size * 0.07), front_y0 + int(size * 0.10)), (front_x1 - int(size * 0.07), front_y0 + int(size * 0.10))],
        fill=(56, 189, 248, 200),
        width=int(size * 0.022)
    )
    
    # 4. Draw Guard Shield / Check Badge in bottom right
    badge_cx = int(size * 0.72)
    badge_cy = int(size * 0.72)
    badge_r = int(size * 0.18)
    
    # Outer badge circle
    draw.ellipse(
        [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
        fill=(16, 185, 129, 255), # Emerald green guard indicator
        outline=(255, 255, 255, 240),
        width=int(size * 0.025)
    )
    
    # Guard checkmark inside badge
    chk_w = int(size * 0.038)
    p1 = (badge_cx - int(badge_r * 0.45), badge_cy)
    p2 = (badge_cx - int(badge_r * 0.10), badge_cy + int(badge_r * 0.38))
    p3 = (badge_cx + int(badge_r * 0.48), badge_cy - int(badge_r * 0.38))
    
    draw.line([p1, p2], fill=(255, 255, 255, 255), width=chk_w)
    draw.line([p2, p3], fill=(255, 255, 255, 255), width=chk_w)
    
    return bg_img

def main():
    # The extension icons live in extension/icons next to this scripts/ folder.
    target_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "extension", "icons")
    os.makedirs(target_dir, exist_ok=True)
    
    master = render_master_icon(size=1024)
    
    sizes = [16, 32, 48, 128]
    for sz in sizes:
        resized = master.resize((sz, sz), Image.Resampling.LANCZOS)
        out_path = os.path.join(target_dir, f"icon{sz}.png")
        resized.save(out_path, format="PNG", optimize=True)
        print(f"Generated {out_path} ({sz}x{sz}, {os.path.getsize(out_path)} bytes)")
        
    print("All icons successfully generated!")

if __name__ == "__main__":
    main()
