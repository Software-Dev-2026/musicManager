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
        self._changes_only_cb = QCheckBox("Show only changes")
        self._changes_only_cb.toggled.connect(self._apply_changes_filter)
        btn_row.addWidget(self._changes_only_cb)
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

    def _direction(self) -> str:
        return "pull" if self._pull_radio.isChecked() else "push"

    def _drop_syncer(self):
        if self._syncer:
            self._syncer.disconnect()
        self._syncer = None

    def _on_direction_changed(self):
        # "Changed" depends on direction, so a diff can't be reused across directions
        self._diff = None
        self._drop_syncer()
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
        self._drop_syncer()
        self._sync_btn.setEnabled(False)
        self._diff_tree.clear()

    def _compute_diff(self):
        if self.config.sync_mode == "folder" and not self.config.folder_sync_dest.strip():
            QMessageBox.warning(self, "No Destination",
                                "No folder destination set. Configure it in Settings.")
            return

        self._diff = None
        self._drop_syncer()
        self._sync_btn.setEnabled(False)
        self._diff_tree.clear()
        self._log.clear()
        self._set_busy(True)
        self._summary_label.setText("Computing diff…")

        self._diff_worker = DiffWorker(self.config.sync_mode, self.config, self._direction())
        self._diff_worker.progress.connect(self._on_progress)
        self._diff_worker.log.connect(lambda msg: self._log.append(msg))
        self._diff_worker.finished.connect(self._on_diff_done)
        self._diff_worker.error.connect(self._on_error)
        self._diff_worker.start()

    def _set_busy(self, busy: bool):
        """Lock the controls while a worker runs; they are only unlocked when it reports back."""
        self._diff_btn.setEnabled(not busy)
        self._push_radio.setEnabled(not busy)
        self._pull_radio.setEnabled(not busy)
        self._delete_cb.setEnabled(not busy)
        self._cancel_btn.setEnabled(busy)
        self._progress_bar.setVisible(busy)
        self._progress_msg.setVisible(busy)
        if busy:
            self._sync_btn.setEnabled(False)
            self._progress_bar.setValue(0)

    def _on_diff_done(self, result: dict):
        self._set_busy(False)
        if result.get("cancelled"):
            self._summary_label.setText("Diff cancelled.")
            return
        self._diff = result["diff"]
        self._syncer = result.get("syncer")
        self._populate_diff_tree(self._diff)
        self._summary_label.setText(self._summary_text(self._diff))
        self._sync_btn.setEnabled(self._diff.has_changes())

    def _status_labels(self) -> dict:
        return _STATUS_COLORS_PULL if self._pull_radio.isChecked() else _STATUS_COLORS

    def _summary_text(self, diff: SyncDiff) -> str:
        """Files and folders counted separately, using the direction's wording."""
        labels = self._status_labels()
        files, every = diff.file_summary(), diff.summary()
        folders = {s: every[s] - files[s] for s in FileStatus}
        name = lambda s: (labels[s][2] or "Unchanged").title()
        text = "Files — " + "  |  ".join(f"{name(s)}: {files[s]}" for s in FileStatus)
        folder_parts = [f"{name(s)}: {folders[s]}" for s in (FileStatus.NEW, FileStatus.DELETED) if folders[s]]
        if folder_parts:
            text += "     Folders — " + "  |  ".join(folder_parts)
        return text

    _HAS_CHANGES = Qt.ItemDataRole.UserRole + 1

    def _populate_diff_tree(self, diff: SyncDiff):
        self._diff_tree.clear()
        nodes: dict[str, QTreeWidgetItem] = {}
        status_colors = self._status_labels()

        def get_parent(rel: str):
            parts = rel.replace("\\", "/").split("/")
            if len(parts) == 1:
                return None
            parent_path = "/".join(parts[:-1])
            if parent_path in nodes:
                return nodes[parent_path]
            grand = get_parent(parent_path)
            folder_label = parent_path.split("/")[-1]
            folder_item = QTreeWidgetItem(grand if grand is not None else self._diff_tree, [folder_label, "", "", ""])
            nodes[parent_path] = folder_item
            return folder_item

        for entry in sorted(diff.entries, key=lambda e: e.rel_path):
            parent = get_parent(entry.rel_path)
            name = entry.rel_path.replace("\\", "/").split("/")[-1]
            bg, fg, status_text = status_colors.get(entry.status, ("#0d1117", "#c9d1d9", ""))
            local_sz = f"{entry.local_size / 1024 / 1024:.2f} MB" if entry.local_size else "—"
            remote_sz = f"{entry.remote_size / 1024 / 1024:.2f} MB" if entry.remote_size else "—"
            item = QTreeWidgetItem(parent if parent is not None else self._diff_tree,
                                   [name, status_text, local_sz, remote_sz])
            item.setData(0, Qt.ItemDataRole.UserRole, entry.status)
            if status_text:
                for col in range(4):
                    item.setBackground(col, QColor(bg))
                    item.setForeground(col, QColor(fg))
            if entry.is_dir:
                nodes[entry.rel_path.replace("\\", "/")] = item

        for i in range(self._diff_tree.topLevelItemCount()):
            self._propagate(self._diff_tree.topLevelItem(i), status_colors)
        self._apply_changes_filter()

    def _propagate(self, item: QTreeWidgetItem, status_colors: dict) -> dict:
        """Roll file changes up into each folder row; returns {status: count} for the subtree."""
        own = item.data(0, Qt.ItemDataRole.UserRole)
        if item.childCount() == 0 and own is not None:  # a file
            counts = {own: 1} if own != FileStatus.UNCHANGED else {}
        else:
            counts: dict = {}
            for i in range(item.childCount()):
                for status, n in self._propagate(item.child(i), status_colors).items():
                    counts[status] = counts.get(status, 0) + n
            if counts and own in (None, FileStatus.UNCHANGED):
                # Folder itself is unchanged but holds changes: summarise them on its row
                order = (FileStatus.NEW, FileStatus.CHANGED, FileStatus.DELETED)
                item.setText(1, ", ".join(f"{counts[s]} {status_colors[s][2].lower()}" for s in order if s in counts))
                kind = next(iter(counts)) if len(counts) == 1 else FileStatus.CHANGED
                for col in range(4):
                    item.setForeground(col, QColor(status_colors[kind][1]))
            item.setExpanded(bool(counts))
        item.setData(0, self._HAS_CHANGES, bool(counts) or own not in (None, FileStatus.UNCHANGED))
        return counts

    def _apply_changes_filter(self, *_):
        only = self._changes_only_cb.isChecked()

        def visit(item: QTreeWidgetItem):
            item.setHidden(only and not item.data(0, self._HAS_CHANGES))
            for i in range(item.childCount()):
                visit(item.child(i))

        for i in range(self._diff_tree.topLevelItemCount()):
            visit(self._diff_tree.topLevelItem(i))

    def _apply_sync(self):
        if not self._diff:
            return
        direction = self._direction()
        delete = self._delete_cb.isChecked()
        s = self._diff.file_summary()
        if direction == "pull":
            transfer_count = s[FileStatus.CHANGED] + s[FileStatus.DELETED]
            delete_count = s[FileStatus.NEW] if delete else 0
            action_label = "Download"
            dest_label = "local"
        else:
            transfer_count = s[FileStatus.NEW] + s[FileStatus.CHANGED]
            delete_count = s[FileStatus.DELETED] if delete else 0
            action_label = "Upload"
            dest_label = "destination"
        msg = f"{action_label}: {transfer_count}  Delete: {delete_count}"
        warnings = []
        newer = self._diff.overwrites_newer()
        if newer:
            warnings.append(f"{newer} {dest_label} file(s) are NEWER than the copy that will replace them.")
        if delete_count:
            warnings.append(f"{delete_count} {dest_label} file(s) will be permanently deleted.")
        if warnings:
            msg += "\n\n⚠ " + "\n⚠ ".join(warnings)
        msg += "\n\nApply sync?"
        box = QMessageBox.warning if warnings else QMessageBox.question
        reply = box(self, "Confirm Sync", msg,
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No)
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._set_busy(True)
        self._log.clear()
        # The apply worker owns (and closes) the SMB connection from here on
        syncer, self._syncer = self._syncer, None
        self._sync_worker = SyncApplyWorker(
            self.config.sync_mode, self._diff, self.config, syncer, delete, direction,
        )
        self._sync_worker.progress.connect(self._on_progress)
        self._sync_worker.log.connect(lambda msg: self._log.append(msg))
        self._sync_worker.finished.connect(self._on_sync_done)
        self._sync_worker.error.connect(self._on_error)
        self._sync_worker.start()

    def _on_sync_done(self, stats: dict):
        self._set_busy(False)
        self._diff_tree.clear()
        self._diff = None
        errors = stats.get("errors", [])
        heading = "Sync cancelled." if stats.get("cancelled") else "Sync complete."
        self._summary_label.setText(
            f"{heading}  Transferred: {stats.get('transferred', 0)}  "
            f"Deleted: {stats.get('deleted', 0)}  Errors: {len(errors)}"
        )
        if stats.get("skipped"):
            self._log.append(f"Kept {stats['skipped']} file(s) whose name only changed case.")
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
        # Only request cancellation; controls unlock when the worker reports back,
        # so a second run can't start while transfers are still in flight.
        if self._diff_worker:
            self._diff_worker.cancel()
        if self._sync_worker:
            self._sync_worker.cancel()
        self._cancel_btn.setEnabled(False)
        self._progress_msg.setText("Cancelling — waiting for in-flight transfers…")

    def _on_error(self, msg: str):
        self._set_busy(False)
        # A failed apply leaves the diff stale; require a fresh one
        self._diff = None
        self._diff_tree.clear()
        self._drop_syncer()
        QMessageBox.critical(self, "Sync Error", msg)
        self._log.append(f"ERROR: {msg}")
