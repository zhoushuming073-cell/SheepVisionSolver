from sheep_solver.capture.screen import Region
from sheep_solver.gui.app import nonoverlap_position


def test_control_window_moves_right_of_calibrated_game():
    screen = Region(0, 0, 2560, 1600)
    game = Region(282, 81, 886, 1276)
    window = Region(305, 294, 1170, 900)
    position = nonoverlap_position(game, window, screen)
    assert position == (game.right + 24, 294)
    assert position[0] + window.width <= screen.right


def test_control_window_stays_put_when_clear_and_blocks_when_no_space():
    screen = Region(0, 0, 1920, 1080)
    game = Region(0, 0, 1000, 1080)
    clear = Region(1050, 100, 600, 600)
    assert nonoverlap_position(game, clear, screen) == (1050, 100)
    assert nonoverlap_position(Region(0, 0, 1920, 1080),
                               Region(300, 100, 600, 600), screen) is None
