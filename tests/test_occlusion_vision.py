"""Asset-free occlusion scenarios; no original game art or private screenshots."""

import json

import numpy as np
from PIL import Image

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.core.memory import VisibleMemory
from sheep_solver.vision.detector import TemplateDetector, VisionProfile
from sheep_solver.vision.occlusion import apply_black_mask, visible_mask


def _sprites(folder):
    rng = np.random.default_rng(27)
    records = []
    icons = {}
    for kind in (1, 2, 3):
        pixels = np.zeros((36, 36, 4), np.uint8)
        pixels[:, :, :3] = rng.integers(35, 230, (36, 36, 3), dtype=np.uint8)
        pixels[:, :, 3] = 255
        icons[kind] = Image.fromarray(pixels, "RGBA")
        icons[kind].save(folder / f"block_{kind}.png")
        records.append({"file": f"block_{kind}.png", "type_id": kind,
                        "original_size": [60, 60], "offset": [0, 0]})
    background = Image.new("RGBA", (60, 60), (222, 220, 165, 255))
    background.save(folder / "block_bg.png")
    Image.new("RGBA", (60, 60), (0, 0, 0, 102)).save(folder / "blackMask.png")
    records.append({"file": "blackMask.png", "type_id": None,
                    "original_size": [60, 60], "offset": [0, 0]})
    (folder / "manifest.json").write_text(json.dumps({"records": records}), encoding="utf-8")
    return background, icons


def _frame(folder, *, scale=1, blocked=True, dark=True, kind=1, top_kind=2,
           offset=(30, 30)):
    background, icons = _sprites(folder)
    card_size = 60 * scale
    icon_size = 36 * scale
    inset = 12 * scale
    bottom = (50 * scale, 40 * scale)
    top = (bottom[0] + offset[0] * scale, bottom[1] + offset[1] * scale)
    canvas = Image.new("RGBA", (270 * scale, 210 * scale), (83, 132, 88, 255))

    def draw_card(where, card_kind, shade):
        x, y = where
        card = background.resize((card_size, card_size))
        card.alpha_composite(icons[card_kind].resize((icon_size, icon_size)), (inset, inset))
        if shade:
            card.alpha_composite(Image.new("RGBA", card.size, (0, 0, 0, 102)))
        canvas.alpha_composite(card, (x, y))

    draw_card(bottom, kind, dark)
    if blocked:
        draw_card(top, top_kind, False)
    image = canvas.convert("RGB")
    frame = CapturedFrame(image, Region(0, 0, *image.size), "synthetic", "synthetic")
    profile = VisionProfile(Region(0, 0, 260 * scale, 160 * scale),
                            Region(0, 175 * scale, 260 * scale, 30 * scale),
                            scales=(float(scale),), tray_scales=(float(scale),),
                            icon_threshold=.88, click_threshold=.95)
    return frame, profile, bottom, top


def test_mask_union_and_verified_black_compositing():
    mask = visible_mask(0, 0, 10, 10, ((0, 0, 5, 10), (3, 0, 5, 5)))
    assert mask.sum() == 35
    color = np.full((1, 1, 3), 200, np.uint8)
    assert apply_black_mask(color, np.array([[102]], np.uint8)).tolist() == [[[120, 120, 120]]]


def test_full_dark_card_keeps_type_but_cannot_be_clicked(tmp_path):
    frame, profile, bottom, _ = _frame(tmp_path, blocked=False)
    observed = TemplateDetector(tmp_path).detect(frame, profile)
    card = next(tile for tile in observed.tiles if abs(tile.x - bottom[0]) <= 2)
    assert card.type_id == "1"
    assert card.shade_state == "dark"
    assert card.visibility == "full"  # Visually complete, but dark and not actionable.
    assert not card.clickable
    assert card.clickable_confidence == 0
    memory = VisibleMemory(observed)
    assert len(memory.full) == 1 and not memory.full[0].clickable


def test_half_offset_dark_card_is_recognized_behind_bright_card(tmp_path):
    frame, profile, bottom, top = _frame(tmp_path)
    observed = TemplateDetector(tmp_path).detect(frame, profile)
    lower = [tile for tile in observed.tiles if abs(tile.x - bottom[0]) <= 2 and abs(tile.y - bottom[1]) <= 2]
    assert len(lower) == 1
    assert lower[0].type_id == "1"
    assert lower[0].shade_state == "dark"
    assert lower[0].visibility == "partial"
    assert lower[0].occlusion_fraction and lower[0].occlusion_fraction > .2
    assert not lower[0].clickable
    assert any(tile.type_id == "2" and tile.clickable and abs(tile.x - top[0]) <= 2
               for tile in observed.tiles)


def test_partial_card_transition_can_be_reobserved_locally(tmp_path):
    before, profile, bottom, top = _frame(tmp_path)
    detector = TemplateDetector(tmp_path)
    previous = detector.detect(before, profile)
    assert any(tile.visibility == "partial" and tile.type_id == "1" for tile in previous.tiles)
    after, _, _, _ = _frame(tmp_path, blocked=False, dark=False)
    local = detector.detect_board_region(after, profile,
                                         Region(20, 20, 150, 130), fast=False)
    assert any(tile.type_id == "1" and tile.clickable and
               abs(tile.x - bottom[0]) <= 2 for tile in local)


def test_half_face_occlusion_recovers_type_from_visible_mask(tmp_path):
    frame, profile, bottom, _ = _frame(tmp_path, offset=(30, 0))
    detector = TemplateDetector(tmp_path)
    raw = detector._match(frame.pixels, profile.board, profile)
    assert not any(abs(tile.x - bottom[0]) <= 2 and tile.type_id == "1" for tile in raw)
    detected = detector.detect(frame, profile)
    lower = [tile for tile in detected.tiles if abs(tile.x - bottom[0]) <= 2]
    assert len(lower) == 1
    assert lower[0].type_id == "1" and lower[0].visibility == "partial"
    assert lower[0].occlusion_source == "observed_overlap"
    assert not lower[0].clickable


def test_same_type_overlap_does_not_make_lower_card_clickable(tmp_path):
    frame, profile, bottom, _ = _frame(tmp_path, offset=(30, 0), top_kind=1)
    detected = TemplateDetector(tmp_path).detect(frame, profile)
    lower = [tile for tile in detected.tiles if abs(tile.x - bottom[0]) <= 2]
    assert len(lower) == 1
    assert lower[0].type_id == "1" and not lower[0].clickable


def test_scaled_partial_card_and_empty_proposal_are_conservative(tmp_path):
    frame, profile, bottom, _ = _frame(tmp_path, scale=2, offset=(30, 0))
    detected = TemplateDetector(tmp_path).detect(frame, profile)
    assert any(tile.type_id == "1" and tile.visibility == "partial" and
               not tile.clickable and abs(tile.x - bottom[0]) <= 3 for tile in detected.tiles)

    # A hidden card has no screen evidence and must never be invented.
    hidden_frame, hidden_profile, _, _ = _frame(tmp_path, offset=(0, 0))
    hidden = TemplateDetector(tmp_path).detect(hidden_frame, hidden_profile)
    assert {tile.type_id for tile in hidden.tiles} == {"2"}


def test_identical_templates_stay_unknown_and_incomplete_border_is_not_clickable(tmp_path):
    frame, profile, bottom, _ = _frame(tmp_path, blocked=False, dark=False)
    (tmp_path / "block_2.png").write_bytes((tmp_path / "block_1.png").read_bytes())
    ambiguous = TemplateDetector(tmp_path).detect(frame, profile)
    assert any(tile.type_id is None and not tile.clickable for tile in ambiguous.tiles)

    frame, profile, bottom, _ = _frame(tmp_path, blocked=False, dark=False)
    damaged = frame.image.copy()
    x, y = bottom
    for box in ((x, y, x + 60, y + 10), (x, y + 50, x + 60, y + 60),
                (x, y, x + 10, y + 60), (x + 50, y, x + 60, y + 60)):
        damaged.paste((0, 0, 0), box)
    damaged_frame = CapturedFrame(damaged, frame.region, "synthetic", "synthetic")
    observed = TemplateDetector(tmp_path).detect(damaged_frame, profile)
    assert all(not tile.clickable for tile in observed.tiles)
