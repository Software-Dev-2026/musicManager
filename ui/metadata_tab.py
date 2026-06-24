from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QSpinBox, QCheckBox, QLabel, QPushButton,
    QMessageBox, QProgressBar, QTextEdit, QSplitter, QSizePolicy,
)
from PyQt6.QtCore import Qt

from ..core.config import Config
from ..core.metadata import read_tags, write_tags, clean_file_tags
from ..workers.metadata_worker import NormalizeWorker
from .library_tab import LibraryTree


class MetadataTab(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._current_file: str | None = None
        self._worker: NormalizeWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Left panel: library browser ──────────────────────────────────────
        left = QWidget()
        left.setMaximumWidth(320)
        left.setMinimumWidth(200)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(6)

        lbl = QLabel("Library")
        lbl.setObjectName("section_header")
        left_layout.addWidget(lbl)

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.textChanged.connect(self._filter_tree)
        left_layout.addWidget(self._search)

        self._tree = LibraryTree(self.config, compact=True)
        self._tree.file_activated.connect(self.load_file)
        left_layout.addWidget(self._tree)

        refresh_btn = QPushButton("Refresh Library")
        refresh_btn.clicked.connect(self._tree.refresh)
        left_layout.addWidget(refresh_btn)

        outer.addWidget(left)

        # Divider
        divider = QWidget()
        divider.setFixedWidth(1)
        divider.setStyleSheet("background:#30363d;")
        outer.addWidget(divider)

        # ── Right panel: tag editor ──────────────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(12)

        self._file_label = QLabel("Select a file from the library")
        self._file_label.setObjectName("muted")
        self._file_label.setWordWrap(True)
        right_layout.addWidget(self._file_label)

        self._missing_banner = QLabel("⚠  Missing metadata — fill in the fields below.")
        self._missing_banner.setStyleSheet("color: #d29922; padding: 6px; border: 1px solid #d29922; border-radius: 4px;")
        self._missing_banner.setVisible(False)
        right_layout.addWidget(self._missing_banner)

        # Tag editor
        editor_group = QGroupBox("Tags")
        form = QFormLayout(editor_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setSpacing(8)

        self._fields: dict[str, QWidget] = {}
        simple_fields = [
            ("title",       "Title"),
            ("artist",      "Artist"),
            ("albumartist", "Album Artist"),
            ("album",       "Album"),
            ("genre",       "Genre"),
            ("comment",     "Comment"),
        ]
        for key, label in simple_fields:
            widget = QLineEdit()
            widget.setPlaceholderText(label)
            self._fields[key] = widget
            form.addRow(label + ":", widget)

        num_row = QHBoxLayout()
        self._fields["tracknumber"] = QSpinBox()
        self._fields["tracknumber"].setRange(0, 999)
        self._fields["discnumber"] = QSpinBox()
        self._fields["discnumber"].setRange(0, 99)
        self._fields["year"] = QSpinBox()
        self._fields["year"].setRange(0, 9999)
        num_row.addWidget(QLabel("Track:"))
        num_row.addWidget(self._fields["tracknumber"])
        num_row.addSpacing(12)
        num_row.addWidget(QLabel("Disc:"))
        num_row.addWidget(self._fields["discnumber"])
        num_row.addSpacing(12)
        num_row.addWidget(QLabel("Year:"))
        num_row.addWidget(self._fields["year"])
        num_row.addStretch()
        form.addRow("", num_row)
        self._fields["compilation"] = QCheckBox("Compilation")
        form.addRow("", self._fields["compilation"])
        right_layout.addWidget(editor_group)

        # File info
        info_group = QGroupBox("File Info")
        info_layout = QHBoxLayout(info_group)
        self._info_labels: dict[str, QLabel] = {}
        for key in ("format", "size", "duration", "bitrate"):
            lbl2 = QLabel("—")
            lbl2.setObjectName("muted")
            self._info_labels[key] = lbl2
            info_layout.addWidget(QLabel(key.title() + ":"))
            info_layout.addWidget(lbl2)
            info_layout.addSpacing(12)
        info_layout.addStretch()
        right_layout.addWidget(info_group)

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
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._clean_btn)
        btn_row.addWidget(self._reload_btn)
        btn_row.addStretch()
        right_layout.addLayout(btn_row)

        # Batch normalize
        batch_group = QGroupBox("Batch Normalize Library")
        batch_layout = QVBoxLayout(batch_group)
        desc = QLabel(
            "Sets artist / album artist / album for every file based on the folder structure."
        )
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
        self._batch_log.setVisible(False)
        batch_layout.addWidget(self._batch_log)
        right_layout.addWidget(batch_group)
        right_layout.addStretch()

        outer.addWidget(right)

        # Kick off the tree scan
        self._tree.refresh()

    def _filter_tree(self, text: str):
        self._tree.filter(text)

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
        self._info_labels["duration"].setText(
            f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "—"
        )
        br = tags.get("_bitrate", 0)
        self._info_labels["bitrate"].setText(f"{br // 1000} kbps" if br else "—")

        self._save_btn.setEnabled(True)
        self._clean_btn.setEnabled(True)
        self._reload_btn.setEnabled(True)

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

    def _save(self):
        if not self._current_file:
            return
        tags = self._collect_tags()
        ok = write_tags(self._current_file, tags)
        if ok:
            self._missing_banner.setVisible(False)
            QMessageBox.information(self, "Saved", "Tags saved successfully.")
        else:
            QMessageBox.warning(self, "Error", "Failed to save tags.")

    def _clean(self):
        if not self._current_file:
            return
        try:
            clean_file_tags(self._current_file)
            QMessageBox.information(self, "Done", "Tags cleaned.")
            self.load_file(self._current_file)
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _batch_normalize(self):
        self._batch_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._batch_progress.setVisible(True)
        self._batch_progress.setValue(0)
        self._batch_log.setVisible(True)
        self._batch_log.clear()
        self._worker = NormalizeWorker(
            self.config.music_dir, set(self.config.skip_dirs), self.config.audio_extensions,
        )
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.log.connect(lambda msg: self._batch_log.append(msg))
        self._worker.finished.connect(self._on_batch_done)
        self._worker.error.connect(self._on_batch_error)
        self._worker.start()

    def _on_batch_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._batch_progress.setMaximum(total)
            self._batch_progress.setValue(current)

    def _on_batch_done(self, result: dict):
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._batch_log.append(f"\nDone. Changed: {result['changed']}  Skipped: {result['skipped']}")
        self._tree.refresh()

    def _on_batch_error(self, msg: str):
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._batch_log.append(f"ERROR: {msg}")

    def _cancel_batch(self):
        if self._worker:
            self._worker.cancel()
        self._batch_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
