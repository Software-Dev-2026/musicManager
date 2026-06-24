import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QStyle,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Config
from ..core.duplicates import check_fpcalc, delete_files, rank_group, resolve_deletions
from ..workers.dupes_worker import DupesWorker
from .styles import COLORS


class _PriorityDialog(QDialog):
    """Edit which directory names count as low-priority (will be deleted over canonical copies)."""

    def __init__(self, priority_dirs: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Auto-Select Priority")
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)

        desc = QLabel(
            "Files inside these directories will be deleted in favour of canonical copies "
            "(files NOT inside any listed directory)."
        )
        desc.setWordWrap(True)
        desc.setObjectName("muted")
        layout.addWidget(desc)

        self._list = QListWidget()
        for d in priority_dirs:
            self._list.addItem(QListWidgetItem(d))
        layout.addWidget(self._list)

        btns = QHBoxLayout()
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add)
        rem_btn = QPushButton("Remove")
        rem_btn.clicked.connect(self._remove)
        _style = QApplication.style()
        up_btn = QPushButton()
        up_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ArrowUp))
        up_btn.setFixedWidth(32)
        up_btn.setToolTip("Move up (higher priority)")
        up_btn.clicked.connect(self._move_up)
        dn_btn = QPushButton()
        dn_btn.setIcon(_style.standardIcon(QStyle.StandardPixmap.SP_ArrowDown))
        dn_btn.setFixedWidth(32)
        dn_btn.setToolTip("Move down (lower priority)")
        dn_btn.clicked.connect(self._move_down)
        btns.addWidget(add_btn)
        btns.addWidget(rem_btn)
        btns.addSpacing(8)
        btns.addWidget(up_btn)
        btns.addWidget(dn_btn)
        btns.addStretch()
        layout.addLayout(btns)

        bbox = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bbox.accepted.connect(self.accept)
        bbox.rejected.connect(self.reject)
        layout.addWidget(bbox)

    def _add(self):
        text, ok = QInputDialog.getText(self, "Add Directory", "Directory name:")
        if ok and text.strip():
            self._list.addItem(QListWidgetItem(text.strip()))

    def _remove(self):
        for item in self._list.selectedItems():
            self._list.takeItem(self._list.row(item))

    def _move_up(self):
        row = self._list.currentRow()
        if row > 0:
            item = self._list.takeItem(row)
            self._list.insertItem(row - 1, item)
            self._list.setCurrentRow(row - 1)

    def _move_down(self):
        row = self._list.currentRow()
        if 0 <= row < self._list.count() - 1:
            item = self._list.takeItem(row)
            self._list.insertItem(row + 1, item)
            self._list.setCurrentRow(row + 1)

    def priority_dirs(self) -> list:
        return [self._list.item(i).text() for i in range(self._list.count())]


class DuplicatesTab(QWidget):
    def __init__(self, config: Config, parent=None):
        super().__init__(parent)
        self.config = config
        self._worker: DupesWorker | None = None
        self._groups: list = []
        self._scan_start: float = 0.0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # Settings
        settings_group = QGroupBox("Scan Settings")
        settings_layout = QHBoxLayout(settings_group)
        settings_layout.addWidget(QLabel("Similarity Threshold:"))
        self._threshold_slider = QSlider(Qt.Orientation.Horizontal)
        self._threshold_slider.setRange(50, 100)
        self._threshold_slider.setValue(int(self.config.fingerprint_threshold * 100))
        self._threshold_slider.setFixedWidth(200)
        self._threshold_slider.valueChanged.connect(self._update_threshold_label)
        self._threshold_label = QLabel(f"{self.config.fingerprint_threshold:.0%}")
        self._threshold_label.setMinimumWidth(40)
        self._no_cache_cb = QCheckBox("Rebuild Cache")
        settings_layout.addWidget(self._threshold_slider)
        settings_layout.addWidget(self._threshold_label)
        settings_layout.addSpacing(24)
        settings_layout.addWidget(self._no_cache_cb)
        settings_layout.addStretch()
        layout.addWidget(settings_group)

        # Buttons
        btn_row = QHBoxLayout()
        self._scan_btn = QPushButton("Scan for Duplicates")
        self._scan_btn.setObjectName("primary")
        self._scan_btn.clicked.connect(self._start_scan)

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._cancel)

        self._auto_select_btn = QPushButton("Auto-Select")
        self._auto_select_btn.setEnabled(False)
        self._auto_select_btn.clicked.connect(self._auto_select)
        self._refresh_auto_select_label()

        self._unselect_btn = QPushButton("Unselect All")
        self._unselect_btn.setEnabled(False)
        self._unselect_btn.clicked.connect(self._unselect_all)

        self._delete_btn = QPushButton("Delete Selected")
        self._delete_btn.setObjectName("danger")
        self._delete_btn.setEnabled(False)
        self._delete_btn.clicked.connect(self._delete_selected)

        btn_row.addWidget(self._scan_btn)
        btn_row.addWidget(self._cancel_btn)
        btn_row.addSpacing(16)
        btn_row.addWidget(self._auto_select_btn)
        btn_row.addWidget(self._unselect_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # Progress row
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._eta_label = QLabel("")
        self._eta_label.setObjectName("muted")
        self._eta_label.setVisible(False)
        layout.addWidget(self._progress_bar)
        layout.addWidget(self._eta_label)

        # Results tree
        self._results = QTreeWidget()
        self._results.setHeaderLabels(["File", "Format", "Size", "Priority"])
        self._results.setColumnWidth(0, 520)
        self._results.setColumnWidth(1, 70)
        self._results.setColumnWidth(2, 90)
        self._results.setColumnWidth(3, 90)
        self._results.setAlternatingRowColors(True)
        self._results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._results.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._results)

        # Log
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(90)
        layout.addWidget(self._log)

        self._summary_label = QLabel("")
        self._summary_label.setObjectName("muted")
        layout.addWidget(self._summary_label)

    def _update_threshold_label(self, value: int):
        self._threshold_label.setText(f"{value}%")

    def _start_scan(self):
        if not check_fpcalc():
            QMessageBox.warning(
                self, "Missing Tool", "fpcalc is not installed.\nInstall with: sudo dnf install chromaprint-tools"
            )
            return
        self._results.clear()
        self._log.clear()
        self._groups.clear()
        self._scan_btn.setEnabled(False)
        self._cancel_btn.setEnabled(True)
        self._delete_btn.setEnabled(False)
        self._auto_select_btn.setEnabled(False)
        self._unselect_btn.setEnabled(False)
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._eta_label.setVisible(True)
        self._eta_label.setText("")
        self._summary_label.setText("")

        threshold = self._threshold_slider.value() / 100.0
        self._worker = DupesWorker(
            self.config.music_dir,
            set(self.config.skip_dirs),
            self.config.audio_extensions,
            self.config.fingerprint_cache,
            threshold,
            self._no_cache_cb.isChecked(),
        )
        self._scan_start = time.monotonic()
        self._worker.progress.connect(self._on_progress)
        self._worker.log.connect(lambda msg: self._log.append(msg))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _cancel(self):
        if self._worker:
            self._worker.cancel()
        self._scan_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._eta_label.setVisible(False)

    def _on_progress(self, current: int, total: int, msg: str):
        if total > 0:
            self._progress_bar.setMaximum(total)
            self._progress_bar.setValue(current)
        if current > 0 and total > 0:
            elapsed = time.monotonic() - self._scan_start
            remaining = int(elapsed / current * (total - current))
            if remaining < 60:
                eta_text = f"~{remaining}s remaining"
            elif remaining < 3600:
                m, s = divmod(remaining, 60)
                eta_text = f"~{m}m {s}s remaining"
            else:
                h, r = divmod(remaining, 3600)
                m, s = divmod(r, 60)
                eta_text = f"~{h}h {m}m {s}s remaining"
            self._eta_label.setText(eta_text)

    def _on_done(self, groups: list):
        self._groups = groups
        self._scan_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._eta_label.setVisible(False)
        self._results.clear()
        if not groups:
            self._summary_label.setText("No duplicates found.")
            return

        total_files = sum(len(g) for g in groups)
        self._summary_label.setText(f"Found {len(groups)} duplicate group(s) across {total_files} files.")
        self._auto_select_btn.setEnabled(True)
        self._unselect_btn.setEnabled(True)

        priority_dirs = list(self.config.dupes_priority_dirs)
        for i, group in enumerate(groups, 1):
            ranked = rank_group(group, self.config.music_dir, priority_dirs)
            n = len(ranked)
            group_item = QTreeWidgetItem(self._results, [f"Group {i} — {n} copies"])
            group_item.setData(0, Qt.ItemDataRole.UserRole, None)
            group_item.setExpanded(True)
            for path, rank in ranked:
                size = path.stat().st_size / 1024 / 1024 if path.exists() else 0
                label = f"#{rank} of {n}"
                child = QTreeWidgetItem(
                    group_item,
                    [
                        str(path),
                        path.suffix.upper().lstrip("."),
                        f"{size:.2f} MB",
                        label,
                    ],
                )
                child.setCheckState(0, Qt.CheckState.Unchecked)
                child.setData(0, Qt.ItemDataRole.UserRole, str(path))
                if rank == 1:
                    child.setForeground(3, QColor(COLORS["success"]))  # keep
                elif rank == n:
                    child.setForeground(3, QColor(COLORS["error"]))  # most expendable
                else:
                    child.setForeground(3, QColor(COLORS["warning"]))  # middle

    def _on_item_changed(self, item: QTreeWidgetItem, _col: int = 0):
        """Enable Delete button as soon as any checkbox is checked."""
        any_checked = any(
            self._results.topLevelItem(i).child(j).checkState(0) == Qt.CheckState.Checked
            for i in range(self._results.topLevelItemCount())
            for j in range(self._results.topLevelItem(i).childCount())
        )
        self._delete_btn.setEnabled(any_checked)

    def _refresh_auto_select_label(self):
        configured = self.config.get("dupes_priority_configured", False)
        if configured:
            dirs = self.config.dupes_priority_dirs
            label = ", ".join(dirs) if dirs else "no priority dirs set"
            self._auto_select_btn.setToolTip(
                f"Apply auto-select using: {label}\n(Edit priority directories in Settings)"
            )
        else:
            self._auto_select_btn.setToolTip("First run: configure which directories are low-priority, then apply")

    def _rerank_all(self):
        if not self._groups:
            return
        priority_dirs = list(self.config.dupes_priority_dirs)
        self._results.blockSignals(True)
        for i, group in enumerate(self._groups):
            group_item = self._results.topLevelItem(i)
            if not group_item:
                continue
            ranked = rank_group(group, self.config.music_dir, priority_dirs)
            rank_map = {p: r for p, r in ranked}
            n = len(ranked)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                path = Path(child.data(0, Qt.ItemDataRole.UserRole) or "")
                rank = rank_map.get(path, j + 1)
                child.setText(3, f"#{rank} of {n}")
                if rank == 1:
                    child.setForeground(3, QColor(COLORS["success"]))
                elif rank == n:
                    child.setForeground(3, QColor(COLORS["error"]))
                else:
                    child.setForeground(3, QColor(COLORS["warning"]))
        self._results.blockSignals(False)

    def refresh_from_config(self):
        self._refresh_auto_select_label()
        self._rerank_all()

    def _auto_select(self):
        configured = self.config.get("dupes_priority_configured", False)
        if not configured:
            dlg = _PriorityDialog(list(self.config.dupes_priority_dirs), self)
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            self.config.set("dupes_priority_dirs", dlg.priority_dirs())
            self.config.set("dupes_priority_configured", True)
            self.config.save()
            self._refresh_auto_select_label()
            self._rerank_all()

        priority_dirs = list(self.config.dupes_priority_dirs)
        for i, group in enumerate(self._groups):
            to_delete, _ = resolve_deletions(group, self.config.music_dir, priority_dirs)
            group_item = self._results.topLevelItem(i)
            if not group_item:
                continue
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                path = Path(child.data(0, Qt.ItemDataRole.UserRole) or "")
                checked = path in to_delete
                child.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def _unselect_all(self):
        for i in range(self._results.topLevelItemCount()):
            group = self._results.topLevelItem(i)
            for j in range(group.childCount()):
                group.child(j).setCheckState(0, Qt.CheckState.Unchecked)

    def _collect_checked(self) -> list:
        paths = []
        for i in range(self._results.topLevelItemCount()):
            group_item = self._results.topLevelItem(i)
            for j in range(group_item.childCount()):
                child = group_item.child(j)
                if child.checkState(0) == Qt.CheckState.Checked:
                    val = child.data(0, Qt.ItemDataRole.UserRole)
                    if val:
                        paths.append(Path(val))
        return paths

    def _delete_selected(self):
        paths = self._collect_checked()
        if not paths:
            QMessageBox.information(self, "Nothing Selected", "Check files to delete first.")
            return
        msg = f"Delete {len(paths)} file(s)?\n\n" + "\n".join(str(p) for p in paths[:8])
        if len(paths) > 8:
            msg += f"\n… and {len(paths) - 8} more"
        reply = QMessageBox.question(
            self, "Confirm Delete", msg, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        errors = delete_files(paths)
        if errors:
            QMessageBox.warning(self, "Errors", "\n".join(errors))
        deleted = {p for p in paths if not p.exists()}
        self._log.append(f"Deleted {len(deleted)} file(s).")
        for e in errors:
            self._log.append(f"  ERROR: {e}")
        self._prune_deleted(deleted)

    def _prune_deleted(self, deleted: set):
        """Remove deleted files from the tree; drop any group reduced to a single copy."""
        if not deleted:
            return

        priority_dirs = list(self.config.dupes_priority_dirs)
        # Iterate in reverse so takeTopLevelItem indices stay valid
        for i in range(self._results.topLevelItemCount() - 1, -1, -1):
            group_item = self._results.topLevelItem(i)

            # Remove children whose files were deleted
            for j in range(group_item.childCount() - 1, -1, -1):
                child = group_item.child(j)
                if Path(child.data(0, Qt.ItemDataRole.UserRole) or "") in deleted:
                    group_item.takeChild(j)

            n = group_item.childCount()
            if n <= 1:
                self._results.takeTopLevelItem(i)
                continue

            # Re-rank remaining files
            remaining = [Path(group_item.child(j).data(0, Qt.ItemDataRole.UserRole)) for j in range(n)]
            ranked = rank_group(remaining, self.config.music_dir, priority_dirs)
            rank_map = {p: r for p, r in ranked}

            # Block itemChanged while updating labels so _on_item_changed isn't spammed
            self._results.blockSignals(True)
            for j in range(n):
                child = group_item.child(j)
                path = Path(child.data(0, Qt.ItemDataRole.UserRole))
                rank = rank_map.get(path, j + 1)
                child.setText(3, f"#{rank} of {n}")
                if rank == 1:
                    child.setForeground(3, QColor(COLORS["success"]))
                elif rank == n:
                    child.setForeground(3, QColor(COLORS["error"]))
                else:
                    child.setForeground(3, QColor(COLORS["warning"]))
            self._results.blockSignals(False)

            # Update group header (keep original "Group N" prefix)
            prefix = group_item.text(0).split(" — ")[0]
            group_item.setText(0, f"{prefix} — {n} copies")

        # Rebuild self._groups from the surviving tree items
        self._groups = [
            [
                Path(self._results.topLevelItem(i).child(j).data(0, Qt.ItemDataRole.UserRole))
                for j in range(self._results.topLevelItem(i).childCount())
            ]
            for i in range(self._results.topLevelItemCount())
        ]

        total_groups = len(self._groups)
        total_files = sum(len(g) for g in self._groups)
        if total_groups:
            self._summary_label.setText(f"Found {total_groups} duplicate group(s) across {total_files} files.")
        else:
            self._summary_label.setText("No duplicates remaining.")
            self._auto_select_btn.setEnabled(False)
            self._unselect_btn.setEnabled(False)

        # Let _on_item_changed recalculate delete button state
        self._on_item_changed(None)

    def _on_error(self, msg: str):
        self._scan_btn.setEnabled(True)
        self._cancel_btn.setEnabled(False)
        self._progress_bar.setVisible(False)
        self._eta_label.setVisible(False)
        QMessageBox.critical(self, "Scan Error", msg)
