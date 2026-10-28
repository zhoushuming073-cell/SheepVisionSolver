"""Visible-only tile memory and transactional tray updates.

This module never sees simulator FullState or hidden tile arrays.  It accepts
only screen observations and confirmed click events.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace

from sheep_solver.capture.screen import Region
from sheep_solver.core.models import Observation, ObservedTile


class StateConflict(ValueError):
    """An observation cannot be reconciled safely with remembered tiles."""


@dataclass(frozen=True, slots=True)
class PendingClick:
    tile_id: str
    type_id: str
    expected_tray: tuple[str, ...]


def tray_after_click(tray: tuple[str, ...], type_id: str) -> tuple[str, ...]:
    """Insert after the last matching tile; eliminate the third matching tile."""
    items = list(tray)
    for index in range(len(items) - 1, -1, -1):
        if items[index] != type_id:
            continue
        if index > 0 and items[index - 1] == type_id:
            del items[index - 1:index + 1]
            return tuple(items)
        items.insert(index + 1, type_id)
        return tuple(items)
    items.append(type_id)
    return tuple(items)


def _iou(left: ObservedTile, right: ObservedTile) -> float:
    x1 = max(left.x, right.x)
    y1 = max(left.y, right.y)
    x2 = min(left.x + left.width, right.x + right.width)
    y2 = min(left.y + left.height, right.y + right.height)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = left.width * left.height + right.width * right.height - intersection
    return intersection / union if union else 0.0


def _touches(tile: ObservedTile, region: Region) -> bool:
    return (tile.x < region.right and tile.x + tile.width > region.left and
            tile.y < region.bottom and tile.y + tile.height > region.top)


def _contained(tile: ObservedTile, region: Region) -> bool:
    return (tile.x >= region.left and tile.y >= region.top and
            tile.x + tile.width <= region.right and tile.y + tile.height <= region.bottom)


class VisibleMemory:
    """One current visible object per ID; type counts are derived on demand."""

    def __init__(self, observation: Observation):
        if observation.tray_confidence < .95 or "unknown" in observation.tray:
            raise StateConflict("Initial tray is not visually confirmed")
        self.tiles: dict[str, ObservedTile] = {}
        self.next_id = 1
        self.tray = tuple(observation.tray)
        self.piles = tuple(observation.piles)
        self.status = observation.status
        self.game_region = observation.game_region
        self.screenshot_size = observation.screenshot_size
        self.timestamp = observation.timestamp
        self.pending: PendingClick | None = None
        self._partial_misses: dict[str, int] = {}
        for tile in sorted(observation.tiles, key=lambda item: (item.y, item.x, item.type_id or "")):
            self._add(tile)

    def _add(self, tile: ObservedTile) -> str:
        tile_id = f"tile_{self.next_id:04d}"
        self.next_id += 1
        self.tiles[tile_id] = replace(tile, id=tile_id)
        return tile_id

    @property
    def full(self) -> tuple[ObservedTile, ...]:
        return tuple(tile for tile in self.tiles.values() if tile.visibility == "full")

    @property
    def partial(self) -> tuple[ObservedTile, ...]:
        return tuple(tile for tile in self.tiles.values() if tile.visibility == "partial")

    @property
    def type_counts(self) -> Counter[str]:
        return Counter(tile.type_id for tile in self.tiles.values() if tile.type_id is not None)

    def observation(self) -> Observation:
        return Observation(tuple(self.tiles.values()), self.piles, self.tray, self.status,
                           self.game_region, self.screenshot_size, self.timestamp)

    def begin_click(self, tile_id: str, type_id: str) -> PendingClick:
        if self.pending is not None:
            raise StateConflict("A click is already pending")
        tile = self.tiles.get(tile_id)
        if tile is None or tile.type_id != type_id or not tile.clickable or tile.visibility != "full":
            raise StateConflict("Target tile is missing, changed, or not confirmed clickable")
        self.pending = PendingClick(tile_id, type_id, tray_after_click(self.tray, type_id))
        return self.pending

    def abort_click(self) -> None:
        self.pending = None

    def merge(self, detected: tuple[ObservedTile, ...], region: Region,
              *, clicked_id: str | None = None, timestamp: str | None = None) -> None:
        """Associate current detections in one region; leave untouched regions alone."""
        if clicked_id is not None and clicked_id not in self.tiles:
            raise StateConflict("Clicked tile identity is stale")
        updated = dict(self.tiles)
        partial_misses = dict(self._partial_misses)
        if clicked_id is not None:
            del updated[clicked_id]
            partial_misses.pop(clicked_id, None)
        available = {tile_id for tile_id, tile in updated.items() if _touches(tile, region)}
        incoming = sorted(detected, key=lambda item: (-item.confidence, item.y, item.x))
        for observed in incoming:
            ranked: list[tuple[float, str]] = []
            for tile_id in available:
                previous = updated[tile_id]
                overlap = _iou(previous, observed)
                if overlap >= .35:
                    ranked.append((overlap, tile_id))
            ranked.sort(reverse=True)
            if ranked:
                _, tile_id = ranked[0]
                previous = updated[tile_id]
                if (previous.type_id is not None and observed.type_id is not None and
                        previous.type_id != observed.type_id):
                    raise StateConflict(f"Tile {tile_id} changed type unexpectedly")
                available.remove(tile_id)
                partial_misses.pop(tile_id, None)
                updated[tile_id] = replace(observed, id=tile_id,
                                           type_id=observed.type_id or previous.type_id)
            else:
                tile_id = f"tile_{self.next_id:04d}"
                self.next_id += 1
                updated[tile_id] = replace(observed, id=tile_id)
        missing_full = [tile_id for tile_id in available
                        if updated[tile_id].visibility == "full" and _contained(updated[tile_id], region)]
        if missing_full:
            raise StateConflict(f"Previously full tiles disappeared: {missing_full}")
        for tile_id in available:
            old = updated[tile_id]
            if old.visibility != "partial" or not _contained(old, region):
                continue
            misses = partial_misses.get(tile_id, 0) + 1
            if misses >= 2:
                del updated[tile_id]
                partial_misses.pop(tile_id, None)
            else:
                partial_misses[tile_id] = misses
        self.tiles = updated
        self._partial_misses = partial_misses
        self.timestamp = timestamp or self.timestamp

    def commit_click(self, detected: tuple[ObservedTile, ...], region: Region,
                     *, confirmed: bool, timestamp: str | None = None) -> None:
        pending = self.pending
        if pending is None:
            raise StateConflict("No pending click")
        if not confirmed:
            self.abort_click()
            raise StateConflict("Click was not confirmed")
        previous_tiles = self.tiles
        previous_next_id = self.next_id
        previous_partial_misses = self._partial_misses
        try:
            self.merge(detected, region, clicked_id=pending.tile_id, timestamp=timestamp)
        except Exception:
            self.tiles = previous_tiles
            self.next_id = previous_next_id
            self._partial_misses = previous_partial_misses
            self.pending = None
            raise
        self.tray = pending.expected_tray
        self.pending = None
        if len(self.tray) >= 7:
            self.status = "lost"
