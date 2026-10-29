"""Every change of a song's path is logged, with its new location when known (R9, S4)."""
from pathlib import Path

from conftest import make_audio, mod, needs_ffmpeg

dl = mod("core.deleted_log")
lib_tab = mod("ui.library_tab")


def mk(p: Path, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def scan(lib, qapp):
    t = lib_tab._ScanThread(lib, set(), {".mp3", ".opus"}, False)
    got = []
    t.missing_found.connect(got.append)
    t.run()
    return got[0] if got else 0


def by_path(log):
    return {Path(e["path"]).name: e for e in log}


def test_first_scan_only_sets_baseline(tmp_path, qapp):
    mk(tmp_path / "A/Alb/a.mp3")
    assert scan(tmp_path, qapp) == 0 and dl.load_log() == []


def test_deleted_moved_and_reencoded_files_are_all_logged(tmp_path, qapp):
    lib = tmp_path
    mk(lib / "A/Alb/gone.mp3", b"gone"); mk(lib / "A/Alb/moveme.mp3", b"mv"); mk(lib / "A/Alb/liked.mp3", b"lk")
    scan(lib, qapp)
    (lib / "A/Alb/gone.mp3").unlink()
    mk(lib / "B/Other/moveme.mp3", b"mv"); (lib / "A/Alb/moveme.mp3").unlink()
    mk(lib / "A/Alb/liked.opus", b"different size"); (lib / "A/Alb/liked.mp3").unlink()
    assert scan(lib, qapp) == 3
    log = by_path(dl.load_log())
    assert log["gone.mp3"]["how"] == "Found missing" and log["gone.mp3"]["replaced_by"] == ""
    assert log["moveme.mp3"]["how"] == "Moved" and log["moveme.mp3"]["replaced_by"].endswith("B/Other/moveme.mp3")
    assert log["liked.mp3"]["how"] == "Extension changed" and log["liked.mp3"]["replaced_by"].endswith("liked.opus")
    assert scan(lib, qapp) == 0, "nothing is logged twice"


def test_empty_scan_and_folder_change_never_mass_report(tmp_path, qapp):
    mk(tmp_path / "A/a.mp3")
    scan(tmp_path, qapp)
    assert dl.reconcile(tmp_path, set(), {}) == 0
    assert dl.reconcile(Path("/elsewhere"), set(), {"/elsewhere/x.mp3": {"title": "x", "artist": "", "size": 1}}) == 0
    assert dl.load_log() == []


def test_in_app_deletion_logged_once_with_replacement(tmp_path, qapp):
    keep, dup = mk(tmp_path / "A/keep.mp3"), mk(tmp_path / "A/dup.mp3")
    scan(tmp_path, qapp)
    snap = dl.snapshot([dup])
    dup.unlink()
    dl.record_deleted(snap, "Deleted in Duplicates", {str(dup): keep})
    assert scan(tmp_path, qapp) == 0
    (entry,) = dl.load_log()
    assert entry["how"] == "Deleted in Duplicates" and entry["replaced_by"] == str(keep)


@needs_ffmpeg
def test_organize_logs_moves(tmp_path, qapp):
    make_audio(tmp_path / "Inbox/one.mp3", artist="Queen", album="Jazz", title="one")
    scan(tmp_path, qapp)
    mod("core.metadata").batch_organize(tmp_path, set(), {".mp3"})
    (entry,) = dl.load_log()
    assert entry["how"] == "Moved by Organize" and entry["replaced_by"].endswith("Queen/Jazz/one.mp3")
    assert scan(tmp_path, qapp) == 0, "an in-app move isn't reported again as missing"


def test_pull_sync_with_delete_logs_removed_songs(tmp_path, qapp):
    sync = mod("core.sync")
    worker_mod = mod("workers.sync_worker")
    lib, remote = tmp_path / "lib", tmp_path / "remote"
    mk(remote / "B/x.mp3"); mk(lib / "B/x.mp3"); mk(lib / "A/localonly.mp3")

    class Cfg:
        music_dir = lib
        folder_sync_dest = str(remote)

    diff = sync.compute_folder_diff(lib, remote, set(), {".mp3"}, direction="pull")
    w = worker_mod.SyncApplyWorker("folder", diff, Cfg(), delete=True, direction="pull")
    w.run()
    assert not (lib / "A/localonly.mp3").exists()
    (entry,) = dl.load_log()
    assert entry["how"] == "Deleted by Pull sync" and entry["path"].endswith("localonly.mp3")
