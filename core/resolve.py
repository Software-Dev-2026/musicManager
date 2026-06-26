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
    """Every normalised identity string for a given stem / title / artist triple."""
    forms: set[str] = set()
    for s in (stem, title):
        if s:
            forms.add(_norm(s))
    if artist:
        forms.add(_norm(artist))
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


def resolve_missing(missing: list, music_dir: Path, skip: set, audio_exts: set,
                    progress_cb=None, cancel_fn=None) -> list:
    """Resolve missing playlist entries against the music library.

    Indexes every library file under all permutations of its stem, title tag,
    and artist tag so that a missing track can be found regardless of which
    piece of data has changed.  Queries are generated from the missing entry's
    path stem and its stored EXTINF title using the same permutation set.

    Returns [(entry_index, new_path), ...] for every entry that was matched.
    """
    index: dict[str, list] = {}
    meta_cache: dict[str, tuple] = {}   # str(path) → (title, artist)

    for f in music_dir.rglob("*"):
        if cancel_fn and cancel_fn():
            return []
        if f.suffix.lower() not in audio_exts:
            continue
        if skip & set(f.relative_to(music_dir).parts):
            continue
        title, artist, _, _ = read_quick_meta(f)
        meta_cache[str(f)] = (title, artist)
        for key in _all_forms(f.stem, title, artist):
            index.setdefault(key, []).append(f)

    index_keys = list(index.keys())
    resolvable = []
    total = len(missing)

    for idx, (i, entry) in enumerate(missing):
        if cancel_fn and cancel_fn():
            return []

        stem = Path(entry["path"]).stem
        extinf = entry.get("title", stem)

        # If the EXTINF title looks like "Artist - Title", extract both parts
        a_part, t_part = "", extinf
        if " - " in extinf:
            a_part, t_part = extinf.split(" - ", 1)

        queries = set(_all_forms(stem, t_part, a_part))

        # Exact lookup across all query forms
        cands: list = []
        for q in queries:
            cands.extend(index.get(q, []))

        # Fuzzy fallback when nothing matched exactly
        if not cands:
            for q in queries:
                for k in difflib.get_close_matches(q, index_keys, n=5, cutoff=0.75):
                    cands.extend(index[k])

        if cands:
            seen: set[str] = set()
            unique = [c for c in cands if str(c) not in seen and not seen.add(str(c))]
            resolvable.append((i, _best_candidate(entry["path"], extinf, unique, meta_cache)))

        if progress_cb:
            progress_cb(idx + 1, total)

    return resolvable
