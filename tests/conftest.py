"""Shared pytest fixtures."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    """Session-wide QApplication (offscreen)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # Deterministic teardown: Qt signal connections from the session-scoped
    # controller keep test MainWindows/popups alive after their tests end
    # (verified: a closed+deleted window is still shiboken-valid). Without
    # this cleanup their C++ objects are destroyed at interpreter shutdown
    # in random order *relative to the QApplication itself*, aborting with
    # an access violation (0xC0000005) / fastfail (0xC0000409) after all
    # tests passed — which intermittently failed build.bat. Destroy every
    # leftover widget now, while QApplication is still valid.
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent

    for widget in app.topLevelWidgets():
        if shiboken6.isValid(widget):
            widget.close()
            widget.deleteLater()
    # Flush the DeferredDelete events so the C++ objects die immediately.
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QCoreApplication.processEvents()
    app.quit()


def pump(app, seconds: float = 1.0) -> None:
    """Process Qt events until ``seconds`` elapse (lets workers finish)."""
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
