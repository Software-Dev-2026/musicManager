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


def write_tags(filepath: str, tags: dict[str, Any]) -> bool:
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
                    progress_cb=None) -> tuple[int, int]:
    changed = skipped = 0
    artists = sorted([d for d in music_dir.iterdir() if d.is_dir()
                      and d.name not in skip_dirs])
    total = len(artists)
    for i, artist_dir in enumerate(artists):
        if progress_cb:
            progress_cb(i, total, artist_dir.name)
        for album_dir in sorted([d for d in artist_dir.iterdir() if d.is_dir()]):
            for song in sorted([f for f in album_dir.iterdir() if f.is_file()]):
                if song.suffix.lower() not in extensions:
                    continue
                if normalize_file(str(song), artist_dir.name, album_dir.name):
                    changed += 1
                else:
                    skipped += 1
        for single in sorted([f for f in artist_dir.iterdir() if f.is_file()]):
            if single.suffix.lower() not in extensions:
                continue
            if normalize_file(str(single), artist_dir.name, None):
                changed += 1
            else:
                skipped += 1
    return changed, skipped
