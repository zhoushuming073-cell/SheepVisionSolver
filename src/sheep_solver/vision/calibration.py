"""Conservative anchor based calibration of the classic green game layout."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np
from PIL import Image

from sheep_solver.capture.screen import Region


@dataclass(frozen=True)
class Layout:
    content: Region  # relative to the captured window client
    board: Region    # relative to content
    tray: Region
    left_pile: Region
    right_pile: Region
    tools: Region
    confidence: float

    def regions_in_image(self) -> tuple[Region, Region, Region, Region, Region]:
        """Map inferred content-local ROIs back to the user-selected image."""
        def offset(region: Region) -> Region:
            return Region(self.content.left + region.left, self.content.top + region.top,
                          region.width, region.height)

        return tuple(offset(region) for region in
                     (self.board, self.tray, self.left_pile, self.right_pile, self.tools))


def tray_scales_from_slots(tray: Region, original_card_width: float,
                           capacity: int = 7) -> tuple[float, ...]:
    """Estimate card scale from the visible seven-slot tray interior width."""
    if original_card_width <= 0 or capacity <= 0:
        raise ValueError("Invalid card width or tray capacity")
    estimate = tray.width / (capacity * original_card_width)
    return tuple(round(max(.25, estimate + delta), 3) for delta in (-.03, 0, .03))


def locate_layout(image: Image.Image) -> Layout | None:
    rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    green = ((rgb[:, :, 1] > 175) & (rgb[:, :, 1] > rgb[:, :, 0] * 1.2) &
             (rgb[:, :, 0] > 105) & (rgb[:, :, 2] > 65))
    columns = np.where(green.mean(axis=0) > .35)[0]
    rows = np.where(green.mean(axis=1) > .35)[0]
    if len(columns) < width * .35 or len(rows) < height * .4:
        return None
    left, right = int(columns[0]), int(columns[-1]) + 1
    top, bottom = int(rows[0]), int(rows[-1]) + 1
    w, h = right - left, bottom - top
    if w < 350 or h < 600 or not .45 < w / h < 1.5:
        return None
    crop = rgb[top:bottom, left:right]
    brown = ((crop[:, :, 0] > 95) & (crop[:, :, 0] < 205) &
             (crop[:, :, 1] > 55) & (crop[:, :, 1] < 155) &
             (crop[:, :, 2] < 110))
    band = brown[:, int(w * .1):int(w * .9)].mean(axis=1)
    tray_rows = np.where((band > .32) &
                         (np.arange(h) > h * .55) & (np.arange(h) < h * .995))[0]
    if len(tray_rows) < h * .025:
        return None
    all_tray_rows = tray_rows
    clusters = np.split(tray_rows, np.where(np.diff(tray_rows) > max(8, round(h * .025)))[0] + 1)
    tray_rows = max(clusters, key=len)
    if len(tray_rows) < h * .025:
        return None
    nearby = all_tray_rows[all_tray_rows >= tray_rows[0] - round(h * .06)]
    tray_top = max(0, int(nearby[0]) - round(h * .01))
    tray_bottom = min(h, int(tray_rows[-1]) + round(h * .015))
    # The outer playable columns and the lowest central row extend beyond the
    # older 18–82% / 58% crop on the current WeChat desktop layout.
    board_top = round(h * .11)
    board_bottom = min(tray_top - round(h * .10), round(h * .66))
    board = Region(round(w * .10), board_top, round(w * .80),
                   board_bottom - board_top)
    tray = Region(round(w * .07), tray_top, round(w * .86), tray_bottom - tray_top)
    if board.bottom >= tray.top or tray.height < 30:
        return None
    pile_y = round(h * .565)
    pile_h = min(round(h * .15), tray.top - pile_y)
    if pile_h <= 0:
        return None
    return Layout(Region(left, top, w, h), board, tray,
                  Region(round(w * .10), pile_y, round(w * .24), pile_h),
                  Region(round(w * .66), pile_y, round(w * .24), pile_h),
                  Region(round(w * .1), round(h * .88), round(w * .8), round(h * .11)),
                  confidence=min(.95, .5 + len(tray_rows) / h))
