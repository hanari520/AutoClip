#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoClip 主播配置生成器
───────────────────────
可视化创建 / 编辑 streamer_configs/{主播名}.json
支持加载模板、逐步骤配置、JSON 实时预览
"""
import os, sys, json

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(PROJECT_ROOT, 'configs', 'streamers')
os.makedirs(CONFIG_DIR, exist_ok=True)

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QFormLayout, QGroupBox, QLabel, QLineEdit, QComboBox, QCheckBox,
    QSpinBox, QPushButton, QTextEdit, QTabWidget, QFileDialog,
    QMessageBox, QPlainTextEdit, QSplitter, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor, QPalette


# ── 分区列表 ──
TID_OPTIONS = [
    (217, "动画·综合"), (208, "虚拟主播"), (218, "动画·MAD·AMV"),
    (219, "动画·MMD·3D"), (154, "生活·日常"), (138, "生活·搞笑"),
    (21, "日常"), (36, "音乐·翻唱"), (30, "音乐·VOCALOID"),
    (31, "音乐·演奏"), (59, "音乐·原创音乐"), (194, "音乐·电台"),
    (177, "纪录片"), (119, "鬼畜"), (22, "游戏·单机"),
    (23, "游戏·电子竞技"), (25, "游戏·音游"), (17, "游戏·Mugen"),
]

# ── 预设模板 ──
TEMPLATES = {
    "virtual_up_general": {
        "name": "🎭 VUP虚拟主播（通用）",
        "description": "虚拟UP主通用模板：FunASR字幕 + LLM高光 + 虚拟主播分区",
        "data": {
            "description": "{streamer} 默认一键切片+投稿配置",
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":208,"tid_name":"虚拟主播","copyright":"original","no_reprint":False,"tags":["虚拟主播","ai切片"]},
        }
    },
    "virtual_up_singer": {
        "name": "🎤 歌势VUP",
        "description": "歌势虚拟UP主：音乐翻唱分区 + LLM高光",
        "data": {
            "description": "{streamer} 默认一键切片+投稿配置",
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":36,"tid_name":"音乐·翻唱","copyright":"original","no_reprint":False,"tags":["翻唱","ai切片"]},
        }
    },
    "daily_life": {
        "name": "📺 生活日常",
        "description": "杂谈/日常向UP主：生活·日常分区",
        "data": {
            "description": "{streamer} 默认一键切片+投稿配置",
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":154,"tid_name":"生活·日常","copyright":"original","no_reprint":False,"tags":["日常","ai切片"]},
        }
    },
}

# ── 字体列表（从 assets/font 扫描）──
def scan_fonts():
    fonts = []
    font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')
    if os.path.exists(font_dir):
        for f in os.listdir(font_dir):
            if f.lower().endswith(('.ttf', '.otf')):
                fonts.append(f)
    return fonts or ["MaokenAssortedSans", "MaoKenShiJinHei-2"]


class ConfigBuilder(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('AutoClip 主播配置生成器')
        self.setMinimumSize(1100, 750)

        self.current_file = None
        self._fonts = None  # 懒加载字体扫描

        self._build_ui()
        self._load_defaults()

    @property
    def fonts(self):
        if self._fonts is None:
            self._fonts = scan_fonts()
        return self._fonts

    # ── UI 构建 ──

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)

        # ── 顶部工具栏 ──
        toolbar = QHBoxLayout()
        self.streamer_input = QLineEdit()
        self.streamer_input.setPlaceholderText("主播名（必填）")
        self.streamer_input.setMinimumWidth(150)
        toolbar.addWidget(QLabel("🎬 主播名:"))
        toolbar.addWidget(self.streamer_input)

        toolbar.addSpacing(20)

        self.template_combo = QComboBox()
        self.template_combo.addItem("-- 加载模板 (可选) --", None)
        for key, tpl in TEMPLATES.items():
            self.template_combo.addItem(tpl["name"], key)
        self.template_combo.currentIndexChanged.connect(self._on_template_changed)
        toolbar.addWidget(QLabel("📋 模板:"))
        toolbar.addWidget(self.template_combo)

        toolbar.addStretch()

        btn_load = QPushButton("📂 加载")
        btn_load.clicked.connect(self._load_config)
        btn_save = QPushButton("💾 保存")
        btn_save.clicked.connect(self._save_config)
        btn_generate = QPushButton("🚀 生成 JSON")
        btn_generate.clicked.connect(self._generate_json)
        btn_generate.setStyleSheet("font-weight:bold; background:#4CAF50; color:white;")
        toolbar.addWidget(btn_load)
        toolbar.addWidget(btn_save)
        toolbar.addWidget(btn_generate)

        main_layout.addLayout(toolbar)

        # ── 分栏：左侧表单 | 右侧预览 ──
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：Tab 表单
        self.tabs = QTabWidget()

        self.tabs.addTab(self._build_step2(), "步骤2 字幕")
        self.tabs.addTab(self._build_step3(), "步骤3 高光")
        self.tabs.addTab(self._build_step4(), "步骤4 切片")
        self.tabs.addTab(self._build_step5(), "步骤5 烧录")
        self.tabs.addTab(self._build_step6(), "步骤6 封面")
        self.tabs.addTab(self._build_step7(), "步骤7 投稿")
        self.tabs.addTab(self._build_links(), "🔗 B站链接")

        scroll = QScrollArea()
        scroll.setWidget(self.tabs)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(620)

        # 右侧：预览
        preview_box = QVBoxLayout()
        preview_box.addWidget(QLabel("📄 JSON 预览:"))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        font = QFont("Consolas", 10)
        self.preview.setFont(font)
        self.preview.setStyleSheet("background:#1e1e1e; color:#d4d4d4;")
        preview_box.addWidget(self.preview)

        preview_widget = QWidget()
        preview_widget.setLayout(preview_box)

        splitter.addWidget(scroll)
        splitter.addWidget(preview_widget)
        splitter.setSizes([650, 400])

        main_layout.addWidget(splitter)

        # ── 底部提示 ──
        self.status_label = QLabel("📝 填写各步骤配置后，点击 🚀 生成 JSON")
        self.status_label.setStyleSheet("color:#888; padding:4px;")
        main_layout.addWidget(self.status_label)

    def _make_group(self, title, layout):
        group = QGroupBox(title)
        group.setLayout(layout)
        return group

    # ── 各步骤 UI ──

    def _build_step2(self):
        w = QWidget()
        l = QFormLayout(w)
        # ASR 引擎
        self.s2_engine = self._combo(["funasr","whisper","belle"])
        l.addRow("ASR 引擎:", self.s2_engine)
        # 模型
        self.s2_model = QLineEdit("SenseVoiceSmall")
        l.addRow("FunASR 模型:", self.s2_model)
        self.s2_whisper_model = self._combo(["large-v3-turbo","large-v3","large-v2","medium","small","base","tiny"])
        l.addRow("Whisper 模型:", self.s2_whisper_model)
        # 语言
        self.s2_lang = self._combo(["zh","en","ja","auto"])
        l.addRow("语言:", self.s2_lang)
        # VAD
        self.s2_vad = QCheckBox("启用 VAD")
        self.s2_vad.setChecked(True)
        l.addRow("", self.s2_vad)
        # 说话人分离
        self.s2_speaker = QCheckBox("说话人分离 (FunASR only)")
        l.addRow("", self.s2_speaker)
        return self._wrap_scroll(w)

    def _build_step3(self):
        w = QWidget()
        l = QFormLayout(w)
        self.s3_mode = self._combo(["llm","danmu","multi"], ticket={
            "llm":"纯 LLM 语义分析",
            "danmu":"弹幕热度分析",
            "multi":"多保险融合 (llm+danmu+emotion)"
        })
        l.addRow("分析模式:", self.s3_mode)
        self.s3_prompt_dir = QLineEdit()
        self.s3_prompt_dir.setPlaceholderText("提示词目录路径")
        l.addRow("提示词目录:", self.s3_prompt_dir)
        return self._wrap_scroll(w)

    def _build_step4(self):
        w = QWidget()
        l = QFormLayout(w)
        self.s4_output = QLineEdit("{input_dir}/切片")
        l.addRow("切片输出目录:", self.s4_output)
        l.addRow("提示", QLabel("可用占位符: {input_dir}  = 录制目录"))
        return self._wrap_scroll(w)

    def _build_step5(self):
        w = QWidget()
        l = QFormLayout(w)

        self.s5_skip = QCheckBox("跳过 ASR（使用已有 SRT）")
        l.addRow("", self.s5_skip)

        self.s5_engine = self._combo(["whisper","portable","direct","belle"])
        l.addRow("ASR 引擎:", self.s5_engine)
        self.s5_model = self._combo(["large-v3-turbo","large-v3","large-v2","SenseVoiceSmall","paraformer-zh"])
        l.addRow("ASR 模型:", self.s5_model)
        self.s5_lang = self._combo(["zh","en","ja","auto"])
        l.addRow("语言:", self.s5_lang)
        self.s5_vad = QCheckBox("启用 VAD")
        self.s5_vad.setChecked(True)
        l.addRow("", self.s5_vad)

        l.addRow(QLabel("── 字幕样式 ──"), QLabel(""))
        self.s5_font = self._combo(self.fonts)
        l.addRow("字体:", self.s5_font)
        self.s5_font_size = self._spin(80, 20, 300)
        l.addRow("字号:", self.s5_font_size)
        self.s5_outline = self._spin(8, 0, 30)
        l.addRow("描边宽:", self.s5_outline)
        self.s5_shadow = QCheckBox("启用阴影")
        l.addRow("", self.s5_shadow)
        self.s5_shadow_color = self._combo(["black","white"])
        l.addRow("阴影颜色:", self.s5_shadow_color)
        self.s5_margin_v = self._spin(80, 0, 400)
        l.addRow("底部边距:", self.s5_margin_v)

        self.s5_color = self._combo(["&H00FFFFFF (白)","&H0000E1FF (黄)","&H00c4f3f9 (浅蓝)","&H009972F0 (紫)","&H00747DDB (蓝)","&H00906657 (棕)"])
        l.addRow("字体颜色:", self.s5_color)
        self.s5_outline_color = self._combo(["&H00000000 (黑)","&H00FFFFFF (白)","&H0038424a (深蓝灰)"])
        l.addRow("描边颜色:", self.s5_outline_color)
        return self._wrap_scroll(w)

    def _build_step6(self):
        w = QWidget()
        l = QFormLayout(w)
        self.s6_style = self._combo(["random_1_4","style1","style2","style3","style4","style5"], ticket={
            "random_1_4":"随机样式1-5","style1":"上白下黄","style2":"上黄下白","style3":"居中大字","style4":"艺术简洁","style5":"多行靠左双色"
        })
        l.addRow("封面样式:", self.s6_style)
        self.s6_font = self._combo(self.fonts)
        l.addRow("封面字体:", self.s6_font)
        self.s6_output = QLineEdit("{input_dir}/切片/封面")
        l.addRow("输出目录:", self.s6_output)
        self.s6_subbed = QCheckBox("只匹配 _subbed.mp4")
        self.s6_subbed.setChecked(True)
        l.addRow("", self.s6_subbed)
        return self._wrap_scroll(w)

    def _build_step7(self):
        w = QWidget()
        l = QFormLayout(w)

        # 分区下拉
        self.s7_tid = QComboBox()
        for tid, name in TID_OPTIONS:
            self.s7_tid.addItem(f"{name} ({tid})", tid)
        l.addRow("投稿分区:", self.s7_tid)

        self.s7_copyright = self._combo(["original (原创)","reprint (转载)"], ticket={
            "original":"原创","reprint":"转载"
        })
        l.addRow("版权类型:", self.s7_copyright)
        self.s7_no_reprint = QCheckBox("禁止转载")
        l.addRow("", self.s7_no_reprint)

        self.s7_tags = QLineEdit("虚拟主播, ai切片")
        self.s7_tags.setPlaceholderText("逗号分隔: 虚拟主播, ai切片, 切片")
        l.addRow("默认标签:", self.s7_tags)

        # 标题规则
        self.s7_title_rule = QLineEdit("从文件名提取: 去掉 [HH-MM-S] 时间戳前缀 和 _subbed 后缀")
        l.addRow("标题规则:", self.s7_title_rule)

        # 简介模板
        self.s7_desc_tpl = QPlainTextEdit()
        self.s7_desc_tpl.setMaximumHeight(80)
        self.s7_desc_tpl.setPlaceholderText("{streamer}的个人主页：{space_url}\n{streamer}的直播间：{live_url}\n\n{summary}")
        l.addRow("简介模板:", self.s7_desc_tpl)

        return self._wrap_scroll(w)

    def _build_links(self):
        w = QWidget()
        l = QFormLayout(w)
        self.links_space = QLineEdit()
        self.links_space.setPlaceholderText("https://space.bilibili.com/xxx")
        l.addRow("B站空间:", self.links_space)
        self.links_live = QLineEdit()
        self.links_live.setPlaceholderText("https://live.bilibili.com/xxx")
        l.addRow("B站直播间:", self.links_live)
        return self._wrap_scroll(w)

    # ── 辅助 ──

    def _wrap_scroll(self, w):
        s = QScrollArea()
        s.setWidget(w)
        s.setWidgetResizable(True)
        return s

    def _combo(self, items, ticket=None):
        c = QComboBox()
        for i, val in enumerate(items):
            label = val
            if ticket:
                k = val.split(" ")[0] if " " in val else val
                label = ticket.get(k, val) if isinstance(ticket, dict) else val
            elif isinstance(val, tuple):
                label = val[0]
            c.addItem(label, val if not isinstance(val, tuple) else val)
        return c

    def _spin(self, val, lo, hi):
        s = QSpinBox()
        s.setRange(lo, hi)
        s.setValue(val)
        return s

    def _load_defaults(self):
        """加载默认值（VUP通用模板）"""
        tpl = TEMPLATES["virtual_up_general"]["data"]
        self._apply_dict(tpl)

    def _apply_dict(self, d):
        """将 dict 应用到表单"""
        if not d:
            return
        # step2
        s2 = d.get("step2_subtitle", {})
        _set_combo(self.s2_engine, s2.get("engine","funasr"))
        self.s2_model.setText(s2.get("model","SenseVoiceSmall"))
        _set_combo(self.s2_whisper_model, s2.get("whisper_model","large-v3-turbo"))
        _set_combo(self.s2_lang, s2.get("language","zh"))
        self.s2_vad.setChecked(s2.get("vad",True))
        self.s2_speaker.setChecked(s2.get("speaker_diarization",False))

        # step3
        s3 = d.get("step3_highlight", {})
        _set_combo(self.s3_mode, s3.get("mode","llm"))
        self.s3_prompt_dir.setText(s3.get("prompt_dir",""))

        # step4
        s4 = d.get("step4_clip", {})
        self.s4_output.setText(s4.get("output_dir","{input_dir}/切片"))

        # step5
        s5 = d.get("step5_burn", {})
        self.s5_skip.setChecked(s5.get("skip_asr",False))
        _set_combo(self.s5_engine, s5.get("engine","whisper"))
        _set_combo(self.s5_model, s5.get("model","large-v3-turbo"))
        _set_combo(self.s5_lang, s5.get("language","zh"))
        self.s5_vad.setChecked(s5.get("vad",True))
        _set_combo(self.s5_font, s5.get("font","MaokenAssortedSans"))
        self.s5_font_size.setValue(s5.get("font_size",80))
        self.s5_outline.setValue(s5.get("outline",8))
        self.s5_shadow.setChecked(s5.get("shadow",False))
        _set_combo(self.s5_shadow_color, s5.get("shadow_color","black"))
        self.s5_margin_v.setValue(s5.get("margin_v",80))
        _set_combo_text(self.s5_color, s5.get("color","&H00FFFFFF"))
        _set_combo_text(self.s5_outline_color, s5.get("outline_color","&H00000000"))

        # step6
        s6 = d.get("step6_cover", {})
        _set_combo(self.s6_style, s6.get("style","random_1_4"))
        _set_combo(self.s6_font, s6.get("font","MaoKenShiJinHei-2.ttf"))
        self.s6_output.setText(s6.get("output_dir","{input_dir}/切片/封面"))
        self.s6_subbed.setChecked(s6.get("match_subbed_only",True))

        # step7
        s7 = d.get("step7_upload", {})
        _set_combo_by_data(self.s7_tid, s7.get("tid",217))
        _set_combo(self.s7_copyright, s7.get("copyright","original"))
        self.s7_no_reprint.setChecked(s7.get("no_reprint",False))
        self.s7_tags.setText(", ".join(s7.get("tags",["虚拟主播","ai切片"])))
        self.s7_title_rule.setText(s7.get("title_rule","从文件名提取"))
        self.s7_desc_tpl.setPlainText(s7.get("desc_template",""))

        # links
        links = d.get("bilibili_links", {})
        self.links_space.setText(links.get("space",""))
        self.links_live.setText(links.get("live",""))

        self._update_preview()

    # ── 收集数据 → dict ──

    def _collect(self):
        """从表单收集所有字段"""
        streamer = self.streamer_input.text().strip()

        def color_val(combo):
            t = combo.currentText()
            m = __import__('re').search(r'&H[0-9a-fA-F]{8}', t)
            return m.group(0) if m else "&H00FFFFFF"

        d = {
            "streamer_name": streamer,
            "version": 1,
            "description": f"{streamer} 默认一键切片+投稿配置",

            "step2_subtitle": {
                "engine": _combo_val(self.s2_engine),
                "model": self.s2_model.text().strip(),
                "language": _combo_val(self.s2_lang),
                "vad": self.s2_vad.isChecked(),
                "speaker_diarization": self.s2_speaker.isChecked(),
            },
            "step3_highlight": {
                "mode": _combo_val(self.s3_mode),
                "prompt_dir": self.s3_prompt_dir.text().strip(),
            },
            "step4_clip": {
                "output_dir": self.s4_output.text().strip() or "{input_dir}/切片",
            },
            "step5_burn": {
                "skip_asr": self.s5_skip.isChecked(),
                "engine": _combo_val(self.s5_engine),
                "model": _combo_val(self.s5_model),
                "language": _combo_val(self.s5_lang),
                "vad": self.s5_vad.isChecked(),
                "font": _combo_val(self.s5_font),
                "font_size": self.s5_font_size.value(),
                "outline": self.s5_outline.value(),
                "shadow": self.s5_shadow.isChecked(),
                "shadow_color": _combo_val(self.s5_shadow_color),
                "margin_v": self.s5_margin_v.value(),
                "color": color_val(self.s5_color),
                "outline_color": color_val(self.s5_outline_color),
            },
            "step6_cover": {
                "style": _combo_val(self.s6_style),
                "font": _combo_val(self.s6_font),
                "output_dir": self.s6_output.text().strip() or "{input_dir}/切片/封面",
                "match_subbed_only": self.s6_subbed.isChecked(),
            },
            "step7_upload": {
                "tid": _combo_data(self.s7_tid),
                "tid_name": self.s7_tid.currentText().split("(")[0].strip(),
                "copyright": "original" if "original" in _combo_val(self.s7_copyright) else "reprint",
                "no_reprint": self.s7_no_reprint.isChecked(),
                "title_rule": self.s7_title_rule.text().strip() or "从文件名提取",
                "desc_template": self.s7_desc_tpl.toPlainText().strip(),
                "cover_rule": "{input_dir}/切片/封面/[{timestamp}] {title}.jpg",
                "tags": [t.strip() for t in self.s7_tags.text().split(",") if t.strip()],
            },
            "bilibili_links": {
                "space": self.links_space.text().strip(),
                "live": self.links_live.text().strip(),
            }
        }
        return d

    def _update_preview(self):
        d = self._collect()
        j = json.dumps(d, ensure_ascii=False, indent=2)
        self.preview.setPlainText(j)

    # ── 事件 ──

    def _on_template_changed(self, idx):
        key = self.template_combo.currentData()
        if not key or key not in TEMPLATES:
            return
        tpl = TEMPLATES[key]["data"]
        self._apply_dict(tpl)
        self.status_label.setText(f"✅ 已加载模板: {TEMPLATES[key]['name']}")

    def _load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载配置", CONFIG_DIR, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.current_file = path
            self.streamer_input.setText(data.get("streamer_name",""))
            self._apply_dict(data)
            self.status_label.setText(f"📂 已加载: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "加载失败", str(e))

    def _save_config(self):
        d = self._collect()
        name = d["streamer_name"]
        if not name:
            QMessageBox.warning(self, "缺少主播名", "请先填写主播名！")
            return

        default_path = os.path.join(CONFIG_DIR, f"{name}.json")
        path, _ = QFileDialog.getSaveFileName(
            self, "保存配置", default_path if self.current_file else default_path, "JSON (*.json)")
        if not path:
            return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            self.current_file = path
            self.status_label.setText(f"💾 已保存: {os.path.basename(path)}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _generate_json(self):
        d = self._collect()
        name = d["streamer_name"]
        if not name:
            QMessageBox.warning(self, "缺少主播名", "请先填写主播名！")
            return

        path = os.path.join(CONFIG_DIR, f"{name}.json")
        if os.path.exists(path):
            r = QMessageBox.question(
                self, "文件已存在", f"{name}.json 已存在，覆盖？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return

        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
            self.current_file = path
            self._update_preview()
            self.status_label.setText(f"🚀 配置已生成: {os.path.basename(path)}")
            QMessageBox.information(self, "生成成功",
                f"主播配置已保存:\n{path}")
        except Exception as e:
            QMessageBox.warning(self, "生成失败", str(e))


# ── 辅助函数 ──

def _set_combo(combo, val):
    for i in range(combo.count()):
        data = combo.itemData(i)
        text = combo.itemText(i)
        if isinstance(data, str) and data == val:
            combo.setCurrentIndex(i)
            return
        if val in text:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)

def _set_combo_text(combo, val):
    for i in range(combo.count()):
        if val in combo.itemText(i):
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)

def _set_combo_by_data(combo, val):
    for i in range(combo.count()):
        if combo.itemData(i) == val:
            combo.setCurrentIndex(i)
            return
    combo.setCurrentIndex(0)

def _combo_val(combo):
    data = combo.currentData()
    if isinstance(data, str):
        return data
    return combo.currentText().split(" ")[0]

def _combo_data(combo):
    return combo.currentData()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    # 暗色基础
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(45,45,48))
    pal.setColor(QPalette.ColorRole.WindowText, Qt.GlobalColor.white)
    pal.setColor(QPalette.ColorRole.Base, QColor(37,37,38))
    pal.setColor(QPalette.ColorRole.Button, QColor(62,62,66))
    pal.setColor(QPalette.ColorRole.ButtonText, Qt.GlobalColor.white)
    app.setPalette(pal)

    win = ConfigBuilder()
    win.show()
    sys.exit(app.exec())