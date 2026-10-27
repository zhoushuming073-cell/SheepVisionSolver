from pathlib import Path

from sheep_solver import runtime_paths


def test_source_paths_point_to_project(monkeypatch):
    monkeypatch.delattr(runtime_paths.sys, "frozen", raising=False)
    root = Path(__file__).resolve().parents[1]
    assert runtime_paths.resources_root() == root
    assert runtime_paths.writable_root() == root


def test_frozen_paths_keep_assets_and_data_separate(monkeypatch, tmp_path):
    bundle = tmp_path / "bundle"
    executable = tmp_path / "portable" / "SheepVisionSolver.exe"
    monkeypatch.setattr(runtime_paths.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runtime_paths.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(runtime_paths.sys, "executable", str(executable))
    assert runtime_paths.resources_root() == bundle
    assert runtime_paths.writable_root() == executable.parent
