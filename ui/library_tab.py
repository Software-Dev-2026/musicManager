import json
import shutil
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config, LIBRARY_STRUCTURES
from ..core.metadata import (
    clean_file_tags, read_tags, write_tags,
    read_quick_meta, compute_dest_dir, sanitize_name,
)
from ..workers.metadata_worker import NormalizeWorker
from .styles import COLORS, make_vdivider

from ..core.config import _CACHE_DIR
_CACHE_PATH = _CACHE_DIR / "library_cache.json"
_scan_cache: dict | None = None  # in-process shared cache across all LibraryTree instances


def _load_disk_cache() -> dict | None:
    try:
        return json.loads(_CACHE_PATH.read_text())
    except Exception:
        return None


def _save_disk_cache(cache: dict):
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(cache))
    except Exception:
        pass


def _cache_is_fresh(cache: dict, config) -> bool:
    """Return True if the cache matches the current config and the library hasn't changed on disk."""
    if (cache.get("music_dir") != str(config.music_dir)
            or cache.get("structure") != config.library_structure
            or cache.get("show_titles") != config.show_titles):
        return False
    scan_time = cache.get("scan_time", 0)
    try:
        music_dir = config.music_dir
        if music_dir.stat().st_mtime > scan_time:
            return False
        for d in music_dir.iterdir():
            if d.is_dir() and d.stat().st_mtime > scan_time:
                return False
    except Exception:
        return False
    return True


class _ScanThread(QThread):
    done = pyqtSignal(list)

    def __init__(self, music_dir: Path, skip_dirs: set, extensions: set, structure: str, show_titles: bool):
        super().__init__()
        self.music_dir = music_dir
        self.skip_dirs = skip_dirs
        self.extensions = extensions
        self.structure = structure
        self.show_titles = show_titles
        self._cancelled = False

    def _meta(self, path: Path) -> tuple[str, str, bool]:
        if self.show_titles:
            title, artist, _, missing = read_quick_meta(path)
            return title, artist, missing
        return path.stem, "", False

    def run(self):
        results = []
        if not self.music_dir.exists():
            self.done.emit(results)
            return

        if self.structure == "flat":
            tracks = []
            for item in sorted(self.music_dir.rglob("*")):
                if self._cancelled:
                    return
                if self.skip_dirs & set(item.relative_to(self.music_dir).parts):
                    continue
                if item.is_file() and item.suffix.lower() in self.extensions:
                    title, artist, missing = self._meta(item)
                    tracks.append((title, str(item), missing))
            results.append(("All Tracks", [], tracks))
            self.done.emit(results)
            return

        for artist_dir in sorted(self.music_dir.iterdir()):
            if self._cancelled:
                return
            if not artist_dir.is_dir() or artist_dir.name in self.skip_dirs:
                continue
            albums = []
            singles = []

            if self.structure == "artist":
                for child in sorted(artist_dir.rglob("*")):
                    if self._cancelled:
                        return
                    if child.is_file() and child.suffix.lower() in self.extensions:
                        title, _, missing = self._meta(child)
                        singles.append((title, str(child), missing))
            else:  # artist/album
                for child in sorted(artist_dir.iterdir()):
                    if self._cancelled:
                        return
                    if child.is_dir():
                        songs = []
                        for f in sorted(child.iterdir()):
                            if self._cancelled:
                                return
                            if f.is_file() and f.suffix.lower() in self.extensions:
                                title, _, missing = self._meta(f)
                                songs.append((title, str(f), missing))
                        if songs:
                            albums.append((child.name, songs))
                    elif child.is_file() and child.suffix.lower() in self.extensions:
                        title, _, missing = self._meta(child)
                        singles.append((title, str(child), missing))

            results.append((artist_dir.name, albums, singles))
        self.done.emit(results)


class LibraryTree(QTreeWidget):
    """Reusable tree widget for browsing the music library."""

    file_activated = pyqtSignal(str)
    file_opened = pyqtSignal(str)
    scan_complete = pyqtSignal()

    def __init__(self, config: Config, compact: bool = False, multi_select: bool = False, parent=None):
        super().__init__(parent)
        self.config = config
        self.compact = compact
        self.setHeaderHidden(True)
        self.setAlternatingRowColors(True)
        if multi_select:
            self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if not compact:
            self.itemDoubleClicked.connect(self._on_double_click)
        self.itemSelectionChanged.connect(self._on_selection)
        self._thread: _ScanThread | None = None

    def refresh(self):
        global _scan_cache
        if self._thread is not None:
            self._thread._cancelled = True
            try:
                self._thread.done.disconnect(self._populate)
            except (TypeError, RuntimeError):
                pass
            self._thread = None

        if _scan_cache is None:
            _scan_cache = _load_disk_cache()
        if _scan_cache and _cache_is_fresh(_scan_cache, self.config):
            self._populate(_scan_cache["results"])
            return

        self.clear()
        thread = _ScanThread(
            self.config.music_dir,
            set(self.config.skip_dirs),
            self.config.audio_extensions,
            self.config.library_structure,
            self.config.show_titles,
        )
        thread.done.connect(self._populate)
        thread.start()
        self._thread = thread

    def _populate(self, results: list):
        global _scan_cache
        _scan_cache = {
            "music_dir": str(self.config.music_dir),
            "structure": self.config.library_structure,
            "show_titles": self.config.show_titles,
            "scan_time": time.time(),
            "results": results,
        }
        _save_disk_cache(_scan_cache)

        self.clear()
        structure = self.config.library_structure
        warn_brush = QBrush(QColor(COLORS["warning"]))

        if structure == "flat":
            if results:
                for title, filepath, missing in results[0][2]:
                    item = QTreeWidgetItem(self, [title])
                    item.setData(0, Qt.ItemDataRole.UserRole, filepath)
                    if missing:
                        item.setForeground(0, warn_brush)
                        item.setToolTip(0, "Missing title or artist metadata")
            self.scan_complete.emit()
            return

        for artist_name, albums, singles in results:
            artist_item = QTreeWidgetItem(self, [artist_name])
            artist_item.setData(0, Qt.ItemDataRole.UserRole, None)
            f = artist_item.font(0)
            f.setBold(True)
            artist_item.setFont(0, f)

            for album_name, songs in albums:
                album_item = QTreeWidgetItem(artist_item, [f"  {album_name}"])
                album_item.setData(0, Qt.ItemDataRole.UserRole, None)
                for title, filepath, missing in songs:
                    song_item = QTreeWidgetItem(album_item, [f"    {title}"])
                    song_item.setData(0, Qt.ItemDataRole.UserRole, filepath)
                    if missing:
                        song_item.setForeground(0, warn_brush)
                        song_item.setToolTip(0, "Missing title or artist metadata")
                        album_item.setForeground(0, warn_brush)

            for title, filepath, missing in singles:
                si = QTreeWidgetItem(artist_item, [f"  {title}"])
                si.setData(0, Qt.ItemDataRole.UserRole, filepath)
                if missing:
                    si.setForeground(0, warn_brush)
                    si.setToolTip(0, "Missing title or artist metadata")

        self.scan_complete.emit()

    def filter(self, text: str):
        text = text.lower()
        for i in range(self.topLevelItemCount()):
            artist = self.topLevelItem(i)
            artist_visible = False
            for j in range(artist.childCount()):
                child = artist.child(j)
                if child.childCount() == 0:
                    match = not text or text in child.text(0).lower() or text in artist.text(0).lower()
                    child.setHidden(not match)
                    if match:
                        artist_visible = True
                else:
                    album_visible = False
                    for k in range(child.childCount()):
                        song = child.child(k)
                        match = not text or (
                            text in song.text(0).lower()
                            or text in child.text(0).lower()
                            or text in artist.text(0).lower()
                        )
                        song.setHidden(not match)
                        if match:
                            album_visible = True
                    child.setHidden(not album_visible)
                    if album_visible:
                        artist_visible = True
            artist.setHidden(not artist_visible)

    def _on_selection(self):
        items = self.selectedItems()
        if items:
            fp = items[0].data(0, Qt.ItemDataRole.UserRole)
            if fp:
                self.file_activated.emit(fp)

    def _on_double_click(self, item: QTreeWidgetItem, _col: int):
        fp = item.data(0, Qt.ItemDataRole.UserRole)
        if fp:
            self.file_opened.emit(fp)


class LibraryTab(QWidget):
    """Combined library browser + metadata editor."""

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._current_file: str | None = None
        self._worker: NormalizeWorker | None = None
        self._setup_ui()
        self.refresh()

    # ── Layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        outer.addWidget(self._build_left_panel())

        outer.addWidget(make_vdivider())

        outer.addWidget(self._build_right_panel())

    def _build_left_panel(self) -> QWidget:
        left = QWidget()
        left.setMinimumWidth(200)
        left.setMaximumWidth(320)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(8, 8, 4, 8)
        layout.setSpacing(6)

        hdr = QLabel("Library")
        hdr.setObjectName("section_header")
        layout.addWidget(hdr)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search artists, albums, tracks…")
        self._search.textChanged.connect(self._filter_tree)
        layout.addWidget(self._search)

        struct_row = QHBoxLayout()
        struct_row.addWidget(QLabel("View:"))
        self._struct_combo = QComboBox()
        for s in LIBRARY_STRUCTURES:
            self._struct_combo.addItem(s.replace("/", " / "), s)
        saved = self.config.library_structure
        if saved in LIBRARY_STRUCTURES:
            self._struct_combo.setCurrentIndex(LIBRARY_STRUCTURES.index(saved))
        self._struct_combo.currentIndexChanged.connect(self._on_struct_change)
        struct_row.addWidget(self._struct_combo, 1)
        layout.addLayout(struct_row)

        self._tree = LibraryTree(self.config, compact=True)
        self._tree.file_activated.connect(self.load_file)
        self._tree.scan_complete.connect(lambda: self._status_label.setText(str(self.config.music_dir)))
        layout.addWidget(self._tree)

        self._status_label = QLabel("")
        self._status_label.setObjectName("muted")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh)
        layout.addWidget(refresh_btn)

        return left

    def _build_right_panel(self) -> QWidget:
        right = QWidget()
        layout = QVBoxLayout(right)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        self._file_label = QLabel("Select a file from the library")
        self._file_label.setObjectName("muted")
        self._file_label.setWordWrap(True)
        layout.addWidget(self._file_label)

        self._missing_banner = QLabel("⚠  Missing metadata — fill in the fields below.")
        self._missing_banner.setStyleSheet(
            f"color: {COLORS['warning']}; padding: 6px; border: 1px solid {COLORS['warning']}; border-radius: 4px;"
        )
        self._missing_banner.setVisible(False)
        layout.addWidget(self._missing_banner)

        # Tag editor
        editor_group = QGroupBox("Tags")
        form = QFormLayout(editor_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self._fields: dict[str, QWidget] = {}
        for key, label in [
            ("title", "Title"),
            ("artist", "Artist"),
            ("albumartist", "Album Artist"),
            ("album", "Album"),
            ("genre", "Genre"),
            ("comment", "Comment"),
        ]:
            w = QLineEdit()
            w.setPlaceholderText(label)
            self._fields[key] = w
            form.addRow(label + ":", w)

        num_row = QHBoxLayout()
        self._fields["tracknumber"] = QSpinBox()
        self._fields["tracknumber"].setRange(0, 999)
        self._fields["discnumber"] = QSpinBox()
        self._fields["discnumber"].setRange(0, 99)
        self._fields["year"] = QSpinBox()
        self._fields["year"].setRange(0, 9999)
        for lbl_text, key in [("Track:", "tracknumber"), ("Disc:", "discnumber"), ("Year:", "year")]:
            num_row.addWidget(QLabel(lbl_text))
            num_row.addWidget(self._fields[key])
            num_row.addSpacing(12)
        num_row.addStretch()
        form.addRow("", num_row)

        self._fields["compilation"] = QCheckBox("Compilation")
        form.addRow("", self._fields["compilation"])
        layout.addWidget(editor_group)

        # File info
        info_group = QGroupBox("File Info")
        info_row = QHBoxLayout(info_group)
        self._info_labels: dict[str, QLabel] = {}
        for key in ("format", "size", "duration", "bitrate"):
            lbl = QLabel("—")
            lbl.setObjectName("muted")
            self._info_labels[key] = lbl
            info_row.addWidget(QLabel(key.title() + ":"))
            info_row.addWidget(lbl)
            info_row.addSpacing(12)
        info_row.addStretch()
        layout.addWidget(info_group)

        # Action buttons
        btn_row = QHBoxLayout()
        self._save_btn = QPushButton("Save Tags")
        self._save_btn.setObjectName("primary")
        self._save_btn.setEnabled(False)
        self._save_btn.clicked.connect(self._save)
        self._clean_btn = QPushButton("Clean Tags")
        self._clean_btn.setEnabled(False)
        self._clean_btn.clicked.connect(self._clean)
        self._reload_btn = QPushButton("Reload")
        self._reload_btn.setEnabled(False)
        self._reload_btn.clicked.connect(lambda: self.load_file(self._current_file))
        for btn in (self._save_btn, self._clean_btn, self._reload_btn):
            btn_row.addWidget(btn)
        self._save_status_label = QLabel("")
        self._save_status_label.setVisible(False)
        btn_row.addSpacing(12)
        btn_row.addWidget(self._save_status_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Batch normalize
        batch_group = QGroupBox("Batch Normalize Library")
        batch_layout = QVBoxLayout(batch_group)
        desc = QLabel("Sets artist / album artist / album for every file based on the folder structure.")
        desc.setObjectName("muted")
        desc.setWordWrap(True)
        batch_layout.addWidget(desc)
        batch_btns = QHBoxLayout()
        self._batch_btn = QPushButton("Run Batch Normalize")
        self._batch_btn.clicked.connect(self._batch_normalize)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel_batch)
        batch_btns.addWidget(self._batch_btn)
        batch_btns.addWidget(self._cancel_btn)
        batch_btns.addStretch()
        batch_layout.addLayout(batch_btns)
        self._batch_progress = QProgressBar()
        self._batch_progress.setVisible(False)
        batch_layout.addWidget(self._batch_progress)
        self._batch_log = QTextEdit()
        self._batch_log.setReadOnly(True)
        self._batch_log.setMaximumHeight(100)
        batch_layout.addWidget(self._batch_log)
        layout.addWidget(batch_group)
        layout.addStretch()

        return right

    # ── Tree / refresh ────────────────────────────────────────────────────────

    def refresh(self):
        self._status_label.setText("Scanning…")
        saved = self.config.library_structure
        if saved in LIBRARY_STRUCTURES:
            self._struct_combo.blockSignals(True)
            self._struct_combo.setCurrentIndex(LIBRARY_STRUCTURES.index(saved))
            self._struct_combo.blockSignals(False)
        self._tree.config = self.config
        self._tree.refresh()

    def _on_struct_change(self, _idx: int):
        self.config.set("library_structure", self._struct_combo.currentData())
        self.config.save()
        self.refresh()

    def _filter_tree(self, text: str):
        self._tree.filter(text)

    # ── File loading ──────────────────────────────────────────────────────────

    def load_file(self, filepath: str):
        if not filepath:
            return
        self._current_file = filepath
        self._file_label.setText(filepath)
        tags = read_tags(filepath)
        path = Path(filepath)

        missing = not tags.get("title") or not tags.get("artist")
        self._missing_banner.setVisible(missing)

        for key, widget in self._fields.items():
            val = tags.get(key, "")
            if isinstance(widget, QLineEdit):
                widget.setText(str(val))
            elif isinstance(widget, QSpinBox):
                try:
                    widget.setValue(int(val) if str(val).strip() else 0)
                except (ValueError, TypeError):
                    widget.setValue(0)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(bool(val) and str(val) not in ("", "0", "False"))

        self._info_labels["format"].setText(path.suffix.upper().lstrip("."))
        size = tags.get("_size", 0)
        self._info_labels["size"].setText(f"{size / 1024 / 1024:.2f} MB")
        dur = tags.get("_duration", 0)
        self._info_labels["duration"].setText(f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "—")
        br = tags.get("_bitrate", 0)
        self._info_labels["bitrate"].setText(f"{br // 1000} kbps" if br else "—")

        self._save_btn.setEnabled(True)
        self._clean_btn.setEnabled(True)
        self._reload_btn.setEnabled(True)

    # ── Tag actions ───────────────────────────────────────────────────────────

    def _collect_tags(self) -> dict:
        result = {}
        for key, widget in self._fields.items():
            if isinstance(widget, QLineEdit):
                result[key] = widget.text()
            elif isinstance(widget, QSpinBox):
                result[key] = widget.value()
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()
        return result

    def _prune_empty_dirs(self, directory: Path):
        music_dir = self.config.music_dir.resolve()
        path = directory.resolve()
        while path != music_dir and path.is_relative_to(music_dir):
            try:
                path.rmdir()  # fails (OSError) if not empty — no iterdir needed
            except OSError:
                break
            path = path.parent

    def _dest_path(self, current: Path, tags: dict) -> Path:
        return compute_dest_dir(
            self.config.music_dir,
            self.config.library_structure,
            tags.get("artist", ""),
            tags.get("album", ""),
        ) / current.name

    def _save(self):
        if not self._current_file:
            return
        tags = self._collect_tags()
        if not write_tags(self._current_file, tags):
            QMessageBox.warning(self, "Error", "Failed to save tags.")
            return
        self._missing_banner.setVisible(False)

        current = Path(self._current_file)
        dest = self._dest_path(current, tags)
        if dest == current:
            self._show_saved()
            self.refresh()
            return

        if dest.exists():
            QMessageBox.warning(self, "Saved (Not Moved)", f"Tags saved, but a file already exists at:\n{dest}")
            self.refresh()
            return

        moved = False
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(dest))
            self._current_file = str(dest)
            self._file_label.setText(str(dest))
            self._prune_empty_dirs(current.parent)
            moved = True
        except Exception as e:
            QMessageBox.warning(self, "Saved (Move Failed)", f"Tags saved but could not move file:\n{e}")
        if moved:
            self._show_saved("Saved — file moved")
        self.refresh()

    def _show_saved(self, text="Saved"):
        self._save_status_label.setText(text)
        self._save_status_label.setStyleSheet(f"color: {COLORS['success']};")
        self._save_status_label.setVisible(True)
        QTimer.singleShot(3000, lambda: self._save_status_label.setVisible(False))

    def _clean(self):
        if not self._current_file:
            return
        try:
            clean_file_tags(self._current_file)
            QMessageBox.information(self, "Done", "Tags cleaned.")
            self.load_file(self._current_file)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Batch normalize ───────────────────────────────────────────────────────

    def _batch_normalize(self):
        if self._worker and self._worker.isRunning():
            return
        self._batch_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._batch_progress.setVisible(True)
        self._batch_progress.setValue(0)
        self._batch_log.clear()
        self._worker = NormalizeWorker(
            self.config.music_dir,
            set(self.config.skip_dirs),
            self.config.audio_extensions,
        )
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.log.connect(lambda msg: self._batch_log.append(msg))
        self._worker.finished.connect(self._on_batch_done)
        self._worker.error.connect(self._on_batch_error)
        self._worker.start()

    def _on_batch_progress(self, current: int, total: int, _msg: str):
        if total > 0:
            self._batch_progress.setMaximum(total)
            self._batch_progress.setValue(current)

    def _on_batch_done(self, result: dict):
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._batch_progress.setVisible(False)
        self._batch_log.append(f"\nDone. Changed: {result['changed']}  Skipped: {result['skipped']}")
        self._tree.refresh()
        if self._current_file:
            self.load_file(self._current_file)

    def _on_batch_error(self, msg: str):
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._batch_log.append(f"ERROR: {msg}")

    def _cancel_batch(self):
        if self._worker:
            self._worker.cancel()
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)

    # ── Tab visibility ────────────────────────────────────────────────────────

    def hideEvent(self, event):
        super().hideEvent(event)
        self._tree.clearSelection()
        self._current_file = None
        self._file_label.setText("Select a file from the library")
        self._missing_banner.setVisible(False)
        self._save_btn.setEnabled(False)
        self._clean_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)
        for widget in self._fields.values():
            if isinstance(widget, QLineEdit):
                widget.clear()
            elif isinstance(widget, QSpinBox):
                widget.setValue(0)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(False)
        for lbl in self._info_labels.values():
            lbl.setText("—")
