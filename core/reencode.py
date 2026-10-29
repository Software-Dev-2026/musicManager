import os
import subprocess
from pathlib import Path
from typing import Callable

AUDIO_CODECS = {
    "mp3":  "libmp3lame",
    "flac": "flac",
    "m4a":  "aac",
    "ogg":  "libvorbis",
    "opus": "libopus",
    "wav":  "pcm_s16le",
}

# Broad set of audio extensions used when scanning for reencode sources.
# Wider than the library playback config so formats like .wma/.aiff/.ape
# are included when normalising a library.
REENCODE_SOURCE_EXTENSIONS = {
    ".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav",
    ".aiff", ".aif", ".wma", ".ape", ".mp2", ".mp4",
    ".mpc", ".wv", ".tta", ".ac3",
}

QUALITY_PRESETS: dict[str, list] = {
    "mp3": [
        ("VBR V0 (best)", ["-q:a", "0"]),
        ("CBR 320k",      ["-b:a", "320k"]),
        ("CBR 256k",      ["-b:a", "256k"]),
        ("CBR 192k",      ["-b:a", "192k"]),
    ],
    "flac": [
        ("Lossless", ["-compression_level", "8"]),
    ],
    "m4a": [
        ("320k AAC", ["-b:a", "320k"]),
        ("256k AAC", ["-b:a", "256k"]),
        ("192k AAC", ["-b:a", "192k"]),
    ],
    "ogg": [
        ("Quality 10 (best)", ["-q:a", "10"]),
        ("Quality 6",         ["-q:a", "6"]),
        ("Quality 3",         ["-q:a", "3"]),
    ],
    "opus": [
        # libopus caps at 256k per channel, so anything higher fails on mono sources
        ("256k (best)", ["-b:a", "256k"]),
        ("192k", ["-b:a", "192k"]),
        ("128k", ["-b:a", "128k"]),
    ],
    "wav": [
        ("16-bit PCM", ["-acodec", "pcm_s16le"]),
        ("24-bit PCM", ["-acodec", "pcm_s24le"]),
    ],
}


def check_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def detect_formats(root: Path, skip_dirs: set, extensions: set) -> dict:
    counts: dict[str, int] = {}
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            ext = item.suffix.lower()
            counts[ext] = counts.get(ext, 0) + 1
    return counts


# Explicit muxers so ffmpeg can write to a temp name whose extension isn't the target's
_MUXERS = {"mp3": "mp3", "flac": "flac", "m4a": "ipod", "ogg": "ogg", "opus": "opus", "wav": "wav"}
# Containers that can carry embedded cover art; others fail on a video stream
_COVER_ART_FORMATS = {"mp3", "flac", "m4a"}


def build_ffmpeg_cmd(src: Path, dst: Path, target_fmt: str, quality_args: list) -> list:
    codec = AUDIO_CODECS.get(target_fmt, "copy")
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(src), "-map", "0:a"]
    if target_fmt in _COVER_ART_FORMATS:
        cmd += ["-map", "0:v?", "-c:v", "copy", "-disposition:v", "attached_pic"]
    cmd += ["-c:a", codec]
    cmd += quality_args
    cmd += ["-map_metadata", "0"]
    if target_fmt in _MUXERS:
        cmd += ["-f", _MUXERS[target_fmt]]
    cmd += ["-y", str(dst)]
    return cmd


def _duration(path: Path, fmt: str | None = None) -> float | None:
    """Duration via mutagen; fmt forces the parser (needed for temp files with no audio extension)."""
    try:
        import mutagen
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from mutagen.mp4 import MP4
        from mutagen.oggopus import OggOpus
        from mutagen.oggvorbis import OggVorbis
        from mutagen.wave import WAVE
        parser = {"mp3": MP3, "flac": FLAC, "m4a": MP4, "ogg": OggVorbis, "opus": OggOpus, "wav": WAVE}.get(fmt)
        f = parser(path) if parser else mutagen.File(path)
        # Not `if f`: a FileType is falsy when it has no tags
        if f is None or getattr(f, "info", None) is None:
            return None
        return float(f.info.length) or None
    except Exception:
        return None


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def collect_reencoding_targets(
    root: Path, skip_dirs: set, extensions: set, target_fmt: str
) -> list:
    target_ext = f".{target_fmt}"
    files = []
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            if item.suffix.lower() != target_ext:
                files.append(item)
    return sorted(files)


def reencode_file(
    src: Path,
    output_dir: Path | None,
    target_fmt: str,
    quality_args: list,
    delete_original: bool = False,
    source_root: Path | None = None,
    cancel_fn: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """Encode src to target_fmt. Never overwrites an existing file.

    With output_dir, the file's folder structure below source_root is recreated
    there so same-named tracks from different albums can't collide.
    The original is only deleted after the output is verified.
    """
    if output_dir:
        try:
            dst_dir = output_dir / src.parent.relative_to(source_root) if source_root else output_dir
        except ValueError:
            dst_dir = output_dir
    else:
        dst_dir = src.parent
    dst = dst_dir / f"{src.stem}.{target_fmt}"
    if dst == src or _same_file(dst, src):
        dst = dst_dir / f"{src.stem}_reencoded.{target_fmt}"
    if dst.exists():
        return False, f"Skipped — {dst} already exists"

    dst_dir.mkdir(parents=True, exist_ok=True)
    tmp = dst_dir / f".{dst.name}.reencodetmp"
    cmd = build_ffmpeg_cmd(src, tmp, target_fmt, quality_args)
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        while True:
            try:
                _, stderr = proc.communicate(timeout=0.5)
                break
            except subprocess.TimeoutExpired:
                if cancel_fn and cancel_fn():
                    proc.kill()
                    proc.communicate()
                    tmp.unlink(missing_ok=True)
                    return False, "Cancelled"
        if proc.returncode != 0:
            tmp.unlink(missing_ok=True)
            return False, stderr[-500:] if stderr else "ffmpeg error"

        # Every target format is readable by mutagen, so an unreadable or empty output is a failure
        src_len, out_len = _duration(src), _duration(tmp, target_fmt)
        if not out_len or (src_len and abs(src_len - out_len) > max(1.0, src_len * 0.01)):
            tmp.unlink(missing_ok=True)
            return False, f"Output failed verification (duration {out_len}s vs source {src_len}s); original kept"
        if dst.exists():
            tmp.unlink(missing_ok=True)
            return False, f"Skipped — {dst} appeared during encoding"
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    if delete_original and dst.exists() and not _same_file(dst, src):
        try:
            src.unlink()
        except Exception as e:
            return True, f"Reencoded but could not delete original: {e}"
    return True, str(dst)
