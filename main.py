#!/usr/bin/env python
"""QuickPrompt AI — application entry point.

Runs the tray icon, main window, popup, and the global hotkey. Works both in
development (``python main.py``) and frozen (``QuickPromptAI.exe``).
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

APP_NAME = "QuickPrompt AI"
APP_VERSION = "1.1.0"

_SHARED_MEM = None  # keeps the single-instance segment alive


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def asset_path(name: str) -> str:
    if getattr(sys, "frozen", False):
        base = os.path.join(getattr(sys, "_MEIPASS", ""), "assets")
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
    return os.path.join(base, name)


def load_icon():
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QLinearGradient

    for candidate in (asset_path("icon.ico"), asset_path("icon.png")):
        if os.path.exists(candidate):
            return QIcon(candidate)
    # Fallback: simple generated square so the app never starts icon-less.
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor("#4f6ef7"))
    return QIcon(pixmap)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="QuickPromptAI", add_help=True)
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="internal: build the full app offscreen, then exit (used to verify packaged builds)",
    )
    return parser.parse_known_args(argv)[0]


def acquire_single_instance() -> bool:
    """Return False when another instance is already running."""
    global _SHARED_MEM
    from PySide6.QtCore import QSharedMemory

    segment = QSharedMemory("QuickPromptAI-single-instance-v1")
    if segment.attach():
        return False  # an existing instance owns the segment
    if not segment.create(1):
        # Race or platform quirk: proceed rather than block the user.
        return True
    _SHARED_MEM = segment
    return True


# ----------------------------------------------------------------------
# Application assembly
# ----------------------------------------------------------------------
def build_app(smoke: bool = False):
    from PySide6.QtWidgets import QApplication

    from ui.controller import AppController
    from ui.main_window import MainWindow
    from ui.popup import EnhancePopup
    from ui.styles import DARK_THEME
    from ui.tray import TrayController

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("QuickPromptAI")
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(DARK_THEME)

    controller = AppController()
    first_run = not controller.settings_store.path.exists()
    configured = controller.has_api_key()

    window = MainWindow(controller)
    window.allow_hide_on_close = True
    popup = EnhancePopup()
    tray = TrayController(load_icon())

    from core.models import EnhanceMode
    from core.errors import friendly_error
    from providers.registry import get_adapter_class

    # ---- popup wiring ------------------------------------------------
    # Capture runs BEFORE any QuickPrompt window is shown: showing our popup
    # first would steal keyboard focus and the simulated Ctrl+C would land on
    # the popup instead of the user's selection. The popup appears once the
    # text is captured (or the capture failed), and shows the loading state
    # while the AI request runs.
    def start_capture_flow(mode: EnhanceMode | None = None) -> None:
        # A clean profile has no credential yet.  Opening the setup window is
        # clearer than starting capture and failing later in a worker thread.
        if not controller.has_api_key():
            window.refresh_after_show()
            window.open_provider_setup()
            window.show()
            window.raise_()
            window.activateWindow()
            window.statusBar().showMessage(
                "First-run setup: save an API key in the Provider tab, then use the shortcut.",
                10000,
            )
            return
        try:
            cls = get_adapter_class(controller.settings.active_provider)
            popup.set_models(cls.capabilities.default_models, cls.display_name)
        except Exception:
            pass
        popup.set_mode(mode) if mode is not None else None
        controller.trigger_popup_enhance(mode)

    def on_capture_ready(capture) -> None:
        popup.open_for_capture()
        popup.set_capture(capture.text, True)

    def on_popup_error(stage: str, error: Exception) -> None:
        if stage == "capture":
            popup.open_for_capture()  # let the user paste text manually
        popup.handle_error(stage, error)

    def on_popup_copy(text: str) -> None:
        if controller.copy_to_clipboard(text):
            popup.show_notice("Copied to clipboard.")
        else:
            popup.set_error("Could not access the clipboard.")

    def on_replace_done(result) -> None:
        popup.set_loading(None)
        popup.show_notice(result.message if result.verified else "Done — verify in the target app.")

    def on_replace_failed(error: Exception) -> None:
        popup.set_loading(None)
        popup.set_error(
            friendly_error(error, [controller.settings.__dict__.get("api_key")])
            + "\nUse Copy instead, then paste manually."
        )

    popup.enhance_requested.connect(
        lambda text, mode: controller.retry_popup_enhance(text, EnhanceMode(mode))
    )
    popup.retry_requested.connect(
        lambda: controller.retry_popup_enhance(
            popup.original_edit.toPlainText(), popup.current_mode()
        )
    )
    popup.replace_requested.connect(controller.replace_selected_text)
    popup.copy_requested.connect(on_popup_copy)

    def on_cancel() -> None:
        controller.cancel_popup_operation()
        popup.hide()

    popup.cancel_requested.connect(on_cancel)

    # ---- dictation (speech-to-text) ------------------------------------
    # The transcript is typed straight back into the window the user pressed
    # the shortcut in (Notepad, Word, an editor…). QuickPrompt deliberately
    # raises nothing while dictation runs: any window we pop up would steal the
    # caret the words are supposed to land at. Feedback therefore goes to the
    # tray tooltip and the main window's status bar, neither of which steals
    # focus.
    def on_dictation_started() -> None:
        window.clear_error()   # a fresh attempt replaces any stale red line
        window.show_dictation_indicator(True)
        tray.set_status("🎤 Listening…")
        if popup.isVisible():
            popup.show_notice("🎤 Listening…")

    def on_dictation_processing() -> None:
        window.show_dictation_indicator(False, processing=True)
        tray.set_status("Transcribing…")
        if popup.isVisible():
            popup.show_notice("Transcribing…")

    def on_dictation_text(text: str) -> None:
        window.clear_error()
        window.show_dictation_indicator(False)
        tray.set_status(APP_NAME)
        if not text.strip():
            window.statusBar().showMessage(
                "Dictation finished, but no speech was detected.", 5000,
            )

    def on_dictation_not_pasted(text: str, error: Exception) -> None:
        # Typing into the original window failed (it closed, or the user
        # switched apps). Put the words in our own editor so nothing is lost.
        window.clear_error()
        window.show_dictation_indicator(False)
        tray.set_status(APP_NAME)
        window.show_error(friendly_error(error) + " The text is below instead.")
        window.insert_transcript(text)

    def on_dictation_failed(error: Exception) -> None:
        window.show_dictation_indicator(False)
        tray.set_status(APP_NAME)
        message = friendly_error(error, [controller.settings.__dict__.get("api_key")])
        # Surface the failure wherever the user is looking: the popup if it is
        # open, and BOTH the red error label and the status bar of the main
        # window (the status bar alone is easy to miss).
        if popup.isVisible():
            popup.set_error(message)
        window.show_error(message)

    controller.dictation_started.connect(on_dictation_started)
    controller.dictation_processing.connect(on_dictation_processing)
    controller.dictation_text_ready.connect(on_dictation_text)
    controller.dictation_not_pasted.connect(on_dictation_not_pasted)
    controller.dictation_failed.connect(on_dictation_failed)

    controller.popup_stage.connect(lambda stage: popup.set_loading(stage or None))
    controller.popup_capture_ready.connect(on_capture_ready)
    controller.popup_result.connect(lambda _capture, result: popup.set_result(result))
    controller.popup_error.connect(on_popup_error)
    controller.popup_replace_done.connect(on_replace_done)
    controller.popup_replace_failed.connect(on_replace_failed)

    # ---- main window / tray -------------------------------------------
    tray.open_requested.connect(lambda: (window.refresh_after_show(), window.show(),
                                         window.raise_(), window.activateWindow()))
    tray.enhance_requested.connect(lambda: start_capture_flow())
    tray.quit_requested.connect(lambda: (setattr(window, "allow_hide_on_close", False),
                                         app.quit()))
    tray.show()

    controller.hotkey_fired.connect(lambda: start_capture_flow())
    # Second global hotkey: start/stop dictation. Kept separate from the
    # enhance shortcut so the two never collide, and processed on the UI thread
    # via Qt's signal marshalling (pynput fires from its own listener thread).
    controller.dictation_hotkey_fired.connect(lambda: controller.toggle_dictation())
    controller.dictation_shortcut_changed.connect(
        lambda combo: window.statusBar().showMessage(
            f"Dictation shortcut active: {combo}", 5000,
        )
    )
    controller.dictation_shortcut_failed.connect(
        lambda err: window.statusBar().showMessage(
            f"Dictation shortcut unavailable: {friendly_error(err)} "
            "Pick another combination in the Shortcut tab.", 8000,
        )
    )

    # ---- startup -------------------------------------------------------
    # Defer registering the global hook until a provider key exists.  This
    # avoids a first-run-only race and prevents a non-functional shortcut from
    # being advertised before setup has finished.
    hotkey_ok = controller.start_hotkeys() if configured else False
    show_window = smoke or first_run or not hotkey_ok
    if show_window:
        window.show()
        if configured and not hotkey_ok:
            window.statusBar().showMessage(
                "The global shortcut could not be registered — set a new one "
                "in the Shortcut tab.", 10000,
            )
        elif not configured:
            window.open_provider_setup()
            window.statusBar().showMessage(
                "First-run setup: save an API key in the Provider tab. The global shortcut will activate automatically.",
                10000,
            )
    elif not smoke:
        tray.notify(
            APP_NAME,
            f"Running in the tray. Press {controller.current_shortcut()} "
            "anywhere to enhance selected text.",
        )

    app.aboutToQuit.connect(controller.shutdown)
    return app, controller, window, popup, tray


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0

    smoke = bool(args.smoke_test)
    if smoke:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        os.environ.setdefault(
            "QUICKPROMPT_DATA_DIR", tempfile.mkdtemp(prefix="qp-smoke-")
        )

    if not smoke and not acquire_single_instance():
        return 0  # another instance is already running

    try:
        app, controller, window, popup, tray = build_app(smoke=smoke)
    except Exception as exc:  # pragma: no cover - surfaced to user
        print(f"QuickPrompt AI failed to start: {exc}", file=sys.stderr)
        return 1

    if smoke:
        # Exercise the critical paths that a packaged build must support.
        from PySide6.QtCore import QTimer
        from shortcuts.global_hotkey import parse_combo

        try:
            canonical, _fmt = parse_combo(controller.settings.shortcut)
            controller.apply_shortcut(canonical)
            controller.settings_store.save(controller.settings)
            roundtrip = controller.settings_store.load()
            assert roundtrip.shortcut == canonical
            popup.open_for_capture()
            popup.hide()
        except Exception as exc:
            print(f"SMOKE FAILED: {exc}", file=sys.stderr)
            return 1
        print("SMOKE OK")
        QTimer.singleShot(300, app.quit)

    exit_code = app.exec()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
