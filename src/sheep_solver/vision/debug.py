"""Render evidence for human review on top of the captured frame."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from sheep_solver.core.models import Observation


def draw_debug(image: Image.Image, observation: Observation) -> Image.Image:
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    font = ImageFont.load_default()
    for tile in observation.tiles:
        color = "#12d070" if tile.clickable else "#f2a900"
        x, y, w, h = tile.bbox
        draw.rectangle((x, y, x + w, y + h), outline=color, width=3)
        cx, cy = tile.center
        draw.line((cx - 5, cy, cx + 5, cy), fill=color, width=2)
        draw.line((cx, cy - 5, cx, cy + 5), fill=color, width=2)
        number = tile.id.rsplit("-", 1)[-1]
        lines = (f"#{number} T{tile.type_id or '?'} {tile.confidence:.2f}",
                 f"{'C' if tile.clickable else '?'} {cx:.0f},{cy:.0f}")
        for line, label_y in ((lines[0], y + 2), (lines[1], y + h - 13)):
            bounds = draw.textbbox((x + 2, label_y), line, font=font)
            draw.rectangle((bounds[0] - 1, bounds[1] - 1,
                            bounds[2] + 1, bounds[3] + 1), fill="#172016")
            draw.text((x + 2, label_y), line, fill=color, font=font)
    legend = f"C=clickable  ?=check  coordinates=local  tray: {', '.join(observation.tray) or '(empty)'}"
    bounds = draw.textbbox((10, 10), legend, font=font)
    draw.rectangle((7, 7, bounds[2] + 3, bounds[3] + 3), fill="#172016")
    draw.text((10, 10), legend, fill="yellow", font=font)
    return result
