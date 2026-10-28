"""Render evidence for human review on top of the captured frame."""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from sheep_solver.core.models import Observation


def draw_debug(image: Image.Image, observation: Observation) -> Image.Image:
    result = image.convert("RGB").copy()
    draw = ImageDraw.Draw(result)
    font = ImageFont.load_default()
    for tile in observation.tiles:
        color = ("#12d070" if tile.clickable else
                 "#4da6ff" if tile.shade_state == "dark" else
                 "#f2a900" if tile.visibility == "partial" else "#b7bdc6")
        x, y, w, h = tile.bbox
        draw.rectangle((x, y, x + w, y + h), outline=color, width=3)
        cx, cy = tile.center
        draw.line((cx - 5, cy, cx + 5, cy), fill=color, width=2)
        draw.line((cx, cy - 5, cx, cy + 5), fill=color, width=2)
        number = tile.id.rsplit("-", 1)[-1]
        shade = {"normal": "B", "dark": "D", "unknown": "?"}[tile.shade_state]
        visibility = "F" if tile.visibility == "full" else "P"
        reason = ("?T" if tile.type_id is None else
                  "?S" if "low icon" in tile.clickability_reason else
                  "?E" if "background" in tile.clickability_reason else
                  "?O" if "overlapping" in tile.clickability_reason else
                  "?R" if "outside" in tile.clickability_reason else "")
        detail = (f"o{tile.occlusion_fraction:.0%}"
                  if tile.occlusion_fraction is not None and
                  tile.occlusion_source == "observed_overlap" else reason)
        labels = [(f"{number}:{tile.type_id or '?'} {tile.confidence:.2f} {shade}{visibility}", y + 2),
                  (f"{cx:.0f},{cy:.0f} {detail}", y + 14)]
        for line, label_y in labels:
            bounds = draw.textbbox((x + 2, label_y), line, font=font)
            draw.rectangle((bounds[0] - 1, bounds[1] - 1,
                            bounds[2] + 1, bounds[3] + 1), fill="#172016")
            draw.text((x + 2, label_y), line, fill=color, font=font)
    legend = ("green=click blue=dark amber=partial gray=uncertain; "
              "B/D/?=shade F/P=visible ?T/S/E/O/R=reason; "
              f"local coords; tray: {', '.join(observation.tray) or '(empty)'}")
    bounds = draw.textbbox((10, 10), legend, font=font)
    draw.rectangle((7, 7, bounds[2] + 3, bounds[3] + 3), fill="#172016")
    draw.text((10, 10), legend, fill="yellow", font=font)
    return result
