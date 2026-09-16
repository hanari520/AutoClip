# -*- coding: utf-8 -*-
"""
AutoClip 配置生成器 — tkinter 桌面版
启动: launchers\启动配置生成器.bat（或 python config_gui.py）
"""
import json, os, sys, threading, tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import asyncio
from utils.meme_keywords import merge_keywords, normalize_keywords

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "configs", "streamers")
GUI_CONFIG = os.path.join(PROJECT_ROOT, "configs", "gui_config.json")

# ── 配色：深灰底 + 高对比 —————————————————————————
BG      = "#2b2b3c"   # 主背景
BG_CARD = "#35354a"   # 卡片背景
FG      = "#e8e8f0"   # 主文字
ACC     = "#6c78fa"   # 强调蓝紫
GREEN   = "#4ade80"
AMBER   = "#fbbf24"
RED     = "#f87171"
MUTED   = "#a0a0b8"   # 辅助文字
INPUT_BG = "#3d3d56"  # 输入框背景
INPUT_FG = "#f0f0f8"

FONT   = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_B = ("Microsoft YaHei UI", 11, "bold")
FONT_T = ("Microsoft YaHei UI", 14, "bold")

DEFAULT_STEP1_PREPARE = {
    "convert_flv": True,
    "keep_flv_source": False,
    "overwrite": False,
    "portrait_to_landscape": True,
    "portrait_overwrite": False,
    "delete_portrait_source": False,
    "landscape_width": 1920,
    "landscape_height": 1080,
    "landscape_blur": 20,
    "burn_danmu": True,
    "burn_danmu_overwrite": False,
    "keep_danmu_ass": False,
    "recursive": False,
}

TEMPLATES = {
    "virtual_up_general": {
        "name": "VUP虚拟主播（通用）",
        "desc": "SenseVoiceSmall + LLM + 虚拟主播分区(tid=208)",
        "cfg": {
            "description": "",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":208,"copyright":"original","no_reprint":False,"tags":["虚拟主播","ai切片"]},
        }
    },
    "virtual_up_singer": {
        "name": "歌势VUP",
        "desc": "音乐翻唱分区(tid=36) + LLM高光",
        "cfg": {
            "description": "",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":36,"copyright":"original","no_reprint":False,"tags":["翻唱","ai切片"]},
        }
    },
    "daily_life": {
        "name": "生活日常",
        "desc": "杂谈/日常向：生活·日常分区(tid=154)",
        "cfg": {
            "description": "",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":154,"copyright":"original","no_reprint":False,"tags":["日常","ai切片"]},
        }
    },
}


class ConfigApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AutoClip 配置生成器")
        self.root.geometry("720x860")
        self.root.configure(bg=BG)
        self.root.minsize(580, 700)

        self.current_streamer = ""
        self.keywords = []

        # 自定义 ttk 样式
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TCombobox",
            fieldbackground=INPUT_BG, background=INPUT_BG,
            foreground=FG, selectbackground=ACC, selectforeground="#fff",
            arrowcolor=FG, borderwidth=0, relief="flat")
        style.map("TCombobox", fieldbackground=[("readonly", INPUT_BG)], background=[("readonly", INPUT_BG)])
        style.configure("TScrollbar", background=BG_CARD, troughcolor=BG, arrowcolor=MUTED, borderwidth=0)

        self._build_ui()
        self._load_streamers()

    # ════════════════════  UI 构建  ════════════════════

    def _build_ui(self):
        # ── 顶部栏 ──
        top = tk.Frame(self.root, bg=ACC, height=52)
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        tk.Label(top, text="AutoClip  配置生成器", bg=ACC, fg="#ffffff", font=FONT_T).pack(side="left", padx=20, pady=10)

        # ── 滚动区域 ──
        canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        self.main_frame = tk.Frame(canvas, bg=BG)
        self.main_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.main_frame, anchor="nw", width=700)
        canvas.configure(yscrollcommand=scrollbar.set)

        def _mw(e):
            canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _mw)

        canvas.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=6)
        scrollbar.pack(side="right", fill="y")

        # ═══ 卡片：主播选择 ═══
        card = self._card("主播")
        self._label(card, "选择已有主播").pack(anchor="w")
        row = tk.Frame(card, bg=BG_CARD)
        row.pack(fill="x", pady=(2, 8))
        self.streamer_var = tk.StringVar()
        self.streamer_combo = ttk.Combobox(row, textvariable=self.streamer_var, state="readonly", font=FONT)
        self.streamer_combo.pack(side="left", fill="x", expand=True)
        self.streamer_combo.bind("<<ComboboxSelected>>", lambda e: self._load_config())

        tk.Frame(card, bg=BG_CARD, height=8).pack(fill="x")

        self._label(card, "或新建主播").pack(anchor="w")
        nr = tk.Frame(card, bg=BG_CARD)
        nr.pack(fill="x", pady=(2, 0))
        self.new_name_var = tk.StringVar()
        self.new_entry = tk.Entry(nr, textvariable=self.new_name_var, bg=INPUT_BG, fg=INPUT_FG,
                                  insertbackground=FG, font=FONT, relief="flat")
        self.new_entry.pack(side="left", fill="x", expand=True)
        self.new_entry.bind("<Return>", lambda e: self._confirm_new())
        tk.Button(nr, text="确认", bg=GREEN, fg="#111", font=FONT_B, relief="flat", padx=14, pady=2,
                  activebackground="#22c55e", command=self._confirm_new).pack(side="left", padx=(8, 0))

        # 模板
        tk.Frame(card, bg=BG_CARD, height=8).pack(fill="x")
        row2 = tk.Frame(card, bg=BG_CARD)
        row2.pack(fill="x", pady=(4, 0))
        self.tpl_var = tk.StringVar()
        self.tpl_combo = ttk.Combobox(row2, textvariable=self.tpl_var, state="readonly", font=FONT)
        tpl_names = [""] + [f"{k}: {v['name']}" for k, v in TEMPLATES.items()]
        self.tpl_combo["values"] = tpl_names
        self.tpl_combo.current(0)
        self.tpl_combo.pack(side="left", fill="x", expand=True)
        tk.Button(row2, text="应用模板", bg=ACC, fg="#fff", font=FONT_B, relief="flat", padx=14, pady=2,
                  activebackground="#5b6bf0", command=self._apply_template).pack(side="left", padx=(8, 0))

        # ═══ 卡片：基本信息 ═══
        card = self._card("基本信息")
        for label, var_attr in [
            ("主播描述（简介/定位）", "cfg_desc"),
            ("B站 UID", "cfg_uid"),
            ("直播间 ID", "cfg_room"),
        ]:
            self._label(card, label).pack(anchor="w", pady=(6, 2))
            e = tk.Entry(card, bg=INPUT_BG, fg=INPUT_FG, insertbackground=FG, font=FONT, relief="flat")
            e.pack(fill="x")
            setattr(self, var_attr, e)

        # ═══ 折叠面板：步骤 2-7 ═══
        steps = [
            ("步骤 1", "准备录播", self._build_step1),
            ("步骤 2", "字幕转录", self._build_step2),
            ("步骤 3", "高光分析", self._build_step3),
            ("步骤 4", "视频切片", self._build_step4),
            ("步骤 5", "字幕烧录", self._build_step5),
            ("步骤 6", "封面生成", self._build_step6),
            ("步骤 7", "B站投稿", self._build_step7),
        ]
        for sid, label, builder in steps:
            self._collapsible(f"{sid} · {label}", builder)

        # ═══ 卡片：关键词 ═══
        card = self._card("关键词 / 标签")
        self._label(card, "输入关键词后回车或点添加").pack(anchor="w")
        kwr = tk.Frame(card, bg=BG_CARD)
        kwr.pack(fill="x", pady=(2, 0))
        self.kw_entry = tk.Entry(kwr, bg=INPUT_BG, fg=INPUT_FG, insertbackground=FG, font=FONT, relief="flat")
        self.kw_entry.pack(side="left", fill="x", expand=True)
        self.kw_entry.bind("<Return>", lambda e: self._add_keyword())
        tk.Button(kwr, text="添加", bg=ACC, fg="#fff", font=FONT_B, relief="flat", padx=12, pady=2,
                  activebackground="#5b6bf0", command=self._add_keyword).pack(side="left", padx=(8, 0))
        self.kw_frame = tk.Frame(card, bg=BG_CARD)
        self.kw_frame.pack(fill="x", pady=(8, 0))

        # ═══ 卡片：梗搜索 ═══
        card = self._card("梗搜索  (B站 + LLM)")
        self._label(card, "分析主播相关梗和关键词").pack(anchor="w")

        msr = tk.Frame(card, bg=BG_CARD)
        msr.pack(fill="x", pady=(6, 0))
        tk.Label(msr, text="月数", bg=BG_CARD, fg=MUTED, font=FONT_S).pack(side="left")
        self.meme_months = tk.Entry(msr, bg=INPUT_BG, fg=INPUT_FG, insertbackground=FG, font=FONT, relief="flat", width=5)
        self.meme_months.insert(0, "3")
        self.meme_months.pack(side="left", padx=(2, 10))
        tk.Label(msr, text="条数", bg=BG_CARD, fg=MUTED, font=FONT_S).pack(side="left")
        self.meme_max = tk.Entry(msr, bg=INPUT_BG, fg=INPUT_FG, insertbackground=FG, font=FONT, relief="flat", width=5)
        self.meme_max.insert(0, "50")
        self.meme_max.pack(side="left", padx=(2, 10))
        self.meme_btn = tk.Button(msr, text="开始分析", bg=ACC, fg="#fff", font=FONT_B, relief="flat", padx=14, pady=2,
                                  activebackground="#5b6bf0", command=self._meme_search)
        self.meme_btn.pack(side="left")

        self.meme_log = scrolledtext.ScrolledText(card, height=9, bg=INPUT_BG, fg="#c0c0d0", font=("Consolas", 9),
                                                  relief="flat", wrap="word", state="disabled", borderwidth=0)
        self.meme_log.pack(fill="x", pady=(8, 0))

        # ═══ 底部 ═══
        bar = tk.Frame(self.main_frame, bg=BG)
        bar.pack(fill="x", pady=(16, 24))
        tk.Button(bar, text="保存配置", bg=GREEN, fg="#111", font=("Microsoft YaHei UI", 13, "bold"),
                  relief="flat", padx=32, pady=8, activebackground="#22c55e", command=self._save_config).pack()

        self.status_var = tk.StringVar()
        tk.Label(self.main_frame, textvariable=self.status_var, bg=BG, fg=GREEN, font=FONT).pack(pady=(0, 8))

    # ════════════════════  UI 工具方法  ════════════════════

    def _card(self, title):
        """带标题的圆角卡片"""
        f = tk.Frame(self.main_frame, bg=BG_CARD, bd=0, highlightthickness=0)
        f.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(f, text="  " + title, bg=BG_CARD, fg=FG, font=FONT_B).pack(anchor="w", pady=(10, 0))
        body = tk.Frame(f, bg=BG_CARD)
        body.pack(fill="x", padx=14, pady=(6, 12))
        return body

    def _collapsible(self, title, builder_func):
        """可折叠区域"""
        card = tk.Frame(self.main_frame, bg=BG_CARD, bd=0, highlightthickness=0)
        card.pack(fill="x", padx=10, pady=(0, 6))

        hdr = tk.Frame(card, bg=BG_CARD, cursor="hand2")
        hdr.pack(fill="x")
        self.hdr_lbl = tk.Label(hdr, text="▶  " + title, bg=BG_CARD, fg=FG, font=FONT_B, anchor="w")
        self.hdr_lbl.pack(side="left", padx=14, pady=10)
        body = tk.Frame(card, bg=BG_CARD)
        body.pack_forget()

        def toggle(h=hdr, b=body, lbl=self.hdr_lbl):
            if b.winfo_ismapped():
                b.pack_forget()
                lbl.config(text="▶  " + title)
            else:
                b.pack(fill="x", padx=14, pady=(0, 12))
                lbl.config(text="▼  " + title)

        hdr.bind("<Button-1>", lambda e: toggle())
        self.hdr_lbl.bind("<Button-1>", lambda e: toggle())
        builder_func(body)

    def _label(self, parent, text):
        return tk.Label(parent, text=text, bg=BG_CARD, fg=MUTED, font=FONT_S)

    def _entry(self, parent, default="", width=None):
        e = tk.Entry(parent, bg=INPUT_BG, fg=INPUT_FG, insertbackground=FG, font=FONT, relief="flat")
        if default:
            e.insert(0, str(default))
        if width:
            e.config(width=width)
        return e

    def _combo(self, parent, values, default=0):
        cb = ttk.Combobox(parent, values=values, state="readonly", font=FONT)
        if default < len(values):
            cb.current(default)
        return cb

    def _row2(self, parent, left_label, right_label=None):
        """两个字段并排"""
        r = tk.Frame(parent, bg=BG_CARD)
        r.pack(fill="x", pady=(4, 0))
        lf = tk.Frame(r, bg=BG_CARD)
        lf.pack(side="left", fill="x", expand=True)
        self._label(lf, left_label).pack(anchor="w")
        ret = {}
        if right_label:
            tk.Frame(r, bg=BG_CARD, width=12).pack(side="left")
            rf = tk.Frame(r, bg=BG_CARD)
            rf.pack(side="right", fill="x", expand=True)
            self._label(rf, right_label).pack(anchor="w")
            return lf, rf
        return lf, None

    # ════════════════════  步骤构建  ════════════════════

    def _check(self, parent, text, default=False):
        var = tk.BooleanVar(value=default)
        cb = tk.Checkbutton(
            parent,
            text=text,
            variable=var,
            bg=BG_CARD,
            fg=FG,
            selectcolor=BG_CARD,
            activebackground=BG_CARD,
            activeforeground=FG,
            font=FONT,
        )
        cb.pack(anchor="w", pady=(4, 0))
        return var

    def _build_step1(self, f):
        self.s1_convert_flv = self._check(f, "1. FLV 转 MP4（成功后默认删除 FLV）", True)
        self.s1_keep_flv = self._check(f, "保留原 FLV 文件", False)
        self.s1_portrait = self._check(f, "2. 竖屏转横屏 1080p（横屏自动跳过）", True)
        self.s1_portrait_overwrite = self._check(f, "重新处理已转过的竖屏视频", False)
        self.s1_delete_portrait = self._check(f, "竖屏转横屏后不保留竖屏原版备份", False)
        la, ra = self._row2(f, "横屏尺寸", "背景模糊")
        self.s1_landscape_size = self._entry(la, "1920x1080"); self.s1_landscape_size.pack(fill="x")
        self.s1_landscape_blur = self._entry(ra, "20"); self.s1_landscape_blur.pack(fill="x")
        self.s1_burn_danmu = self._check(f, "3. 压制同名 MP4 + XML 弹幕", True)
        self.s1_burn_danmu_overwrite = self._check(f, "重新压制已存在的弹幕版", False)
        self.s1_keep_danmu_ass = self._check(f, "保留弹幕 ASS 文件", False)
        self.s1_recursive = self._check(f, "递归扫描子目录", False)
        self.s1_overwrite = self._check(f, "FLV 转 MP4 时覆盖已有 MP4", False)

    def _build_step2(self, f):
        la, ra = self._row2(f, "引擎", "模型")
        self.s2_engine = self._combo(la, ["funasr", "whisper", "belle"]); self.s2_engine.pack(fill="x")
        self.s2_model = self._combo(ra, ["SenseVoiceSmall", "large-v3-turbo"]); self.s2_model.pack(fill="x")

        la, ra = self._row2(f, "语言", "VAD")
        self.s2_lang = self._entry(la, "zh"); self.s2_lang.pack(fill="x")
        self.s2_vad = self._combo(ra, ["yes", "no"]); self.s2_vad.pack(fill="x")

        self._label(f, "说话人分离").pack(anchor="w", pady=(8, 2))
        self.s2_dia = self._combo(f, ["no", "yes"]); self.s2_dia.pack(fill="x")

    def _build_step3(self, f):
        self._label(f, "分析模式").pack(anchor="w")
        self.s3_mode = self._combo(f, ["llm", "danmaku", "combined"]); self.s3_mode.pack(fill="x")
        self._label(f, "提示词目录（可选）").pack(anchor="w", pady=(8, 2))
        self.s3_prompt = self._entry(f); self.s3_prompt.pack(fill="x")

    def _build_step4(self, f):
        self._label(f, "切片输出目录").pack(anchor="w")
        self.s4_out = self._entry(f, "{input_dir}/切片"); self.s4_out.pack(fill="x")

    def _build_step5(self, f):
        la, ra = self._row2(f, "字幕引擎", "模型")
        self.s5_engine = self._combo(la, ["whisper", "funasr", "belle"]); self.s5_engine.pack(fill="x")
        self.s5_model = self._combo(ra, ["large-v3-turbo", "SenseVoiceSmall"]); self.s5_model.pack(fill="x")

        la, ra = self._row2(f, "语言", "VAD")
        self.s5_lang = self._entry(la, "zh"); self.s5_lang.pack(fill="x")
        self.s5_vad = self._combo(ra, ["yes", "no"]); self.s5_vad.pack(fill="x")

        self._label(f, "字体").pack(anchor="w", pady=(8, 2))
        self.s5_font = self._entry(f, "MaokenAssortedSans"); self.s5_font.pack(fill="x")

        la, ra = self._row2(f, "字号", "描边")
        self.s5_size = self._entry(la, "80"); self.s5_size.pack(fill="x")
        self.s5_outline = self._entry(ra, "8"); self.s5_outline.pack(fill="x")

        self._label(f, "颜色 (ASS格式)").pack(anchor="w", pady=(8, 2))
        self.s5_color = self._entry(f, "&H00FFFFFF"); self.s5_color.pack(fill="x")
        self._label(f, "描边颜色").pack(anchor="w", pady=(8, 2))
        self.s5_ocolor = self._entry(f, "&H00000000"); self.s5_ocolor.pack(fill="x")
        self._label(f, "阴影 (启用/颜色)").pack(anchor="w", pady=(8, 2))
        sf = tk.Frame(f); sf.pack(fill="x")
        self.s5_shadow = self._combo(sf, ["on", "off"]); self.s5_shadow.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.s5_shadow_color = self._combo(sf, ["black", "white"]); self.s5_shadow_color.pack(side="left", fill="x", expand=True)

    def _build_step6(self, f):
        self._label(f, "封面样式").pack(anchor="w")
        self.s6_style = self._combo(f, ["random_1_4", "style1", "style2", "style3", "style4", "style5"]); self.s6_style.pack(fill="x")
        self._label(f, "封面字体").pack(anchor="w", pady=(8, 2))
        self.s6_font = self._entry(f, "MaoKenShiJinHei-2.ttf"); self.s6_font.pack(fill="x")
        self._label(f, "封面输出目录").pack(anchor="w", pady=(8, 2))
        self.s6_out = self._entry(f, "{input_dir}/切片/封面"); self.s6_out.pack(fill="x")
        self.s6_match = tk.BooleanVar(value=True)
        tc = tk.Checkbutton(f, text="仅匹配 _subbed.mp4", variable=self.s6_match,
                            bg=BG_CARD, fg=FG, selectcolor=BG_CARD, activebackground=BG_CARD,
                            activeforeground=FG, font=FONT)
        tc.pack(anchor="w", pady=(6, 0))

    def _build_step7(self, f):
        self._label(f, "分区 TID").pack(anchor="w")
        self.s7_tid = self._entry(f, "124"); self.s7_tid.pack(fill="x")
        self._label(f, "版权").pack(anchor="w", pady=(8, 2))
        self.s7_cr = self._combo(f, ["original", "reprint"]); self.s7_cr.pack(fill="x")
        self._label(f, "标签（逗号分隔）").pack(anchor="w", pady=(8, 2))
        self.s7_tags = self._entry(f, "VUP,虚拟主播,ai切片"); self.s7_tags.pack(fill="x")

    # ════════════════════  数据操作  ════════════════════

    def _load_streamers(self):
        streamers = []
        if os.path.isdir(CONFIG_DIR):
            for fn in sorted(os.listdir(CONFIG_DIR)):
                if fn.endswith(".json"):
                    streamers.append(fn[:-5])
        self.streamer_combo["values"] = [""] + streamers
        self.streamer_combo.current(0)

    def _load_config(self):
        name = self.streamer_var.get()
        if not name:
            self._reset_form()
            return
        self.current_streamer = name
        cfg_path = os.path.join(CONFIG_DIR, f"{name}.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            self._fill_form(cfg)
            self.status_var.set(f"已加载: {name}")
        except Exception as e:
            self.status_var.set(f"读取失败: {e}")

    def _fill_form(self, cfg):
        self.cfg_desc.delete(0, "end"); self.cfg_desc.insert(0, cfg.get("description", ""))
        bl = cfg.get("bilibili_links", {})
        self.cfg_uid.delete(0, "end"); self.cfg_uid.insert(0, bl.get("uid", ""))
        self.cfg_room.delete(0, "end"); self.cfg_room.insert(0, bl.get("room", ""))

        s = cfg.get("step1_prepare", {})
        self.s1_convert_flv.set(s.get("convert_flv", True))
        self.s1_keep_flv.set(s.get("keep_flv_source", s.get("keep_source", False)))
        self.s1_portrait.set(s.get("portrait_to_landscape", False))
        self.s1_portrait_overwrite.set(s.get("portrait_overwrite", s.get("landscape_overwrite", False)))
        self.s1_delete_portrait.set(s.get("delete_portrait_source", False))
        self.s1_landscape_size.delete(0, "end")
        self.s1_landscape_size.insert(0, f"{s.get('landscape_width', 1920)}x{s.get('landscape_height', 1080)}")
        self.s1_landscape_blur.delete(0, "end"); self.s1_landscape_blur.insert(0, str(s.get("landscape_blur", 20)))
        self.s1_burn_danmu.set(s.get("burn_danmu", False))
        self.s1_burn_danmu_overwrite.set(s.get("burn_danmu_overwrite", s.get("overwrite_danmu", False)))
        self.s1_keep_danmu_ass.set(s.get("keep_danmu_ass", False))
        self.s1_recursive.set(s.get("recursive", False))
        self.s1_overwrite.set(s.get("overwrite", False))

        s = cfg.get("step2_subtitle", {})
        self.s2_engine.set(s.get("engine", "funasr"))
        self.s2_model.set(s.get("model", "SenseVoiceSmall"))
        self.s2_lang.delete(0, "end"); self.s2_lang.insert(0, s.get("language", "zh"))
        self.s2_vad.set("yes" if s.get("vad", True) else "no")
        self.s2_dia.set("yes" if s.get("speaker_diarization", False) else "no")

        s = cfg.get("step3_highlight", {})
        self.s3_mode.set(s.get("mode", "llm"))
        self.s3_prompt.delete(0, "end"); self.s3_prompt.insert(0, s.get("prompt_dir", ""))

        s = cfg.get("step4_clip", {})
        self.s4_out.delete(0, "end"); self.s4_out.insert(0, s.get("output_dir", "{input_dir}/切片"))

        s = cfg.get("step5_burn", {})
        self.s5_engine.set(s.get("engine", "whisper"))
        self.s5_model.set(s.get("model", "large-v3-turbo"))
        self.s5_lang.delete(0, "end"); self.s5_lang.insert(0, s.get("language", "zh"))
        self.s5_vad.set("yes" if s.get("vad", True) else "no")
        self.s5_font.delete(0, "end"); self.s5_font.insert(0, s.get("font", "MaokenAssortedSans"))
        self.s5_size.delete(0, "end"); self.s5_size.insert(0, str(s.get("font_size", 80)))
        self.s5_outline.delete(0, "end"); self.s5_outline.insert(0, str(s.get("outline", 8)))
        self.s5_color.delete(0, "end"); self.s5_color.insert(0, s.get("color", "&H00FFFFFF"))
        self.s5_ocolor.delete(0, "end"); self.s5_ocolor.insert(0, s.get("outline_color", "&H00000000"))
        self.s5_shadow.set("on" if s.get("shadow", False) else "off")
        self.s5_shadow_color.set(s.get("shadow_color", "black"))

        s = cfg.get("step6_cover", {})
        self.s6_style.set(s.get("style", "random_1_4"))
        self.s6_font.delete(0, "end"); self.s6_font.insert(0, s.get("font", "MaoKenShiJinHei-2.ttf"))
        self.s6_out.delete(0, "end"); self.s6_out.insert(0, s.get("output_dir", "{input_dir}/切片/封面"))
        self.s6_match.set(s.get("match_subbed_only", True))

        s = cfg.get("step7_upload", {})
        self.s7_tid.delete(0, "end"); self.s7_tid.insert(0, str(s.get("tid", 124)))
        self.s7_cr.set(s.get("copyright", "original"))
        self.s7_tags.delete(0, "end"); self.s7_tags.insert(0, ",".join(s.get("tags", [])))

        self.keywords = normalize_keywords(cfg.get("keywords", []))
        self._render_keywords()

    def _reset_form(self):
        self.current_streamer = ""
        self.cfg_desc.delete(0, "end")
        self.cfg_uid.delete(0, "end")
        self.cfg_room.delete(0, "end")
        self.s1_convert_flv.set(True)
        self.s1_keep_flv.set(False)
        self.s1_portrait.set(True)
        self.s1_portrait_overwrite.set(False)
        self.s1_delete_portrait.set(False)
        self.s1_landscape_size.delete(0, "end"); self.s1_landscape_size.insert(0, "1920x1080")
        self.s1_landscape_blur.delete(0, "end"); self.s1_landscape_blur.insert(0, "20")
        self.s1_burn_danmu.set(True)
        self.s1_burn_danmu_overwrite.set(False)
        self.s1_keep_danmu_ass.set(False)
        self.s1_recursive.set(False)
        self.s1_overwrite.set(False)
        self.keywords = []
        self._render_keywords()
        self.status_var.set("")

    def _confirm_new(self):
        name = self.new_name_var.get().strip()
        if not name:
            return
        self.current_streamer = name
        self.new_name_var.set("")
        self._reset_form()
        self.streamer_var.set("")
        self.status_var.set(f"新建主播: {name}")

    def _apply_template(self):
        tpl_key = self.tpl_var.get().split(":")[0].strip()
        if not tpl_key or tpl_key not in TEMPLATES:
            return
        if not self.current_streamer:
            messagebox.showwarning("提示", "请先新建或选择主播")
            return
        tpl = TEMPLATES[tpl_key]
        self._fill_form({**tpl["cfg"], "streamer_name": self.current_streamer, "version": 1})
        self.status_var.set(f"已应用模板: {tpl['name']}")

    # ── 关键词 ──

    def _add_keyword(self):
        kw = self.kw_entry.get().strip()
        self.kw_entry.delete(0, "end")
        if kw and kw not in self.keywords:
            self.keywords.append(kw)
            self._render_keywords()

    def _remove_keyword(self, kw):
        self.keywords = [k for k in self.keywords if k != kw]
        self._render_keywords()

    def _render_keywords(self):
        for w in self.kw_frame.winfo_children():
            w.destroy()
        if not self.keywords:
            tk.Label(self.kw_frame, text="（暂无关键词）", bg=BG_CARD, fg=MUTED, font=FONT_S).pack(anchor="w")
            return
        for kw in self.keywords:
            tag = tk.Frame(self.kw_frame, bg=ACC, padx=8, pady=3)
            tag.pack(side="left", padx=3, pady=3)
            tk.Label(tag, text=kw, bg=ACC, fg="#fff", font=FONT_S).pack(side="left")
            x = tk.Label(tag, text=" ×", bg=ACC, fg="#ddd", font=("Microsoft YaHei UI", 10, "bold"), cursor="hand2")
            x.pack(side="left")
            x.bind("<Button-1>", lambda e, k=kw: self._remove_keyword(k))

    # ── 梗搜索 ──

    def _log(self, text):
        self.meme_log.config(state="normal")
        self.meme_log.insert("end", text + "\n")
        self.meme_log.see("end")
        self.meme_log.config(state="disabled")

    def _meme_search(self):
        if not self.current_streamer:
            self.status_var.set("请先新建或选择主播")
            return
        self.meme_log.config(state="normal")
        self.meme_log.delete("1.0", "end")
        self.meme_log.config(state="disabled")
        self.meme_btn.config(state="disabled", text="分析中...")
        threading.Thread(target=self._do_meme_search, daemon=True).start()

    def _do_meme_search(self):
        streamer = self.current_streamer
        months = int(self.meme_months.get() or 3)
        max_results = int(self.meme_max.get() or 50)

        self.root.after(0, lambda: self._log(f"搜索B站关于「{streamer}」的视频（近{months}个月，最多{max_results}条）..."))

        sys.path.insert(0, PROJECT_ROOT)
        try:
            from utils.bilibili_meme_search import search_videos, analyze_titles
        except Exception as e:
            self.root.after(0, lambda err=e: self._log(f"[FAIL] 导入模块失败: {err}"))
            self.root.after(0, lambda: self.meme_btn.config(state="normal", text="开始分析"))
            return

        try:
            titles = asyncio.run(search_videos(keyword=streamer, months=months, max_results=max_results))
        except Exception as e:
            self.root.after(0, lambda err=e: self._log(f"B站搜索失败: {err}"))
            self.root.after(0, lambda: self.meme_btn.config(state="normal", text="开始分析"))
            return

        if not titles:
            self.root.after(0, lambda: self._log("未搜到相关视频"))
            self.root.after(0, lambda: self.meme_btn.config(state="normal", text="开始分析"))
            return

        self.root.after(0, lambda: self._log(f"找到 {len(titles)} 条视频标题，正在LLM分析..."))

        try:
            with open(GUI_CONFIG, "r", encoding="utf-8") as f:
                gui_cfg = json.load(f)
        except Exception:
            gui_cfg = {}
        llm_config = {
            "api_key": gui_cfg.get("global_api_key", ""),
            "base_url": "https://api.siliconflow.cn/v1",
            "model": gui_cfg.get("global_model", "deepseek-ai/DeepSeek-V4-Pro"),
        }
        try:
            result = analyze_titles(titles=titles[:min(len(titles), 40)], streamer=streamer, llm_config=llm_config)
        except Exception as e:
            self.root.after(0, lambda err=e: self._log(f"LLM分析失败: {err}"))
            self.root.after(0, lambda: self.meme_btn.config(state="normal", text="开始分析"))
            return

        keywords = normalize_keywords(result.get("keywords", []))
        summary = result.get("summary", "")
        self.root.after(0, lambda: self._log(f"\n梗分析摘要:\n{summary}"))
        self.root.after(0, lambda: self._log(f"\n推荐关键词 ({len(keywords)}):"))
        for kw in keywords:
            self.root.after(0, lambda k=kw: self._log(f"  - {k}"))
        self.root.after(0, lambda kws=keywords: self._merge_meme_keywords(kws))
        self.root.after(0, lambda: self.meme_btn.config(state="normal", text="开始分析"))

    def _merge_meme_keywords(self, keywords):
        before = len(self.keywords)
        self.keywords = merge_keywords(self.keywords, keywords)
        added = len(self.keywords) - before
        self._render_keywords()
        self._log(f"\n已自动合并 {added} 个新关键词到 keywords，请保存配置。")
        self.status_var.set(f"已合并 {added} 个梗关键词")

    # ── 保存 ──

    def _collect(self):
        def b(cb_val):
            return cb_val == "yes"
        def landscape_size():
            text = self.s1_landscape_size.get().lower().replace("×", "x")
            parts = [p.strip() for p in text.split("x", 1)]
            if len(parts) != 2:
                return 1920, 1080
            try:
                return int(parts[0] or 1920), int(parts[1] or 1080)
            except ValueError:
                return 1920, 1080
        def int_or_default(value, default):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        landscape_width, landscape_height = landscape_size()
        return {
            "streamer_name": self.current_streamer or "未命名",
            "version": 1,
            "description": self.cfg_desc.get(),
            "bilibili_links": {
                "uid": self.cfg_uid.get(),
                "room": self.cfg_room.get(),
            },
            "step1_prepare": {
                "convert_flv": self.s1_convert_flv.get(),
                "keep_flv_source": self.s1_keep_flv.get(),
                "overwrite": self.s1_overwrite.get(),
                "portrait_to_landscape": self.s1_portrait.get(),
                "portrait_overwrite": self.s1_portrait_overwrite.get(),
                "delete_portrait_source": self.s1_delete_portrait.get(),
                "landscape_width": landscape_width,
                "landscape_height": landscape_height,
                "landscape_blur": int_or_default(self.s1_landscape_blur.get() or 20, 20),
                "burn_danmu": self.s1_burn_danmu.get(),
                "burn_danmu_overwrite": self.s1_burn_danmu_overwrite.get(),
                "keep_danmu_ass": self.s1_keep_danmu_ass.get(),
                "recursive": self.s1_recursive.get(),
            },
            "step2_subtitle": {
                "engine": self.s2_engine.get(),
                "model": self.s2_model.get(),
                "language": self.s2_lang.get(),
                "vad": b(self.s2_vad.get()),
                "speaker_diarization": b(self.s2_dia.get()),
            },
            "step3_highlight": {
                "mode": self.s3_mode.get(),
                "prompt_dir": self.s3_prompt.get(),
            },
            "step4_clip": {
                "output_dir": self.s4_out.get(),
            },
            "step5_burn": {
                "skip_asr": False,
                "engine": self.s5_engine.get(),
                "model": self.s5_model.get(),
                "language": self.s5_lang.get(),
                "vad": b(self.s5_vad.get()),
                "font": self.s5_font.get(),
                "font_size": int(self.s5_size.get() or 80),
                "outline": int(self.s5_outline.get() or 8),
                "shadow": self.s5_shadow.get() == "on",
                "shadow_color": self.s5_shadow_color.get(),
                "margin_v": 80,
                "color": self.s5_color.get(),
                "outline_color": self.s5_ocolor.get(),
            },
            "step6_cover": {
                "style": self.s6_style.get(),
                "font": self.s6_font.get(),
                "output_dir": self.s6_out.get(),
                "match_subbed_only": self.s6_match.get(),
            },
            "step7_upload": {
                "tid": int(self.s7_tid.get() or 124),
                "copyright": self.s7_cr.get(),
                "no_reprint": False,
                "tags": [t.strip() for t in self.s7_tags.get().split(",") if t.strip()],
            },
            "keywords": normalize_keywords(self.keywords),
        }

    def _save_config(self):
        if not self.current_streamer:
            messagebox.showwarning("提示", "请先新建或选择主播")
            return
        cfg = self._collect()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        cfg_path = os.path.join(CONFIG_DIR, f"{self.current_streamer}.json")
        try:
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.status_var.set(f"已保存: {cfg_path}")
        except Exception as e:
            self.status_var.set(f"保存失败: {e}")


def main():
    app = ConfigApp()
    app.root.mainloop()


if __name__ == "__main__":
    main()
