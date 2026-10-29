import csv
import io
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..core import deleted_log
from ..core.fsutil import atomic_write_text

_COLUMNS = [("When", "when"), ("How", "how"), ("Title", "title"), ("Artist", "artist"),
            ("Album", "album"), ("Old path", "path"), ("Now at / replaced by", "replaced_by")]


class DeletedSongsDialog(QDialog):
    """Lists songs deleted in the app or found missing between library scans."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Deleted Songs")
        self.resize(1000, 520)
        layout = QVBoxLayout(self)

        intro = QLabel(
            "Every song whose path changed: deleted in the app (Duplicates, Pull sync with delete), "
            "moved or re-encoded by the app, or found moved/renamed/re-encoded/missing between library "
            "scans. Use \"Now at\" to fix playlists and liked songs in other players."
        )
        intro.setObjectName("muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels([c for c, _ in _COLUMNS])
        self._tree.setRootIsDecorated(False)
        self._tree.setSortingEnabled(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for col, width in enumerate([120, 150, 200, 150, 150, 300]):
            self._tree.setColumnWidth(col, width)
        layout.addWidget(self._tree)

        btns = QHBoxLayout()
        export_btn = QPushButton("Export CSV…")
        export_btn.clicked.connect(self._export)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("Clear All")
        clear_btn.clicked.connect(self._clear)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        for b in (export_btn, remove_btn, clear_btn):
            btns.addWidget(b)
        btns.addStretch()
        btns.addWidget(close_btn)
        layout.addLayout(btns)
        self._load()

    def _load(self):
        self._entries = deleted_log.load_log()
        self._tree.clear()
        for i, e in enumerate(self._entries):
            item = QTreeWidgetItem([str(e.get(key, "")) for _, key in _COLUMNS])
            item.setData(0, Qt.ItemDataRole.UserRole, i)
            item.setToolTip(5, e.get("path", ""))
            self._tree.addTopLevelItem(item)
        self._tree.sortItems(0, Qt.SortOrder.DescendingOrder)

    def _export(self):
        if not self._entries:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Deleted Songs", str(Path.home() / "deleted_songs.csv"), "CSV files (*.csv)"
        )
        if not path:
            return
        if not path.lower().endswith(".csv"):
            path += ".csv"
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow([c for c, _ in _COLUMNS])
        for e in self._entries:
            writer.writerow([e.get(key, "") for _, key in _COLUMNS])
        try:
            atomic_write_text(Path(path), buf.getvalue(), encoding="utf-8-sig")
        except OSError as err:
            QMessageBox.critical(self, "Export Failed", str(err))

    def _remove_selected(self):
        rows = {item.data(0, Qt.ItemDataRole.UserRole) for item in self._tree.selectedItems()}
        if not rows:
            return
        deleted_log.save_log([e for i, e in enumerate(self._entries) if i not in rows])
        self._load()

    def _clear(self):
        if not self._entries:
            return
        reply = QMessageBox.question(
            self, "Clear Log", f"Remove all {len(self._entries)} entries from the deleted-songs log?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            deleted_log.save_log([])
            self._load()
