from PIL import Image, ImageDraw

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.vision.frame_diff import changed_fraction


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
