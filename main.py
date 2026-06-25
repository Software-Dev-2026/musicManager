#!/usr/bin/env python3
import sys
import importlib
from pathlib import Path

_here = Path(__file__).resolve().parent
_pkg = _here.name  # derived at runtime so renaming the folder still works
sys.path.insert(0, str(_here.parent))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication

MainWindow = importlib.import_module(f"{_pkg}.ui.main_window").MainWindow
DARK_STYLESHEET = importlib.import_module(f"{_pkg}.ui.styles").DARK_STYLESHEET


def main():
    QCoreApplication.setApplicationName("Music Manager")
    QCoreApplication.setOrganizationName("MusicManager")

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
