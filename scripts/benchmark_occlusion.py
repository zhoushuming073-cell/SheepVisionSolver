"""Asset-free v0.3 occlusion benchmark; never reads APK or clicks desktop."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.vision.detector import TemplateDetector, VisionProfile


def _fixture(folder: Path, *, scale: int, offset: tuple[int, int] | None,
             dark: bool, same_type: bool = False):
    rng = np.random.default_rng(20261028)
    records = []
    icons = {}
    for kind in range(1, 17):
        pixels = np.empty((36, 36, 4), dtype=np.uint8)
        pixels[:, :, :3] = rng.integers(25, 225, (36, 36, 3), dtype=np.uint8)
        pixels[:, :, 3] = 255
        icons[kind] = Image.fromarray(pixels, "RGBA")
        icons[kind].save(folder / f"block_{kind}.png")
        records.append({"file": f"block_{kind}.png", "type_id": kind,
                        "original_size": [60, 60], "offset": [0, 0]})
    background = Image.new("RGBA", (60, 60), (222, 220, 165, 255))
    background.save(folder / "block_bg.png")
    Image.new("RGBA", (60, 60), (0, 0, 0, 102)).save(folder / "blackMask.png")
    records.append({"file": "blackMask.png", "type_id": None,
                    "original_size": [60, 60], "offset": [0, 0]})
    (folder / "manifest.json").write_text(json.dumps({"records": records}), encoding="utf-8")

    size = 60 * scale
    icon_size = 36 * scale
    inset = 12 * scale
    canvas = Image.new("RGBA", (270 * scale, 210 * scale), (83, 132, 88, 255))
    bottom = (50 * scale, 40 * scale)
    labels = []

    def card(position, kind, shaded):
        image = background.resize((size, size))
        image.alpha_composite(icons[kind].resize((icon_size, icon_size)), (inset, inset))
        if shaded:
            image.alpha_composite(Image.new("RGBA", image.size, (0, 0, 0, 102)))
        canvas.alpha_composite(image, position)

    card(bottom, 1, dark)
    if offset != (0, 0):
        labels.append((bottom[0], bottom[1], "1", "dark" if dark else "normal", not dark))
    if offset is not None:
        top = (bottom[0] + offset[0] * scale, bottom[1] + offset[1] * scale)
        kind = 1 if same_type else 2
        card(top, kind, False)
        labels.append((top[0], top[1], str(kind), "normal", True))
    image = canvas.convert("RGB")
    frame = CapturedFrame(image, Region(0, 0, *image.size), "synthetic", "synthetic")
    profile = VisionProfile(Region(0, 0, 260 * scale, 160 * scale),
                            Region(0, 175 * scale, 260 * scale, 30 * scale),
                            scales=(float(scale),), tray_scales=(float(scale),),
                            icon_threshold=.88, click_threshold=.95)
    return frame, profile, labels


def _scores(predicted, labels):
    matched = []
    used = set()
    for x, y, kind, shade, clickable in labels:
        match = next(((index, tile) for index, tile in enumerate(predicted)
                      if index not in used and abs(tile.x - x) <= 3 and abs(tile.y - y) <= 3), None)
        if match is not None:
            used.add(match[0])
            matched.append((kind, shade, clickable, match[1]))
    return matched, len(predicted) - len(used), len(labels) - len(matched)


def main():
    cases = (("bright", 1, None, False, False),
             ("dark", 1, None, True, False),
             ("half", 1, (30, 0), True, False),
             ("quarter", 1, (30, 30), True, False),
             ("same-type", 1, (30, 0), True, True),
             ("hidden", 1, (0, 0), True, False),
             ("scaled-half", 2, (30, 0), True, False))
    full_ms, local_ms, matched_all = [], [], []
    false_positive = false_negative = 0
    by_case = {}
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        for name, scale, offset, dark, same_type in cases:
            frame, profile, labels = _fixture(folder, scale=scale, offset=offset,
                                              dark=dark, same_type=same_type)
            detector = TemplateDetector(folder)
            detector.detect(frame, profile)
            measured = []
            for _ in range(6):
                started = time.perf_counter()
                observed = detector.detect(frame, profile)
                measured.append((time.perf_counter() - started) * 1000)
            full_ms.extend(measured)
            for _ in range(6):
                started = time.perf_counter()
                detector.detect_board_region(frame, profile,
                                             Region(20 * scale, 20 * scale,
                                                    150 * scale, 125 * scale), fast=False)
                local_ms.append((time.perf_counter() - started) * 1000)
            matched, fp, fn = _scores(observed.tiles, labels)
            false_positive += fp
            false_negative += fn
            matched_all.extend(matched)
            by_case[name] = {"expected": len(labels), "detected": len(observed.tiles),
                             "type_correct": sum(kind == tile.type_id for kind, _, _, tile in matched),
                             "false_clickable": sum(tile.clickable and not click for _, _, click, tile in matched)}
    total_predicted = len(matched_all) + false_positive
    total_expected = len(matched_all) + false_negative
    output = {"fixture": "generated; seven scenes; sixteen fixed types",
              "detection_precision": round(len(matched_all) / total_predicted, 3),
              "detection_recall": round(len(matched_all) / total_expected, 3),
              "false_positive": false_positive, "false_negative": false_negative,
              "type_accuracy_on_matched": round(sum(kind == tile.type_id for kind, _, _, tile in matched_all) / len(matched_all), 3),
              "shade_accuracy_on_matched": round(sum(shade == tile.shade_state for _, shade, _, tile in matched_all) / len(matched_all), 3),
              "false_clickable_on_known_blocked": sum(tile.clickable and not clickable for _, _, clickable, tile in matched_all),
              "full_ms_p50_p95": [round(float(np.percentile(full_ms, p)), 2) for p in (50, 95)],
              "local_ms_p50_p95": [round(float(np.percentile(local_ms, p)), 2) for p in (50, 95)],
              "cases": by_case}
    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
