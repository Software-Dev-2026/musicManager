import traceback
from pathlib import Path
from .base_worker import BaseWorker
from ..core import deleted_log
from ..core.sync import (
    FileStatus, SyncDiff, SyncError, SMBSyncer,
    compute_folder_diff, apply_folder_sync, apply_folder_pull,
)


def _error_text(e: Exception) -> str:
    return str(e) if isinstance(e, SyncError) else traceback.format_exc()


class DiffWorker(BaseWorker):
    """Compute diff only — does not write anything.

    Always ends with exactly one of finished(dict) or error(str);
    finished carries {"cancelled": True} when cancelled.
    """

    description = "sync diff"

    def __init__(self, mode: str, config, direction: str = "push", parent=None):
        super().__init__(parent)
        self.mode = mode
        self.config = config
        self.direction = direction

    def run(self):
        def progress_cb(current, total, msg):
            self.progress.emit(current, total, msg)

        cancel = lambda: self._cancelled
        syncer = None

        try:
            if self.mode == "folder":
                diff = compute_folder_diff(
                    self.config.music_dir,
                    Path(self.config.folder_sync_dest),
                    set(self.config.skip_dirs),
                    self.config.audio_extensions,
                    progress_cb,
                    cancel,
                    self.direction,
                )
            else:
                syncer = SMBSyncer(
                    self.config.smb_host, self.config.smb_share,
                    self.config.smb_user, self.config.smb_pass,
                )
                self.log.emit(f"Connecting to {self.config.smb_host}…")
                if not syncer.connect():
                    self.error.emit(f"Cannot connect to {self.config.smb_host}")
                    return
                self.log.emit("Connected. Computing diff…")
                diff = syncer.compute_diff(
                    self.config.music_dir,
                    set(self.config.skip_dirs),
                    self.config.audio_extensions,
                    "/",
                    progress_cb,
                    cancel,
                    self.direction,
                )
            if self._cancelled:
                if syncer:
                    syncer.disconnect()
                self.finished.emit({"cancelled": True})
                return
            self.finished.emit({"diff": diff, "syncer": syncer})
        except Exception as e:
            if syncer:
                syncer.disconnect()
            self.error.emit(_error_text(e))


class SyncApplyWorker(BaseWorker):
    description = "sync (transferring files)"

    """Apply a pre-computed diff. Always ends with finished(stats) or error(str)."""

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
            self.progress.emit(current, total, msg)
            self.log.emit(f"[sync] {msg}")

        cancel = lambda: self._cancelled

        try:
            if self.diff.direction != self.direction:
                raise SyncError("The diff was computed for the other sync direction — compute it again.")
            local = self.config.music_dir
            # A pull with delete removes local-only songs; capture them for the deleted-songs log
            doomed = {}
            if self.direction == "pull" and self.delete:
                doomed = deleted_log.snapshot(
                    local / e.rel_path for e in self.diff.entries if not e.is_dir and e.status == FileStatus.NEW
                )
            if self.mode == "folder":
                remote = Path(self.config.folder_sync_dest)
                if self.direction == "pull":
                    stats = apply_folder_pull(self.diff, local, remote, self.delete, progress_cb, cancel)
                else:
                    stats = apply_folder_sync(self.diff, local, remote, self.delete, progress_cb, cancel)
            else:
                if not self.syncer:
                    self.error.emit("No SMB connection")
                    return
                if self.direction == "pull":
                    stats = self.syncer.apply_pull(
                        self.diff, local, "/", self.delete, progress_cb, cancel,
                    )
                else:
                    stats = self.syncer.apply_sync(
                        self.diff, local, "/", self.delete, progress_cb, cancel,
                    )
            gone = {p: info for p, info in doomed.items() if not Path(p).exists()}
            if gone:
                try:
                    deleted_log.record_deleted(gone, "Deleted by Pull sync")
                except OSError as e:
                    stats["errors"].append(f"deleted-songs log not updated — {e}")
            self.finished.emit(stats)
        except Exception as e:
            self.error.emit(_error_text(e))
        finally:
            if self.syncer:
                self.syncer.disconnect()
