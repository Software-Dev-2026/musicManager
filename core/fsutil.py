import os
import tempfile
from pathlib import Path


def atomic_write_text(
    path: Path, text: str, encoding: str = "utf-8", mode: int | None = None, errors: str = "strict"
) -> None:
    """Write text to path via a temp file + rename, so a crash never leaves a truncated file.

    The existing file's permissions are kept unless mode is given.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, errors=errors, newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        elif path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o7777)
        else:
            os.chmod(tmp, 0o666 & ~_umask())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current
