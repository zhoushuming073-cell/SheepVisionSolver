"""Screen-space geometry and the APK's black-mask sprite model.

Geometry here is a visual hypothesis. Only an observed brighter card is used as
an occluder; an inferred layer is never treated as a game-state fact.
"""

from __future__ import annotations

import numpy as np


def visible_mask(x: int, y: int, width: int, height: int,
                 blockers: tuple[tuple[int, int, int, int], ...]) -> np.ndarray:
    """Mask the union of rectangular blockers over a candidate card."""
    mask = np.ones((height, width), dtype=bool)
    for bx, by, bw, bh in blockers:
        left, top = max(x, bx), max(y, by)
        right, bottom = min(x + width, bx + bw), min(y + height, by + bh)
        if right > left and bottom > top:
            mask[top - y:bottom - y, left - x:right - x] = False
    return mask


def apply_black_mask(rgb: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Composite the extracted black sprite at its final node opacity (255)."""
    value = rgb.astype(np.float32) * (1.0 - alpha.astype(np.float32)[..., None] / 255.0)
    return np.rint(value).clip(0, 255).astype(np.uint8)


def masked_similarity(actual: np.ndarray, expected: np.ndarray,
                      mask: np.ndarray) -> tuple[float, float]:
    """Return normalized correlation and mean absolute RGB error on visible pixels."""
    if np.count_nonzero(mask) < 40:
        return 0.0, 255.0
    left = actual[mask].astype(np.float32).reshape(-1)
    right = expected[mask].astype(np.float32).reshape(-1)
    delta_left, delta_right = left - left.mean(), right - right.mean()
    denominator = float(np.linalg.norm(delta_left) * np.linalg.norm(delta_right))
    correlation = float(np.dot(delta_left, delta_right) / denominator) if denominator > 1e-6 else 0.0
    return correlation, float(np.mean(np.abs(left - right)))
