from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGroupBox,
    QComboBox, QCheckBox, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFileDialog, QMessageBox,
    QTreeWidget, QTreeWidgetItem,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor

from ..core.config import Config
from ..core.reencode import (
    detect_formats, collect_reencoding_targets,
    QUALITY_PRESETS, AUDIO_CODECS, check_ffmpeg,
)
from ..workers.reencode_worker import ReencodeWorker
from .styles import COLORS

_LOSSLESS = {".flac", ".wav"}


def _src_kbps(path: Path) -> int | None:
    """Read the actual encoded bitrate of a file in kbps via mutagen, or None."""
    try:
        import mutagen
        f = mutagen.File(path)
        if f and hasattr(f, "info") and hasattr(f.info, "bitrate"):
            return f.info.bitrate // 1000
    except Exception:
        pass
    return None


def _target_kbps(quality_args: list) -> int | None:
    """Parse the target bitrate in kbps from ffmpeg quality_args, or None for VBR/lossless."""
    for i, arg in enumerate(quality_args):
        if arg == "-b:a" and i + 1 < len(quality_args):
            val = quality_args[i + 1]
            if val.endswith("k"):
                try:
                    return int(val[:-1])
                except ValueError:
                    pass
    return None


def _quality_warning(src: Path, target_fmt: str, quality_args: list) -> tuple[str, str]:
    """Return (warning_text, color_hex) for the Quality column.

    Compares source file's actual bitrate against the chosen target quality.
    Empty string means no warning (safe conversion).
    """
    tgt_ext = f".{target_fmt}"
    if tgt_ext in _LOSSLESS:
        return "", ""                                     # → lossless: no quality loss

    src_ext = src.suffix.lower()
    if src_ext in _LOSSLESS:
        return "Lossy conversion", COLORS["error"]        # lossless → lossy

    # Both lossy — compare actual bitrates when available
    src_k = _src_kbps(src)
    tgt_k = _target_kbps(quality_args)

    if src_k is not None and tgt_k is not None:
        if tgt_k < src_k:
            return f"Bitrate loss ({src_k}k → {tgt_k}k)", COLORS["error"]
        return f"Generation loss ({src_k}k → {tgt_k}k)", COLORS["warning"]

    if src_k is not None:
        return f"Generation loss ({src_k}k → VBR)", COLORS["warning"]

    return "Generation loss", COLORS["warning"]           # fallback: lossy → lossy


class ReencodeTab(QWidget):
    reencode_completed = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._worker: ReencodeWorker | None = None
        self._targets: list = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        if not check_ffmpeg():
            warn = QLabel("ffmpeg is not installed. Install with: sudo dnf install ffmpeg")
            warn.setObjectName("status_warn")
            layout.addWidget(warn)

        # Library analysis
        analysis_group = QGroupBox("Library Format Analysis")
        analysis_layout = QVBoxLayout(analysis_group)
        analysis_btns = QHBoxLayout()
        self._analyze_btn = QPushButton("Analyze Library")
        self._analyze_btn.setObjectName("primary")
        self._analyze_btn.clicked.connect(self._analyze)
        analysis_btns.addWidget(self._analyze_btn)
        analysis_btns.addStretch()
        analysis_layout.addLayout(analysis_btns)
        self._analysis_tree = QTreeWidget()
        self._analysis_tree.setHeaderLabels(["Format", "Count", "Percentage"])
        self._analysis_tree.setMaximumHeight(160)
        self._analysis_tree.setColumnWidth(0, 100)
        self._analysis_tree.setColumnWidth(1, 80)
        analysis_layout.addWidget(self._analysis_tree)
        self._recommendation = QLabel("")
        self._recommendation.setObjectName("muted")
        self._recommendation.setWordWrap(True)
        analysis_layout.addWidget(self._recommendation)
        layout.addWidget(analysis_group)

        # Reencode settings
        settings_group = QGroupBox("Reencode Settings")
        settings_layout = QHBoxLayout(settings_group)
        settings_layout.addWidget(QLabel("Target Format:"))
        self._fmt_combo = QComboBox()
        for fmt in AUDIO_CODECS:
            self._fmt_combo.addItem(fmt.upper(), fmt)
        saved_fmt = self.config.get("reencode.target_format")
        keys = list(AUDIO_CODECS.keys())
        if saved_fmt in keys:
            self._fmt_combo.setCurrentIndex(keys.index(saved_fmt))
        self._fmt_combo.currentIndexChanged.connect(self._update_quality_combo)
        settings_layout.addWidget(self._fmt_combo)

        settings_layout.addSpacing(16)
        settings_layout.addWidget(QLabel("Quality:"))
        self._quality_combo = QComboBox()
        self._quality_combo.setMinimumWidth(180)
        settings_layout.addWidget(self._quality_combo)
        self._update_quality_combo()

        settings_layout.addSpacing(16)
        self._delete_cb = QCheckBox("Delete originals after reencode")
        self._delete_cb.setChecked(bool(self.config.get("reencode.delete_originals")))
        settings_layout.addWidget(self._delete_cb)

        # Connect _save_settings only after all three widgets exist
        self._fmt_combo.currentIndexChanged.connect(self._save_settings)
        self._quality_combo.currentIndexChanged.connect(self._save_settings)
        self._delete_cb.toggled.connect(self._save_settings)

        settings_layout.addSpacing(16)
        settings_layout.addWidget(QLabel("Output Dir:"))
        self._output_label = QLabel("Same folder as source")
        self._output_label.setObjectName("muted")
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output)
        clear_btn = QPushButton("Same Folder")
        clear_btn.clicked.connect(lambda: self._output_label.setText("Same folder as source"))
        settings_layout.addWidget(self._output_label)
        settings_layout.addWidget(browse_btn)
        settings_layout.addWidget(clear_btn)
        settings_layout.addStretch()
        layout.addWidget(settings_group)

        # Target files list
        targets_group = QGroupBox("Files to Reencode")
        targets_layout = QVBoxLayout(targets_group)
        find_btns = QHBoxLayout()
        self._find_btn = QPushButton("Find Files")
        self._find_btn.clicked.connect(self._find_targets)
        self._targets_label = QLabel("Click 'Find Files' to see what would be reencoded")
        self._targets_label.setObjectName("muted")
        find_btns.addWidget(self._find_btn)
        find_btns.addWidget(self._targets_label)
        find_btns.addStretch()
        targets_layout.addLayout(find_btns)

        self._targets_list = QTreeWidget()
        self._targets_list.setHeaderLabels(["File", "Format", "Size", "Quality"])
        self._targets_list.setColumnWidth(0, 480)
        self._targets_list.setColumnWidth(1, 70)
        self._targets_list.setColumnWidth(2, 80)
        self._targets_list.setColumnWidth(3, 160)
        self._targets_list.setMaximumHeight(200)
        targets_layout.addWidget(self._targets_list)
        layout.addWidget(targets_group)

        # Action buttons
        btn_row = QHBoxLayout()
        self._start_btn = QPushButton("Start Reencode")
        self._start_btn.setObjectName("primary")
        self._start_btn.setEnabled(False)
        self._start_btn.clicked.connect(self._start_reencode)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._start_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_label = QLabel("")
        self._progress_label.setObjectName("muted")
        self._progress_label.setVisible(False)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._progress_label)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        layout.addWidget(self._log)

    def _save_settings(self, *_):
        fmt = self._fmt_combo.currentData() or "mp3"
        preset = self._quality_combo.currentText()
        self.config.set("reencode.target_format", fmt)
        self.config.set("reencode.quality_preset", preset)
        self.config.set("reencode.delete_originals", self._delete_cb.isChecked())
        self.config.save()

    def _update_quality_combo(self, _=None):
        fmt = self._fmt_combo.currentData() or "mp3"
        self._quality_combo.clear()
        for label, _args in QUALITY_PRESETS.get(fmt, [("Default", [])]):
            self._quality_combo.addItem(label)

    def _browse_output(self):
        path = QFileDialog.getExistingDirectory(self, "Select Output Directory", str(Path.home()))
        if path:
            self._output_label.setText(path)

    def _analyze(self):
        self._analysis_tree.clear()
        counts = detect_formats(
            self.config.music_dir, set(self.config.skip_dirs), self.config.audio_extensions
        )
        total = sum(counts.values()) or 1
        sorted_counts = sorted(counts.items(), key=lambda x: x[1], reverse=True)
        for ext, count in sorted_counts:
            pct = count / total * 100
            item = QTreeWidgetItem(self._analysis_tree, [
                ext.upper().lstrip("."), str(count), f"{pct:.1f}%"
            ])
        if sorted_counts:
            top_fmt = sorted_counts[0][0]
            top_pct = sorted_counts[0][1] / total * 100
            self._recommendation.setText(
                f"Most common format: {top_fmt.upper().lstrip('.')} ({top_pct:.0f}%).  "
                f"Recommend reencoding other formats to match."
            )
            # Pre-select the most common format
            keys = list(AUDIO_CODECS.keys())
            fmt_key = top_fmt.lstrip(".")
            if fmt_key in keys:
                self._fmt_combo.setCurrentIndex(keys.index(fmt_key))

    def _find_targets(self):
        self._targets_list.clear()
        fmt = self._fmt_combo.currentData() or "mp3"
        qi = self._quality_combo.currentIndex()
        presets = QUALITY_PRESETS.get(fmt, [("Default", [])])
        quality_args = presets[qi][1] if qi < len(presets) else []

        self._targets = collect_reencoding_targets(
            self.config.music_dir, set(self.config.skip_dirs),
            self.config.audio_extensions, fmt,
        )
        warn_count = 0
        for f in self._targets:
            size = f.stat().st_size / 1024 / 1024
            warning, color = _quality_warning(f, fmt, quality_args)
            item = QTreeWidgetItem(self._targets_list, [
                str(f), f.suffix.upper().lstrip("."), f"{size:.2f} MB", warning,
            ])
            if color:
                item.setForeground(3, QColor(color))
                warn_count += 1
        warn_note = f"  —  {warn_count} with quality warning" if warn_count else ""
        self._targets_label.setText(
            f"{len(self._targets)} file(s) would be reencoded to {fmt.upper()}{warn_note}"
        )
        self._start_btn.setEnabled(bool(self._targets))

    def _start_reencode(self):
        if not self._targets:
            return
        fmt = self._fmt_combo.currentData() or "mp3"
        qi = self._quality_combo.currentIndex()
        presets = QUALITY_PRESETS.get(fmt, [("Default", [])])
        quality_args = presets[qi][1] if qi < len(presets) else []
        delete = self._delete_cb.isChecked()
        out_text = self._output_label.text()
        output_dir = Path(out_text) if out_text != "Same folder as source" else None

        msg = (
            f"Reencode {len(self._targets)} file(s) to {fmt.upper()}?\n"
            f"Quality: {self._quality_combo.currentText()}\n"
            f"Delete originals: {'Yes' if delete else 'No'}"
        )
        reply = QMessageBox.question(self, "Confirm Reencode", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._find_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_label.setVisible(True)
        self._log.clear()

        self._worker = ReencodeWorker(
            self._targets, fmt, quality_args, output_dir, delete
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(lambda msg: self._log.append(msg))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
            pct = int(current / total * 100)
            self._progress_label.setText(f"[{pct}%] {msg}")

    def _on_done(self, results: dict):
        self._start_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._find_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)
        self._targets_list.clear()
        self._targets.clear()
        self._targets_label.setText("Click 'Find Files' to see what would be reencoded")
        errors = results.get("errors", [])
        self._log.append(
            f"\nDone. Reencoded: {results.get('done', 0)}  Errors: {len(errors)}"
        )
        if errors:
            for e in errors:
                self._log.append(f"  ERROR: {e}")
        self.reencode_completed.emit()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self._start_btn.setEnabled(bool(self._targets))
        self._cancel_btn.setEnabled(False)
        self._find_btn.setEnabled(True)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)

    def _on_error(self, msg: str):
        self._start_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_label.setVisible(False)
        QMessageBox.critical(self, "Reencode Error", msg)
