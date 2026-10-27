"""Run a hand-built miniature level; no real game data is used."""

from sheep_solver.core import Tile
from sheep_solver.simulator import RuleEngine


def main() -> None:
    tiles = (
        Tile("a1", "A", 0, 0, 1, 1, 1),
        Tile("a2", "A", 1, 0, 1, 1, 1),
        Tile("a3", "A", 2, 0, 1, 1, 1),
        Tile("b1", "B", 0, 0, 1, 1, 0, blockers=("a1",)),
        Tile("b2", "B", 1, 0, 1, 1, 0, blockers=("a2",)),
        Tile("b3", "B", 2, 0, 1, 1, 0, blockers=("a3",)),
    )
    engine = RuleEngine(tiles)
    state = engine.initial_state()
    for tile_id in ("a1", "a2", "a3", "b1", "b2", "b3"):
        state = engine.step(state, tile_id)
        print(f"click={tile_id} tray={state.tray} remaining={state.remaining_count} status={state.status}")


if __name__ == "__main__":
    main()

