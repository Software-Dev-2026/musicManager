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
        ("320k", ["-b:a", "320k"]),
        ("256k", ["-b:a", "256k"]),
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


def build_ffmpeg_cmd(src: Path, dst: Path, target_fmt: str, quality_args: list) -> list:
    codec = AUDIO_CODECS.get(target_fmt, "copy")
    cmd = ["ffmpeg", "-i", str(src), "-c:a", codec]
    cmd += quality_args
    cmd += ["-map_metadata", "0", "-y", str(dst)]
    return cmd


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
) -> tuple[bool, str]:
    dst_dir = output_dir if output_dir else src.parent
    dst = (dst_dir / src.stem).with_suffix(f".{target_fmt}")
    if dst == src:
        dst = dst.with_stem(src.stem + "_reencoded")

    cmd = build_ffmpeg_cmd(src, dst, target_fmt, quality_args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False, result.stderr[-500:] if result.stderr else "ffmpeg error"

    if delete_original and dst.exists() and dst != src:
        try:
            src.unlink()
        except Exception as e:
            return True, f"Reencoded but could not delete original: {e}"
    return True, str(dst)
