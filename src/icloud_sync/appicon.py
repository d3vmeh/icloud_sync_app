"""App icon, drawn with PIL so the repo ships no binary assets."""

from __future__ import annotations

BLUE = (10, 132, 255, 255)
WHITE = (255, 255, 255, 255)


def make_image(size: int = 64, *, tile: bool = False):
    """Cloud glyph. tile=True puts a white cloud on a rounded blue square
    (dock/launcher style); tile=False is a bare blue cloud (tray style)."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    if tile:
        radius = size * 0.22
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=BLUE)
        cloud, box = WHITE, (0.18, 0.24)  # inset and vertical shift of the glyph
    else:
        cloud, box = BLUE, (0.0, 0.0)

    inset, shift = box
    s = size * (1 - 2 * inset) / 64  # glyph designed on a 64px grid
    ox = size * inset
    oy = size * inset + size * shift * 0.3

    def xy(x1: float, y1: float, x2: float, y2: float) -> tuple[float, ...]:
        return (ox + x1 * s, oy + y1 * s, ox + x2 * s, oy + y2 * s)

    draw.ellipse(xy(6, 26, 32, 52), fill=cloud)
    draw.ellipse(xy(18, 12, 48, 42), fill=cloud)
    draw.ellipse(xy(34, 26, 60, 52), fill=cloud)
    draw.rectangle(xy(19, 36, 47, 52), fill=cloud)
    return img
