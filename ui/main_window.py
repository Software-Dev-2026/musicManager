from pathlib import Path

from PyQt6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QLabel,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QPen, QBrush

from .library_tab import LibraryTab
from .duplicates_tab import DuplicatesTab
from .sync_tab import SyncTab
from .download_tab import DownloadTab
from .playlist_tab import PlaylistTab
from .settings_tab import SettingsTab
from .reencode_tab import ReencodeTab
from ..core.config import Config

_SVG_PATH = Path(__file__).parent.parent / "icon.svg"


def _load_icon() -> QIcon:
    """Load icon.svg; fall back to a QPainter-drawn icon if SVG is unavailable."""
    if _SVG_PATH.exists():
        icon = QIcon(str(_SVG_PATH))
        if not icon.isNull():
            return icon
    return _fallback_icon()


def _fallback_icon() -> QIcon:
    px = QPixmap(64, 64)
    px.fill(QColor(0, 0, 0, 0))
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor("#1f6feb")))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(2, 2, 60, 60)
    p.setBrush(QBrush(QColor("white")))
    p.setPen(QPen(QColor("white"), 1))
    p.drawEllipse(10, 42, 18, 12)
    p.drawRect(27, 13, 4, 32)
    p.drawLine(31, 13, 50, 21)
    p.drawLine(50, 21, 31, 27)
    p.drawLine(31, 27, 50, 35)
    p.drawLine(50, 35, 31, 40)
    p.end()
    return QIcon(px)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = Config()
        self.setWindowTitle("Music Manager")
        self.setWindowIcon(_load_icon())
        self.setMinimumSize(1100, 700)
        self.resize(1340, 820)

        self._build_tabs()
        self._build_statusbar()
        self._connect_cross_tab()

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QTabWidget.TabPosition.North)

        self.library_tab    = LibraryTab(self.config)
        self.duplicates_tab = DuplicatesTab(self.config)
        self.sync_tab       = SyncTab(self.config)
        self.download_tab   = DownloadTab(self.config)
        self.playlist_tab   = PlaylistTab(self.config)
        self.reencode_tab   = ReencodeTab(self.config)
        self.settings_tab   = SettingsTab(self.config)

        self.tabs.addTab(self.library_tab,    "  Library  ")
        self.tabs.addTab(self.duplicates_tab, "  Duplicates  ")
        self.tabs.addTab(self.sync_tab,       "  Sync  ")
        self.tabs.addTab(self.download_tab,   "  Download  ")
        self.tabs.addTab(self.playlist_tab,   "  Playlists  ")
        self.tabs.addTab(self.reencode_tab,   "  Reencode  ")
        self.tabs.addTab(self.settings_tab,   "  Settings  ")

        self.setCentralWidget(self.tabs)

    def _build_statusbar(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._status_msg = QLabel("Ready")
        self._music_dir_label = QLabel()
        self._update_dir_label()
        self.status_bar.addWidget(self._status_msg)
        self.status_bar.addPermanentWidget(self._music_dir_label)

    def _update_dir_label(self):
        self._music_dir_label.setText(f"Music: {self.config.music_dir}")

    def _connect_cross_tab(self):
        self.settings_tab.settings_saved.connect(self._on_settings_saved)
        self.reencode_tab.reencode_completed.connect(self.library_tab.refresh)

    def _on_settings_saved(self):
        self.config.load()
        self._update_dir_label()
        self.library_tab.refresh()
        self.download_tab.refresh_from_config()
        self.sync_tab.refresh_from_config()
        self.duplicates_tab.refresh_from_config()
        self.playlist_tab._scan_playlists()

    def closeEvent(self, event):
        if not self.playlist_tab._confirm_discard():
            event.ignore()
            return
        event.accept()

    def set_status(self, msg: str, timeout_ms: int = 4000):
        self._status_msg.setText(msg)
        if timeout_ms > 0:
            QTimer.singleShot(timeout_ms, lambda: self._status_msg.setText("Ready"))
