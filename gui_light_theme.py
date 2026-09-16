# -*- coding: utf-8 -*-
"""AutoClip GUI theme tokens and shared QSS.

This file is intentionally separated from the new GUI entry so the redesign has
a single source of truth for colors, spacing, and widget states.
"""

LIGHT_COLORS = {
    "bg": "#EEF1F5",
    "card": "#FFFFFF",
    "input": "#FFFFFF",
    "hover": "#F2F6FF",
    "pressed": "#E8F0FF",
    "active": "#E8F0FF",
    "border": "#D0D5DD",
    "primary": "#4080FF",
    "primary_hover": "#2F6FED",
    "primary_pressed": "#1F5FDB",
    "secondary": "#FF8822",
    "secondary_hover": "#F07812",
    "secondary_pressed": "#D86408",
    "success": "#00B42A",
    "warning": "#FF7D00",
    "error": "#F53F3F",
    "text": "#111827",
    "muted": "#4E5969",
    "disabled": "#98A2B3",
    "progress_track": "#D0D5DD",
    "purple": "#8B5CF6",
    "video_blue": "#4080FF",
    "mp4_green": "#00B42A",
    "srt_purple": "#8B5CF6",
}

DARK_COLORS = {
    "bg": "#111827",
    "card": "#1F2937",
    "input": "#111827",
    "hover": "#263244",
    "pressed": "#1E3A5F",
    "active": "#173B6D",
    "border": "#374151",
    "primary": "#60A5FA",
    "primary_hover": "#3B82F6",
    "primary_pressed": "#2563EB",
    "secondary": "#FB923C",
    "secondary_hover": "#F97316",
    "secondary_pressed": "#EA580C",
    "success": "#22C55E",
    "warning": "#F59E0B",
    "error": "#F87171",
    "text": "#F9FAFB",
    "muted": "#CBD5E1",
    "disabled": "#6B7280",
    "progress_track": "#374151",
    "purple": "#A78BFA",
    "video_blue": "#60A5FA",
    "mp4_green": "#22C55E",
    "srt_purple": "#A78BFA",
}

COLORS = dict(LIGHT_COLORS)


def set_theme_colors(theme_name):
    """Mutate the shared COLORS dict so existing imports keep working."""
    source = DARK_COLORS if theme_name == "dark" else LIGHT_COLORS
    COLORS.clear()
    COLORS.update(source)
    return COLORS


def build_app_qss(colors=None):
    colors = colors or COLORS
    return f"""
QMainWindow, QWidget {{
    background: {colors["bg"]};
    color: {colors["text"]};
    font-size: 14px;
}}

QWidget[card="true"], QGroupBox {{
    background: {colors["card"]};
    border: 1px solid {colors["border"]};
    border-radius: 8px;
}}

QGroupBox {{
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-size: 15px;
    font-weight: 700;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {colors["text"]};
    background: {colors["card"]};
}}

QLabel {{
    color: {colors["text"]};
    background: transparent;
}}

QLabel[muted="true"] {{
    color: {colors["muted"]};
    font-size: 12px;
    font-weight: 600;
}}

QLabel[title="true"] {{
    font-size: 15px;
    font-weight: 700;
}}

QLineEdit, QComboBox, QSpinBox, QTextEdit, QPlainTextEdit {{
    background: {colors["input"]};
    color: {colors["text"]};
    border: 1px solid {colors["border"]};
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: {colors["primary"]};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {colors["primary"]};
}}

QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{
    color: {colors["disabled"]};
    background: {colors["pressed"]};
}}

QPushButton {{
    background: {colors["card"]};
    color: {colors["text"]};
    border: 1px solid {colors["border"]};
    border-radius: 6px;
    min-height: 30px;
    padding: 0 12px;
    font-size: 13px;
}}

QPushButton:hover {{
    background: {colors["hover"]};
    border-color: {colors["primary"]};
    color: {colors["primary"]};
}}

QPushButton:pressed {{
    background: {colors["pressed"]};
    padding-top: 1px;
}}

QPushButton:disabled {{
    color: {colors["disabled"]};
    background: {colors["pressed"]};
    border-color: {colors["border"]};
}}

QPushButton[role="primary"] {{
    background: {colors["primary"]};
    color: #FFFFFF;
    border: 1px solid {colors["primary"]};
    min-height: 36px;
    font-weight: 700;
}}

QPushButton[role="primary"]:hover {{
    background: {colors["primary_hover"]};
    border-color: {colors["primary_hover"]};
    color: #FFFFFF;
}}

QPushButton[role="primary"]:pressed {{
    background: {colors["primary_pressed"]};
    border-color: {colors["primary_pressed"]};
}}

QPushButton[role="secondary"] {{
    background: {colors["secondary"]};
    color: #FFFFFF;
    border: 1px solid {colors["secondary"]};
    min-height: 36px;
    font-weight: 700;
}}

QPushButton[role="secondary"]:hover {{
    background: {colors["secondary_hover"]};
    border-color: {colors["secondary_hover"]};
    color: #FFFFFF;
}}

QPushButton[role="secondary"]:pressed {{
    background: {colors["secondary_pressed"]};
    border-color: {colors["secondary_pressed"]};
}}

QPushButton[role="danger"] {{
    background: {colors["error"]};
    color: #FFFFFF;
    border: 1px solid {colors["error"]};
    min-height: 36px;
    font-weight: 700;
}}

QPushButton[nav="true"] {{
    text-align: left;
    min-height: 38px;
    padding-left: 12px;
    border: 1px solid transparent;
    font-size: 14px;
}}

QPushButton[active="true"] {{
    background: {colors["active"]};
    border-color: {colors["primary"]};
    color: {colors["primary"]};
    font-weight: 700;
}}

QTabWidget::pane {{
    border: none;
    background: transparent;
}}

QTabBar::tab {{
    height: 0px;
    width: 0px;
    margin: 0px;
    padding: 0px;
    border: none;
}}

QSplitter::handle {{
    background: {colors["border"]};
}}

QSplitter::handle:horizontal {{
    width: 6px;
}}

QSplitter::handle:vertical {{
    height: 6px;
}}

QProgressBar {{
    background: {colors["progress_track"]};
    border: none;
    border-radius: 7px;
    min-height: 14px;
    max-height: 14px;
    text-align: center;
    color: {colors["text"]};
    font-size: 12px;
    font-weight: 700;
}}

QProgressBar::chunk {{
    background: {colors["primary"]};
    border-radius: 7px;
}}

QScrollArea {{
    border: none;
    background: transparent;
}}
"""


APP_QSS = build_app_qss(COLORS)
