import time
from pathlib import Path
from .base_worker import BaseWorker
from ..core.duplicates import find_duplicates, check_fpcalc


class DupesWorker(BaseWorker):
    def __init__(
        self, music_dir: Path, skip_dirs: set, extensions: set,
        cache_path: Path, threshold: float, no_cache: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.music_dir = music_dir
        self.skip_dirs = skip_dirs
        self.extensions = extensions
        self.cache_path = cache_path
        self.threshold = threshold
        self.no_cache = no_cache
        self._start_time: float = 0.0

    def run(self):
        if not check_fpcalc():
            self.error.emit("fpcalc not found. Install with: sudo dnf install chromaprint-tools")
            return

        self._start_time = time.monotonic()

        def progress_cb(current: int, total: int, msg: str):
            elapsed = time.monotonic() - self._start_time
            if current > 0 and total > 0:
                rate = current / elapsed if elapsed > 0 else 0
                remaining = (total - current) / rate if rate > 0 else 0
                eta = f"ETA {_fmt_eta(remaining)}" if remaining > 0 else ""
                pct = int(current / total * 100)
                full_msg = f"[{pct}%] {msg}" + (f"\n{eta}" if eta else "")
            else:
                full_msg = msg
            self.progress.emit(current, total, full_msg)
            self.log.emit(msg)

        try:
            groups = find_duplicates(
                self.music_dir, self.skip_dirs, self.extensions,
                self.cache_path, self.threshold, self.no_cache, progress_cb,
                cancel_fn=lambda: self._cancelled,
            )
            if not self._cancelled:
                self.finished.emit(groups)
        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e))


def _fmt_eta(seconds: float) -> str:
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m:02d}m"
