import shutil
import subprocess
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config
from ..core.downloader import AUDIO_FORMATS, check_ytdlp
from ..core.metadata import read_tags, write_tags, compute_dest_dir
from ..workers.download_worker import DownloadWorker
from .styles import COLORS, make_hdivider

# Flat template: keeps all files in one directory, prefixes with uploader name
_STAGING_TEMPLATE = "%(uploader)s - %(title)s.%(ext)s"


class _QueueItem:
    def __init__(self, url: str):
        self.url = url
        self.status = "queued"  # queued | downloading | done | error
        self.worker: DownloadWorker | None = None


class DownloadTab(QWidget):
    committed = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._queue: list[_QueueItem] = []
        self._active_worker: DownloadWorker | None = None
        self._staging_files: list[Path] = []
        self._setup_ui()
        self._refresh_staging()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _staging_dir(self) -> Path:
        return Path(self.config.get("download.staging_dir"))

    def _ensure_staging_dir(self) -> Path:
        p = self._staging_dir()
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ── Layout ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_download_panel())
        splitter.addWidget(self._build_staging_panel())
        splitter.setSizes([400, 720])
        outer.addWidget(splitter)

    # ── Left panel: download queue ────────────────────────────────────────────

    def _build_download_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(12, 12, 6, 12)
        layout.setSpacing(10)

        if not check_ytdlp():
            warn = QLabel("yt-dlp not installed — pip install yt-dlp")
            warn.setObjectName("status_warn")
            layout.addWidget(warn)

        # URL input
        url_group = QGroupBox("Add URLs")
        url_layout = QHBoxLayout(url_group)
        self._url_input = QLineEdit()
        self._url_input.setPlaceholderText("YouTube / SoundCloud / Bandcamp URL…")
        self._url_input.returnPressed.connect(self._add_url)
        add_btn = QPushButton("Add")
        add_btn.setObjectName("primary")
        add_btn.clicked.connect(self._add_url)
        url_layout.addWidget(self._url_input)
        url_layout.addWidget(add_btn)
        layout.addWidget(url_group)

        # Options
        opts_group = QGroupBox("Options")
        opts_layout = QVBoxLayout(opts_group)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Format:"))
        self._format_combo = QComboBox()
        for fmt in AUDIO_FORMATS:
            self._format_combo.addItem(fmt.upper(), fmt)
        saved_fmt = self.config.get("download.format")
        if saved_fmt in AUDIO_FORMATS:
            self._format_combo.setCurrentIndex(AUDIO_FORMATS.index(saved_fmt))
        self._format_combo.currentIndexChanged.connect(self._save_options)
        fmt_row.addWidget(self._format_combo)
        fmt_row.addStretch()
        opts_layout.addLayout(fmt_row)

        self._thumb_cb = QCheckBox("Embed Thumbnail")
        self._thumb_cb.setChecked(bool(self.config.get("download.embed_thumbnail")))
        self._meta_cb = QCheckBox("Fetch Metadata via yt-dlp")
        self._meta_cb.setChecked(bool(self.config.get("download.add_metadata")))
        self._thumb_cb.toggled.connect(self._save_options)
        self._meta_cb.toggled.connect(self._save_options)
        opts_layout.addWidget(self._thumb_cb)
        opts_layout.addWidget(self._meta_cb)

        staging_row = QHBoxLayout()
        staging_row.addWidget(QLabel("Staging:"))
        self._staging_path_label = QLabel(str(self._staging_dir()))
        self._staging_path_label.setObjectName("muted")
        self._staging_path_label.setWordWrap(False)
        browse_staging = QPushButton("Change…")
        browse_staging.clicked.connect(self._browse_staging)
        staging_row.addWidget(self._staging_path_label, 1)
        staging_row.addWidget(browse_staging)
        opts_layout.addLayout(staging_row)

        layout.addWidget(opts_group)

        # Queue list
        queue_group = QGroupBox("Queue")
        queue_layout = QVBoxLayout(queue_group)
        self._queue_list = QListWidget()
        self._queue_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        queue_layout.addWidget(self._queue_list)

        q_btns = QHBoxLayout()
        dl_all = QPushButton("Download All")
        dl_all.setObjectName("primary")
        dl_all.clicked.connect(self._start_all)
        dl_sel = QPushButton("Download Selected")
        dl_sel.clicked.connect(self._start_selected)
        clear = QPushButton("Clear Done")
        clear.clicked.connect(self._clear_completed)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove_selected)
        for btn in (dl_all, dl_sel, clear, remove):
            q_btns.addWidget(btn)
        queue_layout.addLayout(q_btns)
        layout.addWidget(queue_group)

        # Progress
        self._current_label = QLabel("Idle")
        self._current_label.setObjectName("muted")
        layout.addWidget(self._current_label)
        self._progress_bar = QProgressBar()
        layout.addWidget(self._progress_bar)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

        return w

    # ── Right panel: staging + metadata ──────────────────────────────────────

    def _build_staging_panel(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.setContentsMargins(6, 12, 12, 12)
        layout.setSpacing(8)

        hdr = QLabel("Staging Area")
        hdr.setObjectName("section_header")
        layout.addWidget(hdr)

        # File list
        self._staging_tree = QTreeWidget()
        self._staging_tree.setHeaderLabels(["Filename", "Ext", "Size", "Title (tag)", "Artist (tag)"])
        self._staging_tree.setColumnWidth(0, 220)
        self._staging_tree.setColumnWidth(1, 46)
        self._staging_tree.setColumnWidth(2, 70)
        self._staging_tree.setColumnWidth(3, 180)
        self._staging_tree.setColumnWidth(4, 150)
        self._staging_tree.setAlternatingRowColors(True)
        self._staging_tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._staging_tree.itemSelectionChanged.connect(self._on_staging_selection)
        layout.addWidget(self._staging_tree)

        file_btns = QHBoxLayout()
        for label, slot in [
            ("Select All", self._select_all),
            ("Deselect All", self._deselect_all),
            ("Refresh", self._refresh_staging),
            ("Open Folder", self._open_staging_dir),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(slot)
            file_btns.addWidget(btn)
        self._staging_count = QLabel("")
        self._staging_count.setObjectName("muted")
        file_btns.addStretch()
        file_btns.addWidget(self._staging_count)
        layout.addLayout(file_btns)

        # Divider
        layout.addWidget(make_hdivider())

        # Metadata editor
        self._meta_header = QLabel("Select files above to edit metadata")
        self._meta_header.setObjectName("muted")
        layout.addWidget(self._meta_header)

        meta_group = QGroupBox("Metadata Editor")
        meta_form = QFormLayout(meta_group)
        meta_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        meta_form.setSpacing(6)

        self._meta_fields: dict[str, tuple] = {}

        for key, label in [
            ("title", "Title"),
            ("artist", "Artist"),
            ("albumartist", "Album Artist"),
            ("album", "Album"),
            ("genre", "Genre"),
        ]:
            row = QHBoxLayout()
            edit = QLineEdit()
            cb = QCheckBox("Apply")
            cb.setToolTip("Apply this field to all selected files when saving")
            edit.textChanged.connect(lambda _, _cb=cb: _cb.setChecked(True))
            row.addWidget(edit)
            row.addWidget(cb)
            self._meta_fields[key] = (edit, cb)
            meta_form.addRow(label + ":", row)

        num_row = QHBoxLayout()
        for key, label in [("tracknumber", "Track"), ("year", "Year")]:
            spin = QSpinBox()
            spin.setRange(0, 9999)
            spin.setFixedWidth(72)
            cb = QCheckBox("Apply")
            spin.valueChanged.connect(lambda _, _cb=cb: _cb.setChecked(True))
            self._meta_fields[key] = (spin, cb)
            num_row.addWidget(QLabel(label + ":"))
            num_row.addWidget(spin)
            num_row.addWidget(cb)
            num_row.addSpacing(12)
        num_row.addStretch()
        meta_form.addRow("", num_row)

        layout.addWidget(meta_group)

        save_meta_btn = QPushButton("Save Metadata to Selected Files")
        save_meta_btn.setObjectName("primary")
        save_meta_btn.clicked.connect(self._save_metadata)
        layout.addWidget(save_meta_btn)

        # Divider + commit
        layout.addWidget(make_hdivider())

        commit_hdr = QLabel("Commit to Library")
        commit_hdr.setObjectName("section_header")
        layout.addWidget(commit_hdr)

        commit_desc = QLabel(
            "Moves files from staging into the music library, organised into folders "
            "based on their metadata tags and the current library structure setting."
        )
        commit_desc.setObjectName("muted")
        commit_desc.setWordWrap(True)
        layout.addWidget(commit_desc)

        commit_btns = QHBoxLayout()
        commit_sel = QPushButton("Commit Selected")
        commit_sel.setObjectName("success")
        commit_sel.clicked.connect(self._commit_selected)
        commit_all = QPushButton("Commit All")
        commit_all.setObjectName("success")
        commit_all.clicked.connect(self._commit_all)
        delete_btn = QPushButton("Delete Selected")
        delete_btn.setObjectName("danger")
        delete_btn.clicked.connect(self._delete_from_staging)
        commit_btns.addWidget(commit_sel)
        commit_btns.addWidget(commit_all)
        commit_btns.addWidget(delete_btn)
        commit_btns.addStretch()
        layout.addLayout(commit_btns)

        return w

    # ── Queue logic ───────────────────────────────────────────────────────────

    def _add_url(self):
        url = self._url_input.text().strip()
        if not url:
            return
        self._queue.append(_QueueItem(url))
        self._queue_list.addItem(QListWidgetItem(f"[queued]  {url}"))
        self._url_input.clear()

    def _browse_staging(self):
        path = QFileDialog.getExistingDirectory(self, "Select Staging Directory", str(self._staging_dir()))
        if path:
            self.config.set("download.staging_dir", path)
            self.config.save()
            self._staging_path_label.setText(path)
            self._refresh_staging()

    def _save_options(self, *_):
        self.config.set("download.format", self._format_combo.currentData())
        self.config.set("download.embed_thumbnail", self._thumb_cb.isChecked())
        self.config.set("download.add_metadata", self._meta_cb.isChecked())
        self.config.save()

    def refresh_from_config(self):
        self._staging_path_label.setText(str(self._staging_dir()))
        self._refresh_staging()

    def _start_all(self):
        if self._active_worker and self._active_worker.isRunning():
            return
        self._process_next(indices=None)

    def _start_selected(self):
        if self._active_worker and self._active_worker.isRunning():
            return
        rows = {self._queue_list.row(i) for i in self._queue_list.selectedItems()}
        if not rows:
            QMessageBox.information(self, "None Selected", "Select queue items first.")
            return
        self._process_next(indices=rows)

    def _process_next(self, indices=None):
        for i, item in enumerate(self._queue):
            if indices is not None and i not in indices:
                continue
            if item.status == "queued":
                self._download_item(i, item)
                return
        self._log.append("All downloads complete.")
        self._refresh_staging()

    def _download_item(self, index: int, item: _QueueItem):
        item.status = "downloading"
        self._update_queue_item(index, f"[downloading]  {item.url}")
        self._current_label.setText(item.url)
        self._progress_bar.setValue(0)

        worker = DownloadWorker(
            item.url,
            str(self._ensure_staging_dir()),
            self._format_combo.currentData(),
            self._thumb_cb.isChecked(),
            self._meta_cb.isChecked(),
            output_template=_STAGING_TEMPLATE,
        )
        item.worker = worker
        self._active_worker = worker
        worker.progress.connect(lambda c, _t, _m: self._progress_bar.setValue(c))
        worker.log.connect(lambda msg: self._log.append(msg))
        worker.finished.connect(lambda res: self._on_dl_done(index, item, res))
        worker.error.connect(lambda msg: self._on_dl_error(index, item, msg))
        worker.start()

    def _on_dl_done(self, index: int, item: _QueueItem, result: dict):
        rc = result.get("returncode", 0)
        item.status = "done" if rc == 0 else "error"
        self._update_queue_item(index, f"[{'done' if rc == 0 else f'error rc={rc}'}]  {item.url}")
        self._progress_bar.setValue(100 if rc == 0 else 0)
        self._current_label.setText("Idle")
        self._refresh_staging()
        self._process_next()

    def _on_dl_error(self, index: int, item: _QueueItem, msg: str):
        item.status = "error"
        self._update_queue_item(index, f"[error]  {item.url}")
        self._log.append(f"ERROR: {msg}")
        self._current_label.setText("Idle")
        self._process_next()

    def _update_queue_item(self, index: int, text: str):
        if index < self._queue_list.count():
            self._queue_list.item(index).setText(text)

    def _clear_completed(self):
        to_remove = [i for i, item in enumerate(self._queue) if item.status in ("done", "error")]
        for i in reversed(to_remove):
            self._queue_list.takeItem(i)
            self._queue.pop(i)

    def _remove_selected(self):
        rows = sorted({self._queue_list.row(i) for i in self._queue_list.selectedItems()}, reverse=True)
        for r in rows:
            if r < len(self._queue) and self._queue[r].status != "downloading":
                self._queue_list.takeItem(r)
                self._queue.pop(r)

    # ── Staging list ──────────────────────────────────────────────────────────

    def _refresh_staging(self):
        self._staging_tree.clear()
        self._staging_files.clear()
        staging = self._staging_dir()
        for f in sorted(staging.iterdir()):
            if not (f.is_file() and f.suffix.lower() in self.config.audio_extensions):
                continue
            self._staging_files.append(f)
            tags = read_tags(str(f))
            title = tags.get("title", "")
            artist = tags.get("artist", "")
            size = f"{f.stat().st_size / 1024 / 1024:.2f} MB"
            item = QTreeWidgetItem(
                self._staging_tree,
                [
                    f.name,
                    f.suffix.upper().lstrip("."),
                    size,
                    title,
                    artist,
                ],
            )
            item.setData(0, Qt.ItemDataRole.UserRole, str(f))
            # Amber tint if tags are missing
            if not title or not artist:
                for col in range(5):
                    item.setForeground(col, QColor(COLORS["warning"]))
                item.setToolTip(0, "Missing title or artist — edit metadata before committing")

        n = len(self._staging_files)
        self._staging_count.setText(f"{n} file{'s' if n != 1 else ''} in staging")

    def _on_staging_selection(self):
        sel = self._staging_tree.selectedItems()
        if not sel:
            self._meta_header.setText("Select files above to edit metadata")
            self._clear_meta_form()
            return
        paths = [item.data(0, Qt.ItemDataRole.UserRole) for item in sel]
        if len(paths) == 1:
            self._meta_header.setText(f"Editing: {Path(paths[0]).name}")
            self._load_single_meta(paths[0])
        else:
            self._meta_header.setText(f"Bulk editing {len(paths)} files — check 'Apply' for each field to update")
            self._load_bulk_meta(paths)

    def _clear_meta_form(self):
        for _key, (widget, cb) in self._meta_fields.items():
            widget.blockSignals(True)
            (widget.setText("") if isinstance(widget, QLineEdit) else widget.setValue(0))
            widget.blockSignals(False)
            cb.setChecked(False)

    def _load_single_meta(self, filepath: str):
        tags = read_tags(filepath)
        for key, (widget, cb) in self._meta_fields.items():
            val = tags.get(key, "")
            widget.blockSignals(True)
            if isinstance(widget, QLineEdit):
                widget.setText(str(val))
            else:
                try:
                    widget.setValue(int(val) if str(val).strip() else 0)
                except (ValueError, TypeError):
                    widget.setValue(0)
            widget.blockSignals(False)
            cb.setChecked(False)

    def _load_bulk_meta(self, paths: list):
        all_tags = [read_tags(p) for p in paths]
        for key, (widget, cb) in self._meta_fields.items():
            vals = [str(t.get(key, "") or "") for t in all_tags]
            common = vals[0] if len(set(vals)) == 1 else ""
            widget.blockSignals(True)
            if isinstance(widget, QLineEdit):
                widget.setPlaceholderText("" if common else "(multiple values)")
                widget.setText(common)
            else:
                try:
                    widget.setValue(int(common) if common.strip() else 0)
                except (ValueError, TypeError):
                    widget.setValue(0)
            widget.blockSignals(False)
            cb.setChecked(False)

    def _save_metadata(self):
        sel = self._staging_tree.selectedItems()
        if not sel:
            QMessageBox.information(self, "Nothing Selected", "Select staging files to edit.")
            return

        tags_to_write = {}
        for key, (widget, cb) in self._meta_fields.items():
            if not cb.isChecked():
                continue
            tags_to_write[key] = widget.text() if isinstance(widget, QLineEdit) else widget.value()

        if not tags_to_write:
            QMessageBox.information(self, "Nothing to Apply", "Check 'Apply' next to at least one field.")
            return

        errors = []
        for item in sel:
            fp = item.data(0, Qt.ItemDataRole.UserRole)
            if not write_tags(fp, tags_to_write):
                errors.append(Path(fp).name)

        if errors:
            QMessageBox.warning(self, "Some Failed", "Could not write to:\n" + "\n".join(errors))
        else:
            QMessageBox.information(self, "Saved", f"Metadata saved to {len(sel)} file(s).")
        self._refresh_staging()

    def _select_all(self):
        self._staging_tree.selectAll()

    def _deselect_all(self):
        self._staging_tree.clearSelection()

    def _open_staging_dir(self):
        try:
            subprocess.Popen(["xdg-open", str(self._staging_dir())])
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    # ── Commit logic ──────────────────────────────────────────────────────────

    def _selected_paths(self) -> list[Path]:
        return [Path(i.data(0, Qt.ItemDataRole.UserRole)) for i in self._staging_tree.selectedItems()]

    def _commit_selected(self):
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select files to commit.")
            return
        self._do_commit(paths)

    def _commit_all(self):
        if not self._staging_files:
            QMessageBox.information(self, "Empty Staging", "No files in staging.")
            return
        self._do_commit(list(self._staging_files))

    def _do_commit(self, paths: list[Path]):
        music_dir = self.config.music_dir
        structure = self.config.library_structure
        committed, errors = [], []

        for src in paths:
            if not src.exists():
                continue
            tags = read_tags(str(src))
            dest_dir = compute_dest_dir(
                music_dir, structure,
                tags.get("artist", ""),
                tags.get("album", ""),
            )

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src.name
            if dest.exists():
                n = 1
                while (dest_dir / f"{src.stem}_{n}{src.suffix}").exists():
                    n += 1
                dest = dest_dir / f"{src.stem}_{n}{src.suffix}"

            try:
                shutil.move(str(src), str(dest))
                committed.append(dest)
                self._log.append(f"[committed] {src.name} → {dest.parent}")
            except Exception as e:
                errors.append(f"{src.name}: {e}")

        if errors:
            QMessageBox.warning(
                self, "Commit Errors", f"{len(committed)} committed, {len(errors)} failed:\n" + "\n".join(errors)
            )
        else:
            QMessageBox.information(self, "Done", f"Committed {len(committed)} file(s) to the library.")
        self._refresh_staging()
        if committed:
            self.committed.emit()

    def _delete_from_staging(self):
        paths = self._selected_paths()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Select files to delete.")
            return
        reply = QMessageBox.question(
            self,
            "Delete from Staging",
            f"Permanently delete {len(paths)} file(s) from staging?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for p in paths:
                try:
                    p.unlink()
                except Exception:
                    pass
            self._refresh_staging()
