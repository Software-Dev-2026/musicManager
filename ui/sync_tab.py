from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QCheckBox, QRadioButton, QButtonGroup,
    QLabel, QProgressBar, QTreeWidget, QTreeWidgetItem,
    QMessageBox, QTextEdit, QSplitter,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor

from ..core.config import Config
from ..core.sync import FileStatus, SyncDiff
from ..workers.sync_worker import DiffWorker, SyncApplyWorker
from .styles import COLORS


_STATUS_COLORS = {
    FileStatus.NEW:       (COLORS["new"],     COLORS["new_text"],       "NEW"),
    FileStatus.CHANGED:   (COLORS["changed"], COLORS["changed_text"],   "CHANGED"),
    FileStatus.DELETED:   (COLORS["deleted"], COLORS["deleted_text"],   "DELETED"),
    FileStatus.UNCHANGED: (COLORS["bg_primary"], COLORS["unchanged_text"], ""),
}

# Pull direction inverts NEW (local-only) and DELETED (remote-new)
_STATUS_COLORS_PULL = {
    FileStatus.NEW:       (COLORS["deleted"], COLORS["deleted_text"], "LOCAL ONLY"),
    FileStatus.CHANGED:   (COLORS["changed"], COLORS["changed_text"], "CHANGED"),
    FileStatus.DELETED:   (COLORS["new"],     COLORS["new_text"],     "REMOTE NEW"),
    FileStatus.UNCHANGED: (COLORS["bg_primary"], COLORS["unchanged_text"], ""),
}


class SyncTab(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._diff: SyncDiff | None = None
        self._syncer = None
        self._diff_worker: DiffWorker | None = None
        self._sync_worker: SyncApplyWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        # Read-only target summary
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("Target:"))
        self._target_label = QLabel()
        self._target_label.setObjectName("muted")
        target_row.addWidget(self._target_label)
        target_row.addStretch()
        outer.addLayout(target_row)
        self._update_target_label()

        # Options row
        opts_layout = QHBoxLayout()
        self._push_radio = QRadioButton("Push  (local → remote)")
        self._pull_radio = QRadioButton("Pull  (remote → local)")
        self._push_radio.setChecked(True)
        self._direction_group = QButtonGroup(self)
        self._direction_group.addButton(self._push_radio)
        self._direction_group.addButton(self._pull_radio)
        self._push_radio.toggled.connect(self._on_direction_changed)
        self._delete_cb = QCheckBox("Delete extra files at destination")
        opts_layout.addWidget(self._push_radio)
        opts_layout.addWidget(self._pull_radio)
        opts_layout.addSpacing(24)
        opts_layout.addWidget(self._delete_cb)
        opts_layout.addStretch()
        outer.addLayout(opts_layout)

        # Action buttons + legend
        btn_row = QHBoxLayout()
        self._diff_btn = QPushButton("Compute Diff")
        self._diff_btn.setObjectName("primary")
        self._diff_btn.clicked.connect(self._compute_diff)
        self._sync_btn = QPushButton("Apply Sync")
        self._sync_btn.setObjectName("success")
        self._sync_btn.setEnabled(False)
        self._sync_btn.clicked.connect(self._apply_sync)
        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)
        btn_row.addWidget(self._diff_btn)
        btn_row.addWidget(self._sync_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(24)
        for label, color in [("New", COLORS["new_text"]), ("Changed", COLORS["changed_text"]),
                              ("Deleted", COLORS["deleted_text"]), ("Unchanged", COLORS["unchanged_text"])]:
            dot = QLabel(f"● {label}")
            dot.setStyleSheet(f"color: {color};")
            btn_row.addWidget(dot)
        btn_row.addStretch()
        outer.addLayout(btn_row)

        # Progress
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_msg = QLabel("")
        self._progress_msg.setObjectName("muted")
        self._progress_msg.setVisible(False)
        outer.addWidget(self._progress_bar)
        outer.addWidget(self._progress_msg)

        # Summary
        self._summary_label = QLabel("")
        self._summary_label.setObjectName("muted")
        outer.addWidget(self._summary_label)

        # Diff tree + log with proper spacing
        splitter = QSplitter(Qt.Orientation.Vertical)

        self._diff_tree = QTreeWidget()
        self._diff_tree.setHeaderLabels(["Path", "Status", "Local Size", "Remote Size"])
        self._diff_tree.setColumnWidth(0, 500)
        self._diff_tree.setColumnWidth(1, 90)
        self._diff_tree.setColumnWidth(2, 100)
        self._diff_tree.setColumnWidth(3, 100)
        self._diff_tree.setAlternatingRowColors(False)
        splitter.addWidget(self._diff_tree)

        log_container = QWidget()
        log_layout = QVBoxLayout(log_container)
        log_layout.setContentsMargins(0, 8, 0, 0)  # top margin gap
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        log_layout.addWidget(self._log)
        splitter.addWidget(log_container)

        splitter.setSizes([420, 140])
        outer.addWidget(splitter)

    def _on_direction_changed(self):
        self._diff = None
        self._syncer = None
        self._sync_btn.setEnabled(False)
        self._diff_tree.clear()
        self._summary_label.setText("")

    def _update_target_label(self):
        if self.config.sync_mode == "folder":
            dest = self.config.folder_sync_dest or "(no destination set)"
            self._target_label.setText(f"{dest}  [Local Folder]")
        else:
            host = self.config.smb_host or "(no host set)"
            share = self.config.smb_share
            self._target_label.setText(
                f"{host}/{share}  [Server]" if share else f"{host}  [Server]"
            )

    def refresh_from_config(self):
        self._update_target_label()
        self._diff = None
        self._sync_btn.setEnabled(False)
        self._diff_tree.clear()

    def _compute_diff(self):
        if self.config.sync_mode == "folder" and not self.config.folder_sync_dest.strip():
            QMessageBox.warning(self, "No Destination",
                                "No folder destination set. Configure it in Settings.")
            return

        self._diff = None
        self._syncer = None
        self._sync_btn.setEnabled(False)
        self._diff_tree.clear()
        self._log.clear()
        self._diff_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_msg.setVisible(True)
        self._summary_label.setText("Computing diff…")

        self._diff_worker = DiffWorker(self.config.sync_mode, self.config)
        self._diff_worker.progress.connect(self._on_progress)
        self._diff_worker.log.connect(lambda msg: self._log.append(msg))
        self._diff_worker.finished.connect(self._on_diff_done)
        self._diff_worker.error.connect(self._on_error)
        self._diff_worker.start()

    def _on_diff_done(self, result: dict):
        self._diff = result["diff"]
        self._syncer = result.get("syncer")
        self._diff_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_msg.setVisible(False)
        self._populate_diff_tree(self._diff)
        s = self._diff.summary()
        self._summary_label.setText(
            f"New: {s[FileStatus.NEW]}  |  Changed: {s[FileStatus.CHANGED]}  |  "
            f"Deleted: {s[FileStatus.DELETED]}  |  Unchanged: {s[FileStatus.UNCHANGED]}"
        )
        has_changes = s[FileStatus.NEW] + s[FileStatus.CHANGED] + s[FileStatus.DELETED] > 0
        self._sync_btn.setEnabled(has_changes)

    def _populate_diff_tree(self, diff: SyncDiff):
        self._diff_tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}

        status_colors = _STATUS_COLORS_PULL if self._pull_radio.isChecked() else _STATUS_COLORS

        def get_parent(rel: str):
            parts = rel.replace("\\", "/").split("/")
            if len(parts) == 1:
                return None
            parent_path = "/".join(parts[:-1])
            if parent_path in nodes:
                return nodes[parent_path]
            grand = get_parent(parent_path)
            folder_label = parent_path.split("/")[-1]
            folder_item = QTreeWidgetItem(grand or self._diff_tree, [folder_label, "", "", ""])
            folder_item.setExpanded(True)
            nodes[parent_path] = folder_item
            return folder_item

        for entry in sorted(diff.entries, key=lambda e: e.rel_path):
            parent = get_parent(entry.rel_path)
            name = entry.rel_path.replace("\\", "/").split("/")[-1]
            bg, fg, status_text = status_colors.get(entry.status, ("#0d1117", "#c9d1d9", ""))
            local_sz = f"{entry.local_size / 1024 / 1024:.2f} MB" if entry.local_size else "—"
            remote_sz = f"{entry.remote_size / 1024 / 1024:.2f} MB" if entry.remote_size else "—"
            item = QTreeWidgetItem(parent or self._diff_tree, [name, status_text, local_sz, remote_sz])
            if status_text:
                for col in range(4):
                    item.setBackground(col, QColor(bg))
                    item.setForeground(col, QColor(fg))

    def _apply_sync(self):
        if not self._diff:
            return
        direction = "pull" if self._pull_radio.isChecked() else "push"
        delete = self._delete_cb.isChecked()
        s = self._diff.summary()
        if direction == "pull":
            transfer_count = s[FileStatus.CHANGED] + s[FileStatus.DELETED]
            delete_count = s[FileStatus.NEW] if delete else 0
            action_label = "Download"
        else:
            transfer_count = s[FileStatus.NEW] + s[FileStatus.CHANGED]
            delete_count = s[FileStatus.DELETED] if delete else 0
            action_label = "Upload"
        msg = (
            f"{action_label}: {transfer_count}  Delete: {delete_count}\n\nApply sync?"
        )
        reply = QMessageBox.question(self, "Confirm Sync", msg,
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._diff_btn.setEnabled(False)
        self._sync_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._progress_msg.setVisible(True)
        self._log.clear()
        self._sync_worker = SyncApplyWorker(
            self.config.sync_mode, self._diff, self.config, self._syncer, delete, direction,
        )
        self._sync_worker.progress.connect(self._on_progress)
        self._sync_worker.log.connect(lambda msg: self._log.append(msg))
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_error)
        self._sync_worker.start()

    def _on_sync_done(self, stats: dict):
        self._diff_btn.setEnabled(True)
        self._sync_btn.setEnabled(False)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_msg.setVisible(False)
        self._diff_tree.clear()
        self._diff = None
        errors = stats.get("errors", [])
        self._summary_label.setText(
            f"Sync complete.  Transferred: {stats.get('transferred', 0)}  "
            f"Deleted: {stats.get('deleted', 0)}  Errors: {len(errors)}"
        )
        if errors:
            self._log.append("ERRORS:")
            for e in errors:
                self._log.append(f"  {e}")

    def _on_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
        self._progress_msg.setText(msg)

    def _cancel(self):
        if self._diff_worker:
            self._diff_worker.cancel()
        if self._sync_worker:
            self._sync_worker.cancel()
        self._diff_btn.setEnabled(True)
        self._sync_btn.setEnabled(self._diff is not None)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_msg.setVisible(False)

    def _on_error(self, msg: str):
        self._diff_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._progress_msg.setVisible(False)
        QMessageBox.critical(self, "Sync Error", msg)
        self._log.append(f"ERROR: {msg}")
