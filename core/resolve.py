import difflib
import re
import unicodedata
from pathlib import Path

from .metadata import read_quick_meta


def _norm(s: str) -> str:
    nfkd = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _all_forms(stem: str, title: str, artist: str) -> list[str]:
    """Every normalised identity string for a given stem / title / artist triple.

    The artist on its own is deliberately not a form: it would match every
    track by that artist.
    """
    forms: set[str] = set()
    for s in (stem, title):
        if s:
            forms.add(_norm(s))
    if artist:
        if title:
            forms.add(_norm(f"{artist} - {title}"))
            forms.add(_norm(f"{title} - {artist}"))
            forms.add(_norm(f"{artist} {title}"))
            forms.add(_norm(f"{title} {artist}"))
    return [f for f in forms if f]


def _best_candidate(orig_path: str, orig_title: str, candidates: list,
                    meta_cache: dict) -> str:
    if len(candidates) == 1:
        return str(candidates[0])

    orig_p = orig_path.lower().replace("\\", "/")
    orig_t = _norm(orig_title)
    orig_a = _norm(orig_title.split(" - ", 1)[0]) if " - " in orig_title else ""

    def score(c: Path) -> float:
        path_s = difflib.SequenceMatcher(None, orig_p, str(c).lower().replace("\\", "/")).ratio()
        lib_title, lib_artist = meta_cache.get(str(c), ("", ""))
        title_s = difflib.SequenceMatcher(None, orig_t, _norm(lib_title)).ratio()
        artist_s = (
            difflib.SequenceMatcher(None, orig_a, _norm(lib_artist)).ratio()
            if orig_a and lib_artist else 0.0
        )
        return path_s + title_s * 1.5 + artist_s

    return str(max(candidates, key=score))


def _moves_from_log() -> dict[str, str]:
    """old path → newest known location, from the deleted/moved songs log."""
    try:
        from . import deleted_log
        log = deleted_log.load_log()
    except Exception:
        return {}
    return {e["path"]: e["replaced_by"] for e in log if e.get("replaced_by")}


def _follow(moves: dict[str, str], path: str) -> str | None:
    """Follow a chain of recorded moves (A → B → C) to a file that exists."""
    seen = set()
    while path in moves and path not in seen:
        seen.add(path)
        path = moves[path]
    return path if seen and Path(path).is_file() else None


def resolve_missing(missing: list, music_dir: Path, skip: set, audio_exts: set,
                    progress_cb=None, cancel_fn=None) -> list:
    """Resolve missing playlist entries against the music library.

    Tried in order of confidence:
      1. "recorded move" — the deleted/moved songs log says where the file went
      2. "new extension" — same folder and name, different audio format (re-encode)
      3. "same filename" — the file exists elsewhere in the library
      4. "tags" / "similar name" — every library file indexed under permutations of
         its stem, title tag and artist tag, matched exactly or fuzzily

    Tags come from the library's metadata cache when fresh, so repeat runs are fast.
    progress_cb(current, total, message). Returns [(entry_index, new_path, reason), ...].
    """
    from .cache import flush_meta_cache, get_cached_meta, set_cached_meta

    files = []
    for f in music_dir.rglob("*"):
        if cancel_fn and cancel_fn():
            return []
        if f.suffix.lower() in audio_exts and not skip & set(f.relative_to(music_dir).parts) and f.is_file():
            files.append(f)

    index: dict[str, list] = {}
    meta_cache: dict[str, tuple] = {}   # str(path) → (title, artist)
    by_dir_stem: dict[tuple, Path] = {}
    by_name: dict[str, list] = {}
    for n, f in enumerate(files):
        if cancel_fn and cancel_fn():
            return []
        if progress_cb and n % 200 == 0:
            progress_cb(n, len(files), "Indexing library…")
        hit = get_cached_meta(f)
        if hit is None:
            title, artist, _, missing_tags = read_quick_meta(f)
            set_cached_meta(f, title, artist, missing_tags)
        else:
            title, artist, _ = hit
        meta_cache[str(f)] = (title, artist)
        by_dir_stem[(str(f.parent), f.stem)] = f
        by_name.setdefault(f.name.casefold(), []).append(f)
        for key in _all_forms(f.stem, title, artist):
            index.setdefault(key, []).append(f)
    flush_meta_cache()

    moves = _moves_from_log()
    index_keys = list(index.keys())
    resolvable = []
    total = len(missing)

    for idx, (i, entry) in enumerate(missing):
        if cancel_fn and cancel_fn():
            return []
        if progress_cb:
            progress_cb(idx + 1, total, "Matching…")

        old = Path(entry["path"])
        stem = old.stem
        extinf = entry.get("title", stem)

        # If the EXTINF title looks like "Artist - Title", extract both parts
        a_part, t_part = "", extinf
        if " - " in extinf:
            a_part, t_part = extinf.split(" - ", 1)
        want = _norm(a_part)

        def same_artist(c: Path) -> bool:
            """When the entry names an artist, reject candidates clearly by someone else
            (a bare title or filename like "Intro" matches many unrelated tracks)."""
            if not want:
                return True
            have = _norm(meta_cache.get(str(c), ("", ""))[1])
            if not have:
                return True  # artist unknown: keep the candidate
            # containment covers "Queen" vs "Queen feat. David Bowie"
            return want in have or have in want or difflib.SequenceMatcher(None, want, have).ratio() >= 0.9

        moved_to = _follow(moves, entry["path"])
        if moved_to:
            resolvable.append((i, moved_to, "recorded move"))
            continue
        same_place = by_dir_stem.get((str(old.parent), stem))
        if same_place is not None:
            resolvable.append((i, str(same_place), "new extension"))
            continue
        named = [c for c in by_name.get(old.name.casefold(), []) if same_artist(c)]
        if named:
            resolvable.append((i, _best_candidate(entry["path"], extinf, named, meta_cache), "same filename"))
            continue

        queries = set(_all_forms(stem, t_part, a_part))

        # Exact lookup across all query forms
        cands: list = []
        for q in queries:
            cands.extend(index.get(q, []))
        reason = "tags"

        # Fuzzy fallback when nothing matched exactly
        if not cands:
            reason = "similar name"
            for q in queries:
                for k in difflib.get_close_matches(q, index_keys, n=5, cutoff=0.75):
                    cands.extend(index[k])

        cands = [c for c in cands if same_artist(c)]

        if cands:
            seen: set[str] = set()
            unique = [c for c in cands if str(c) not in seen and not seen.add(str(c))]
            resolvable.append((i, _best_candidate(entry["path"], extinf, unique, meta_cache), reason))

    return resolvable
