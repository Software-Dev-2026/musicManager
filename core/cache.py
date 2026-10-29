import json
from pathlib import Path

from .config import _CACHE_DIR
from .fsutil import atomic_write_text


# ── Generic JSON helpers ──────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save_json(path: Path, data: dict) -> None:
    atomic_write_text(path, json.dumps(data))


# ── Per-file metadata cache ───────────────────────────────────────────────────
# Maps str(path) -> {mtime, title, artist, missing} so LibraryTree construction
# skips re-reading tags for files that haven't changed since the last scan.
# Mutagen updates mtime when writing tags, so this invalidates correctly after
# batch normalize without any explicit cache busting.

METADATA_CACHE_PATH = _CACHE_DIR / "metadata_cache.json"
_metadata_cache: dict = {}
_metadata_loaded = False


def _load_meta_cache() -> dict:
    # A flag, not `if not _metadata_cache`: an empty cache would otherwise be re-read from disk on every call
    global _metadata_cache, _metadata_loaded
    if not _metadata_loaded:
        _metadata_cache = load_json(METADATA_CACHE_PATH)
        _metadata_loaded = True
    return _metadata_cache


def get_cached_meta(path: Path) -> "tuple[str, str, bool] | None":
    """Return (title, artist, missing) if a fresh cache entry exists, else None."""
    cache = _load_meta_cache()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    hit = cache.get(str(path))
    if hit and hit.get("mtime") == mtime:
        return hit["title"], hit["artist"], hit["missing"]
    return None


def set_cached_meta(path: Path, title: str, artist: str, missing: bool) -> None:
    """Store metadata for a file in the in-memory cache."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    _load_meta_cache()[str(path)] = {
        "mtime": mtime,
        "title": title,
        "artist": artist,
        "missing": missing,
    }


def flush_meta_cache() -> None:
    """Persist the in-memory metadata cache to disk."""
    try:
        save_json(METADATA_CACHE_PATH, _metadata_cache)
    except Exception:
        pass
