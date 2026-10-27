import pytest

from sheep_solver.core import Pile, Rules, Tile
from sheep_solver.simulator import RuleEngine


def tile(tile_id: str, type_id: str, *, blockers=(), region="main", layer=0):
    return Tile(tile_id, type_id, 0, 0, 1, 1, layer, region, blockers)


def test_blockers_and_three_match_win():
    engine = RuleEngine((
        tile("top1", "A", layer=1),
        tile("top2", "A", layer=1),
        tile("top3", "A", layer=1),
        tile("bottom1", "B", blockers=("top1",)),
        tile("bottom2", "B", blockers=("top2",)),
        tile("bottom3", "B", blockers=("top3",)),
    ))
    state = engine.initial_state()
    assert engine.legal_moves(state) == ("top1", "top2", "top3")
    with pytest.raises(ValueError, match="Illegal move"):
        engine.step(state, "bottom1")
    for tile_id in ("top1", "top2", "top3"):
        state = engine.step(state, tile_id)
    assert state.tray == ()
    assert state.history[-1].eliminated_type == "A"
    assert engine.legal_moves(state) == ("bottom1", "bottom2", "bottom3")
    for tile_id in ("bottom1", "bottom2", "bottom3"):
        state = engine.step(state, tile_id)
    assert state.status == "won"
    assert state.remaining_count == 0
    assert state.tray == ()
    assert len(state.history) == 6
    assert engine.legal_moves(state) == ()


def test_tray_capacity_is_checked_after_elimination():
    engine = RuleEngine(tuple(tile(str(i), str(i)) for i in range(3)), rules=Rules(3, 2))
    state = engine.step(engine.initial_state(), "0")
    state = engine.step(state, "1")
    assert state.status == "lost"
    assert state.failure_reason == "tray_full"
    with pytest.raises(ValueError, match="Illegal move"):
        engine.step(state, "2")


def test_match_on_capacity_boundary_does_not_lose():
    engine = RuleEngine(tuple(tile(str(i), "A") for i in range(3)), rules=Rules(3, 3))
    state = engine.initial_state()
    for tile_id in ("0", "1", "2"):
        state = engine.step(state, tile_id)
    assert state.status == "won"


def test_special_pile_only_exposes_next_tile_and_restart():
    tiles = (tile("b1", "secret1", region="blind"),
             tile("b2", "secret2", region="blind"),
             tile("m", "visible"))
    pile = Pile("blind", "blind", ("b1", "b2"))
    engine = RuleEngine(tiles, (pile,))
    initial = engine.initial_state()
    assert engine.legal_moves(initial) == ("m", "b1")
    obs = engine.observe(initial)
    assert obs.piles[0].clickable is True
    assert obs.piles[0].x == 0
    assert obs.piles[0].top_type_id is None
    assert obs.piles[0].visible_count is None
    state = engine.step(initial, "b1")
    assert engine.legal_moves(state) == ("m", "b2")
    assert engine.initial_state() == initial


def test_invalid_blocker_graph_rejected():
    with pytest.raises(ValueError, match="cycle"):
        RuleEngine((tile("a", "A", blockers=("b",)),
                    tile("b", "B", blockers=("a",))))
    with pytest.raises(ValueError, match="Invalid blocker"):
        RuleEngine((tile("a", "A", blockers=("missing",)),))


def test_no_moves_with_nonempty_tray_is_loss():
    engine = RuleEngine((tile("only", "A"),))
    state = engine.step(engine.initial_state(), "only")
    assert state.status == "lost"
    assert state.failure_reason == "no_moves"
    assert state.history[-1].status_after == "lost"

