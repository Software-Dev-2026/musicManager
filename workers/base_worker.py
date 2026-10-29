from PyQt6.QtCore import QThread, pyqtSignal

# Every background thread is held here until it finishes, so a running QThread
# is never garbage-collected (which aborts the app) and the main window can
# stop them all cleanly before quitting.
_threads: set[QThread] = set()


def track(thread: QThread) -> None:
    _threads.difference_update([t for t in _threads if t.isFinished()])
    _threads.add(thread)


def running_threads() -> list[QThread]:
    _threads.difference_update([t for t in _threads if t.isFinished()])
    return [t for t in _threads if t.isRunning()]


def stop_all(timeout_ms: int = 60000) -> bool:
    """Ask every running thread to cancel, then wait. True if all have stopped."""
    running = running_threads()
    for t in running:
        t.cancel()
    return all(t.wait(timeout_ms) for t in running)


class BaseWorker(QThread):
    progress = pyqtSignal(int, int, str)  # current, total, message
    log = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal(object)  # result payload

    # Shown when the user tries to quit while this worker runs
    description = "background task"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancelled = False
        track(self)

    def cancel(self):
        self._cancelled = True
