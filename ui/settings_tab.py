from pathlib import Path

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLineEdit, QDoubleSpinBox, QCheckBox, QComboBox,
    QPushButton, QLabel, QFileDialog, QMessageBox, QScrollArea,
    QListWidget, QListWidgetItem, QInputDialog,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QAction

from ..core.config import Config, LIBRARY_STRUCTURES, encrypt_password
from .styles import COLORS, OrderedListWidget


class _PasswordEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEchoMode(QLineEdit.EchoMode.Password)
        self._action = QAction("Show", self)
        self._action.setCheckable(True)
        self._action.toggled.connect(self._toggle)
        self.addAction(self._action, QLineEdit.ActionPosition.TrailingPosition)

    def _toggle(self, visible: bool):
        self.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )
        self._action.setText("Hide" if visible else "Show")


class SettingsTab(QWidget):
    settings_saved = pyqtSignal()

    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._dirty = False
        self._setup_ui()
        self._load_values()
        self._connect_change_signals()

    def _setup_ui(self):
        # Outer layout: scroll area + sticky save bar
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(scroll.Shape.NoFrame)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(16)
        scroll.setWidget(container)
        outer.addWidget(scroll)

        # ── Music Library ────────────────────────────────────────────────────
        lib_group = QGroupBox("Music Library")
        lib_form = QFormLayout(lib_group)

        dir_row = QHBoxLayout()
        self._music_dir = QLineEdit()
        browse_dir = QPushButton("Browse…")
        browse_dir.clicked.connect(self._browse_music_dir)
        dir_row.addWidget(self._music_dir)
        dir_row.addWidget(browse_dir)
        lib_form.addRow("Music Directory:", dir_row)

        skip_lbl = QLabel("Skip Directories:")
        skip_lbl.setObjectName("muted")
        self._skip_dirs = QListWidget()
        self._skip_dirs.setMaximumHeight(90)
        skip_btns = QHBoxLayout()
        add_skip = QPushButton("Add")
        add_skip.clicked.connect(self._add_skip_dir)
        rem_skip = QPushButton("Remove")
        rem_skip.clicked.connect(self._remove_skip_dir)
        skip_btns.addWidget(add_skip)
        skip_btns.addWidget(rem_skip)
        skip_btns.addStretch()
        lib_form.addRow(skip_lbl, self._skip_dirs)
        lib_form.addRow("", skip_btns)

        lib_form.addRow("Library Structure:", self._make_struct_combo())

        self._show_titles_cb = QCheckBox("Read title tags when scanning (slower, shows song names)")
        lib_form.addRow("", self._show_titles_cb)

        layout.addWidget(lib_group)

        # ── Sync ─────────────────────────────────────────────────────────────
        smb_group = QGroupBox("Sync")
        sync_layout = QVBoxLayout(smb_group)

        mode_form = QFormLayout()
        self._sync_mode_combo = QComboBox()
        self._sync_mode_combo.addItem("Server (SMB/NAS)", "server")
        self._sync_mode_combo.addItem("Local Folder", "folder")
        self._sync_mode_combo.currentIndexChanged.connect(self._update_sync_fields)
        mode_form.addRow("Mode:", self._sync_mode_combo)
        sync_layout.addLayout(mode_form)

        self._smb_fields = QWidget()
        smb_form = QFormLayout(self._smb_fields)
        smb_form.setContentsMargins(0, 4, 0, 0)
        self._smb_host = QLineEdit()
        self._smb_host.setPlaceholderText("hostname or IP")
        smb_form.addRow("Host:", self._smb_host)
        self._smb_share = QLineEdit()
        self._smb_share.setPlaceholderText("share name")
        smb_form.addRow("Share:", self._smb_share)
        self._smb_user = QLineEdit()
        smb_form.addRow("Username:", self._smb_user)
        self._smb_pass = _PasswordEdit()
        smb_form.addRow("Password:", self._smb_pass)
        note = QLabel("Password is stored obfuscated (XOR+Base64) in the config file.")
        note.setObjectName("muted")
        smb_form.addRow("", note)
        sync_layout.addWidget(self._smb_fields)

        self._folder_fields = QWidget()
        folder_form = QFormLayout(self._folder_fields)
        folder_form.setContentsMargins(0, 4, 0, 0)
        dest_row = QHBoxLayout()
        self._folder_dest = QLineEdit()
        self._folder_dest.setPlaceholderText("Destination folder")
        browse_dest = QPushButton("Browse…")
        browse_dest.clicked.connect(self._browse_folder_dest)
        dest_row.addWidget(self._folder_dest)
        dest_row.addWidget(browse_dest)
        folder_form.addRow("Destination:", dest_row)
        sync_layout.addWidget(self._folder_fields)

        layout.addWidget(smb_group)

        # ── Duplicate Finder ─────────────────────────────────────────────────
        dupe_group = QGroupBox("Duplicate Finder")
        dupe_form = QFormLayout(dupe_group)
        self._fp_threshold = QDoubleSpinBox()
        self._fp_threshold.setRange(0.5, 1.0)
        self._fp_threshold.setSingleStep(0.01)
        self._fp_threshold.setDecimals(2)
        dupe_form.addRow("Similarity Threshold:", self._fp_threshold)
        cache_row = QHBoxLayout()
        self._fp_cache = QLineEdit()
        browse_cache = QPushButton("Browse…")
        browse_cache.clicked.connect(self._browse_fp_cache)
        cache_row.addWidget(self._fp_cache)
        cache_row.addWidget(browse_cache)
        dupe_form.addRow("Fingerprint Cache:", cache_row)

        prio_note = QLabel(
            "Low-priority directories — files here are deleted in favour of canonical copies "
            "when using Auto-Select in the Duplicates tab."
        )
        prio_note.setObjectName("muted")
        prio_note.setWordWrap(True)
        dupe_form.addRow("Auto-Select Priority:", prio_note)
        self._prio_dirs = OrderedListWidget("Directory name (top-level folder in music library):")
        self._prio_dirs.setMaximumHeight(130)
        dupe_form.addRow("", self._prio_dirs)

        layout.addWidget(dupe_group)

        # ── Download ─────────────────────────────────────────────────────────
        dl_group = QGroupBox("Download (yt-dlp)")
        dl_form = QFormLayout(dl_group)
        dl_staging_row = QHBoxLayout()
        self._dl_staging = QLineEdit()
        self._dl_staging.setPlaceholderText("Temp folder where downloads land before committing")
        browse_dl = QPushButton("Browse…")
        browse_dl.clicked.connect(self._browse_dl_staging)
        dl_staging_row.addWidget(self._dl_staging)
        dl_staging_row.addWidget(browse_dl)
        dl_form.addRow("Staging Directory:", dl_staging_row)
        staging_note = QLabel(
            "Files stay here until you review/edit metadata and commit them to the library."
        )
        staging_note.setObjectName("muted")
        dl_form.addRow("", staging_note)
        self._dl_embed_thumb = QCheckBox("Embed Thumbnail")
        self._dl_add_meta = QCheckBox("Add Metadata")
        dl_form.addRow("", self._dl_embed_thumb)
        dl_form.addRow("", self._dl_add_meta)
        layout.addWidget(dl_group)

        layout.addStretch()

        # ── Sticky save bar ───────────────────────────────────────────────────
        save_bar = QWidget()
        save_bar.setStyleSheet(f"background:{COLORS['bg_secondary']}; border-top:1px solid {COLORS['border']};")
        save_layout = QHBoxLayout(save_bar)
        save_layout.setContentsMargins(24, 10, 24, 10)
        save_btn = QPushButton("Save Settings")
        save_btn.setObjectName("primary")
        save_btn.setMinimumWidth(160)
        save_btn.clicked.connect(self._save)
        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.clicked.connect(self._reset)
        wizard_btn = QPushButton("Run Setup Wizard…")
        wizard_btn.clicked.connect(self._run_wizard)
        save_layout.addWidget(save_btn)
        save_layout.addWidget(reset_btn)
        save_layout.addStretch()
        save_layout.addWidget(wizard_btn)
        outer.addWidget(save_bar)

    def _make_struct_combo(self) -> QComboBox:
        self._struct_combo = QComboBox()
        for s in LIBRARY_STRUCTURES:
            self._struct_combo.addItem(s.replace("/", " / "), s)
        return self._struct_combo

    def _load_values(self):
        self._music_dir.setText(str(self.config.music_dir))
        self._skip_dirs.clear()
        for d in self.config.skip_dirs:
            self._skip_dirs.addItem(QListWidgetItem(d))
        saved_struct = self.config.library_structure
        if saved_struct in LIBRARY_STRUCTURES:
            self._struct_combo.setCurrentIndex(LIBRARY_STRUCTURES.index(saved_struct))
        self._show_titles_cb.setChecked(self.config.show_titles)
        mode_idx = self._sync_mode_combo.findData(self.config.sync_mode)
        if mode_idx >= 0:
            self._sync_mode_combo.setCurrentIndex(mode_idx)
        self._smb_host.setText(self.config.smb_host)
        self._smb_share.setText(self.config.smb_share)
        self._smb_user.setText(self.config.smb_user)
        self._smb_pass.setText(self.config.smb_pass)
        self._folder_dest.setText(self.config.folder_sync_dest)
        self._fp_threshold.setValue(self.config.fingerprint_threshold)
        self._fp_cache.setText(str(self.config.fingerprint_cache))
        self._dl_staging.setText(self.config.get("download.staging_dir"))
        self._dl_embed_thumb.setChecked(bool(self.config.get("download.embed_thumbnail")))
        self._dl_add_meta.setChecked(bool(self.config.get("download.add_metadata")))
        self._prio_dirs.set_items(self.config.dupes_priority_dirs)
        self._update_sync_fields()
        self._dirty = False

    def _browse_music_dir(self):
        path = QFileDialog.getExistingDirectory(self, "Select Music Directory",
                                                 self._music_dir.text())
        if path:
            self._music_dir.setText(path)

    def _browse_folder_dest(self):
        path = QFileDialog.getExistingDirectory(self, "Select Sync Destination",
                                                 self._folder_dest.text() or str(Path.home()))
        if path:
            self._folder_dest.setText(path)

    def _browse_fp_cache(self):
        path, _ = QFileDialog.getSaveFileName(self, "Fingerprint Cache File",
                                               self._fp_cache.text(), "JSON (*.json)")
        if path:
            self._fp_cache.setText(path)

    def _browse_dl_staging(self):
        path = QFileDialog.getExistingDirectory(self, "Select Staging Directory",
                                                 self._dl_staging.text())
        if path:
            self._dl_staging.setText(path)

    def _update_sync_fields(self):
        is_server = self._sync_mode_combo.currentData() == "server"
        self._smb_fields.setVisible(is_server)
        self._folder_fields.setVisible(not is_server)

    def _mark_dirty(self, *_):
        self._dirty = True

    def _connect_change_signals(self):
        self._music_dir.textChanged.connect(self._mark_dirty)
        self._skip_dirs.model().rowsInserted.connect(self._mark_dirty)
        self._skip_dirs.model().rowsRemoved.connect(self._mark_dirty)
        self._struct_combo.currentIndexChanged.connect(self._mark_dirty)
        self._show_titles_cb.toggled.connect(self._mark_dirty)
        self._sync_mode_combo.currentIndexChanged.connect(self._mark_dirty)
        self._smb_host.textChanged.connect(self._mark_dirty)
        self._smb_share.textChanged.connect(self._mark_dirty)
        self._smb_user.textChanged.connect(self._mark_dirty)
        self._smb_pass.textChanged.connect(self._mark_dirty)
        self._folder_dest.textChanged.connect(self._mark_dirty)
        self._fp_threshold.valueChanged.connect(self._mark_dirty)
        self._fp_cache.textChanged.connect(self._mark_dirty)
        self._dl_staging.textChanged.connect(self._mark_dirty)
        self._dl_embed_thumb.toggled.connect(self._mark_dirty)
        self._dl_add_meta.toggled.connect(self._mark_dirty)
        self._prio_dirs.changed.connect(self._mark_dirty)

    def confirm_discard(self) -> bool:
        """Show Save/Discard/Cancel dialog. Returns True if navigation may proceed."""
        if not self._dirty:
            return True
        msg = QMessageBox(self)
        msg.setWindowTitle("Unsaved Changes")
        msg.setText("You have unsaved changes in Settings.")
        save_btn = msg.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard_btn = msg.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == save_btn:
            self._save()
            return True
        if clicked == discard_btn:
            self.config.load()
            self._load_values()
            return True
        return False

    def _add_skip_dir(self):
        text, ok = QInputDialog.getText(self, "Add Skip Directory", "Directory name to skip:")
        if ok and text.strip():
            self._skip_dirs.addItem(QListWidgetItem(text.strip()))

    def _remove_skip_dir(self):
        for item in self._skip_dirs.selectedItems():
            self._skip_dirs.takeItem(self._skip_dirs.row(item))

    def _save(self):
        self.config.set("music_dir", self._music_dir.text())
        self.config.set("skip_dirs",
                        [self._skip_dirs.item(i).text() for i in range(self._skip_dirs.count())])
        self.config.set("library_structure", self._struct_combo.currentData())
        self.config.set("show_titles", self._show_titles_cb.isChecked())
        self.config.set("sync_mode", self._sync_mode_combo.currentData())
        self.config.set("server.host", self._smb_host.text())
        self.config.set("server.share", self._smb_share.text())
        self.config.set("server.user", self._smb_user.text())
        self.config.set("server.pass", encrypt_password(self._smb_pass.text()))
        self.config.set("folder_sync.destination", self._folder_dest.text())
        self.config.set("fingerprint_threshold", self._fp_threshold.value())
        self.config.set("fingerprint_cache", self._fp_cache.text())
        self.config.set("dupes_priority_dirs",
                        self._prio_dirs.items())
        self.config.set("dupes_priority_configured", True)
        self.config.set("download.staging_dir", self._dl_staging.text())
        self.config.set("download.embed_thumbnail", self._dl_embed_thumb.isChecked())
        self.config.set("download.add_metadata", self._dl_add_meta.isChecked())
        self.config.save()
        self._dirty = False
        QMessageBox.information(self, "Saved", "Settings saved.")
        self.settings_saved.emit()

    def _reset(self):
        reply = QMessageBox.question(
            self, "Reset Settings", "Reset all settings to defaults?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.config.reset()
            self._load_values()

    def _run_wizard(self):
        from .first_run_wizard import FirstRunWizard
        wizard = FirstRunWizard(self.config, self.window())
        if wizard.exec():
            self._load_values()
            self.settings_saved.emit()
