"""Log of songs that left their path: deleted, moved, renamed, or re-encoded.

Other music players (and their playlists / liked songs) only know a song by its
path, so any change of path is recorded, with the new location where known.

Two JSON files live in DATA_DIR:
  known_files.json   — the library as of the last full scan (baseline for "found missing")
  deleted_songs.json — the log shown to the user
"""
import json
import os
import threading
import time
from pathlib import Path

from .config import DATA_DIR
from .fsutil import atomic_write_text

LOG_PATH = DATA_DIR / "deleted_songs.json"
KNOWN_PATH = DATA_DIR / "known_files.json"

_lock = threading.Lock()  # library scans can run on several threads (library tab, pickers)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def load_log() -> list[dict]:
    with _lock:
        return _load(LOG_PATH, [])


def save_log(entries: list[dict]) -> None:
    with _lock:
        atomic_write_text(LOG_PATH, json.dumps(entries, indent=1))


def _append(entries: list[dict]) -> None:
    if entries:
        log = _load(LOG_PATH, [])
        log.extend(entries)
        atomic_write_text(LOG_PATH, json.dumps(log, indent=1))


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def snapshot(paths) -> dict[str, dict]:
    """Capture title/artist/album for files about to be deleted (call before deleting)."""
    from .metadata import read_quick_meta

    with _lock:
        known = _load(KNOWN_PATH, {}).get("files", {})
    out = {}
    for p in paths:
        p = Path(p)
        info = dict(known.get(str(p), {}))
        if p.exists():
            title, artist, _, _ = read_quick_meta(p)
            info.update(title=title, artist=artist)
        info.setdefault("title", p.stem)
        info.setdefault("artist", "")
        info.setdefault("album", "")
        out[str(p)] = info
    return out


def record_deleted(snap: dict[str, dict], how: str, replaced_by: dict | None = None) -> None:
    """Log in-app deletions. snap comes from snapshot(); only pass paths that were actually deleted."""
    replaced_by = replaced_by or {}
    entries = [
        {
            "path": path, "title": info.get("title", ""), "artist": info.get("artist", ""),
            "album": info.get("album", ""), "when": _now(), "how": how,
            "replaced_by": str(replaced_by[path]) if path in replaced_by else "",
        }
        for path, info in snap.items()
    ]
    with _lock:
        _append(entries)
        # Forget them in the baseline so the next scan doesn't report them as missing too
        known = _load(KNOWN_PATH, None)
        if known:
            for path in snap:
                known["files"].pop(path, None)
            atomic_write_text(KNOWN_PATH, json.dumps(known))


def record_moves(moves: dict, how: str) -> None:
    """Log in-app path changes (old → new) and carry the baseline entries over to the new paths."""
    if not moves:
        return
    from .metadata import read_quick_meta

    with _lock:
        known = _load(KNOWN_PATH, None)
        files = known["files"] if known else {}
        entries = []
        for old, new in moves.items():
            old, new = str(old), str(new)
            info = files.pop(old, None)
            if info is None:
                title, artist, _, _ = read_quick_meta(Path(new))
                info = {"title": title, "artist": artist, "album": ""}
            if known and Path(new).exists():
                files[new] = {**info, "size": Path(new).stat().st_size}
            entries.append({
                "path": old, "title": info.get("title", ""), "artist": info.get("artist", ""),
                "album": info.get("album", ""), "when": _now(), "how": how, "replaced_by": new,
            })
        _append(entries)
        if known:
            atomic_write_text(KNOWN_PATH, json.dumps(known))


def _norm(s: str) -> str:
    return " ".join(s.casefold().split())


def reconcile(music_dir: Path, skip_dirs: set, current: dict[str, dict]) -> int:
    """Compare a full library scan with the previous one and log songs that disappeared.

    current maps path → {"title", "artist", "album", "size"}. A vanished file is logged
    as moved when it reappears with a new extension in the same folder, with the same
    name and size elsewhere, or with the same title+artist; otherwise as missing.
    Returns how many songs were newly logged (missing or moved).
    """
    if not current:
        return 0  # empty scan: drive unmounted or wrong folder — never treat as mass deletion
    with _lock:
        prev = _load(KNOWN_PATH, None)
        baseline_ok = (
            prev is not None
            and prev.get("music_dir") == str(music_dir)
            and sorted(prev.get("skip_dirs", [])) == sorted(skip_dirs)
        )
        logged = []
        if baseline_ok:
            old = prev["files"]
            gone = [p for p in old if p not in current]
            if gone:
                new_paths = [p for p in current if p not in old]
                # Where did each vanished file go? Most specific match first.
                by_dir_stem = {(os.path.dirname(p), os.path.splitext(os.path.basename(p))[0]): p for p in new_paths}
                by_name_size = {(os.path.basename(p), current[p].get("size")): p for p in new_paths}
                by_tags = {
                    (_norm(current[p]["title"]), _norm(current[p]["artist"])): p
                    for p in new_paths if current[p].get("artist")
                }
                for p in gone:
                    info = old[p]
                    stem = os.path.splitext(os.path.basename(p))[0]
                    if (dest := by_dir_stem.get((os.path.dirname(p), stem))):
                        how = "Extension changed"
                    elif (dest := by_name_size.get((os.path.basename(p), info.get("size")))):
                        how = "Moved"
                    elif info.get("artist") and (dest := by_tags.get((_norm(info["title"]), _norm(info["artist"])))):
                        how = "Moved & renamed"
                    else:
                        dest, how = "", "Found missing"
                    logged.append({
                        "path": p, "title": info.get("title", ""), "artist": info.get("artist", ""),
                        "album": info.get("album", ""), "when": _now(), "how": how, "replaced_by": dest,
                    })
            _append(logged)
        atomic_write_text(KNOWN_PATH, json.dumps(
            {"music_dir": str(music_dir), "skip_dirs": sorted(skip_dirs), "files": current}
        ))
        return len(logged)
