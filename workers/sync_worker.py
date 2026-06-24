from pathlib import Path
from .base_worker import BaseWorker
from ..core.sync import (
    SyncDiff, SMBSyncer,
    compute_folder_diff, apply_folder_sync, apply_folder_pull,
    FileStatus,
)


class DiffWorker(BaseWorker):
    """Compute diff only — does not write anything."""

    def __init__(self, mode: str, config, parent=None):
        super().__init__(parent)
        self.mode = mode
        self.config = config

    def run(self):
        def progress_cb(current, total, msg):
            self._progress(current, total, msg)

        try:
            if self.mode == "folder":
                src = self.config.music_dir
                dst = Path(self.config.folder_sync_dest)
                diff = compute_folder_diff(
                    src, dst,
                    set(self.config.skip_dirs),
                    self.config.audio_extensions,
                    progress_cb,
                )
                self.finished.emit({"diff": diff, "syncer": None})
            else:
                syncer = SMBSyncer(
                    self.config.smb_host, self.config.smb_share,
                    self.config.smb_user, self.config.smb_pass,
                )
                self._log(f"Connecting to {self.config.smb_host}…")
                if not syncer.connect():
                    self.error.emit(f"Cannot connect to {self.config.smb_host}")
                    return
                self._log("Connected. Computing diff…")
                diff = syncer.compute_diff(
                    self.config.music_dir,
                    set(self.config.skip_dirs),
                    self.config.audio_extensions,
                    "/",
                    progress_cb,
                )
                self.finished.emit({"diff": diff, "syncer": syncer})
        except Exception as e:
            self.error.emit(str(e))


class SyncApplyWorker(BaseWorker):
    """Apply a pre-computed diff."""

    def __init__(
        self, mode: str, diff: SyncDiff, config,
        syncer: SMBSyncer | None = None,
        delete: bool = False,
        direction: str = "push",
        parent=None,
    ):
        super().__init__(parent)
        self.mode = mode
        self.diff = diff
        self.config = config
        self.syncer = syncer
        self.delete = delete
        self.direction = direction

    def run(self):
        def progress_cb(current, total, msg):
            self._progress(current, total, msg)
            self._log(f"[sync] {msg}")

        try:
            local = self.config.music_dir
            if self.mode == "folder":
                remote = Path(self.config.folder_sync_dest)
                if self.direction == "pull":
                    stats = apply_folder_pull(self.diff, local, remote, self.delete, progress_cb)
                else:
                    stats = apply_folder_sync(self.diff, local, remote, self.delete, progress_cb)
            else:
                if not self.syncer:
                    self.error.emit("No SMB connection")
                    return
                if self.direction == "pull":
                    stats = self.syncer.apply_pull(
                        self.diff, local, "/", self.delete, progress_cb,
                    )
                else:
                    stats = self.syncer.apply_sync(
                        self.diff, local, "/", self.delete, progress_cb,
                    )
                self.syncer.disconnect()
            self.finished.emit(stats)
        except Exception as e:
            self.error.emit(str(e))
