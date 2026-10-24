"""Main application window: manual enhancement, provider configuration,
shortcut customization, and usage history."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from core.config import AppSettings, DEFAULT_SHORTCUT, DEFAULT_DICTATION_SHORTCUT
from core.enhancement import MODEL_STRATEGY_INSTRUCTIONS
from core.errors import ShortcutError, friendly_error
from core.models import EnhanceMode
from providers.registry import all_adapters
from shortcuts.global_hotkey import parse_combo

from .controller import AppController
from .jobs import run_async

_PROVIDER_LABELS = {cls.id: cls.display_name for cls in all_adapters()}
_PROVIDER_MODELS = {cls.id: list(cls.capabilities.default_models) for cls in all_adapters()}
_PROVIDER_BASE_URLS = {cls.id: cls.capabilities.default_base_url for cls in all_adapters()}


def _qt_key_name(key: int) -> str | None:
    """Map a Qt key code to a shortcut-combo key name (or None)."""
    from PySide6.QtCore import Qt as _Qt

    if _Qt.Key_A <= key <= _Qt.Key_Z:
        return chr(key).lower()
    if _Qt.Key_0 <= key <= _Qt.Key_9:
        return chr(key)
    if _Qt.Key_F1 <= key <= _Qt.Key_F24:
        return f"F{key - _Qt.Key_F1 + 1}"
    named = {
        _Qt.Key_Space: "Space", _Qt.Key_Tab: "Tab", _Qt.Key_Return: "Enter",
        _Qt.Key_Enter: "Enter", _Qt.Key_Backspace: "Backspace",
        _Qt.Key_Delete: "Delete", _Qt.Key_Insert: "Insert",
        _Qt.Key_Home: "Home", _Qt.Key_End: "End",
        _Qt.Key_PageUp: "PgUp", _Qt.Key_PageDown: "PgDn",
        _Qt.Key_Up: "Up", _Qt.Key_Down: "Down",
        _Qt.Key_Left: "Left", _Qt.Key_Right: "Right",
    }
    return named.get(key)


class ShortcutEdit(QLineEdit):
    """Line edit that can record a key combination from the user."""

    recordingChanged = Signal(bool)
    invalidCombo = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._recording = False

    @property
    def recording(self) -> bool:
        return self._recording

    def start_recording(self) -> None:
        self._recording = True
        self.clear()
        self.setPlaceholderText("Press the new shortcut… (Esc cancels)")
        self.grabKeyboard()
        self.recordingChanged.emit(True)

    def stop_recording(self) -> None:
        if not self._recording:
            return
        self._recording = False
        self.releaseKeyboard()
        self.setPlaceholderText(f"Current: {self.text() or DEFAULT_SHORTCUT}")
        self.recordingChanged.emit(False)

    def keyPressEvent(self, event) -> None:
        if not self._recording:
            super().keyPressEvent(event)
            return
        from PySide6.QtCore import Qt as _Qt

        key = event.key()
        if key in (_Qt.Key_Escape, _Qt.Key_unknown):
            self.stop_recording()
            return
        mods = []
        qmods = event.modifiers()
        if qmods & _Qt.ControlModifier:
            mods.append("Ctrl")
        if qmods & _Qt.ShiftModifier:
            mods.append("Shift")
        if qmods & _Qt.AltModifier:
            mods.append("Alt")
        if qmods & _Qt.MetaModifier:
            mods.append("Win")
        name = _qt_key_name(key)
        if name is None:
            return  # still waiting for a usable key
        if not mods:
            self.invalidCombo.emit("Add at least one modifier (Ctrl, Shift, Alt or Win).")
            return
        try:
            canonical, _ = parse_combo("+".join(mods + [name]))
        except ShortcutError as exc:
            self.invalidCombo.emit(exc.message)
            return
        self.setText(canonical)
        self.stop_recording()


class MainWindow(QMainWindow):
    """Settings, provider configuration, shortcut and history management."""

    def __init__(self, controller: AppController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.allow_hide_on_close = False
        self._manual_worker = None
        self._draft = AppSettings.from_dict(controller.settings.to_dict())
        self._history_entries = []

        self.setWindowTitle("QuickPrompt AI — Settings")
        self.setMinimumSize(760, 620)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_enhance_tab(), "Enhance")
        self.tabs.addTab(self._build_provider_tab(), "Provider")
        self.tabs.addTab(self._build_shortcut_tab(), "Shortcut")
        self.tabs.addTab(self._build_history_tab(), "History")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.addWidget(self.tabs)
        self.setCentralWidget(central)

        status = QStatusBar()
        self.setStatusBar(status)
        self.controller.shortcut_changed.connect(
            lambda combo: status.showMessage(f"Global shortcut active: {combo}", 5000)
        )
        self.controller.shortcut_failed.connect(
            lambda err: status.showMessage(friendly_error(err), 8000)
        )
        self.controller.test_finished.connect(self._on_test_finished)

        self._load_provider_tab()
        self._refresh_shortcut_label()
        self._refresh_history_list()

    # ==================================================================
    # Enhance tab (manual workflow — no global shortcut required)
    # ==================================================================
    def _build_enhance_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)

        layout.addWidget(QLabel("Text to enhance (paste here, or select text anywhere and press the shortcut)"))
        self.source_edit = QPlainTextEdit()
        self.source_edit.setPlaceholderText("Type or paste rough text / a draft prompt…")
        self.source_edit.setFixedHeight(130)
        layout.addWidget(self.source_edit)

        row = QHBoxLayout()
        row.addWidget(QLabel("Mode"))
        self.manual_mode = QComboBox()
        for mode in EnhanceMode:
            self.manual_mode.addItem(mode.label, mode.value)
        row.addWidget(self.manual_mode)
        paste_button = QPushButton("Paste from Clipboard")
        paste_button.clicked.connect(self._paste_from_clipboard)
        row.addWidget(paste_button)
        # Speech-to-text: press to start recording, press again to stop. The
        # transcript is inserted at the caret in the typing area above. Bound
        # to the dictation shortcut globally, so this button is an equivalent
        # mouse-driven path for the same feature.
        self.dictate_button = QPushButton("🎤 Dictate")
        self.dictate_button.setToolTip(
            "Record from the microphone and insert the transcript here.\n"
            "Keyboard shortcut works too (see the Shortcut tab)."
        )
        self.dictate_button.setCheckable(True)
        self.dictate_button.clicked.connect(self._toggle_dictation_clicked)
        row.addWidget(self.dictate_button)
        row.addStretch(1)
        self.manual_enhance = QPushButton("Enhance")
        self.manual_enhance.setProperty("accent", True)
        self.manual_enhance.clicked.connect(self._enhance_clicked)
        row.addWidget(self.manual_enhance)
        layout.addLayout(row)

        self.manual_busy = QProgressBar()
        self.manual_busy.setRange(0, 0)
        self.manual_busy.setVisible(False)
        self.manual_busy.setFixedHeight(6)
        layout.addWidget(self.manual_busy)

        self.manual_error = QLabel("")
        self.manual_error.setObjectName("ErrorLabel")
        self.manual_error.setWordWrap(True)
        self.manual_error.setVisible(False)
        layout.addWidget(self.manual_error)

        layout.addWidget(QLabel("Enhanced result (editable)"))
        self.manual_output = QPlainTextEdit()
        self.manual_output.setPlaceholderText("The enhanced prompt will appear here.")
        layout.addWidget(self.manual_output, 1)

        bottom = QHBoxLayout()
        self.manual_usage = QLabel("")
        bottom.addWidget(self.manual_usage)
        bottom.addStretch(1)
        copy_button = QPushButton("Copy Result")
        copy_button.clicked.connect(
            lambda: self._copy_text(self.manual_output.toPlainText())
        )
        bottom.addWidget(copy_button)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(lambda: (
            self.source_edit.clear(), self.manual_output.clear(),
            self.manual_error.setVisible(False), self.manual_usage.setText(""),
        ))
        bottom.addWidget(clear_button)
        layout.addLayout(bottom)
        return tab

    def _paste_from_clipboard(self) -> None:
        from windows_integration import clipboard as clipboard_mod

        try:
            text = clipboard_mod.get_text()
        except Exception as exc:
            self._show_manual_error(friendly_error(exc))
            return
        if text:
            self.source_edit.setPlainText(text)

    def insert_transcript(self, text: str) -> None:
        """Insert dictated text at the caret in the typing area.

        Preserves whatever is already there and drops the words exactly at the
        caret, mirroring :meth:`EnhancePopup.insert_transcript`. A space is
        added on either side only when needed, so words never merge.
        """
        if not text:
            return
        cursor = self.source_edit.textCursor()
        full = cursor.document().toPlainText()
        before = full[: cursor.position()]
        after = full[cursor.position():]
        prefix = "" if (not before or before[-1].isspace()) else " "
        suffix = "" if (not after or after[0].isspace()) else " "
        cursor.insertText(prefix + text + suffix)
        cursor.setPosition(cursor.position() - len(suffix))
        self.source_edit.setTextCursor(cursor)
        self.source_edit.setFocus()

    # ------------------------------------------------------------------
    # Dictation (speech-to-text)
    # ------------------------------------------------------------------
    def _toggle_dictation_clicked(self) -> None:
        # The button is checkable, but the controller owns the real state —
        # the shortcut can also start recording, so sync from the controller.
        self.controller.toggle_dictation()
        self.dictate_button.setChecked(self.controller.is_dictating)

    def show_dictation_indicator(self, recording: bool, processing: bool = False) -> None:
        """Reflect dictation state in the button and the status bar."""
        self.dictate_button.blockSignals(True)
        self.dictate_button.setChecked(recording)
        if recording:
            self.dictate_button.setText("■ Stop Dictation")
            self.dictate_button.setToolTip(
                "Recording… press to stop and transcribe."
            )
        else:
            self.dictate_button.setText("🎤 Dictate")
            self.dictate_button.setToolTip(
                "Record from the microphone and insert the transcript here.\n"
                "Keyboard shortcut works too (see the Shortcut tab)."
            )
        self.dictate_button.blockSignals(False)
        if recording:
            self.tabs.setCurrentIndex(0)  # show the Enhance/typing tab
            self.statusBar().showMessage("🎤 Listening… speak now.", 0)
        elif processing:
            self.statusBar().showMessage("Transcribing your recording…", 0)
        else:
            self.statusBar().clearMessage()

    def _enhance_clicked(self) -> None:
        text = self.source_edit.toPlainText()
        mode = EnhanceMode(self.manual_mode.currentData())
        self.manual_error.setVisible(False)
        self.manual_busy.setVisible(True)
        self.manual_enhance.setEnabled(False)

        def job():
            return self.controller.enhance_text(text, mode)

        self._manual_worker = run_async(
            job, self._on_manual_ok, self._on_manual_fail, parent=self,
            on_finished=self._manual_finished,
        )

    def _on_manual_ok(self, result) -> None:
        self.manual_output.setPlainText(result.text)
        if result.usage is not None:
            self.manual_usage.setText(
                f"{result.provider_id}/{result.model or 'default'} · "
                f"{result.usage.summary()}"
            )

    def _on_manual_fail(self, error: Exception) -> None:
        self._show_manual_error(friendly_error(error))

    def _manual_finished(self) -> None:
        self.manual_busy.setVisible(False)
        self.manual_enhance.setEnabled(True)
        self._manual_worker = None

    def _show_manual_error(self, message: str) -> None:
        self.manual_error.setText(message + "\nYou can retry with the Enhance button.")
        self.manual_error.setVisible(True)

    def show_error(self, message: str) -> None:
        """Show a red, unmissable error line in the Enhance tab + status bar.

        Used by dictation failures so errors never disappear into the status
        bar alone. Cleared automatically on the next successful action.
        """
        self.manual_error.setText(message)
        self.manual_error.setVisible(True)
        self.statusBar().showMessage(message, 8000)

    def clear_error(self) -> None:
        """Hide the red error line (called when a new attempt succeeds/starts)."""
        self.manual_error.setVisible(False)
        self.manual_error.setText("")

    def _copy_text(self, text: str) -> None:
        if not text:
            return
        ok = self.controller.copy_to_clipboard(text)
        self.statusBar().showMessage(
            "Copied to clipboard." if ok else "Could not access the clipboard.",
            4000,
        )

    # ==================================================================
    # Provider tab
    # ==================================================================
    def _build_provider_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        form_box = QGroupBox("Provider & API configuration")
        form = QFormLayout(form_box)

        self.provider_combo = QComboBox()
        for cls in all_adapters():
            self.provider_combo.addItem(cls.display_name, cls.id)
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("Provider", self.provider_combo)

        key_row = QHBoxLayout()
        self.api_key_edit = QLineEdit()
        self.api_key_edit.setEchoMode(QLineEdit.Password)
        self.api_key_edit.setPlaceholderText("Paste your API key here (stored securely)")
        key_row.addWidget(self.api_key_edit, 1)
        self.save_key_button = QPushButton("Save Key")
        self.save_key_button.clicked.connect(self._save_key)
        key_row.addWidget(self.save_key_button)
        self.remove_key_button = QPushButton("Remove")
        self.remove_key_button.setProperty("danger", True)
        self.remove_key_button.clicked.connect(self._remove_key)
        key_row.addWidget(self.remove_key_button)
        form.addRow("API key", key_row)
        self.key_status_label = QLabel("")
        form.addRow("", self.key_status_label)

        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.NoInsert)
        form.addRow("Model ID", self.model_combo)

        self.base_url_edit = QLineEdit()
        form.addRow("API base URL", self.base_url_edit)

        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(1, 300)
        self.timeout_spin.setSuffix(" s")
        form.addRow("Timeout", self.timeout_spin)

        self.max_tokens_spin = QSpinBox()
        self.max_tokens_spin.setRange(0, 32768)
        self.max_tokens_spin.setSpecialValueText("Provider default")
        form.addRow("Max output tokens", self.max_tokens_spin)

        temp_row = QHBoxLayout()
        self.temp_check = QCheckBox("Set temperature")
        temp_row.addWidget(self.temp_check)
        self.temp_spin = QDoubleSpinBox()
        self.temp_spin.setRange(0.0, 2.0)
        self.temp_spin.setSingleStep(0.1)
        self.temp_spin.setEnabled(False)
        temp_row.addWidget(self.temp_spin)
        temp_row.addStretch(1)
        self.temp_check.toggled.connect(self.temp_spin.setEnabled)
        form.addRow("Temperature", temp_row)

        self.strategy_combo = QComboBox()
        for key in ("universal", "chatgpt", "claude", "gemini"):
            self.strategy_combo.addItem(key.capitalize(), key)
        form.addRow("Prompt style", self.strategy_combo)
        self.strategy_hint = QLabel("")
        self.strategy_hint.setWordWrap(True)
        self.strategy_hint.setObjectName("HintLabel")
        self.strategy_combo.currentIndexChanged.connect(self._update_strategy_hint)
        form.addRow("", self.strategy_hint)

        self.max_input_spin = QSpinBox()
        self.max_input_spin.setRange(200, 200_000)
        self.max_input_spin.setSingleStep(2000)
        self.max_input_spin.setSpecialValueText("No limit")
        form.addRow("Max input characters", self.max_input_spin)

        # Speech-to-text model. Dictation reuses this provider's endpoint and
        # key — only the model differs from the chat model above.
        self.whisper_model_edit = QLineEdit()
        self.whisper_model_edit.setPlaceholderText(
            "auto — e.g. whisper-1 (OpenAI) · whisper-large-v3-turbo (Groq)"
        )
        self.whisper_model_edit.setToolTip(
            "Model sent to the /audio/transcriptions endpoint when you "
            "dictate. Leave EMPTY to use your provider's default Whisper "
            "model. Only set this if you know your provider's transcription "
            "model ID — IDs are not shared between providers."
        )
        form.addRow("Dictation (Whisper) model", self.whisper_model_edit)

        layout.addWidget(form_box)

        actions = QHBoxLayout()
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self._test_connection)
        actions.addWidget(self.test_button)
        self.save_settings_button = QPushButton("Save Settings")
        self.save_settings_button.setProperty("accent", True)
        self.save_settings_button.clicked.connect(self._save_settings)
        actions.addWidget(self.save_settings_button)
        actions.addStretch(1)
        self.test_status = QLabel("")
        self.test_status.setWordWrap(True)
        actions.addWidget(self.test_status, 1)
        layout.addLayout(actions)

        note = QLabel(
            "Privacy: your selected text is sent to the configured provider only "
            "when you trigger an enhancement (shortcut or Enhance button). API keys "
            "are stored in Windows Credential Manager — never in settings files or "
            "logs. API access and any charges depend on your provider account."
        )
        note.setWordWrap(True)
        note.setObjectName("HintLabel")
        layout.addWidget(note)
        layout.addStretch(1)
        return tab

    def _load_provider_tab(self) -> None:
        d = self._draft
        index = self.provider_combo.findData(d.active_provider)
        self.provider_combo.setCurrentIndex(max(0, index))
        self._on_provider_changed()
        self.timeout_spin.setValue(int(d.timeout))
        self.max_tokens_spin.setValue(d.max_tokens)
        self.temp_check.setChecked(d.temperature_enabled)
        self.temp_spin.setValue(d.temperature)
        si = self.strategy_combo.findData(d.model_strategy)
        self.strategy_combo.setCurrentIndex(max(0, si))
        self.max_input_spin.setValue(d.max_input_chars)
        self.whisper_model_edit.setText(d.whisper_model)
        self._update_key_status()

    def _on_provider_changed(self) -> None:
        provider_id = self.provider_combo.currentData()
        caps = next(
            cls.capabilities for cls in all_adapters() if cls.id == provider_id
        )
        # Model list: defaults + saved value
        saved_model = self._draft.models.get(provider_id, "")
        self.model_combo.clear()
        self.model_combo.addItems(_PROVIDER_MODELS.get(provider_id, []))
        if saved_model and self.model_combo.findText(saved_model) < 0:
            self.model_combo.setEditText(saved_model)
        else:
            self.model_combo.setCurrentText(saved_model or caps.default_model)

        saved_url = self._draft.base_urls.get(provider_id, "")
        self.base_url_edit.setText(saved_url)
        default_url = _PROVIDER_BASE_URLS.get(provider_id, "")
        self.base_url_edit.setPlaceholderText(
            default_url or "https://api.example.com/v1  (required)"
        )
        self.api_key_edit.clear()
        self._update_key_status()

    def _update_key_status(self) -> None:
        provider_id = self.provider_combo.currentData()
        has_key = self.controller.has_api_key(provider_id)
        _, backend = self.controller.api_key_status()
        label = _PROVIDER_LABELS.get(provider_id, provider_id)
        if has_key:
            self.key_status_label.setText(
                f"✓ Key stored for {label} — backend: {backend}"
            )
        else:
            self.key_status_label.setText(
                f"No key stored for {label} — backend: {backend}"
            )

    def _update_strategy_hint(self) -> None:
        key = self.strategy_combo.currentData()
        hint = MODEL_STRATEGY_INSTRUCTIONS.get(key, "").strip()
        first_line = hint.splitlines()[0] if hint else "Neutral formatting for any provider."
        self.strategy_hint.setText(first_line)

    def _collect_draft(self) -> AppSettings:
        d = self._draft
        d.active_provider = self.provider_combo.currentData()
        d.models[d.active_provider] = self.model_combo.currentText().strip()
        d.base_urls[d.active_provider] = self.base_url_edit.text().strip()
        d.timeout = float(self.timeout_spin.value())
        d.max_tokens = self.max_tokens_spin.value()
        d.temperature_enabled = self.temp_check.isChecked()
        d.temperature = float(self.temp_spin.value())
        d.model_strategy = self.strategy_combo.currentData()
        d.max_input_chars = self.max_input_spin.value()
        # Empty falls back to the default Whisper model in normalize().
        d.whisper_model = self.whisper_model_edit.text().strip()
        return d

    def _save_settings(self) -> None:
        try:
            self.controller.save_settings(self._collect_draft())
        except Exception as exc:
            self.test_status.setText(friendly_error(exc))
            return
        self.test_status.setText("Settings saved.")
        self._update_key_status()
        self._activate_shortcut_if_ready()

    def _save_key(self) -> None:
        key = self.api_key_edit.text().strip()
        if not key:
            self.test_status.setText("Enter an API key first.")
            return
        provider_id = self.provider_combo.currentData()
        try:
            self.controller.save_api_key(key, provider_id)
        except Exception as exc:
            self.test_status.setText(friendly_error(exc))
            return
        self.api_key_edit.clear()
        self._update_key_status()
        self.test_status.setText(
            f"API key saved for {_PROVIDER_LABELS.get(provider_id, provider_id)}."
        )
        self._activate_shortcut_if_ready()

    def _activate_shortcut_if_ready(self) -> None:
        """Finish deferred first-run shortcut registration after key setup."""
        if self.controller.hotkeys_active or not self.controller.has_api_key():
            return
        if self.controller.start_hotkeys():
            self.statusBar().showMessage(
                f"Setup complete. Global shortcut active: {self.controller.current_shortcut()}",
                8000,
            )

    def open_provider_setup(self) -> None:
        """Bring an unconfigured first run directly to its required setup."""
        self.tabs.setCurrentIndex(1)
        self.provider_combo.setFocus()

    def _remove_key(self) -> None:
        provider_id = self.provider_combo.currentData()
        removed = self.controller.delete_api_key(provider_id)
        self._update_key_status()
        self.test_status.setText(
            "Stored key removed." if removed else "No stored key to remove."
        )

    def _test_connection(self) -> None:
        draft = self._collect_draft()
        provider_id = draft.active_provider
        api_key = self.api_key_edit.text().strip() or None
        if not api_key and not self.controller.has_api_key(provider_id):
            self.test_status.setText(
                "Save an API key first (it is stored in Windows Credential Manager)."
            )
            return
        self.test_button.setEnabled(False)
        self.test_status.setText("Testing connection…")
        self.controller.test_connection_async(
            provider_id,
            api_key or self._stored_key(provider_id),
            draft.models.get(provider_id, ""),
            draft.base_urls.get(provider_id, ""),
            draft.timeout,
        )

    def _stored_key(self, provider_id: str) -> str | None:
        from security.credstore import load_secret

        return load_secret(provider_id)

    def _on_test_finished(self, result) -> None:
        self.test_button.setEnabled(True)
        if hasattr(result, "ok"):
            self.test_status.setText("✓ " + result.message)
        else:
            self.test_status.setText(friendly_error(result))

    # ==================================================================
    # Shortcut tab
    # ==================================================================
    def _build_shortcut_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)

        info = QLabel(
            "The global shortcut works in any application: select text, press "
            "the shortcut, and QuickPrompt AI enhances it and shows the result "
            "in a popup. Windows reserves some combinations (Win+letter, "
            "Ctrl+Alt+Del) — if registration fails, pick another one."
        )
        info.setWordWrap(True)
        info.setObjectName("HintLabel")
        layout.addWidget(info)

        row = QHBoxLayout()
        row.addWidget(QLabel("Shortcut"))
        self.shortcut_edit = ShortcutEdit()
        self.shortcut_edit.setReadOnly(False)
        self.shortcut_edit.setMaximumWidth(260)
        self.shortcut_edit.invalidCombo.connect(
            lambda msg: self.shortcut_status.setText(msg)
        )
        row.addWidget(self.shortcut_edit)
        self.record_button = QPushButton("Record")
        self.record_button.clicked.connect(self._toggle_record)
        self.shortcut_edit.recordingChanged.connect(
            lambda on: self.record_button.setText("Stop" if on else "Record")
        )
        row.addWidget(self.record_button)
        row.addStretch(1)
        layout.addLayout(row)

        actions = QHBoxLayout()
        apply_button = QPushButton("Apply Shortcut")
        apply_button.setProperty("accent", True)
        apply_button.clicked.connect(self._apply_shortcut)
        actions.addWidget(apply_button)
        default_button = QPushButton("Reset to Default (Ctrl+Shift+E)")
        default_button.clicked.connect(
            lambda: self.shortcut_edit.setText(DEFAULT_SHORTCUT)
        )
        actions.addWidget(default_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.shortcut_status = QLabel("")
        self.shortcut_status.setWordWrap(True)
        layout.addWidget(self.shortcut_status)

        # --- dictation (speech-to-text) shortcut -----------------------
        divider = QLabel(" ")
        layout.addWidget(divider)
        dictation_info = QLabel(
            "The dictation shortcut toggles voice input from anywhere: press "
            "once to start recording, speak, then press again to stop. The "
            "transcript is inserted at the cursor in the typing area. It is "
            "sent to your provider's Whisper endpoint. It must differ from the "
            "enhance shortcut above."
        )
        dictation_info.setWordWrap(True)
        dictation_info.setObjectName("HintLabel")
        layout.addWidget(dictation_info)

        drow = QHBoxLayout()
        drow.addWidget(QLabel("Dictation"))
        self.dictation_shortcut_edit = ShortcutEdit()
        self.dictation_shortcut_edit.setReadOnly(False)
        self.dictation_shortcut_edit.setMaximumWidth(260)
        self.dictation_shortcut_edit.invalidCombo.connect(
            lambda msg: self.dictation_shortcut_status.setText(msg)
        )
        drow.addWidget(self.dictation_shortcut_edit)
        self.dictation_record_button = QPushButton("Record")
        self.dictation_record_button.clicked.connect(self._toggle_dictation_record)
        self.dictation_shortcut_edit.recordingChanged.connect(
            lambda on: self.dictation_record_button.setText("Stop" if on else "Record")
        )
        drow.addWidget(self.dictation_record_button)
        drow.addStretch(1)
        layout.addLayout(drow)

        dactions = QHBoxLayout()
        dictation_apply_button = QPushButton("Apply Dictation Shortcut")
        dictation_apply_button.setProperty("accent", True)
        dictation_apply_button.clicked.connect(self._apply_dictation_shortcut)
        dactions.addWidget(dictation_apply_button)
        dictation_default_button = QPushButton("Reset to Default (Ctrl+Shift+Q)")
        dictation_default_button.clicked.connect(
            lambda: self.dictation_shortcut_edit.setText(DEFAULT_DICTATION_SHORTCUT)
        )
        dactions.addWidget(dictation_default_button)
        dactions.addStretch(1)
        layout.addLayout(dactions)

        self.dictation_shortcut_status = QLabel("")
        self.dictation_shortcut_status.setWordWrap(True)
        layout.addWidget(self.dictation_shortcut_status)

        # Keep the editors in sync with the controller (also after a
        # first-run deferred registration).
        self.controller.dictation_shortcut_changed.connect(
            self._refresh_dictation_shortcut_label
        )
        self.controller.dictation_shortcut_failed.connect(
            lambda err: self.dictation_shortcut_status.setText(
                "Could not register this combination: " + friendly_error(err)
            )
        )

        layout.addStretch(1)
        return tab

    def _toggle_record(self) -> None:
        if self.shortcut_edit.recording:
            self.shortcut_edit.stop_recording()
        else:
            self.shortcut_edit.start_recording()

    def _toggle_dictation_record(self) -> None:
        if self.dictation_shortcut_edit.recording:
            self.dictation_shortcut_edit.stop_recording()
        else:
            self.dictation_shortcut_edit.start_recording()

    def _refresh_dictation_shortcut_label(self, combo: str | None = None) -> None:
        current = combo or self.controller.current_dictation_shortcut()
        self.dictation_shortcut_edit.setText(current)
        self.dictation_shortcut_edit.setPlaceholderText(f"Current: {current}")

    def _apply_dictation_shortcut(self) -> None:
        combo = self.dictation_shortcut_edit.text().strip()
        if not combo:
            self.dictation_shortcut_status.setText("Enter or record a shortcut first.")
            return
        try:
            canonical = self.controller.apply_dictation_shortcut(combo)
        except ShortcutError as exc:
            self.dictation_shortcut_status.setText(exc.message)
            return
        except Exception as exc:
            self.dictation_shortcut_status.setText(friendly_error(exc))
            return
        self._draft.dictation_shortcut = canonical
        self._refresh_dictation_shortcut_label(canonical)
        self.dictation_shortcut_status.setText(f"✓ Dictation shortcut active: {canonical}")

    def _refresh_shortcut_label(self) -> None:
        current = self.controller.current_shortcut()
        self.shortcut_edit.setText(current)
        self.shortcut_edit.setPlaceholderText(f"Current: {current}")
        self._refresh_dictation_shortcut_label()

    def _apply_shortcut(self) -> None:
        combo = self.shortcut_edit.text().strip()
        if not combo:
            self.shortcut_status.setText("Enter or record a shortcut first.")
            return
        try:
            canonical = self.controller.apply_shortcut(combo)
        except ShortcutError as exc:
            self.shortcut_status.setText(exc.message)
            return
        except Exception as exc:
            self.shortcut_status.setText(friendly_error(exc))
            return
        self._draft.shortcut = canonical
        self._refresh_shortcut_label()
        self.shortcut_status.setText(f"✓ Shortcut active: {canonical}")

    # ==================================================================
    # History tab
    # ==================================================================
    def _build_history_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)

        controls = QHBoxLayout()
        self.history_check = QCheckBox("Keep local history of enhancements")
        controls.addWidget(self.history_check)
        controls.addStretch(1)
        clear_button = QPushButton("Clear History")
        clear_button.setProperty("danger", True)
        clear_button.clicked.connect(self._clear_history)
        controls.addWidget(clear_button)
        layout.addLayout(controls)

        self.history_list = QListWidget()
        self.history_list.currentItemChanged.connect(self._history_selected)
        layout.addWidget(self.history_list, 2)

        layout.addWidget(QLabel("Details (double-click an entry to load it into the Enhance tab)"))
        self.history_detail = QPlainTextEdit()
        self.history_detail.setReadOnly(True)
        self.history_detail.setPlaceholderText("Select a history entry…")
        layout.addWidget(self.history_detail, 1)

        copy_button = QPushButton("Copy Enhanced Text")
        copy_button.clicked.connect(lambda: self._copy_text(
            self.history_detail.toPlainText().split("Enhanced:\n", 1)[-1]
        ))
        layout.addWidget(copy_button)

        self.history_check.toggled.connect(self._toggle_history)
        return tab

    def _toggle_history(self, enabled: bool) -> None:
        self._draft.history_enabled = enabled
        self.controller.settings.history_enabled = enabled
        try:
            self.controller.settings_store.save(self.controller.settings)
        except Exception:
            pass

    def _clear_history(self) -> None:
        self.controller.clear_history()
        self._refresh_history_list()
        self.history_detail.clear()

    def _refresh_history_list(self) -> None:
        self.history_check.setChecked(self.controller.settings.history_enabled)
        entries = self.controller.history_store.list(limit=200)
        self._history_entries = entries
        self.history_list.clear()
        for entry in entries:
            stamp = datetime.fromtimestamp(entry.timestamp).strftime("%Y-%m-%d %H:%M")
            item = QListWidgetItem(
                f"{stamp}  [{entry.mode}] {entry.provider_id}/{entry.model or 'default'} — "
                f"{entry.preview()}"
            )
            self.history_list.addItem(item)

    def _history_selected(self, current: QListWidgetItem | None, _previous) -> None:
        if current is None:
            return
        index = self.history_list.row(current)
        if 0 <= index < len(self._history_entries):
            entry = self._history_entries[index]
            self.history_detail.setPlainText(
                f"Time: {datetime.fromtimestamp(entry.timestamp)}\n"
                f"Mode: {entry.mode}\n"
                f"Provider: {entry.provider_id} / {entry.model or 'default'}\n"
                f"Tokens: {entry.input_tokens} in, {entry.output_tokens} out\n\n"
                f"Original:\n{entry.original}\n\nEnhanced:\n{entry.enhanced}"
            )

    # ==================================================================
    # Window behaviour
    # ==================================================================
    def refresh_after_show(self) -> None:
        self._load_provider_tab()
        self._refresh_shortcut_label()
        self._refresh_history_list()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.allow_hide_on_close:
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)
