"""Small, deterministic rule engine for manually constructed levels."""

from dataclasses import replace

from sheep_solver.core import (
    FullState, MoveRecord, Observation, ObservedPile, ObservedTile, Pile, Rules, Tile,
)


class RuleEngine:
    def __init__(self, tiles: tuple[Tile, ...], piles: tuple[Pile, ...] = (), rules: Rules | None = None):
        self.tiles = tiles
        self.piles = piles
        self.rules = rules or Rules()
        self._by_id = {tile.id: tile for tile in tiles}
        self._validate()

    def _validate(self) -> None:
        if not self.tiles or len(self._by_id) != len(self.tiles):
            raise ValueError("Tiles must be nonempty and have unique IDs")
        if len({pile.id for pile in self.piles}) != len(self.piles):
            raise ValueError("Pile IDs must be unique")
        used_in_pile: set[str] = set()
        for pile in self.piles:
            if not pile.tile_ids:
                raise ValueError("Pile must be nonempty")
            for tile_id in pile.tile_ids:
                if tile_id not in self._by_id or tile_id in used_in_pile:
                    raise ValueError("Pile tile missing or repeated")
                tile = self._by_id[tile_id]
                if tile.region != pile.region or tile.blockers:
                    raise ValueError("Pile tile region or blockers invalid")
                used_in_pile.add(tile_id)
        for tile in self.tiles:
            if not tile.id or not tile.type_id or tile.width <= 0 or tile.height <= 0:
                raise ValueError("Invalid tile fields")
            if tile.region == "main" and tile.id in used_in_pile:
                raise ValueError("Main tile cannot be in a pile")
            if tile.region != "main" and tile.id not in used_in_pile:
                raise ValueError("Special tile must belong to a pile")
            for blocker in tile.blockers:
                if blocker not in self._by_id or blocker == tile.id:
                    raise ValueError("Invalid blocker")
                if self._by_id[blocker].region != "main":
                    raise ValueError("Only main tiles can block main tiles")
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(tile_id: str) -> None:
            if tile_id in visiting:
                raise ValueError("Blocker graph has a cycle")
            if tile_id in visited:
                return
            visiting.add(tile_id)
            for blocker in self._by_id[tile_id].blockers:
                visit(blocker)
            visiting.remove(tile_id)
            visited.add(tile_id)

        for tile in self.tiles:
            visit(tile.id)

    def initial_state(self) -> FullState:
        return FullState(tiles=self.tiles, piles=self.piles, rules=self.rules)

    def _check_state(self, state: FullState) -> None:
        if state.tiles != self.tiles or state.piles != self.piles or state.rules != self.rules:
            raise ValueError("State belongs to a different level")

    def legal_moves(self, state: FullState) -> tuple[str, ...]:
        self._check_state(state)
        if state.status != "playing":
            return ()
        moves = [
            tile.id for tile in self.tiles
            if tile.region == "main"
            and tile.id not in state.removed
            and all(blocker in state.removed for blocker in tile.blockers)
        ]
        for pile in self.piles:
            remaining = next((tile_id for tile_id in pile.tile_ids if tile_id not in state.removed), None)
            if remaining is not None:
                moves.append(remaining)
        return tuple(moves)

    def step(self, state: FullState, tile_id: str) -> FullState:
        if tile_id not in self.legal_moves(state):
            raise ValueError(f"Illegal move: {tile_id}")
        tile_type = self._by_id[tile_id].type_id
        tray = (*state.tray, tile_type)
        eliminated = None
        if tray.count(tile_type) >= self.rules.match_size:
            tray = tuple(value for value in tray if value != tile_type)
            eliminated = tile_type
        removed = state.removed | {tile_id}
        status: str = "playing"
        reason = None
        if len(tray) >= self.rules.tray_capacity:
            status, reason = "lost", "tray_full"
        elif len(removed) == len(self.tiles) and not tray:
            status = "won"
        next_state = replace(state, removed=removed, tray=tray, status=status,
                             failure_reason=reason,
                             history=(*state.history, MoveRecord(tile_id, eliminated, status)))
        if status == "playing" and not self.legal_moves(next_state):
            next_state = replace(next_state, status="lost", failure_reason="no_moves",
                                 history=(*state.history, MoveRecord(tile_id, eliminated, "lost")))
        return next_state

    def observe(self, state: FullState) -> Observation:
        self._check_state(state)
        legal = set(self.legal_moves(state))
        observed_tiles: list[ObservedTile] = []
        for tile in self.tiles:
            if tile.region != "main" or tile.id in state.removed:
                continue
            blocked = any(blocker not in state.removed for blocker in tile.blockers)
            if blocked and not tile.geometry_visible_when_blocked:
                continue
            observed_tiles.append(ObservedTile(
                id=tile.id,
                type_id=tile.type_id if not blocked or tile.face_visible_when_blocked else None,
                x=tile.x, y=tile.y, width=tile.width, height=tile.height,
                layer=tile.layer, region=tile.region, clickable=tile.id in legal,
            ))
        observed_piles: list[ObservedPile] = []
        for pile in self.piles:
            remaining = [tile_id for tile_id in pile.tile_ids if tile_id not in state.removed]
            top = remaining[0] if remaining else None
            top_tile = self._by_id[top] if top else None
            observed_piles.append(ObservedPile(
                id=pile.id, region=pile.region, clickable=top in legal if top else False,
                x=top_tile.x if top_tile else None, y=top_tile.y if top_tile else None,
                width=top_tile.width if top_tile else None,
                height=top_tile.height if top_tile else None,
                top_type_id=top_tile.type_id if top_tile and pile.top_face_visible else None,
                visible_count=len(remaining) if pile.count_visible else None,
            ))
        return Observation(tuple(observed_tiles), tuple(observed_piles), state.tray, state.status)

