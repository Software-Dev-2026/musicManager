"""Shared setup for the data-safety regression tests.

Isolation comes first: before the app is imported, HOME and the XDG folders are
pointed at a throwaway directory, so config, caches, the deleted-songs log and any
library scan can never touch the real user's files. The import is refused if that
isolation didn't take.
"""
import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_SANDBOX = Path(tempfile.mkdtemp(prefix="musicmanager-tests-"))
os.environ["HOME"] = str(_SANDBOX)
for var, sub in (("XDG_CONFIG_HOME", ".config"), ("XDG_CACHE_HOME", ".cache"), ("XDG_DATA_HOME", ".local/share")):
    os.environ[var] = str(_SANDBOX / sub)
os.environ["QT_QPA_PLATFORM"] = "offscreen"

_REPO = Path(__file__).resolve().parents[1]
PKG = _REPO.name  # imported by folder name, like main.py does
sys.path.insert(0, str(_REPO.parent))

import pytest  # noqa: E402

_config = importlib.import_module(f"{PKG}.core.config")
for _d in (_config.CONFIG_DIR, _config._CACHE_DIR, _config.DATA_DIR):
    if not Path(_d).resolve().is_relative_to(_SANDBOX.resolve()):
        raise RuntimeError(f"test isolation failed: {_d} is outside the sandbox — refusing to run")


def mod(name: str):
    """Import an app module, e.g. mod("core.sync")."""
    return importlib.import_module(f"{PKG}.{name}")


HAS_FFMPEG = shutil.which("ffmpeg") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


def make_audio(path: Path, secs: float = 1, art: bool = False, **tags) -> Path:
    """Generate a real (sine wave) audio file; format from the extension. Optional cover art and tags."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=f=440:d={secs}", str(path)], check=True)
    if art:
        jpg = path.with_suffix(".jpg")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64",
                        "-frames:v", "1", str(jpg)], check=True)
        data = jpg.read_bytes()
        jpg.unlink()
        if path.suffix == ".flac":
            from mutagen.flac import FLAC, Picture
            f = FLAC(path)
            pic = Picture()
            pic.type, pic.mime, pic.data = 3, "image/jpeg", data
            f.add_picture(pic)
            f.save()
        else:
            from mutagen.id3 import APIC, ID3
            t = ID3()
            t.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=data))
            t.save(path)
    if tags:
        import music_tag
        f = music_tag.load_file(str(path))
        for k, v in tags.items():
            f[k] = v
        f.save()
    return path


def audio_md5(path: Path) -> str:
    """MD5 of the decoded audio — identical means not a single sample changed."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a", "-f", "md5", "-"],
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="session")
def qapp():
    from PyQt6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fresh_logs():
    """Each test starts with an empty deleted-songs log and no library baseline."""
    dl = mod("core.deleted_log")
    for p in (dl.LOG_PATH, dl.KNOWN_PATH):
        Path(p).unlink(missing_ok=True)
    yield


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)
