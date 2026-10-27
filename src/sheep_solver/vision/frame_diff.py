"""Cheap bounded comparisons used to decide whether recognition is necessary."""

from __future__ import annotations

import numpy as np

from sheep_solver.capture.screen import CapturedFrame, Region


def changed_fraction(before: CapturedFrame, after: CapturedFrame, region: Region,
                     *, stride: int = 4, threshold: int = 25) -> float:
    if stride < 1:
        raise ValueError("stride must be positive")
    if before.image.size != after.image.size:
        return 1.0
    first = before.pixels[region.top:region.bottom:stride, region.left:region.right:stride]
    second = after.pixels[region.top:region.bottom:stride, region.left:region.right:stride]
    if first.shape != second.shape:
        return 1.0
    if np.array_equal(first, second):
        return 0.0
    delta = np.max(np.abs(first.astype(np.int16) - second.astype(np.int16)), axis=2)
    return float(np.count_nonzero(delta > threshold) / delta.size)


def max_patch_changed_fraction(before: CapturedFrame, after: CapturedFrame,
                               region: Region, *, stride: int = 4,
                               patch_pixels: int = 64, threshold: int = 25) -> float:
    """Detect a moving card even when it occupies little of a long travel corridor."""
    if stride < 1 or patch_pixels < stride:
        raise ValueError("Invalid motion sampling geometry")
    if before.image.size != after.image.size:
        return 1.0
    first = before.pixels[region.top:region.bottom:stride, region.left:region.right:stride]
    second = after.pixels[region.top:region.bottom:stride, region.left:region.right:stride]
    if first.shape != second.shape or first.size == 0:
        return 1.0
    if np.array_equal(first, second):
        return 0.0
    changed = np.max(np.abs(first.astype(np.int16) - second.astype(np.int16)), axis=2) > threshold
    block = max(1, patch_pixels // stride)
    ys = np.arange(0, changed.shape[0], block)
    xs = np.arange(0, changed.shape[1], block)
    counts = np.add.reduceat(np.add.reduceat(changed.astype(np.uint16), ys, axis=0), xs, axis=1)
    heights = np.minimum(block, changed.shape[0] - ys)
    widths = np.minimum(block, changed.shape[1] - xs)
    return float(np.max(counts / (heights[:, None] * widths[None, :])))
