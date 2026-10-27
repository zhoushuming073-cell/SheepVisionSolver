from pathlib import Path

import pytest
from PIL import Image

from sheep_solver.capture.screen import Region, load_frame, save_frame


def test_region_mapping_with_negative_monitor_origin():
    region = Region.from_drag(-800, 100, -200, 500)
    assert region.bbox == (-800, 100, -200, 500)
    assert region.to_screen(20, 30) == (-780, 130)
    assert region.contains(-780, 130)
    assert not region.contains(-200, 130)
    with pytest.raises(ValueError):
        region.to_screen(600, 0)


def test_offline_frame_round_trip(tmp_path: Path):
    source = tmp_path / "screenshot.png"
    Image.new("RGB", (160, 90), "green").save(source)
    frame = load_frame(source, screen_origin=(-1600, 250))
    assert frame.to_screen(50, 40) == (-1550, 290)
    target = tmp_path / "saved" / "copy.png"
    save_frame(frame, target)
    assert Image.open(target).size == (160, 90)

