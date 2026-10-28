"""Conservative, screen-only tile recognition using APK sprite templates."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from sheep_solver.capture.screen import CapturedFrame, Region
from sheep_solver.core.models import Observation, ObservedPile, ObservedTile
from .occlusion import apply_black_mask, masked_similarity, visible_mask


@dataclass(frozen=True, slots=True)
class VisionProfile:
    board: Region  # local coordinates within the selected game frame
    tray: Region
    scales: tuple[float, ...] = (0.55, 0.7, 0.85, 1.0, 1.15, 1.3)
    tray_scales: tuple[float, ...] | None = None
    icon_threshold: float = 0.88
    click_threshold: float = 0.94
    ambiguity_margin: float = 0.025
    border_error_limit: float = 55.0
    left_pile: Region | None = None
    right_pile: Region | None = None


@dataclass(frozen=True, slots=True)
class _Template:
    type_id: str
    rgba: np.ndarray
    inset_x: float
    inset_y: float
    original_width: float
    original_height: float


@dataclass(frozen=True, slots=True)
class _Candidate:
    type_id: str
    x: int
    y: int
    width: int
    height: int
    score: float
    shade_state: str = "unknown"

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.width / 2, self.y + self.height / 2


def _point_near(a: _Candidate, b: _Candidate) -> bool:
    ax, ay = a.center
    bx, by = b.center
    return abs(ax - bx) < min(a.width, b.width) * .35 and abs(ay - by) < min(a.height, b.height) * .35


def _overlap(a: _Candidate, b: _Candidate) -> float:
    x = max(0, min(a.x + a.width, b.x + b.width) - max(a.x, b.x))
    y = max(0, min(a.y + a.height, b.y + b.height) - max(a.y, b.y))
    return x * y / max(1, min(a.width * a.height, b.width * b.height))


class TemplateDetector:
    def __init__(self, template_dir: str | Path):
        # Masked matchTemplate on our small ROIs gains no latency from OpenCV's
        # default 32 workers; one worker also avoids large transient CPU spikes.
        cv2.setNumThreads(1)
        folder = Path(template_dir)
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        templates: list[_Template] = []
        for record in manifest["records"]:
            if not re.fullmatch(r"block_\d+\.png", record["file"]):
                continue
            image = np.array(Image.open(folder / record["file"]).convert("RGBA"))
            original = record["original_size"] or [image.shape[1], image.shape[0]]
            offset = record.get("offset", [0, 0])
            # Cocos offset is relative to the center; Y points upward.
            templates.append(_Template(
                str(record["type_id"]), image,
                (original[0] - image.shape[1]) / 2 + offset[0],
                (original[1] - image.shape[0]) / 2 - offset[1],
                float(original[0]), float(original[1]),
            ))
        if not templates:
            raise ValueError("No block templates found")
        self.templates = tuple(templates)
        self._templates_by_type = {template.type_id: template for template in templates}
        self.backgrounds = tuple(
            np.array(Image.open(folder / name).convert("RGBA"))
            for name in ("block_bg.png", "block_bg_change.png", "block_bg_gold.png")
            if (folder / name).exists()
        )
        mask_record = next((record for record in manifest["records"]
                            if record["file"] == "blackMask.png"), None)
        self._black_alpha: np.ndarray | None = None
        if mask_record is not None and (folder / "blackMask.png").exists():
            cropped = np.array(Image.open(folder / "blackMask.png").convert("RGBA"))[:, :, 3]
            original = mask_record["original_size"]
            offset = mask_record.get("offset", [0, 0])
            x = round((original[0] - cropped.shape[1]) / 2 + offset[0])
            y = round((original[1] - cropped.shape[0]) / 2 - offset[1])
            alpha = np.zeros((original[1], original[0]), dtype=np.uint8)
            alpha[y:y + cropped.shape[0], x:x + cropped.shape[1]] = cropped
            self._black_alpha = alpha

    @lru_cache(maxsize=32)
    def _scaled_black_alpha(self, width: int, height: int) -> np.ndarray:
        assert self._black_alpha is not None
        return cv2.resize(self._black_alpha, (width, height), interpolation=cv2.INTER_LINEAR)

    @lru_cache(maxsize=128)
    def _dark_icon(self, type_id: str, icon_w: int, icon_h: int,
                   card_w: int, card_h: int, inset_x: int,
                   inset_y: int) -> tuple[np.ndarray, np.ndarray, int]:
        icon, alpha = self._scaled_template(type_id, icon_w, icon_h)
        black = self._scaled_black_alpha(card_w, card_h)
        shade = black[inset_y:inset_y + icon_h, inset_x:inset_x + icon_w]
        return apply_black_mask(icon, shade), alpha, cv2.countNonZero(alpha)

    @lru_cache(maxsize=128)
    def _scaled_template(self, type_id: str, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        """Cache scaled sprites and masks with a strict entry bound."""
        base = self._templates_by_type[type_id].rgba
        interpolation = cv2.INTER_AREA if width < base.shape[1] else cv2.INTER_LINEAR
        scaled = cv2.resize(base, (width, height), interpolation=interpolation)
        mask = np.ascontiguousarray((scaled[:, :, 3] > 240).astype(np.uint8) * 255)
        return np.ascontiguousarray(scaled[:, :, :3]), mask

    @lru_cache(maxsize=64)
    def _scaled_background(self, index: int, width: int, height: int) -> np.ndarray:
        return cv2.resize(self.backgrounds[index], (width, height), interpolation=cv2.INTER_AREA)

    def _match(self, rgb: np.ndarray, roi: Region, profile: VisionProfile,
               scales: tuple[float, ...] | None = None,
               type_ids: frozenset[str] | None = None) -> list[_Candidate]:
        search = rgb[roi.top:roi.bottom, roi.left:roi.right]
        raw: list[_Candidate] = []
        for template in self.templates:
            if type_ids is not None and template.type_id not in type_ids:
                continue
            base = template.rgba
            for scale in scales if scales is not None else profile.scales:
                w = round(base.shape[1] * scale)
                h = round(base.shape[0] * scale)
                if w < 12 or h < 12 or w > roi.width or h > roi.height:
                    continue
                scaled_rgb, mask = self._scaled_template(template.type_id, w, h)
                if cv2.countNonZero(mask) < 50:
                    continue
                result = cv2.matchTemplate(search, scaled_rgb, cv2.TM_CCOEFF_NORMED, mask=mask)
                result = np.nan_to_num(result, nan=0.0, posinf=0.0, neginf=0.0)
                local_max = cv2.dilate(result, np.ones((9, 9), np.uint8))
                ys, xs = np.where((result >= profile.icon_threshold) & (result >= local_max - 1e-6))
                if len(xs) > 20:
                    best = np.argsort(result[ys, xs])[-20:]
                    xs, ys = xs[best], ys[best]
                for x, y in zip(xs, ys):
                    card_x = round(roi.left + int(x) - template.inset_x * scale)
                    card_y = round(roi.top + int(y) - template.inset_y * scale)
                    raw.append(_Candidate(template.type_id, card_x, card_y,
                                          round(template.original_width * scale),
                                          round(template.original_height * scale),
                                          float(result[y, x])))
        raw.sort(key=lambda item: item.score, reverse=True)
        groups: list[list[_Candidate]] = []
        for candidate in raw:
            group = next((items for items in groups if _point_near(candidate, items[0])), None)
            if group is None:
                groups.append([candidate])
            else:
                group.append(candidate)
        selected: list[_Candidate] = []
        for group in groups:
            best = group[0]
            runner_up = next((candidate for candidate in group if candidate.type_id != best.type_id), None)
            if runner_up and best.score - runner_up.score <= profile.ambiguity_margin:
                best = _Candidate("unknown", best.x, best.y, best.width, best.height, best.score)
            selected.append(best)
        return selected

    def _border_error(self, rgb: np.ndarray, candidate: _Candidate,
                      *, dark: bool = False,
                      visible: np.ndarray | None = None) -> float:
        x, y, w, h = candidate.x, candidate.y, candidate.width, candidate.height
        if x < 0 or y < 0 or x + w > rgb.shape[1] or y + h > rgb.shape[0]:
            return 255.0
        target = rgb[y:y + h, x:x + w].astype(np.int16)
        errors: list[float] = []
        for index in range(len(self.backgrounds)):
            image = self._scaled_background(index, w, h)
            expected = (apply_black_mask(image[:, :, :3], self._scaled_black_alpha(w, h))
                        if dark and self._black_alpha is not None else image[:, :, :3])
            edge = np.zeros((h, w), dtype=bool)
            edge[:max(3, h // 10)] = True
            edge[-max(3, h // 7):] = True
            edge[:, :max(3, w // 12)] = True
            edge[:, -max(3, w // 12):] = True
            edge &= image[:, :, 3] > 100
            if visible is not None:
                edge &= visible
            if edge.any():
                errors.append(float(np.abs(target[edge] - expected[edge]).mean()))
        return min(errors, default=255.0)

    def _shade(self, rgb: np.ndarray, candidate: _Candidate,
               border_limit: float) -> tuple[str, float]:
        normal = self._border_error(rgb, candidate)
        if self._black_alpha is None:
            return ("normal" if normal <= border_limit else "unknown"), normal
        dark = self._border_error(rgb, candidate, dark=True)
        if dark + 7 < normal and dark <= border_limit:
            return "dark", dark
        if normal <= border_limit:
            return "normal", normal
        return "unknown", min(normal, dark)

    def _partial_candidates(self, rgb: np.ndarray, roi: Region,
                            seeds: list[_Candidate], profile: VisionProfile) -> list[_Candidate]:
        """Classify dark cards at geometry proposed by observed nearby cards.

        The half-card shifts are search hypotheses, not assertions about level
        generation. Candidates must fit visible icon pixels and stay unclickable.
        """
        if self._black_alpha is None or not seeds:
            return []
        bright = [item for item in seeds if self._shade(rgb, item, profile.border_error_limit)[0] == "normal"
                  and item.score >= profile.click_threshold]
        if not bright:
            return []
        blockers = tuple((item.x, item.y, item.width, item.height) for item in bright)
        positions: dict[tuple[int, int, int, int], None] = {}
        for seed in bright:
            # APK row/column positions use the same cell pitch; card art is taller.
            dx = dy = round(seed.width / 2)
            for sx, sy in ((-dx, 0), (dx, 0), (0, -dy), (0, dy),
                           (-dx, -dy), (-dx, dy), (dx, -dy), (dx, dy)):
                x, y = seed.x + sx, seed.y + sy
                if (x < profile.board.left or y < profile.board.top or
                        x + seed.width > profile.board.right or
                        y + seed.height > profile.board.bottom or
                        x >= roi.right or y >= roi.bottom or
                        x + seed.width <= roi.left or y + seed.height <= roi.top):
                    continue
                probe = _Candidate("unknown", x, y, seed.width, seed.height, 0.0)
                if any(_point_near(probe, old) for old in seeds):
                    continue
                positions[(x, y, seed.width, seed.height)] = None
        output: list[_Candidate] = []
        for x, y, w, h in positions:
            exposed = visible_mask(x, y, w, h, blockers)
            occlusion = 1.0 - float(exposed.mean())
            if occlusion < .08 or occlusion > .85:
                continue
            matches: list[tuple[float, float, str]] = []
            for template in self.templates:
                scale = w / template.original_width
                icon_w, icon_h = round(template.rgba.shape[1] * scale), round(template.rgba.shape[0] * scale)
                ix, iy = x + round(template.inset_x * scale), y + round(template.inset_y * scale)
                if icon_w < 12 or icon_h < 12 or ix < 0 or iy < 0 or ix + icon_w > rgb.shape[1] or iy + icon_h > rgb.shape[0]:
                    continue
                offset_x, offset_y = ix - x, iy - y
                if offset_x < 0 or offset_y < 0 or offset_x + icon_w > w or offset_y + icon_h > h:
                    continue
                expected, alpha, icon_pixels = self._dark_icon(
                    template.type_id, icon_w, icon_h, w, h, offset_x, offset_y)
                valid = (alpha > 240) & exposed[offset_y:offset_y + icon_h, offset_x:offset_x + icon_w]
                if np.count_nonzero(valid) < max(100, int(icon_pixels * .15)):
                    continue
                score, error = masked_similarity(rgb[iy:iy + icon_h, ix:ix + icon_w], expected, valid)
                matches.append((score, error, template.type_id))
            matches.sort(reverse=True)
            if not matches:
                continue
            score, error, kind = matches[0]
            runner = max((value[0] for value in matches[1:]), default=-1.0)
            if score >= .92 and error <= 22 and score - runner > max(.05, profile.ambiguity_margin):
                output.append(_Candidate(kind, x, y, w, h, score, "dark"))
        output.sort(key=lambda item: item.score, reverse=True)
        unique: list[_Candidate] = []
        for item in output:
            if not any(_point_near(item, old) for old in (*seeds, *unique)):
                unique.append(item)
        return unique

    @staticmethod
    def _tray_occupied_count(rgb: np.ndarray, roi: Region) -> int:
        crop = rgb[roi.top:roi.bottom, roi.left:roi.right]
        red, green, blue = crop[:, :, 0], crop[:, :, 1], crop[:, :, 2]
        pale = ((red > 185) & (green > 185) & (blue > 125)).astype(np.uint8)
        count, _, stats, _ = cv2.connectedComponentsWithStats(pale, 8)
        return sum(1 for _x, _y, w, h, area in stats[1:count]
                   if 35 <= w <= 170 and 35 <= h <= 180 and area >= 500)

    def tray_card_count(self, frame: CapturedFrame, profile: VisionProfile) -> int:
        """Cheap occupancy check, without classifying tray icons."""
        return self._tray_occupied_count(self._rgb(frame), profile.tray)

    @staticmethod
    def _rgb(frame: CapturedFrame) -> np.ndarray:
        return frame.pixels

    def _describe_board(self, rgb: np.ndarray, board: list[_Candidate],
                        profile: VisionProfile) -> tuple[tuple[ObservedTile, ...], tuple[str, ...]]:
        warnings: list[str] = []
        observed: list[ObservedTile] = []
        shade_info = [self._shade(rgb, candidate, profile.border_error_limit)
                      for candidate in board]
        border_errors = [value[1] for value in shade_info]
        for index, candidate in enumerate(board):
            error = border_errors[index]
            shade = candidate.shade_state if candidate.shade_state != "unknown" else shade_info[index][0]
            blockers = tuple((other.x, other.y, other.width, other.height)
                             for j, other in enumerate(board) if j != index and
                             shade_info[j][0] == "normal" and
                             other.score >= profile.click_threshold and
                             _overlap(candidate, other) > .08)
            exposed = visible_mask(candidate.x, candidate.y, candidate.width,
                                   candidate.height, blockers)
            occlusion_fraction = round(1.0 - float(exposed.mean()), 3) if blockers else 0.0
            overlapping = any(_overlap(candidate, other) > .08 and
                              shade_info[j][0] != "dark" and
                              border_errors[j] <= profile.border_error_limit and
                              other.score >= candidate.score - .015
                              for j, other in enumerate(board) if index != j)
            clickable = (shade == "normal" and candidate.score >= profile.click_threshold and
                         candidate.type_id != "unknown" and
                         error <= profile.border_error_limit and not overlapping and
                         profile.board.contains(round(candidate.center[0]), round(candidate.center[1]), margin=3))
            if candidate.type_id == "unknown":
                click_reason = "type ambiguous"
            elif shade == "dark":
                click_reason = "black-mask shade; not clickable"
            elif candidate.score < profile.click_threshold:
                click_reason = "low icon confidence"
            elif error > profile.border_error_limit:
                click_reason = "background or occlusion uncertain"
            elif overlapping:
                click_reason = "overlapping visible candidates"
            elif not clickable:
                click_reason = "outside board"
            else:
                click_reason = "icon and visible border agree"
            if not clickable:
                warnings.append(f"tile_{index}: {click_reason}")
            observed.append(ObservedTile(
                id=f"screen-{index}", type_id=None if candidate.type_id == "unknown" else candidate.type_id,
                x=candidate.x, y=candidate.y, width=candidate.width, height=candidate.height,
                layer=0, region="main", clickable=clickable, confidence=candidate.score,
                clickable_confidence=(max(0.0, 1.0 - error / 255.0)
                                      if shade == "normal" and not overlapping else 0.0),
                visible_face=candidate.type_id != "unknown",
                clickability_reason=click_reason,
                visibility="full" if error <= profile.border_error_limit
                           and not overlapping and not blockers else "partial",
                occlusion_fraction=occlusion_fraction,
                shade_state=shade,
                occlusion_source="observed_overlap" if blockers else "unresolved",
            ))
        return tuple(observed), tuple(warnings)

    def detect_board_region(self, frame: CapturedFrame, profile: VisionProfile,
                            region: Region, *, fast: bool = True) -> tuple[ObservedTile, ...]:
        """Classify only a bounded board region; widen scales after a miss."""
        if not (profile.board.left <= region.left and profile.board.top <= region.top and
                region.right <= profile.board.right and region.bottom <= profile.board.bottom):
            raise ValueError("Local observation must remain inside calibrated board")
        rgb = self._rgb(frame)
        preferred = (profile.scales[len(profile.scales) // 2],)
        board = self._match(rgb, region, profile, preferred if fast else profile.scales)
        if fast and not board:
            board = self._match(rgb, region, profile, profile.scales)
        board = self._exclude_piles(board, profile)
        board.extend(self._partial_candidates(rgb, region, board, profile))
        board = self._exclude_piles(board, profile)
        observed, _ = self._describe_board(rgb, board, profile)
        return observed

    @staticmethod
    def _exclude_piles(board: list[_Candidate], profile: VisionProfile) -> list[_Candidate]:
        """Keep pile tops out of main-board decisions when the regions overlap."""
        piles = tuple(area for area in (profile.left_pile, profile.right_pile) if area is not None)
        return [item for item in board if not any(
            area.contains(round(item.center[0]), round(item.center[1])) for area in piles)]

    def read_tray(self, frame: CapturedFrame, profile: VisionProfile) -> tuple[str, ...] | None:
        """Independently classify the tray when the cheap occupancy counter disagrees."""
        rgb = self._rgb(frame)
        candidates = self._match(rgb, profile.tray, profile, profile.tray_scales)
        candidates.sort(key=lambda item: item.x)
        if any(item.type_id == "unknown" or item.score < profile.click_threshold or
               self._border_error(rgb, item) > profile.border_error_limit
               for item in candidates):
            return None
        return tuple(item.type_id for item in candidates)

    def confirm_target(self, frame: CapturedFrame, profile: VisionProfile,
                       tile: ObservedTile) -> bool:
        """Recheck one known type at its remembered position before a click."""
        if tile.type_id is None or tile.type_id not in self._templates_by_type:
            return False
        margin = max(8, round(max(tile.width, tile.height)))
        left = max(profile.board.left, round(tile.x) - margin)
        top = max(profile.board.top, round(tile.y) - margin)
        right = min(profile.board.right, round(tile.x + tile.width) + margin)
        bottom = min(profile.board.bottom, round(tile.y + tile.height) + margin)
        roi = Region(left, top, right - left, bottom - top)
        rgb = self._rgb(frame)
        source_width = self._templates_by_type[tile.type_id].original_width
        preferred = (min(profile.scales, key=lambda scale: abs(source_width * scale - tile.width)),)
        for scales in (preferred, profile.scales):
            matches = self._exclude_piles(self._match(rgb, roi, profile, scales), profile)
            observed, _ = self._describe_board(rgb, matches, profile)
            if any(candidate.clickable and candidate.type_id == tile.type_id and
                   abs(candidate.center[0] - tile.center[0]) <= 5 and
                   abs(candidate.center[1] - tile.center[1]) <= 5
                   for candidate in observed):
                return True
        return False

    def detect(self, frame: CapturedFrame, profile: VisionProfile) -> Observation:
        width, height = frame.image.size
        for roi in (profile.board, profile.tray):
            if not (roi.left >= 0 and roi.top >= 0 and roi.right <= width and roi.bottom <= height):
                raise ValueError("Board/tray ROI outside screenshot; recalibrate")
        if (profile.board.right > profile.tray.left and profile.tray.right > profile.board.left and
                profile.board.bottom > profile.tray.top and profile.tray.bottom > profile.board.top):
            raise ValueError("Board and tray regions overlap")
        rgb = self._rgb(frame)
        board = self._exclude_piles(self._match(rgb, profile.board, profile), profile)
        board.extend(self._partial_candidates(rgb, profile.board, board, profile))
        board = self._exclude_piles(board, profile)
        tray_matches = self._match(rgb, profile.tray, profile, profile.tray_scales)
        observed, board_warnings = self._describe_board(rgb, board, profile)
        warnings = list(board_warnings)
        tray_matches.sort(key=lambda item: item.x)
        trusted_tray = [item for item in tray_matches
                        if item.score >= profile.click_threshold and item.type_id != "unknown"
                        and self._border_error(rgb, item) <= profile.border_error_limit]
        tray = tuple(item.type_id for item in trusted_tray)
        occupied = self._tray_occupied_count(rgb, profile.tray)
        if len(trusted_tray) != len(tray_matches) or occupied > len(trusted_tray):
            warnings.append("Tray contains low-confidence candidate")
        piles: list[ObservedPile] = []
        for name, area in (("left", profile.left_pile), ("right", profile.right_pile)):
            if area is None:
                continue
            matches = self._match(rgb, area, profile)
            trusted = [item for item in matches if item.type_id != "unknown" and
                       item.score >= profile.click_threshold]
            top = max(trusted, key=lambda item: item.score, default=None)
            piles.append(ObservedPile(name, "blind", False,
                                      top.x if top else None, top.y if top else None,
                                      top.width if top else None, top.height if top else None,
                                      top.type_id if top else None, None))
        status = "playing" if observed else "unknown"
        return Observation(observed, tuple(piles), tray, status, frame.region.bbox,
                           frame.image.size, frame.timestamp,
                           tray_confidence=1.0 if not any("Tray" in w for w in warnings) else 0.0,
                           warnings=tuple(warnings))
