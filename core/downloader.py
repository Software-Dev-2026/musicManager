import re
import subprocess
from typing import Callable


AUDIO_FORMATS = ["mp3", "flac", "m4a", "ogg", "opus", "wav"]

_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+([\d.]+\w+)\s+at\s+([\S]+)\s+ETA\s+([\S]+)"
)


def build_command(
    url: str,
    output_dir: str,
    fmt: str = "mp3",
    embed_thumbnail: bool = True,
    add_metadata: bool = True,
    output_template: str = "%(uploader)s/%(title)s.%(ext)s",
) -> list[str]:
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", fmt,
        "--no-abort-on-error",
        "-o", f"{output_dir}/{output_template}",
    ]
    if embed_thumbnail:
        cmd.append("--embed-thumbnail")
    if add_metadata:
        cmd.append("--add-metadata")
    cmd.append(url)
    return cmd


def download(
    url: str,
    output_dir: str,
    fmt: str = "mp3",
    embed_thumbnail: bool = True,
    add_metadata: bool = True,
    progress_cb: Callable | None = None,
    log_cb: Callable | None = None,
    output_template: str = "%(uploader)s/%(title)s.%(ext)s",
) -> int:
    cmd = build_command(url, output_dir, fmt, embed_thumbnail, add_metadata, output_template)
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    for line in process.stdout:
        line = line.rstrip()
        if log_cb:
            log_cb(line)
        m = _PROGRESS_RE.match(line)
        if m and progress_cb:
            progress_cb(float(m.group(1)))
    process.wait()
    return process.returncode


def check_ytdlp() -> bool:
    try:
        subprocess.run(["yt-dlp", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False


def decode_playlist(text: str) -> str:
    result = []
    i = 0
    while i < len(text):
        c = text[i]
        if c == "%" and i + 2 < len(text):
            try:
                result.append(bytes.fromhex(text[i+1:i+3]).decode("latin-1"))
                i += 3
                continue
            except Exception:
                pass
        result.append(c)
        i += 1
    return "".join(result)
