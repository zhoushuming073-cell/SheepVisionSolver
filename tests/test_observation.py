from dataclasses import asdict

from sheep_solver.core import Pile, Tile
from sheep_solver.simulator import RuleEngine


def test_hidden_face_never_appears_in_observation():
    tiles = (
        Tile("cover", "front", 0, 0, 1, 1, 1),
        Tile("under", "SECRET_UNDER", 0, 0, 1, 1, 0,
             blockers=("cover",), geometry_visible_when_blocked=True),
        Tile("deep", "SECRET_DEEP", 0, 0, 1, 1, 0, "blind"),
        Tile("next", "SECRET_NEXT", 0, 0, 1, 1, 0, "blind"),
    )
    engine = RuleEngine(tiles, (Pile("box", "blind", ("deep", "next")),))
    state = engine.initial_state()
    assert state.tiles[1].type_id == "SECRET_UNDER"  # full simulator information
    observation = engine.observe(state)
    serialized = str(asdict(observation))
    assert "SECRET_UNDER" not in serialized
    assert "SECRET_DEEP" not in serialized
    assert "SECRET_NEXT" not in serialized
    assert "'deep'" not in serialized
    assert "'next'" not in serialized
    assert next(t for t in observation.tiles if t.id == "under").type_id is None
    assert next(t for t in observation.tiles if t.id == "under").clickable is False

    state = engine.step(state, "cover")
    revealed = engine.observe(state)
    assert next(t for t in revealed.tiles if t.id == "under").type_id == "SECRET_UNDER"


def test_visible_face_and_count_are_explicit_level_settings():
    tiles = (
        Tile("top", "A", 0, 0, 1, 1, 1),
        Tile("under", "B", 0, 0, 1, 1, 0, blockers=("top",),
             geometry_visible_when_blocked=True, face_visible_when_blocked=True),
        Tile("p1", "C", 0, 0, 1, 1, 0, "auxiliary"),
        Tile("p2", "D", 0, 0, 1, 1, 0, "auxiliary"),
    )
    engine = RuleEngine(tiles, (Pile("aux", "auxiliary", ("p1", "p2"),
                                    top_face_visible=True, count_visible=True),))
    obs = engine.observe(engine.initial_state())
    assert next(t for t in obs.tiles if t.id == "under").type_id == "B"
    assert obs.piles[0].top_type_id == "C"
    assert obs.piles[0].visible_count == 2

