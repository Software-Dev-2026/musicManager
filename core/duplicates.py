import subprocess
from pathlib import Path
from typing import Callable

from .cache import load_json, save_json


FORMAT_RANK = {".flac": 4, ".wav": 3, ".m4a": 2, ".mp3": 2, ".ogg": 1, ".opus": 1}


def check_fpcalc() -> bool:
    try:
        subprocess.run(["fpcalc", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def fingerprint(path: Path) -> "tuple[float, list[int]] | None":
    try:
        result = subprocess.run(
            ["fpcalc", "-raw", "-length", "120", str(path)],
            capture_output=True, text=True, timeout=120,  # a corrupt file must not hang the scan
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0:
        return None
    duration, fp = None, None
    for line in result.stdout.splitlines():
        if line.startswith("DURATION="):
            duration = float(line[9:])
        elif line.startswith("FINGERPRINT="):
            fp = list(map(int, line[12:].split(",")))
    if duration is None or not fp:
        return None
    return duration, fp


def similarity(fp1: list, fp2: list) -> float:
    length = min(len(fp1), len(fp2))
    if length == 0:
        return 0.0
    diff = sum(bin((a ^ b) & 0xFFFFFFFF).count("1") for a, b in zip(fp1[:length], fp2[:length]))
    return 1.0 - diff / (length * 32)



def collect_files(root: Path, skip_dirs: set, extensions: set) -> list[Path]:
    files = []
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            files.append(item)
    return sorted(files)


def find_duplicates(
    music_dir: Path,
    skip_dirs: set,
    extensions: set,
    cache_path: Path,
    threshold: float = 0.9,
    no_cache: bool = False,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> list[list[Path]]:
    files = collect_files(music_dir, skip_dirs, extensions)
    cache = {} if no_cache else load_json(cache_path)
    if progress_cb and cache:
        progress_cb(0, len(files), f"Loaded {len(cache)} cached fingerprints")
    entries: list[tuple[Path, float, list]] = []

    for i, path in enumerate(files):
        if cancel_fn and cancel_fn():
            save_json(cache_path, cache)
            return []
        if progress_cb:
            progress_cb(i, len(files), f"Fingerprinting: {path.name[:50]}")
        key = str(path)
        mtime = path.stat().st_mtime
        hit = cache.get(key)
        if hit and hit.get("mtime") == mtime:
            entries.append((path, hit["duration"], hit["fingerprint"]))
        else:
            result = fingerprint(path)
            if result is None:
                continue
            duration, fp = result
            cache[key] = {"mtime": mtime, "duration": duration, "fingerprint": fp}
            entries.append((path, duration, fp))

    # Drop entries for files that no longer exist so the cache doesn't grow forever.
    # Files outside this scan (other music dirs, skipped folders) are kept if present.
    for key in [k for k in cache if not Path(k).exists()]:
        del cache[key]
    save_json(cache_path, cache)

    if cancel_fn and cancel_fn():
        return []

    groups: list[list[Path]] = []
    used: set[int] = set()
    total = len(entries)

    for i, (path_a, dur_a, fp_a) in enumerate(entries):
        if cancel_fn and cancel_fn():
            return []
        if progress_cb:
            progress_cb(i, total, f"Comparing: {path_a.name[:50]}")
        if i in used:
            continue
        group = [path_a]
        for j in range(i + 1, total):
            if j in used:
                continue
            path_b, dur_b, fp_b = entries[j]
            if abs(dur_a - dur_b) > 10:
                continue
            if similarity(fp_a, fp_b) >= threshold:
                group.append(path_b)
                used.add(j)
        if len(group) > 1:
            used.add(i)
            groups.append(group)

    return groups


def quality(path: Path) -> tuple:
    fmt = FORMAT_RANK.get(path.suffix.lower(), 0)
    size = path.stat().st_size if path.exists() else 0
    return (fmt, size)


def _priority_level(path: Path, music_dir: Path, priority_dirs: list[str]) -> int | None:
    """Return the index into priority_dirs of the first matching component, or None if canonical.

    Checks every component of the path under music_dir, not just the top level,
    so the result is independent of library folder structure.
    Index 0 = most expendable (deleted first); None = canonical (kept over any priority file).
    """
    try:
        parts = set(path.relative_to(music_dir).parts)
    except ValueError:
        return None
    for i, d in enumerate(priority_dirs):
        if d in parts:
            return i
    return None


def _sort_key(path: Path, music_dir: Path, priority_dirs: list[str]) -> tuple:
    """Comparable key: higher = better (keep over delete).

    priority_dirs index 0 = most expendable → lowest tier.
    Files matching no priority dir → highest tier (canonical).
    Tiebreak: format quality rank, then file size.
    """
    level = _priority_level(path, music_dir, priority_dirs)
    tier = (level + 1) if level is not None else (len(priority_dirs) + 1)
    fmt, size = quality(path)
    return (tier, fmt, size)


def rank_group(
    group: list[Path],
    music_dir: Path,
    priority_dirs: list[str] | None = None,
) -> list[tuple[Path, int]]:
    """Return [(path, rank), ...] sorted best-first.

    Rank 1 = file that would be kept.
    Rank N = most expendable (deleted first).
    """
    if not group:
        return []
    dirs = list(priority_dirs or [])
    ordered = sorted(group, key=lambda p: _sort_key(p, music_dir, dirs), reverse=True)
    return [(p, i + 1) for i, p in enumerate(ordered)]


def resolve_deletions(
    group: list[Path],
    music_dir: Path,
    priority_dirs: list[str] | None = None,
) -> tuple[list[Path], Path | None]:
    """Return (to_delete, keep).

    priority_dirs is an ordered list: index 0 = most expendable (deleted first).
    Files matching no priority dir are kept over any priority-dir file.
    Within the same tier, higher audio quality (format rank then file size) wins.
    """
    if not group:
        return [], None
    ranked = rank_group(group, music_dir, priority_dirs)
    keep = ranked[0][0]
    return [p for p, _ in ranked[1:]], keep


def delete_files(paths: list[Path]) -> list[str]:
    errors = []
    for p in paths:
        try:
            p.unlink()
        except OSError as e:
            errors.append(f"{p}: {e}")
    return errors
