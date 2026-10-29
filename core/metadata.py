import os
import re
import shutil
from pathlib import Path
from typing import Any

import mutagen
import mutagen.apev2
from mutagen.id3 import ID3, ID3NoHeaderError

from . import deleted_log
from .playlists import update_playlists

try:
    import music_tag

    HAS_MUSIC_TAG = True
except ImportError:
    HAS_MUSIC_TAG = False

_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize_name(name: str) -> str:
    """Strip filesystem-illegal characters from a directory/file name component.

    Leading/trailing dots and spaces are removed together ("Foo. " → "Foo"),
    since Windows, FAT and SMB silently drop them; reserved device names get a "_".
    """
    cleaned = _INVALID_CHARS.sub("_", name).strip(". ") or "Unknown"
    if cleaned.split(".")[0].upper() in _WINDOWS_RESERVED:
        cleaned += "_"
    return cleaned


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


def compute_dest_dir(music_dir: Path, artist: str, album: str) -> Path:
    """Return the artist/album destination directory for a track (artist only when there's no album)."""
    s_artist = sanitize_name(artist.strip() or "Unknown")
    if album.strip():
        return music_dir / s_artist / sanitize_name(album.strip())
    return music_dir / s_artist



_VORBIS_RENAMES = {
    "ALBUM ARTIST": "ALBUMARTIST",
    "ALBUM_ARTIST": "ALBUMARTIST",
    "TRACK TITLE": "TITLE",
    "ARTIST NAME": "ARTIST",
    "ALBUM TITLE": "ALBUM",
}

# TXXX frames written by yt-dlp / ffmpeg that carry no music metadata.
# Everything else (ReplayGain, MusicBrainz IDs, TRACKTOTAL, BPM, …) is kept.
_JUNK_TXXX = {
    "purl", "comment", "description", "synopsis", "major_brand", "minor_version",
    "compatible_brands", "software", "encoder", "handler_name", "null",
}

_NUMERIC_FIELDS = ("tracknumber", "discnumber", "year")

TAG_FIELDS = [
    "title",
    "artist",
    "albumartist",
    "album",
    "tracknumber",
    "discnumber",
    "year",
    "genre",
    "comment",
    "compilation",
]


_ID3_ALLOWED_FLAGS = {2: 0xC0, 3: 0xE0, 4: 0xF0}
_FRAME_ID = re.compile(rb"[A-Z0-9]{3,4}")


def _id3_block_size(data: bytes, idx: int, strict: bool = True) -> int | None:
    """Total size of a genuine ID3v2 tag starting at idx, or None if the bytes there aren't one.

    The bytes "ID3" turn up by chance inside compressed audio, so every header
    field is checked before the block is trusted. strict=False skips the body
    and trailing-data checks; used only to measure the primary tag's extent.
    """
    if data[idx : idx + 3] != b"ID3" or idx + 10 > len(data):
        return None
    version, revision, flags = data[idx + 3], data[idx + 4], data[idx + 5]
    if version not in _ID3_ALLOWED_FLAGS or revision != 0:
        return None
    if flags & ~_ID3_ALLOWED_FLAGS[version] & 0xFF:
        return None
    size_bytes = data[idx + 6 : idx + 10]
    if any(b >= 0x80 for b in size_bytes):
        return None
    size = (size_bytes[0] << 21) | (size_bytes[1] << 14) | (size_bytes[2] << 7) | size_bytes[3]
    total = 10 + size + (10 if version == 4 and flags & 0x10 else 0)
    end = idx + total
    if size == 0 or end > len(data):
        return None
    if not strict:
        return total
    # Body must open with a frame ID (or padding) unless an extended header precedes it
    id_len = 3 if version == 2 else 4
    body = data[idx + 10 : idx + 10 + id_len]
    if not (flags & 0x40) and body != b"\x00" * id_len and not _FRAME_ID.fullmatch(body):
        return None
    # Whatever follows must be EOF, another tag, or an MPEG frame sync
    tail = data[end : end + 8]
    if not (
        not tail
        or tail[:3] in (b"ID3", b"TAG")
        or tail == b"APETAGEX"
        or (len(tail) >= 2 and tail[0] == 0xFF and tail[1] & 0xE0 == 0xE0)
    ):
        return None
    return total


def _atomic_write(filepath: str, chunks: list[bytes]) -> None:
    """Write chunks to a temp file beside filepath, then swap it into place."""
    tmp = filepath + ".tagtmp"
    try:
        with open(tmp, "wb") as f:
            for chunk in chunks:
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
        shutil.copymode(filepath, tmp)
        os.replace(tmp, filepath)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _strip_embedded_id3(filepath: str) -> bool:
    """Remove stray ID3v2 tags that follow the primary tag (duplicate tags left by some taggers)."""
    with open(filepath, "rb") as f:
        data = f.read()
    keep_from = _id3_block_size(data, 0, strict=False) or 0  # never search inside the primary tag
    chunks = [data[:keep_from]]
    cursor = keep_from
    found = False
    while (idx := data.find(b"ID3", cursor)) != -1:
        block_size = _id3_block_size(data, idx)
        if block_size is None:
            cursor = idx + 1
            continue
        chunks.append(data[keep_from:idx])
        found = True
        keep_from = cursor = idx + block_size
    chunks.append(data[keep_from:])
    if found:
        _atomic_write(filepath, chunks)
    return found


def clean_file_tags(filepath: str) -> bool:
    ext = filepath.lower().rsplit(".", 1)[-1]
    if ext == "mp3":
        dirty = _strip_embedded_id3(filepath)
        try:
            tags = ID3(filepath)
        except ID3NoHeaderError:
            return dirty
        if tags.version[:2] != (2, 3):
            tags.update_to_v23()
            dirty = True
        for key in list(tags.keys()):
            if key.startswith("TXXX:") and key[5:].casefold() in _JUNK_TXXX:
                del tags[key]
                dirty = True
        try:
            mutagen.apev2.APEv2(filepath)
            mutagen.apev2.delete(filepath)
            dirty = True
        except mutagen.apev2.APENoHeaderError:
            pass
        except Exception:
            pass
        if dirty:
            tags.save(filepath, v2_version=3, v1=0)
        return dirty
    elif ext in ("flac", "ogg", "opus"):
        try:
            f = mutagen.File(filepath)
        except Exception:
            return False
        if f is None or f.tags is None:
            return False
        dirty = False
        for old, new in _VORBIS_RENAMES.items():
            if old in f.tags:
                if new not in f.tags:
                    f.tags[new] = f.tags[old]
                del f.tags[old]
                dirty = True
        if dirty:
            f.save()
        return dirty
    return False


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
        if mf is not None and hasattr(mf, "info"):  # not `if mf`: falsy when untagged
            result["_duration"] = getattr(mf.info, "length", 0)
            result["_bitrate"] = getattr(mf.info, "bitrate", 0)
            result["_sample_rate"] = getattr(mf.info, "sample_rate", 0)
    except Exception:
        pass
    try:
        result["_size"] = Path(filepath).stat().st_size
    except OSError:
        result["_size"] = 0
    return result


def write_tags(filepath: str, tags: dict[str, Any], clean: bool = False) -> bool:
    if not HAS_MUSIC_TAG:
        return False
    try:
        f = music_tag.load_file(filepath)
    except Exception:
        return False
    changed = False
    failed: list[str] = []
    for field, value in tags.items():
        if field.startswith("_"):
            continue
        try:
            current = str(f[field]) if f[field] is not None else ""
            if field in _NUMERIC_FIELDS:
                # The editor uses 0 for "empty"; an empty tag must stay empty, not become "0"
                new = int(value) if str(value).strip() else 0
                try:
                    cur = int(current) if current.strip() else 0
                except ValueError:
                    cur = None  # e.g. a full date in the year tag; keep it unless a value is entered
                if new == cur or (new == 0 and cur is None):
                    continue
                if new == 0:
                    f.remove_tag(field)
                else:
                    f[field] = new
            elif field == "compilation":
                new = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "yes")
                if new == bool(f[field].value if hasattr(f[field], "value") else current):
                    continue
                f[field] = new
            else:
                if current == str(value):
                    continue
                if str(value) == "":
                    f.remove_tag(field)
                else:
                    f[field] = str(value)
            changed = True
        except Exception:
            failed.append(field)
    if changed:
        try:
            f.save()
        except Exception:
            return False
    if clean:
        clean_file_tags(filepath)
    return not failed


def _matches_folder(tag_value: str, folder_name: str) -> bool:
    """True if a tag already corresponds to a folder name built from it by sanitize_name.

    Folder names are lossy ("AC/DC" → "AC_DC", "R.E.M." → "R.E.M"), so a tag
    that sanitizes to the folder name is correct and must not be overwritten.
    """
    return bool(tag_value.strip()) and sanitize_name(tag_value.strip()).casefold() == folder_name.casefold()


def normalize_file(filepath: str, artist: str, album: str | None) -> bool:
    """Make a file's tags agree with the folder it lives in.

    artist is the album-artist folder name; album is the album folder name, or
    None when the file isn't inside an album folder (its album tag is left alone).
    Tags that already sanitize to the folder name are kept verbatim, and a
    per-track artist is kept when the album artist already matches the folder
    (compilations, "feat." credits).
    """
    if not HAS_MUSIC_TAG:
        return False
    try:
        f = music_tag.load_file(filepath)
    except NotImplementedError:
        return False
    if f is None:
        return False
    changed = False
    cur_artist = str(f["artist"] or "")
    cur_albumartist = str(f["albumartist"] or "")
    if not _matches_folder(cur_artist, artist) and not _matches_folder(cur_albumartist, artist):
        f["artist"] = artist
        changed = True
    if album is not None:
        if not _matches_folder(cur_albumartist, artist):
            f["albumartist"] = artist
            changed = True
        if not _matches_folder(str(f["album"] or ""), album):
            f["album"] = album
            changed = True
    if not str(f["title"] or "").strip():
        f["title"] = Path(filepath).stem
        changed = True
    if changed:
        f.save()
    changed |= clean_file_tags(filepath)
    return changed


def collect_audio_files(root: Path, skip_dirs: set, extensions: set) -> list[Path]:
    files = []
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            files.append(item)
    return sorted(files)


def batch_normalize(
    music_dir: Path, skip_dirs: set, extensions: set, progress_cb=None, cancel_fn=None,
) -> tuple[int, int]:
    """Write folder names into tags: <artist>/<album>/…/track → artist + album.

    Deeper folders such as "CD1" are treated as part of the album above them;
    files directly in an artist folder keep their album tag.
    """
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
            rel_dirs = f.parent.relative_to(artist_dir).parts
            album = rel_dirs[0] if rel_dirs else None
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


def batch_organize(
    music_dir: Path, skip_dirs: set, extensions: set, progress_cb=None, cancel_fn=None
) -> tuple[int, int, list]:
    """Move every audio file into the folder dictated by its artist/album tags.

    Does not edit tags. Files with no artist tag are moved to an Unknown folder.
    Playlists under music_dir are updated to the new locations.
    Returns (moved, skipped, errors).
    """
    files = collect_audio_files(music_dir, skip_dirs, extensions)
    skipped = 0
    errors: list[str] = []
    moves: dict[Path, Path] = {}
    try:
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
                dest = compute_dest_dir(music_dir, artist, album) / f.name
                if dest.resolve() == f.resolve():
                    skipped += 1
                    continue
                if dest.exists():
                    errors.append(f"{f.name}: destination already exists, skipped")
                    skipped += 1
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(f), str(dest))
                moves[f] = dest
                _prune_empty_dir(f.parent, music_dir)
            except Exception as e:
                errors.append(f"{f.name}: {e}")
    finally:
        # Even after a cancel or an unexpected error, repoint playlists at whatever moved
        _, pl_errors = update_playlists(music_dir, moves, skip_dirs)
        errors.extend(f"playlist not updated — {e}" for e in pl_errors)
        try:
            deleted_log.record_moves(moves, "Moved by Organize")
        except OSError as e:
            errors.append(f"deleted-songs log not updated — {e}")
    return len(moves), skipped, errors
