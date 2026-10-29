"""Sync must never delete or overwrite things it shouldn't (C2–C4, H9–H13, M24, M26, R3)."""
import os
import time
from pathlib import Path

import pytest

from conftest import mod

s = mod("core.sync")
F = s.FileStatus
EXT = {".mp3", ".flac"}


def mk(p: Path, data=b"x" * 10, mtime=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if mtime:
        os.utime(p, (mtime, mtime))
    return p


@pytest.fixture
def roots(tmp_path):
    return tmp_path / "lib", tmp_path / "dst"


def test_push_delete_keeps_unrelated_and_non_audio_files(roots):
    lib, dst = roots
    mk(lib / "A/al/1.mp3"); mk(dst / "A/al/1.mp3"); mk(dst / "A/al/cover.jpg")
    mk(dst / "Documents/taxes.pdf"); mk(dst / "Podcasts/ep.mp3")
    mk(dst / "Old/gone.mp3"); mk(dst / "OldAlbum/track.mp3"); mk(dst / "OldAlbum/cover.jpg")
    d = s.compute_folder_diff(lib, dst, {"Podcasts"}, EXT)
    s.apply_folder_sync(d, lib, dst, delete=True)
    assert (dst / "Documents/taxes.pdf").exists()
    assert (dst / "A/al/cover.jpg").exists()
    assert (dst / "Podcasts/ep.mp3").exists(), "skip_dirs must be left alone at the destination"
    assert not (dst / "Old").exists(), "remote-only audio is deleted and its emptied folder removed"
    assert not (dst / "OldAlbum/track.mp3").exists()
    assert (dst / "OldAlbum/cover.jpg").exists(), "a remote-only folder is never deleted with non-audio files in it"


def test_missing_destination_is_an_error(roots):
    lib, dst = roots
    mk(lib / "a.mp3")
    with pytest.raises(s.SyncError):
        s.compute_folder_diff(lib, dst, set(), EXT)


def test_relative_destination_is_an_error(roots):
    lib, _ = roots
    mk(lib / "a.mp3")
    with pytest.raises(s.SyncError):
        s.compute_folder_diff(lib, Path("relative/dir"), set(), EXT)


def test_push_delete_refused_when_library_is_empty(roots):
    lib, dst = roots
    lib.mkdir(); mk(dst / "keep.mp3")
    d = s.compute_folder_diff(lib, dst, set(), EXT)
    with pytest.raises(s.SyncError):
        s.apply_folder_sync(d, lib, dst, delete=True)
    assert (dst / "keep.mp3").exists()


def test_pull_delete_refused_when_remote_is_empty(roots):
    lib, dst = roots
    mk(lib / "mine.mp3"); dst.mkdir()
    d = s.compute_folder_diff(lib, dst, set(), EXT, direction="pull")
    with pytest.raises(s.SyncError):
        s.apply_folder_pull(d, lib, dst, delete=True)
    assert (lib / "mine.mp3").exists()


def test_case_only_rename_does_not_delete_the_new_copy():
    d = s.SyncDiff(entries=[s.SyncEntry("artist/x.mp3", F.NEW), s.SyncEntry("Artist/x.mp3", F.DELETED)])
    deleted = []
    st = s._apply_diff(d, True, False, lambda r: None, lambda e: None, deleted.append, lambda r: None,
                       same_file_fn=lambda a, b: True)  # case-insensitive destination
    assert deleted == [] and st["skipped"] == 1
    deleted.clear()
    s._apply_diff(d, True, False, lambda r: None, lambda e: None, deleted.append, lambda r: None,
                  same_file_fn=lambda a, b: False)  # case-sensitive: genuinely separate file
    assert deleted == ["Artist/x.mp3"]


def test_case_variant_dirs_not_merged_on_case_sensitive_fs(roots):
    _, dst = roots
    mk(dst / "Abba/old.mp3")
    s._ensure_case_correct_dir(dst / "ABBA", dst)
    assert (dst / "Abba/old.mp3").exists() and (dst / "ABBA").is_dir()


def test_change_detection_is_direction_aware(roots):
    lib, dst = roots
    now = time.time()
    mk(lib / "localedit.mp3", mtime=now); mk(dst / "localedit.mp3", mtime=now - 1000)
    mk(lib / "remoteedit.mp3", mtime=now - 1000); mk(dst / "remoteedit.mp3", mtime=now)
    pull = {e.rel_path: e.status for e in s.compute_folder_diff(lib, dst, set(), EXT, direction="pull").entries}
    push = {e.rel_path: e.status for e in s.compute_folder_diff(lib, dst, set(), EXT).entries}
    assert pull["localedit.mp3"] == F.UNCHANGED, "pull must not overwrite a newer local edit"
    assert pull["remoteedit.mp3"] == F.CHANGED
    assert push["localedit.mp3"] == F.CHANGED and push["remoteedit.mp3"] == F.UNCHANGED


def test_overwriting_newer_files_is_reported(roots):
    lib, dst = roots
    now = time.time()
    mk(lib / "s.mp3", b"y" * 20, mtime=now); mk(dst / "s.mp3", mtime=now - 1000)
    assert s.compute_folder_diff(lib, dst, set(), EXT, direction="pull").overwrites_newer() == 1


def test_round_trip_is_clean_and_leaves_no_temp_files(roots):
    lib, dst = roots
    mk(lib / "a/b.mp3", b"z" * 100, mtime=time.time() - 5000); dst.mkdir()
    s.apply_folder_sync(s.compute_folder_diff(lib, dst, set(), EXT), lib, dst, False)
    assert not s.compute_folder_diff(lib, dst, set(), EXT).has_changes()
    assert not list(dst.rglob("*.synctmp"))


def test_cancel_stops_queued_transfers(roots):
    lib, dst = roots
    dst.mkdir()
    for i in range(40):
        mk(lib / f"{i}.mp3")
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 3

    st = s.apply_folder_sync(s.compute_folder_diff(lib, dst, set(), EXT), lib, dst, False, cancel_fn=cancel)
    assert st["cancelled"] and st["transferred"] < 40


def test_overlapping_folders_refused(roots):
    lib, _ = roots
    mk(lib / "a.mp3"); (lib / "backup").mkdir()
    with pytest.raises(s.SyncError):
        s.compute_folder_diff(lib, lib / "backup", set(), EXT)


def test_folders_without_music_are_not_listed(roots):
    lib, dst = roots
    mk(lib / "A/song.mp3"); mk(lib / "ArtOnly/cover.jpg"); dst.mkdir()
    paths = {e.rel_path for e in s.compute_folder_diff(lib, dst, set(), EXT).entries}
    assert "ArtOnly" not in paths and "A" in paths


# ── SMB, against a fake connection ────────────────────────────────────────────

class _E:
    def __init__(self, n, d=False):
        self.filename, self.isDirectory, self.file_size, self.last_write_time = n, d, 1, 0.0


class _FakeConn:
    def __init__(self, files, fail_swap_to=None, fail_list=()):
        self.files, self.fail_swap_to, self.fail_list = dict(files), fail_swap_to, fail_list

    def storeFile(self, share, p, fh):
        self.files[p] = fh.read()

    def rename(self, share, a, b):
        if b in self.files or a not in self.files or (b == self.fail_swap_to and a.endswith(".synctmp")):
            raise OSError("rename failed")
        self.files[b] = self.files.pop(a)

    def deleteFiles(self, share, p):
        self.files.pop(p)

    def listPath(self, share, path):
        if path in self.fail_list:
            raise OSError("access denied")
        pre, kids = path.rstrip("/") + "/", {}
        for f in self.files:
            if f.startswith(pre):
                head = f[len(pre):].split("/")[0]
                kids[head] = _E(head, d="/" in f[len(pre):])
        return list(kids.values())


def test_smb_upload_swap_restores_original_on_failure(tmp_path):
    src = mk(tmp_path / "song.mp3", b"NEW")
    sy = s.SMBSyncer("h", "share", "u", "p")
    ok = _FakeConn({"/A/song.mp3": b"OLD"})
    sy._upload_via(ok, "/A/song.mp3", src)
    assert ok.files == {"/A/song.mp3": b"NEW"}
    bad = _FakeConn({"/A/song.mp3": b"OLD"}, fail_swap_to="/A/song.mp3")
    with pytest.raises(OSError):
        sy._upload_via(bad, "/A/song.mp3", src)
    assert bad.files == {"/A/song.mp3": b"OLD"}


def test_smb_remote_filtered_to_library_audio(tmp_path):
    sy = s.SMBSyncer("h", "share", "u", "p")
    sy._conn = _FakeConn({"/Music/a.mp3": b"1", "/Movies/film.mkv": b"1", "/Music/x.mp3.1.synctmp": b"1",
                          "/Podcasts/ep.mp3": b"1", "/Music/cover.jpg": b"1"})
    d = sy.compute_diff(tmp_path, {"Podcasts"}, {".mp3"})
    assert {e.rel_path for e in d.entries if not e.is_dir} == {"Music/a.mp3"}


def test_smb_listing_error_aborts_instead_of_looking_empty(tmp_path):
    sy = s.SMBSyncer("h", "share", "u", "p")
    sy._conn = _FakeConn({"/Music/a.mp3": b"1", "/Locked/b.mp3": b"1"}, fail_list=("/Locked",))
    with pytest.raises(s.SyncError):
        sy.compute_diff(tmp_path, set(), {".mp3"})
    sy._conn = None
    with pytest.raises(s.SyncError):
        sy.compute_diff(tmp_path, set(), {".mp3"})
