from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Thread

import pytest
from PIL import Image, ImageDraw

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.control import SafeMouse, VisualController
from sheep_solver.core.models import Observation, ObservedTile
from sheep_solver.planning import Action, BasicPlanner
from sheep_solver.vision import VisionProfile


def _tile(tile_id: str, kind: str, x: int, y: int) -> ObservedTile:
    return ObservedTile(tile_id, kind, x, y, 30, 30, 0, "main", True,
                        confidence=.99, clickable_confidence=.99)


def _obs(tiles: tuple[ObservedTile, ...], tray: tuple[str, ...] = ()) -> Observation:
    return Observation(tiles, (), tray, "playing", (100, 200, 300, 400),
                       (200, 200), datetime.now(timezone.utc).isoformat())


def test_basic_planner_triple_pair_and_safe_stop():
    planner = BasicPlanner()
    triple = planner.decide(_obs((_tile("a", "1", 10, 10),), ("1", "1")))
    assert triple.action and triple.action.reason == "直接完成三消"
    assert (triple.action.screen_x, triple.action.screen_y) == (125, 225)
    pair = planner.decide(_obs((_tile("a", "1", 10, 10),), ("1",)))
    assert pair.action and pair.action.reason == "与卡槽已有牌配对"
    opening = planner.decide(_obs((_tile("a", "1", 10, 10),
                                   _tile("b", "1", 60, 10), _tile("c", "1", 110, 10))))
    assert opening.action and opening.action.reason == "当前可见同类牌至少三张"
    assert planner.decide(_obs((_tile("a", "1", 10, 10),))).action is not None
    assert planner.decide(_obs((_tile("a", "1", 10, 10),),
                               ("2", "3", "4", "5", "6"))).action is None


class FakeMouseBackend:
    def __init__(self):
        self.clicks: list[tuple[int, int]] = []

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))


def test_safe_mouse_refuses_outside_board_and_stopped():
    backend = FakeMouseBackend()
    stop = Event()
    mouse = SafeMouse(backend, min_interval=0, stop_event=stop)
    game = Region(-500, 200, 200, 200)
    board = Region(0, 0, 200, 120)
    good = Action("a", "1", 25, 25, -475, 225, "test")
    mouse.click(good, game, board)
    assert backend.clicks == [(-475, 225)]
    with pytest.raises(ValueError, match="outside calibrated board"):
        mouse.click(Action("b", "1", 25, 160, -475, 360, "test"), game, board)
    with pytest.raises(ValueError, match="mismatch"):
        mouse.click(Action("b", "1", 25, 25, 999, 225, "test"), game, board)
    stop.set()
    with pytest.raises(RuntimeError, match="stop"):
        mouse.click(good, game, board)
    assert len(backend.clicks) == 1


def test_click_rate_wait_is_interruptible():
    backend = FakeMouseBackend()
    stop = Event()
    mouse = SafeMouse(backend, min_interval=.5, stop_event=stop)
    game = Region(0, 0, 200, 200)
    board = Region(0, 0, 200, 120)
    action = Action("a", "1", 25, 25, 25, 25, "test")
    mouse.click(action, game, board)
    errors = []
    worker = Thread(target=lambda: _click_collect(mouse, action, game, board, errors))
    worker.start()
    stop.set()
    worker.join(1)
    assert not worker.is_alive()
    assert errors and "stop" in errors[0]
    assert backend.clicks == [(25, 25)]


def _click_collect(mouse, action, game, board, errors):
    try:
        mouse.click(action, game, board)
    except RuntimeError as exc:
        errors.append(str(exc))


def test_one_step_reobserves_and_logs(tmp_path: Path):
    region = Region(100, 200, 200, 200)
    before_image = Image.new("RGB", (200, 200), "black")
    after_image = before_image.copy()
    ImageDraw.Draw(after_image).rectangle((10, 10, 45, 45), fill="white")
    before_frame = CapturedFrame(before_image, region, "before")
    after_frame = CapturedFrame(after_image, region, "after")
    frames = iter((before_frame, before_frame, after_frame, after_frame, after_frame))
    before = _obs((_tile("a", "1", 10, 10), _tile("b", "1", 60, 10),
                   _tile("c", "1", 110, 10)))
    after = _obs((_tile("b", "1", 60, 10), _tile("c", "1", 110, 10)), ("1",))

    class Detector:
        def detect(self, frame, profile):
            return before if frame.timestamp == "before" else after

        def confirm_target(self, frame, profile, tile):
            return frame.timestamp == "before"

        def detect_board_region(self, frame, profile, region):
            return ()

        def tray_card_count(self, frame, profile):
            return 1

    backend = FakeMouseBackend()
    log_path = tmp_path / "actions.jsonl"
    controller = VisualController(Detector(), VisionProfile(Region(0, 0, 200, 120),
                                                             Region(0, 130, 200, 60)), region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: next(frames), poll_interval=.001,
                                  log_path=log_path)
    outcome = controller.run_once()
    assert outcome.success
    assert backend.clicks == [(125, 225)]
    assert "直接" not in outcome.decision.reason
    assert log_path.exists() and '"success": true' in log_path.read_text(encoding="utf-8")


def test_target_change_before_click_stops_even_when_pixel_difference_is_small(tmp_path: Path):
    region = Region(100, 200, 200, 200)
    image = Image.new("RGB", (200, 200), "black")
    before_frame = CapturedFrame(image, region, "before")
    fresh_frame = CapturedFrame(image.copy(), region, "fresh")
    frames = iter((before_frame, fresh_frame))
    before = _obs((_tile("a", "1", 10, 10), _tile("b", "1", 60, 10),
                   _tile("c", "1", 110, 10)))
    changed = _obs((_tile("b", "1", 60, 10), _tile("c", "1", 110, 10)))

    class Detector:
        def detect(self, frame, profile):
            return before if frame.timestamp == "before" else changed

        def confirm_target(self, frame, profile, tile):
            return frame.timestamp == "before"

    backend = FakeMouseBackend()
    controller = VisualController(Detector(), VisionProfile(Region(0, 0, 200, 120),
                                                             Region(0, 130, 200, 60)), region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: next(frames), animation_delay=0,
                                  log_path=tmp_path / "actions.jsonl")
    outcome = controller.run_once()
    assert not outcome.success
    assert "点击前目标牌" in outcome.reason
    assert backend.clicks == []


def test_unconfirmed_click_keeps_tray_and_requires_resync(tmp_path: Path):
    region = Region(100, 200, 200, 200)
    frame = CapturedFrame(Image.new("RGB", (200, 200), "black"), region, "same")
    before = _obs((_tile("a", "1", 10, 10),), ("1", "1"))

    class Detector:
        def detect(self, _frame, _profile):
            return before

        def confirm_target(self, _frame, _profile, _tile):
            return True

    backend = FakeMouseBackend()
    controller = VisualController(Detector(), VisionProfile(Region(0, 0, 200, 120),
                                                             Region(0, 130, 200, 60)), region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: frame, poll_interval=.005,
                                  feedback_timeout=.025,
                                  log_path=tmp_path / "actions.jsonl")
    outcome = controller.run_once()
    assert backend.clicks == [(125, 225)]
    assert not outcome.success
    assert outcome.after and outcome.after.tray == ("1", "1")
    assert controller.memory and controller.memory.tray == ("1", "1")
    assert controller.memory.pending is None


def test_controller_rejects_wrong_stable_manual_id(tmp_path: Path):
    region = Region(100, 200, 200, 200)
    frame = CapturedFrame(Image.new("RGB", (200, 200)), region, "same")
    before = _obs((_tile("a", "1", 10, 10),), ("1", "1"))

    class Detector:
        def detect(self, _frame, _profile):
            return before

    backend = FakeMouseBackend()
    controller = VisualController(Detector(), VisionProfile(Region(0, 0, 200, 120),
                                                             Region(0, 130, 200, 60)), region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: frame,
                                  log_path=tmp_path / "actions.jsonl")
    outcome = controller.run_once("tile_9999", ("1", 25, 25))
    assert not outcome.success
    assert "ID" in outcome.reason
    assert backend.clicks == []


def test_controller_never_runs_two_click_transactions_at_once(tmp_path: Path):
    region = Region(100, 200, 200, 200)
    frame = CapturedFrame(Image.new("RGB", (200, 200)), region, "same")
    before = _obs((_tile("a", "1", 10, 10),), ("1", "1"))
    clicked = Event()
    release = Event()

    class Detector:
        def detect(self, _frame, _profile):
            return before

        def confirm_target(self, _frame, _profile, _tile):
            return True

    class BlockingMouse:
        def click(self, *_args):
            clicked.set()
            release.wait(1)

    controller = VisualController(Detector(), VisionProfile(Region(0, 0, 200, 120),
                                                             Region(0, 130, 200, 60)), region,
                                  mouse=BlockingMouse(), capture=lambda _region: frame,
                                  poll_interval=.005, feedback_timeout=.025,
                                  log_path=tmp_path / "actions.jsonl")
    worker = Thread(target=controller.run_once)
    worker.start()
    try:
        assert clicked.wait(1)
        outcome = controller.run_once()
        assert not outcome.success
        assert "未完成" in outcome.reason
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive()


def test_stop_before_step_prevents_capture_and_click(tmp_path: Path):
    stop = Event()
    stop.set()
    captures = []
    backend = FakeMouseBackend()
    controller = VisualController(None, VisionProfile(Region(0, 0, 100, 100),
                                                      Region(0, 110, 100, 50)),
                                  Region(0, 0, 100, 200),
                                  mouse=SafeMouse(backend, min_interval=0, stop_event=stop),
                                  stop_event=stop,
                                  capture=lambda region: captures.append(region),
                                  log_path=tmp_path / "actions.jsonl")
    outcome = controller.run_once()
    assert not outcome.success and "停止" in outcome.reason
    assert not captures and not backend.clicks
