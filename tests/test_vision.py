import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.vision import TemplateDetector, VisionProfile, draw_debug


ROOT = Path(__file__).resolve().parents[1]
TILES = ROOT / "assets" / "tiles" / "classic"
pytestmark = pytest.mark.skipif(not (TILES / "manifest.json").exists(),
                                reason="Classic APK sprites are local-only; extract them before vision tests")


def _synthetic_frame() -> CapturedFrame:
    image = Image.new("RGBA", (460, 520), (80, 125, 90, 255))
    manifest = json.loads((TILES / "manifest.json").read_text(encoding="utf-8"))
    indexed = {entry["file"]: entry for entry in manifest["records"]}
    background = Image.open(TILES / "block_bg.png").convert("RGBA")
    for name, left, top in (("block_1.png", 45, 55), ("block_2.png", 250, 55),
                            ("block_1.png", 45, 355)):
        icon = Image.open(TILES / name).convert("RGBA")
        entry = indexed[name]
        original = entry["original_size"]
        offset_x, offset_y = entry["offset"]
        image.alpha_composite(background, (left, top))
        inset_x = round((original[0] - icon.width) / 2 + offset_x)
        inset_y = round((original[1] - icon.height) / 2 - offset_y)
        image.alpha_composite(icon, (left + inset_x, top + inset_y))
    return CapturedFrame(image.convert("RGB"), Region(-500, 200, 460, 520),
                         datetime.now(timezone.utc).isoformat(), "synthetic")


def test_apk_templates_detect_and_render(tmp_path: Path):
    detector = TemplateDetector(TILES)
    frame = _synthetic_frame()
    profile = VisionProfile(Region(0, 0, 460, 310), Region(0, 320, 460, 200),
                            scales=(1.0,), icon_threshold=.94, click_threshold=.97,
                            ambiguity_margin=.005)
    observation = detector.detect(frame, profile)
    assert observation.game_region == (-500, 200, -40, 720)
    assert {tile.type_id for tile in observation.tiles if tile.clickable} >= {"1", "2"}
    assert observation.tray == ("1",)
    first = next(tile for tile in observation.tiles if tile.type_id == "1" and tile.clickable)
    assert frame.to_screen(round(first.center[0]), round(first.center[1])) == (-395, 322)
    output = tmp_path / "debug.png"
    draw_debug(frame.image, observation).save(output)
    assert output.exists()


def test_local_recognition_and_target_confirmation():
    detector = TemplateDetector(TILES)
    frame = _synthetic_frame()
    profile = VisionProfile(Region(0, 0, 460, 310), Region(0, 320, 460, 200),
                            scales=(1.0,), icon_threshold=.94, click_threshold=.97,
                            ambiguity_margin=.005)
    observed = detector.detect_board_region(frame, profile, Region(0, 0, 220, 250))
    first = next(tile for tile in observed if tile.type_id == "1" and tile.clickable)
    assert detector.confirm_target(frame, profile, first)
    changed = frame.image.copy()
    changed.paste((80, 125, 90), (45, 55, 165, 190))
    after = CapturedFrame(changed, frame.region, "after", "synthetic")
    assert not detector.confirm_target(after, profile, first)


def test_unclassified_tray_card_blocks_automatic_decision():
    frame = _synthetic_frame()
    image = frame.image.convert("RGBA")
    blank = Image.open(TILES / "block_bg.png").convert("RGBA")
    image.alpha_composite(blank, (250, 355))
    frame = CapturedFrame(image.convert("RGB"), frame.region, frame.timestamp, "synthetic")
    profile = VisionProfile(Region(0, 0, 460, 310), Region(0, 320, 460, 200),
                            scales=(1.0,), icon_threshold=.94, click_threshold=.97,
                            ambiguity_margin=.005)
    observation = TemplateDetector(TILES).detect(frame, profile)
    assert observation.tray == ("1",)
    assert observation.tray_confidence == 0.0


def test_tray_cards_can_use_larger_scale_than_board_cards():
    image = Image.new("RGBA", (460, 520), (80, 125, 90, 255))
    manifest = json.loads((TILES / "manifest.json").read_text(encoding="utf-8"))
    entry = next(record for record in manifest["records"] if record["file"] == "block_2.png")
    scale = 1.2
    background = Image.open(TILES / "block_bg.png").convert("RGBA")
    icon = Image.open(TILES / "block_2.png").convert("RGBA")
    background = background.resize((round(background.width * scale), round(background.height * scale)),
                                   Image.Resampling.BILINEAR)
    icon = icon.resize((round(icon.width * scale), round(icon.height * scale)),
                       Image.Resampling.BILINEAR)
    left, top = 250, 355
    image.alpha_composite(background, (left, top))
    original = entry["original_size"]
    offset_x, offset_y = entry["offset"]
    inset_x = round(((original[0] - Image.open(TILES / "block_2.png").width) / 2 + offset_x) * scale)
    inset_y = round(((original[1] - Image.open(TILES / "block_2.png").height) / 2 - offset_y) * scale)
    image.alpha_composite(icon, (left + inset_x, top + inset_y))
    frame = CapturedFrame(image.convert("RGB"), Region(0, 0, 460, 520), "synthetic", "synthetic")
    profile = VisionProfile(Region(0, 0, 460, 310), Region(0, 320, 460, 200),
                            scales=(1.0,), tray_scales=(1.2,), icon_threshold=.83,
                            click_threshold=.88)
    observation = TemplateDetector(TILES).detect(frame, profile)
    assert observation.tray == ("2",)
    assert observation.tray_confidence == 1.0

