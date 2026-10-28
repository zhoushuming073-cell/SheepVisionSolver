from PIL import Image, ImageDraw

from sheep_solver.capture.screen import Region
from sheep_solver.vision.detector import TemplateDetector, VisionProfile, _Candidate
from sheep_solver.vision.calibration import locate_layout, tray_scales_from_slots


def test_classic_green_field_and_wood_tray_are_found_inside_window():
    image = Image.new("RGB", (1000, 1500), (30, 30, 30))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 50, 940, 1450), fill=(190, 255, 150))
    draw.rectangle((110, 1120, 870, 1270), fill=(150, 100, 45))
    layout = locate_layout(image)
    assert layout is not None
    assert layout.content.left <= 50 and layout.content.top <= 60
    assert layout.board.bottom < layout.tray.top
    assert layout.tray.top < 1150 < layout.tray.bottom
    board, tray, left_pile, right_pile, tools = layout.regions_in_image()
    assert board.left == layout.content.left + layout.board.left
    assert tray.top == layout.content.top + layout.tray.top
    assert left_pile.right < right_pile.left
    assert tools.top > board.bottom


def test_unrelated_screen_is_not_calibrated():
    assert locate_layout(Image.new("RGB", (1000, 1500), "gray")) is None


def test_tray_template_scale_comes_from_seven_slot_width():
    scales = tray_scales_from_slots(Region(10, 20, 630, 160), 120)
    assert scales == (.72, .75, .78)


def test_pile_tiles_are_not_promoted_into_main_board_candidates():
    profile = VisionProfile(Region(0, 0, 600, 800), Region(0, 900, 600, 100),
                            left_pile=Region(30, 650, 160, 150),
                            right_pile=Region(410, 650, 160, 150))
    candidates = [_Candidate("1", 70, 680, 70, 80, .99),
                  _Candidate("2", 260, 680, 70, 80, .99),
                  _Candidate("3", 460, 680, 70, 80, .99)]
    retained = TemplateDetector._exclude_piles(candidates, profile)
    assert [tile.type_id for tile in retained] == ["2"]
