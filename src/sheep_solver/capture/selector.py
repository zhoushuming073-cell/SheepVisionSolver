"""Tk screen overlay for manual game-region selection."""

from __future__ import annotations

import ctypes
import tkinter as tk
from collections.abc import Callable

from PIL import ImageTk

from .screen import Region, capture_screen, virtual_screen


def select_region(root: tk.Misc, callback: Callable[[Region | None], None]) -> None:
    bounds = virtual_screen()
    image = capture_screen()
    if image.size != (bounds.width, bounds.height):
        raise RuntimeError("Virtual desktop and screenshot sizes differ; check DPI settings")
    overlay = tk.Toplevel(root)
    overlay.overrideredirect(True)
    overlay.attributes("-topmost", True)
    overlay.geometry(f"{bounds.width}x{bounds.height}+0+0")
    canvas = tk.Canvas(overlay, width=bounds.width, height=bounds.height,
                       highlightthickness=0, cursor="crosshair")
    canvas.pack(fill="both", expand=True)
    photo = ImageTk.PhotoImage(image)
    canvas.image = photo  # keep reference
    canvas.create_image(0, 0, image=photo, anchor="nw")
    canvas.create_text(25, 25, text="拖动框选游戏区域，Esc 取消", anchor="nw",
                       fill="yellow", font=("Microsoft YaHei UI", 16, "bold"))
    overlay.update_idletasks()
    # Tk's negative geometry is relative to the right/bottom edge. Set physical
    # coordinates through Win32 for desktops with monitors left of the primary.
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    user32.SetWindowPos.restype = ctypes.c_bool
    if not user32.SetWindowPos(ctypes.c_void_p(overlay.winfo_id()), ctypes.c_void_p(-1),
                               bounds.left, bounds.top, bounds.width, bounds.height, 0x0040):
        overlay.destroy()
        raise RuntimeError("Could not position selection overlay on virtual desktop")
    start: list[int] = []
    rectangle: list[int] = []

    def finish(selected: Region | None) -> None:
        overlay.destroy()
        callback(selected)

    def on_down(event: tk.Event) -> None:
        start[:] = [event.x, event.y]
        if rectangle:
            canvas.delete(rectangle.pop())
        rectangle.append(canvas.create_rectangle(event.x, event.y, event.x, event.y,
                                                  outline="yellow", width=3))

    def on_move(event: tk.Event) -> None:
        if start and rectangle:
            canvas.coords(rectangle[0], start[0], start[1], event.x, event.y)

    def on_up(event: tk.Event) -> None:
        if not start:
            return
        if abs(event.x - start[0]) < 80 or abs(event.y - start[1]) < 80:
            finish(None)
            return
        selected = Region.from_drag(bounds.left + start[0], bounds.top + start[1],
                                    bounds.left + event.x, bounds.top + event.y)
        finish(selected if selected.width >= 80 and selected.height >= 80 else None)

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_move)
    canvas.bind("<ButtonRelease-1>", on_up)
    overlay.bind("<Escape>", lambda _event: finish(None))
    overlay.focus_force()
