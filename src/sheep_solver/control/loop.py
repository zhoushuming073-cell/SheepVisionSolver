"""One observed action at a time, with a fresh pre-click check and feedback."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import Event
from typing import Callable

import numpy as np

from sheep_solver.capture.screen import CapturedFrame, Region, capture_frame
from sheep_solver.core.models import Observation
from sheep_solver.planning.basic import Action, BasicPlanner, Decision
from sheep_solver.vision.detector import TemplateDetector, VisionProfile

from .mouse import SafeMouse


@dataclass(frozen=True, slots=True)
class StepOutcome:
    success: bool
    reason: str
    decision: Decision
    before: Observation | None = None
    after: Observation | None = None
    after_frame: CapturedFrame | None = None


class VisualController:
    def __init__(self, detector: TemplateDetector, profile: VisionProfile,
                 game_region: Region, *, planner: BasicPlanner | None = None,
                 mouse: SafeMouse | None = None,
                 stop_event: Event | None = None,
                 capture: Callable[[Region], CapturedFrame] = capture_frame,
                 animation_delay: float = .9, log_path: str | Path = "data/logs/actions.jsonl"):
        self.detector = detector
        self.profile = profile
        self.game_region = game_region
        self.planner = planner or BasicPlanner()
        self.stop_event = stop_event or Event()
        self.mouse = mouse or SafeMouse(stop_event=self.stop_event)
        self.capture = capture
        self.animation_delay = animation_delay
        self.log_path = Path(log_path)

    @staticmethod
    def _changed_fraction(a: CapturedFrame, b: CapturedFrame, region: Region) -> float:
        first = np.asarray(a.image.convert("RGB"))[region.top:region.bottom, region.left:region.right]
        second = np.asarray(b.image.convert("RGB"))[region.top:region.bottom, region.left:region.right]
        if first.shape != second.shape:
            return 1.0
        return float(np.mean(np.max(np.abs(first.astype(np.int16) - second.astype(np.int16)), axis=2) > 25))

    @staticmethod
    def _target_still_visible(after: Observation, action: Action) -> bool:
        return any(tile.type_id == action.type_id and
                   abs(tile.center[0] - action.local_x) < tile.width * .3 and
                   abs(tile.center[1] - action.local_y) < tile.height * .3
                   for tile in after.tiles)

    def _record(self, outcome: StepOutcome) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "success": outcome.success, "reason": outcome.reason,
            "candidates": outcome.decision.candidates,
            "action": asdict(outcome.decision.action) if outcome.decision.action else None,
            "decision_reason": outcome.decision.reason,
            "before_timestamp": outcome.before.timestamp if outcome.before else None,
            "after_timestamp": outcome.after.timestamp if outcome.after else None,
            "before_tray": outcome.before.tray if outcome.before else None,
            "after_tray": outcome.after.tray if outcome.after else None,
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False) + "\n")

    def run_once(self, manual_tile_id: str | None = None,
                 manual_expected: tuple[str, int, int] | None = None) -> StepOutcome:
        if self.stop_event.is_set():
            return StepOutcome(False, "已停止", Decision((), None, "已停止"))
        try:
            before_frame = self.capture(self.game_region)
            before = self.detector.detect(before_frame, self.profile)
            decision = self.planner.decide(before)
            if manual_tile_id is not None:
                tile = next((tile for tile in before.tiles if tile.id == manual_tile_id and tile.clickable and tile.type_id), None)
                if (manual_expected is None or tile is None or tile.type_id != manual_expected[0] or
                        abs(tile.center[0] - manual_expected[1]) > 5 or
                        abs(tile.center[1] - manual_expected[2]) > 5):
                    tile = None
                if tile is None:
                    decision = Decision(decision.candidates, None, "人工指定的牌不可点击或已变化")
                else:
                    local_x, local_y = round(tile.center[0]), round(tile.center[1])
                    decision = Decision(decision.candidates,
                                        Action(tile.id, tile.type_id, local_x, local_y,
                                               self.game_region.left + local_x,
                                               self.game_region.top + local_y, "人工选择已识别方块"),
                                        "人工选择已识别方块")
            if decision.action is None:
                outcome = StepOutcome(False, decision.reason, decision, before=before)
                self._record(outcome)
                return outcome
            fresh = self.capture(self.game_region)
            if (self._changed_fraction(before_frame, fresh, self.profile.board) > .03 or
                    self._changed_fraction(before_frame, fresh, self.profile.tray) > .03):
                outcome = StepOutcome(False, "点击前画面已变化，需重新识别", decision, before=before)
                self._record(outcome)
                return outcome
            fresh_observation = self.detector.detect(fresh, self.profile)
            target = next((tile for tile in fresh_observation.tiles
                           if tile.type_id == decision.action.type_id and tile.clickable and
                           tile.id == decision.action.tile_id and
                           abs(tile.center[0] - decision.action.local_x) <= 5 and
                           abs(tile.center[1] - decision.action.local_y) <= 5), None)
            if (target is None or fresh_observation.tray != before.tray or
                    fresh_observation.tray_confidence < .95):
                outcome = StepOutcome(False, "点击前目标牌或卡槽已变化，需重新识别", decision, before=before)
                self._record(outcome)
                return outcome
            self.mouse.click(decision.action, self.game_region, self.profile.board)
            if self.stop_event.wait(self.animation_delay):
                outcome = StepOutcome(False, "点击后收到停止命令", decision, before=before)
                self._record(outcome)
                return outcome
            after_frame = self.capture(self.game_region)
            after = self.detector.detect(after_frame, self.profile)
            board_change = self._changed_fraction(before_frame, after_frame, self.profile.board)
            tray_change = self._changed_fraction(before_frame, after_frame, self.profile.tray)
            changed = board_change > .005 or tray_change > .005
            expected = (not self._target_still_visible(after, decision.action) or
                        before.tray != after.tray)
            success = changed and expected and after.tray_confidence >= .95
            reason = "点击后画面变化已确认" if success else "点击反馈异常或卡槽识别不可靠，已停止"
            outcome = StepOutcome(success, reason, decision, before, after, after_frame)
            self._record(outcome)
            return outcome
        except Exception as exc:
            outcome = StepOutcome(False, f"异常：{type(exc).__name__}: {exc}",
                                  Decision((), None, "执行异常"))
            self._record(outcome)
            return outcome
