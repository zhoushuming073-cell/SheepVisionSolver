"""Offline screenshot analysis: python -m sheep_solver.vision.cli --help."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from sheep_solver.capture.screen import Region, load_frame
from sheep_solver.planning import BasicPlanner

from .debug import draw_debug
from .detector import TemplateDetector, VisionProfile


def parse_region(text: str) -> Region:
    values = tuple(int(piece) for piece in text.split(","))
    if len(values) != 4:
        raise argparse.ArgumentTypeError("Region format: left,top,width,height")
    return Region(*values)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect classic Sheep tiles from a saved screenshot")
    parser.add_argument("image", type=Path)
    parser.add_argument("--board", type=parse_region, required=True)
    parser.add_argument("--tray", type=parse_region, required=True)
    parser.add_argument("--scale", type=float, default=.68)
    parser.add_argument("--templates", type=Path, default=Path("assets/tiles/classic"))
    parser.add_argument("--output", type=Path, default=Path("data/screenshots/debug-output.png"))
    args = parser.parse_args()
    if not .25 <= args.scale <= 2.5:
        parser.error("--scale must be between .25 and 2.5")
    frame = load_frame(args.image)
    profile = VisionProfile(args.board, args.tray,
                            scales=tuple(round(args.scale + d, 3) for d in (-.03, 0, .03)),
                            tray_scales=tuple(round(args.scale * factor, 3)
                                              for factor in (1.18, 1.23, 1.28)),
                            icon_threshold=.83, click_threshold=.88)
    observation = TemplateDetector(args.templates).detect(frame, profile)
    decision = BasicPlanner().decide(observation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    draw_debug(frame.image, observation).save(args.output)
    print(json.dumps({"tiles": [asdict(tile) for tile in observation.tiles],
                      "tray": observation.tray, "warnings": observation.warnings,
                      "decision": asdict(decision), "debug_image": str(args.output)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
