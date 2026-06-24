from pathlib import Path
from .base_worker import BaseWorker
from ..core.metadata import batch_normalize


class NormalizeWorker(BaseWorker):
    def __init__(self, music_dir: Path, skip_dirs: set, extensions: set, parent=None):
        super().__init__(parent)
        self.music_dir = music_dir
        self.skip_dirs = skip_dirs
        self.extensions = extensions

    def run(self):
        def progress_cb(current, total, name):
            if self._cancelled:
                return
            self._progress(current, total, name)
            self._log(f"Processing: {name}")

        try:
            changed, skipped = batch_normalize(
                self.music_dir, self.skip_dirs, self.extensions, progress_cb
            )
            self.finished.emit({"changed": changed, "skipped": skipped})
        except Exception as e:
            self.error.emit(str(e))
