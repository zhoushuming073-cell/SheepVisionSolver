"""Locate bundled read-only assets and persistent user data."""

from __future__ import annotations

import sys
from pathlib import Path


def resources_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[2]


def writable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return resources_root()
