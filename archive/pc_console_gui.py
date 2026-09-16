# -*- coding: utf-8 -*-
"""
AutoClip PC 监控面板 — tkinter 暗色 GUI（增强进度显示版）
启动 web_console.py 服务后可在此窗口查看状态/日志
"""

import json
import math
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext
from urllib.request import urlopen, Request
from urllib.error import URLError

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

BG = "#1e1e30"
CARD = "#2b2b3c"
TEXT = "#e0e0f0"
MUTED = "#a0a0b8"
ACCENT = "#6c78fa"
GREEN = "#4ade80"
RED = "#f87171"
AMBER = "#fbbf24"
CYAN = "#48dbfb"
FONT = ("Microsoft YaHei UI", 11)
FONT_MONO = ("Cascadia Code", 10)
FONT_TITLE = ("Microsoft YaHei UI", 12, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 9)

# 阶段定义（用于进度估算）
PIPELINE_STEPS = [
    ("starting", "启动中"),
    ("步骤2", "字幕识别"),
    ("步骤3", "高光分析"),
    ("步骤4", "视频切片"),
    ("步骤5", "字幕烧录"),
    ("步骤6", "封面生成"),
    ("done", "已完成"),
]


class ConsoleGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AutoClip 移动控制台")
        self.root.geometry("560x760")
        self.root.minsize(460, 560)
        self.root.configure(bg=BG)
        self.server_proc = None
        self.poll_timer = None
        self._reader_thread = None
        self._reader_stop = False

        # 动画状态
        self._spinner_angle = 0
        self._spinner_timer = None
        self._pulse_timer = None
        self._pulse_state = 0
        self._last_step = ""
        self._step_start_time = None
        self._current_progress = 0

        self._build_ui()
        self._start_server()

    def _build_ui(self):
        # ── 标题栏 ──
        header = tk.Frame(self.root, bg=CARD, height=48)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(header, text="🎬 AutoClip 移动控制台", fg=TEXT, bg=CARD,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(side="left", padx=14, pady=10)

        self.status_dot = tk.Canvas(header, width=14, height=14, bg=CARD, highlightthickness=0)
        self.status_dot.pack(side="right", padx=14)
        self._dot = self.status_dot.create_oval(2, 2, 12, 12, fill=MUTED, outline="")

        # ── 状态卡片 ──
        card = tk.Frame(self.root, bg=CARD, padx=14, pady=12)
        card.pack(fill="x", padx=10, pady=(10, 4))

        self.lbl_ip = tk.Label(card, text="", fg=MUTED, bg=CARD, font=FONT_SMALL)
        self.lbl_ip.pack(anchor="w")
        self.lbl_streamer = tk.Label(card, text="", fg=TEXT, bg=CARD, font=FONT)
        self.lbl_streamer.pack(anchor="w", pady=(2, 0))

        # 状态徽章 + 当前阶段
        top_row = tk.Frame(card, bg=CARD)
        top_row.pack(fill="x", pady=(4, 0))
        self.lbl_badge = tk.Label(top_row, text="就绪", fg=BG, bg=AMBER,
                                  font=("Microsoft YaHei UI", 10, "bold"),
                                  padx=10, pady=2)
        self.lbl_badge.pack(side="left")
        self.lbl_step = tk.Label(top_row, text="等待任务开始", fg=AMBER, bg=CARD, font=FONT_TITLE)
        self.lbl_step.pack(side="left", padx=(8, 0))

        # ── 进度条区域 ──
        progress_card = tk.Frame(self.root, bg=CARD, padx=14, pady=12)
        progress_card.pack(fill="x", padx=10, pady=(0, 4))

        top_info = tk.Frame(progress_card, bg=CARD)
        top_info.pack(fill="x")
        self.lbl_percent = tk.Label(top_info, text="0%", fg=TEXT, bg=CARD,
                                    font=("Microsoft YaHei UI", 14, "bold"))
        self.lbl_percent.pack(side="left")
        self.lbl_eta = tk.Label(top_info, text="预计剩余: --", fg=MUTED, bg=CARD, font=FONT_SMALL)
        self.lbl_eta.pack(side="right")

        # 自定义进度条（Canvas，可精确控制样式与动画）
        self.progress_canvas = tk.Canvas(progress_card, height=18, bg=CARD, highlightthickness=0)
        self.progress_canvas.pack(fill="x", pady=(6, 0))
        self._progress_bar = self.progress_canvas.create_rectangle(
            0, 0, 0, 18, fill=ACCENT, outline="", tags="bar"
        )
        self._progress_bg = self.progress_canvas.create_rectangle(
            0, 0, 0, 18, fill="#3d3d56", outline="", tags="bg"
        )
        self.progress_canvas.tag_lower("bg")
        self._progress_width = 1
        self.progress_canvas.bind("<Configure>", self._on_progress_resize)

        # 阶段时间/文件信息
        self.lbl_stage_info = tk.Label(progress_card, text="", fg=MUTED, bg=CARD, font=FONT_SMALL)
        self.lbl_stage_info.pack(anchor="w", pady=(4, 0))

        # ── 旋转动画 ──
        self.spinner_canvas = tk.Canvas(self.root, width=40, height=40, bg=BG, highlightthickness=0)
        self.spinner_canvas.pack(pady=(6, 0))
        self._spinner_arc = self.spinner_canvas.create_arc(
            6, 6, 34, 34, start=0, extent=90, outline=ACCENT, width=4, style="arc"
        )
        self.spinner_canvas.pack_forget()  # 初始隐藏

        # ── 按钮栏 ──
        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(fill="x", padx=10, pady=6)

        self.btn_pause = tk.Button(btn_frame, text="⏸ 暂停", command=self._pause,
                                    bg=CARD, fg=TEXT, font=FONT, relief="flat",
                                    activebackground="#3d3d56", activeforeground=TEXT,
                                    bd=0, padx=14, pady=6, state="disabled")
        self.btn_pause.pack(side="left", padx=(0, 6))

        self.btn_resume = tk.Button(btn_frame, text="▶ 继续", command=self._resume,
                                     bg=CARD, fg=TEXT, font=FONT, relief="flat",
                                     activebackground="#3d3d56", activeforeground=TEXT,
                                     bd=0, padx=14, pady=6, state="disabled")
        self.btn_resume.pack(side="left", padx=(0, 6))

        self.btn_stop = tk.Button(btn_frame, text="⏹ 停止服务", command=self._stop_server,
                                   bg="#3d2d30", fg=RED, font=FONT, relief="flat",
                                   activebackground="#4d3d40", activeforeground=RED,
                                   bd=0, padx=14, pady=6)
        self.btn_stop.pack(side="right")

        # ── 日志区 ──
        log_label = tk.Label(self.root, text="  📋 运行日志", fg=MUTED, bg=BG, font=FONT,
                              anchor="w", justify="left")
        log_label.pack(fill="x", padx=14, pady=(10, 2))

        self.log_text = scrolledtext.ScrolledText(
            self.root, bg="#1a1a2e", fg=TEXT, font=FONT_MONO,
            insertbackground=TEXT, relief="flat", bd=1,
            highlightthickness=0, wrap="word",
        )
        self.log_text.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.log_text.configure(state="disabled")

        # ── 底部 ──
        footer = tk.Label(self.root, text="手机通过 Tailscale 连接 → 操作实时显示在下方日志", fg=MUTED, bg=BG, font=("Microsoft YaHei UI", 9))
        footer.pack(pady=(0, 8))

    def _on_progress_resize(self, event=None):
        self._progress_width = event.width if event else self.progress_canvas.winfo_width()
        self._draw_progress(self._current_progress)

    def _draw_progress(self, pct):
        """绘制进度条，pct 为 0-100"""
        self._current_progress = max(0, min(100, pct))
        w = self._progress_width
        h = 18
        if w <= 1:
            w = self.progress_canvas.winfo_width()
            self._progress_width = w
        # 背景
        self.progress_canvas.coords("bg", 0, 0, w, h)
        self.progress_canvas.itemconfig("bg", fill="#3d3d56")
        # 进度填充
        fill_w = max(0, w * self._current_progress / 100)
        self.progress_canvas.coords("bar", 0, 0, fill_w, h)
        # 颜色根据进度变化
        if self._current_progress >= 100:
            color = GREEN
        elif self._current_progress >= 60:
            color = CYAN
        elif self._current_progress >= 30:
            color = AMBER
        else:
            color = ACCENT
        self.progress_canvas.itemconfig("bar", fill=color)
        self.progress_canvas.update_idletasks()

    # ── 服务器管理 ──

    def _read_stdout(self):
        """后台线程：持续读取子进程 stdout 并写入日志"""
        while not self._reader_stop and self.server_proc and self.server_proc.poll() is None:
            try:
                line = self.server_proc.stdout.readline()
                if line:
                    self._log(line.rstrip())
                else:
                    break
            except Exception:
                break
        if not self._reader_stop and self.server_proc:
            try:
                remaining = self.server_proc.stdout.read()
                if remaining:
                    for line in remaining.splitlines():
                        self._log(line)
            except Exception:
                pass

    def _start_server(self):
        self._log("═══════════════════════════════════")
        self._log("  启动 web_console.py (端口 5000)...")
        self._log("═══════════════════════════════════")
        python = sys.executable
        script = os.path.join(PROJECT_ROOT, "web_console.py")
        try:
            self.server_proc = subprocess.Popen(
                [python, script, "--port", "5000"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                cwd=PROJECT_ROOT, bufsize=1,
            )
            self._reader_stop = False
            self._reader_thread = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader_thread.start()

            time.sleep(2)
            if self.server_proc.poll() is not None:
                self._log("❌ 服务启动失败！")
                self._set_status("offline", "服务异常")
                return
            self._log("✅ 服务已启动 (PID {})".format(self.server_proc.pid))
        except Exception as e:
            self._log(f"❌ 启动异常: {e}")
            self._set_status("offline", "启动失败")
            return

        self._refresh_ip()
        self._set_status("idle", "就绪")
        self._start_polling()

    def _stop_server(self):
        self._log("⏹ 正在停止服务...")
        self._stop_polling()
        self._reader_stop = True
        try:
            urlopen(Request("http://127.0.0.1:5000/api/stop-server", method="POST"), timeout=2)
        except Exception:
            pass
        if self.server_proc:
            try:
                self.server_proc.terminate()
                self.server_proc.wait(timeout=3)
            except Exception:
                self.server_proc.kill()
        self._log("⏹ 服务已停止")
        self._set_status("offline", "已停止")
        self.btn_stop.configure(text="🚀 重新启动", command=self._start_server, bg="#2d3d30", fg=GREEN, activebackground="#3d4d40", activeforeground=GREEN)

    def _refresh_ip(self):
        try:
            out = subprocess.check_output(["tailscale", "ip", "-4"], text=True, timeout=5).strip()
            ip = out if out else "未知"
        except Exception:
            ip = "未知（Tailscale 未运行？）"
        self.lbl_ip.configure(text=f"📡 Tailscale: {ip}  |  端口: 5000  |  手机: http://{ip}:5000")

    # ── 状态轮询 ──

    def _start_polling(self):
        self._poll()

    def _stop_polling(self):
        if self.poll_timer:
            self.root.after_cancel(self.poll_timer)
            self.poll_timer = None
        self._stop_animations()

    def _poll(self):
        try:
            req = Request("http://127.0.0.1:5000/api/status", method="GET")
            with urlopen(req, timeout=2) as resp:
                data = json.loads(resp.read())
        except Exception:
            self.poll_timer = self.root.after(1500, self._poll)
            return

        running = data.get("running", False)
        paused = data.get("paused", False)
        step = data.get("step", "")
        streamer = data.get("streamer", "")
        folder = data.get("folder", "")
        log_count = data.get("logCount", 0)

        if paused:
            self._set_status("paused", step or "已暂停")
        elif running:
            self._set_status("running", step or "运行中...")
        else:
            self._set_status("idle", "就绪")

        if streamer:
            self.lbl_streamer.configure(text=f"🎬 {streamer} / {folder}")

        # 刷新阶段信息
        self._update_stage_info(step, log_count, running, paused)

        self.poll_timer = self.root.after(1500, self._poll)

    def _update_stage_info(self, step, log_count, running, paused):
        """根据步骤文本更新进度、百分比、ETA等"""
        # 解析步骤数字
        stage_idx, stage_name, stage_count = self._parse_step(step)
        info_text = f""

        if not running and not paused:
            if stage_name == "已完成":
                info_text = "全部步骤已完成"
            else:
                info_text = f"等待开始"
            self._draw_progress(100 if stage_name == "已完成" else 0)
            self._stop_animations()
            return

        # 计算百分比
        if stage_idx >= 0 and stage_count > 0:
            # 步骤区间占整体进度；若步骤内无更细粒度，则取步骤中点
            base = (stage_idx - 1) / (stage_count - 2) * 100 if stage_count > 2 else 0
            pct = min(95, base + 10)  # 当前步骤推进中，留 5% 给收尾
        else:
            pct = 0 if running else 0

        # 尝试从日志/步骤文本中解析 [X/Y] 或百分比
        extra_pct = self._extract_percent_from_step(step)
        if extra_pct is not None:
            pct = extra_pct

        self._draw_progress(pct)
        self.lbl_percent.configure(text=f"{pct:.0f}%")
        self.lbl_stage_info.configure(text=f"阶段 {stage_idx}/{(stage_count - 2) if stage_count > 2 else 0} · {stage_name} · 日志 {log_count} 条")

        # ETA 估算
        if self._step_start_time is None or self._last_step != step:
            self._step_start_time = time.time()
            self._last_step = step
        if pct > 0 and pct < 100:
            elapsed = time.time() - self._step_start_time
            total_est = elapsed / (pct / 100.0)
            remaining = total_est - elapsed
            if remaining > 0:
                self.lbl_eta.configure(text=f"预计剩余: {self._fmt_duration(remaining)}")
            else:
                self.lbl_eta.configure(text="预计剩余: 计算中...")
        else:
            self.lbl_eta.configure(text="预计剩余: --")

        # 启动/停止动画
        if running:
            self._start_spinner()
            self._start_pulse()
        elif paused:
            self._stop_spinner()
            self._start_pulse()
        else:
            self._stop_animations()

    def _parse_step(self, step_text):
        """从步骤文本解析步骤索引和名称"""
        if not step_text:
            return -1, "等待中", len(PIPELINE_STEPS)
        step_text = str(step_text)
        # 匹配“步骤N”
        import re
        m = re.search(r"步骤(\d+)", step_text)
        if m:
            n = int(m.group(1))
            for i, (key, name) in enumerate(PIPELINE_STEPS):
                if key.startswith(f"步骤{n}"):
                    return i, name, len(PIPELINE_STEPS)
            return n, f"步骤{n}", len(PIPELINE_STEPS)
        if "done" in step_text.lower() or "完成" in step_text:
            return len(PIPELINE_STEPS) - 1, "已完成", len(PIPELINE_STEPS)
        if "starting" in step_text.lower() or "启动" in step_text:
            return 0, "启动中", len(PIPELINE_STEPS)
        return -1, step_text, len(PIPELINE_STEPS)

    def _extract_percent_from_step(self, step_text):
        """从步骤文本或日志中解析百分比，如 35% 或 [7/20]"""
        if not step_text:
            return None
        import re
        # 35% / 35.5%
        m = re.search(r"(\d+(?:\.\d+)?)%", step_text)
        if m:
            return float(m.group(1))
        # [7/20]
        m = re.search(r"\[(\d+)\s*/\s*(\d+)\]", step_text)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b > 0:
                return a / b * 100
        return None

    def _fmt_duration(self, seconds):
        if seconds < 60:
            return f"{int(seconds)}秒"
        elif seconds < 3600:
            return f"{int(seconds // 60)}分{int(seconds % 60)}秒"
        else:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h}小时{m}分"

    def _set_status(self, state, text):
        colors = {"idle": MUTED, "running": GREEN, "paused": AMBER, "offline": RED}
        self.status_dot.itemconfig(self._dot, fill=colors.get(state, MUTED))
        self.lbl_step.configure(text=text)

        # 状态徽章
        badge_text = {"idle": "就绪", "running": "运行中", "paused": "已暂停", "offline": "离线"}
        badge_colors = {
            "idle": (AMBER, BG),
            "running": (GREEN, BG),
            "paused": (AMBER, BG),
            "offline": (RED, BG),
        }
        self.lbl_badge.configure(
            text=f" {badge_text.get(state, state)} ",
            bg=badge_colors[state][0],
            fg=badge_colors[state][1]
        )

        if state in ("running",):
            self.btn_pause.configure(state="normal")
            self.btn_resume.configure(state="disabled")
        elif state in ("paused",):
            self.btn_pause.configure(state="disabled")
            self.btn_resume.configure(state="normal")
        else:
            self.btn_pause.configure(state="disabled")
            self.btn_resume.configure(state="disabled")

    # ── 动画 ──

    def _start_spinner(self):
        if self._spinner_timer is not None:
            return
        self.spinner_canvas.pack(pady=(6, 0))
        self._spinner_timer = True  # 占位，避免 _animate_spinner 立即退出
        self._animate_spinner()

    def _animate_spinner(self):
        if self._spinner_timer is None:
            return
        self._spinner_angle = (self._spinner_angle + 15) % 360
        # 绘制旋转的短弧
        x0, y0, x1, y1 = 6, 6, 34, 34
        self.spinner_canvas.itemconfig(
            self._spinner_arc,
            start=self._spinner_angle,
            outline=ACCENT
        )
        self.spinner_canvas.coords(self._spinner_arc, x0, y0, x1, y1)
        self._spinner_timer = self.root.after(60, self._animate_spinner)

    def _stop_spinner(self):
        if self._spinner_timer is not None:
            self.root.after_cancel(self._spinner_timer)
            self._spinner_timer = None
        self.spinner_canvas.pack_forget()

    def _start_pulse(self):
        if self._pulse_timer is not None:
            return
        self._pulse_timer = True
        self._pulse_loop()

    def _pulse_loop(self):
        if self._pulse_timer is None:
            return
        self._pulse_state = (self._pulse_state + 1) % 20
        # 状态点脉冲：半径缩放
        base = 5
        offset = abs(math.sin(self._pulse_state * math.pi / 10)) * 3
        r = base + offset
        cx, cy = 7, 7
        self.status_dot.coords(self._dot, cx - r, cy - r, cx + r, cy + r)
        self._pulse_timer = self.root.after(80, self._pulse_loop)

    def _stop_pulse(self):
        if self._pulse_timer is not None:
            self.root.after_cancel(self._pulse_timer)
            self._pulse_timer = None
        self.status_dot.coords(self._dot, 2, 2, 12, 12)

    def _stop_animations(self):
        self._stop_spinner()
        self._stop_pulse()

    # ── 日志 ──

    def _log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ── 暂停 / 恢复 ──

    def _pause(self):
        try:
            req = Request("http://127.0.0.1:5000/api/pause", method="POST", data=b"")
            urlopen(req, timeout=2)
        except Exception as e:
            self._log(f"暂停失败: {e}")

    def _resume(self):
        try:
            req = Request("http://127.0.0.1:5000/api/resume", method="POST", data=b"")
            urlopen(req, timeout=2)
        except Exception as e:
            self._log(f"恢复失败: {e}")

    def run(self):
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    def _on_close(self):
        self._stop_polling()
        self.root.destroy()


def main():
    gui = ConsoleGUI()
    gui.run()


if __name__ == "__main__":
    main()
