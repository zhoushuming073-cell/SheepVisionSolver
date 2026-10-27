"""Verify Win32 input sequencing without touching the real desktop."""

import os
import sys

import pytest

from sheep_solver.control import mouse as mouse_module


class FakeFunction:
    def __init__(self, callback):
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.callback(*args)


class FakeUser32:
    def __init__(self, *, process_id: int, focus_succeeds: bool = True):
        self.process_id = process_id
        self.foreground = 99
        self.focus_succeeds = focus_succeeds
        self.sent: list[int] = []
        self.WindowFromPoint = FakeFunction(lambda point: 11)
        self.GetAncestor = FakeFunction(lambda handle, mode: 22)
        self.GetForegroundWindow = FakeFunction(lambda: self.foreground)
        self.SetForegroundWindow = FakeFunction(self._focus)
        self.GetWindowThreadProcessId = FakeFunction(self._process)
        self.SetCursorPos = FakeFunction(lambda x, y: True)
        self.SendInput = FakeFunction(self._send)

    def _focus(self, handle):
        if self.focus_succeeds:
            self.foreground = handle
        return self.focus_succeeds

    def _process(self, handle, pointer):
        pointer._obj.value = self.process_id
        return 1

    def _send(self, count, pointer, size):
        assert count == 1 and size == 40
        self.sent.append(pointer._obj.data.mi.dwFlags)
        return 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input backend")
def test_mouse_focuses_target_before_sendinput(monkeypatch):
    monkeypatch.setattr(mouse_module, "set_dpi_awareness", lambda: None)
    fake = FakeUser32(process_id=os.getpid() + 1)
    mouse_module.WindowsMouseBackend(user32=fake).click(150, 240)
    assert fake.foreground == 22
    assert fake.sent == [0x0002, 0x0004]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows input backend")
def test_mouse_never_clicks_control_window_or_unfocused_target(monkeypatch):
    monkeypatch.setattr(mouse_module, "set_dpi_awareness", lambda: None)
    covered = FakeUser32(process_id=os.getpid())
    with pytest.raises(RuntimeError, match="Control window covers"):
        mouse_module.WindowsMouseBackend(user32=covered).click(150, 240)
    assert covered.sent == []

    unfocused = FakeUser32(process_id=os.getpid() + 1, focus_succeeds=False)
    with pytest.raises(RuntimeError, match="Could not focus"):
        mouse_module.WindowsMouseBackend(user32=unfocused).click(150, 240)
    assert unfocused.sent == []
