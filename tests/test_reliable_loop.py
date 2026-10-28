"""Feedback and recovery contracts use generated frames and a simulated mouse."""
from dataclasses import replace
from itertools import chain, repeat

from scripts.benchmark_synthetic import _fixture
from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.control.loop import StepCode, VisualController
from sheep_solver.control.mouse import SafeMouse
from sheep_solver.vision.detector import TemplateDetector


class FakeMouse:
    def __init__(self):
        self.clicks = 0

    def click(self, _x, _y):
        self.clicks += 1


def test_false_cheap_tray_count_is_resolved_by_exact_visual_read(tmp_path):
    before, after, profile = _fixture(tmp_path)
    base = TemplateDetector(tmp_path)

    class Detector:
        def detect(self, *args):
            return base.detect(*args)

        def confirm_target(self, *args):
            return base.confirm_target(*args)

        def tray_card_count(self, *_args):
            return 0  # The cheap occupancy method is deliberately wrong.

        def read_tray(self, frame, profile):
            return base.read_tray(frame, profile)

        def detect_board_region(self, *args, **kwargs):
            return base.detect_board_region(*args, **kwargs)

    backend = FakeMouse()
    frames = chain((before,), repeat(after))
    controller = VisualController(Detector(), profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: next(frames),
                                  poll_interval=.001, settle_seconds=.01,
                                  feedback_timeout=.4,
                                  log_path=tmp_path / "actions.jsonl")
    controller.initialize_from_observation(before, base.detect(before, profile))
    outcome = controller.run_once()
    assert outcome.code == StepCode.CONFIRMED
    assert backend.clicks == 1
    assert outcome.metrics["tray_exact_reads"] == 1


def test_window_change_stops_before_mouse_input(tmp_path):
    before, _, profile = _fixture(tmp_path)
    backend = FakeMouse()
    controller = VisualController(TemplateDetector(tmp_path), profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: before,
                                  window_region=lambda: Region(10, 0, before.region.width + 1,
                                                               before.region.height),
                                  log_path=tmp_path / "actions.jsonl")
    outcome = controller.run_once()
    assert outcome.code == StepCode.WINDOW_CHANGED
    assert backend.clicks == 0


def test_window_translation_updates_capture_origin_without_recalibration(tmp_path):
    before, _, profile = _fixture(tmp_path)
    shifted = Region(before.region.left + 32, before.region.top + 19,
                     before.region.width, before.region.height)
    captured = []

    def capture(region):
        captured.append(region)
        return CapturedFrame(before.image, region, before.timestamp)

    backend = FakeMouse()
    controller = VisualController(TemplateDetector(tmp_path), profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=capture, window_region=lambda: shifted,
                                  feedback_timeout=.03, poll_interval=.005,
                                  log_path=tmp_path / "actions.jsonl")
    controller.initialize_from_observation(before, TemplateDetector(tmp_path).detect(before, profile))
    outcome = controller.run_once()
    assert outcome.code != StepCode.WINDOW_CHANGED
    assert captured and all(region == shifted for region in captured)
    assert controller.game_region == shifted


def test_uncertain_sent_click_cannot_be_replayed(tmp_path):
    before, _, profile = _fixture(tmp_path)
    backend = FakeMouse()
    controller = VisualController(TemplateDetector(tmp_path), profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: before,
                                  log_path=tmp_path / "actions.jsonl")
    controller._uncertain_expected_tray = ("1",)
    assert controller.run_once().code == StepCode.UNRECOVERABLE
    assert backend.clicks == 0


def test_recovery_confirms_uncertain_click_without_replaying_it(tmp_path):
    before, after, profile = _fixture(tmp_path)
    backend = FakeMouse()
    detector = TemplateDetector(tmp_path)
    controller = VisualController(detector, profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: after,
                                  log_path=tmp_path / "actions.jsonl")
    controller.initialize_from_observation(before, detector.detect(before, profile))
    controller._uncertain_expected_tray = ("1",)
    outcome = controller.recover(max_wait=.3)
    assert outcome.code == StepCode.RECOVERED
    assert controller._uncertain_expected_tray is None
    assert controller.memory is not None and controller.memory.tray == ("1",)
    assert backend.clicks == 0


def test_recovery_keeps_uncertain_click_blocked_on_wrong_tray(tmp_path):
    before, _, profile = _fixture(tmp_path)
    backend = FakeMouse()
    detector = TemplateDetector(tmp_path)
    controller = VisualController(detector, profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: before,
                                  log_path=tmp_path / "actions.jsonl")
    controller._uncertain_expected_tray = ("1",)
    assert controller.recover(max_wait=.3).code == StepCode.UNRECOVERABLE
    assert controller.run_once().code == StepCode.UNRECOVERABLE
    assert backend.clicks == 0


def test_preclick_recovery_cannot_erase_confirmed_tray_without_click(tmp_path):
    before, after, profile = _fixture(tmp_path)
    detector = TemplateDetector(tmp_path)
    controller = VisualController(detector, profile, before.region,
                                  mouse=SafeMouse(FakeMouse(), min_interval=0),
                                  capture=lambda _region: before,
                                  log_path=tmp_path / "actions.jsonl")
    controller.initialize_from_observation(after, detector.detect(after, profile))
    assert controller._last_confirmed_tray == ("1",)
    outcome = controller.recover(max_wait=.3)
    assert outcome.code == StepCode.UNRECOVERABLE
    assert controller._last_confirmed_tray == ("1",)


def test_stationary_in_flight_frame_is_not_committed_as_finished_click(tmp_path):
    before, after, profile = _fixture(tmp_path)
    transient_image = before.image.copy()
    transient_image.paste((80, 125, 90), (40, 50, 90, 100))
    transient = CapturedFrame(transient_image, before.region, "in-flight", "synthetic")
    detector = TemplateDetector(tmp_path)
    frames = chain((before,), (transient for _ in range(30)), repeat(after))
    backend = FakeMouse()
    controller = VisualController(detector, profile, before.region,
                                  mouse=SafeMouse(backend, min_interval=0),
                                  capture=lambda _region: next(frames),
                                  poll_interval=.001, settle_seconds=.01,
                                  feedback_timeout=.8,
                                  log_path=tmp_path / "actions.jsonl")
    controller.initialize_from_observation(before, detector.detect(before, profile))
    outcome = controller.run_once()
    assert outcome.code == StepCode.CONFIRMED
    assert outcome.metrics["captures"] > 30
    assert backend.clicks == 1


def test_preclick_confirmation_prefers_observed_tile_scale(tmp_path):
    before, _, profile = _fixture(tmp_path)
    profile = replace(profile, scales=(.8, .9, 1.0))
    detector = TemplateDetector(tmp_path)
    tile = next(tile for tile in detector.detect(before, profile).tiles if tile.clickable)
    calls = []
    original_match = detector._match

    def observed_match(rgb, roi, current_profile, scales=None, type_ids=None):
        calls.append(scales)
        return original_match(rgb, roi, current_profile, scales, type_ids)

    detector._match = observed_match
    assert detector.confirm_target(before, profile, tile)
    assert calls[0] == (1.0,)
