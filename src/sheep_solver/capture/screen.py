"""Physical-pixel Windows screen capture and coordinate mapping."""

from __future__ import annotations

import ctypes
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Iterator

from PIL import Image, ImageGrab


def set_dpi_awareness() -> None:
    """Call before creating Tk windows or taking screenshots."""
    if sys.platform != "win32":
        return
    user32 = ctypes.windll.user32
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


@dataclass(frozen=True, slots=True)
class Region:
    left: int
    top: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Region must have positive width and height")

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom

    def contains(self, x: int, y: int, *, margin: int = 0) -> bool:
        return self.left + margin <= x < self.right - margin and self.top + margin <= y < self.bottom - margin

    def to_screen(self, local_x: int, local_y: int) -> tuple[int, int]:
        if not (0 <= local_x < self.width and 0 <= local_y < self.height):
            raise ValueError("Local point outside region")
        return self.left + local_x, self.top + local_y

    @classmethod
    def from_drag(cls, x1: int, y1: int, x2: int, y2: int) -> Region:
        return cls(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))


@dataclass(frozen=True, slots=True)
class CapturedFrame:
    image: Image.Image
    region: Region
    timestamp: str
    source: str = "screen"
    _pixels: object | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def pixels(self):
        """One cached RGB array per frame, discarded with the frame."""
        if self._pixels is None:
            import numpy as np
            pixels = np.asarray(self.image if self.image.mode == "RGB" else self.image.convert("RGB"))
            object.__setattr__(self, "_pixels", pixels)
        return self._pixels

    def to_screen(self, local_x: int, local_y: int) -> tuple[int, int]:
        return self.region.to_screen(local_x, local_y)


def virtual_screen() -> Region:
    if sys.platform != "win32":
        raise RuntimeError("Live capture requires Windows")
    set_dpi_awareness()
    user32 = ctypes.windll.user32
    return Region(user32.GetSystemMetrics(76), user32.GetSystemMetrics(77),
                  user32.GetSystemMetrics(78), user32.GetSystemMetrics(79))


def capture_screen() -> Image.Image:
    if sys.platform != "win32":
        raise RuntimeError("Live capture requires Windows")
    set_dpi_awareness()
    return ImageGrab.grab(all_screens=True).convert("RGB")


def capture_region(region: Region) -> Image.Image:
    if sys.platform != "win32":
        raise RuntimeError("Live capture requires Windows")
    bounds = virtual_screen()
    if not (bounds.left <= region.left and bounds.top <= region.top and
            region.right <= bounds.right and region.bottom <= bounds.bottom):
        raise ValueError("Capture region outside virtual desktop; recalibrate")
    image = ImageGrab.grab(bbox=region.bbox, all_screens=True).convert("RGB")
    if image.size != (region.width, region.height):
        raise RuntimeError("Capture size differs from physical coordinates; check DPI settings")
    return image


def capture_frame(region: Region | None = None) -> CapturedFrame:
    actual = region or virtual_screen()
    image = capture_region(actual) if region else capture_screen()
    if image.size != (actual.width, actual.height):
        raise RuntimeError("Full screen capture size differs from virtual desktop")
    return CapturedFrame(image, actual, datetime.now(timezone.utc).isoformat())


def load_frame(path: str | Path, *, screen_origin: tuple[int, int] = (0, 0)) -> CapturedFrame:
    source = Path(path)
    with Image.open(source) as loaded:
        image = loaded.convert("RGB")
    region = Region(screen_origin[0], screen_origin[1], *image.size)
    return CapturedFrame(image, region, datetime.now(timezone.utc).isoformat(), str(source))


def save_frame(frame: CapturedFrame, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    frame.image.save(destination)


def timed_capture(region: Region, interval_seconds: float, stop: Event) -> Iterator[CapturedFrame]:
    if interval_seconds <= 0:
        raise ValueError("Interval must be positive")
    while not stop.is_set():
        yield capture_frame(region)
        stop.wait(interval_seconds)

