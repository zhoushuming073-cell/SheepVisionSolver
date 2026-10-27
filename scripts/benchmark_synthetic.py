"""Public, asset-free performance smoke benchmark. Never clicks the desktop."""

from __future__ import annotations

import json
import statistics
import tempfile
import time
from pathlib import Path

import numpy as np
import psutil
from PIL import Image

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.control.loop import VisualController
from sheep_solver.control.mouse import SafeMouse
from sheep_solver.vision.detector import TemplateDetector, VisionProfile


def _summary(values: list[float]) -> dict[str, float]:
    return {"p50_ms": round(float(np.percentile(values, 50)), 2),
            "p95_ms": round(float(np.percentile(values, 95)), 2)}


def _fixture(folder: Path) -> tuple[CapturedFrame, CapturedFrame, VisionProfile]:
    rng = np.random.default_rng(20261027)
    records = []
    background = Image.new("RGBA", (50, 50), (220, 220, 150, 255))
    background.save(folder / "block_bg.png")
    icons = []
    for kind in range(1, 4):
        pixels = np.zeros((24, 24, 4), dtype=np.uint8)
        pixels[:, :, :3] = rng.integers(30, 220, size=(24, 24, 3), dtype=np.uint8)
        pixels[:, :, 3] = 255
        icon = Image.fromarray(pixels, "RGBA")
        icon.save(folder / f"block_{kind}.png")
        icons.append(icon)
        records.append({"file": f"block_{kind}.png", "type_id": kind,
                        "original_size": [50, 50], "offset": [0, 0]})
    (folder / "manifest.json").write_text(json.dumps({"records": records}), encoding="utf-8")

    def card(image: Image.Image, x: int, y: int) -> None:
        image.alpha_composite(background, (x, y))
        image.alpha_composite(icons[0], (x + 13, y + 13))

    before = Image.new("RGBA", (400, 380), (80, 125, 90, 255))
    for x in (40, 120, 200):
        card(before, x, 50)
    after = before.copy()
    after.paste((80, 125, 90, 255), (40, 50, 90, 100))
    card(after, 30, 310)
    frame_before = CapturedFrame(before.convert("RGB"), Region(0, 0, 400, 380), "before", "synthetic")
    frame_after = CapturedFrame(after.convert("RGB"), frame_before.region, "after", "synthetic")
    profile = VisionProfile(Region(0, 0, 400, 280), Region(0, 300, 400, 80),
                            scales=(1.0,), tray_scales=(1.0,),
                            icon_threshold=.95, click_threshold=.97)
    return frame_before, frame_after, profile


class _FakeMouse:
    def click(self, _x: int, _y: int) -> None:
        pass


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        before, after, profile = _fixture(folder)
        process = psutil.Process()
        detector = TemplateDetector(folder)
        initial = detector.detect(before, profile)
        if len(initial.tiles) != 3 or initial.tray:
            raise RuntimeError(f"Synthetic baseline invalid: {initial}")
        full, local, precheck, feedback, cycle = [], [], [], [], []
        rss = []
        cpu_start = process.cpu_times()
        wall_start = time.perf_counter()
        for _ in range(8):
            started = time.perf_counter()
            detector.detect(before, profile)
            full.append((time.perf_counter() - started) * 1000)
        for index in range(40):
            frames = iter((before, after, after))
            controller = VisualController(detector, profile, before.region,
                                          mouse=SafeMouse(_FakeMouse(), min_interval=0),
                                          capture=lambda _region: next(frames),
                                          poll_interval=.001, feedback_timeout=.2,
                                          log_path=folder / "actions.jsonl")
            controller.initialize_from_observation(before, initial)
            outcome = controller.run_once()
            if not outcome.success or outcome.metrics["full_detects"] != 0:
                raise RuntimeError(f"Synthetic cycle failed: {outcome.reason}")
            cycle.append(float(outcome.metrics["cycle_ms"]))
            local.append(float(outcome.metrics["local_detect_ms"]))
            precheck.append(float(outcome.metrics["preclick_ms"]))
            feedback.append(float(outcome.metrics["feedback_ms"]))
            if index % 10 == 9:
                rss.append(round(process.memory_info().rss / 2**20, 2))
        elapsed = time.perf_counter() - wall_start
        cpu_end = process.cpu_times()
        print(json.dumps({"fixture": "generated, no game assets", "full": _summary(full),
                          "local": _summary(local), "preclick": _summary(precheck),
                          "feedback": _summary(feedback), "cycle": _summary(cycle),
                          "cpu_one_core_percent": round(100 * (
                              (cpu_end.user + cpu_end.system) -
                              (cpu_start.user + cpu_start.system)) / elapsed, 2),
                          "rss_every_10_cycles_mib": rss}, ensure_ascii=False))


if __name__ == "__main__":
    main()
