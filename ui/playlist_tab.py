from pathlib import Path

from PyQt6.QtCore import QEvent, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QStyle,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config
from ..core.downloader import decode_playlist
from ..core.metadata import read_quick_meta
from ..workers.base_worker import BaseWorker
from .library_tab import LibraryTree
from .styles import COLORS, make_vdivider

_OK_COLOR = QColor(COLORS["success"])
_MISS_COLOR = QColor(COLORS["error"])


class _TrackInfoWorker(BaseWorker):
    def __init__(self, paths: list[str], parent=None):
        super().__init__(parent)
        self._paths = paths

    def run(self):
        total = len(self._paths)
        entries = []
        for i, p in enumerate(self._paths):
            path = Path(p)
            title, _, dur, _ = read_quick_meta(path)
            entries.append(
                {
                    "raw": p,
                    "path": p,
                    "title": title,
                    "duration": dur,
                    "exists": path.exists(),
                }
            )
            self.progress.emit(i + 1, total, "")
        self.finished.emit(entries)


class _LibraryPickerDialog(QDialog):
    """Modal dialog with the library tree for selecting songs to add."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Songs from Library")
        self.resize(480, 560)
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        search = QLineEdit()
        search.setPlaceholderText("Filter…")
        layout.addWidget(search)

        self._tree = LibraryTree(config, compact=True, multi_select=True)
        layout.addWidget(self._tree)
        self._tree.refresh()
        search.textChanged.connect(self._tree.filter)

        hint = QLabel("Hold Ctrl or Shift to select multiple songs.")
        hint.setObjectName("muted")
        layout.addWidget(hint)

        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def selected_paths(self) -> list[str]:
        def collect(item) -> list[str]:
            if item.isHidden():
                return []
            path = item.data(0, Qt.ItemDataRole.UserRole)
            if path:
                return [path]
            result = []
            for i in range(item.childCount()):
                result.extend(collect(item.child(i)))
            return result

        seen: set[str] = set()
        paths: list[str] = []
        for item in self._tree.selectedItems():
            for p in collect(item):
                if p not in seen:
                    seen.add(p)
                    paths.append(p)
        return paths



def _parse_m3u(playlist_path: Path) -> list[dict]:
    try:
        raw = playlist_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    raw = decode_playlist(raw)
    entries, title, duration = [], None, -1
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF:"):
            rest = line[8:]
            comma = rest.find(",")
            if comma >= 0:
                try:
                    duration = int(float(rest[:comma]))
                except ValueError:
                    duration = -1
                title = rest[comma + 1 :].strip()
            else:
                title, duration = rest, -1
        elif line.startswith("#"):
            continue
        else:
            p = Path(line.replace("\\", "/"))
            if not p.is_absolute():
                p = (playlist_path.parent / p).resolve()
            entries.append(
                {
                    "raw": line,
                    "path": str(p),
                    "title": title or p.stem,
                    "duration": duration,
                    "exists": p.exists(),
                }
            )
            title, duration = None, -1
    return entries


def _write_m3u(playlist_path: Path, entries: list[dict]) -> list[str]:
    """Write entries to an M3U file. Returns a list of paths that could not be made relative."""
    base = playlist_path.parent
    lines = ["#EXTM3U"]
    unresolvable = []
    for e in entries:
        lines.append(f"#EXTINF:{e.get('duration', -1)},{e.get('title', '')}")
        try:
            lines.append(str(Path(e["path"]).relative_to(base)))
        except ValueError:
            unresolvable.append(e["path"])
            lines.append(e["path"])
    playlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return unresolvable


def _fmt_duration(total_secs: int) -> str:
    if total_secs <= 0:
        return "unknown duration"
    h, r = divmod(total_secs, 3600)
    m, s = divmod(r, 60)
    return f"{h}h {m}m {s}s" if h else f"{m}m {s}s"


class _PlaylistTree(QTreeWidget):
    reordered = pyqtSignal()

    def dropEvent(self, event):
        super().dropEvent(event)
        # Flatten any items accidentally nested under another item
        i = 0
        while i < self.topLevelItemCount():
            item = self.topLevelItem(i)
            insert_at = i + 1
            while item.childCount():
                self.insertTopLevelItem(insert_at, item.takeChild(0))
                insert_at += 1
            i += 1
        self.reordered.emit()


class PlaylistTab(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._entries: list[dict] = []
        self._playlist_path: Path | None = None
        self._dirty = False
        self._undo_stack: list[list[dict]] = []
        self._redo_stack: list[list[dict]] = []
        self._picker_dialog: _LibraryPickerDialog | None = None
        self._setup_ui()
        self.scan_playlists()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_left())

        outer.addWidget(make_vdivider())

        outer.addWidget(self._build_right())

        self._track_list.installEventFilter(self)
        self._playlist_list.installEventFilter(self)
        self._track_list.reordered.connect(self._on_rows_moved)

        QShortcut(QKeySequence.StandardKey.Undo, self).activated.connect(self._undo)
        QShortcut(QKeySequence.StandardKey.Redo, self).activated.connect(self._redo)

    def _build_left(self) -> QWidget:
        left = QWidget()
        left.setFixedWidth(270)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(8, 12, 4, 12)
        ll.setSpacing(8)

        # Header row — matches height of right panel's header row
        hdr_row = QHBoxLayout()
        hdr = QLabel("Playlists")
        hdr.setObjectName("section_header")
        hdr_row.addWidget(hdr, 1)
        scan_btn = QPushButton("Scan")
        scan_btn.clicked.connect(self.scan_playlists)
        hdr_row.addWidget(scan_btn)
        ll.addLayout(hdr_row)

        self._playlist_list = QListWidget()
        self._playlist_list.itemClicked.connect(self._on_playlist_clicked)
        ll.addWidget(self._playlist_list)

        btn_row = QHBoxLayout()
        new_btn = QPushButton("New")
        new_btn.clicked.connect(self._new_playlist)
        rename_btn = QPushButton("Rename")
        rename_btn.clicked.connect(self._rename_playlist)
        del_btn = QPushButton("Delete")
        del_btn.setObjectName("danger")
        del_btn.clicked.connect(self._delete_playlist)
        btn_row.addWidget(new_btn)
        btn_row.addWidget(rename_btn)
        btn_row.addWidget(del_btn)
        ll.addLayout(btn_row)

        return left

    def _build_right(self) -> QWidget:
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.setSpacing(8)

        # Header: path + Fix Encoding button
        header_row = QHBoxLayout()
        self._path_label = QLabel("No playlist selected")
        self._path_label.setObjectName("muted")
        self._path_label.setWordWrap(True)
        header_row.addWidget(self._path_label, 1)
        self._fix_btn = QPushButton("Fix Encoding")
        self._fix_btn.setEnabled(False)
        self._fix_btn.clicked.connect(self._fix_encoding)
        self._fix_btn.setToolTip("URL-decode %xx sequences in file paths within the playlist")
        header_row.addWidget(self._fix_btn)
        rl.addLayout(header_row)

        # Track list
        self._track_list = _PlaylistTree()
        self._track_list.setHeaderLabels(["", "#", "Title", "Status"])
        self._track_list.setColumnWidth(0, 20)
        self._track_list.setColumnWidth(1, 40)
        self._track_list.setColumnWidth(2, 420)
        self._track_list.setColumnWidth(3, 100)
        self._track_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._track_list.setAlternatingRowColors(True)
        self._track_list.setDragEnabled(True)
        self._track_list.setAcceptDrops(True)
        self._track_list.setDropIndicatorShown(True)
        self._track_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._track_list.itemDoubleClicked.connect(self._on_track_double_clicked)

        _placeholder = QLabel("No playlist selected\n\nSelect or create a playlist from the left panel.")
        _placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        _placeholder.setObjectName("muted")
        _placeholder.setWordWrap(True)

        self._track_stack = QStackedWidget()
        self._track_stack.addWidget(_placeholder)  # page 0 — no selection
        self._track_stack.addWidget(self._track_list)  # page 1 — playlist loaded
        rl.addWidget(self._track_stack)

        self._add_progress = QProgressBar()
        self._add_progress.setVisible(False)
        self._add_progress.setTextVisible(False)
        self._add_progress.setFixedHeight(4)
        rl.addWidget(self._add_progress)

        # Track action buttons
        _style = QApplication.style()
        track_btns = QHBoxLayout()
        self._add_btn = QPushButton("Add Songs…")
        self._add_btn.clicked.connect(self._add_songs)
        add_btn = self._add_btn
        rm_btn = QPushButton("Remove")
        rm_btn.clicked.connect(self._remove_selected)
        up_btn = QPushButton()
        up_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        up_btn.setFixedWidth(32)
        up_btn.setToolTip("Move up")
        up_btn.clicked.connect(self._move_up)
        dn_btn = QPushButton()
        dn_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        dn_btn.setFixedWidth(32)
        dn_btn.setToolTip("Move down")
        dn_btn.clicked.connect(self._move_down)
        rm_miss_btn = QPushButton("Remove Missing")
        rm_miss_btn.clicked.connect(self._remove_missing)
        resolve_btn = QPushButton("Resolve Missing")
        resolve_btn.setToolTip("Find missing tracks by filename in the music library")
        resolve_btn.clicked.connect(self._resolve_missing)
        dedup_btn = QPushButton("Remove Duplicates")
        dedup_btn.clicked.connect(self._dedup)
        sort_lbl = QLabel("Sort:")
        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Unsorted", "By Title", "By Path", "By Duration"])
        self._sort_combo.currentIndexChanged.connect(self._sort_tracks)
        track_btns.addWidget(add_btn)
        track_btns.addWidget(rm_btn)
        track_btns.addWidget(up_btn)
        track_btns.addWidget(dn_btn)
        track_btns.addSpacing(12)
        track_btns.addWidget(rm_miss_btn)
        track_btns.addWidget(resolve_btn)
        track_btns.addWidget(dedup_btn)
        track_btns.addSpacing(12)
        track_btns.addWidget(sort_lbl)
        track_btns.addWidget(self._sort_combo)
        track_btns.addStretch()
        rl.addLayout(track_btns)

        # Status bar + Save
        status_row = QHBoxLayout()
        self._stats_label = QLabel("")
        self._stats_label.setObjectName("muted")
        status_row.addWidget(self._stats_label, 1)
        self._save_status_label = QLabel("")
        self._save_status_label.setVisible(False)
        status_row.addWidget(self._save_status_label)
        self._save_btn = QPushButton("Save")
        self._save_btn.setObjectName("primary")
        self._save_btn.setVisible(False)
        self._save_btn.clicked.connect(self._save)
        status_row.addWidget(self._save_btn)
        rl.addLayout(status_row)

        return right

    # ── Playlist sidebar ──────────────────────────────────────────────────────

    def scan_playlists(self):
        self._playlist_list.clear()
        music_dir = self.config.music_dir
        if not music_dir.exists():
            return
        skip = set(self.config.skip_dirs)
        found = sorted(
            p
            for pattern in ("*.m3u", "*.m3u8")
            for p in music_dir.rglob(pattern)
            if not (skip & set(p.relative_to(music_dir).parts))
        )
        for path in found:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self._playlist_list.addItem(item)
        if not found:
            placeholder = QListWidgetItem("(no playlists found)")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self._playlist_list.addItem(placeholder)
        if self._picker_dialog is not None:
            self._picker_dialog._tree.refresh()

    def _on_playlist_clicked(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        if self._playlist_path == Path(path):
            return
        if not self.confirm_discard():
            return
        self._load_playlist(Path(path))

    def _new_playlist(self):
        if not self.confirm_discard():
            return
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name.strip():
            return
        name = name.strip()
        if not name.endswith((".m3u", ".m3u8")):
            name += ".m3u"
        dest = self.config.music_dir / name
        if dest.exists():
            QMessageBox.warning(self, "Already Exists", f"{dest.name} already exists.")
            return
        dest.write_text("#EXTM3U\n", encoding="utf-8")
        self.scan_playlists()
        self._select_in_list(dest)
        self._load_playlist(dest)

    def _rename_playlist(self):
        item = self._playlist_list.currentItem()
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        name, ok = QInputDialog.getText(self, "Rename Playlist", "New name:", text=path.stem)
        if not ok or not name.strip():
            return
        name = name.strip()
        if not name.endswith((".m3u", ".m3u8")):
            name += path.suffix
        new_path = path.parent / name
        if new_path.exists():
            QMessageBox.warning(self, "Already Exists", f"{new_path.name} already exists.")
            return
        try:
            path.rename(new_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        if self._playlist_path == path:
            self._playlist_path = new_path
            self._path_label.setText(str(new_path))
        self.scan_playlists()
        self._select_in_list(new_path)

    def _delete_playlist(self):
        item = self._playlist_list.currentItem()
        if not item or not item.data(Qt.ItemDataRole.UserRole):
            return
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        reply = QMessageBox.question(
            self,
            "Delete Playlist",
            f"Permanently delete {path.name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            path.unlink()
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self.scan_playlists()
        if self._playlist_path == path:
            self._unload_playlist()

    def _unload_playlist(self):
        self._playlist_path = None
        self._entries = []
        self._dirty = False
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._track_list.clear()
        self._track_stack.setCurrentIndex(0)
        self._path_label.setText("No playlist selected")
        self._stats_label.setText("")
        self._save_btn.setVisible(False)
        self._save_status_label.setVisible(False)
        self._fix_btn.setEnabled(False)

    def _select_in_list(self, path: Path):
        for i in range(self._playlist_list.count()):
            item = self._playlist_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == str(path):
                self._playlist_list.setCurrentItem(item)
                break

    # ── Loading / display ─────────────────────────────────────────────────────

    def _load_playlist(self, path: Path):
        self._playlist_path = path
        self._entries = _parse_m3u(path)
        self._undo_stack.clear()
        self._redo_stack.clear()
        # Mark dirty immediately if any entry uses an absolute path so that
        # saving will rewrite them as relative paths.
        has_absolute = any(Path(e["raw"]).is_absolute() for e in self._entries)
        self._dirty = has_absolute
        # Absolute paths that fall outside the library directory are unreachable;
        # treat them as missing even if the file exists on disk.
        music_dir = self.config.music_dir.resolve()
        for e in self._entries:
            if e["exists"] and Path(e["raw"]).is_absolute():
                try:
                    Path(e["path"]).resolve().relative_to(music_dir)
                except ValueError:
                    e["exists"] = False
        self._path_label.setText(str(path))
        self._fix_btn.setEnabled(True)
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(0)
        self._sort_combo.blockSignals(False)
        self._save_status_label.setVisible(False)
        self._save_btn.setVisible(has_absolute)
        if has_absolute:
            self._save_status_label.setText("Unsaved changes")
            self._save_status_label.setStyleSheet(f"color: {COLORS['warning']};")
            self._save_status_label.setVisible(True)
        self._track_stack.setCurrentIndex(1)
        self._populate_track_list()

    def _populate_track_list(self):
        self._track_list.clear()
        self._entries_snapshot = list(self._entries)
        _handle_color = QColor("#484f58")
        for i, e in enumerate(self._entries):
            status = "✓" if e["exists"] else "✗  Missing"
            item = QTreeWidgetItem(self._track_list, ["⠿", str(i + 1), e["title"], status])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setToolTip(2, e["path"])
            item.setForeground(0, _handle_color)
            color = _OK_COLOR if e["exists"] else _MISS_COLOR
            item.setForeground(3, color)
            if not e["exists"]:
                item.setForeground(2, _MISS_COLOR)
        self._update_stats()

    def _on_rows_moved(self, *_):
        # Push the pre-drag state (snapshot was captured at last populate)
        self._undo_stack.append([dict(e) for e in self._entries_snapshot])
        self._redo_stack.clear()
        snapshot = self._entries_snapshot
        new_entries = []
        for i in range(self._track_list.topLevelItemCount()):
            tree_item = self._track_list.topLevelItem(i)
            orig_idx = tree_item.data(0, Qt.ItemDataRole.UserRole)
            new_entries.append(snapshot[orig_idx])
            tree_item.setText(1, str(i + 1))
        self._entries = new_entries
        self._entries_snapshot = list(new_entries)
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(0)
        self._sort_combo.blockSignals(False)
        self._update_stats()
        self._mark_dirty()

    def _update_stats(self):
        n = len(self._entries)
        missing = sum(1 for e in self._entries if not e["exists"])
        total_secs = sum(e.get("duration") or 0 for e in self._entries if (e.get("duration") or 0) > 0)
        self._stats_label.setText(f"{n} tracks · {n - missing} found · {missing} missing · {_fmt_duration(total_secs)}")

    def _on_track_double_clicked(self, item: QTreeWidgetItem, _col: int):
        path = self._entries[self._track_list.indexOfTopLevelItem(item)]["path"]
        QMessageBox.information(self, "File Path", path)

    # ── Track editing ─────────────────────────────────────────────────────────

    def _add_songs(self):
        if self._playlist_path is None:
            return
        if self._picker_dialog is None:
            self._picker_dialog = _LibraryPickerDialog(self.config, self)
        self._picker_dialog._tree.clearSelection()
        self._picker_dialog._tree.refresh()
        if self._picker_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = self._picker_dialog.selected_paths()
        if not paths:
            return
        self._push_undo()
        self._add_btn.setEnabled(False)
        self._add_progress.setRange(0, len(paths))
        self._add_progress.setValue(0)
        self._add_progress.setVisible(True)
        self._add_worker = _TrackInfoWorker(paths, self)
        self._add_worker.progress.connect(lambda cur, _tot, _msg: self._add_progress.setValue(cur))
        self._add_worker.finished.connect(self._on_add_done)
        self._add_worker.start()

    def _on_add_done(self, new_entries: list):
        self._entries.extend(new_entries)
        self._add_progress.setVisible(False)
        self._add_btn.setEnabled(True)
        self._mark_dirty()
        self._populate_track_list()

    def _remove_selected(self):
        rows = sorted(
            {self._track_list.indexOfTopLevelItem(i) for i in self._track_list.selectedItems()},
            reverse=True,
        )
        if not rows:
            return
        self._push_undo()
        for r in rows:
            if 0 <= r < len(self._entries):
                self._entries.pop(r)
        self._mark_dirty()
        self._populate_track_list()

    def _move_up(self):
        rows = sorted({self._track_list.indexOfTopLevelItem(i) for i in self._track_list.selectedItems()})
        if not rows or rows[0] == 0:
            return
        self._push_undo()
        for r in rows:
            self._entries[r - 1], self._entries[r] = self._entries[r], self._entries[r - 1]
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(0)
        self._sort_combo.blockSignals(False)
        self._mark_dirty()
        self._populate_track_list()
        for r in rows:
            self._track_list.topLevelItem(r - 1).setSelected(True)

    def _move_down(self):
        rows = sorted(
            {self._track_list.indexOfTopLevelItem(i) for i in self._track_list.selectedItems()},
            reverse=True,
        )
        if not rows or rows[0] >= len(self._entries) - 1:
            return
        self._push_undo()
        for r in rows:
            self._entries[r], self._entries[r + 1] = self._entries[r + 1], self._entries[r]
        self._sort_combo.blockSignals(True)
        self._sort_combo.setCurrentIndex(0)
        self._sort_combo.blockSignals(False)
        self._mark_dirty()
        self._populate_track_list()
        for r in rows:
            self._track_list.topLevelItem(r + 1).setSelected(True)

    def _remove_missing(self):
        before = len(self._entries)
        filtered = [e for e in self._entries if e["exists"]]
        removed = before - len(filtered)
        if removed:
            self._push_undo()
            self._entries = filtered
            self._mark_dirty()
            self._populate_track_list()
            QMessageBox.information(self, "Removed", f"Removed {removed} missing track(s).")
        else:
            QMessageBox.information(self, "None Missing", "All tracks are present on disk.")

    def _dedup(self):
        seen: set[str] = set()
        unique = []
        for e in self._entries:
            key = e["path"].lower()
            if key not in seen:
                seen.add(key)
                unique.append(e)
        removed = len(self._entries) - len(unique)
        if removed:
            self._push_undo()
            self._entries = unique
            self._mark_dirty()
            self._populate_track_list()
            QMessageBox.information(self, "Duplicates Removed", f"Removed {removed} duplicate(s).")
        else:
            QMessageBox.information(self, "No Duplicates", "No duplicate entries found.")

    def _sort_tracks(self, index: int):
        if not self._entries or index == 0:
            return
        self._push_undo()
        key_fns = [
            None,
            lambda e: e["title"].lower(),
            lambda e: e["path"].lower(),
            lambda e: e.get("duration") or -1,
        ]
        self._entries.sort(key=key_fns[index])
        self._mark_dirty()
        self._populate_track_list()

    # ── Playlist tools ────────────────────────────────────────────────────────

    def _fix_encoding(self):
        if not self._playlist_path:
            return
        try:
            original = self._playlist_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        fixed = decode_playlist(original)
        if original == fixed:
            QMessageBox.information(self, "Fix Encoding", "No URL-encoded sequences found.")
            return
        changed = sum(1 for a, b in zip(original.splitlines(), fixed.splitlines()) if a != b)
        reply = QMessageBox.question(
            self,
            "Fix Encoding",
            f"{changed} line(s) contain URL-encoded paths. Decode and save?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._playlist_path.write_text(fixed, encoding="utf-8")
            self._load_playlist(self._playlist_path)

    def _save(self):
        if not self._playlist_path:
            return
        try:
            unresolvable = _write_m3u(self._playlist_path, self._entries)
            if unresolvable:
                QMessageBox.warning(
                    self,
                    "Save Warning",
                    f"{len(unresolvable)} track(s) could not be written as relative paths "
                    f"(outside playlist directory) and were stored as absolute paths:\n"
                    + "\n".join(unresolvable[:5])
                    + (f"\n… and {len(unresolvable) - 5} more" if len(unresolvable) > 5 else ""),
                )
            # Reload from the written file so raw/path/exists reflect the saved state.
            self._entries = _parse_m3u(self._playlist_path)
            music_dir = self.config.music_dir.resolve()
            for e in self._entries:
                if e["exists"] and Path(e["raw"]).is_absolute():
                    try:
                        Path(e["path"]).resolve().relative_to(music_dir)
                    except ValueError:
                        e["exists"] = False
            self._dirty = False
            self._save_btn.setVisible(False)
            self._save_status_label.setText("Saved")
            self._save_status_label.setStyleSheet(f"color: {COLORS['success']};")
            self._save_status_label.setVisible(True)
            QTimer.singleShot(3000, lambda: self._save_status_label.setVisible(False))
            self._populate_track_list()
        except Exception as e:
            QMessageBox.critical(self, "Save Error", str(e))

    # ── Event filter ─────────────────────────────────────────────────────────

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            if obj is self._track_list:
                self._remove_selected()
                return True
            if obj is self._playlist_list:
                self._delete_playlist()
                return True
        return super().eventFilter(obj, event)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _push_undo(self):
        self._undo_stack.append([dict(e) for e in self._entries])
        self._redo_stack.clear()

    def _undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append([dict(e) for e in self._entries])
        self._entries = self._undo_stack.pop()
        self._mark_dirty()
        self._populate_track_list()

    def _redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append([dict(e) for e in self._entries])
        self._entries = self._redo_stack.pop()
        self._mark_dirty()
        self._populate_track_list()

    def _mark_dirty(self):
        self._dirty = True
        self._save_status_label.setText("Unsaved changes")
        self._save_status_label.setStyleSheet(f"color: {COLORS['warning']};")
        self._save_status_label.setVisible(True)
        self._save_btn.setVisible(True)

    def _resolve_missing(self):
        missing = [(i, e) for i, e in enumerate(self._entries) if not e["exists"]]
        if not missing:
            QMessageBox.information(self, "None Missing", "All tracks are present on disk.")
            return
        music_dir = self.config.music_dir
        if not music_dir.exists():
            QMessageBox.warning(self, "No Music Directory", "Music directory not found.")
            return
        skip = set(self.config.skip_dirs)
        audio_exts = self.config.audio_extensions
        by_stem: dict[str, list] = {}
        by_title: dict[str, list] = {}
        for f in music_dir.rglob("*"):
            if f.suffix.lower() in audio_exts and not (skip & set(f.relative_to(music_dir).parts)):
                by_stem.setdefault(f.stem.lower(), []).append(f)
                title, _, _, _ = read_quick_meta(f)
                if title and title.lower() != f.stem.lower():
                    by_title.setdefault(title.lower(), []).append(f)
        resolvable = []
        for i, entry in missing:
            key = Path(entry["path"]).stem.lower()
            seen: dict = {}
            for f in by_stem.get(key, []) + by_title.get(key, []):
                seen[f] = None
            candidates = list(seen)
            if len(candidates) == 1:
                resolvable.append((i, str(candidates[0])))
        if not resolvable:
            QMessageBox.information(self, "No Matches",
                                    f"Could not auto-resolve any of {len(missing)} missing track(s).")
            return
        self._push_undo()
        for i, new_path in resolvable:
            self._entries[i]["path"] = new_path
            self._entries[i]["exists"] = True
        self._mark_dirty()
        self._populate_track_list()
        QMessageBox.information(self, "Resolved",
                                f"Resolved {len(resolvable)} of {len(missing)} missing track(s).")

    def confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText("You have unsaved changes to the current playlist.")
        save_btn = msg.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == save_btn:
            self._save()
            return True
        if clicked == discard_btn:
            if self._playlist_path and self._playlist_path.exists():
                self._entries = _parse_m3u(self._playlist_path)
            self._dirty = False
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._save_btn.setVisible(False)
            self._save_status_label.setVisible(False)
            self._populate_track_list()
            return True
        return False

    def hideEvent(self, event):
        super().hideEvent(event)
        self._playlist_list.clearSelection()
        self._unload_playlist()
