"""Public tests use generated sprites; no APK art or user screenshots."""

from scripts.benchmark_synthetic import _fixture
from sheep_solver.control.loop import VisualController
from sheep_solver.vision.detector import TemplateDetector


def test_generated_templates_are_detected_and_locally_updated(tmp_path):
    before, after, profile = _fixture(tmp_path)
    detector = TemplateDetector(tmp_path)
    initial = detector.detect(before, profile)
    assert len(initial.tiles) == 3
    assert all(tile.type_id == "1" and tile.clickable for tile in initial.tiles)
    assert initial.tray == ()
    assert initial.tray_confidence == 1.0
    clicked = min(initial.tiles, key=lambda tile: tile.x)
    assert detector.confirm_target(before, profile, clicked)
    assert not detector.confirm_target(after, profile, clicked)
    region = VisualController._affected_region(clicked, profile.board)
    local = detector.detect_board_region(after, profile, region)
    assert all(tile.type_id == "1" for tile in local)
    assert detector.tray_card_count(after, profile) == 1
