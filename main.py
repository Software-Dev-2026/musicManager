#!/usr/bin/env python3
import sys
import os

# Ensure the parent directory is in the path so `musicmanager` is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QCoreApplication

from musicmanager.ui.main_window import MainWindow
from musicmanager.ui.styles import DARK_STYLESHEET


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
