from dataclasses import replace
import gc
import tracemalloc

import pytest

from sheep_solver.capture.screen import Region
from sheep_solver.core.memory import StateConflict, VisibleMemory, tray_after_click
from sheep_solver.core.models import Observation, ObservedTile


def tile(kind: str | None, x: int, y: int, *, full: bool = True,
         clickable: bool = True) -> ObservedTile:
    return ObservedTile("detector-temp", kind, x, y, 80, 90, 0, "main", clickable,
                        confidence=.98, clickable_confidence=.95,
                        visibility="full" if full else "partial")


def observation(*tiles: ObservedTile, tray: tuple[str, ...] = ()) -> Observation:
    return Observation(tiles, (), tray, "playing", (100, 200, 600, 800),
                       (500, 600), "test")


def test_repeated_observation_preserves_identity_and_count():
    memory = VisibleMemory(observation(tile("1", 20, 30)))
    original_id = memory.full[0].id
    memory.merge((replace(tile("1", 21, 30), confidence=.97),), Region(0, 0, 200, 200))
    assert len(memory.tiles) == 1
    assert memory.full[0].id == original_id
    assert memory.type_counts == {"1": 1}


def test_unconfirmed_partial_detection_expires_after_two_local_misses():
    memory = VisibleMemory(observation(tile("2", 20, 30, full=False, clickable=False)))
    region = Region(0, 0, 200, 200)
    memory.merge((), region)
    assert len(memory.partial) == 1
    memory.merge((), region)
    assert memory.partial == ()
    assert memory.type_counts == {}


def test_partial_unknown_becomes_full_without_new_identity():
    memory = VisibleMemory(observation(tile(None, 20, 30, full=False, clickable=False)))
    original_id = memory.partial[0].id
    memory.merge((tile("2", 20, 30),), Region(0, 0, 200, 200))
    assert not memory.partial
    assert memory.full[0].id == original_id
    assert memory.full[0].type_id == "2"


def test_newly_exposed_hidden_tile_is_registered():
    memory = VisibleMemory(observation(tile("1", 20, 30)))
    memory.merge((tile("1", 20, 30), tile("2", 150, 30)), Region(0, 0, 250, 200))
    assert len(memory.full) == 2
    assert memory.type_counts == {"1": 1, "2": 1}
    assert len({tile.id for tile in memory.full}) == 2


def test_confirmed_click_removes_tile_and_third_card_clears_tray():
    memory = VisibleMemory(observation(tile("8", 20, 30), tray=("8", "8")))
    clicked = memory.full[0]
    pending = memory.begin_click(clicked.id, "8")
    assert pending.expected_tray == ()
    assert memory.tray == ("8", "8")
    memory.commit_click((), Region(0, 0, 120, 150), confirmed=True)
    assert not memory.tiles
    assert memory.tray == ()
    assert memory.pending is None


def test_failed_click_does_not_commit_prediction():
    memory = VisibleMemory(observation(tile("3", 20, 30), tray=("1",)))
    clicked = memory.full[0]
    memory.begin_click(clicked.id, "3")
    with pytest.raises(StateConflict, match="not confirmed"):
        memory.commit_click((), Region(0, 0, 120, 150), confirmed=False)
    assert memory.tray == ("1",)
    assert clicked.id in memory.tiles
    assert memory.pending is None


def test_wrong_identity_and_type_conflict_stop_without_state_drift():
    memory = VisibleMemory(observation(tile("1", 20, 30), tile("2", 150, 30)))
    original = dict(memory.tiles)
    with pytest.raises(StateConflict, match="missing, changed"):
        memory.begin_click("not-a-tile", "1")
    clicked = next(tile for tile in memory.full if tile.type_id == "1")
    memory.begin_click(clicked.id, "1")
    with pytest.raises(StateConflict, match="changed type"):
        memory.commit_click((tile("3", 150, 30),), Region(0, 0, 250, 150), confirmed=True)
    assert memory.tiles == original
    assert memory.tray == ()
    assert memory.pending is None


def test_tray_insertion_keeps_equal_types_adjacent():
    assert tray_after_click(("1", "2", "1"), "2") == ("1", "2", "2", "1")
    assert tray_after_click(("2", "1", "1"), "1") == ("2",)


def test_repeated_incremental_observations_have_bounded_memory():
    memory = VisibleMemory(observation(tile("1", 20, 30)))
    detected = (tile("1", 20, 30),)
    region = Region(0, 0, 200, 200)
    tracemalloc.start()
    try:
        for _ in range(100):
            memory.merge(detected, region)
        gc.collect()
        initial, _ = tracemalloc.get_traced_memory()
        for _ in range(2000):
            memory.merge(detected, region)
        gc.collect()
        final, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(memory.tiles) == 1
    assert final - initial < 256_000
