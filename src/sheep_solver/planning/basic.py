"""A deliberately conservative policy using Observation only."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sheep_solver.core.models import Observation, ObservedTile


@dataclass(frozen=True, slots=True)
class Action:
    tile_id: str
    type_id: str
    local_x: int
    local_y: int
    screen_x: int
    screen_y: int
    reason: str


@dataclass(frozen=True, slots=True)
class Decision:
    candidates: tuple[str, ...]
    action: Action | None
    reason: str


class BasicPlanner:
    def __init__(self, *, min_confidence: float = .88, min_clickability: float = .78,
                 tray_capacity: int = 7):
        self.min_confidence = min_confidence
        self.min_clickability = min_clickability
        self.tray_capacity = tray_capacity

    def decide(self, observation: Observation) -> Decision:
        if observation.status != "playing":
            return Decision((), None, "游戏未处于可操作状态")
        if observation.game_region is None or observation.screenshot_size is None:
            return Decision((), None, "缺少屏幕区域或截图尺寸")
        if observation.tray_confidence < .95 or "unknown" in observation.tray:
            return Decision((), None, "卡槽识别不确定")
        candidates = tuple(tile for tile in observation.tiles
                           if tile.region == "main" and tile.clickable and tile.type_id
                           and tile.confidence >= self.min_confidence
                           and tile.clickable_confidence >= self.min_clickability)
        labels = tuple(f"{tile.id}:{tile.type_id}@{tuple(round(n) for n in tile.center)}"
                       for tile in candidates)
        if not candidates:
            return Decision(labels, None, "没有高置信度且确认可点击的主牌")
        counts = Counter(observation.tray)
        if len(observation.tray) >= self.tray_capacity:
            return Decision(labels, None, "卡槽已满")
        immediate = [tile for tile in candidates if counts[tile.type_id] >= 2]
        if immediate:
            selected = self._select(immediate)
            return Decision(labels, self._action(selected, observation, "直接完成三消"), "直接完成三消")
        if len(observation.tray) >= self.tray_capacity - 2:
            return Decision(labels, None, "卡槽接近满格且没有直接三消")
        paired = [tile for tile in candidates if counts[tile.type_id] == 1]
        if paired:
            selected = self._select(paired)
            return Decision(labels, self._action(selected, observation, "与卡槽已有牌配对"), "与卡槽已有牌配对")
        visible_counts = Counter(tile.type_id for tile in candidates)
        triple = [tile for tile in candidates if visible_counts[tile.type_id] >= 3]
        if triple:
            selected = self._select(triple)
            return Decision(labels, self._action(selected, observation, "当前可见同类牌至少三张"),
                            "当前可见同类牌至少三张")
        # A single unmatched card is a bounded risk while there is ample room.
        # Prefer a visible pair; never spend the final two slots speculatively.
        if len(observation.tray) <= self.tray_capacity - 3:
            ranked = sorted(candidates, key=lambda tile: (
                -visible_counts[tile.type_id], -tile.clickable_confidence,
                -tile.confidence, tile.y, tile.x, tile.id))
            selected = ranked[0]
            reason = "卡槽空间充足；选择当前可见同类较多的牌"
            return Decision(labels, self._action(selected, observation, reason), reason)
        return Decision(labels, None, "卡槽空间不足以安全尝试新类型")

    @staticmethod
    def _select(tiles: list[ObservedTile]) -> ObservedTile:
        return sorted(tiles, key=lambda tile: (-tile.clickable_confidence, -tile.confidence,
                                                tile.y, tile.x, tile.id))[0]

    @staticmethod
    def _action(tile: ObservedTile, observation: Observation, reason: str) -> Action:
        local_x, local_y = round(tile.center[0]), round(tile.center[1])
        left, top, _, _ = observation.game_region
        return Action(tile.id, tile.type_id or "unknown", local_x, local_y,
                      left + local_x, top + local_y, reason)


def choose_action(observation: Observation) -> Action | None:
    return BasicPlanner().decide(observation).action
