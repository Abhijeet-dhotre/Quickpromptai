"""Floating enhancement popup.

Frameless, compact, always-on-top window that shows the captured text, the
enhanced result (editable), mode/model pickers, and Copy / Replace / Retry /
Cancel controls. Positioned near the cursor and clamped to the monitor that
contains it (multi-monitor safe).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QCursor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.models import EnhanceMode, EnhanceResult, MODEL_STRATEGIES
from core.errors import EmptySelectionError, friendly_error

from .styles import POPUP_FRAME


class EnhancePopup(QWidget):
    enhance_requested = Signal(str, str)      # (text, mode value)
    retry_requested = Signal()
    replace_requested = Signal(str)           # (enhanced text)
    cancel_requested = Signal()
    copy_requested = Signal(str)

    MIN_WIDTH = 620
    MIN_HEIGHT = 540

    def __init__(self, parent: QWidget | None = None):
        super().__init__(
            parent,
            Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint,
        )
        self.setObjectName("EnhancePopup")
        self.setMinimumSize(self.MIN_WIDTH, self.MIN_HEIGHT)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._drag_offset: QPoint | None = None
        self._result_available = False
        self._has_capture = False
        self._build_ui()
        self._wire_keys()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 12)
        root.setSpacing(8)

        # --- title bar (frameless drag handle + close) -----------------
        bar = QHBoxLayout()
        title = QLabel("QuickPrompt AI")
        title.setObjectName("PopupTitle")
        bar.addWidget(title)
        bar.addStretch(1)
        self.stage_label = QLabel("")
        self.stage_label.setObjectName("HintLabel")
        bar.addWidget(self.stage_label)
        self.close_button = QPushButton("✕")
        self.close_button.setObjectName("CloseButton")
        self.close_button.setAccessibleName("Close popup")
        self.close_button.clicked.connect(self.cancel_requested.emit)
        bar.addWidget(self.close_button)
        root.addLayout(bar)

        # --- original text ---------------------------------------------
        original_label = QLabel("Original selected text")
        original_label.setObjectName("SectionLabel")
        root.addWidget(original_label)
        self.original_edit = QPlainTextEdit()
        self.original_edit.setPlaceholderText("No text captured yet — paste or type text here.")
        self.original_edit.setFixedHeight(88)
        root.addWidget(self.original_edit)

        # --- mode / model / actions ------------------------------------
        options = QHBoxLayout()
        options.addWidget(QLabel("Mode"))
        self.mode_combo = QComboBox()
        for mode in EnhanceMode:
            self.mode_combo.addItem(mode.label, mode.value)
        self.mode_combo.setMinimumWidth(150)
        options.addWidget(self.mode_combo)

        options.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        self.model_combo.lineEdit().setPlaceholderText("default (from settings)")
        self.model_combo.setMinimumWidth(170)
        options.addWidget(self.model_combo)
        options.addStretch(1)

        self.enhance_button = QPushButton("Enhance")
        self.enhance_button.setProperty("accent", True)
        options.addWidget(self.enhance_button)
        self.retry_button = QPushButton("Try Again")
        options.addWidget(self.retry_button)
        root.addLayout(options)

        # --- busy indicator --------------------------------------------
        busy = QHBoxLayout()
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedHeight(6)
        self.busy_bar.setVisible(False)
        busy.addWidget(self.busy_bar, 1)
        root.addLayout(busy)

        # --- enhanced output -------------------------------------------
        output_label = QLabel("Enhanced prompt (editable before use)")
        output_label.setObjectName("SectionLabel")
        root.addWidget(output_label)
        self.output_edit = QPlainTextEdit()
        self.output_edit.setPlaceholderText("The enhanced prompt will appear here.")
        root.addWidget(self.output_edit, 1)

        # --- error area -------------------------------------------------
        self.error_label = QLabel("")
        self.error_label.setObjectName("ErrorLabel")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        root.addWidget(self.error_label)

        # --- action buttons ---------------------------------------------
        actions = QHBoxLayout()
        self.copy_button = QPushButton("Copy")
        self.copy_button.setEnabled(False)
        actions.addWidget(self.copy_button)
        self.replace_button = QPushButton("Replace Selected Text")
        self.replace_button.setEnabled(False)
        self.replace_button.setToolTip(
            "Paste the result over the original selection in the app it came from."
        )
        actions.addWidget(self.replace_button)
        actions.addStretch(1)
        self.cancel_button = QPushButton("Cancel")
        actions.addWidget(self.cancel_button)
        root.addLayout(actions)

        # --- connections -------------------------------------------------
        self.enhance_button.clicked.connect(self._emit_enhance)
        self.retry_button.clicked.connect(self.retry_requested.emit)
        self.copy_button.clicked.connect(
            lambda: self.copy_requested.emit(self.output_edit.toPlainText())
        )
        self.replace_button.clicked.connect(
            lambda: self.replace_requested.emit(self.output_edit.toPlainText())
        )
        self.cancel_button.clicked.connect(self.cancel_requested.emit)

        self.setStyleSheet(POPUP_FRAME)

    def _wire_keys(self) -> None:
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.cancel_requested.emit)
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self._emit_enhance)
        QShortcut(QKeySequence("Ctrl+Enter"), self, activated=self._emit_enhance)

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------
    def _emit_enhance(self) -> None:
        text = self.original_edit.toPlainText().strip()
        if not text:
            self.set_error(
                "No text to enhance. Paste some text into the box above first."
            )
            return
        self.enhance_requested.emit(text, self.current_mode().value)

    def current_mode(self) -> EnhanceMode:
        value = self.mode_combo.currentData()
        try:
            return EnhanceMode(value)
        except ValueError:
            return EnhanceMode.PROMPT_ENHANCE

    def set_mode(self, mode: EnhanceMode) -> None:
        index = self.mode_combo.findData(mode.value)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def set_models(self, models: list[str], provider_label: str) -> None:
        current = self.model_combo.currentText()
        self.model_combo.clear()
        self.model_combo.addItems(models)
        self.model_combo.setPlaceholderText(f"default ({provider_label})")
        if current:
            self.model_combo.setCurrentText("")

    def set_capture(self, text: str, has_capture: bool = True) -> None:
        self._has_capture = has_capture
        self.original_edit.setPlainText(text)
        self.replace_button.setEnabled(False)

    def insert_transcript(self, text: str) -> None:
        """Insert dictated text at the caret in the input box.

        Uses the *current* text cursor rather than replacing the content, so
        any existing draft (or previously dictated words) is preserved and the
        text lands exactly where the user last put their caret. A single space
        is added on either side when needed so dictated words never glue
        themselves to neighbouring text.
        """
        if not text:
            return
        edit = self.original_edit
        cursor = edit.textCursor()
        full = cursor.document().toPlainText()
        before = full[: cursor.position()]
        after = full[cursor.position():]
        # Dictation rarely emits reliable edge whitespace, so separate from
        # whatever is adjacent unless punctuation/whitespace already does it.
        prefix = "" if (not before or before[-1].isspace()) else " "
        suffix = "" if (not after or after[0].isspace()) else " "
        cursor.insertText(prefix + text + suffix)
        # Leave the caret just after the spoken words (before any suffix).
        cursor.setPosition(cursor.position() - len(suffix))
        edit.setTextCursor(cursor)
        edit.setFocus()

    def set_loading(self, stage: str | None) -> None:
        busy = bool(stage)
        self.busy_bar.setVisible(busy)
        self.stage_label.setText(stage or "")
        self.enhance_button.setEnabled(not busy)
        self.retry_button.setEnabled(not busy)
        self.replace_button.setEnabled(False)
        if busy:
            self.error_label.setVisible(False)
            self.setCursor(Qt.WaitCursor)
        else:
            self.unsetCursor()

    def set_result(self, result: EnhanceResult) -> None:
        self.output_edit.setPlainText(result.text)
        self.copy_button.setEnabled(True)
        self.replace_button.setEnabled(self._has_capture)
        self._result_available = True
        usage = ""
        if result.usage is not None:
            usage = f"  ·  {result.usage.summary()}"
        self.stage_label.setText(
            f"{result.provider_id}/{result.model or 'default'}{usage}"
        )
        self.output_edit.setFocus()

    def set_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setVisible(True)
        self.busy_bar.setVisible(False)
        self.enhance_button.setEnabled(True)
        self.retry_button.setEnabled(True)
        self.unsetCursor()

    def show_notice(self, message: str) -> None:
        """Non-error status text in the title bar area."""
        self.stage_label.setText(message)

    def handle_error(self, stage: str, error: Exception) -> None:
        if stage == "capture" and isinstance(error, EmptySelectionError):
            self.set_error(
                friendly_error(error) +
                "\nYou can still paste text into the box above and press Enhance."
            )
            return
        self.set_error(friendly_error(error))

    def reset_for_new_capture(self) -> None:
        self._has_capture = False
        self._result_available = False
        self.original_edit.clear()
        self.output_edit.clear()
        self.error_label.setVisible(False)
        self.stage_label.setText("")
        self.copy_button.setEnabled(False)
        self.replace_button.setEnabled(False)

    # ------------------------------------------------------------------
    # Placement & window behaviour
    # ------------------------------------------------------------------
    def position_near_cursor(self) -> None:
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:  # pragma: no cover - defensive
            return
        area = screen.availableGeometry()
        size = self.frameGeometry().size()
        x = cursor.x() + 12
        y = cursor.y() + 16
        if x + size.width() > area.right():
            x = max(area.left(), cursor.x() - size.width() - 12)
        if y + size.height() > area.bottom():
            y = max(area.top(), cursor.y() - size.height() - 16)
        self.move(x, y)

    def open_for_capture(self, mode: EnhanceMode | None = None) -> None:
        self.reset_for_new_capture()
        if mode is not None:
            self.set_mode(mode)
        self.position_near_cursor()
        self.show()
        self.raise_()
        self.activateWindow()

    # --- frameless dragging -------------------------------------------
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._drag_offset is None:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    @property
    def has_result(self) -> bool:
        return self._result_available

    def result_text(self) -> str:
        return self.output_edit.toPlainText()
