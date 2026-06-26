import posixpath
import shutil
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable


_IO_BUFFER = 4 * 1024 * 1024   # 4 MB read/write buffer per transfer
_WORKERS   = 4                  # concurrent transfer threads


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
        """Counts by status across all entries (files and directories)."""
        counts = {s: 0 for s in FileStatus}
        for e in self.entries:
            counts[e.status] += 1
        return counts

    def file_summary(self) -> dict:
        """Counts by status for file entries only (directories excluded)."""
        counts = {s: 0 for s in FileStatus}
        for e in self.entries:
            if not e.is_dir:
                counts[e.status] += 1
        return counts

    def has_changes(self) -> bool:
        return any(e.status != FileStatus.UNCHANGED for e in self.entries)

    @property
    def new(self):
        return [e for e in self.entries if e.status == FileStatus.NEW]

    @property
    def changed(self):
        return [e for e in self.entries if e.status == FileStatus.CHANGED]

    @property
    def deleted(self):
        return [e for e in self.entries if e.status == FileStatus.DELETED]

    @property
    def unchanged(self):
        return [e for e in self.entries if e.status == FileStatus.UNCHANGED]


# ── Local filesystem helpers ──────────────────────────────────────────────────


def _scan_local(
    root: Path,
    skip_dirs: set,
    extensions: set,
) -> tuple[dict[str, Path], set[str]]:
    """Single rglob pass returning (files, dirs), keys as posix-relative paths."""
    files: dict[str, Path] = {}
    dirs: set[str] = set()
    for item in root.rglob("*"):
        rel = item.relative_to(root)
        if skip_dirs & set(rel.parts):
            continue
        key = rel.as_posix()
        if item.is_dir():
            dirs.add(key)
        elif item.is_file() and item.suffix.lower() in extensions:
            files[key] = item
    return files, dirs


def _ensure_case_correct_dir(path: Path) -> None:
    """Create directory, renaming any wrong-case ancestors first.

    On case-insensitive filesystems (FAT, exFAT, SMB mounts),
    mkdir(exist_ok=True) silently reuses a wrong-case directory.
    Walking each component and renaming before descending ensures the
    on-disk name matches the intended case.
    """
    parts = path.parts
    for depth in range(2, len(parts) + 1):
        segment = Path(*parts[:depth])
        parent = segment.parent
        if not parent.is_dir():
            break
        if segment.is_dir():
            continue
        for sibling in parent.iterdir():
            if sibling.is_dir() and sibling.name != segment.name and sibling.name.lower() == segment.name.lower():
                try:
                    sibling.rename(segment)
                except OSError:
                    pass
                break
    path.mkdir(parents=True, exist_ok=True)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy src → dst with a 4 MB buffer, writing through a temp file for atomicity."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".synctmp")
    try:
        with open(src, "rb", buffering=_IO_BUFFER) as f_in, \
             open(tmp, "wb", buffering=_IO_BUFFER) as f_out:
            shutil.copyfileobj(f_in, f_out, length=_IO_BUFFER)
        shutil.copystat(str(src), str(tmp))
        tmp.replace(dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# ── Folder sync ───────────────────────────────────────────────────────────────


def _order_for_apply(entries: list) -> list:
    """Reverse alphabetical so children are always processed before their parents."""
    return sorted(entries, key=lambda e: e.rel_path, reverse=True)


def compute_folder_diff(
    src: Path,
    dst: Path,
    skip_dirs: set,
    extensions: set,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> SyncDiff:
    diff = SyncDiff()
    local_files, local_dirs = _scan_local(src, skip_dirs, extensions)
    if dst.exists():
        remote_files, remote_dirs = _scan_local(dst, set(), extensions)
    else:
        remote_files, remote_dirs = {}, set()

    all_keys = set(local_files) | set(remote_files) | local_dirs | remote_dirs
    for i, rel in enumerate(all_keys):
        if cancel_fn and cancel_fn():
            return diff
        if progress_cb:
            progress_cb(i, len(all_keys), rel)
        if rel in local_dirs or rel in remote_dirs:
            in_l, in_r = rel in local_dirs, rel in remote_dirs
            if in_l and not in_r:
                diff.entries.append(SyncEntry(rel, FileStatus.NEW, is_dir=True))
            elif not in_l and in_r:
                diff.entries.append(SyncEntry(rel, FileStatus.DELETED, is_dir=True))
            else:
                diff.entries.append(SyncEntry(rel, FileStatus.UNCHANGED, is_dir=True))
        else:
            loc = local_files.get(rel)
            rem = remote_files.get(rel)
            if loc and not rem:
                diff.entries.append(SyncEntry(rel, FileStatus.NEW, loc.stat().st_size))
            elif not loc and rem:
                diff.entries.append(SyncEntry(rel, FileStatus.DELETED, 0, rem.stat().st_size))
            elif loc and rem:
                ls, rs = loc.stat(), rem.stat()
                changed = ls.st_size != rs.st_size or ls.st_mtime - rs.st_mtime > 2
                diff.entries.append(
                    SyncEntry(
                        rel,
                        FileStatus.CHANGED if changed else FileStatus.UNCHANGED,
                        ls.st_size,
                        rs.st_size,
                    )
                )
    return diff


def apply_folder_sync(
    diff: SyncDiff,
    src: Path,
    dst: Path,
    delete: bool,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> dict:
    """Push: copy NEW/CHANGED from src → dst in parallel; optionally delete DELETED."""
    stats = {"transferred": 0, "deleted": 0, "errors": []}
    to_act = [
        e for e in diff.entries
        if e.status in (FileStatus.NEW, FileStatus.CHANGED) or (delete and e.status == FileStatus.DELETED)
    ]
    to_act = _order_for_apply(to_act)

    new_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.NEW]
    del_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.DELETED]
    transfers = [e for e in to_act if not e.is_dir and e.status in (FileStatus.NEW, FileStatus.CHANGED)]
    deletions = [e for e in to_act if not e.is_dir and e.status == FileStatus.DELETED]

    # Dirs first — sequential, fast, order matters for nested paths
    for entry in new_dirs:
        if cancel_fn and cancel_fn():
            return stats
        try:
            _ensure_case_correct_dir(dst / entry.rel_path)
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    total = len(transfers) + len(deletions)
    done = 0

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        future_map = {}
        for entry in transfers:
            if cancel_fn and cancel_fn():
                break
            future_map[pool.submit(_copy_file, src / entry.rel_path, dst / entry.rel_path)] = entry
        for fut in as_completed(future_map):
            entry = future_map[fut]
            done += 1
            if progress_cb:
                progress_cb(done, total, entry.rel_path)
            try:
                fut.result()
                stats["transferred"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")

    for entry in deletions:
        if cancel_fn and cancel_fn():
            return stats
        done += 1
        if progress_cb:
            progress_cb(done, total, entry.rel_path)
        try:
            (dst / entry.rel_path).unlink(missing_ok=True)
            stats["deleted"] += 1
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    for entry in del_dirs:
        if cancel_fn and cancel_fn():
            return stats
        try:
            shutil.rmtree(dst / entry.rel_path)
        except OSError:
            pass

    return stats


def apply_folder_pull(
    diff: SyncDiff,
    local: Path,
    remote: Path,
    delete: bool,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> dict:
    """Pull: copy CHANGED/DELETED from remote → local in parallel; optionally delete NEW."""
    stats = {"transferred": 0, "deleted": 0, "errors": []}
    to_act = [
        e for e in diff.entries
        if e.status in (FileStatus.CHANGED, FileStatus.DELETED) or (delete and e.status == FileStatus.NEW)
    ]
    to_act = _order_for_apply(to_act)

    # DELETED dirs: exist on remote, not local → create locally
    # NEW dirs: local-only, delete if delete=True
    new_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.DELETED]
    del_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.NEW]
    transfers = [e for e in to_act if not e.is_dir and e.status in (FileStatus.CHANGED, FileStatus.DELETED)]
    deletions = [e for e in to_act if not e.is_dir and e.status == FileStatus.NEW]

    for entry in new_dirs:
        if cancel_fn and cancel_fn():
            return stats
        try:
            _ensure_case_correct_dir(local / entry.rel_path)
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    total = len(transfers) + len(deletions)
    done = 0

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        future_map = {}
        for entry in transfers:
            if cancel_fn and cancel_fn():
                break
            future_map[pool.submit(_copy_file, remote / entry.rel_path, local / entry.rel_path)] = entry
        for fut in as_completed(future_map):
            entry = future_map[fut]
            done += 1
            if progress_cb:
                progress_cb(done, total, entry.rel_path)
            try:
                fut.result()
                stats["transferred"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")

    for entry in deletions:
        if cancel_fn and cancel_fn():
            return stats
        done += 1
        if progress_cb:
            progress_cb(done, total, entry.rel_path)
        try:
            (local / entry.rel_path).unlink(missing_ok=True)
            stats["deleted"] += 1
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    for entry in del_dirs:
        if cancel_fn and cancel_fn():
            return stats
        try:
            shutil.rmtree(local / entry.rel_path)
        except OSError:
            pass

    return stats


# ── SMB sync ──────────────────────────────────────────────────────────────────


class SMBSyncer:
    def __init__(self, host: str, share: str, user: str, password: str):
        self.host = host
        self.share = share
        self.user = user
        self.password = password
        self._conn = None
        self._pool: list = []
        self._pool_lock = threading.Lock()

    def _make_conn(self):
        try:
            from smb.SMBConnection import SMBConnection
        except ImportError:
            raise RuntimeError("pysmb not installed — pip install pysmb")
        remote_name = self.host.split(".")[0]
        for port, direct in ((445, True), (139, False)):
            try:
                conn = SMBConnection(
                    self.user, self.password, socket.gethostname(), remote_name,
                    use_ntlm_v2=True, is_direct_tcp=direct,
                )
                if conn.connect(self.host, port):
                    return conn
            except Exception:
                pass
        return None

    def connect(self) -> bool:
        conn = self._make_conn()
        if conn:
            self._conn = conn
            return True
        return False

    def disconnect(self):
        conns = [self._conn] if self._conn else []
        self._conn = None
        with self._pool_lock:
            conns.extend(self._pool)
            self._pool.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass

    @contextmanager
    def _pooled_conn(self):
        with self._pool_lock:
            conn = self._pool.pop() if self._pool else None
        if conn is None:
            conn = self._make_conn()
        if conn is None:
            raise RuntimeError(f"Could not connect to {self.host}")
        try:
            yield conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
            raise
        else:
            with self._pool_lock:
                self._pool.append(conn)

    def _list_remote(self, path: str) -> dict:
        try:
            entries = self._conn.listPath(self.share, path)
            return {e.filename: e for e in entries if e.filename not in (".", "..")}
        except Exception:
            return {}

    def _walk_remote(self, path: str) -> tuple[dict[str, object], set[str]]:
        files: dict[str, object] = {}
        dirs: set[str] = set()
        for name, entry in self._list_remote(path).items():
            rpath = posixpath.join(path, name)
            if entry.isDirectory:
                dirs.add(rpath)
                sub_files, sub_dirs = self._walk_remote(rpath)
                files.update(sub_files)
                dirs.update(sub_dirs)
            else:
                files[rpath] = entry
        return files, dirs

    def _rel(self, rpath: str, remote_root: str) -> str:
        rpath = rpath.lstrip("/")
        prefix = remote_root.strip("/")
        if prefix and rpath.startswith(prefix + "/"):
            rpath = rpath[len(prefix) + 1:]
        return rpath

    def compute_diff(
        self,
        local_root: Path,
        skip_dirs: set,
        extensions: set,
        remote_root: str = "/",
        progress_cb: Callable | None = None,
        cancel_fn: Callable | None = None,
    ) -> SyncDiff:
        diff = SyncDiff()
        if not self._conn:
            return diff

        local_files, local_dirs = _scan_local(local_root, skip_dirs, extensions)
        remote_flat, remote_dirs_abs = self._walk_remote(remote_root)
        remote_files = {self._rel(k, remote_root): v for k, v in remote_flat.items()}
        remote_dirs = {self._rel(d, remote_root) for d in remote_dirs_abs}
        all_keys = set(local_files) | set(remote_files) | local_dirs | remote_dirs

        for i, rel in enumerate(all_keys):
            if cancel_fn and cancel_fn():
                return diff
            if progress_cb:
                progress_cb(i, len(all_keys), rel)
            if rel in local_dirs or rel in remote_dirs:
                in_l, in_r = rel in local_dirs, rel in remote_dirs
                if in_l and not in_r:
                    diff.entries.append(SyncEntry(rel, FileStatus.NEW, is_dir=True))
                elif not in_l and in_r:
                    diff.entries.append(SyncEntry(rel, FileStatus.DELETED, is_dir=True))
                else:
                    diff.entries.append(SyncEntry(rel, FileStatus.UNCHANGED, is_dir=True))
            else:
                loc = local_files.get(rel)
                rem = remote_files.get(rel)
                if loc and not rem:
                    diff.entries.append(SyncEntry(rel, FileStatus.NEW, loc.stat().st_size))
                elif not loc and rem:
                    diff.entries.append(SyncEntry(rel, FileStatus.DELETED, 0, rem.file_size))
                elif loc and rem:
                    ls = loc.stat()
                    changed = ls.st_size != rem.file_size or ls.st_mtime - rem.last_write_time > 2
                    diff.entries.append(
                        SyncEntry(
                            rel,
                            FileStatus.CHANGED if changed else FileStatus.UNCHANGED,
                            ls.st_size,
                            rem.file_size,
                        )
                    )
        return diff

    def _build_remote_path(self, components: list[str], remote_root: str, conn=None) -> None:
        """Create each directory component under remote_root, renaming wrong-case entries first."""
        conn = conn or self._conn
        current = remote_root
        for part in components:
            expected = posixpath.join(current, part)
            try:
                for e in conn.listPath(self.share, current):
                    if (
                        e.filename not in (".", "..")
                        and e.isDirectory
                        and e.filename != part
                        and e.filename.lower() == part.lower()
                    ):
                        try:
                            conn.rename(self.share, posixpath.join(current, e.filename), expected)
                        except Exception:
                            pass
                        break
            except Exception:
                pass
            try:
                conn.listPath(self.share, expected)
            except Exception:
                try:
                    conn.createDirectory(self.share, expected)
                except Exception:
                    pass
            current = expected

    def _ensure_remote_dir(self, rel_dir: str, remote_root: str, conn=None) -> None:
        parts = rel_dir.replace("\\", "/").split("/")
        self._build_remote_path(parts, remote_root, conn=conn)

    def _ensure_remote_file_dirs(self, rel_path: str, remote_root: str, conn=None) -> None:
        parts = rel_path.replace("\\", "/").split("/")[:-1]
        self._build_remote_path(parts, remote_root, conn=conn)

    def _remove_empty_remote_parents(self, rpath: str, remote_root: str) -> None:
        root = remote_root.rstrip("/")
        parent = posixpath.dirname(rpath)
        while parent and parent != root and parent != "/":
            try:
                contents = [e for e in self._conn.listPath(self.share, parent) if e.filename not in (".", "..")]
                if contents:
                    break
                self._conn.deleteDirectory(self.share, parent)
            except Exception:
                break
            parent = posixpath.dirname(parent)

    def apply_sync(
        self,
        diff: SyncDiff,
        local_root: Path,
        remote_root: str = "/",
        delete: bool = False,
        progress_cb: Callable | None = None,
        cancel_fn: Callable | None = None,
    ) -> dict:
        """Push: upload NEW/CHANGED in parallel; optionally delete DELETED from remote."""
        stats = {"transferred": 0, "deleted": 0, "errors": []}
        to_act = [
            e for e in diff.entries
            if e.status in (FileStatus.NEW, FileStatus.CHANGED) or (delete and e.status == FileStatus.DELETED)
        ]
        to_act = _order_for_apply(to_act)

        new_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.NEW]
        del_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.DELETED]
        transfers = [e for e in to_act if not e.is_dir and e.status in (FileStatus.NEW, FileStatus.CHANGED)]
        deletions = [e for e in to_act if not e.is_dir and e.status == FileStatus.DELETED]

        # Create explicit new dirs sequentially on main connection
        for entry in new_dirs:
            if cancel_fn and cancel_fn():
                return stats
            self._ensure_remote_dir(entry.rel_path, remote_root)

        total = len(transfers) + len(deletions)
        done = 0

        _conn_lock = threading.Lock()

        def _upload_via(conn, rpath, local_path):
            # Thread-unique temp name so parallel uploads of case-equivalent paths
            # (same name on a case-insensitive server) don't share a temp file.
            rtmp = rpath + f".{threading.get_ident()}.synctmp"
            uploaded = False
            try:
                with open(local_path, "rb", buffering=_IO_BUFFER) as fh:
                    conn.storeFile(self.share, rtmp, fh)
                uploaded = True
                try:
                    conn.deleteFiles(self.share, rpath)
                except Exception:
                    pass
                conn.rename(self.share, rtmp, rpath)
            except Exception:
                if uploaded:
                    try:
                        conn.deleteFiles(self.share, rtmp)
                    except Exception:
                        pass
                raise

        def _do_upload(entry):
            rpath = posixpath.join(remote_root.rstrip("/") or "/", entry.rel_path)
            local_path = local_root / entry.rel_path
            try:
                with self._pooled_conn() as conn:
                    self._ensure_remote_file_dirs(entry.rel_path, remote_root, conn=conn)
                    _upload_via(conn, rpath, local_path)
            except Exception:
                # Fallback: main connection serialised — avoids concurrent open of
                # case-equivalent paths on a case-insensitive server
                with _conn_lock:
                    self._ensure_remote_file_dirs(entry.rel_path, remote_root)
                    _upload_via(self._conn, rpath, local_path)

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            future_map = {}
            for entry in transfers:
                if cancel_fn and cancel_fn():
                    break
                future_map[pool.submit(_do_upload, entry)] = entry
            for fut in as_completed(future_map):
                entry = future_map[fut]
                done += 1
                if progress_cb:
                    progress_cb(done, total, entry.rel_path)
                try:
                    fut.result()
                    stats["transferred"] += 1
                except Exception as e:
                    import traceback
                    stats["errors"].append(f"{entry.rel_path}: {traceback.format_exc()}")

        for entry in deletions:
            if cancel_fn and cancel_fn():
                return stats
            done += 1
            if progress_cb:
                progress_cb(done, total, entry.rel_path)
            rpath = posixpath.join(remote_root.rstrip("/"), entry.rel_path)
            try:
                self._conn.deleteFiles(self.share, rpath)
                self._remove_empty_remote_parents(rpath, remote_root)
                stats["deleted"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")

        for entry in del_dirs:
            if cancel_fn and cancel_fn():
                return stats
            rpath = posixpath.join(remote_root.rstrip("/"), entry.rel_path)
            try:
                self._conn.deleteDirectory(self.share, rpath)
            except Exception:
                pass

        return stats

    def apply_pull(
        self,
        diff: SyncDiff,
        local_root: Path,
        remote_root: str = "/",
        delete: bool = False,
        progress_cb: Callable | None = None,
        cancel_fn: Callable | None = None,
    ) -> dict:
        """Pull: download CHANGED/DELETED in parallel; optionally delete NEW (local-only)."""
        stats = {"transferred": 0, "deleted": 0, "errors": []}
        to_act = [
            e for e in diff.entries
            if e.status in (FileStatus.CHANGED, FileStatus.DELETED) or (delete and e.status == FileStatus.NEW)
        ]
        to_act = _order_for_apply(to_act)

        new_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.DELETED]
        del_dirs  = [e for e in to_act if e.is_dir and e.status == FileStatus.NEW]
        transfers = [e for e in to_act if not e.is_dir and e.status in (FileStatus.CHANGED, FileStatus.DELETED)]
        deletions = [e for e in to_act if not e.is_dir and e.status == FileStatus.NEW]

        for entry in new_dirs:
            if cancel_fn and cancel_fn():
                return stats
            try:
                _ensure_case_correct_dir(local_root / entry.rel_path)
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")

        total = len(transfers) + len(deletions)
        done = 0

        def _do_download(entry):
            rpath = posixpath.join(remote_root.rstrip("/"), entry.rel_path)
            local_path = local_root / entry.rel_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = local_path.with_name(local_path.name + ".synctmp")
            try:
                with self._pooled_conn() as conn:
                    with open(tmp, "wb", buffering=_IO_BUFFER) as fh:
                        conn.retrieveFile(self.share, rpath, fh)
                tmp.replace(local_path)
            except Exception:
                tmp.unlink(missing_ok=True)
                raise

        with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
            future_map = {}
            for entry in transfers:
                if cancel_fn and cancel_fn():
                    break
                future_map[pool.submit(_do_download, entry)] = entry
            for fut in as_completed(future_map):
                entry = future_map[fut]
                done += 1
                if progress_cb:
                    progress_cb(done, total, entry.rel_path)
                try:
                    fut.result()
                    stats["transferred"] += 1
                except Exception as e:
                    stats["errors"].append(f"{entry.rel_path}: {e}")

        for entry in deletions:
            if cancel_fn and cancel_fn():
                return stats
            done += 1
            if progress_cb:
                progress_cb(done, total, entry.rel_path)
            try:
                (local_root / entry.rel_path).unlink(missing_ok=True)
                stats["deleted"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")

        for entry in del_dirs:
            if cancel_fn and cancel_fn():
                return stats
            try:
                shutil.rmtree(local_root / entry.rel_path)
            except OSError:
                pass

        return stats
