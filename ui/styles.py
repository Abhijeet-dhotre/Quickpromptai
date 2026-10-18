"""Application-wide Qt stylesheet (dark, native-feeling)."""

DARK_THEME = """
QWidget {
    background: #1e1f24;
    color: #e8e8ea;
    font-size: 13px;
}
QMainWindow, QDialog { background: #1e1f24; }
QLabel { background: transparent; }
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #2a2c33;
    border: 1px solid #3a3d46;
    border-radius: 6px;
    padding: 6px;
    selection-background-color: #4f6ef7;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #4f6ef7;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: #2a2c33;
    border: 1px solid #3a3d46;
    selection-background-color: #4f6ef7;
}
QPushButton {
    background: #34373f;
    border: 1px solid #454954;
    border-radius: 6px;
    padding: 6px 14px;
    min-height: 20px;
}
QPushButton:hover { background: #3d414a; }
QPushButton:pressed { background: #2c2f36; }
QPushButton:disabled { color: #7a7d86; background: #26282e; border-color: #34373f; }
QPushButton[accent="true"] {
    background: #4f6ef7;
    border: 1px solid #4f6ef7;
    color: #ffffff;
    font-weight: 600;
}
QPushButton[accent="true"]:hover { background: #6480ff; }
QPushButton[accent="true"]:disabled { background: #3a3d55; border-color: #3a3d55; color: #9a9db0; }
QPushButton[danger="true"] { color: #ff9b9b; }
QTabWidget::pane {
    border: 1px solid #34373f;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    color: #a9adb8;
    padding: 8px 18px;
    margin-right: 2px;
}
QTabBar::tab:selected { color: #ffffff; border-bottom: 2px solid #4f6ef7; }
QTabBar::tab:hover { color: #d5d7dd; }
QListWidget {
    background: #23252b;
    border: 1px solid #34373f;
    border-radius: 6px;
}
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QStatusBar { background: #1a1b1f; color: #a9adb8; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #454954;
    border-radius: 5px;
    min-height: 28px;
}
QScrollBar::handle:vertical:hover { background: #565b68; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #454954;
    border-radius: 5px;
    min-width: 28px;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QProgressBar {
    background: #2a2c33;
    border: none;
    border-radius: 3px;
    max-height: 6px;
    min-height: 6px;
    text-align: center;
    color: transparent;
}
QProgressBar::chunk { background: #4f6ef7; border-radius: 3px; }
QToolTip {
    background: #2a2c33;
    color: #e8e8ea;
    border: 1px solid #454954;
    padding: 4px;
}
QMenu { background: #23252b; border: 1px solid #3a3d46; }
QMenu::item { padding: 6px 24px; }
QMenu::item:selected { background: #4f6ef7; }
QMenu::separator { height: 1px; background: #3a3d46; margin: 4px 8px; }
"""

POPUP_FRAME = """
EnhancePopup {
    background: #1e1f24;
    border: 1px solid #3a3d46;
    border-radius: 10px;
}
#PopupTitle { font-size: 14px; font-weight: 600; color: #ffffff; }
#CloseButton {
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 2px 8px;
    color: #a9adb8;
    font-size: 15px;
}
#CloseButton:hover { background: #c94040; color: #ffffff; }
#ErrorLabel { color: #ff9b9b; }
#HintLabel { color: #8b8f9a; }
#SectionLabel { color: #a9adb8; font-weight: 600; }
"""
