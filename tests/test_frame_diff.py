from PIL import Image, ImageDraw

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.vision.frame_diff import changed_fraction, max_patch_changed_fraction


def test_frame_difference_is_local_and_zero_for_identical_frames():
    original = Image.new("RGB", (200, 200), "black")
    changed = original.copy()
    ImageDraw.Draw(changed).rectangle((10, 10, 70, 70), fill="white")
    first = CapturedFrame(original, Region(0, 0, 200, 200), "before")
    second = CapturedFrame(changed, Region(0, 0, 200, 200), "after")
    assert changed_fraction(first, first, Region(0, 0, 100, 100)) == 0.0
    assert changed_fraction(first, second, Region(0, 0, 100, 100)) > .2
    assert changed_fraction(first, second, Region(100, 100, 100, 100)) == 0.0
    assert first.pixels is first.pixels


def test_small_moving_card_is_visible_in_large_travel_corridor():
    before = Image.new("RGB", (400, 400), "black")
    after = before.copy()
    ImageDraw.Draw(after).rectangle((200, 180, 220, 200), fill="white")
    first = CapturedFrame(before, Region(0, 0, 400, 400), "before")
    second = CapturedFrame(after, first.region, "after")
    corridor = Region(0, 0, 400, 400)
    assert changed_fraction(first, second, corridor) < .01
    assert max_patch_changed_fraction(first, second, corridor) > .02
    assert max_patch_changed_fraction(second, second, corridor) == 0.0
