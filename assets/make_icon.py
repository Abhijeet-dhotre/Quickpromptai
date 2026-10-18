"""Generate the original QuickPrompt AI icon (assets/icon.png + icon.ico).

Run:  python assets/make_icon.py
Requires Pillow (see requirements-dev.txt).
"""
from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw, ImageFilter

SIZE = 256


def _lerp(a: int, b: int, t: float) -> int:
    return int(a + (b - a) * t)


def make_icon(size: int = SIZE) -> Image.Image:
    """Blue-violet gradient rounded square with a white lightning bolt."""
    c1 = (79, 110, 247)    # #4f6ef7
    c2 = (155, 92, 246)    # #9b5cf6

    # Diagonal gradient background.
    gradient = Image.new("RGB", (size, size))
    pixels = gradient.load()
    for y in range(size):
        for x in range(size):
            t = (x + y) / (2 * size)
            pixels[x, y] = (_lerp(c1[0], c2[0], t),
                            _lerp(c1[1], c2[1], t),
                            _lerp(c1[2], c2[2], t))

    # Rounded-square mask.
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = size // 5
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)

    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    icon.paste(gradient, (0, 0), mask)

    # Lightning bolt.
    bolt = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(bolt)
    s = size / 256.0
    points = [
        (150 * s, 36 * s),
        (74 * s, 140 * s),
        (119 * s, 140 * s),
        (100 * s, 220 * s),
        (186 * s, 112 * s),
        (140 * s, 112 * s),
    ]
    glow = bolt.filter(ImageFilter.GaussianBlur(6 * s))
    bdraw.polygon(points, fill=(255, 255, 255, 255))
    icon = Image.alpha_composite(icon, glow)
    icon = Image.alpha_composite(icon, bolt)
    return icon


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    icon = make_icon()
    png_path = os.path.join(here, "icon.png")
    ico_path = os.path.join(here, "icon.ico")
    icon.save(png_path, "PNG")
    icon.save(
        ico_path,
        "ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"Wrote {png_path}")
    print(f"Wrote {ico_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
