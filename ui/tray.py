"""System tray icon and menu."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon


class TrayController(QObject):
    open_requested = Signal()
    enhance_requested = Signal()
    quit_requested = Signal()

    def __init__(self, icon: QIcon, parent=None):
        super().__init__(parent)
        self.tray = QSystemTrayIcon(icon, parent)
        self.tray.setToolTip("QuickPrompt AI")

        menu = QMenu()
        open_action = QAction("Open QuickPrompt AI", menu)
        open_action.triggered.connect(self.open_requested.emit)
        menu.addAction(open_action)

        enhance_action = QAction("Enhance Selected Text", menu)
        enhance_action.triggered.connect(self.enhance_requested.emit)
        menu.addAction(enhance_action)

        menu.addSeparator()
        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(self.quit_requested.emit)
        menu.addAction(quit_action)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger or reason == QSystemTrayIcon.DoubleClick:
            self.open_requested.emit()

    def set_status(self, text: str) -> None:
        """Update the hover tooltip (dictation feedback that steals no focus)."""
        try:
            self.tray.setToolTip(text or "QuickPrompt AI")
        except Exception:
            pass

    def show(self) -> None:
        self.tray.show()

    def hide(self) -> None:
        self.tray.hide()

    def notify(self, title: str, message: str) -> None:
        try:
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 4000)
        except Exception:
            pass
