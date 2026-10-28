"""Evaluate a private hand-labelled screenshot without publishing its pixels.

Only labelled examples are scored. Set complete=true only after every visible
card in the board has an independent label; otherwise precision/recall are not
reported as whole-screen metrics.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sheep_solver.capture.screen import Region, load_frame
from sheep_solver.vision.detector import TemplateDetector, VisionProfile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("annotations", type=Path)
    parser.add_argument("--templates", type=Path, default=Path("assets/tiles/classic"))
    args = parser.parse_args()
    data = json.loads(args.annotations.read_text(encoding="utf-8"))
    frame = load_frame(data["image"])
    profile = VisionProfile(Region(*data["board"]), Region(*data["tray"]),
                            scales=(float(data["scale"]),),
                            tray_scales=(float(data["tray_scale"]),),
                            icon_threshold=.83, click_threshold=.88)
    observed = TemplateDetector(args.templates).detect(frame, profile)
    used = set()
    matched = []
    misses = []
    for label in data["tiles"]:
        x, y = label["x"], label["y"]
        nearest = min(((i, tile) for i, tile in enumerate(observed.tiles) if i not in used),
                      key=lambda pair: abs(pair[1].x - x) + abs(pair[1].y - y), default=None)
        if nearest is None or abs(nearest[1].x - x) > 4 or abs(nearest[1].y - y) > 4:
            misses.append({"x": x, "y": y, "type_id": label["type_id"]})
            continue
        used.add(nearest[0])
        matched.append((label, nearest[1]))
    output = {"complete_annotation": bool(data.get("complete", False)),
              "labelled": len(data["tiles"]), "matched": len(matched),
              "missed_labels": misses,
              "type_correct": sum(label["type_id"] == tile.type_id for label, tile in matched),
              "shade_correct": sum(label["shade_state"] == tile.shade_state for label, tile in matched),
              "dark_false_clickable": sum(label["shade_state"] == "dark" and tile.clickable
                                           for label, tile in matched)}
    if data.get("complete", False):
        output["detection_precision"] = len(matched) / len(observed.tiles) if observed.tiles else 0
        output["detection_recall"] = len(matched) / len(data["tiles"]) if data["tiles"] else 0
        output["unlabelled_detections"] = len(observed.tiles) - len(matched)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
