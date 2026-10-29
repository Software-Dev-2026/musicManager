import re
import subprocess
import urllib.parse
from typing import Callable


AUDIO_FORMATS = ["mp3", "flac", "m4a", "ogg", "opus", "wav"]

_PROGRESS_RE = re.compile(
    r"\[download\]\s+([\d.]+)%\s+of\s+~?\s*([\d.]+\w+)\s+at\s+([\S]+)\s+ETA\s+([\S]+)"
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
    on_start: Callable[[subprocess.Popen], None] | None = None,
) -> int:
    """Run yt-dlp; on_start receives the process so the caller can terminate it to cancel."""
    cmd = build_command(url, output_dir, fmt, embed_thumbnail, add_metadata, output_template)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if on_start:
        on_start(process)
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


def decode_path(line: str) -> str:
    """Decode a URL-encoded playlist path ("%C3%A9" → "é", "file:///a%20b" → "/a b")."""
    if line.startswith("file://"):
        line = urllib.parse.urlparse(line).path
    decoded = urllib.parse.unquote(line, encoding="utf-8", errors="strict") if "%" in line else line
    return decoded


def decode_playlist(text: str) -> str:
    """URL-decode the path lines of a playlist; comment/#EXTINF lines are left as-is."""
    out = []
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        if body and not body.startswith("#"):
            try:
                body = decode_path(body)
            except UnicodeDecodeError:
                body = urllib.parse.unquote(body, encoding="latin-1")
        out.append(body + line[len(line.rstrip("\r\n")):])
    return "".join(out)
