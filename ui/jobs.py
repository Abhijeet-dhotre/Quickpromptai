"""Background job helpers (Qt threads with typed signals)."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class FunctionWorker(QThread):
    """Runs a callable off the UI thread."""

    ok = Signal(object)
    fail = Signal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):  # noqa: D102 - executed on the worker thread
        try:
            result = self._fn()
        except Exception as exc:  # noqa: BLE001 - surfaced via signal
            self.fail.emit(exc)
        else:
            self.ok.emit(result)


def run_async(fn, on_ok, on_fail, parent=None, on_finished=None) -> FunctionWorker:
    """Start ``fn`` in a thread and deliver the result on the UI thread.

    The caller must keep the returned worker referenced until it finishes.
    """
    worker = FunctionWorker(fn, parent)
    worker.ok.connect(on_ok)
    worker.fail.connect(on_fail)
    if on_finished is not None:
        worker.finished.connect(on_finished)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker
