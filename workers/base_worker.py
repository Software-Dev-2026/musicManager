from PyQt6.QtCore import QThread, pyqtSignal


class BaseWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    log = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(object)  # result payload

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _progress(self, current: int, total: int, msg: str = ""):
        self.progress.emit(current, total, msg)

    def _log(self, msg: str):
        self.log.emit(msg)
