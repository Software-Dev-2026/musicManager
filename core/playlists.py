import os
from pathlib import Path

from .fsutil import atomic_write_text

PLAYLIST_EXTENSIONS = {".m3u", ".m3u8"}


def _norm(path: "str | Path") -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def update_playlists(music_dir: Path, moves: dict, skip_dirs: set = frozenset()) -> tuple[int, list[str]]:
    """Point playlist entries at files' new locations after they were moved or replaced.

    moves maps old path → new path. Every .m3u/.m3u8 under music_dir is checked;
    each rewritten entry keeps its style (relative vs absolute, "\\" vs "/").
    Returns (playlists_changed, errors).
    """
    if not moves:
        return 0, []
    lookup = {_norm(old): Path(new) for old, new in moves.items()}
    changed_count, errors = 0, []
    for pl in music_dir.rglob("*"):
        if pl.suffix.lower() not in PLAYLIST_EXTENSIONS or not pl.is_file():
            continue
        if skip_dirs & set(pl.relative_to(music_dir).parts):
            continue
        try:
            # surrogateescape round-trips any bytes, so non-UTF-8 .m3u files aren't damaged
            with open(pl, encoding="utf-8", errors="surrogateescape", newline="") as fh:  # keep CRLF as-is
                text = fh.read()
            out, changed = [], False
            for line in text.splitlines(keepends=True):
                body = line.rstrip("\r\n")
                ending = line[len(body):]
                if body.strip() and not body.startswith("#"):
                    backslash = "\\" in body and "/" not in body
                    raw = body.replace("\\", "/")
                    target = raw if os.path.isabs(raw) else os.path.join(pl.parent, raw)
                    new = lookup.get(_norm(target))
                    if new is not None:
                        if os.path.isabs(raw):
                            body = str(new)
                        else:
                            try:
                                body = os.path.relpath(new, pl.parent)
                            except ValueError:  # different drive
                                body = str(new)
                        if backslash:
                            body = body.replace("/", "\\")
                        changed = True
                out.append(body + ending)
            if changed:
                atomic_write_text(pl, "".join(out), encoding="utf-8", errors="surrogateescape")
                changed_count += 1
        except (OSError, UnicodeError) as e:
            errors.append(f"{pl.name}: {e}")
    return changed_count, errors
