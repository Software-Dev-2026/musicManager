"""UI-level safety nets: duplicate deletion, quitting mid-job, library paths, diff view (C7, C8, R1–R6)."""
import csv
import os
import time
from pathlib import Path
from unittest import mock

import pytest

from conftest import mod


@pytest.fixture
def dupes_tab(tmp_path, qapp):
    dt = mod("ui.duplicates_tab")

    class Cfg:
        music_dir = tmp_path
        skip_dirs = []
        audio_extensions = {".mp3"}
        fingerprint_cache = tmp_path / "fp.json"
        fingerprint_threshold = 0.9
        dupes_priority_dirs = []

        def get(self, k, default=None):
            return default

        def set(self, *a):
            pass

        def save(self):
            pass

    return dt, dt.DuplicatesTab(Cfg())


def _check(tab, *rows):
    from PyQt6.QtCore import Qt
    g = tab._results.topLevelItem(0)
    for j in range(g.childCount()):
        g.child(j).setCheckState(0, Qt.CheckState.Checked if j in rows else Qt.CheckState.Unchecked)
    return [Path(g.child(j).data(0, Qt.ItemDataRole.UserRole)) for j in range(g.childCount())]


def _delete(dt, tab):
    with mock.patch.object(dt.QMessageBox, "warning") as warn, \
         mock.patch.object(dt.QMessageBox, "question", return_value=dt.QMessageBox.StandardButton.Yes):
        tab._delete_selected()
    return warn.called


def test_cannot_delete_every_copy(dupes_tab, tmp_path):
    dt, tab = dupes_tab
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"1"); b.write_bytes(b"22")
    tab._on_done([[a, b]])
    _check(tab, 0, 1)
    assert _delete(dt, tab) and a.exists() and b.exists()


def test_cannot_delete_last_copy_when_kept_copy_vanished(dupes_tab, tmp_path):
    dt, tab = dupes_tab
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"1"); b.write_bytes(b"22")
    tab._on_done([[a, b]])
    keep, other = _check(tab, 1)
    keep.unlink()
    assert _delete(dt, tab) and other.exists()


def test_export_lists_every_copy(dupes_tab, tmp_path):
    dt, tab = dupes_tab
    a, b = tmp_path / "a.mp3", tmp_path / "é.mp3"
    a.write_bytes(b"1"); b.write_bytes(b"22")
    tab._on_done([[a, b]])
    _check(tab, 1)
    with mock.patch.object(dt.QFileDialog, "getSaveFileName", return_value=(str(tmp_path / "out"), "")):
        tab._export_list()
    rows = list(csv.reader(open(tmp_path / "out.csv", encoding="utf-8-sig")))
    assert rows[0][0] == "Group" and {r[5] for r in rows[1:]} == {str(a), str(b)}
    assert sum(r[2] == "yes" for r in rows[1:]) == 1


def test_quitting_stops_running_jobs_first(qapp):
    bw = mod("workers.base_worker")

    class Busy(bw.BaseWorker):
        def run(self):
            while not self._cancelled:
                time.sleep(0.02)

    b = Busy()
    b.start()
    time.sleep(0.1)
    assert b in bw.running_threads()
    assert bw.stop_all(5000) and not b.isRunning()


def test_library_scan_stores_real_paths(tmp_path, qapp):
    lt = mod("ui.library_tab")
    for rel in ["root.mp3", "A/single.mp3", "A/Alb/t.mp3", "A/Alb/CD1/deep.mp3"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"x")
    t = lt._ScanThread(tmp_path, set(), {".mp3"}, False)
    out = []
    t.done.connect(out.append)
    t.run()
    tree = {artist: ({a: [Path(x[1]).name for x in s] for a, s in albums}, [Path(x[1]).name for x in singles])
            for artist, albums, singles in out[0]}
    paths = [x[1] for _, albums, singles in out[0] for x in singles + [y for _, s in albums for y in s]]
    assert all(os.path.isfile(p) for p in paths) and len(paths) == 4
    assert sorted(tree["A"][0]["Alb"]) == ["deep.mp3", "t.mp3"]
    assert tree["Misc"] == ({}, ["root.mp3"])


def test_diff_tree_rolls_changes_up_and_filters(tmp_path, qapp):
    st = mod("ui.sync_tab")
    sync = mod("core.sync")
    lib, dst = tmp_path / "lib", tmp_path / "dst"

    def mk(root, rel, data=b"x"):
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(data)
        os.utime(root / rel, (1e9, 1e9))

    for i in range(5):
        mk(lib, f"Same/Alb/{i}.mp3"); mk(dst, f"Same/Alb/{i}.mp3")
    mk(lib, "Deep/Alb/CD1/changed.mp3", b"new!"); mk(dst, "Deep/Alb/CD1/changed.mp3")
    mk(lib, "rootnew.mp3")

    class Cfg:
        sync_mode = "folder"
        folder_sync_dest = str(dst)
        smb_host = smb_share = ""

    tab = st.SyncTab(Cfg())
    tab._populate_diff_tree(sync.compute_folder_diff(lib, dst, set(), {".mp3"}))
    top = {tab._diff_tree.topLevelItem(i).text(0): tab._diff_tree.topLevelItem(i)
           for i in range(tab._diff_tree.topLevelItemCount())}
    assert top["Deep"].text(1) == "1 changed" and top["Deep"].isExpanded()
    assert not top["Same"].isExpanded() and top["rootnew.mp3"].text(1) == "NEW"
    tab._changes_only_cb.setChecked(True)
    assert sorted(n for n, it in top.items() if not it.isHidden()) == ["Deep", "rootnew.mp3"]
