import re
import shutil
from pathlib import Path
from typing import Any

import mutagen
import mutagen.apev2
from mutagen.id3 import ID3, ID3NoHeaderError

try:
    import music_tag
    HAS_MUSIC_TAG = True
except ImportError:
    HAS_MUSIC_TAG = False

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def sanitize_name(name: str) -> str:
    """Strip filesystem-illegal characters from a directory/file name component."""
    return _INVALID_CHARS.sub("_", name).strip(".").strip() or "Unknown"


def read_quick_meta(path: Path) -> tuple[str, str, int, bool]:
    """Return (title, artist, duration_seconds, has_missing) via a fast mutagen read."""
    try:
        f = mutagen.File(path, easy=True)
        if f is None:
            return path.stem, "", -1, True
        title = (f.get("title") or [None])[0]
        artist = (f.get("artist") or [None])[0]
        dur = int(f.info.length) if hasattr(f, "info") and hasattr(f.info, "length") else -1
        missing = not title or not artist
        return (title or path.stem), (artist or ""), dur, missing
    except Exception:
        return path.stem, "", -1, True


def compute_dest_dir(music_dir: Path, structure: str, artist: str, album: str) -> Path:
    """Return the destination directory for a track given its artist/album and library structure."""
    s_artist = sanitize_name(artist.strip() or "Unknown")
    if structure == "flat":
        return music_dir
    if structure == "artist":
        return music_dir / s_artist
    if album.strip():
        return music_dir / s_artist / sanitize_name(album.strip())
    return music_dir / s_artist


_TXXX_BLOCKLIST = {
    "ARTIST", "ARTISTS", "ARTIST NAME",
    "ALBUMARTIST", "ALBUM_ARTIST", "ALBUM ARTIST",
    "TITLE", "TRACK TITLE",
    "ALBUM", "ALBUM TITLE",
    "COMPILATION",
}

_VORBIS_RENAMES = {
    "ALBUM ARTIST": "ALBUMARTIST",
    "ALBUM_ARTIST": "ALBUMARTIST",
    "TRACK TITLE":  "TITLE",
    "ARTIST NAME":  "ARTIST",
    "ALBUM TITLE":  "ALBUM",
}

TAG_FIELDS = ["title", "artist", "albumartist", "album", "tracknumber", "discnumber",
              "year", "genre", "comment", "compilation"]


def _strip_embedded_id3(filepath: str):
    with open(filepath, "rb") as f:
        data = f.read()
    if data[:3] == b"ID3":
        primary_size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        search_from = 10 + primary_size
        if data[5] & 0x10:
            search_from += 10
    else:
        search_from = 0
    chunks = [data[:search_from]]
    pos = search_from
    found = False
    while pos < len(data):
        idx = data.find(b"ID3", pos)
        if idx == -1:
            chunks.append(data[pos:])
            break
        if idx + 10 <= len(data) and data[idx + 3] in (2, 3, 4):
            block_size = 10 + ((data[idx+6] << 21) | (data[idx+7] << 14) | (data[idx+8] << 7) | data[idx+9])
            chunks.append(data[pos:idx])
            found = True
            pos = idx + block_size
        else:
            pos = idx + 3
    if found:
        with open(filepath, "wb") as f:
            for chunk in chunks:
                f.write(chunk)


def clean_file_tags(filepath: str):
    ext = filepath.lower().rsplit(".", 1)[-1]
    if ext == "mp3":
        _strip_embedded_id3(filepath)
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            return
        tags.update_to_v23()
        for key in list(tags.keys()):
            if key.startswith("TXXX:") and key[5:].upper() in _TXXX_BLOCKLIST:
                del tags[key]
        tags.save(filepath, v2_version=3, v1=0)
        try:
            mutagen.apev2.delete(filepath)
        except Exception:
            pass
    elif ext in ("flac", "ogg", "opus"):
        try:
            f = mutagen.File(filepath)
        except Exception:
            return
        if f is None or f.tags is None:
            return
        dirty = False
        for old, new in _VORBIS_RENAMES.items():
            if old in f.tags:
                if new not in f.tags:
                    f.tags[new] = f.tags[old]
                del f.tags[old]
                dirty = True
        if dirty:
            f.save()


def read_tags(filepath: str) -> dict[str, Any]:
    if not HAS_MUSIC_TAG:
        return {}
    try:
        f = music_tag.load_file(filepath)
    except Exception:
        return {}
    result = {}
    for field in TAG_FIELDS:
        try:
            val = f[field]
            result[field] = str(val) if val is not None else ""
        except Exception:
            result[field] = ""
    # Also get raw info via mutagen for bitrate/duration
    try:
        mf = mutagen.File(filepath)
        if mf and hasattr(mf, "info"):
            result["_duration"] = getattr(mf.info, "length", 0)
            result["_bitrate"] = getattr(mf.info, "bitrate", 0)
            result["_sample_rate"] = getattr(mf.info, "sample_rate", 0)
    except Exception:
        pass
    result["_size"] = Path(filepath).stat().st_size
    return result


def write_tags(filepath: str, tags: dict[str, Any], clean: bool = False) -> bool:
    if not HAS_MUSIC_TAG:
        return False
    try:
        f = music_tag.load_file(filepath)
    except Exception:
        return False
    changed = False
    for field, value in tags.items():
        if field.startswith("_"):
            continue
        try:
            current = str(f[field]) if f[field] is not None else ""
            if current != str(value):
                if field == "compilation":
                    f[field] = bool(value)
                elif field in ("tracknumber", "discnumber", "year"):
                    f[field] = int(value) if str(value).strip() else 0
                else:
                    f[field] = str(value)
                changed = True
        except Exception:
            pass
    if changed:
        try:
            f.save()
        except Exception:
            return False
    if clean:
        clean_file_tags(filepath)
    return True


def normalize_file(filepath: str, artist: str, album: str | None) -> bool:
    if not HAS_MUSIC_TAG:
        return False
    try:
        f = music_tag.load_file(filepath)
    except NotImplementedError:
        return False
    if not f:
        return False
    changed = False
    if str(f["artist"]) != artist:
        f["artist"] = artist
        changed = True
    if album is None:
        if str(f["albumartist"]) != "":
            f["albumartist"] = ""
            changed = True
        if str(f["album"]) != "":
            f["album"] = ""
            changed = True
    else:
        if str(f["albumartist"]) != artist:
            f["albumartist"] = artist
            changed = True
        if str(f["album"]) != album:
            f["album"] = album
            changed = True
    if bool(f["compilation"]):
        f["compilation"] = 0
        changed = True
    if changed:
        f.save()
    clean_file_tags(filepath)
    return changed


def collect_audio_files(root: Path, skip_dirs: set, extensions: set) -> list[Path]:
    files = []
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            files.append(item)
    return sorted(files)


def batch_normalize(music_dir: Path, skip_dirs: set, extensions: set,
                    progress_cb=None, cancel_fn=None) -> tuple[int, int]:
    changed = skipped = 0
    artists = sorted(d for d in music_dir.iterdir() if d.is_dir() and d.name not in skip_dirs)
    total = len(artists)
    for i, artist_dir in enumerate(artists):
        if cancel_fn and cancel_fn():
            break
        if progress_cb:
            progress_cb(i, total, artist_dir.name)
        for f in collect_audio_files(artist_dir, skip_dirs, extensions):
            if cancel_fn and cancel_fn():
                break
            album: str | None = f.parent.name if f.parent != artist_dir else None
            if normalize_file(str(f), artist_dir.name, album):
                changed += 1
            else:
                skipped += 1
    return changed, skipped


def _prune_empty_dir(directory: Path, stop_at: Path):
    """Remove directory and its empty parents up to (but not including) stop_at."""
    path = directory.resolve()
    root = stop_at.resolve()
    while path != root and path.is_relative_to(root):
        try:
            path.rmdir()
        except OSError:
            break
        path = path.parent


def batch_organize(music_dir: Path, structure: str, skip_dirs: set, extensions: set,
                   progress_cb=None, cancel_fn=None) -> tuple[int, int, list]:
    """Move every audio file into the folder dictated by its artist/album tags.

    Does not edit tags. Files with no artist tag are moved to an Unknown folder.
    Returns (moved, skipped, errors).
    """
    files = collect_audio_files(music_dir, skip_dirs, extensions)
    moved = skipped = 0
    errors: list[str] = []
    for i, f in enumerate(files):
        if cancel_fn and cancel_fn():
            break
        if progress_cb:
            progress_cb(i, len(files), f.name)
        try:
            mf = mutagen.File(f, easy=True)
            if mf is None:
                skipped += 1
                continue
            artist = (mf.get("artist") or mf.get("albumartist") or [""])[0].strip()
            album = (mf.get("album") or [""])[0].strip()
            dest = compute_dest_dir(music_dir, structure, artist, album) / f.name
            if dest.resolve() == f.resolve():
                skipped += 1
                continue
            if dest.exists():
                errors.append(f"{f.name}: destination already exists, skipped")
                skipped += 1
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(f), str(dest))
            _prune_empty_dir(f.parent, music_dir)
            moved += 1
        except Exception as e:
            errors.append(f"{f.name}: {e}")
    return moved, skipped, errors
