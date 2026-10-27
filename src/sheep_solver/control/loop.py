"""One transactional click at a time, using visible memory and local feedback."""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from threading import Event, Lock
from typing import Callable

from sheep_solver.capture.screen import CapturedFrame, Region, capture_frame
from sheep_solver.core.memory import StateConflict, VisibleMemory
from sheep_solver.core.models import Observation, ObservedTile
from sheep_solver.planning.basic import Action, BasicPlanner, Decision
from sheep_solver.vision.detector import TemplateDetector, VisionProfile
from sheep_solver.vision.frame_diff import changed_fraction, max_patch_changed_fraction

from .mouse import SafeMouse


@dataclass(frozen=True, slots=True)
class StepOutcome:
    success: bool
    reason: str
    decision: Decision
    before: Observation | None = None
    after: Observation | None = None
    after_frame: CapturedFrame | None = None
    metrics: dict[str, float | int] = field(default_factory=dict)


class VisualController:
    def __init__(self, detector: TemplateDetector, profile: VisionProfile,
                 game_region: Region, *, planner: BasicPlanner | None = None,
                 mouse: SafeMouse | None = None,
                 stop_event: Event | None = None,
                 capture: Callable[[Region], CapturedFrame] = capture_frame,
                 feedback_timeout: float = 1.6, poll_interval: float = .04,
                 settle_seconds: float = .12,
                 animation_delay: float | None = None,
                 log_path: str | Path = "data/logs/actions.jsonl"):
        self.detector = detector
        self.profile = profile
        self.game_region = game_region
        self.planner = planner or BasicPlanner()
        self.stop_event = stop_event or Event()
        self.mouse = mouse or SafeMouse(stop_event=self.stop_event)
        self.capture = capture
        self.feedback_timeout = feedback_timeout
        self.poll_interval = poll_interval
        self.settle_seconds = settle_seconds
        self.log_path = Path(log_path)
        self.memory: VisibleMemory | None = None
        self.last_frame: CapturedFrame | None = None
        self._step_lock = Lock()

    def initialize_from_observation(self, frame: CapturedFrame,
                                    observation: Observation) -> None:
        """Reuse a GUI result instead of scanning its frame again."""
        if frame.region != self.game_region or observation.game_region != frame.region.bbox:
            raise StateConflict("Calibration or frame origin changed")
        self.memory = VisibleMemory(observation)
        self.last_frame = frame

    def _capture(self, metrics: dict[str, float | int]) -> CapturedFrame:
        started = time.perf_counter()
        frame = self.capture(self.game_region)
        metrics["captures"] = int(metrics["captures"]) + 1
        metrics["capture_ms"] = float(metrics["capture_ms"]) + (time.perf_counter() - started) * 1000
        return frame

    def _full_detect(self, frame: CapturedFrame,
                     metrics: dict[str, float | int]) -> Observation:
        started = time.perf_counter()
        observation = self.detector.detect(frame, self.profile)
        metrics["full_detects"] = int(metrics["full_detects"]) + 1
        metrics["full_detect_ms"] = float(metrics["full_detect_ms"]) + (time.perf_counter() - started) * 1000
        return observation

    def _resync(self, frame: CapturedFrame,
                metrics: dict[str, float | int]) -> Observation | None:
        self.memory = None
        self.last_frame = None
        try:
            observation = self._full_detect(frame, metrics)
            self.initialize_from_observation(frame, observation)
            return self.memory.observation() if self.memory else None
        except (StateConflict, ValueError):
            return None

    @staticmethod
    def _affected_region(tile: ObservedTile, board: Region) -> Region:
        padding = round(max(tile.width, tile.height))
        left = max(board.left, round(tile.x) - padding)
        top = max(board.top, round(tile.y) - padding)
        right = min(board.right, round(tile.x + tile.width) + padding)
        bottom = min(board.bottom, round(tile.y + tile.height) + padding)
        return Region(left, top, right - left, bottom - top)

    @staticmethod
    def _tile_region(tile: ObservedTile, board: Region) -> Region:
        left = max(board.left, round(tile.x))
        top = max(board.top, round(tile.y))
        right = min(board.right, round(tile.x + tile.width))
        bottom = min(board.bottom, round(tile.y + tile.height))
        return Region(left, top, right - left, bottom - top)

    def _wait_for_feedback(self, before: CapturedFrame, tile: ObservedTile,
                           expected_occupancy: int,
                           metrics: dict[str, float | int]) -> tuple[CapturedFrame, bool]:
        target_region = self._tile_region(tile, self.profile.board)
        board, tray = self.profile.board, self.profile.tray
        travel_region = Region(min(board.left, tray.left), min(board.top, tray.top),
                               max(board.right, tray.right) - min(board.left, tray.left),
                               max(board.bottom, tray.bottom) - min(board.top, tray.top))
        deadline = time.monotonic() + self.feedback_timeout
        previous = before
        changed = False
        quiet = 0
        quiet_since: float | None = None
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if self.stop_event.wait(self.poll_interval):
                break
            current = self._capture(metrics)
            target_delta = changed_fraction(before, current, target_region, stride=2)
            tray_delta = changed_fraction(before, current, self.profile.tray, stride=4)
            changed = changed or target_delta > .05 or tray_delta > .015
            if changed:
                motion = max_patch_changed_fraction(previous, current, travel_region)
                if motion < .02 and self.detector.tray_card_count(current, self.profile) == expected_occupancy:
                    quiet += 1
                    quiet_since = quiet_since or time.monotonic()
                    if quiet >= 2 and time.monotonic() - quiet_since >= self.settle_seconds:
                        return current, True
                else:
                    quiet = 0
                    quiet_since = None
            previous = current
        return previous, False

    def _manual_decision(self, before: Observation, manual_id: str,
                         expected: tuple[str, int, int] | None,
                         base: Decision) -> Decision:
        if expected is None:
            return Decision(base.candidates, None, "人工选择缺少目标类型和位置")
        kind, x, y = expected
        tile = next((candidate for candidate in before.tiles
                     if candidate.type_id == kind and candidate.clickable and
                     abs(candidate.center[0] - x) <= 5 and
                     abs(candidate.center[1] - y) <= 5), None)
        if tile is None:
            return Decision(base.candidates, None, "人工指定的牌不可点击或已变化")
        if manual_id.startswith("tile_") and manual_id != tile.id:
            return Decision(base.candidates, None, "人工指定的目标 ID 与当前方块不匹配")
        local_x, local_y = round(tile.center[0]), round(tile.center[1])
        return Decision(base.candidates,
                        Action(tile.id, kind, local_x, local_y,
                               self.game_region.left + local_x,
                               self.game_region.top + local_y, "人工选择已识别方块"),
                        "人工选择已识别方块")

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
            "metrics": outcome.metrics,
        }
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(data, ensure_ascii=False) + "\n")

    def _run_step(self, manual_tile_id: str | None,
                  manual_expected: tuple[str, int, int] | None,
                  metrics: dict[str, float | int]) -> StepOutcome:
        if self.stop_event.is_set():
            return StepOutcome(False, "已停止", Decision((), None, "已停止"))
        if self.memory is None:
            initial_frame = self._capture(metrics)
            initial = self._full_detect(initial_frame, metrics)
            self.initialize_from_observation(initial_frame, initial)
        assert self.memory is not None and self.last_frame is not None
        before = self.memory.observation()
        decision = self.planner.decide(before)
        if manual_tile_id is not None:
            decision = self._manual_decision(before, manual_tile_id, manual_expected, decision)
        if decision.action is None:
            return StepOutcome(False, decision.reason, decision, before=before)

        target = self.memory.tiles.get(decision.action.tile_id)
        if target is None or target.type_id != decision.action.type_id:
            raise StateConflict("Decision target identity changed")
        preclick_started = time.perf_counter()
        fresh = self._capture(metrics)
        if (changed_fraction(self.last_frame, fresh, self.profile.board) > .03 or
                changed_fraction(self.last_frame, fresh, self.profile.tray) > .03):
            self.memory = None
            self.last_frame = None
            return StepOutcome(False, "点击前画面变化；已暂停，待稳定后重新识别", decision,
                               before, after_frame=fresh)
        if not self.detector.confirm_target(fresh, self.profile, target):
            self.memory = None
            self.last_frame = None
            return StepOutcome(False, "点击前目标牌验证失败；已暂停，待稳定后重新识别", decision,
                               before, after_frame=fresh)
        metrics["preclick_ms"] = (time.perf_counter() - preclick_started) * 1000

        self.memory.begin_click(target.id, target.type_id)
        try:
            self.mouse.click(decision.action, self.game_region, self.profile.board)
        except Exception:
            self.memory.abort_click()
            self.memory = None  # The backend may have sent a partial click.
            self.last_frame = None
            raise
        feedback_started = time.perf_counter()
        expected_tray = self.memory.pending.expected_tray
        after_frame, confirmed = self._wait_for_feedback(fresh, target,
                                                         len(expected_tray), metrics)
        metrics["feedback_ms"] = (time.perf_counter() - feedback_started) * 1000
        if self.stop_event.is_set():
            self.memory.abort_click()
            self.memory = None
            self.last_frame = None
            return StepOutcome(False, "点击后收到停止命令；下次需重新同步", decision, before,
                               after_frame=after_frame)
        if not confirmed:
            self.memory.abort_click()
            self.memory = None
            self.last_frame = None
            return StepOutcome(False, "点击反馈未稳定；停止并等待下次完整同步", decision,
                               before, after_frame=after_frame)
        if self.detector.confirm_target(after_frame, self.profile, target):
            self.memory.abort_click()
            after = self._resync(after_frame, metrics)
            return StepOutcome(False, "点击反馈未确认，已重新同步并停止", decision, before,
                               after, after_frame)

        affected = self._affected_region(target, self.profile.board)
        local_started = time.perf_counter()
        local_tiles = self.detector.detect_board_region(after_frame, self.profile, affected)
        metrics["local_detects"] = 1
        metrics["local_detect_ms"] = (time.perf_counter() - local_started) * 1000
        try:
            self.memory.commit_click(local_tiles, affected, confirmed=True,
                                     timestamp=after_frame.timestamp)
        except StateConflict:
            after = self._resync(after_frame, metrics)
            if after is None or after.tray != expected_tray:
                return StepOutcome(False, "局部状态冲突，完整同步后卡槽不一致", decision,
                                   before, after, after_frame)
            self.last_frame = after_frame
            return StepOutcome(True, "点击已确认；局部冲突后完整同步", decision,
                               before, after, after_frame)

        self.last_frame = after_frame
        observed_occupancy = self.detector.tray_card_count(after_frame, self.profile)
        if observed_occupancy != len(self.memory.tray):
            after = self._resync(after_frame, metrics)
            if after is None or after.tray != expected_tray:
                return StepOutcome(False, "卡槽模拟与屏幕不一致，已停止", decision,
                                   before, after, after_frame)
            return StepOutcome(True, "点击已确认；卡槽数量复核后完整同步", decision,
                               before, after, after_frame)
        after = self.memory.observation()
        return StepOutcome(True, "点击和局部状态变化已确认", decision, before, after, after_frame)

    def run_once(self, manual_tile_id: str | None = None,
                 manual_expected: tuple[str, int, int] | None = None) -> StepOutcome:
        if not self._step_lock.acquire(blocking=False):
            return StepOutcome(False, "已有未完成的点击事务", Decision((), None, "已有事务"))
        started = time.perf_counter()
        metrics: dict[str, float | int] = {
            "captures": 0, "full_detects": 0, "local_detects": 0,
            "capture_ms": 0.0, "full_detect_ms": 0.0,
            "preclick_ms": 0.0, "feedback_ms": 0.0, "local_detect_ms": 0.0,
        }
        try:
            try:
                outcome = self._run_step(manual_tile_id, manual_expected, metrics)
            except Exception as exc:
                if self.memory is not None:
                    self.memory.abort_click()
                self.memory = None  # Never reuse uncertain state after an exception.
                self.last_frame = None
                outcome = StepOutcome(False, f"异常：{type(exc).__name__}: {exc}",
                                      Decision((), None, "执行异常"))
            metrics["cycle_ms"] = (time.perf_counter() - started) * 1000
            outcome = replace(outcome, metrics=dict(metrics))
            self._record(outcome)
            return outcome
        finally:
            self._step_lock.release()
