import posixpath
import shutil
import socket
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


class FileStatus(Enum):
    NEW = "new"
    CHANGED = "changed"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass
class SyncEntry:
    rel_path: str
    status: FileStatus
    local_size: int = 0
    remote_size: int = 0
    is_dir: bool = False


@dataclass
class SyncDiff:
    entries: list = field(default_factory=list)

    def summary(self) -> dict:
        counts = {s: 0 for s in FileStatus}
        for e in self.entries:
            counts[e.status] += 1
        return counts

    @property
    def new(self): return [e for e in self.entries if e.status == FileStatus.NEW]
    @property
    def changed(self): return [e for e in self.entries if e.status == FileStatus.CHANGED]
    @property
    def deleted(self): return [e for e in self.entries if e.status == FileStatus.DELETED]
    @property
    def unchanged(self): return [e for e in self.entries if e.status == FileStatus.UNCHANGED]


# ── Folder Sync ───────────────────────────────────────────────────────────────

def _iter_local(root: Path, skip_dirs: set, extensions: set) -> dict[str, Path]:
    result = {}
    for item in root.rglob("*"):
        if skip_dirs & set(item.relative_to(root).parts):
            continue
        if item.is_file() and item.suffix.lower() in extensions:
            result[str(item.relative_to(root))] = item
    return result


def compute_folder_diff(
    src: Path, dst: Path,
    skip_dirs: set, extensions: set,
    progress_cb: Callable | None = None,
) -> SyncDiff:
    diff = SyncDiff()
    local_files = _iter_local(src, skip_dirs, extensions)
    remote_files: dict[str, Path] = {}
    if dst.exists():
        remote_files = _iter_local(dst, set(), extensions)

    all_keys = set(local_files) | set(remote_files)
    for i, rel in enumerate(sorted(all_keys)):
        if progress_cb:
            progress_cb(i, len(all_keys), rel)
        local = local_files.get(rel)
        remote = remote_files.get(rel)
        if local and not remote:
            diff.entries.append(SyncEntry(rel, FileStatus.NEW, local.stat().st_size))
        elif not local and remote:
            diff.entries.append(SyncEntry(rel, FileStatus.DELETED, 0, remote.stat().st_size))
        elif local and remote:
            ls, rs = local.stat(), remote.stat()
            if ls.st_size != rs.st_size or ls.st_mtime - rs.st_mtime > 2:
                diff.entries.append(SyncEntry(rel, FileStatus.CHANGED, ls.st_size, rs.st_size))
            else:
                diff.entries.append(SyncEntry(rel, FileStatus.UNCHANGED, ls.st_size, rs.st_size))
    return diff


def apply_folder_sync(
    diff: SyncDiff, src: Path, dst: Path,
    delete: bool,
    progress_cb: Callable | None = None,
) -> dict:
    stats = {"transferred": 0, "deleted": 0, "errors": []}
    to_act = [e for e in diff.entries
              if e.status in (FileStatus.NEW, FileStatus.CHANGED)
              or (delete and e.status == FileStatus.DELETED)]
    for i, entry in enumerate(to_act):
        if progress_cb:
            progress_cb(i, len(to_act), entry.rel_path)
        if entry.status in (FileStatus.NEW, FileStatus.CHANGED):
            src_path = src / entry.rel_path
            dst_path = dst / entry.rel_path
            try:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_path), str(dst_path))
                stats["transferred"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")
        elif delete and entry.status == FileStatus.DELETED:
            dst_path = dst / entry.rel_path
            try:
                dst_path.unlink(missing_ok=True)
                stats["deleted"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")
    return stats


def apply_folder_pull(
    diff: SyncDiff, local: Path, remote: Path,
    delete: bool,
    progress_cb: Callable | None = None,
) -> dict:
    stats = {"transferred": 0, "deleted": 0, "errors": []}
    to_act = [e for e in diff.entries
              if e.status in (FileStatus.CHANGED, FileStatus.DELETED)
              or (delete and e.status == FileStatus.NEW)]
    for i, entry in enumerate(to_act):
        if progress_cb:
            progress_cb(i, len(to_act), entry.rel_path)
        if entry.status in (FileStatus.CHANGED, FileStatus.DELETED):
            src_path = remote / entry.rel_path
            dst_path = local / entry.rel_path
            try:
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_path), str(dst_path))
                stats["transferred"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")
        elif delete and entry.status == FileStatus.NEW:
            try:
                (local / entry.rel_path).unlink(missing_ok=True)
                stats["deleted"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")
    return stats


# ── SMB Sync ──────────────────────────────────────────────────────────────────

class SMBSyncer:
    def __init__(self, host: str, share: str, user: str, password: str):
        self.host = host
        self.share = share
        self.user = user
        self.password = password
        self._conn = None

    def connect(self) -> bool:
        try:
            from smb.SMBConnection import SMBConnection
        except ImportError:
            raise RuntimeError("pysmb not installed — pip install pysmb")
        remote_name = self.host.split(".")[0]
        for port, direct in ((445, True), (139, False)):
            conn = SMBConnection(
                self.user, self.password,
                socket.gethostname(), remote_name,
                use_ntlm_v2=True, is_direct_tcp=direct,
            )
            if conn.connect(self.host, port):
                self._conn = conn
                return True
        return False

    def disconnect(self):
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _list_remote(self, path: str) -> dict:
        try:
            from smb.smb_structs import OperationFailure
            entries = self._conn.listPath(self.share, path)
            return {e.filename: e for e in entries if e.filename not in (".", "..")}
        except Exception:
            return {}

    def _walk_remote(self, path: str) -> dict[str, object]:
        result = {}
        entries = self._list_remote(path)
        for name, entry in entries.items():
            rpath = posixpath.join(path, name)
            if entry.isDirectory:
                result.update(self._walk_remote(rpath))
            else:
                result[rpath] = entry
        return result

    def compute_diff(
        self, local_root: Path, skip_dirs: set, extensions: set,
        remote_root: str = "/",
        progress_cb: Callable | None = None,
    ) -> SyncDiff:
        diff = SyncDiff()
        if not self._conn:
            return diff

        local_files = _iter_local(local_root, skip_dirs, extensions)
        remote_flat = self._walk_remote(remote_root)

        # Normalise remote paths to relative
        def _rel(rpath: str) -> str:
            rpath = rpath.lstrip("/")
            if remote_root.strip("/"):
                prefix = remote_root.strip("/") + "/"
                if rpath.startswith(prefix):
                    rpath = rpath[len(prefix):]
            return rpath

        remote_rel: dict[str, object] = {_rel(k): v for k, v in remote_flat.items()}
        all_keys = set(local_files) | set(remote_rel)

        for i, rel in enumerate(sorted(all_keys)):
            if progress_cb:
                progress_cb(i, len(all_keys), rel)
            local = local_files.get(rel)
            remote = remote_rel.get(rel)
            if local and not remote:
                diff.entries.append(SyncEntry(rel, FileStatus.NEW, local.stat().st_size))
            elif not local and remote:
                diff.entries.append(SyncEntry(rel, FileStatus.DELETED, 0, remote.file_size))
            elif local and remote:
                ls = local.stat()
                if ls.st_size != remote.file_size or ls.st_mtime - remote.last_write_time > 2:
                    diff.entries.append(SyncEntry(rel, FileStatus.CHANGED, ls.st_size, remote.file_size))
                else:
                    diff.entries.append(SyncEntry(rel, FileStatus.UNCHANGED, ls.st_size, remote.file_size))
        return diff

    def _ensure_remote_dirs(self, rel_path: str, remote_root: str):
        parts = rel_path.replace("\\", "/").split("/")
        current = remote_root
        for part in parts[:-1]:
            current = posixpath.join(current, part)
            try:
                self._conn.listPath(self.share, current)
            except Exception:
                try:
                    self._conn.createDirectory(self.share, current)
                except Exception:
                    pass

    def apply_sync(
        self, diff: SyncDiff, local_root: Path,
        remote_root: str = "/",
        delete: bool = False,
        progress_cb: Callable | None = None,
    ) -> dict:
        stats = {"transferred": 0, "deleted": 0, "errors": []}
        to_act = [e for e in diff.entries
                  if e.status in (FileStatus.NEW, FileStatus.CHANGED)
                  or (delete and e.status == FileStatus.DELETED)]

        for i, entry in enumerate(to_act):
            if progress_cb:
                progress_cb(i, len(to_act), entry.rel_path)
            rpath = posixpath.join(remote_root.rstrip("/"), entry.rel_path)
            if entry.status in (FileStatus.NEW, FileStatus.CHANGED):
                self._ensure_remote_dirs(entry.rel_path, remote_root)
                try:
                    with open(local_root / entry.rel_path, "rb") as fh:
                        self._conn.storeFile(self.share, rpath, fh)
                    stats["transferred"] += 1
                except Exception as e:
                    stats["errors"].append(f"{entry.rel_path}: {e}")
            elif delete and entry.status == FileStatus.DELETED:
                try:
                    self._conn.deleteFiles(self.share, rpath)
                    stats["deleted"] += 1
                except Exception as e:
                    stats["errors"].append(f"{entry.rel_path}: {e}")
        return stats

    def apply_pull(
        self, diff: SyncDiff, local_root: Path,
        remote_root: str = "/",
        delete: bool = False,
        progress_cb: Callable | None = None,
    ) -> dict:
        stats = {"transferred": 0, "deleted": 0, "errors": []}
        to_act = [e for e in diff.entries
                  if e.status in (FileStatus.CHANGED, FileStatus.DELETED)
                  or (delete and e.status == FileStatus.NEW)]

        for i, entry in enumerate(to_act):
            if progress_cb:
                progress_cb(i, len(to_act), entry.rel_path)
            rpath = posixpath.join(remote_root.rstrip("/"), entry.rel_path)
            if entry.status in (FileStatus.CHANGED, FileStatus.DELETED):
                local_path = local_root / entry.rel_path
                try:
                    local_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(local_path, "wb") as fh:
                        self._conn.retrieveFile(self.share, rpath, fh)
                    stats["transferred"] += 1
                except Exception as e:
                    stats["errors"].append(f"{entry.rel_path}: {e}")
            elif delete and entry.status == FileStatus.NEW:
                try:
                    (local_root / entry.rel_path).unlink(missing_ok=True)
                    stats["deleted"] += 1
                except Exception as e:
                    stats["errors"].append(f"{entry.rel_path}: {e}")
        return stats
