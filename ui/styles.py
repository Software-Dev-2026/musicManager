DARK_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #0d1117;
}

QWidget {
    background-color: #0d1117;
    color: #c9d1d9;
    font-family: "Segoe UI", "Inter", "Helvetica Neue", Arial, sans-serif;
    font-size: 13px;
}

/* ── Tab Bar ─────────────────────────────────────────────────────────────── */
QTabWidget::pane {
    border: 1px solid #30363d;
    border-top: none;
    background-color: #0d1117;
}

QTabBar {
    background-color: #161b22;
}

QTabBar::tab {
    background-color: #161b22;
    color: #8b949e;
    padding: 10px 22px;
    border: 1px solid #30363d;
    border-bottom: none;
    margin-right: 2px;
    border-radius: 6px 6px 0 0;
    min-width: 90px;
}

QTabBar::tab:selected {
    background-color: #1f6feb;
    color: #ffffff;
    font-weight: bold;
    border-color: #1f6feb;
}

QTabBar::tab:hover:!selected {
    background-color: #21262d;
    color: #e6edf3;
}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
QPushButton {
    background-color: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 500;
}

QPushButton:hover {
    background-color: #30363d;
    border-color: #8b949e;
}

QPushButton:pressed {
    background-color: #161b22;
}

QPushButton:disabled {
    background-color: #161b22;
    color: #484f58;
    border-color: #21262d;
}

QPushButton#primary {
    background-color: #1f6feb;
    color: #ffffff;
    border-color: #1f6feb;
}

QPushButton#primary:hover {
    background-color: #388bfd;
    border-color: #388bfd;
}

QPushButton#primary:pressed {
    background-color: #1158c7;
}

QPushButton#danger {
    background-color: #21262d;
    color: #f85149;
    border-color: #f85149;
}

QPushButton#danger:hover {
    background-color: #3d1a1a;
}

QPushButton#success {
    background-color: #21262d;
    color: #3fb950;
    border-color: #3fb950;
}

QPushButton#success:hover {
    background-color: #1a2f1a;
}

/* ── Inputs ──────────────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QPlainTextEdit {
    background-color: #161b22;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: #1f6feb;
}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {
    border-color: #388bfd;
    outline: none;
}

QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #161b22;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 5px 10px;
    min-width: 80px;
}

QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #388bfd;
}

QComboBox::drop-down {
    border: none;
    width: 24px;
}

QComboBox QAbstractItemView {
    background-color: #161b22;
    border: 1px solid #30363d;
    selection-background-color: #1f6feb;
    color: #c9d1d9;
}

/* ── Sliders ─────────────────────────────────────────────────────────────── */
QSlider::groove:horizontal {
    height: 4px;
    background: #30363d;
    border-radius: 2px;
}

QSlider::handle:horizontal {
    background: #388bfd;
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
}

QSlider::sub-page:horizontal {
    background: #1f6feb;
    border-radius: 2px;
}

/* ── Progress Bar ────────────────────────────────────────────────────────── */
QProgressBar {
    background-color: #21262d;
    border: 1px solid #30363d;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    color: transparent;
}

QProgressBar::chunk {
    background-color: #1f6feb;
    border-radius: 4px;
}

/* ── Trees & Lists ───────────────────────────────────────────────────────── */
QTreeWidget, QTreeView, QListWidget, QListView, QTableWidget, QTableView {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    alternate-background-color: #1a1f26;
    gridline-color: #21262d;
    outline: none;
}

QTreeWidget::item, QTreeView::item, QListWidget::item, QListView::item,
QTableWidget::item, QTableView::item {
    padding: 4px 6px;
    border: none;
}

QTreeWidget::item:selected, QTreeView::item:selected,
QListWidget::item:selected, QListView::item:selected,
QTableWidget::item:selected, QTableView::item:selected {
    background-color: #1f6feb;
    color: #ffffff;
}

QTreeWidget::item:hover:!selected, QTreeView::item:hover:!selected,
QListWidget::item:hover:!selected {
    background-color: #21262d;
}

QHeaderView::section {
    background-color: #21262d;
    color: #8b949e;
    border: none;
    border-right: 1px solid #30363d;
    border-bottom: 1px solid #30363d;
    padding: 6px 10px;
    font-weight: 600;
}

/* ── Checkboxes & Radio ──────────────────────────────────────────────────── */
QCheckBox, QRadioButton {
    spacing: 8px;
    color: #c9d1d9;
}

QCheckBox::indicator, QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #30363d;
    border-radius: 3px;
    background-color: #161b22;
}

QRadioButton::indicator {
    border-radius: 8px;
}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {
    background-color: #1f6feb;
    border-color: #1f6feb;
}

/* ── Group Box ───────────────────────────────────────────────────────────── */
QGroupBox {
    border: 1px solid #30363d;
    border-radius: 6px;
    margin-top: 12px;
    padding-top: 8px;
    color: #8b949e;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 8px;
    left: 12px;
}

/* ── Scroll Bars ─────────────────────────────────────────────────────────── */
QScrollBar:vertical {
    background: #161b22;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #30363d;
    border-radius: 4px;
    min-height: 24px;
}

QScrollBar::handle:vertical:hover {
    background: #484f58;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}

QScrollBar:horizontal {
    background: #161b22;
    height: 8px;
    border-radius: 4px;
}

QScrollBar::handle:horizontal {
    background: #30363d;
    border-radius: 4px;
    min-width: 24px;
}

QScrollBar::handle:horizontal:hover {
    background: #484f58;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}

/* ── Splitter ────────────────────────────────────────────────────────────── */
QSplitter::handle {
    background-color: #30363d;
}

QSplitter::handle:horizontal {
    width: 2px;
}

QSplitter::handle:vertical {
    height: 2px;
}

/* ── Labels ──────────────────────────────────────────────────────────────── */
QLabel {
    color: #c9d1d9;
}

QLabel#section_header {
    color: #e6edf3;
    font-size: 15px;
    font-weight: 600;
}

QLabel#muted {
    color: #8b949e;
    font-size: 12px;
}

QLabel#status_ok {
    color: #3fb950;
}

QLabel#status_warn {
    color: #d29922;
}

QLabel#status_error {
    color: #f85149;
}

/* ── Status Bar ──────────────────────────────────────────────────────────── */
QStatusBar {
    background-color: #161b22;
    border-top: 1px solid #30363d;
    color: #8b949e;
}

/* ── Toolbar ─────────────────────────────────────────────────────────────── */
QToolBar {
    background-color: #161b22;
    border-bottom: 1px solid #30363d;
    spacing: 4px;
    padding: 4px;
}

/* ── Menu ────────────────────────────────────────────────────────────────── */
QMenuBar {
    background-color: #161b22;
    color: #c9d1d9;
    border-bottom: 1px solid #30363d;
}

QMenuBar::item:selected {
    background-color: #21262d;
}

QMenu {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: #c9d1d9;
}

QMenu::item {
    padding: 6px 20px;
}

QMenu::item:selected {
    background-color: #1f6feb;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background: #30363d;
    margin: 4px 0;
}

/* ── Tooltip ─────────────────────────────────────────────────────────────── */
QToolTip {
    background-color: #21262d;
    color: #e6edf3;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 8px;
}
"""

# Color constants for programmatic use
COLORS = {
    "bg_primary": "#0d1117",
    "bg_secondary": "#161b22",
    "bg_surface": "#21262d",
    "border": "#30363d",
    "text_primary": "#c9d1d9",
    "text_secondary": "#8b949e",
    "text_heading": "#e6edf3",
    "accent": "#1f6feb",
    "accent_hover": "#388bfd",
    "success": "#3fb950",
    "warning": "#d29922",
    "error": "#f85149",
    "new": "#1a3020",       # green tinted bg for new files
    "changed": "#2d2009",   # amber tinted bg for changed
    "deleted": "#3d1010",   # red tinted bg for deleted
    "new_text": "#3fb950",
    "changed_text": "#d29922",
    "deleted_text": "#f85149",
    "unchanged_text": "#484f58",
}
