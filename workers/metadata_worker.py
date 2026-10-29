from pathlib import Path
from .base_worker import BaseWorker
from ..core.metadata import batch_normalize, batch_organize


class NormalizeWorker(BaseWorker):
    description = "tag normalize (editing files)"

    def __init__(self, music_dir: Path, skip_dirs: set, extensions: set, parent=None):
        super().__init__(parent)
        self.music_dir = music_dir
        self.skip_dirs = skip_dirs
        self.extensions = extensions

    def run(self):
        def progress_cb(current, total, name):
            self.progress.emit(current, total, name)
            self.log.emit(f"Processing: {name}")

        try:
            changed, skipped = batch_normalize(
                self.music_dir, self.skip_dirs, self.extensions,
                progress_cb, cancel_fn=lambda: self._cancelled,
            )
            self.finished.emit({"changed": changed, "skipped": skipped})
        except Exception as e:
            self.error.emit(str(e))


class OrganizeWorker(BaseWorker):
    description = "organize (moving files)"

    def __init__(self, music_dir: Path, skip_dirs: set, extensions: set, parent=None):
        super().__init__(parent)
        self.music_dir = music_dir
        self.skip_dirs = skip_dirs
        self.extensions = extensions

    def run(self):
        def progress_cb(current, total, name):
            self.progress.emit(current, total, name)
            self.log.emit(f"Processing: {name}")

        try:
            moved, skipped, errors = batch_organize(
                self.music_dir, self.skip_dirs, self.extensions,
                progress_cb, cancel_fn=lambda: self._cancelled,
            )
            self.finished.emit({"moved": moved, "skipped": skipped, "errors": errors})
        except Exception as e:
            self.error.emit(str(e))
