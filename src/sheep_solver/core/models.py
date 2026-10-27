"""Initial data contract. These types do not assert real game mechanics."""

from dataclasses import dataclass, field
from typing import Literal


Status = Literal["playing", "won", "lost"]
Region = Literal["main", "auxiliary", "blind"]


@dataclass(frozen=True, slots=True)
class Rules:
    match_size: int = 3
    tray_capacity: int = 7

    def __post_init__(self) -> None:
        if self.match_size < 2 or self.tray_capacity < 1:
            raise ValueError("Invalid rule parameters")


@dataclass(frozen=True, slots=True)
class Tile:
    id: str
    type_id: str
    x: float
    y: float
    width: float
    height: float
    layer: int
    region: Region = "main"
    blockers: tuple[str, ...] = ()
    geometry_visible_when_blocked: bool = False
    face_visible_when_blocked: bool = False


@dataclass(frozen=True, slots=True)
class Pile:
    """Special area, ordered from next clickable tile to deepest tile."""

    id: str
    region: Literal["auxiliary", "blind"]
    tile_ids: tuple[str, ...]
    top_face_visible: bool = False
    count_visible: bool = False


@dataclass(frozen=True, slots=True)
class MoveRecord:
    tile_id: str
    eliminated_type: str | None
    status_after: Status


@dataclass(frozen=True, slots=True)
class FullState:
    """Simulator-only state. All hidden types live here."""

    tiles: tuple[Tile, ...]
    piles: tuple[Pile, ...] = ()
    rules: Rules = field(default_factory=Rules)
    removed: frozenset[str] = frozenset()
    tray: tuple[str, ...] = ()
    status: Status = "playing"
    failure_reason: str | None = None
    history: tuple[MoveRecord, ...] = ()

    @property
    def remaining_count(self) -> int:
        return len(self.tiles) - len(self.removed)


@dataclass(frozen=True, slots=True)
class ObservedTile:
    id: str
    type_id: str | None  # None means unknown, never inferred from FullState.
    x: float
    y: float
    width: float
    height: float
    layer: int
    region: Region
    clickable: bool
    confidence: float = 1.0
    clickable_confidence: float = 1.0
    visible_face: bool = True
    clickability_reason: str = "simulator rule"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.x, self.y, self.width, self.height

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


@dataclass(frozen=True, slots=True)
class ObservedPile:
    id: str
    region: Literal["auxiliary", "blind"]
    clickable: bool
    x: float | None
    y: float | None
    width: float | None
    height: float | None
    top_type_id: str | None
    visible_count: int | None


@dataclass(frozen=True, slots=True)
class Observation:
    """Screen-facing state; deliberately has no hidden tile collection."""

    tiles: tuple[ObservedTile, ...]
    piles: tuple[ObservedPile, ...]
    tray: tuple[str, ...]
    status: Status
    game_region: tuple[int, int, int, int] | None = None
    screenshot_size: tuple[int, int] | None = None
    timestamp: str | None = None
    tray_confidence: float = 1.0
    warnings: tuple[str, ...] = ()

