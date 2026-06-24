from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QTextEdit, QSplitter,
    QFileDialog, QMessageBox, QGroupBox, QListWidget,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from ..core.config import Config
from ..core.downloader import decode_playlist


class PlaylistTab(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._original_text: str = ""
        self._fixed_text: str = ""
        self._current_path: str | None = None
        self._setup_ui()
        self._scan_playlists()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # ── Left panel: playlist list ────────────────────────────────────────
        left = QWidget()
        left.setMaximumWidth(280)
        left.setMinimumWidth(180)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        lbl = QLabel("Playlists")
        lbl.setObjectName("section_header")
        left_layout.addWidget(lbl)

        self._playlist_list = QListWidget()
        self._playlist_list.itemClicked.connect(self._load_from_list)
        left_layout.addWidget(self._playlist_list)

        list_btns = QHBoxLayout()
        self._scan_btn = QPushButton("Scan Library")
        self._scan_btn.clicked.connect(self._scan_playlists)
        open_btn = QPushButton("Open…")
        open_btn.clicked.connect(self._browse_open)
        list_btns.addWidget(self._scan_btn)
        list_btns.addWidget(open_btn)
        left_layout.addLayout(list_btns)

        outer.addWidget(left)

        # Divider
        div = QWidget()
        div.setFixedWidth(1)
        div.setStyleSheet("background:#30363d;")
        outer.addWidget(div)

        # ── Right panel: before/after view ───────────────────────────────────
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self._file_label = QLabel("No playlist loaded")
        self._file_label.setObjectName("muted")
        self._file_label.setWordWrap(True)
        right_layout.addWidget(self._file_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        mono = QFont("Monospace", 10)

        orig_group = QGroupBox("Original")
        orig_layout = QVBoxLayout(orig_group)
        self._orig_view = QTextEdit()
        self._orig_view.setReadOnly(True)
        self._orig_view.setFont(mono)
        orig_layout.addWidget(self._orig_view)
        splitter.addWidget(orig_group)

        fixed_group = QGroupBox("Fixed (URL Decoded)")
        fixed_layout = QVBoxLayout(fixed_group)
        self._fixed_view = QTextEdit()
        self._fixed_view.setReadOnly(True)
        self._fixed_view.setFont(mono)
        fixed_layout.addWidget(self._fixed_view)
        splitter.addWidget(fixed_group)

        splitter.setSizes([500, 500])
        right_layout.addWidget(splitter)

        self._stats_label = QLabel("")
        self._stats_label.setObjectName("muted")
        right_layout.addWidget(self._stats_label)

        btn_row = QHBoxLayout()
        self._fix_btn = QPushButton("Save Fixed As…")
        self._fix_btn.setObjectName("primary")
        self._fix_btn.setEnabled(False)
        self._fix_btn.clicked.connect(self._save_fixed)
        self._overwrite_btn = QPushButton("Overwrite Original")
        self._overwrite_btn.setObjectName("danger")
        self._overwrite_btn.setEnabled(False)
        self._overwrite_btn.clicked.connect(self._overwrite)
        btn_row.addWidget(self._fix_btn)
        btn_row.addWidget(self._overwrite_btn)
        btn_row.addStretch()
        right_layout.addLayout(btn_row)

        outer.addWidget(right)

    def _scan_playlists(self):
        self._playlist_list.clear()
        music_dir = self.config.music_dir
        if not music_dir.exists():
            return
        found = sorted(music_dir.rglob("*.m3u")) + sorted(music_dir.rglob("*.m3u8"))
        for path in found:
            item = QListWidgetItem(path.name)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(str(path))
            self._playlist_list.addItem(item)
        if not found:
            item = QListWidgetItem("(no playlists found)")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self._playlist_list.addItem(item)

    def _load_from_list(self, item: QListWidgetItem):
        path = item.data(Qt.ItemDataRole.UserRole)
        if path:
            self._load_file(path)

    def _browse_open(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Playlist", str(self.config.music_dir),
            "Playlists (*.m3u *.m3u8);;All Files (*)"
        )
        if path:
            self._load_file(path)

    def _load_file(self, path: str):
        self._current_path = path
        self._file_label.setText(path)
        try:
            self._original_text = Path(path).read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self._fixed_text = decode_playlist(self._original_text)
        self._orig_view.setPlainText(self._original_text)
        self._fixed_view.setPlainText(self._fixed_text)

        orig_lines = self._original_text.splitlines()
        fixed_lines = self._fixed_text.splitlines()
        changed = sum(1 for a, b in zip(orig_lines, fixed_lines) if a != b)
        self._stats_label.setText(f"{len(orig_lines)} lines  |  {changed} line(s) decoded")
        self._fix_btn.setEnabled(True)
        self._overwrite_btn.setEnabled(self._original_text != self._fixed_text)

    def _save_fixed(self):
        if not self._fixed_text or not self._current_path:
            return
        stem = Path(self._current_path).stem
        default = str(Path(self._current_path).with_stem(stem + "_fixed"))
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Fixed Playlist", default,
            "Playlists (*.m3u *.m3u8);;All Files (*)"
        )
        if path:
            Path(path).write_text(self._fixed_text, encoding="utf-8")
            QMessageBox.information(self, "Saved", f"Saved to:\n{path}")
            self._scan_playlists()

    def _overwrite(self):
        reply = QMessageBox.question(
            self, "Overwrite",
            f"Overwrite original file?\n{self._current_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            Path(self._current_path).write_text(self._fixed_text, encoding="utf-8")
            self._original_text = self._fixed_text
            self._orig_view.setPlainText(self._original_text)
            self._overwrite_btn.setEnabled(False)
            self._stats_label.setText("File overwritten — no more differences.")
            QMessageBox.information(self, "Done", "File overwritten.")
