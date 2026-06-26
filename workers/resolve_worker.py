from pathlib import Path

from ..core.resolve import resolve_missing
from .base_worker import BaseWorker


class ResolveWorker(BaseWorker):
    def __init__(self, missing: list, music_dir: Path, skip: set, audio_exts: set, parent=None):
        super().__init__(parent)
        self._missing = missing
        self._music_dir = music_dir
        self._skip = skip
        self._audio_exts = audio_exts

    def run(self):
        result = resolve_missing(
            self._missing,
            self._music_dir,
            self._skip,
            self._audio_exts,
            progress_cb=lambda cur, tot: self.progress.emit(cur, tot, ""),
            cancel_fn=lambda: self._cancelled,
        )
        self.finished.emit(result)
