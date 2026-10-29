import base64
import copy
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text

if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "musicmanager"
    _CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "musicmanager"
elif sys.platform == "darwin":
    CONFIG_DIR = Path.home() / "Library" / "Application Support" / "musicmanager"
    _CACHE_DIR = Path.home() / "Library" / "Caches" / "musicmanager"
else:
    CONFIG_DIR = Path.home() / ".config" / "musicmanager"
    _CACHE_DIR = Path.home() / ".cache" / "musicmanager"

CONFIG_PATH = CONFIG_DIR / "config.json"
# User data that must survive cache clearing (e.g. the deleted-songs log)
DATA_DIR = (
    Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "musicmanager"
    if sys.platform not in ("win32", "darwin")
    else CONFIG_DIR
)

_PW_KEY = b"MusicMgr2024XOR"

DEFAULTS: dict = {
    "music_dir": str(Path.home() / "Music"),
    "skip_dirs": [],
    "audio_extensions": [".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"],
    "fingerprint_cache": str(_CACHE_DIR / "fingerprints.json"),
    "fingerprint_threshold": 0.9,
    "dupes_priority_dirs": [],
    "dupes_priority_configured": False,
    "show_titles": True,
    "sync_mode": "folder",
    "server": {
        "host": "",
        "share": "",
        "user": "",
        "pass": "",
    },
    "folder_sync": {
        "destination": "",
    },
    "sync_delete": False,
    "download": {
        "staging_dir": str(_CACHE_DIR / "staging"),
        "format": "mp3",
        "embed_thumbnail": True,
        "add_metadata": True,
    },
    "reencode": {
        "target_format": "mp3",
        "quality_preset": "VBR V0 (best)",
        "delete_originals": False,
        "output_dir": "",
    },
    "setup_complete": False,
}


def encrypt_password(pw: str) -> str:
    if not pw:
        return ""
    data = pw.encode("utf-8")
    key = (_PW_KEY * (len(data) // len(_PW_KEY) + 1))[:len(data)]
    xored = bytes(a ^ b for a, b in zip(data, key))
    return "enc:" + base64.b64encode(xored).decode()


def decrypt_password(stored: str) -> str:
    if not stored:
        return ""
    if not stored.startswith("enc:"):
        return stored  # plain-text legacy fallback
    try:
        xored = base64.b64decode(stored[4:].encode())
        key = (_PW_KEY * (len(xored) // len(_PW_KEY) + 1))[:len(xored)]
        return bytes(a ^ b for a, b in zip(xored, key)).decode("utf-8")
    except Exception:
        return stored


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


_MISSING = object()


class Config:
    def __init__(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self.load_error: str | None = None
        self.load()

    def load(self):
        data: dict = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
                if not isinstance(data, dict):
                    raise ValueError("config root is not an object")
            except Exception as e:
                # Keep the unreadable file so the next save can't silently destroy the user's settings
                backup = CONFIG_PATH.with_name(f"config.json.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
                try:
                    CONFIG_PATH.replace(backup)
                    self.load_error = (f"The settings file was unreadable ({e}).\n"
                                       f"It was kept as {backup}; defaults are in use.")
                except OSError:
                    self.load_error = f"The settings file {CONFIG_PATH} is unreadable ({e}); defaults are in use."
                print(f"musicmanager: {self.load_error}", file=sys.stderr)
                data = {}
        self._data = _deep_merge(copy.deepcopy(DEFAULTS), data)

    def save(self):
        # Atomic, and owner-only: the file holds the (merely obfuscated) SMB password
        atomic_write_text(CONFIG_PATH, json.dumps(self._data, indent=2), mode=0o600)

    def reset(self):
        self._data = copy.deepcopy(DEFAULTS)

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        val: Any = self._data
        for p in parts:
            if not isinstance(val, dict):
                return default
            val = val.get(p, _MISSING)
            if val is _MISSING:
                return default
        return val

    def set(self, key: str, value: Any):
        parts = key.split(".")
        d = self._data
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = value

    @property
    def music_dir(self) -> Path:
        return Path(self.get("music_dir"))

    @music_dir.setter
    def music_dir(self, v: "str | Path"):
        self.set("music_dir", str(v))

    @property
    def skip_dirs(self) -> list:
        return self.get("skip_dirs")

    @property
    def audio_extensions(self) -> set:
        return set(self.get("audio_extensions"))

    @property
    def fingerprint_cache(self) -> Path:
        return Path(self.get("fingerprint_cache"))

    @property
    def fingerprint_threshold(self) -> float:
        return float(self.get("fingerprint_threshold"))

    @property
    def dupes_priority_dirs(self) -> list:
        return self.get("dupes_priority_dirs")

    @property
    def show_titles(self) -> bool:
        return bool(self.get("show_titles"))

    @property
    def sync_mode(self) -> str:
        return self.get("sync_mode")

    @property
    def smb_host(self) -> str:
        return self.get("server.host")

    @property
    def smb_share(self) -> str:
        return self.get("server.share")

    @property
    def smb_user(self) -> str:
        return self.get("server.user")

    @property
    def smb_pass(self) -> str:
        return decrypt_password(self.get("server.pass"))

    def set_smb_pass(self, pw: str):
        self.set("server.pass", encrypt_password(pw))

    @property
    def folder_sync_dest(self) -> str:
        return self.get("folder_sync.destination", "")
