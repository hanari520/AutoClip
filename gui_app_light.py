# -*- coding: utf-8 -*-
"""AutoClip Light GUI.

New desktop GUI entry that keeps the original business logic in
``gui_app_mobile.AutoClipGUI`` and only replaces layout, visual styling, and
presentation widgets.
"""

import html
import json
import os
import sys
import time
from datetime import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QPalette, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QComboBox,
    QPlainTextEdit,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtGui import QPixmap

from gui_app_mobile import AutoClipGUI, BatchUploadDialog, CONFIG_FILE, PROJECT_ROOT
from gui_light_theme import COLORS, build_app_qss, set_theme_colors


STEP_NAV = [
    ("1", "准备文件"),
    ("2", "字幕识别"),
    ("3", "高光提取"),
    ("4", "视频切片"),
    ("5", "字幕烧录"),
    ("6", "封面生成"),
    ("7", "B站投稿"),
]


class LightStepIndicator(QWidget):
    """Compact light seven-step progress indicator for the right status rail."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._labels = []
        layout = QVBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        for step_no, step_name in STEP_NAV:
            label = QLabel(f"{step_no}. {step_name}")
            label.setFixedHeight(28)
            label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            label.setStyleSheet(self._label_style("pending"))
            self._labels.append(label)
            layout.addWidget(label)

    def _label_style(self, state):
        if state == "done":
            return (
                f"background:{COLORS['card']};color:{COLORS['success']};"
                f"border:1px solid {COLORS['success']};border-radius:6px;padding-left:8px;font-size:13px;"
            )
        if state == "active":
            return (
                f"background:{COLORS['active']};color:{COLORS['primary']};"
                f"border:1px solid {COLORS['primary']};border-radius:6px;padding-left:8px;"
                "font-size:13px;font-weight:700;"
            )
        return (
            f"background:{COLORS['input']};color:{COLORS['muted']};"
            f"border:1px solid {COLORS['border']};border-radius:6px;padding-left:8px;font-size:13px;"
        )

    def set_active_step(self, step_id):
        for idx, label in enumerate(self._labels, start=1):
            if step_id and idx < step_id:
                label.setStyleSheet(self._label_style("done"))
            elif step_id and idx == step_id:
                label.setStyleSheet(self._label_style("active"))
            else:
                label.setStyleSheet(self._label_style("pending"))


class LightLogHighlighter(QSyntaxHighlighter):
    """UI readability optimization: color log levels without switching to rich text widgets."""

    def __init__(self, document):
        super().__init__(document)
        self._formats = {}
        self.refresh_formats()

    def refresh_formats(self):
        level_colors = {
            "ERROR": COLORS["error"],
            "SUCCESS": COLORS["success"],
            "WARNING": COLORS["warning"],
            "INFO": COLORS["text"],
        }
        self._formats.clear()
        for level, color in level_colors.items():
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            fmt.setFontWeight(QFont.Weight.Bold if level != "INFO" else QFont.Weight.Medium)
            self._formats[level] = fmt

        self._timestamp_format = QTextCharFormat()
        self._timestamp_format.setForeground(QColor(COLORS["muted"]))
        self._timestamp_format.setFontWeight(QFont.Weight.DemiBold)

        self._level_format = QTextCharFormat()
        self._level_format.setForeground(QColor(COLORS["primary"]))
        self._level_format.setFontWeight(QFont.Weight.Bold)
        self.rehighlight()

    def highlightBlock(self, text):
        end = text.find("]")
        if text.startswith("[") and end > 0:
            self.setFormat(0, end + 1, self._timestamp_format)
        for level, fmt in self._formats.items():
            marker = f"[{level}]"
            pos = text.find(marker)
            if pos >= 0:
                self.setFormat(pos, len(marker), self._level_format)
                msg_start = pos + len(marker)
                self.setFormat(msg_start, max(len(text) - msg_start, 0), fmt)
                break


class LightBatchUploadDialog(BatchUploadDialog):
    """Light themed batch upload dialog; upload selection logic is inherited."""

    def _setup_ui(self):
        self.setMinimumSize(800, 560)
        self.resize(920, 640)
        self.setStyleSheet(build_app_qss(COLORS))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        title = QLabel(f"投稿清单 - {os.path.basename(self.input_dir)}")
        title.setProperty("title", True)
        layout.addWidget(title)

        toolbar_card = QFrame()
        toolbar_card.setProperty("card", True)
        toolbar = QHBoxLayout(toolbar_card)
        toolbar.setContentsMargins(12, 10, 12, 10)
        btn_all = QPushButton("全选")
        btn_all.clicked.connect(lambda: self._toggle_all(True))
        btn_none = QPushButton("反选")
        btn_none.clicked.connect(self._invert_selection)
        self._count_label = QLabel("0 / 0 个已选")
        self._count_label.setStyleSheet(f"color:{COLORS['primary']};font-weight:700;")
        toolbar.addWidget(btn_all)
        toolbar.addWidget(btn_none)
        toolbar.addStretch()
        toolbar.addWidget(self._count_label)
        layout.addWidget(toolbar_card)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("card", True)
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(10, 10, 10, 10)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_widget)
        layout.addWidget(scroll, 1)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_upload = QPushButton("批量投稿")
        btn_upload.setProperty("role", "secondary")
        btn_upload.clicked.connect(self._do_batch_upload)
        btn_layout.addWidget(btn_upload)
        layout.addLayout(btn_layout)

    def _build_item_row(self, idx, item):
        row = QFrame()
        row.setStyleSheet(
            f"QFrame{{background:{COLORS['card']};border:1px solid {COLORS['border']};"
            f"border-radius:8px;}} QFrame:hover{{background:{COLORS['hover']};}}"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 10, 10, 10)
        row_layout.setSpacing(10)

        status = item.get("upload_status", "pending")
        cb = QCheckBox()
        cb.setChecked(status != "success")
        cb.setEnabled(True)
        self._checkboxes.append(cb)
        cb.stateChanged.connect(self._update_count)
        row_layout.addWidget(cb)

        cover = item.get("cover_path", "")
        cover_lbl = QLabel()
        cover_lbl.setFixedSize(96, 58)
        cover_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_lbl.setStyleSheet(
            f"background:{COLORS['input']};border:1px solid {COLORS['border']};"
            f"border-radius:6px;color:{COLORS['muted']};font-size:12px;"
        )
        if cover and os.path.exists(cover):
            pix = QPixmap(cover).scaled(
                96,
                58,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            cover_lbl.setPixmap(pix)
        else:
            cover_lbl.setText("无封面")
        row_layout.addWidget(cover_lbl)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(6)
        title = item.get("title", "无标题")
        if len(title) > 56:
            title = title[:53] + "..."
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"color:{COLORS['text']};font-weight:700;font-size:13px;")
        info_layout.addWidget(title_lbl)

        video = os.path.basename(item.get("video_path", ""))
        tags = ", ".join(item.get("tags", [])[:4])
        sub_lbl = QLabel(f"{video}  |  分区: {item.get('tid', '?')}  |  {tags}")
        sub_lbl.setWordWrap(True)
        sub_lbl.setStyleSheet(f"color:{COLORS['muted']};font-size:12px;")
        info_layout.addWidget(sub_lbl)

        status_text = {
            "pending": "待投稿",
            "uploading": "投稿中",
            "success": "已投稿",
            "failed": "投稿失败",
            "skipped": "未投稿/已中断",
        }.get(status, status or "待投稿")
        status_color = {
            "pending": COLORS["warning"],
            "uploading": COLORS["primary"],
            "success": COLORS["success"],
            "failed": COLORS["error"],
            "skipped": COLORS["muted"],
        }.get(status, COLORS["muted"])
        attempts = int(item.get("upload_attempts", 0) or 0)
        detail = f"状态: {status_text}  |  投稿次数: {attempts}"
        if item.get("bvid"):
            detail += f"  |  BV: {item.get('bvid')}"
        last_error = item.get("last_error") or ""
        if last_error and status in ("failed", "skipped"):
            detail += f"  |  {last_error[:80]}"
        state_lbl = QLabel(detail)
        state_lbl.setWordWrap(True)
        state_lbl.setStyleSheet(f"color:{status_color};font-size:12px;font-weight:700;")
        info_layout.addWidget(state_lbl)

        row_layout.addLayout(info_layout, 1)
        return row

    def _update_count(self):
        selected = sum(1 for cb in self._checkboxes if cb.isChecked())
        total = len(self._checkboxes)
        self._count_label.setText(f"{selected} / {total} 个已选")

    def _do_batch_upload(self):
        selected = [self._items[i] for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
        if not selected:
            QMessageBox.warning(self, "提示", "请至少选择一个视频投稿")
            return
        reply = QMessageBox.question(
            self,
            "确认投稿",
            f"确认要投稿 {len(selected)} 个视频吗？\n\n投稿后将无法撤回。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.accept()
        self.parent_gui.start_task(
            "batch_upload",
            {
                "items": selected,
                "manifest_path": os.path.join(self.input_dir, "upload_manifest.json"),
            },
        )


class LightAutoClipGUI(AutoClipGUI):
    """New three-column light UI; original task methods remain inherited."""

    WINDOW_TITLE = "AutoClip Light"
    SHOW_MAXIMIZED = True
    DEFAULT_SIZE = (1440, 860)

    def __init__(self):
        super().__init__()
        self.resize(*self.DEFAULT_SIZE)
        # 缩放友好：给三栏布局一个真实可达的最小尺寸，更小时由页内滚动条接管。
        self.setMinimumSize(1024, 620)
        self._theme_name = self.config.get("ui_theme", "light")
        self._apply_theme(self._theme_name, persist=False)

    def init_ui(self):
        """UI layout optimization: full three-column splitter with adaptive resize."""
        central = QWidget()
        central.setObjectName("lightRoot")
        self.setCentralWidget(central)
        self.setStyleSheet(build_app_qss(COLORS))

        root = QVBoxLayout(central)
        root.setContentsMargins(14, 12, 14, 14)
        root.setSpacing(10)

        # UI layout optimization: splitter handles allow drag-resizing all three columns.
        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        left_panel = self._create_left_nav_panel()
        center_panel = self._create_center_workspace()
        right_panel = self._create_right_status_panel()

        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(center_panel)
        main_splitter.addWidget(right_panel)
        main_splitter.setStretchFactor(0, 0)
        main_splitter.setStretchFactor(1, 1)
        main_splitter.setStretchFactor(2, 0)
        main_splitter.setSizes([300, 640, 300])

        # 日志改为全宽底部抽屉：窄窗口或导航折叠时不再被挤成窄条。
        root.addWidget(main_splitter, 1)
        root.addWidget(self._create_log_drawer())

        self._polish_created_controls()
        self._update_nav_state(0)
        self._refresh_status_metrics()

    def _create_card(self, title=None):
        card = QFrame()
        card.setProperty("card", True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        if title:
            label = QLabel(title)
            label.setProperty("title", True)
            layout.addWidget(label)
        return card, layout

    def _create_left_nav_panel(self):
        """UI layout optimization: left column holds navigation only; the log
        lives in a full-width bottom drawer so collapsing never crushes it."""
        panel = QWidget()
        self._left_panel = panel
        panel.setMinimumWidth(260)
        panel.setMaximumWidth(420)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        nav_card, nav_layout = self._create_card()
        nav_header = QHBoxLayout()
        self._nav_title = QLabel("流程导航")
        self._nav_title.setProperty("title", True)
        self._nav_collapse_btn = QToolButton()
        self._nav_collapse_btn.setText("收起")
        self._nav_collapse_btn.setCheckable(True)
        # 用 toggled 而非 clicked：resizeEvent 的窄窗口自动折叠依赖程序化 setChecked。
        self._nav_collapse_btn.toggled.connect(self._toggle_nav_panel)
        nav_header.addWidget(self._nav_title)
        nav_header.addStretch()
        nav_header.addWidget(self._nav_collapse_btn)
        nav_layout.addLayout(nav_header)
        self._nav_buttons = []
        for idx, (step_no, step_name) in enumerate(STEP_NAV):
            btn = QPushButton(f"{step_no}. {step_name}")
            btn.setProperty("nav", True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, i=idx: self._scroll_to_step(i + 1))
            self._nav_buttons.append(btn)
            nav_layout.addWidget(btn)
        nav_layout.addStretch(1)
        layout.addWidget(nav_card)
        return panel

    def _create_log_drawer(self):
        """全宽日志抽屉：横排长日志更易读，折叠后只留标题行。"""
        card, layout = self._create_card()
        header = QHBoxLayout()
        title = QLabel("执行日志")
        title.setProperty("title", True)
        self._log_toggle_btn = QToolButton()
        self._log_toggle_btn.setText("收起")
        self._log_toggle_btn.setCheckable(True)
        self._log_toggle_btn.setChecked(True)
        # 先置状态再连接：setChecked(True) 会立即发出 toggled，此时 log_text 尚未创建。
        self._log_toggle_btn.toggled.connect(self._toggle_log_panel)
        clear_btn = QPushButton("清空日志")
        clear_btn.clicked.connect(self.clear_log)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(clear_btn)
        header.addWidget(self._log_toggle_btn)
        layout.addLayout(header)

        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        self.log_text.setMinimumHeight(96)
        self.log_text.setMaximumHeight(220)
        self.log_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._apply_log_text_style()
        self._log_highlighter = LightLogHighlighter(self.log_text.document())
        layout.addWidget(self.log_text)
        return card

    def _create_center_workspace(self):
        """UI layout optimization: center keeps the core step card and file list wide."""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        layout.addWidget(self._create_global_bar())

        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.setChildrenCollapsible(False)

        # The original create_right_content builds every control and connects every event.
        self.step_tabs = self.create_right_content()
        self.step_tabs.currentChanged.connect(self._update_nav_state)
        vertical_splitter.addWidget(self.step_tabs)

        files_card, files_layout = self._create_card("素材文件列表")
        # UI layout optimization: move the original file list to a persistent bottom panel.
        if hasattr(self, "file_list_text"):
            self.file_list_text.setParent(files_card)
            self.file_list_text.setMinimumHeight(90)
            self.file_list_text.setMaximumHeight(200)
            files_layout.addWidget(self.file_list_text)
        vertical_splitter.addWidget(files_card)
        vertical_splitter.setStretchFactor(0, 1)
        vertical_splitter.setStretchFactor(1, 0)
        vertical_splitter.setSizes([680, 180])

        # 缩放友好：每个步骤页面包滚动区并重排为表单布局，窗口变小时出滚动条而非挤压。
        self._relayout_step_pages()

        layout.addWidget(vertical_splitter, 1)
        return panel

    def _create_right_status_panel(self):
        """UI layout optimization: right rail separates live status from editing controls.

        状态卡内容放进纵向滚动区：窗口高度不足时出滚动条，不再裁剪步骤指示器。
        """
        panel = QWidget()
        panel.setMinimumWidth(280)
        panel.setMaximumWidth(380)
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        status_card, status_layout = self._create_card("实时运行状态")

        self.step_indicator = LightStepIndicator()
        status_layout.addWidget(self.step_indicator)

        self.stage_label = QLabel("● 准备就绪")
        self.stage_label.setStyleSheet(f"color:{COLORS['muted']};font-weight:700;font-size:14px;")
        status_layout.addWidget(self.stage_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.progress_bar)

        self.compact_info_label = QLabel("— · — · —")
        self.compact_info_label.setProperty("muted", True)
        status_layout.addWidget(self.compact_info_label)

        self.current_file_label = QLabel("当前文件: -")
        self.current_file_label.setWordWrap(True)
        self.current_file_label.setStyleSheet(f"color:{COLORS['primary']};font-size:13px;font-weight:600;")
        status_layout.addWidget(self.current_file_label)

        counts = QGridLayout()
        self._pending_label = QLabel("0")
        self._done_label = QLabel("0")
        self._total_label = QLabel("0")
        self._metric_boxes = []
        for value_label in (self._pending_label, self._done_label, self._total_label):
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value_label.setStyleSheet(f"font-size:22px;font-weight:800;color:{COLORS['text']};")
        counts.addWidget(self._metric_box("待处理", self._pending_label), 0, 0)
        counts.addWidget(self._metric_box("已完成", self._done_label), 0, 1)
        counts.addWidget(self._metric_box("文件总数", self._total_label), 1, 0, 1, 2)
        status_layout.addLayout(counts)

        self.stop_btn = QPushButton("中断任务")
        self.stop_btn.setProperty("role", "danger")
        self.stop_btn.clicked.connect(self.stop_task)
        self.stop_btn.setVisible(False)
        status_layout.addWidget(self.stop_btn)

        scroll.setWidget(status_card)
        outer.addWidget(scroll)
        return panel

    def _metric_box(self, title, value_label):
        box = QFrame()
        self._metric_boxes.append(box)
        box.setStyleSheet(
            f"QFrame{{background:{COLORS['bg']};border:1px solid {COLORS['border']};border-radius:8px;}}"
        )
        layout = QVBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        name = QLabel(title)
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name.setProperty("muted", True)
        layout.addWidget(value_label)
        layout.addWidget(name)
        return box

    def _toggle_theme(self):
        self._apply_theme("dark" if self._theme_name != "dark" else "light", persist=True)

    def _apply_theme(self, theme_name, persist=False):
        self._theme_name = "dark" if theme_name == "dark" else "light"
        set_theme_colors(self._theme_name)
        qss = build_app_qss(COLORS)
        self.setStyleSheet(qss)
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(qss)
            app.setPalette(self._build_palette())
        self._refresh_theme_sensitive_styles()
        if persist:
            self.save_config()

    def _build_palette(self):
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
        palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["input"]))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["bg"]))
        palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
        palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["card"]))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
        return palette

    def _refresh_theme_sensitive_styles(self):
        if hasattr(self, "_theme_toggle_btn"):
            self._theme_toggle_btn.setText("浅色" if self._theme_name == "dark" else "深色")
        if hasattr(self, "global_bili_login_label"):
            self.global_bili_login_label.setStyleSheet(f"color:{COLORS['warning']};font-weight:700;")
        if hasattr(self, "stage_label"):
            self.stage_label.setStyleSheet(f"font-size:14px;font-weight:800;color:{COLORS['muted']};")
        if hasattr(self, "current_file_label"):
            self.current_file_label.setStyleSheet(
                f"color:{COLORS['primary']};font-size:13px;font-weight:600;"
            )
        for label in (
            getattr(self, "_pending_label", None),
            getattr(self, "_done_label", None),
            getattr(self, "_total_label", None),
        ):
            if label is not None:
                label.setStyleSheet(f"font-size:22px;font-weight:800;color:{COLORS['text']};")
        for box in getattr(self, "_metric_boxes", []):
            box.setStyleSheet(
                f"QFrame{{background:{COLORS['bg']};border:1px solid {COLORS['border']};border-radius:8px;}}"
            )
        if hasattr(self, "log_text"):
            self._apply_log_text_style()
        if hasattr(self, "_log_highlighter"):
            self._log_highlighter.refresh_formats()
        if hasattr(self, "step_indicator"):
            self.step_indicator.set_active_step(getattr(self, "_current_pipeline_step", -1))
        if hasattr(self, "_nav_buttons"):
            self._update_nav_state(self.step_tabs.currentIndex() if hasattr(self, "step_tabs") else 0)
        if hasattr(self, "progress_bar"):
            self._progress_color_tier = -1
            self._update_progress_bar_color(self.progress_bar.value())

    def save_config(self):
        super().save_config()
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["ui_theme"] = getattr(self, "_theme_name", "light")
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存主题配置失败: {e}")

    def _create_global_bar(self):
        """UI layout optimization: top controls are grouped by workflow priority.

        缩放友好：去掉分组前缀标签压缩行宽；窄窗口时控件按最小宽度收缩，
        不再出现标签/按钮被裁切。
        """
        bar, layout = self._create_card()
        layout.setSpacing(8)

        # 行1：录播目录 + 主播 + 应用
        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.global_input_dir = QLineEdit()
        self.global_input_dir.setPlaceholderText("录播目录")
        self.global_input_dir.setMinimumWidth(120)
        btn_browse = QPushButton("浏览")
        btn_browse.clicked.connect(lambda: self.browse_dir(self.global_input_dir))
        streamer_label = QLabel("主播")
        self.global_streamer = QComboBox()
        self.global_streamer.setEditable(True)
        self.global_streamer.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.global_streamer.setMinimumWidth(110)
        self.global_streamer.addItems(["栞栞Shiori", "灰泽满Hazel", "岁己SUI","四时小路Komichi", "小松绿Viridis", "枝堇Sumire"])
        apply_btn = QPushButton("应用到所有步骤")
        apply_btn.clicked.connect(self.apply_global_config)
        path_row.addWidget(self.global_input_dir, 1)
        path_row.addWidget(btn_browse)
        path_row.addWidget(streamer_label)
        path_row.addWidget(self.global_streamer)
        path_row.addWidget(apply_btn)
        layout.addLayout(path_row)

        # 行2：核心操作 + 梗搜索
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        pipeline_btn = QPushButton("一键流转")
        pipeline_btn.setProperty("role", "primary")
        pipeline_btn.clicked.connect(self.run_pipeline)
        batch_btn = QPushButton("批量投稿")
        batch_btn.setProperty("role", "secondary")
        batch_btn.clicked.connect(self.open_batch_upload)
        meme_btn = QPushButton("梗搜索")
        meme_btn.clicked.connect(self.open_meme_search_dialog)
        action_row.addWidget(pipeline_btn, 1)
        action_row.addWidget(batch_btn, 1)
        action_row.addWidget(meme_btn)
        action_row.addStretch()
        layout.addLayout(action_row)

        # 行3：B站登录与外观
        platform_row = QHBoxLayout()
        platform_row.setSpacing(8)
        self.global_bili_login_label = QLabel("未登录")
        self.global_bili_login_label.setStyleSheet(f"color:{COLORS['warning']};font-weight:700;")
        qr_btn = QPushButton("扫码")
        qr_btn.clicked.connect(self.start_qr_login)
        cookie_btn = QPushButton("Cookie")
        cookie_btn.clicked.connect(self.show_cookie_dialog)
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_bili_login_status)
        self._api_toggle_btn = QPushButton("高级")
        self._api_toggle_btn.setCheckable(True)
        self._api_toggle_btn.toggled.connect(self._toggle_api_panel)
        self._theme_toggle_btn = QPushButton("深色")
        self._theme_toggle_btn.clicked.connect(self._toggle_theme)
        platform_row.addWidget(self.global_bili_login_label)
        platform_row.addStretch()
        platform_row.addWidget(qr_btn)
        platform_row.addWidget(cookie_btn)
        platform_row.addWidget(refresh_btn)
        platform_row.addWidget(self._api_toggle_btn)
        platform_row.addWidget(self._theme_toggle_btn)
        layout.addLayout(platform_row)

        self._api_panel = QWidget()
        api_layout = QHBoxLayout(self._api_panel)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.addWidget(QLabel("API Key"))
        self.global_api_key = QLineEdit()
        self.global_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.global_api_key.setPlaceholderText("sk-...")
        api_layout.addWidget(self.global_api_key, 1)
        api_layout.addWidget(QLabel("供应商"))
        self.llm_provider = QComboBox()
        self.llm_provider.addItem("硅基流动 (SiliconFlow)", "siliconflow")
        self.llm_provider.addItem("DeepSeek 官方", "deepseek")
        self.llm_provider.addItem("OpenCode Go (Zen)", "opencode-go")
        self.llm_provider.addItem("智谱 (Zhipu)", "zhipu")
        self.llm_provider.addItem("智谱 Coding (编码套餐)", "zhipu-coding")
        self._llm_keys = {"siliconflow": "", "deepseek": "", "opencode-go": "", "zhipu": "", "zhipu-coding": ""}
        self._last_provider = "siliconflow"
        self.llm_provider.currentIndexChanged.connect(self._on_provider_changed)
        api_layout.addWidget(self.llm_provider)
        api_layout.addWidget(QLabel("模型"))
        self.global_model = QComboBox()
        self.global_model.setEditable(True)
        self.global_model.setMinimumWidth(180)
        self.global_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.global_model.addItem("deepseek-ai/DeepSeek-V4-Flash")
        self.global_model.setCurrentText("deepseek-ai/DeepSeek-V4-Flash")
        api_layout.addWidget(self.global_model, 1)
        self._api_panel.setVisible(False)
        layout.addWidget(self._api_panel)

        return bar

    def _create_step_container(self, title, description):
        """UI layout optimization: every step is a white card with collapsible intro."""
        container, outer = self._create_card()

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setProperty("title", True)
        toggle = QPushButton("收起简介")
        toggle.setFixedWidth(86)
        header.addWidget(title_label)
        header.addStretch()
        header.addWidget(toggle)
        outer.addLayout(header)

        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setProperty("muted", True)
        outer.addWidget(desc)
        toggle.clicked.connect(lambda: self._toggle_description(desc, toggle))

        content = QWidget()
        outer.addWidget(content, 1)
        # 记录各步骤的 content 容器（按步骤 1-7 顺序），供 _relayout_step_pages 重排。
        registry = getattr(self, "_step_content_registry", None)
        if registry is None:
            registry = self._step_content_registry = []
        registry.append(content)
        return container, content

    def _toggle_description(self, desc, button):
        desc.setVisible(not desc.isVisible())
        button.setText("收起简介" if desc.isVisible() else "展开简介")

    # ------------------------------------------------------------------
    # 步骤页重排：滚动包裹 + 表单化布局（复用基类已接线的控件，仅换布局）
    # ------------------------------------------------------------------

    def _relayout_step_pages(self):
        tabs = getattr(self, "step_tabs", None)
        registry = getattr(self, "_step_content_registry", [])
        if tabs is None:
            return
        builders = [
            self._rebuild_step1_page,
            self._rebuild_step2_page,
            self._rebuild_step3_page,
            self._rebuild_step4_page,
            self._rebuild_step5_page,
            self._rebuild_step6_page,
            self._rebuild_step7_page,
        ]
        tabs.blockSignals(True)
        try:
            labels = [tabs.tabText(i) for i in range(tabs.count())]
            # 先把每页包进滚动区：窗口缩小时出现滚动条而不是挤压组件。
            # 顺序很重要：必须先 removeTab 再把页面挂进滚动区——
            # 若页面在仍属于 TabWidget 堆栈时被重新挂载父级，
            # QTabWidget 会错位并吞掉相邻 Tab（实测 PySide/PyQt6 均如此）。
            for i in range(tabs.count()):
                page = tabs.widget(i)
                if page is None:
                    continue
                page.setMaximumWidth(880)
                tabs.removeTab(i)
                scroll = self._scroll_wrap_page(page)
                tabs.insertTab(i, scroll, labels[i] if i < len(labels) else f"步骤{i + 1}")
            # 再逐页重排内部布局（控件与信号保持不变）。
            for i, build in enumerate(builders):
                if i >= len(registry):
                    break
                try:
                    build(registry[i])
                except Exception as exc:  # 重排失败时保留原布局，不影响功能
                    print(f"步骤{i + 1}页面重排失败: {exc}")
            tabs.setCurrentIndex(0)
        finally:
            tabs.blockSignals(False)

    def _scroll_wrap_page(self, page):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        row.addStretch(1)
        row.addWidget(page)
        row.addStretch(1)
        # removeTab 会把页面标记为显式隐藏；不清除该标记的话，
        # 页面进入滚动区后永远不会被布局重新显示（表现为整页空白）。
        page.show()
        scroll.setWidget(inner)
        return scroll

    def _strip_layout(self, widget):
        old = widget.layout()
        if old is None:
            return
        # 把旧布局整体摘到一个"孤儿箱"里。必须长期持有引用：
        # 临时容器一旦被 GC，其 C++ 对象连同里面等待重挂的控件一起被销毁。
        bin_widget = QWidget()
        bin_widget.setObjectName("lightOrphanBin")
        bin_widget.setLayout(old)
        bin_widget.hide()
        if not hasattr(self, "_orphan_bins"):
            self._orphan_bins = []
        self._orphan_bins.append(bin_widget)

    @staticmethod
    def _find_button(root, text):
        for btn in root.findChildren(QPushButton):
            if btn.text() == text:
                return btn
        return None

    @staticmethod
    def _form_row(label_text, *widgets, stretch=0):
        """构造"标签 + 控件"表单行；stretch 指定哪些控件吃掉多余宽度。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        if label_text:
            label = QLabel(label_text)
            label.setMinimumWidth(72)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(label)
        stretches = stretch if isinstance(stretch, (tuple, list)) else (stretch,)
        for i, widget in enumerate(widgets):
            if widget is None:
                continue
            row.addWidget(widget, 1 if i in stretches else 0)
        return row

    @staticmethod
    def _stack_widgets(*widgets):
        """垂直叠放互斥显隐的控件（如 FunASR/Whisper 模型下拉）。"""
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        for widget in widgets:
            if widget is not None:
                layout.addWidget(widget)
        return cell

    def _dir_row(self, line_edit, filter_str=None):
        """目录/文件选择行：输入框 + 浏览按钮（保留原 browse 逻辑）。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(line_edit, 1)
        btn = QPushButton("浏览...")
        if filter_str:
            btn.clicked.connect(lambda: self.browse_file(line_edit, filter_str))
        else:
            btn.clicked.connect(lambda: self.browse_dir(line_edit))
        row.addWidget(btn)
        return row

    def _rebuild_step1_page(self, content):
        refresh_btn = self._find_button(content, "刷新文件列表")
        buttons = [
            self._find_button(content, "转换 FLV 为 MP4（copy）"),
            self._find_button(content, "竖屏转横屏 1080p"),
            self._find_button(content, "压制同名 MP4 + XML 为弹幕版"),
        ]
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("输入目录", *self._wrap_row(self._dir_row(self.step1_input_dir))))
        actions = QHBoxLayout()
        actions.setSpacing(8)
        for btn in buttons:
            if btn is not None:
                actions.addWidget(btn, 1)
        form.addLayout(actions)
        if refresh_btn is not None:
            refresh_row = QHBoxLayout()
            refresh_row.addStretch()
            refresh_row.addWidget(refresh_btn)
            form.addLayout(refresh_row)
        hint = QLabel("转换后的素材可在下方“素材文件列表”中查看；一键流转会自动完成本步骤。")
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        form.addWidget(hint)
        form.addStretch(1)

    def _rebuild_step2_page(self, content):
        run_btn = self._find_button(content, "执行字幕识别")
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("输入目录", *self._wrap_row(self._dir_row(self.whisper_input_dir))))
        form.addLayout(self._form_row("引擎", self.asr_engine, stretch=0))
        # FunASR / Whisper / Qwen3 模型下拉互斥显隐（on_engine_changed 控制），垂直叠放。
        form.addLayout(self._form_row("模型", self._stack_widgets(self.funasr_model, self.whisper_model, self.qwen3_model), stretch=0))
        option_row = QHBoxLayout()
        option_row.setSpacing(8)
        lang_label = QLabel("语言")
        lang_label.setMinimumWidth(72)
        lang_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        option_row.addWidget(lang_label)
        option_row.addWidget(self.whisper_lang)
        option_row.addWidget(self.whisper_auto_lang)
        option_row.addWidget(self.funasr_speaker)
        option_row.addStretch()
        form.addLayout(option_row)
        form.addStretch(1)
        if run_btn is not None:
            form.addWidget(run_btn)

    def _rebuild_step3_page(self, content):
        run_btn = self._find_button(content, "执行高光提取")
        prompts_row = self._dir_row(self.llm_prompts_dir)
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("输入目录", *self._wrap_row(self._dir_row(self.llm_input_dir))))
        form.addLayout(self._form_row("主播", self.llm_streamer, stretch=0))
        form.addLayout(self._form_row("分析方式", self.llm_method, stretch=0))
        form.addWidget(self.multi_sources_group)
        prompts_container = QWidget()
        prompts_container.setLayout(prompts_row)
        form.addLayout(self._form_row("提示词目录", prompts_container, stretch=0))
        form.addStretch(1)
        if run_btn is not None:
            form.addWidget(run_btn)

    def _rebuild_step4_page(self, content):
        run_btn = self._find_button(content, "执行切片")
        output_row = self._dir_row(self.clip_output_dir)
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("输入目录", *self._wrap_row(self._dir_row(self.clip_input_dir))))
        output_container = QWidget()
        output_container.setLayout(output_row)
        form.addLayout(self._form_row("输出目录", output_container, stretch=0))
        form.addStretch(1)
        if run_btn is not None:
            form.addWidget(run_btn)

    def _rebuild_step5_page(self, content):
        run_btn = self._find_button(content, "执行字幕烧录")
        style_group = content.findChild(QGroupBox)
        srt_row = self._dir_row(self.subtitle_srt_dir)
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("切片目录", *self._wrap_row(self._dir_row(self.subtitle_input_dir))))
        srt_container = QWidget()
        srt_container.setLayout(srt_row)
        form.addLayout(self._form_row("SRT目录", srt_container, stretch=0))
        form.addLayout(
            self._form_row(
                "ASR引擎",
                self.subtitle_asr_engine,
                QLabel("模型"),
                self._stack_widgets(self.subtitle_asr_model, self.subtitle_whisper_model, self.subtitle_qwen3_model),
                stretch=(0, 2),
            )
        )
        lang_row = QHBoxLayout()
        lang_row.setSpacing(8)
        lang_label = QLabel("语言")
        lang_label.setMinimumWidth(72)
        lang_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lang_row.addWidget(lang_label)
        lang_row.addWidget(self.subtitle_asr_lang)
        lang_row.addWidget(self.subtitle_skip_asr)
        lang_row.addStretch()
        form.addLayout(lang_row)
        form.addWidget(self.subtitle_enabled)
        if style_group is not None:
            form.addWidget(style_group)
        form.addStretch(1)
        if run_btn is not None:
            form.addWidget(run_btn)

    def _rebuild_step6_page(self, content):
        run_btn = self._find_button(content, "执行封面生成")
        load_fonts_btn = self._find_button(content, "加载字体")
        self._strip_layout(content)
        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)
        form.addLayout(self._form_row("输入目录", *self._wrap_row(self._dir_row(self.cover_input_dir))))
        form.addLayout(self._form_row("样式", self.cover_style, stretch=0))
        font_row = QHBoxLayout()
        font_row.setSpacing(8)
        font_row.addWidget(self.cover_font, 1)
        if load_fonts_btn is not None:
            font_row.addWidget(load_fonts_btn)
        font_container = QWidget()
        font_container.setLayout(font_row)
        form.addLayout(self._form_row("封面字体", font_container, stretch=0))
        form.addStretch(1)
        if run_btn is not None:
            form.addWidget(run_btn)

    def _rebuild_step7_page(self, content):
        upload_btn = self._find_button(content, "执行B站投稿")
        manifest_btn = self._find_button(content, "📄 根据文件生成投稿清单")
        info_label = None
        for lbl in content.findChildren(QLabel):
            if "其他可选字段" in lbl.text():
                info_label = lbl
                break
        self._strip_layout(content)

        form = QVBoxLayout(content)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        # 分区一：视频内容（文件 / 标题 / 简介）
        media_card, media_layout = self._create_card("视频内容")
        media_layout.setContentsMargins(12, 10, 12, 12)
        video_row = self._dir_row(self.upload_video, "视频文件 (*.mp4 *.mkv *.flv)")
        media_layout.addLayout(self._form_row("视频文件", *self._wrap_row(video_row)))
        media_layout.addLayout(self._form_row("视频标题", self.upload_title, stretch=0))
        self.upload_desc.setMinimumHeight(72)
        # 限高：避免简介框吃掉整页高度，把"投稿设置"区推到折叠线下。
        self.upload_desc.setMaximumHeight(140)
        media_layout.addWidget(QLabel("视频简介"))
        media_layout.addWidget(self.upload_desc)
        form.addWidget(media_card)

        # 分区二：投稿设置（标签 / 分区 / 封面 / 版权）
        setting_card, setting_layout = self._create_card("投稿设置")
        setting_layout.setContentsMargins(12, 10, 12, 12)
        setting_grid = QGridLayout()
        setting_grid.setHorizontalSpacing(12)
        setting_grid.setVerticalSpacing(10)
        cover_row = self._dir_row(self.upload_cover, "图片文件 (*.jpg *.png *.jpeg)")
        cover_container = QWidget()
        cover_container.setLayout(cover_row)
        setting_grid.addWidget(QLabel("视频标签"), 0, 0)
        setting_grid.addWidget(self.upload_tags, 0, 1)
        setting_grid.addWidget(QLabel("投稿分区"), 0, 2)
        setting_grid.addWidget(self.upload_tid, 0, 3)
        setting_grid.addWidget(QLabel("封面文件"), 1, 0)
        setting_grid.addWidget(cover_container, 1, 1)
        setting_grid.addWidget(QLabel("版权类型"), 1, 2)
        setting_grid.addWidget(self.upload_copyright, 1, 3)
        setting_grid.addWidget(self.upload_source_label, 2, 0, 1, 1)
        setting_grid.addWidget(self.upload_source, 2, 1, 1, 3)
        setting_grid.addWidget(self.upload_no_reprint, 3, 0, 1, 4)
        setting_grid.setColumnStretch(1, 1)
        setting_grid.setColumnStretch(3, 1)
        for label in setting_grid.itemAtPosition(0, 0).widget(), setting_grid.itemAtPosition(0, 2).widget(), \
                setting_grid.itemAtPosition(1, 0).widget(), setting_grid.itemAtPosition(1, 2).widget():
            label.setMinimumWidth(72)
            label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        setting_layout.addLayout(setting_grid)
        form.addWidget(setting_card)

        if info_label is not None:
            info_label.setProperty("muted", True)
            form.addWidget(info_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        if upload_btn is not None:
            actions.addWidget(upload_btn, 1)
        if manifest_btn is not None:
            manifest_btn.setProperty("role", "secondary")
            actions.addWidget(manifest_btn, 1)
        form.addLayout(actions)
        form.addStretch(1)

    def _wrap_row(self, row_layout):
        container = QWidget()
        container.setLayout(row_layout)
        return (container,)

    def _toggle_api_panel(self, checked):
        self._api_panel.setVisible(checked)
        self._api_toggle_btn.setText("收起高级" if checked else "高级")

    def _toggle_log_panel(self, checked):
        """折叠 = 只留抽屉标题行；展开 = 显示日志内容（上限高度防挤占）。"""
        if hasattr(self, "log_text"):
            self.log_text.setVisible(checked)
            self.log_text.setMaximumHeight(220 if checked else 24)
        self._log_toggle_btn.setText("收起" if checked else "展开")

    def _toggle_nav_panel(self, checked):
        """UI layout optimization: collapse left navigation to a compact step rail."""
        self._left_panel.setMinimumWidth(84 if checked else 320)
        self._left_panel.setMaximumWidth(108 if checked else 560)
        self._nav_title.setText("步骤" if checked else "流程导航")
        self._nav_collapse_btn.setText("展开" if checked else "收起")
        for btn, (step_no, step_name) in zip(self._nav_buttons, STEP_NAV):
            btn.setText(step_no if checked else f"{step_no}. {step_name}")
            btn.setToolTip(step_name)

    def resizeEvent(self, event):
        """缩放友好：窗口变窄时自动折叠左侧导航为图标栏；变矮时自动折叠日志抽屉，
        优先保证步骤工作区可用（用户仍可手动展开，阈值跨越时才自动切换）。"""
        super().resizeEvent(event)
        btn = getattr(self, "_nav_collapse_btn", None)
        if btn is not None:
            narrow = self.width() < 1240
            if narrow != getattr(self, "_nav_auto_collapsed", None):
                self._nav_auto_collapsed = narrow
                if btn.isChecked() != narrow:
                    btn.setChecked(narrow)
        log_btn = getattr(self, "_log_toggle_btn", None)
        if log_btn is not None:
            short = self.height() < 780
            if short != getattr(self, "_log_auto_collapsed", None):
                self._log_auto_collapsed = short
                if log_btn.isChecked() == short:
                    log_btn.setChecked(not short)

    def _ensure_log_expanded(self):
        self._log_toggle_btn.setChecked(True)
        self._toggle_log_panel(True)

    def _scroll_to_step(self, idx):
        if hasattr(self, "step_tabs") and 1 <= idx <= 7:
            self.step_tabs.setCurrentIndex(idx - 1)

    def _update_nav_state(self, idx):
        for i, btn in enumerate(getattr(self, "_nav_buttons", [])):
            btn.setProperty("active", i == idx)
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if not self._is_task_running():
            self._current_pipeline_step = idx + 1
            if hasattr(self, "step_indicator"):
                self.step_indicator.set_active_step(self._current_pipeline_step)
            self._refresh_status_metrics()

    def _polish_created_controls(self):
        """UI style optimization: remove old inline dark styles from reused controls."""
        primary_text = [
            "执行字幕识别",
            "执行高光提取",
            "执行切片",
            "执行字幕烧录",
            "执行封面生成",
            "执行B站投稿",
            "转换 FLV",
        ]
        secondary_text = ["批量投稿"]
        for btn in self.findChildren(QPushButton):
            text = btn.text()
            if btn.property("role"):
                continue
            btn.setStyleSheet("")
            if any(key in text for key in primary_text):
                btn.setProperty("role", "primary")
            elif any(key in text for key in secondary_text):
                btn.setProperty("role", "secondary")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

        for widget in self.findChildren((QGroupBox, QLabel, QTextEdit, QPlainTextEdit, QTabWidget)):
            if widget is not self.log_text:
                widget.setStyleSheet("")

        if hasattr(self, "file_list_text"):
            self.file_list_text.setAcceptRichText(True)
            self.file_list_text.setPlaceholderText("刷新后显示 FLV / MP4 / SRT 等素材文件")

    def _update_progress_bar_color(self, percent):
        """UI style optimization: keep progress fill soft blue instead of bright green."""
        tier = 0 if percent < 30 else 1 if percent < 70 else 2
        if getattr(self, "_progress_color_tier", -1) == tier:
            return
        self._progress_color_tier = tier
        self.progress_bar.setStyleSheet(
            f"""
            QProgressBar {{
                background:{COLORS['progress_track']};
                border:none;
                border-radius:7px;
                min-height:14px;
                max-height:14px;
                text-align:center;
                color:{COLORS['text']};
                font-size:11px;
            }}
            QProgressBar::chunk {{
                background:{COLORS['primary']};
                border-radius:7px;
            }}
            """
        )

    def _apply_log_text_style(self):
        """UI readability optimization: make dense logs easier to scan in both themes."""
        self.log_text.setStyleSheet(
            f"QPlainTextEdit{{background:{COLORS['input']};color:{COLORS['text']};"
            f"border:1px solid {COLORS['border']};border-radius:8px;"
            "font-family:Consolas, 'Microsoft YaHei UI';font-size:13px;"
            "font-weight:600;padding:10px;selection-background-color:"
            f"{COLORS['primary']};}}"
        )

    def open_batch_upload(self):
        """Open the light themed batch upload dialog while preserving upload flow."""
        input_dir = self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log("错误：请先在全局配置中填写有效输入目录", "error")
            return
        manifest_path = os.path.join(input_dir, "upload_manifest.json")
        if not os.path.exists(manifest_path):
            reply = QMessageBox.question(
                self,
                "提示",
                "该目录没有投稿清单(upload_manifest.json)。\n\n"
                "可以点击“根据文件生成投稿清单”按钮，基于切片目录的实际文件自动生成。\n\n"
                "是否先运行“一键流转”生成？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.run_pipeline()
            return
        dlg = LightBatchUploadDialog(self, input_dir, self.config)
        dlg.exec()

    def start_task(self, task_type, params):
        if self._is_task_running():
            return super().start_task(task_type, params)
        self._active_task_type = task_type
        self._active_task_params = dict(params or {})
        self._last_status_metrics_refresh_ts = 0.0
        super().start_task(task_type, params)
        if self._is_task_running():
            step_id = self._step_for_task(task_type)
            if step_id:
                self._current_pipeline_step = step_id
                self.step_indicator.set_active_step(step_id)
            self._refresh_status_metrics(force=True)

    def _update_stage_label(self, text, active=True):
        color = COLORS["primary"] if active else COLORS["muted"]
        self.stage_label.setText(f"● {text}")
        self.stage_label.setStyleSheet(f"font-size:14px;font-weight:800;color:{color};")

    def _update_status_counts(self, completed, total):
        completed = max(int(completed or 0), 0)
        total = max(int(total or 0), 0)
        if total:
            completed = min(completed, total)
        pending = max(total - completed, 0)
        self._pending_label.setText(str(pending))
        self._done_label.setText(str(completed))
        self._total_label.setText(str(total))

    def on_progress(self, data):
        super().on_progress(data)
        if "step" in data:
            self._current_pipeline_step = data.get("step")
        if not self._refresh_status_metrics() and "total_files" in data:
            self._update_status_counts(data.get("completed_files", 0), data.get("total_files", 0))

    def on_finished(self, success, message):
        super().on_finished(success, message)
        self._refresh_status_metrics(force=True)

    def _is_task_running(self):
        worker = getattr(self, "worker", None)
        return bool(worker and worker.isRunning())

    def _step_for_task(self, task_type):
        return {
            "convert_flv": 1,
            "subtitles": 2,
            "llm": 3,
            "clips": 4,
            "subtitles_burn": 5,
            "covers": 6,
            "upload": 7,
            "batch_upload": 7,
            "pipeline": 1,
        }.get(task_type)

    def _refresh_status_metrics(self, force=False):
        """UI status optimization: count pending/done from the active workflow step."""
        if not hasattr(self, "_pending_label"):
            return False
        if self._is_task_running() and not force:
            now = time.monotonic()
            last = getattr(self, "_last_status_metrics_refresh_ts", 0.0)
            if now - last < 0.8:
                return False
            self._last_status_metrics_refresh_ts = now
        step_id = int(getattr(self, "_current_pipeline_step", 0) or 0)
        if step_id < 1 or step_id > 7:
            return False
        completed, total = self._status_stats_for_step(step_id)
        self._update_status_counts(completed, total)
        if total > 0 and not self._is_task_running():
            percent = int(completed / total * 100)
            self.progress_bar.setValue(percent)
            self._update_progress_bar_color(percent)
            self._progress_completed = completed
            self._progress_total = total
            self._update_compact_info()
        return total > 0

    def _status_stats_for_step(self, step_id):
        input_dir = self._status_input_dir_for_step(step_id)
        if step_id in (1, 2, 3, 4, 7) and (not input_dir or not os.path.isdir(input_dir)):
            return (0, 0)
        try:
            if step_id == 1:
                return self._step1_prepare_stats(input_dir)
            if step_id == 2:
                return self._step2_subtitle_stats(input_dir)
            if step_id == 3:
                total = 1 if self._has_step3_sources(input_dir) else 0
                done = 1 if os.path.exists(os.path.join(input_dir, "Data_source.txt")) else 0
                return (done, total)
            if step_id == 4:
                return self._step4_clip_stats(input_dir)
            if step_id == 5:
                return self._step5_burn_stats()
            if step_id == 6:
                return self._step6_cover_stats()
            if step_id == 7:
                return self._step7_upload_stats(input_dir)
        except Exception as e:
            self.log(f"状态统计失败: {e}", "warning")
        return (0, 0)

    def _status_input_dir_for_step(self, step_id):
        params = getattr(self, "_active_task_params", {}) or {}
        if self._is_task_running() and params.get("input_dir"):
            return params.get("input_dir")
        field_map = {
            1: "step1_input_dir",
            2: "whisper_input_dir",
            3: "llm_input_dir",
            4: "clip_input_dir",
            6: "cover_input_dir",
            7: "global_input_dir",
        }
        field = getattr(self, field_map.get(step_id, "global_input_dir"), None)
        value = field.text().strip() if field is not None else ""
        if value:
            return value
        global_field = getattr(self, "global_input_dir", None)
        return global_field.text().strip() if global_field is not None else ""

    def _has_step3_sources(self, input_dir):
        if os.path.exists(os.path.join(input_dir, "Data_source.txt")):
            return True
        return any(f.lower().endswith(".srt") for f in os.listdir(input_dir))

    def _step1_prepare_stats(self, input_dir):
        flv_files = [
            f for f in os.listdir(input_dir)
            if os.path.isfile(os.path.join(input_dir, f)) and f.lower().endswith(".flv")
        ]
        done = sum(
            1 for f in flv_files
            if os.path.exists(os.path.join(input_dir, os.path.splitext(f)[0] + ".mp4"))
        )
        return (done, len(flv_files))

    def _step2_subtitle_stats(self, input_dir):
        video_exts = (".mp4", ".mkv", ".avi", ".mov", ".wmv")
        videos = [
            f for f in os.listdir(input_dir)
            if os.path.isfile(os.path.join(input_dir, f)) and f.lower().endswith(video_exts)
        ]
        done = 0
        for f in videos:
            base = os.path.splitext(f)[0]
            srt_path = os.path.join(input_dir, base + ".srt")
            danmaku_base = base.replace("-弹幕版", "")
            danmaku_srt = os.path.join(input_dir, danmaku_base + ".srt")
            if os.path.exists(srt_path) or os.path.exists(danmaku_srt):
                done += 1
        return (done, len(videos))

    def _data_source_item_count(self, input_dir):
        ds_path = os.path.join(input_dir, "Data_source.txt")
        if not os.path.exists(ds_path):
            return 0
        with open(ds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return len(data) if isinstance(data, list) else 1

    def _step4_clip_stats(self, input_dir):
        ds_path = os.path.join(input_dir, "Data_source.txt")
        if not os.path.exists(ds_path):
            return (0, 0)
        with open(ds_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        clips = data if isinstance(data, list) else [data]
        slices_dir = self._resolve_slices_video_dir(input_dir)
        if not slices_dir or not os.path.isdir(slices_dir):
            return (0, len(clips))
        outputs = [
            f for f in os.listdir(slices_dir)
            if f.lower().endswith(".mp4") and not f.lower().endswith("_subbed.mp4")
        ]
        done = 0
        for clip in clips:
            segment = str(clip.get("segment", "") if isinstance(clip, dict) else "")
            title = str(clip.get("title", "") if isinstance(clip, dict) else "")
            if any((segment and segment in f) or (title and title in f) for f in outputs):
                done += 1
        return (done, len(clips))

    def _resolve_slices_video_dir(self, root_dir=""):
        candidates = []
        if root_dir:
            candidates.extend([
                os.path.join(root_dir, "切片", "视频"),
                os.path.join(root_dir, "切片"),
                root_dir,
            ])
        global_field = getattr(self, "global_input_dir", None)
        global_value = global_field.text().strip() if global_field is not None else ""
        if global_value and global_value != root_dir:
            candidates.extend([
                os.path.join(global_value, "切片", "视频"),
                os.path.join(global_value, "切片"),
            ])
        for path in candidates:
            if path and os.path.isdir(path):
                return path
        return root_dir

    def _step5_burn_stats(self):
        params = getattr(self, "_active_task_params", {}) or {}
        input_dir = params.get("input_dir") if self._is_task_running() else ""
        if not input_dir:
            field = getattr(self, "subtitle_input_dir", None)
            input_dir = field.text().strip() if field is not None else ""
        slices_dir = self._resolve_slices_video_dir(input_dir)
        if not slices_dir or not os.path.isdir(slices_dir):
            return (0, 0)
        sources = [
            f for f in os.listdir(slices_dir)
            if f.lower().endswith(".mp4") and not f.lower().endswith("_subbed.mp4")
        ]
        done = sum(1 for f in sources if os.path.exists(os.path.join(slices_dir, f[:-4] + "_subbed.mp4")))
        return (done, len(sources))

    def _step6_cover_stats(self):
        root_dir = self._status_input_dir_for_step(6)
        slices_dir = self._resolve_slices_video_dir(root_dir)
        if not slices_dir or not os.path.isdir(slices_dir):
            return (0, 0)
        subbed = [f for f in os.listdir(slices_dir) if f.lower().endswith("_subbed.mp4")]
        covers_dir = os.path.join(root_dir, "切片", "封面") if root_dir else ""
        if not os.path.isdir(covers_dir):
            covers_dir = os.path.join(os.path.dirname(slices_dir), "封面")
        covers = set(os.listdir(covers_dir)) if os.path.isdir(covers_dir) else set()
        done = 0
        for video in subbed:
            base = video[:-len("_subbed.mp4")]
            if any(base in c and c.lower().endswith((".jpg", ".png", ".jpeg")) for c in covers):
                done += 1
        return (done, len(subbed))

    def _step7_upload_stats(self, input_dir):
        manifest_path = os.path.join(input_dir, "upload_manifest.json")
        if not os.path.exists(manifest_path):
            return (0, 0)
        with open(manifest_path, "r", encoding="utf-8") as f:
            items = json.load(f)
        if not isinstance(items, list):
            return (0, 0)
        done = sum(1 for item in items if item.get("upload_status") == "success")
        return (done, len(items))

    def log(self, message, msg_type="info"):
        if msg_type == "error" and hasattr(self, "_log_toggle_btn"):
            self._ensure_log_expanded()
        if not hasattr(self, "log_text") or self.log_text is None:
            print(f"[LOG-{msg_type}] {message}")
            return
        if self._log_flush_timer.isActive() and self._log_queue:
            self._log_queue.append((message, msg_type))
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.appendPlainText(f"[{timestamp}] [{msg_type.upper()}] {message}")

    def on_log(self, message, msg_type):
        if msg_type == "error" and hasattr(self, "_log_toggle_btn"):
            self._ensure_log_expanded()
        super().on_log(message, msg_type)

    def _flush_log_queue(self):
        """UI style optimization: light log colors while keeping batched rendering."""
        if not self._log_queue:
            self._log_flush_timer.stop()
            return

        parts = []
        ts = datetime.now().strftime("%H:%M:%S")
        while self._log_queue:
            message, msg_type = self._log_queue.popleft()
            parts.append(f"[{ts}] [{msg_type.upper()}] {message}")
        self.log_text.appendPlainText("\n".join(parts))

    def refresh_file_list(self):
        """UI presentation optimization: keep file scan behavior and add type icons/colors."""
        input_dir = self.step1_input_dir.text()
        if not input_dir or not os.path.exists(input_dir):
            self.log("错误：目录不存在", "error")
            return

        try:
            rows = []
            total = 0
            type_counts = {"flv": 0, "mp4": 0, "srt": 0, "video": 0}
            for item in os.listdir(input_dir):
                item_path = os.path.join(input_dir, item)
                if not os.path.isfile(item_path):
                    continue
                ext = os.path.splitext(item)[1].lower()
                if ext in [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv"] or ext == ".srt":
                    total += 1
                    if ext == ".flv":
                        type_counts["flv"] += 1
                    elif ext == ".mp4":
                        type_counts["mp4"] += 1
                    elif ext == ".srt":
                        type_counts["srt"] += 1
                    else:
                        type_counts["video"] += 1
                    rows.append(self._file_row_text(item, item_path, ext))

            if rows:
                summary = self._file_list_summary_text(total, type_counts)
                self.file_list_text.setPlainText(summary + "\n" + "\n".join(rows))
                self.log(f"找到 {total} 个文件", "success")
            else:
                self.file_list_text.setPlainText("目录为空")
                self.log("目录为空", "info")
            self._refresh_status_metrics()
        except Exception as e:
            self.log(f"读取目录失败: {e}", "error")

    def _file_list_summary_text(self, total, type_counts):
        parts = [f"共 {total} 个"]
        if type_counts.get("flv"):
            parts.append(f"FLV {type_counts['flv']}")
        if type_counts.get("mp4"):
            parts.append(f"MP4 {type_counts['mp4']}")
        if type_counts.get("srt"):
            parts.append(f"SRT {type_counts['srt']}")
        if type_counts.get("video"):
            parts.append(f"其他视频 {type_counts['video']}")
        return " | ".join(parts)

    def _file_row_text(self, name, path, ext):
        if ext == ".flv":
            badge, label = "FLV", "原始"
        elif ext == ".mp4":
            badge, label = "MP4", "成品"
        elif ext == ".srt":
            badge, label = "SRT", "字幕"
        else:
            badge, label = ext.replace(".", "").upper(), "视频"
        return f"[{badge}] {name}  {label}"


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    font = QFont("Microsoft YaHei")
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(build_app_qss(COLORS))

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLORS["bg"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLORS["text"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLORS["card"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLORS["text"]))
    app.setPalette(palette)

    window = LightAutoClipGUI()
    if LightAutoClipGUI.SHOW_MAXIMIZED:
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
