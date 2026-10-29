from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config, encrypt_password
from .styles import COLORS

_NEXT_LABELS = ["Get Started  →", "Next  →", "Next  →", "Open Music Manager"]


def _h(text: str, size: int = 18) -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{COLORS['text_heading']}; font-size:{size}px; font-weight:600;")
    return lbl


def _muted(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    lbl.setWordWrap(True)
    return lbl


class _StepBar(QWidget):
    _STEPS = ["Welcome", "Library", "Sync", "Done"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current = 0
        self.setFixedHeight(46)
        self.setStyleSheet(f"background:{COLORS['bg_secondary']};border-bottom:1px solid {COLORS['border']};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(32, 0, 32, 0)
        lay.setSpacing(0)
        self._items: list[QLabel] = []
        for i, name in enumerate(self._STEPS):
            if i:
                sep = QLabel("  ›  ")
                sep.setStyleSheet(f"color:{COLORS['border']};")
                lay.addWidget(sep)
            lbl = QLabel(f"{i + 1}. {name}")
            lay.addWidget(lbl)
            self._items.append(lbl)
        lay.addStretch()
        self._refresh()

    def set_step(self, idx: int):
        self._current = idx
        self._refresh()

    def _refresh(self):
        for i, lbl in enumerate(self._items):
            if i < self._current:
                lbl.setStyleSheet(f"color:{COLORS['success']};")
            elif i == self._current:
                lbl.setStyleSheet(f"color:{COLORS['accent']}; font-weight:600;")
            else:
                lbl.setStyleSheet(f"color:{COLORS['text_secondary']};")


class FirstRunWizard(QDialog):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self.setWindowTitle("Music Manager — Setup")
        self.setMinimumSize(660, 520)
        self.resize(680, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._step_bar = _StepBar()
        root.addWidget(self._step_bar)

        self._stack = QStackedWidget()
        self._stack.addWidget(self._page_welcome())
        self._stack.addWidget(self._page_library())
        self._stack.addWidget(self._page_sync())
        self._stack.addWidget(self._page_done())
        root.addWidget(self._stack, 1)

        # ── Nav bar ──────────────────────────────────────────────────────────
        bar = QWidget()
        bar.setStyleSheet(f"background:{COLORS['bg_secondary']};border-top:1px solid {COLORS['border']};")
        nav = QHBoxLayout(bar)
        nav.setContentsMargins(24, 12, 24, 12)

        self._skip_btn = QPushButton("Skip Setup")
        self._skip_btn.clicked.connect(self._finish)

        self._back_btn = QPushButton("←  Back")
        self._back_btn.setVisible(False)
        self._back_btn.clicked.connect(self._go_back)

        self._next_btn = QPushButton(_NEXT_LABELS[0])
        self._next_btn.setObjectName("primary")
        self._next_btn.setMinimumWidth(160)
        self._next_btn.clicked.connect(self._go_next)

        nav.addWidget(self._skip_btn)
        nav.addStretch()
        nav.addWidget(self._back_btn)
        nav.addSpacing(8)
        nav.addWidget(self._next_btn)
        root.addWidget(bar)

    # ── Pages ─────────────────────────────────────────────────────────────────

    def _page_welcome(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(52, 40, 52, 28)
        lay.setSpacing(10)

        lay.addWidget(_h("Welcome to Music Manager", 22))
        lay.addWidget(
            _muted("A local-first desktop app for organising, syncing, and maintaining your music on device.")
        )
        lay.addSpacing(10)

        lay.addWidget(QLabel("What you can do:"))

        for name, desc in [
            ("Library", "Browse and organise your local music files."),
            ("Sync", "Mirror your library to a share, or local folder."),
            ("Download", "Grab audio from YouTube, Bandcamp, and SoundCloud via yt-dlp."),
            ("Playlists", "Create and manage M3U playlists."),
            ("Duplicates", "Find duplicate tracks using acoustic fingerprinting."),
            ("Re-encode", "Convert files between MP3, FLAC, AAC, OGG, and more."),
        ]:
            row = QHBoxLayout()
            row.setSpacing(10)
            dot = QLabel("●")
            dot.setFixedWidth(12)
            dot.setStyleSheet(f"color:{COLORS['accent']};")
            dot.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
            lbl = QLabel(f"<b>{name}</b> — {desc}")
            lbl.setObjectName("muted")
            row.addWidget(dot)
            row.addWidget(lbl, 1)
            lay.addLayout(row)

        lay.addStretch()
        lay.addWidget(
            _muted("The next two steps cover the essentials. Everything can be changed later in the Settings tab.")
        )
        return w

    def _page_library(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(52, 36, 52, 28)
        lay.setSpacing(14)

        lay.addWidget(_h("Music Library"))

        lay.addWidget(QLabel("Where is your music stored?"))
        dir_row = QHBoxLayout()
        self._dir_edit = QLineEdit(str(self.config.music_dir))
        self._dir_edit.setPlaceholderText(str(self.config.music_dir))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_music_dir)
        dir_row.addWidget(self._dir_edit, 1)
        dir_row.addWidget(browse)
        lay.addLayout(dir_row)

        lay.addSpacing(6)

        lay.addStretch()
        return w

    def _page_sync(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(52, 36, 52, 28)
        lay.setSpacing(10)

        hdr = QHBoxLayout()
        hdr.addWidget(_h("Sync Target"))
        opt = QLabel("optional")
        opt.setObjectName("muted")
        opt.setContentsMargins(10, 0, 0, 0)
        opt.setAlignment(Qt.AlignmentFlag.AlignBottom)
        hdr.addWidget(opt)
        hdr.addStretch()
        lay.addLayout(hdr)

        lay.addWidget(
            _muted(
                "Music Manager can push or pull your library to a NAS, SMB server, "
                "or any local folder. Leave this on None if you don't need it."
            )
        )

        lay.addSpacing(4)

        # ── Mode selection ────────────────────────────────────────────────────
        self._sync_group = QButtonGroup(w)

        none_rb = QRadioButton("None  —  skip for now")
        none_rb.setProperty("sync_value", "none")
        none_rb.setChecked(True)  # default to first item
        self._sync_group.addButton(none_rb)
        lay.addWidget(none_rb)

        server_rb = QRadioButton("Server  —  SMB / NAS")
        server_rb.setProperty("sync_value", "server")
        self._sync_group.addButton(server_rb)
        lay.addWidget(server_rb)

        folder_rb = QRadioButton("Folder  —  local path or external drive")
        folder_rb.setProperty("sync_value", "folder")
        self._sync_group.addButton(folder_rb)
        lay.addWidget(folder_rb)

        # ── Settings panel (bottom) ───────────────────────────────────────────
        lay.addStretch()

        self._smb_widget = QWidget()
        smb = QFormLayout(self._smb_widget)
        smb.setContentsMargins(0, 8, 0, 0)
        smb.setSpacing(6)
        self._smb_host = QLineEdit()
        self._smb_host.setPlaceholderText("hostname or IP")
        self._smb_share = QLineEdit()
        self._smb_share.setPlaceholderText("share name")
        self._smb_user = QLineEdit()
        self._smb_pass = QLineEdit()
        self._smb_pass.setEchoMode(QLineEdit.EchoMode.Password)
        self._smb_pass.setPlaceholderText("optional")
        smb.addRow("Host:", self._smb_host)
        smb.addRow("Share:", self._smb_share)
        smb.addRow("Username:", self._smb_user)
        smb.addRow("Password:", self._smb_pass)
        lay.addWidget(self._smb_widget)

        self._folder_widget = QWidget()
        folder_row = QHBoxLayout(self._folder_widget)
        folder_row.setContentsMargins(0, 8, 0, 0)
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("/mnt/nas/Music")
        folder_browse = QPushButton("Browse…")
        folder_browse.clicked.connect(self._browse_folder_dest)
        folder_row.addWidget(QLabel("Destination:"))
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(folder_browse)
        lay.addWidget(self._folder_widget)

        for btn in self._sync_group.buttons():
            btn.toggled.connect(self._update_sync_fields)
        self._update_sync_fields()

        return w

    def _page_done(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(52, 48, 52, 28)
        lay.setSpacing(14)

        lay.addWidget(_h("You're all set!", 22))
        lay.addWidget(_muted("Here are a few things to try first:"))
        lay.addSpacing(8)

        for tab, tip in [
            ("Library tab", "Click Refresh to scan your music directory."),
            ("Download tab", "Paste a YouTube or SoundCloud URL to grab audio."),
            ("Duplicates tab", "Run a scan after adding music to find accidental duplicates."),
            ("Settings tab", "Fine-tune skip directories, sync credentials, and more."),
        ]:
            row = QHBoxLayout()
            arrow = QLabel("→")
            arrow.setFixedWidth(16)
            arrow.setStyleSheet(f"color:{COLORS['accent']};")
            lbl = QLabel(f"<b>{tab}</b> — {tip}")
            lbl.setObjectName("muted")
            row.addWidget(arrow)
            row.addWidget(lbl, 1)
            lay.addLayout(row)

        lay.addStretch()
        return w

    # ── Navigation ────────────────────────────────────────────────────────────

    def _go_next(self):
        idx = self._stack.currentIndex()
        self._save_page(idx)
        if idx == self._stack.count() - 1:
            self._finish()
            return
        next_idx = idx + 1
        self._stack.setCurrentIndex(next_idx)
        self._step_bar.set_step(next_idx)
        self._back_btn.setVisible(True)
        self._next_btn.setText(_NEXT_LABELS[next_idx])
        if next_idx == self._stack.count() - 1:
            self._skip_btn.setVisible(False)
            self._back_btn.setVisible(False)

    def _go_back(self):
        idx = self._stack.currentIndex()
        if idx == 0:
            return
        prev_idx = idx - 1
        self._stack.setCurrentIndex(prev_idx)
        self._step_bar.set_step(prev_idx)
        self._next_btn.setText(_NEXT_LABELS[prev_idx])
        self._skip_btn.setVisible(True)
        self._back_btn.setVisible(prev_idx > 0)

    def _finish(self):
        self._save_page(self._stack.currentIndex())
        self.config.set("setup_complete", True)
        self.config.save()
        self.accept()

    # ── Save helpers ──────────────────────────────────────────────────────────

    def _save_page(self, idx: int):
        if idx == 1:
            d = self._dir_edit.text().strip()
            if d:
                path = Path(d).expanduser()
                if path.is_absolute() and path.is_dir():
                    self.config.set("music_dir", str(path))
                else:
                    QMessageBox.warning(self, "Music Directory",
                                        f"'{d}' is not an existing folder; keeping {self.config.music_dir}.\n"
                                        "You can change it later in Settings.")
            self.config.save()
        elif idx == 2:
            mode = next(
                (btn.property("sync_value") for btn in self._sync_group.buttons() if btn.isChecked()),
                "none",
            )
            if mode != "none":
                self.config.set("sync_mode", mode)
            if mode == "server":
                self.config.set("server.host", self._smb_host.text())
                self.config.set("server.share", self._smb_share.text())
                self.config.set("server.user", self._smb_user.text())
                if self._smb_pass.text():
                    self.config.set("server.pass", encrypt_password(self._smb_pass.text()))
            elif mode == "folder":
                dest = self._folder_edit.text().strip()
                if dest and not Path(dest).expanduser().is_absolute():
                    QMessageBox.warning(self, "Sync Destination",
                                        "The sync destination must be an absolute path; it was not saved.")
                else:
                    self.config.set("folder_sync.destination", str(Path(dest).expanduser()) if dest else "")
            self.config.save()

    # ── Field helpers ─────────────────────────────────────────────────────────

    def _update_sync_fields(self):
        mode = next(
            (btn.property("sync_value") for btn in self._sync_group.buttons() if btn.isChecked()),
            "none",
        )
        self._smb_widget.setVisible(mode == "server")
        self._folder_widget.setVisible(mode == "folder")

    def _browse_music_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Music Directory", self._dir_edit.text())
        if path:
            self._dir_edit.setText(path)

    def _browse_folder_dest(self):
        path = QFileDialog.getExistingDirectory(self, "Select Sync Destination", self._folder_edit.text())
        if path:
            self._folder_edit.setText(path)
