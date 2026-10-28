"""Tk desktop application. Launch with python -m sheep_solver.gui.app."""

from __future__ import annotations

import json
import shutil
import time
import tkinter as tk
from dataclasses import asdict
from queue import Empty, Full, Queue
from datetime import datetime
from threading import Event, Thread
from tkinter import filedialog, messagebox, simpledialog, ttk

from PIL import Image, ImageTk

from sheep_solver.capture.screen import (CapturedFrame, Region, capture_frame,
                                         load_frame, save_frame, set_dpi_awareness, virtual_screen)
from sheep_solver.capture.selector import select_region
from sheep_solver.capture.window import (WindowInfo, windows, current_client,
                                         focus_window, window_for_region)
from sheep_solver.control.loop import StepCode, StepOutcome, VisualController
from sheep_solver.control.mouse import SafeMouse, WindowsMouseBackend
from sheep_solver.core.models import Observation
from sheep_solver.runtime_paths import resources_root, writable_root
from sheep_solver.vision import TemplateDetector, VisionProfile, draw_debug
from sheep_solver.vision.calibration import Layout, locate_layout, tray_scales_from_slots

ROOT = writable_root()
TEMPLATE_DIR = resources_root() / "assets" / "tiles" / "classic"
CALIBRATION = ROOT / "data" / "calibration.json"
LOG_PATH = ROOT / "data" / "logs" / "actions.jsonl"


def nonoverlap_position(game: Region, window: Region, screen: Region,
                        margin: int = 24) -> tuple[int, int] | None:
    """Place a control window outside the physical-pixel capture region."""
    if window.width > screen.width or window.height > screen.height:
        return None
    intersects = (window.left < game.right and window.right > game.left and
                  window.top < game.bottom and window.bottom > game.top)
    if not intersects:
        return window.left, window.top
    y = max(screen.top, min(window.top, screen.bottom - window.height))
    x_right = game.right + margin
    if x_right + window.width <= screen.right:
        return x_right, y
    x_left = game.left - window.width - margin
    if x_left >= screen.left:
        return x_left, y
    x = max(screen.left, min(window.left, screen.right - window.width))
    y_below = game.bottom + margin
    if y_below + window.height <= screen.bottom:
        return x, y_below
    y_above = game.top - window.height - margin
    if y_above >= screen.top:
        return x, y_above
    return None


class SheepApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("SheepVisionSolver — Windows 视觉控制")
        self.root.geometry("1170x900")
        self.detector = TemplateDetector(TEMPLATE_DIR)
        self.game_region: Region | None = None
        self.board: Region | None = None
        self.tray: Region | None = None
        self.left_pile: Region | None = None
        self.right_pile: Region | None = None
        self.bound_window: WindowInfo | None = None
        self.window_content: Region | None = None
        self.frame: CapturedFrame | None = None
        self.observation: Observation | None = None
        self.controller: VisualController | None = None
        self.stop_event = Event()
        self.hotkey_event = Event()
        self.mouse = SafeMouse(stop_event=self.stop_event)
        self.running = False
        self.needs_manual_sync = False
        self.step_active = False
        self.step_thread: Thread | None = None
        self.step_results: Queue[StepOutcome] = Queue(maxsize=2)
        self.recognition_results: Queue[tuple[Observation | None, str | None]] = Queue(maxsize=1)
        self.recognition_active = False
        self._last_preview = 0.0
        self.feedback_failures = 0
        self.mark_mode: str | None = None
        self.drag_start: tuple[int, int] | None = None
        self.preview_scale = 1.0
        self.photo: ImageTk.PhotoImage | None = None
        self.list_ids: list[str] = []
        self._build()
        self._load_calibration()
        self.root.after(0, self._move_off_game)
        self.hotkeys = None
        try:
            from pynput.keyboard import GlobalHotKeys
            self.hotkeys = GlobalHotKeys({"<ctrl>+<alt>+s": self.emergency_stop})
            self.hotkeys.start()
        except Exception as exc:
            self._status(f"全局热键不可用：{exc}；仍可使用停止按钮")
        self.root.after(100, self._poll_hotkey)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        sidebar = ttk.Frame(outer, width=280)
        sidebar.pack(side="left", fill="y", padx=(0, 8))
        sidebar.pack_propagate(False)
        for label, command in (
            ("框选游戏区域（自动分区）", self.select_game_region),
            ("自动发现游戏窗口", self.auto_locate),
            ("获取截图", self.capture),
            ("打开本地截图", self.open_image),
            ("设置主牌区（在图上拖动）", lambda: self._begin_mark("board")),
            ("设置卡槽区（在图上拖动）", lambda: self._begin_mark("tray")),
            ("识别当前画面", self.recognize),
            ("点击选中方块一步", self.manual_click),
            ("自动选择并点击一步", self.auto_once),
            ("开始基础自动操作", self.start_auto),
            ("暂停", self.pause),
            ("停止", self.stop),
            ("保存日志", self.save_log),
        ):
            ttk.Button(sidebar, text=label, command=command).pack(fill="x", pady=3)
        ttk.Label(sidebar, text="紧急停止：Ctrl+Alt+S").pack(anchor="w", pady=(9, 4))
        ttk.Label(sidebar, text="模板缩放（按截图宽度预填）").pack(anchor="w", pady=(4, 2))
        self.scale_var = tk.StringVar(value="0.68")
        ttk.Entry(sidebar, textvariable=self.scale_var).pack(fill="x", pady=(0, 5))
        self.status_var = tk.StringVar(value="请选择游戏区域，或打开截图做离线识别")
        ttk.Label(sidebar, textvariable=self.status_var, wraplength=265).pack(fill="x", pady=5)
        self.tray_var = tk.StringVar(value="卡槽：未识别")
        ttk.Label(sidebar, textvariable=self.tray_var, wraplength=265).pack(fill="x", pady=5)
        self.stats_var = tk.StringVar(value="确认点击 0 · 恢复 0 · 全量识别 0 · 反馈失败 0")
        ttk.Label(sidebar, textvariable=self.stats_var, wraplength=265).pack(fill="x", pady=5)
        ttk.Label(sidebar, text="识别出的主牌（选择后可人工点击）").pack(anchor="w", pady=(12, 2))
        self.tile_list = tk.Listbox(sidebar, height=16, width=34)
        self.tile_list.pack(fill="both", expand=True)
        preview_container = ttk.Frame(outer)
        preview_container.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(preview_container, bg="#202327", cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._mark_start)
        self.canvas.bind("<B1-Motion>", self._mark_move)
        self.canvas.bind("<ButtonRelease-1>", self._mark_end)
        self.canvas.bind("<Configure>", lambda _event: self._show_preview())

    def _status(self, text: str) -> None:
        self.status_var.set(text)

    def _move_off_game(self) -> str:
        if self.game_region is None:
            return "clear"
        self.root.update_idletasks()
        window = Region(self.root.winfo_rootx(), self.root.winfo_rooty(),
                        max(1, self.root.winfo_width()), max(1, self.root.winfo_height()))
        destination = nonoverlap_position(self.game_region, window, virtual_screen())
        if destination is None:
            self._status("控制窗口与游戏截图区域重叠，桌面没有足够空间；请调整游戏区域")
            return "blocked"
        if destination == (window.left, window.top):
            return "clear"
        self.root.geometry(f"+{destination[0]}+{destination[1]}")
        self.root.update_idletasks()
        self._status("已将控制窗口移出游戏区域；请重新获取截图")
        return "moved"

    def _load_calibration(self) -> None:
        if not CALIBRATION.exists():
            return
        try:
            data = json.loads(CALIBRATION.read_text(encoding="utf-8"))
            self.game_region = Region(*data["game_region"])
            self.board = Region(*data["board"]) if data.get("board") else None
            self.tray = Region(*data["tray"]) if data.get("tray") else None
            self.left_pile = Region(*data["left_pile"]) if data.get("left_pile") else None
            self.right_pile = Region(*data["right_pile"]) if data.get("right_pile") else None
            bounds = virtual_screen()
            if not (bounds.contains(self.game_region.left, self.game_region.top) and
                    bounds.contains(self.game_region.right - 1, self.game_region.bottom - 1)):
                self.game_region = self.board = self.tray = self.left_pile = self.right_pile = None
                self._status("显示器布局变化，请重新框选游戏区域")
            else:
                self._status("已加载上次校准；窗口移动后请重新框选")
        except (OSError, ValueError, KeyError, TypeError):
            self._status("校准文件无效，请重新框选")

    def _save_calibration(self) -> None:
        if self.game_region is None:
            return
        CALIBRATION.parent.mkdir(parents=True, exist_ok=True)
        data = {"game_region": [self.game_region.left, self.game_region.top,
                                self.game_region.width, self.game_region.height],
                "board": [self.board.left, self.board.top, self.board.width, self.board.height] if self.board else None,
                "tray": [self.tray.left, self.tray.top, self.tray.width, self.tray.height] if self.tray else None,
                "left_pile": [self.left_pile.left, self.left_pile.top, self.left_pile.width, self.left_pile.height] if self.left_pile else None,
                "right_pile": [self.right_pile.left, self.right_pile.top, self.right_pile.width, self.right_pile.height] if self.right_pile else None}
        CALIBRATION.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def select_game_region(self) -> None:
        self.stop()
        if self.step_active or self.recognition_active:
            self._status("请等待当前操作停止后再重新校准")
            return

        def selected(region: Region | None) -> None:
            if region is None:
                self._status("已取消区域选择")
                return
            self.game_region = region
            self._bind_selected_region(region)
            self.board = self.tray = self.left_pile = self.right_pile = None
            self.controller = None
            self.observation = None
            self._save_calibration()
            self._status(f"游戏区域：{region.bbox}；正在自动识别牌堆和卡槽")
            self.capture()

        try:
            select_region(self.root, selected)
        except Exception as exc:
            self._status(f"区域选择失败：{exc}")

    def auto_locate(self) -> None:
        if self.step_active or self.recognition_active:
            self._status("请等待当前操作结束")
            return
        available = windows()
        candidates = [item for item in available if
                      any(token in item.title.lower() for token in ("mumu", "羊了个羊", "sheep"))]
        if not candidates:
            candidates = list(available)
        if not candidates:
            self._status("没有可见的候选窗口；请手动框选")
            return
        if len(candidates) > 1:
            choices = "\n".join(f"{i + 1}. {item.title} ({item.client.width}×{item.client.height})"
                                for i, item in enumerate(candidates))
            selected = simpledialog.askinteger("选择游戏窗口", choices + "\n输入序号", parent=self.root,
                                               minvalue=1, maxvalue=len(candidates))
            if selected is None:
                return
            target = candidates[selected - 1]
        else:
            target = candidates[0]
        self.root.update_idletasks()
        control = Region(self.root.winfo_rootx(), self.root.winfo_rooty(),
                         max(1, self.root.winfo_width()), max(1, self.root.winfo_height()))
        placement = nonoverlap_position(target.client, control, virtual_screen())
        if placement is None:
            self._status("控制窗口遮挡游戏，无法安全自动定位；请先移动窗口")
            return
        if placement != (control.left, control.top):
            self.root.geometry(f"+{placement[0]}+{placement[1]}")
            self.root.update_idletasks()
        self.recognition_active = True
        self._status("正在检测游戏内容和卡槽锚点…")

        def worker() -> None:
            try:
                captured = capture_frame(target.client)
                layout = locate_layout(captured.image)
                if layout is None or layout.confidence < .53:
                    raise ValueError("游戏内容或卡槽锚点不可靠，请手动框选")
                content = layout.content
                region = Region(target.client.left + content.left, target.client.top + content.top,
                                content.width, content.height)
                frame = CapturedFrame(captured.image.crop(content.bbox), region,
                                      captured.timestamp, "screen")
                scale = .68 * content.width / 888
                profile = VisionProfile(layout.board, layout.tray,
                                        scales=tuple(round(scale + d, 3) for d in (-.03, 0, .03)),
                                        tray_scales=tray_scales_from_slots(
                                            layout.tray, self.detector.templates[0].original_width),
                                        icon_threshold=.83, click_threshold=.88,
                                        left_pile=layout.left_pile, right_pile=layout.right_pile)
                observed = self.detector.detect(frame, profile)
                if len(observed.tiles) < 3 or observed.tray_confidence < .95:
                    raise ValueError("卡牌或卡槽校验不足，请手动调整")
                self.recognition_results.put(((target, layout, frame, observed, scale), None))
            except Exception as exc:
                self.recognition_results.put((None, str(exc)))

        Thread(target=worker, daemon=True).start()
        self.root.after(50, self._poll_auto_location)

    def _poll_auto_location(self) -> None:
        try:
            result, error = self.recognition_results.get_nowait()
        except Empty:
            self.root.after(50, self._poll_auto_location)
            return
        self.recognition_active = False
        if error or result is None:
            self._status(f"自动定位失败：{error}")
            return
        target, layout, frame, observed, scale = result
        self.bound_window, self.window_content = target, layout.content
        self.game_region, self.board, self.tray = frame.region, layout.board, layout.tray
        self.left_pile, self.right_pile = layout.left_pile, layout.right_pile
        self.frame, self.observation, self.controller = frame, observed, None
        self.mouse = SafeMouse(WindowsMouseBackend(self.stop_event,
                               expected_hwnd=target.hwnd, expected_pid=target.pid),
                               stop_event=self.stop_event)
        self.scale_var.set(f"{scale:.3f}")
        self.needs_manual_sync = False
        self._save_calibration()
        self._show_preview(draw_debug(frame.image, observed))
        self._update_tile_list(observed)
        self.tray_var.set("卡槽：" + (", ".join(observed.tray) or "空"))
        self._status(f"已绑定 {target.title}；识别 {len(observed.tiles)} 张牌")

    def capture(self) -> None:
        if self.step_active or self.recognition_active:
            self._status("操作正在进行，请先暂停并等待完成")
            return
        if self.game_region is None:
            self._status("请先选择游戏区域")
            return
        placement = self._move_off_game()
        if placement == "blocked":
            return
        if placement == "moved":
            self.root.after(200, self.capture)
            return
        try:
            if self.bound_window is not None:
                current = self._bound_content_region()
                if current is None or not focus_window(self.bound_window.hwnd, self.bound_window.pid):
                    self._status("游戏窗口不在前台或尺寸已变化；请重新框选游戏区域")
                    return
                self.game_region = current
            self.frame = capture_frame(self.game_region)
            self.scale_var.set(f"{.68 * self.frame.image.width / 888:.3f}")
            self.observation = None
            self.controller = None
            if self.board is None or self.tray is None:
                layout = locate_layout(self.frame.image)
                if layout is not None and layout.confidence >= .53:
                    self.board, self.tray, self.left_pile, self.right_pile, _ = layout.regions_in_image()
                    self._save_calibration()
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            save_frame(self.frame, ROOT / "data" / "screenshots" / f"capture-{stamp}.png")
            self._show_preview(self.frame.image)
            if self.board is not None and self.tray is not None:
                self._status("截图成功；已自动标出主牌、左右牌堆和卡槽，正在识别牌面")
                self.recognize()
            else:
                self._status("截图成功，但无法确认牌堆和卡槽位置；请在图上手动修正")
        except Exception as exc:
            self._status(f"截图失败：{exc}")

    def open_image(self) -> None:
        if self.step_active or self.recognition_active:
            self._status("操作正在进行，请先暂停并等待完成")
            return
        path = filedialog.askopenfilename(filetypes=[("图像", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return
        try:
            self.frame = load_frame(path)
            self.scale_var.set(f"{.68 * self.frame.image.width / 888:.3f}")
            self.observation = None
            self.controller = None
            self.board = self.tray = self.left_pile = self.right_pile = None
            layout = locate_layout(self.frame.image)
            if layout is not None and layout.confidence >= .53:
                self.board, self.tray, self.left_pile, self.right_pile, _ = layout.regions_in_image()
            self._show_preview(self.frame.image)
            self._status("已打开离线截图并尝试自动分区；可识别和调试，不能据此点击实时桌面")
        except Exception as exc:
            self._status(f"打开失败：{exc}")

    def _show_preview(self, image: Image.Image | None = None) -> None:
        if self.frame is None:
            return
        original = image or self.frame.image
        self.root.update_idletasks()
        width = max(200, self.canvas.winfo_width() - 8)
        height = max(200, self.canvas.winfo_height() - 8)
        scale = min(width / original.width, height / original.height, 1.0)
        display = original.resize((max(1, round(original.width * scale)),
                                   max(1, round(original.height * scale))), Image.Resampling.LANCZOS)
        self.preview_scale = scale
        self.photo = ImageTk.PhotoImage(display)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        for name, region, color in (("主牌区", self.board, "#42df80"), ("卡槽区", self.tray, "#facc15"),
                                    ("左牌堆", self.left_pile, "#50a8ff"),
                                    ("右牌堆", self.right_pile, "#50a8ff")):
            if region:
                self.canvas.create_rectangle(region.left * scale, region.top * scale,
                                             region.right * scale, region.bottom * scale,
                                             outline=color, width=2, tags="calibration")
                self.canvas.create_text(region.left * scale + 3, region.top * scale + 3,
                                        text=name, fill=color, anchor="nw", tags="calibration")

    def _begin_mark(self, mode: str) -> None:
        if self.step_active or self.recognition_active:
            self._status("请先暂停并等待当前操作结束")
            return
        if self.frame is None:
            self._status("先获取或打开截图")
            return
        self.mark_mode = mode
        self._status("在截图上拖动鼠标框选" + ("主牌区（排除底部盲盒和按钮）" if mode == "board" else "卡槽区"))

    def _mark_start(self, event: tk.Event) -> None:
        if self.mark_mode:
            self.drag_start = (event.x, event.y)
            self.canvas.delete("drag")

    def _mark_move(self, event: tk.Event) -> None:
        if self.drag_start:
            self.canvas.delete("drag")
            self.canvas.create_rectangle(*self.drag_start, event.x, event.y,
                                         outline="red", width=2, tags="drag")

    def _mark_end(self, event: tk.Event) -> None:
        if not self.drag_start or not self.frame or not self.mark_mode:
            return
        x1, y1 = self.drag_start
        self.drag_start = None
        self.canvas.delete("drag")
        if abs(event.x - x1) < 3 or abs(event.y - y1) < 3:
            self._status("请拖动一个矩形区域")
            return
        region = Region.from_drag(round(x1 / self.preview_scale), round(y1 / self.preview_scale),
                                  round(event.x / self.preview_scale), round(event.y / self.preview_scale))
        if (region.width < 50 or region.height < 50 or region.left < 0 or region.top < 0 or
                region.right > self.frame.image.width or region.bottom > self.frame.image.height):
            self._status("框选无效，请在截图范围内重新拖动")
            return
        if self.mark_mode == "board":
            self.board = region
        else:
            self.tray = region
        self.controller = None
        self.observation = None
        self.mark_mode = None
        if self.frame.source == "screen":
            self._save_calibration()
        self._show_preview()
        self._status(f"已校准：{region.bbox}")

    def _profile(self) -> VisionProfile | None:
        if self.board is None or self.tray is None:
            self._status("尚未找到主牌区和卡槽；请重新框选游戏区域或在图上修正")
            return None
        try:
            base = float(self.scale_var.get())
            if not .25 <= base <= 2.5:
                raise ValueError("模板缩放超出范围")
        except ValueError:
            self._status("模板缩放需为 0.25 到 2.5 的数字")
            return None
        scales = tuple(round(base + delta, 3) for delta in (-.03, 0, .03))
        tray_scales = tray_scales_from_slots(self.tray, self.detector.templates[0].original_width)
        return VisionProfile(self.board, self.tray, scales=scales, tray_scales=tray_scales,
                             icon_threshold=.83, click_threshold=.88,
                             left_pile=self.left_pile, right_pile=self.right_pile)

    def recognize(self) -> None:
        profile = self._profile()
        if profile is None or self.frame is None or self.step_active or self.recognition_active:
            return
        frame = self.frame
        self.recognition_active = True
        self._status("后台识别中…")

        def worker() -> None:
            try:
                self.recognition_results.put((self.detector.detect(frame, profile), None))
            except Exception as exc:
                self.recognition_results.put((None, str(exc)))

        Thread(target=worker, daemon=True).start()
        self.root.after(50, self._poll_recognition)

    def _poll_recognition(self) -> None:
        try:
            observation, error = self.recognition_results.get_nowait()
        except Empty:
            self.root.after(50, self._poll_recognition)
            return
        self.recognition_active = False
        if error or observation is None or self.frame is None:
            self._status(f"识别失败：{error}")
            return
        self.observation = observation
        self.controller = None  # Manual recognition is an explicit state resync.
        self.needs_manual_sync = False
        debug_image = draw_debug(self.frame.image, observation)
        self._show_preview(debug_image)
        self._update_tile_list(observation)
        self.tray_var.set("卡槽：" + (", ".join(observation.tray) or "空"))
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        debug_path = ROOT / "data" / "screenshots" / f"debug-{stamp}.png"
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_image.save(debug_path)
        (debug_path.with_suffix(".json")).write_text(
            json.dumps({"tiles": [asdict(tile) for tile in observation.tiles],
                        "tray": observation.tray, "warnings": observation.warnings,
                        "timestamp": observation.timestamp}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        self._status(f"识别 {len(observation.tiles)} 张；可点 {sum(t.clickable for t in observation.tiles)} 张；调试图：{debug_path}")

    def _update_tile_list(self, observation: Observation) -> None:
        self.tile_list.delete(0, tk.END)
        self.list_ids.clear()
        for tile in observation.tiles:
            self.tile_list.insert(tk.END, f"{tile.id} 类型{tile.type_id or '?'} {tile.confidence:.2f} "
                                  f"{tile.shade_state}/{tile.visibility} "
                                  f"{'可点' if tile.clickable else '待核实'}")
            self.list_ids.append(tile.id)

    def _new_controller(self) -> VisualController | None:
        if self.step_active or self.recognition_active:
            self._status("当前一步仍在识别或验证，请等待其结束")
            return None
        if self.needs_manual_sync:
            self._status("画面状态未确认；请等动画结束后重新获取截图并识别")
            return None
        if self._move_off_game() != "clear":
            self._status("控制窗口已移动；请重新获取截图并识别后再点击")
            return None
        if self.bound_window is not None and not focus_window(self.bound_window.hwnd,
                                                                self.bound_window.pid):
            self._status("无法将游戏窗口置前；已禁止点击")
            return None
        profile = self._profile()
        if profile is None or self.game_region is None or self.frame is None or self.frame.source != "screen":
            self._status("点击前请从实时游戏区域获取截图并完成主牌区、卡槽区校准")
            return None
        if (self.controller is not None and self.controller.memory is not None and
                self.controller.profile == profile and
                self.controller.game_region == self.game_region and
                not self.controller.stop_event.is_set()):
            return self.controller
        self.stop_event.clear()
        self.controller = VisualController(self.detector, profile, self.game_region,
                                           mouse=self.mouse, stop_event=self.stop_event,
                                            log_path=LOG_PATH,
                                            window_region=(lambda: self._bound_content_region())
                                            if self.bound_window else None)
        if self.observation is not None and self.frame.region == self.game_region:
            try:
                self.controller.initialize_from_observation(self.frame, self.observation)
            except ValueError:
                pass  # Fresh screen recognition remains available in the worker.
        return self.controller

    def _bind_selected_region(self, region: Region) -> None:
        target = window_for_region(region)
        self.bound_window = target
        if target is None:
            self.window_content = None
            self.mouse = SafeMouse(stop_event=self.stop_event)
            return
        self.window_content = Region(region.left - target.client.left,
                                     region.top - target.client.top,
                                     region.width, region.height)
        self.mouse = SafeMouse(WindowsMouseBackend(self.stop_event,
                               expected_hwnd=target.hwnd, expected_pid=target.pid),
                               stop_event=self.stop_event)

    def _bound_content_region(self) -> Region | None:
        if self.bound_window is None or self.window_content is None:
            return None
        client = current_client(self.bound_window.hwnd, self.bound_window.pid)
        content = self.window_content
        if client is None or client.width != self.bound_window.client.width or client.height != self.bound_window.client.height:
            return None
        return Region(client.left + content.left, client.top + content.top,
                      content.width, content.height)

    def _apply_outcome(self, outcome: StepOutcome) -> None:
        self._status(f"{outcome.code.value}：{outcome.reason}；决策：{outcome.decision.reason}")
        if outcome.code in (StepCode.ANIMATION_TIMEOUT, StepCode.CLICK_UNCHANGED,
                            StepCode.TRAY_CONFLICT, StepCode.VISUAL_CONFLICT):
            self.feedback_failures += 1
        if outcome.code in (StepCode.ANIMATION_TIMEOUT, StepCode.PRECLICK_CHANGED,
                            StepCode.TARGET_CHANGED, StepCode.CLICK_UNCHANGED,
                            StepCode.UNRECOVERABLE, StepCode.WINDOW_CHANGED):
            if outcome.code in (StepCode.CLICK_UNCHANGED, StepCode.UNRECOVERABLE,
                                StepCode.WINDOW_CHANGED) or not self.running:
                self.controller = None
            self.frame = None
            self.observation = None
            self.needs_manual_sync = True
        if outcome.after_frame and outcome.after:
            self.frame = outcome.after_frame
            self.observation = outcome.after
            self.game_region = outcome.after_frame.region
            self._update_tile_list(outcome.after)
            if not self.running or time.monotonic() - self._last_preview >= .5:
                self._show_preview(self.frame.image if self.running else
                                   draw_debug(self.frame.image, outcome.after))
                self._last_preview = time.monotonic()
            self.tray_var.set("卡槽：" + (", ".join(outcome.after.tray) or "空"))
        if outcome.code == StepCode.RECOVERED and self.running and not self.stop_event.is_set():
            self.needs_manual_sync = False
        if self.controller:
            self.stats_var.set(f"确认点击 {self.controller.confirmed_clicks} · "
                               f"恢复 {self.controller.recovery_count} · "
                               f"全量识别 {self.controller.full_syncs} · "
                               f"反馈失败 {self.feedback_failures}")
        if not outcome.success and outcome.code not in (
                StepCode.RECOVERED, StepCode.PRECLICK_CHANGED, StepCode.TARGET_CHANGED,
                StepCode.ANIMATION_TIMEOUT, StepCode.VISUAL_CONFLICT, StepCode.TRAY_CONFLICT):
            self.running = False

    def manual_click(self) -> None:
        selected = self.tile_list.curselection()
        if not selected:
            self._status("先识别并在列表中选择一张牌")
            return
        controller = self._new_controller()
        if controller:
            if self.observation is None:
                self._status("请先识别当前截图")
                return
            tile = next((item for item in self.observation.tiles if item.id == self.list_ids[selected[0]]), None)
            if tile is None:
                self._status("选中方块已失效，请重新识别")
                return
            expected = (tile.type_id or "unknown", round(tile.center[0]), round(tile.center[1]))
            self._start_step(controller, tile.id, expected)

    def auto_once(self) -> None:
        controller = self._new_controller()
        if controller:
            self._start_step(controller)

    def start_auto(self) -> None:
        controller = self._new_controller()
        if controller is None:
            return
        self.running = True
        self._status("基础自动操作已启动；Ctrl+Alt+S 可紧急停止")
        self._start_step(controller)

    def _start_step(self, controller: VisualController, manual_tile_id: str | None = None,
                    manual_expected: tuple[str, int, int] | None = None) -> None:
        self.step_active = True

        def worker() -> None:
            first = True
            consecutive_recoveries = 0
            while first or (self.running and not controller.stop_event.is_set()):
                outcome = controller.run_once(manual_tile_id if first else None,
                                              manual_expected if first else None)
                while True:
                    try:
                        self.step_results.put(outcome, timeout=.1)
                        break
                    except Full:
                        if controller.stop_event.is_set():
                            return
                first = False
                if outcome.success:
                    consecutive_recoveries = 0
                    continue
                if (self.running and consecutive_recoveries < 2 and outcome.code in
                        (StepCode.PRECLICK_CHANGED, StepCode.TARGET_CHANGED,
                         StepCode.ANIMATION_TIMEOUT, StepCode.VISUAL_CONFLICT,
                         StepCode.TRAY_CONFLICT)):
                    recovery = controller.recover()
                    consecutive_recoveries += 1
                    while not controller.stop_event.is_set():
                        try:
                            self.step_results.put(recovery, timeout=.1)
                            break
                        except Full:
                            continue
                    if recovery.code == StepCode.RECOVERED:
                        continue
                break

        self.step_thread = Thread(target=worker, daemon=True)
        self.step_thread.start()
        self.root.after(50, self._poll_step)

    def _poll_step(self) -> None:
        try:
            outcome = self.step_results.get_nowait()
        except Empty:
            if self.step_thread is not None and self.step_thread.is_alive():
                self.root.after(50, self._poll_step)
            else:
                self.step_active = False
            return
        self._apply_outcome(outcome)
        self.root.after(50, self._poll_step)

    def pause(self) -> None:
        self.running = False
        if self.controller:
            self.controller.stop_event.set()
        self.needs_manual_sync = True
        self._status("已暂停；重新开始会先重新截图和识别")

    def stop(self) -> None:
        self.running = False
        if self.controller:
            self.controller.stop_event.set()
        self.needs_manual_sync = True
        self._status("已停止")

    def emergency_stop(self) -> None:
        if self.controller:
            self.controller.stop_event.set()
        self.running = False
        self.hotkey_event.set()

    def _poll_hotkey(self) -> None:
        if self.hotkey_event.is_set():
            self.hotkey_event.clear()
            self._status("紧急停止已触发")
        self.root.after(100, self._poll_hotkey)

    def save_log(self) -> None:
        if not LOG_PATH.exists():
            self._status("当前没有操作日志")
            return
        destination = filedialog.asksaveasfilename(defaultextension=".jsonl",
                                                   filetypes=[("JSON Lines", "*.jsonl")])
        if destination:
            shutil.copy2(LOG_PATH, destination)
            self._status(f"日志已保存：{destination}")

    def _close(self) -> None:
        self.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        self.root.destroy()


def main() -> None:
    set_dpi_awareness()
    root = tk.Tk()
    app = SheepApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
