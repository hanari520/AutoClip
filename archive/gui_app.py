# -*- coding: utf-8 -*-
"""AutoClip Desktop GUI - desktop optimized layout."""
import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QColor, QPalette
from gui_app_mobile import AutoClipGUI

class DesktopAutoClipGUI(AutoClipGUI):
    WINDOW_TITLE = "AutoClip"
    SHOW_MAXIMIZED = True

def main():
    app = QApplication(sys.argv)
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    app.setStyle("Fusion")
    app.setStyleSheet("""
        QGroupBox { font-size: 12px; font-weight: bold; padding-top: 16px; margin-top: 4px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
        QTabBar::tab { padding: 6px 12px; font-size: 11px; }
        QComboBox { padding: 2px 6px; }
        QLineEdit { padding: 3px 6px; }
        QSpinBox { padding: 2px 4px; }
        QPlainTextEdit { font-family: Consolas, monospace; font-size: 11px; }
    """)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#2b2b3c"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#35354a"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#3d3d56"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e0e0e0"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#3d3d56"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e0e0e0"))
    app.setPalette(palette)
    window = DesktopAutoClipGUI()
    if DesktopAutoClipGUI.SHOW_MAXIMIZED:
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
