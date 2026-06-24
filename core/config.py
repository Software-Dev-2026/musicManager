import base64
import json
from pathlib import Path
from typing import Any

CONFIG_DIR = Path.home() / ".config" / "musicmanager"
CONFIG_PATH = CONFIG_DIR / "config.json"

_PW_KEY = b"MusicMgr2024XOR"

DEFAULTS: dict = {
    "music_dir": str(Path.home() / "Music"),
    "skip_dirs": ["editMetadata", "Metadata"],
    "audio_extensions": [".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"],
    "fingerprint_cache": str(Path.home() / ".cache" / "musicmanager" / "fingerprints.json"),
    "fingerprint_threshold": 0.9,
    "dupes_priority_dirs": ["likes", "osu"],
    "dupes_priority_configured": False,
    "library_structure": "artist/album",
    "show_titles": True,
    "sync_mode": "server",
    "server": {
        "host": "truenas-scale.taila98db7.ts.net",
        "share": "test",
        "user": "",
        "pass": "",
    },
    "folder_sync": {
        "destination": "",
    },
    "sync_delete": False,
    "download": {
        "staging_dir": str(Path.home() / ".cache" / "musicmanager" / "staging"),
        "format": "mp3",
        "embed_thumbnail": True,
        "add_metadata": True,
    },
    "reencode": {
        "target_format": "mp3",
        "quality_preset": "VBR V0",
        "delete_originals": False,
        "output_dir": "",
    },
    "format_rank": {".flac": 4, ".wav": 3, ".m4a": 2, ".mp3": 2, ".ogg": 1, ".opus": 1},
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


class Config:
    def __init__(self):
        self._data: dict = {}
        self.load()

    def load(self):
        data: dict = {}
        if CONFIG_PATH.exists():
            try:
                data = json.loads(CONFIG_PATH.read_text())
            except Exception:
                pass
        self._data = _deep_merge(DEFAULTS, data)

    def save(self):
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(self._data, indent=2))

    def get(self, key: str, default: Any = None) -> Any:
        parts = key.split(".")
        val: Any = self._data
        for p in parts:
            if not isinstance(val, dict):
                return default
            val = val.get(p)
            if val is None:
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
        return self.get("skip_dirs", [])

    @property
    def audio_extensions(self) -> set:
        return set(self.get("audio_extensions", []))

    @property
    def fingerprint_cache(self) -> Path:
        return Path(self.get("fingerprint_cache"))

    @property
    def fingerprint_threshold(self) -> float:
        return float(self.get("fingerprint_threshold", 0.9))

    @property
    def dupes_priority_dirs(self) -> list:
        return self.get("dupes_priority_dirs", ["likes", "osu"])

    @property
    def library_structure(self) -> str:
        return self.get("library_structure", "artist/album")

    @property
    def show_titles(self) -> bool:
        return bool(self.get("show_titles", True))

    @property
    def sync_mode(self) -> str:
        return self.get("sync_mode", "server")

    @property
    def smb_host(self) -> str:
        return self.get("server.host", "")

    @property
    def smb_share(self) -> str:
        return self.get("server.share", "")

    @property
    def smb_user(self) -> str:
        return self.get("server.user", "")

    @property
    def smb_pass(self) -> str:
        return decrypt_password(self.get("server.pass", ""))

    def set_smb_pass(self, pw: str):
        self.set("server.pass", encrypt_password(pw))

    @property
    def folder_sync_dest(self) -> str:
        return self.get("folder_sync.destination", "")
