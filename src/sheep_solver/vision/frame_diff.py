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
