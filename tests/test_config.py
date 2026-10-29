"""A damaged or edited config must never silently lose the user's settings (M23, L1)."""
import json
import stat

import pytest

from conftest import mod

C = mod("core.config")


@pytest.fixture
def cfg_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(C, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(C, "_CACHE_DIR", tmp_path / "cache")
    return tmp_path


def test_corrupt_config_is_kept_as_backup(cfg_paths):
    (cfg_paths / "config.json").write_text('{"music_dir": "/srv/music", "server": {"host": "nas"')
    cfg = C.Config()
    backups = list(cfg_paths.glob("config.json.corrupt-*"))
    assert len(backups) == 1 and "/srv/music" in backups[0].read_text()
    assert cfg.load_error


def test_save_is_atomic_and_owner_only(cfg_paths):
    cfg = C.Config()
    cfg.set("server.host", "nas2")
    cfg.save()
    path = cfg_paths / "config.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text())["server"]["host"] == "nas2"
    assert not list(cfg_paths.glob(".config.json.*"))


def test_defaults_are_never_mutated_and_reset_works(cfg_paths):
    cfg = C.Config()
    cfg.set("server.host", "x")
    cfg.get("skip_dirs").append("y")
    assert C.DEFAULTS["server"]["host"] == "" and C.DEFAULTS["skip_dirs"] == []
    cfg.reset()
    assert cfg.get("skip_dirs") == [] and cfg.get("server.host") == ""
