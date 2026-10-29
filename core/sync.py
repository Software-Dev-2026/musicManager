import os
import posixpath
import socket
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

_IO_BUFFER = 4 * 1024 * 1024  # 4 MB read/write buffer per transfer
_WORKERS = 4  # concurrent transfer threads
_MTIME_SLACK = 2  # seconds; FAT/exFAT store mtimes at 2 s resolution


class SyncError(Exception):
    """A sync precondition failed; the message is shown to the user as-is."""


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
    local_mtime: float = 0.0
    remote_mtime: float = 0.0


@dataclass
class SyncDiff:
    entries: list = field(default_factory=list)
    direction: str = "push"

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

    def overwrites_newer(self) -> int:
        """CHANGED files whose destination copy is newer than the copy about to replace it."""
        n = 0
        for e in self.changed:
            if self.direction == "pull":
                n += e.local_mtime - e.remote_mtime > _MTIME_SLACK
            else:
                n += e.remote_mtime - e.local_mtime > _MTIME_SLACK
        return n

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


class _RemoteEntry:
    """Wraps an SMB SharedFile so it duck-types like a local Path for stat() calls."""

    def __init__(self, size: int, mtime: float):
        self.st_size = size
        self.st_mtime = mtime

    def stat(self):
        return self


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


def _check_roots(src: Path, dst: Path) -> None:
    """Refuse to sync a folder with itself or with a folder nested inside it."""
    s, d = src.resolve(), dst.resolve()
    if s == d or s.is_relative_to(d) or d.is_relative_to(s):
        raise SyncError(f"The music folder and the sync destination overlap:\n{s}\n{d}")


def _same_local_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _ensure_case_correct_dir(path: Path, root: Path) -> None:
    """Create directory under root, renaming any wrong-case ancestors first.

    On case-insensitive filesystems (FAT, exFAT, SMB mounts),
    mkdir(exist_ok=True) silently reuses a wrong-case directory.
    Walking each component below root and renaming before descending ensures
    the on-disk name matches the intended case. On case-sensitive filesystems a
    differently-cased sibling is a distinct directory and is left alone.
    """
    rel_parts = path.relative_to(root).parts
    for depth in range(1, len(rel_parts) + 1):
        segment = root.joinpath(*rel_parts[:depth])
        parent = segment.parent
        if not parent.is_dir():
            break
        if segment.is_dir():
            # Exists — but on a case-insensitive FS it may exist under another case
            try:
                actual = next(s.name for s in parent.iterdir() if s.name.lower() == segment.name.lower())
            except StopIteration:
                continue
            if actual == segment.name:
                continue
            sibling = parent / actual
            if not _same_local_file(sibling, segment):
                continue
            try:
                # Two-step rename: a direct case-only rename is a no-op on some filesystems
                tmp = parent / f"{actual}.{threading.get_ident()}.casetmp"
                sibling.rename(tmp)
                tmp.rename(segment)
            except OSError:
                pass
    path.mkdir(parents=True, exist_ok=True)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy src → dst with a 4 MB buffer, writing through a temp file for atomicity."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    # Thread-unique temp name so parallel copies of case-equivalent paths
    # (same name on a case-insensitive destination) never share a temp file.
    tmp = dst.with_name(f"{dst.name}.{threading.get_ident()}.synctmp")
    try:
        with open(src, "rb", buffering=_IO_BUFFER) as f_in, open(tmp, "wb", buffering=_IO_BUFFER) as f_out:
            while chunk := f_in.read(_IO_BUFFER):
                f_out.write(chunk)
        st = os.stat(src)
        os.utime(tmp, (st.st_atime, st.st_mtime))
        tmp.replace(dst)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _rmdir_if_empty(path: Path) -> None:
    """Remove a directory only if it is empty — never deletes files the diff didn't list."""
    try:
        path.rmdir()
    except OSError:
        pass


# ── Folder sync ───────────────────────────────────────────────────────────────


def _apply_diff(
    diff: SyncDiff,
    delete: bool,
    pull: bool,
    mkdir_fn: Callable[[str], None],
    transfer_fn: Callable[[SyncEntry], None],
    delete_file_fn: Callable[[str], None],
    delete_dir_fn: Callable[[str], None],
    same_file_fn: Callable[[str, str], bool],
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> dict:
    """Backend-agnostic apply: push or pull, local or SMB, driven by injected callbacks.

    pull=False (push): NEW+CHANGED are transferred, DELETED are optionally removed from dst.
    pull=True  (pull): CHANGED+DELETED are transferred, NEW are optionally removed from dst.
    Directories are only ever removed when empty, so files outside the diff
    (non-audio files, skipped folders) are never deleted.
    """
    stats = {"transferred": 0, "deleted": 0, "skipped": 0, "errors": [], "cancelled": False}

    if pull:
        transfer_statuses = (FileStatus.CHANGED, FileStatus.DELETED)
        kept_statuses     = (FileStatus.CHANGED, FileStatus.DELETED, FileStatus.UNCHANGED)
        delete_status     = FileStatus.NEW
        new_dir_status    = FileStatus.DELETED
        del_dir_status    = FileStatus.NEW
    else:
        transfer_statuses = (FileStatus.NEW, FileStatus.CHANGED)
        kept_statuses     = (FileStatus.NEW, FileStatus.CHANGED, FileStatus.UNCHANGED)
        delete_status     = FileStatus.DELETED
        new_dir_status    = FileStatus.NEW
        del_dir_status    = FileStatus.DELETED

    files = [e for e in diff.entries if not e.is_dir]
    dirs = [e for e in diff.entries if e.is_dir]
    new_dirs  = sorted((e for e in dirs if e.status == new_dir_status), key=lambda e: e.rel_path)
    del_dirs  = sorted((e for e in dirs if delete and e.status == del_dir_status),
                       key=lambda e: e.rel_path, reverse=True)
    transfers = [e for e in files if e.status in transfer_statuses]
    deletions = [e for e in files if delete and e.status == delete_status]

    kept = {e.rel_path.casefold(): e.rel_path for e in files if e.status in kept_statuses}
    if deletions and not kept:
        source = "remote" if pull else "local"
        raise SyncError(
            f"Refusing to delete {len(deletions)} file(s): the {source} side has no music files.\n"
            "Is the drive mounted / the music folder correct?"
        )

    def cancelled() -> bool:
        if cancel_fn and cancel_fn():
            stats["cancelled"] = True
        return stats["cancelled"]

    for entry in new_dirs:
        if cancelled():
            return stats
        try:
            mkdir_fn(entry.rel_path)
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    total = len(transfers) + len(deletions)
    done  = 0

    def guarded_transfer(entry: SyncEntry) -> bool:
        if cancelled():
            return False
        transfer_fn(entry)
        return True

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        future_map = {pool.submit(guarded_transfer, entry): entry for entry in transfers}
        for fut in as_completed(future_map):
            entry = future_map[fut]
            done += 1
            if progress_cb:
                progress_cb(done, total, entry.rel_path)
            try:
                if fut.result():
                    stats["transferred"] += 1
            except Exception as e:
                stats["errors"].append(f"{entry.rel_path}: {e}")
    if stats["cancelled"]:
        return stats

    for entry in deletions:
        if cancelled():
            return stats
        done += 1
        if progress_cb:
            progress_cb(done, total, entry.rel_path)
        # Case-only rename: on a case-insensitive destination the old-case path
        # *is* the file just transferred, so deleting it would destroy it.
        twin = kept.get(entry.rel_path.casefold())
        if twin and twin != entry.rel_path and same_file_fn(entry.rel_path, twin):
            stats["skipped"] += 1
            continue
        try:
            delete_file_fn(entry.rel_path)
            stats["deleted"] += 1
        except Exception as e:
            stats["errors"].append(f"{entry.rel_path}: {e}")

    for entry in del_dirs:
        if cancelled():
            return stats
        try:
            delete_dir_fn(entry.rel_path)
        except Exception:
            pass

    return stats


def compute_folder_diff(
    src: Path,
    dst,  # Path for folder sync, or (files_dict, dirs_set) pre-scanned tuple for SMB
    skip_dirs: set,
    extensions: set,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
    direction: str = "push",
) -> SyncDiff:
    """Compare src (local) with dst (remote).

    A file is CHANGED when sizes differ or the side being copied *from* is newer:
    the local copy for a push, the remote copy for a pull.
    """
    diff = SyncDiff(direction=direction)
    if not Path(src).is_dir():
        raise SyncError(f"Music folder not found: {src}")
    if isinstance(dst, tuple):
        remote_files, remote_dirs = dst
    else:
        dst = Path(dst)
        if not dst.is_absolute():
            raise SyncError(f"Sync destination must be an absolute path: {dst}")
        if not dst.is_dir():
            raise SyncError(f"Sync destination not found: {dst}\nIs the drive mounted? Create the folder first for a new backup.")
        _check_roots(Path(src), dst)
        remote_files, remote_dirs = _scan_local(dst, skip_dirs, extensions)
    local_files, local_dirs = _scan_local(src, skip_dirs, extensions)

    # Only folders that hold music on at least one side matter; folders with just
    # cover art, hidden folders, etc. would otherwise show up as "new"/"deleted".
    music_dirs: set[str] = set()
    for key in set(local_files) | set(remote_files):
        parts = key.split("/")
        music_dirs.update("/".join(parts[:i]) for i in range(1, len(parts)))
    local_dirs = set(local_dirs) & music_dirs
    remote_dirs = set(remote_dirs) & music_dirs

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
                ls = loc.stat()
                diff.entries.append(SyncEntry(rel, FileStatus.NEW, ls.st_size, local_mtime=ls.st_mtime))
            elif not loc and rem:
                rs = rem.stat()
                diff.entries.append(SyncEntry(rel, FileStatus.DELETED, 0, rs.st_size, remote_mtime=rs.st_mtime))
            elif loc and rem:
                ls, rs = loc.stat(), rem.stat()
                if direction == "pull":
                    source_newer = rs.st_mtime - ls.st_mtime > _MTIME_SLACK
                else:
                    source_newer = ls.st_mtime - rs.st_mtime > _MTIME_SLACK
                changed = ls.st_size != rs.st_size or source_newer
                diff.entries.append(
                    SyncEntry(
                        rel,
                        FileStatus.CHANGED if changed else FileStatus.UNCHANGED,
                        ls.st_size,
                        rs.st_size,
                        local_mtime=ls.st_mtime,
                        remote_mtime=rs.st_mtime,
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
    _check_roots(src, dst)
    return _apply_diff(
        diff, delete, pull=False,
        mkdir_fn=lambda rel: _ensure_case_correct_dir(dst / rel, dst),
        transfer_fn=lambda e: _copy_file(src / e.rel_path, dst / e.rel_path),
        delete_file_fn=lambda rel: (dst / rel).unlink(missing_ok=True),
        delete_dir_fn=lambda rel: _rmdir_if_empty(dst / rel),
        same_file_fn=lambda a, b: _same_local_file(dst / a, dst / b),
        progress_cb=progress_cb, cancel_fn=cancel_fn,
    )


def apply_folder_pull(
    diff: SyncDiff,
    local: Path,
    remote: Path,
    delete: bool,
    progress_cb: Callable | None = None,
    cancel_fn: Callable | None = None,
) -> dict:
    """Pull: copy CHANGED/DELETED from remote → local in parallel; optionally delete NEW."""
    _check_roots(local, remote)
    return _apply_diff(
        diff, delete, pull=True,
        mkdir_fn=lambda rel: _ensure_case_correct_dir(local / rel, local),
        transfer_fn=lambda e: _copy_file(remote / e.rel_path, local / e.rel_path),
        delete_file_fn=lambda rel: (local / rel).unlink(missing_ok=True),
        delete_dir_fn=lambda rel: _rmdir_if_empty(local / rel),
        same_file_fn=lambda a, b: _same_local_file(local / a, local / b),
        progress_cb=progress_cb, cancel_fn=cancel_fn,
    )


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
        self._conn_lock = threading.Lock()

    def _make_conn(self):
        try:
            from smb.SMBConnection import SMBConnection
        except ImportError:
            raise RuntimeError("pysmb not installed — pip install pysmb")
        remote_name = self.host.split(".")[0]
        for port, direct in ((445, True), (139, False)):
            try:
                conn = SMBConnection(
                    self.user,
                    self.password,
                    socket.gethostname(),
                    remote_name,
                    use_ntlm_v2=True,
                    is_direct_tcp=direct,
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

    def _ensure_connected(self) -> None:
        """Reconnect the main connection if the server dropped it (e.g. while the diff was reviewed)."""
        if self._conn is not None:
            try:
                self._conn.echo(b"ping", timeout=10)
                return
            except Exception:
                try:
                    self._conn.close()
                except Exception:
                    pass
        if not self.connect():
            raise SyncError(f"Lost connection to {self.host} and could not reconnect")

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

    def _upload_via(self, conn, rpath: str, local_path: Path) -> None:
        """Atomic upload: write to a temp file, then swap it in, keeping the old copy until the swap succeeds."""
        tid = threading.get_ident()
        rtmp = f"{rpath}.{tid}.synctmp"
        rbak = f"{rpath}.{tid}.syncold"
        try:
            with open(local_path, "rb", buffering=_IO_BUFFER) as fh:
                conn.storeFile(self.share, rtmp, fh)
        except Exception:
            try:
                conn.deleteFiles(self.share, rtmp)
            except Exception:
                pass
            raise
        # SMB rename can't overwrite, so move the old copy aside rather than deleting it first
        moved_old = False
        try:
            conn.rename(self.share, rpath, rbak)
            moved_old = True
        except Exception:
            pass  # no existing file
        try:
            conn.rename(self.share, rtmp, rpath)
        except Exception:
            if moved_old:
                try:
                    conn.rename(self.share, rbak, rpath)
                except Exception:
                    pass
            try:
                conn.deleteFiles(self.share, rtmp)
            except Exception:
                pass
            raise
        if moved_old:
            try:
                conn.deleteFiles(self.share, rbak)
            except Exception:
                pass

    def _list_remote(self, path: str) -> dict:
        if self._conn is None:
            raise SyncError(f"Not connected to {self.host}")
        try:
            entries = self._conn.listPath(self.share, path)
        except Exception as e:
            # Never treat an unreadable folder as empty — that would mark its contents for deletion
            raise SyncError(f"Could not list {self.share}{path} on {self.host}: {e}") from e
        return {e.filename: e for e in entries if e.filename not in (".", "..")}

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
            rpath = rpath[len(prefix) + 1 :]
        return rpath

    def compute_diff(
        self,
        local_root: Path,
        skip_dirs: set,
        extensions: set,
        remote_root: str = "/",
        progress_cb: Callable | None = None,
        cancel_fn: Callable | None = None,
        direction: str = "push",
    ) -> SyncDiff:
        # Bridge: walk the SMB remote and wrap entries as _RemoteEntry so they
        # duck-type as local Paths; then delegate to compute_folder_diff.
        # The remote gets the same filtering as the local side so non-audio
        # files and skipped folders are never transferred or deleted.
        remote_flat, remote_dirs_abs = self._walk_remote(remote_root)
        remote_files = {}
        for k, v in remote_flat.items():
            rel = self._rel(k, remote_root)
            parts = rel.split("/")
            if skip_dirs & set(parts[:-1]) or posixpath.splitext(rel)[1].lower() not in extensions:
                continue
            remote_files[rel] = _RemoteEntry(v.file_size, v.last_write_time)
        remote_dirs = {
            rel for rel in (self._rel(d, remote_root) for d in remote_dirs_abs)
            if not skip_dirs & set(rel.split("/"))
        }
        return compute_folder_diff(
            local_root, (remote_files, remote_dirs),
            skip_dirs, extensions, progress_cb, cancel_fn, direction,
        )

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
        self._ensure_connected()
        root = remote_root.rstrip("/")

        def _rpath(rel: str) -> str:
            return root + "/" + rel.replace("\\", "/")

        def mkdir_fn(rel: str) -> None:
            with self._conn_lock:
                self._ensure_remote_dir(rel, remote_root)

        def transfer_fn(entry: SyncEntry) -> None:
            rel = entry.rel_path
            rpath = _rpath(rel)
            local_path = local_root / rel
            try:
                with self._pooled_conn() as conn:
                    self._ensure_remote_file_dirs(rel, remote_root, conn=conn)
                    self._upload_via(conn, rpath, local_path)
            except Exception:
                # Retry once, serialised on the main connection
                with self._conn_lock:
                    self._ensure_remote_file_dirs(rel, remote_root, conn=self._conn)
                    self._upload_via(self._conn, rpath, local_path)

        def delete_file_fn(rel: str) -> None:
            with self._conn_lock:
                self._conn.deleteFiles(self.share, _rpath(rel))

        def delete_dir_fn(rel: str) -> None:
            # deleteDirectory only succeeds on empty directories
            try:
                with self._conn_lock:
                    self._conn.deleteDirectory(self.share, _rpath(rel))
            except Exception:
                pass

        return _apply_diff(
            diff, delete, pull=False,
            mkdir_fn=mkdir_fn,
            transfer_fn=transfer_fn,
            delete_file_fn=delete_file_fn,
            delete_dir_fn=delete_dir_fn,
            # SMB servers are case-insensitive: a case-variant path is the same file
            same_file_fn=lambda a, b: True,
            progress_cb=progress_cb, cancel_fn=cancel_fn,
        )

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
        self._ensure_connected()
        root = remote_root.rstrip("/")

        def _rpath(rel: str) -> str:
            return root + "/" + rel.replace("\\", "/")

        def mkdir_fn(rel: str) -> None:
            _ensure_case_correct_dir(local_root / rel, local_root)

        def transfer_fn(entry: SyncEntry) -> None:
            rel = entry.rel_path
            rpath = _rpath(rel)
            local_path = local_root / rel
            local_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = local_path.with_name(f"{local_path.name}.{threading.get_ident()}.synctmp")
            try:
                with self._pooled_conn() as conn:
                    with open(tmp, "wb", buffering=_IO_BUFFER) as fh:
                        conn.retrieveFile(self.share, rpath, fh)
                if entry.remote_mtime:
                    # Keep the remote mtime so the next diff sees the files as identical
                    os.utime(tmp, (entry.remote_mtime, entry.remote_mtime))
                tmp.replace(local_path)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass
                raise

        def delete_file_fn(rel: str) -> None:
            (local_root / rel).unlink(missing_ok=True)

        return _apply_diff(
            diff, delete, pull=True,
            mkdir_fn=mkdir_fn,
            transfer_fn=transfer_fn,
            delete_file_fn=delete_file_fn,
            delete_dir_fn=lambda rel: _rmdir_if_empty(local_root / rel),
            same_file_fn=lambda a, b: _same_local_file(local_root / a, local_root / b),
            progress_cb=progress_cb, cancel_fn=cancel_fn,
        )
