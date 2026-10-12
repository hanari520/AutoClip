# -*- coding: utf-8 -*-
"""
AutoClip Mobile GUI (远程操控版)
PyQt6桌面程序，移动端远程操控优化版 — 按钮/文字更大
"""
import os
import sys

import json
import re
import subprocess
import threading
import time
from collections import deque
from datetime import datetime
from urllib.parse import unquote

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QTextEdit, QPlainTextEdit, QProgressBar,
    QGroupBox, QGridLayout, QSplitter, QFileDialog, QMessageBox, QDialog,
    QCheckBox, QSpinBox, QTabWidget, QFrame, QScrollArea, QSizePolicy
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPropertyAnimation, QEasingCurve, pyqtProperty
from PyQt6.QtGui import QFont, QColor, QPalette, QPixmap
from utils.meme_keywords import merge_keywords, normalize_keywords

# 项目根目录 & 关键路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(PROJECT_ROOT, 'configs', 'gui_config.json')
# B站投稿限频防护：实测单账号约每小时 20 条投稿，超频会被 406 拒绝
# （参考 biliLive-tools 的 minUploadInterval 方案：逐稿最低间隔 + 406 冷却重试 + 连续 406 熔断）
UPLOAD_MIN_INTERVAL_SEC = 240   # 相邻两次投稿的最小间隔（4 分钟 ≈ 15 条/小时）
UPLOAD_406_BACKOFF_SEC = 600    # 命中 406 后的冷却时长
MAX_406_RETRIES = 3             # 连续 406 次数上限，超过则熔断停止本批
WHISPER_MODEL_DIR = os.path.join(PROJECT_ROOT, 'models', 'whisper')

# Qwen3-ASR 本地转写模型（models/ 下目录名）
QWEN3_ASR_MODELS = ('Qwen3-ASR-1.7B', 'Qwen3-ASR-0.6B')
FFMPEG_BIN = os.environ.get('AUTOCLIP_FFMPEG_BIN', '')
if os.path.isdir(FFMPEG_BIN):
    os.environ['PATH'] = FFMPEG_BIN + os.pathsep + os.environ.get('PATH', '')
# 二次启动缓存：字体列表等扫描结果缓存到此文件，避免每次启动都 glob 磁盘
CACHE_FILE = os.path.join(PROJECT_ROOT, 'sessions', '.cache.json')

# 七步流水线步骤定义
PIPELINE_STEPS = [
    (1, '准备', '#95a5a6'),
    (2, '字幕', '#3498db'),
    (3, '高光', '#9b59b6'),
    (4, '切片', '#1abc9c'),
    (5, '烧录', '#f39c12'),
    (6, '封面', '#e74c3c'),
    (7, '投稿', '#e67e22'),
]

# ============================================================
# 进度显示组件
# ============================================================

class StepIndicatorWidget(QWidget):
    """七步流水线进度指示器 - 7个小胶囊"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_step = -1
        self._step_labels = []
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(6)
        layout.setContentsMargins(0, 0, 0, 0)
        
        for step_id, name, color in PIPELINE_STEPS:
            lbl = QLabel(f'{step_id}. {name}')
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setFixedHeight(28)
            lbl.setStyleSheet(f'''
                QLabel {{
                    background: #3d3d56;
                    color: #888;
                    border: 2px solid #555;
                    border-radius: 14px;
                    padding: 2px 10px;
                    font-size: 11px;
                    font-weight: bold;
                }}
            ''')
            self._step_labels.append((lbl, color))
            layout.addWidget(lbl)
    
    def set_active_step(self, step_id):
        """设置当前激活步骤 (1-7)，-1 表示无激活"""
        self._current_step = step_id
        for i, (lbl, color) in enumerate(self._step_labels):
            sid = i + 1
            if sid < step_id:
                lbl.setStyleSheet(f'''
                    QLabel {{
                        background: {color};
                        color: #fff;
                        border: 2px solid {color};
                        border-radius: 14px;
                        padding: 2px 10px;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                ''')
            elif sid == step_id:
                lbl.setStyleSheet(f'''
                    QLabel {{
                        background: {color};
                        color: #fff;
                        border: 3px solid #fff;
                        border-radius: 14px;
                        padding: 2px 10px;
                        font-size: 12px;
                        font-weight: bold;
                    }}
                ''')
            else:
                lbl.setStyleSheet(f'''
                    QLabel {{
                        background: #3d3d56;
                        color: #888;
                        border: 2px solid #555;
                        border-radius: 14px;
                        padding: 2px 10px;
                        font-size: 11px;
                        font-weight: bold;
                    }}
                ''')


class PulseDot(QLabel):
    """脉冲动画点 - 任务运行时显示跳动的圆点"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(14, 14)
        self._opacity = 1.0
        self._anim = QPropertyAnimation(self, b"opacity")
        self._anim.setDuration(800)
        self._anim.setStartValue(0.3)
        self._anim.setEndValue(1.0)
        self._anim.setLoopCount(-1)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._running = False
        self._update_style()
    
    def _get_opacity(self):
        return self._opacity
    
    def _set_opacity(self, v):
        self._opacity = v
        self._update_style()
    
    opacity = pyqtProperty(float, _get_opacity, _set_opacity)
    
    def _update_style(self):
        r, g, b = 72, 219, 251
        self.setStyleSheet(f'''
            QLabel {{
                background: rgba({r}, {g}, {b}, {self._opacity:.1f});
                border-radius: 7px;
                border: none;
            }}
        ''')
    
    def start(self):
        if not self._running:
            self._anim.start()
            self._running = True
    
    def stop(self):
        if self._running:
            self._anim.stop()
            self._opacity = 1.0
            self._update_style()
            self._running = False

# ============================================================
# 工作线程
# ============================================================

class WorkerThread(QThread):
    """后台任务线程"""
    log_signal = pyqtSignal(str, str)  # message, type
    progress_signal = pyqtSignal(dict)  # progress data
    finished_signal = pyqtSignal(bool, str)  # success, message
    
    def __init__(self, task_type, params):
        super().__init__()
        self.task_type = task_type
        self.params = params
        self.process = None
        self.stop_requested = False
        # 上传进度节流时间戳：uploading 事件高频触发（每个分片一次），
        # 500ms 内只 emit 一次，避免信号风暴。关键事件（done/error）不节流。
        self._last_upload_progress_ts = 0.0
        
    def run(self):
        try:
            self.log_signal.emit(f'开始任务: {self.task_type}', 'info')
            
            # upload 任务直接调用 Python 模块，不走子进程
            if self.task_type == 'upload':
                self._run_upload_inline()
                return
            
        # pipeline 一键流转：顺序执行步骤1-6
            if self.task_type == 'pipeline':
                self._run_pipeline()
                return
            
            # batch_upload 批量投稿
            if self.task_type == 'batch_upload':
                self._run_batch_upload_inline()
                return
            
            # 构建命令
            cmd = self.build_command()
            self.log_signal.emit(f'执行: {" ".join(cmd)}', 'info')
            
            # 启动进程（PYTHONUNBUFFERED=1 强制实时输出，避免缓冲导致日志卡死）
            env = os.environ.copy()
            env['PYTHONUNBUFFERED'] = '1'
            env['PYTHONIOENCODING'] = 'utf-8'
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                universal_newlines=True,
                encoding='utf-8',
                errors='ignore',
                env=env
            )
            
            # 读取输出
            for line in self.process.stdout:
                if self.stop_requested:
                    break
                line = line.strip()
                if line:
                    self.log_signal.emit(line, 'info')
                    self.parse_progress(line)
            
            # 等待进程结束
            if self.stop_requested:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except:
                    self.process.kill()
                self.finished_signal.emit(False, '任务已中断')
            else:
                self.process.wait()
                if self.process.returncode == 0:
                    self.finished_signal.emit(True, '任务完成')
                else:
                    self.finished_signal.emit(False, f'进程退出码: {self.process.returncode}')
                    
        except Exception as e:
            self.finished_signal.emit(False, str(e))
    
    def _run_upload_inline(self):
        """B站投稿 — 直接调用 bilibili_uploader，不使用子进程"""
        try:
            from utils.bilibili_uploader import BilibiliUploader
            
            video_path = self.params.get('video_path', '')
            if not video_path or not os.path.exists(video_path):
                self.finished_signal.emit(False, f'视频文件不存在: {video_path}')
                return
            
            # 查找封面 — 优先用 params 指定的，否则在同目录下找封面图
            cover = self.params.get('cover_path') or ''
            if not cover:
                import glob as _glob
                video_dir = os.path.dirname(video_path)
                for ext in ('*.jpg', '*.png', '*.jpeg'):
                    candidates = _glob.glob(os.path.join(video_dir, ext))
                    if candidates:
                        cover = candidates[0]
                        break
            
            def progress_cb(event, pct, data):
                if self.stop_requested:
                    return
                pct_str = f'{pct*100:.1f}%'
                if event == 'start':
                    self.log_signal.emit(f'开始上传: {data.get("file", "")}', 'info')
                elif event == 'preupload':
                    self.log_signal.emit(f'准备上传端点...', 'info')
                elif event == 'uploading':
                    c = data.get('chunk', 0)
                    t = data.get('chunks', 1)
                    # 节流：uploading 每个分片都触发，500ms 内只 emit 一次
                    # 最后一个分片强制 emit，确保进度条到 100%
                    now = time.time()
                    if now - self._last_upload_progress_ts >= 0.5 or c >= t:
                        self._last_upload_progress_ts = now
                        self.log_signal.emit(f'上传中 {pct_str} (分片 {c}/{t})', 'info')
                        self.progress_signal.emit({'completed_files': c, 'total_files': t})
                elif event == 'merging':
                    self.log_signal.emit(f'合并分片中... {pct_str}', 'info')
                elif event == 'cover_upload':
                    self.log_signal.emit(f'上传封面中...', 'info')
                elif event == 'publishing':
                    self.log_signal.emit(f'发布投稿中...', 'info')
                elif event == 'done':
                    bvid = data.get('bvid', '?')
                    aid = data.get('aid', '?')
                    self.log_signal.emit(f'✅ 投稿成功！BV={bvid} AV={aid}', 'success')
                elif event == 'error':
                    self.log_signal.emit(f'投稿出错: {data.get("error")}', 'error')
            
            uploader = BilibiliUploader()
            uploader.on_progress(progress_cb)
            
            result = uploader.upload(
                video_path=video_path,
                title=self.params.get('title', ''),
                desc=self.params.get('desc', ''),
                tags=self.params.get('tags', []),
                tid=self.params.get('tid', 124),
                copyright=self.params.get('copyright', 1),
                source=self.params.get('source') or '',
                cover_path=cover or None,
                no_reprint=self.params.get('no_reprint', 1),
            )
            
            if self.stop_requested:
                self.finished_signal.emit(False, '任务已中断')
            else:
                bvid = result.get('bvid', '?')
                aid = result.get('aid', '?')
                self.finished_signal.emit(True, f'投稿完成 BV={bvid} AV={aid}')
                
        except SystemExit:
            self.finished_signal.emit(False, '上传被取消')
        except Exception as e:
            self.finished_signal.emit(False, f'投稿失败: {e}')
    
    def _run_pipeline(self):
        """一键流转：顺序执行步骤2-6（字幕→高光→切片→烧录→封面）"""
        import re as _re
        from pathlib import Path as _Path

        input_dir = self.params.get('input_dir', '')
        streamer_name = self.params.get('streamer', '')
        config_path = self.params.get('config_path', '')

        if not input_dir or not os.path.isdir(input_dir):
            self.finished_signal.emit(False, f'目录不存在: {input_dir}')
            return

        # 自动检测主播名
        if not streamer_name:
            parts = _Path(input_dir).parts
            for i, p in enumerate(parts):
                if p in ('AA录播', '录播', 'recordings') and i + 1 < len(parts):
                    streamer_name = parts[i + 1]
                    break
            if not streamer_name and len(parts) >= 2:
                streamer_name = parts[-2]

        # 加载主播配置
        import json as _json
        if config_path:
            path = config_path
        else:
            path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer_name}.json')
        cfg = {}
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                cfg = _json.load(f)
            self.log_signal.emit(f'📋 已加载主播配置: {path}', 'info')
        else:
            self.log_signal.emit(f'⚠️ 未找到主播配置 {path}，使用默认参数', 'info')

        # 步骤5字幕烧录只认主播配置 step5_burn.enabled，不接受面板参数覆盖

        python = sys.executable
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        env['PYTHONIOENCODING'] = 'utf-8'

        # ── 步骤定义 ──
        # (step_name, cmd_builder_lambda, checker_func_or_None, step_id)
        # step_id 用于主动 emit progress_signal 让 step_indicator 立即更新
        steps = [
            ('步骤1: 准备录播文件', None, None, 1),
            ('步骤2: 字幕识别', None, None, 2),
            ('步骤3: 高光分析', None, None, 3),
            ('步骤4: 视频切片', None, self._check_step4_clips, 4),
            ('步骤5: 字幕烧录', None, self._check_step5_burn, 5),
            ('步骤6: 封面生成', None, self._check_step6_covers, 6),
        ]

        MAX_RETRIES = 3

        for step_name, _, checker_func, step_id in steps:
            if self.stop_requested:
                self.finished_signal.emit(False, '流水线已中断')
                return

            # 构建当前步骤的命令
            if '步骤1' in step_name:
                commands = self._pipeline_step1(input_dir, cfg, python)
                if not commands:
                    self.log_signal.emit('⏭️ 步骤1: 准备录播文件 已按配置跳过', 'info')
                    continue
            elif '步骤2' in step_name:
                commands = [(step_name, self._pipeline_step2(input_dir, cfg, python))]
            elif '步骤3' in step_name:
                commands = [(step_name, self._pipeline_step3(input_dir, cfg, python, streamer_name))]
            elif '步骤4' in step_name:
                commands = [(step_name, self._pipeline_step4(input_dir, cfg, python))]
            elif '步骤5' in step_name:
                # 字幕烧录为可选项：主播配置 step5_burn.enabled=false 时跳过
                if not cfg.get('step5_burn', {}).get('enabled', True):
                    self.log_signal.emit('⏭️ 步骤5: 字幕烧录 已按配置跳过', 'info')
                    continue
                commands = [(step_name, self._pipeline_step5(input_dir, cfg, python))]
            elif '步骤6' in step_name:
                commands = [(step_name, self._pipeline_step6(input_dir, cfg, python))]
            else:
                continue

            # 检查步骤是否完全完成
            if '步骤1' not in step_name and self._is_step_done(step_name, input_dir):
                self.log_signal.emit(f'⏭️ {step_name} 已完成（全部文件就绪），跳过', 'info')
                continue
            # 注意：_is_step_done 返回 False 时不跳过，正常执行该步骤。
            # generate_subtitles.py 等子脚本本身会跳过已有字幕的视频，
            # 只处理缺失的部分，所以"部分完成"时会自动补齐未处理的文件。

            # 主动 emit step 事件，让 step_indicator 和 stage_label 立即更新
            # （不依赖子进程 stdout 里出现"步骤N"字样才被动解析）
            step_names_map = {1: '准备录播文件', 2: '字幕识别', 3: '高光分析', 4: '视频切片',
                              5: '字幕烧录', 6: '封面生成', 7: '生成投稿清单'}
            self.progress_signal.emit({
                'step': step_id,
                'stage': step_names_map.get(step_id, step_name),
            })

            # 执行 + 验证重试
            for sub_name, cmd in commands:
                ok = self._run_step_with_retry(sub_name, cmd, env, checker_func if len(commands) == 1 else None, input_dir, MAX_RETRIES)
                if not ok:
                    return

        # 生成投稿清单
        self.log_signal.emit(f'\n{"="*50}', 'info')
        self.log_signal.emit('▶ 步骤7: 生成投稿清单', 'info')
        self.log_signal.emit(f'{"="*50}', 'info')
        # 主动 emit step 7，让进度指示器走到最后一步
        self.progress_signal.emit({'step': 7, 'stage': '生成投稿清单'})
        self.generate_manifest_from_files(input_dir, streamer_name)

        self.finished_signal.emit(True, '一键流转完成！点击「📤 批量投稿」筛选并投稿')

    def _run_step_with_retry(self, step_name, cmd, env, checker_func, input_dir, max_retries):
        """执行单个步骤，完成后验证所有文件是否齐全，不全则重试。"""
        for attempt in range(max_retries + 1):
            if self.stop_requested:
                self.finished_signal.emit(False, '流水线已中断')
                return False

            if attempt > 0:
                self.log_signal.emit(f'\n🔄 重新执行 {step_name} (第{attempt+1}次)...', 'info')

            self.log_signal.emit(f'\n{"="*50}', 'info')
            self.log_signal.emit(f'▶ {step_name}', 'info')
            self.log_signal.emit(f'{"="*50}', 'info')
            self.log_signal.emit(f'$ {" ".join(cmd)}', 'info')

            try:
                self.process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    bufsize=1, universal_newlines=True, encoding='utf-8',
                    errors='ignore', env=env, cwd=PROJECT_ROOT
                )
                for line in self.process.stdout:
                    if self.stop_requested:
                        self.process.terminate()
                        try:
                            self.process.wait(timeout=5)
                        except:
                            self.process.kill()
                        self.finished_signal.emit(False, '流水线已中断')
                        return False
                    line = line.strip()
                    if line:
                        self.log_signal.emit(line, 'info')
                        # 解析进度信息（步骤/文件名/百分比/[X/Y]）
                        # 原版只 emit log_signal，从不调用 parse_progress，
                        # 导致 on_progress 收不到 step/current_file/total_files 事件，
                        # step_indicator/stage_label/current_file_label 全部不更新。
                        # 修复：补上 parse_progress 调用，与普通任务路径保持一致。
                        self.parse_progress(line)
                self.process.wait()
                if self.process.returncode != 0:
                    self.finished_signal.emit(False, f'{step_name} 失败 (exit {self.process.returncode})')
                    return False
            except Exception as e:
                self.finished_signal.emit(False, f'{step_name} 异常: {e}')
                return False

            # 验证完成度
            if checker_func is None:
                self.log_signal.emit(f'✅ {step_name} 完成', 'success')
                return True

            missing = checker_func(input_dir)
            if not missing:
                self.log_signal.emit(f'✅ {step_name} 完成 (所有文件已就绪)', 'success')
                return True

            if attempt < max_retries:
                self.log_signal.emit(f'⚠️  仍缺 {len(missing)} 个输出文件', 'info')
            else:
                self.log_signal.emit(f'⚠️  经过 {max_retries+1} 次尝试，仍有 {len(missing)} 个文件未完成:', 'info')
                for m in missing[:5]:
                    self.log_signal.emit(f'    - {m}', 'info')
                return True  # 耗尽重试，继续下一步

        return True

    def _check_step4_clips(self, input_dir):
        """检查步骤4：Data_source.txt 中每个 segment 都有对应的 .mp4。返回缺失列表。"""
        import json as _json
        ds_path = os.path.join(input_dir, 'Data_source.txt')
        if not os.path.exists(ds_path):
            return []
        with open(ds_path, 'r', encoding='utf-8') as f:
            clips = _json.load(f)
        slices_dir = os.path.join(input_dir, '切片', '视频')
        if not os.path.isdir(slices_dir):
            slices_dir = os.path.join(input_dir, '切片')
        if not os.path.isdir(slices_dir):
            return [f'[{c.get("segment","?")}] {c.get("title","?")}' for c in clips]
        existing = set(os.listdir(slices_dir))
        missing = []
        for clip in clips:
            seg = clip.get('segment', '').strip()
            if seg and not any(seg in f and f.endswith('.mp4') for f in existing):
                missing.append(f'[{seg}] {clip.get("title", "?")}')
        return missing

    def _check_step5_burn(self, input_dir):
        """检查步骤5：每个非subbed .mp4 都有对应的 _subbed.mp4。返回缺失列表。"""
        slices_video_dir = os.path.join(input_dir, '切片', '视频')
        if not os.path.isdir(slices_video_dir):
            slices_video_dir = os.path.join(input_dir, '切片')
        if not os.path.isdir(slices_video_dir):
            return []
        from utils.video_filter import filter_danmaku_versions_in_dir
        source_files = filter_danmaku_versions_in_dir(
            slices_video_dir,
            [f for f in os.listdir(slices_video_dir) if f.endswith('.mp4') and not f.endswith('_subbed.mp4')]
        )
        missing = []
        for f in source_files:
            subbed_name = f.replace('.mp4', '_subbed.mp4')
            subbed_path = os.path.join(slices_video_dir, subbed_name)
            if not os.path.exists(subbed_path):
                missing.append(f)
        return missing

    def _check_step6_covers(self, input_dir):
        """检查步骤6：每个切片视频（优先 _subbed.mp4，无则原版）都有对应封面。返回缺失列表。"""
        import re as _re
        slices_video_dir = os.path.join(input_dir, '切片', '视频')
        if not os.path.isdir(slices_video_dir):
            slices_video_dir = os.path.join(input_dir, '切片')
        covers_dir = os.path.join(input_dir, '切片', '封面')
        if not os.path.isdir(slices_video_dir):
            return []

        from utils.video_filter import filter_danmaku_versions_in_dir
        all_videos = [f for f in os.listdir(slices_video_dir) if f.lower().endswith('.mp4')]
        subbed_files = [f for f in all_videos if f.endswith('_subbed.mp4')]
        if subbed_files:
            target = filter_danmaku_versions_in_dir(slices_video_dir, subbed_files)
        else:
            target = filter_danmaku_versions_in_dir(slices_video_dir, all_videos)
        if not target:
            return []
        if not os.path.isdir(covers_dir):
            return list(target)
        existing_covers = set(os.listdir(covers_dir))
        missing = []
        for f in target:
            seg_match = _re.match(r'\[([^\]]+)\]', f)
            seg = seg_match.group(1) if seg_match else os.path.splitext(f)[0].replace('_subbed', '')
            if not any(seg in c and c.lower().endswith(('.jpg', '.png', '.jpeg')) for c in existing_covers):
                missing.append(f)
        return missing

    def _is_step_done(self, step_name, input_dir):
        """检查步骤是否完全完成（所有视频都有对应输出才跳过）。

        修复：原版步骤2只要有一个 .srt 就认为完成，导致 6 个视频只处理 3 个也跳过。
        现在精确对比视频总数 vs 输出文件数，全部完成才跳过。
        """
        if '步骤1' in step_name:
            missing = self._check_step1_prepare(input_dir)
            if missing:
                self.log_signal.emit(f'📊 步骤1完成度: {len(missing)} 个 FLV 需要转换', 'info')
                return False
            return True
        if '步骤2' in step_name:
            # 步骤2：每个 .mp4 都要有对应 .srt
            videos, missing = self._check_step2_subtitles(input_dir)
            if missing:
                self.log_signal.emit(
                    f'📊 步骤2完成度: {len(videos)-len(missing)}/{len(videos)} '
                    f'(缺 {len(missing)} 个字幕: {", ".join(missing[:3])}{"..." if len(missing)>3 else ""})',
                    'info')
                return False
            return len(videos) > 0
        elif '步骤3' in step_name:
            done = os.path.exists(os.path.join(input_dir, 'Data_source.txt'))
            if not done:
                self.log_signal.emit('📊 步骤3完成度: Data_source.txt 未生成', 'info')
            return done
        elif '步骤4' in step_name:
            missing = self._check_step4_clips(input_dir)
            if missing:
                self.log_signal.emit(f'📊 步骤4完成度: 缺 {len(missing)} 个切片', 'info')
                return False
            return True
        elif '步骤5' in step_name:
            missing = self._check_step5_burn(input_dir)
            if missing:
                self.log_signal.emit(f'📊 步骤5完成度: 缺 {len(missing)} 个烧录', 'info')
                return False
            return True
        elif '步骤6' in step_name:
            missing = self._check_step6_covers(input_dir)
            if missing:
                self.log_signal.emit(f'📊 步骤6完成度: 缺 {len(missing)} 个封面', 'info')
                return False
            return True
        return False

    def _check_step1_prepare(self, input_dir):
        """检查步骤1：返回缺少同名 MP4 的 FLV 列表。"""
        missing = []
        if not os.path.isdir(input_dir):
            return missing
        for f in sorted(os.listdir(input_dir)):
            if not f.lower().endswith('.flv'):
                continue
            mp4_path = os.path.join(input_dir, os.path.splitext(f)[0] + '.mp4')
            if not os.path.exists(mp4_path):
                missing.append(f)
        return missing

    def _check_step2_subtitles(self, input_dir):
        """检查步骤2：每个 .mp4 都要有对应 .srt。

        返回 (videos, missing)：
          - videos: 所有视频文件名列表
          - missing: 缺少字幕的视频文件名列表
        """
        video_exts = ('.mp4', '.mkv', '.avi', '.mov', '.wmv')
        videos = []
        missing = []
        if not os.path.isdir(input_dir):
            return videos, missing
        from utils.video_filter import filter_danmaku_versions_in_dir
        candidates = [f for f in sorted(os.listdir(input_dir)) if f.lower().endswith(video_exts)]
        for f in filter_danmaku_versions_in_dir(input_dir, candidates):
            base = os.path.splitext(f)[0]
            srt_path = os.path.join(input_dir, base + '.srt')
            videos.append(f)
            if not os.path.exists(srt_path):
                missing.append(f)
        return videos, missing

    def _pipeline_step1(self, input_dir, cfg, python):
        s1 = cfg.get('step1_prepare', {})
        recursive = s1.get('recursive')
        commands = []

        if s1.get('convert_flv', True):
            cmd = [python, os.path.join('core', 'convert_flv_to_mp4.py'), '--input-dir', input_dir]
            if recursive:
                cmd.append('--recursive')
            if s1.get('overwrite'):
                cmd.append('--overwrite')
            if s1.get('keep_flv_source') or s1.get('keep_source'):
                cmd.append('--keep-source')
            commands.append(('步骤1-1: FLV 转 MP4', cmd))

        if s1.get('portrait_to_landscape'):
            cmd = [python, os.path.join('core', 'convert_portrait_to_landscape.py'), '--input-dir', input_dir]
            if recursive:
                cmd.append('--recursive')
            if s1.get('portrait_overwrite') or s1.get('landscape_overwrite'):
                cmd.append('--overwrite')
            if s1.get('delete_portrait_source'):
                cmd.append('--delete-source')
            if s1.get('landscape_width'):
                cmd.extend(['--width', str(s1.get('landscape_width'))])
            if s1.get('landscape_height'):
                cmd.extend(['--height', str(s1.get('landscape_height'))])
            if s1.get('landscape_blur') is not None:
                cmd.extend(['--blur', str(s1.get('landscape_blur'))])
            commands.append(('步骤1-2: 竖屏转横屏', cmd))

        if s1.get('burn_danmu'):
            cmd = [python, os.path.join('core', 'burn_danmu_video.py'), '--input-dir', input_dir]
            if recursive:
                cmd.append('--recursive')
            if s1.get('burn_danmu_overwrite') or s1.get('overwrite_danmu'):
                cmd.append('--overwrite')
            if s1.get('keep_danmu_ass'):
                cmd.append('--keep-ass')
            # 并行路数：缺省用脚本默认（2 路）；配置里显式写了才覆盖
            if s1.get('burn_danmu_jobs'):
                cmd.extend(['--jobs', str(s1.get('burn_danmu_jobs'))])
            commands.append(('步骤1-3: 压制弹幕', cmd))

        return commands

    def _pipeline_step2(self, input_dir, cfg, python):
        s2 = cfg.get('step2_subtitle', {})
        engine = s2.get('engine', 'funasr')
        cmd = [python, os.path.join('core', 'generate_subtitles.py'), '--input-dir', input_dir,
               '--engine', engine, '--language', s2.get('language', 'zh')]
        if engine in ('funasr', 'portable'):
            cmd.extend(['--funasr-model', s2.get('model', 'SenseVoiceSmall')])
            if s2.get('speaker_diarization'):
                cmd.append('--speaker')
        elif engine == 'qwen3':
            # Qwen3-ASR: models/ 下本地模型，主播配置 model 字段可指定 1.7B/0.6B
            qwen3_model = s2.get('model', 'Qwen3-ASR-1.7B')
            if qwen3_model in QWEN3_ASR_MODELS:
                cmd.extend(['--qwen3-model', qwen3_model])
        elif engine == 'moss':
            # MOSS-Transcribe: models/ 下默认模型目录，无需额外参数
            pass
        else:
            cmd.extend(['--model', s2.get('model', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))])
            if s2.get('auto_lang'):
                cmd.append('--auto-lang')
        return cmd

    def _pipeline_step3(self, input_dir, cfg, python, streamer_name):
        s3 = cfg.get('step3_highlight', {})
        method = s3.get('mode', 'llm')
        cmd = [python, os.path.join('core', 'generate_clips_combined.py'), '--method', method,
               '--input-dir', input_dir, '--streamer', streamer_name]
        if method == 'multi' and s3.get('multi_sources'):
            cmd.extend(['--multi-sources', s3['multi_sources']])
        prompts = s3.get('prompt_dir', '')
        if prompts:
            cmd.extend(['--prompts-dir', prompts])

        # ── 修复 401：传递 API Key 和模型名 ──
        # 原版漏传这两个参数，导致 generate_clips_combined.py 用 DEFAULT_CONFIG 的空 api_key，
        # 请求头 Authorization: Bearer 为空 → 401 Unauthorized。
        # 优先级：GUI 全局配置 > 主播配置 > DEFAULT_CONFIG
        gui_cfg = self._load_gui_config_for_pipeline()
        api_key = gui_cfg.get('global_api_key', '') or cfg.get('llm', {}).get('api_key', '')
        model = gui_cfg.get('global_model', '') or cfg.get('llm', {}).get('model', '')
        if api_key:
            cmd.extend(['--api-key', api_key])
            self.log_signal.emit(f'🔑 已传递 API Key ({"前8位:" + api_key[:8] + "..."})', 'info')
        else:
            self.log_signal.emit('⚠️ 未配置 API Key，LLM 步骤可能返回 401', 'warning')
        if model:
            cmd.extend(['--model', model])
        # LLM provider 切换：透传 base_url，与 CLI 流水线共用同一份 llm_provider 配置
        try:
            from utils.llm_config import resolve_llm_base_url
            cmd.extend(['--base-url', resolve_llm_base_url(gui_config=gui_cfg)])
        except Exception:
            pass
        return cmd

    def _pipeline_step4(self, input_dir, cfg, python):
        s4 = cfg.get('step4_clip', {})
        output = s4.get('output_dir', '{input_dir}/切片')
        output = output.replace('{input_dir}', input_dir)
        return [python, os.path.join('core', 'generate_clips.py'), '--input-dir', input_dir, '--output-dir', output]

    def _pipeline_step5(self, input_dir, cfg, python):
        s5 = cfg.get('step5_burn', {})
        slices_video = os.path.join(input_dir, '切片', '视频')
        if not os.path.exists(slices_video):
            slices_video = os.path.join(input_dir, '切片')
        skip = s5.get('skip_asr', False)
        cmd = [python, os.path.join('core', 'burn_subtitles.py'), '--input-dir', slices_video,
               '--srt-dir', slices_video,
               '--asr-engine', s5.get('engine', 'whisper'),
               '--asr-model', s5.get('model', 'large-v3-turbo'),
               '--asr-language', s5.get('language', 'zh'),
               '--font-size', str(s5.get('font_size', 80)),
               '--primary-color', s5.get('color', '&H00FFFFFF'),
               '--outline-color', s5.get('outline_color', '&H00000000'),
               '--outline-width', str(s5.get('outline', 8)),
               '--margin-v', str(s5.get('margin_v', 100)),
               '--margin-l', str(s5.get('margin_l', 80)),
               '--margin-r', str(s5.get('margin_r', 80)),
               '--max-line-chars', str(s5.get('max_line_chars', 0)),
               '--orientation', s5.get('orientation', 'auto')]
        # 字体: 支持文件路径（--font-file）或家族名（--font-family）
        font_config = s5.get('font', 'MaokenAssortedSans')
        if font_config.lower().endswith(('.ttf', '.otf')):
            cmd.extend(['--font-file', font_config])
        else:
            cmd.extend(['--font-family', font_config])
        if s5.get('shadow', False):
            cmd.append('--shadow')
        shadow_color = s5.get('shadow_color', 'black')
        if s5.get('shadow', False) and shadow_color != 'black':
            cmd.extend(['--shadow-color', shadow_color])
        if skip:
            cmd.append('--skip-asr')
        speed_val = s5.get('speed', 'auto')
        if speed_val and speed_val != 'auto':
            cmd.extend(['--speed', speed_val])
        return cmd

    def _pipeline_step6(self, input_dir, cfg, python):
        s6 = cfg.get('step6_cover', {})
        slices_video = os.path.join(input_dir, '切片', '视频')
        covers_out = os.path.join(input_dir, '切片', '封面')
        style = s6.get('style', 'style1')
        font = s6.get('font', 'MaoKenShiJinHei-2.ttf')
        font_path = self._resolve_cover_font(font)
        use_new_style = s6.get('use_new_style', False)
        cmd = [python, os.path.join('core', 'generate_covers.py'), '--input-dir', input_dir,
               '--video-dir', slices_video, '--match-subbed',
               '--output-dir', covers_out, '--style', style]
        if use_new_style:
            cmd.append('--use-new-style')
        if font_path:
            cmd.extend(['--font', font_path])
        return cmd

    def _resolve_cover_font(self, font_name):
        if not font_name:
            return None
        if os.path.isfile(font_name):
            return font_name
        font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')
        if os.path.isdir(font_dir):
            for f in os.listdir(font_dir):
                if font_name.lower() in f.lower():
                    return os.path.join(font_dir, f)
        return font_name

    def _load_gui_config_for_pipeline(self):
        """一键流转专用：读取 gui_config.json 全局配置

        WorkerThread 在后台线程运行，不能直接访问 GUI 控件（self.global_api_key.text()），
        所以从磁盘配置文件读取。GUI 的 apply_config/save_config 会把控件值写入此文件。
        """
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception:
            pass
        return {}

    def generate_manifest_from_files(self, input_dir, streamer_name):
        """生成投稿清单 upload_manifest.json — 基于文件扫描，不依赖 Data_source.txt"""
        import json as _json, re as _re
        videos_dir = os.path.join(input_dir, '切片', '视频')
        covers_dir = os.path.join(input_dir, '切片', '封面')
        if not os.path.isdir(videos_dir):
            videos_dir = os.path.join(input_dir, '切片')
            covers_dir = os.path.join(input_dir, '切片')

        subbed = []
        used_fallback = False
        if os.path.isdir(videos_dir):
            raw_subbed = []
            raw_plain = []
            for f in sorted(os.listdir(videos_dir)):
                if f.endswith('_subbed.mp4'):
                    raw_subbed.append(f)
                elif f.lower().endswith('.mp4'):
                    raw_plain.append(f)
            from utils.video_filter import filter_danmaku_versions_in_dir
            if raw_subbed:
                subbed = filter_danmaku_versions_in_dir(videos_dir, raw_subbed)
            elif raw_plain:
                subbed = filter_danmaku_versions_in_dir(videos_dir, raw_plain)
                used_fallback = True
        if not subbed:
            self.log_signal.emit(f'❌ 未在 {videos_dir} 找到可投稿的视频（无 _subbed.mp4 或原版切片）', 'error')
            return
        if used_fallback:
            self.log_signal.emit(f'⚠️ 未找到 _subbed.mp4，使用原版切片视频生成投稿清单', 'warning')

        # 加载主播专属配置
        scfg_path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer_name}.json')
        scfg = {}
        if os.path.exists(scfg_path):
            with open(scfg_path, 'r', encoding='utf-8') as f:
                scfg = _json.load(f)
            self.log_signal.emit(f'📋 已加载主播配置: {scfg_path}', 'info')

        s7 = scfg.get('step7_upload', {})
        links = scfg.get('bilibili_links', {})
        space = links.get('space', '')
        live = links.get('live', '')
        tags = s7.get('tags', [])
        tid = s7.get('tid', 217)
        copyright_original = s7.get('copyright') == 'original'
        title_suffix_name = str(s7.get('title_suffix_name') or streamer_name).strip() or streamer_name

        # 加载 Data_source.txt 的简介元数据映射 (sanitized_title → summary/real_timestamp)
        # ── 关键：文件名经过 sanitize_filename 处理（替换 <>:"/\|?* 为 _），
        # 所以匹配键也必须做同样的 sanitize，否则含特殊字符的标题永远匹配不上。
        _invalid_chars = '<>:"/\\|?*'
        title_meta = {}
        ds_path = os.path.join(input_dir, 'Data_source.txt')
        if os.path.exists(ds_path):
            with open(ds_path, 'r', encoding='utf-8') as _fds:
                for clip in _json.load(_fds):
                    t = clip.get('title', '').strip()
                    if t:
                        key = t
                        for ch in _invalid_chars:
                            key = key.replace(ch, '_')
                        key = key[:100]
                        title_meta[key] = {
                            'title': t,
                            'summary': clip.get('summary', '').strip(),
                            'real_timestamp': clip.get('real_timestamp', '').strip(),
                        }

        old_state = {}
        manifest_path = os.path.join(input_dir, 'upload_manifest.json')
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    for old in _json.load(f):
                        old_state[old.get('video_path', '')] = {
                            k: old.get(k) for k in (
                                'upload_status', 'upload_attempts', 'bvid', 'aid',
                                'last_error', 'last_upload_at', 'last_attempt_at'
                            )
                        }
            except Exception:
                old_state = {}

        upload_list = []
        for video_file in subbed:
            # 兼容两种命名：[seg] title.mp4 或 [seg] title_subbed.mp4
            m = _re.match(r'^\[([^\]]+)\]\s*(.+?)(?:_subbed)?\.mp4$', video_file)
            if not m:
                self.log_signal.emit(f'⚠️ 跳过无法解析的文件名: {video_file}', 'warning')
                continue
            seg = m.group(1)
            title_raw = m.group(2)
            # 投稿标题正文优先用 Data_source.txt 原始标题（文件名里的 ? : 等已被清洗成 _）
            original_title = title_meta.get(title_raw, {}).get('title', '')
            title = f'【{title_suffix_name}】{original_title or title_raw}'

            # 匹配封面：按完整文件名前缀精确匹配（同 segment 下可能有多个切片）
            # 封面文件名不带 _subbed 后缀，原版与字幕版指向同一封面
            cover = None
            if os.path.isdir(covers_dir):
                cover_base = video_file[:-len('_subbed.mp4')] if video_file.endswith('_subbed.mp4') else video_file[:-len('.mp4')]
                for ext in ('.jpg', '.png', '.jpeg'):
                    candidate = os.path.join(covers_dir, cover_base + ext)
                    if os.path.exists(candidate):
                        cover = candidate
                        break
                # 回退：按 segment ID 匹配（兼容旧命名 / 封面标题与视频标题不一致的情况）
                if not cover:
                    for cf in sorted(os.listdir(covers_dir)):
                        if cf.startswith(f'[{seg}]') and cf.lower().endswith(('.jpg', '.png', '.jpeg')):
                            cover = os.path.join(covers_dir, cf)
                            break

            desc_parts = []
            if space:
                desc_parts.append(f'{streamer_name}的个人主页：{space}')
            if live:
                desc_parts.append(f'{streamer_name}的直播间：{live}')
            # 从 Data_source.txt 匹配 summary / real_timestamp（按 title 精确匹配）
            meta = title_meta.get(title_raw, {})
            real_timestamp = meta.get('real_timestamp', '')
            if real_timestamp:
                desc_parts.append('')
                desc_parts.append(f'直播时间：{real_timestamp}')
            summary = meta.get('summary', '')
            if summary:
                desc_parts.append('')
                desc_parts.append(summary)

            video_path = os.path.join(videos_dir, video_file)
            entry = {
                'video_path': video_path,
                'title': title,
                'desc': '\n'.join(desc_parts),
                'tags': tags,
                'tid': tid,
                'copyright': 1 if copyright_original else 2,
                'cover_path': cover,
                'upload_status': 'pending',
                'upload_attempts': 0,
                'bvid': '',
                'aid': '',
                'last_error': '',
                'last_upload_at': '',
                'last_attempt_at': '',
            }
            entry.update({k: v for k, v in old_state.get(video_path, {}).items() if v not in (None, '')})
            upload_list.append(entry)
            self.log_signal.emit(f'  📎 [{seg}] {title}', 'info')

        with open(manifest_path, 'w', encoding='utf-8') as f:
            _json.dump(upload_list, f, ensure_ascii=False, indent=2)
        covered = sum(1 for u in upload_list if u['cover_path'])
        self.log_signal.emit(f'📄 投稿清单: {manifest_path} ({len(upload_list)} 个待投稿 / {covered} 有封面)', 'success')

    def _run_batch_upload_inline(self):
        """批量投稿：遍历 upload_manifest.json 中选中的项目"""
        from utils.bilibili_uploader import BilibiliUploader
        selected = self.params.get('items', [])
        if not selected:
            self.finished_signal.emit(False, '没有选择任何投稿项目')
            return
        manifest_path = self.params.get('manifest_path', '')

        def _now():
            return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        def _save_manifest_item(video_path, updates):
            if not manifest_path or not os.path.exists(manifest_path):
                return
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    items = json.load(f)
                changed = False
                for m in items:
                    if os.path.normcase(m.get('video_path', '')) == os.path.normcase(video_path):
                        m.update(updates)
                        changed = True
                        break
                if changed:
                    with open(manifest_path, 'w', encoding='utf-8') as f:
                        json.dump(items, f, ensure_ascii=False, indent=2)
            except Exception as e:
                self.log_signal.emit(f'  ⚠️ 投稿状态写回失败: {e}', 'warning')

        def _sleep_with_stop(seconds):
            for _ in range(seconds):
                if self.stop_requested:
                    break
                time.sleep(1)

        success = 0
        fail = 0
        skipped = 0
        failed_items = []
        consecutive_406 = 0
        i = 0
        while i < len(selected):
            if self.stop_requested:
                break
            item = selected[i]
            _406_hit = False
            title = item.get('title', '未知标题')
            video_path = item.get('video_path', '')
            self.log_signal.emit(f'\n--- [{i+1}/{len(selected)}] {title} ---', 'info')
            if item.get('upload_status') == 'success' and item.get('bvid'):
                self.log_signal.emit(f'  🔁 已投稿过 BV={item.get("bvid")}，按本次选择重新投稿', 'warning')
            attempts = int(item.get('upload_attempts', 0) or 0) + 1
            item['upload_attempts'] = attempts
            _save_manifest_item(video_path, {
                'upload_status': 'uploading',
                'upload_attempts': attempts,
                'last_attempt_at': _now(),
                'last_error': '',
            })
            try:
                if not os.path.isfile(video_path):
                    raise FileNotFoundError(f'视频文件不存在: {video_path}')
                uploader = BilibiliUploader()
                def cb(evt, pct, data):
                    if evt == 'uploading':
                        c = data.get('chunk', 0)
                        t = data.get('chunks', 1)
                        # 节流：500ms 内只 emit 一次 uploading 进度
                        now = time.time()
                        if now - self._last_upload_progress_ts >= 0.5 or c >= t:
                            self._last_upload_progress_ts = now
                            self.log_signal.emit(f'  上传中 {pct*100:.0f}% (分片{c}/{t})', 'info')
                    elif evt == 'done':
                        self.log_signal.emit(f'  ✅ BV={data.get("bvid","?")}', 'success')
                    elif evt == 'error':
                        error_msg = data.get("error","")
                        self.log_signal.emit(f'  ❌ {error_msg}', 'error')
                        if '406' in error_msg:
                            nonlocal _406_hit
                            _406_hit = True
                uploader.on_progress(cb)
                result = uploader.upload(
                    video_path=video_path,
                    title=title,
                    desc=item.get('desc', ''),
                    tags=item.get('tags', []),
                    tid=item.get('tid', 124),
                    copyright=item.get('copyright', 1),
                    cover_path=item.get('cover_path') or None,
                    no_reprint=item.get('no_reprint', 1),
                )
                bvid = result.get('bvid', '?')
                aid = result.get('aid', '')
                if bvid and bvid != '?':
                    success += 1
                    item['upload_status'] = 'success'
                    item['bvid'] = bvid
                    _save_manifest_item(video_path, {
                        'upload_status': 'success',
                        'bvid': bvid,
                        'aid': aid,
                        'last_error': '',
                        'last_upload_at': _now(),
                    })
                elif _406_hit:
                    pass  # 406 后重试，不计入失败
                else:
                    err = f'上传接口未返回有效 BV 号: {result}'
                    fail += 1
                    failed_items.append(f'  ❌ {title} ({err})\n     视频: {video_path}')
                    _save_manifest_item(video_path, {
                        'upload_status': 'failed',
                        'last_error': err,
                    })
            except Exception as e:
                err_str = str(e)
                self.log_signal.emit(f'  ❌ 异常: {e}', 'error')
                if _406_hit or '406' in err_str:
                    _406_hit = True
                else:
                    fail += 1
                    failed_items.append(f'  ❌ {title} (异常: {err_str[:100]})\n     视频: {video_path}')
                    _save_manifest_item(video_path, {
                        'upload_status': 'failed',
                        'last_error': err_str[:500],
                    })
            # 406 错误 → 冷却后重试当前稿件
            if _406_hit:
                consecutive_406 += 1
                if consecutive_406 > MAX_406_RETRIES:
                    err = (f'B站限频(406)：冷却重试 {MAX_406_RETRIES} 次仍被拒，'
                           f'停止本批投稿，未投稿件保持待投稿状态，可稍后重试')
                    self.log_signal.emit(f'  🛑 {err}', 'error')
                    fail += 1
                    failed_items.append(f'  ❌ {title} ({err})\n     视频: {video_path}')
                    _save_manifest_item(video_path, {
                        'upload_status': 'failed',
                        'last_error': err,
                    })
                    break
                self.log_signal.emit(
                    f'  🕐 检测到 406（B站限频，约每小时20条），'
                    f'冷却 {UPLOAD_406_BACKOFF_SEC // 60} 分钟后重试当前稿件'
                    f'（第 {consecutive_406}/{MAX_406_RETRIES} 次）...', 'warning')
                remaining_count = len(selected) - i
                if remaining_count > 0:
                    self.log_signal.emit(f'  📋 还剩 {remaining_count} 个待投稿:', 'warning')
                    for j in range(i, len(selected)):
                        self.log_signal.emit(f'     [{j+1}] {selected[j].get("title", "未知")}', 'info')
                _sleep_with_stop(UPLOAD_406_BACKOFF_SEC)
                continue  # 不前进 i，重试同一个稿件
            consecutive_406 = 0
            # 投稿间隔控制：逐稿最低间隔，避免触发 B站限频（约 20 条/小时）
            i += 1
            if i < len(selected) and not self.stop_requested:
                self.log_signal.emit(f'  ⏳ 等待 {UPLOAD_MIN_INTERVAL_SEC // 60} 分钟后投下一个（防限频）...', 'info')
                _sleep_with_stop(UPLOAD_MIN_INTERVAL_SEC)
        # 中断时列出未投稿项
        if self.stop_requested:
            remaining = selected[i:]
            if remaining:
                for rem in remaining:
                    _save_manifest_item(rem.get('video_path', ''), {
                        'upload_status': 'skipped',
                        'last_error': '用户中断，尚未投稿',
                    })
                self.log_signal.emit(f'\n{"="*50}', 'warning')
                self.log_signal.emit(f'🛑 任务已中断，还剩 {len(remaining)} 个未投稿:', 'warning')
                for j, rem in enumerate(remaining):
                    self.log_signal.emit(f'   [{i+j+1}] {rem.get("title", "未知")}', 'info')
                self.log_signal.emit(f'{"="*50}\n', 'warning')
        # 打印失败稿件汇总
        summary = f'批量投稿完成 ✅{success}/❌{fail}/⏭{skipped}'
        if failed_items:
            self.log_signal.emit(f'\n{"="*50}', 'error')
            self.log_signal.emit(f'📋 以下 {len(failed_items)} 个稿件投稿失败，请手动重试:', 'error')
            for fi in failed_items:
                self.log_signal.emit(fi, 'error')
            self.log_signal.emit(f'{"="*50}\n', 'error')
            summary += f'\n失败稿件已在上方列出，共 {len(failed_items)} 个'
        self.finished_signal.emit(fail == 0, summary)

    def build_command(self):
        """构建命令"""
        python = sys.executable
        
        if self.task_type == 'convert_flv':
            cmd = [
                python, os.path.join('core', 'convert_flv_to_mp4.py'),
                '--input-dir', self.params.get('input_dir', ''),
            ]
            if self.params.get('recursive'):
                cmd.append('--recursive')
            if self.params.get('overwrite'):
                cmd.append('--overwrite')

        elif self.task_type == 'portrait_landscape':
            cmd = [
                python, os.path.join('core', 'convert_portrait_to_landscape.py'),
                '--input-dir', self.params.get('input_dir', ''),
            ]
            if self.params.get('recursive'):
                cmd.append('--recursive')
            if self.params.get('overwrite'):
                cmd.append('--overwrite')
            if self.params.get('delete_source'):
                cmd.append('--delete-source')
            cmd.extend(['--width', str(self.params.get('width', 1920))])
            cmd.extend(['--height', str(self.params.get('height', 1080))])
            cmd.extend(['--blur', str(self.params.get('blur', 20))])

        elif self.task_type == 'burn_danmu':
            cmd = [
                python, os.path.join('core', 'burn_danmu_video.py'),
                '--input-dir', self.params.get('input_dir', ''),
            ]
            if self.params.get('recursive'):
                cmd.append('--recursive')
            if self.params.get('overwrite'):
                cmd.append('--overwrite')
            if self.params.get('keep_ass'):
                cmd.append('--keep-ass')
            # 并行路数：缺省用脚本默认（2 路）
            if self.params.get('jobs'):
                cmd.extend(['--jobs', str(self.params.get('jobs'))])

        elif self.task_type == 'subtitles':
            engine = self.params.get('engine', 'portable')
            cmd = [
                python, os.path.join('core', 'generate_subtitles.py'),
                '--input-dir', self.params.get('input_dir', ''),
                '--engine', engine,
                '--language', self.params.get('language', 'zh')
            ]
            
            if engine in ('portable', 'funasr'):
                cmd.extend(['--funasr-model', self.params.get('funasr_model', 'sensevoice')])
                if self.params.get('speaker'):
                    cmd.append('--speaker')
            elif engine == 'qwen3':
                cmd.extend(['--qwen3-model', self.params.get('qwen3_model', 'Qwen3-ASR-1.7B')])
            else:
                cmd.extend(['--model', self.params.get('model', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))])
                if self.params.get('auto_lang'):
                    cmd.append('--auto-lang')
        
        elif self.task_type == 'llm':
            cmd = [
                python, os.path.join('core', 'generate_clips_combined.py'),
                '--method', self.params.get('method', 'combined'),
                '--input-dir', self.params.get('input_dir', ''),
                '--streamer', self.params.get('streamer', '')
            ]
            if self.params.get('api_key'):
                cmd.extend(['--api-key', self.params['api_key']])
            if self.params.get('model'):
                cmd.extend(['--model', self.params['model']])
            if self.params.get('prompts_dir'):
                cmd.extend(['--prompts-dir', self.params['prompts_dir']])
            if self.params.get('method') == 'multi' and self.params.get('multi_sources'):
                cmd.extend(['--multi-sources', self.params['multi_sources']])
                
        elif self.task_type == 'clips':
            cmd = [
                python, os.path.join('core', 'generate_clips.py'),
                '--input-dir', self.params.get('input_dir', '')
            ]
            if self.params.get('output_dir'):
                cmd.extend(['--output-dir', self.params['output_dir']])
                
        elif self.task_type == 'covers':
            cmd = [
                python, os.path.join('core', 'generate_covers.py'),
                '--input-dir', self.params.get('input_dir', ''),
                '--style', self.params.get('style', 'style1')
            ]
            if self.params.get('output_dir'):
                cmd.extend(['--output-dir', self.params['output_dir']])
            if self.params.get('font'):
                cmd.extend(['--font', self.params['font']])
        elif self.task_type == 'subtitles_burn':
            cmd = [
                python, os.path.join('core', 'burn_subtitles.py'),
                '--input-dir', self.params.get('input_dir', ''),
                '--srt-dir', self.params.get('srt_dir') or self.params.get('input_dir', ''),
                '--asr-engine', self.params.get('asr_engine', 'whisper'),
                '--asr-model', self.params.get('asr_model', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt')),
                '--asr-language', self.params.get('asr_language', 'zh'),
                '--font-family', self.params.get('font_family', 'MaokenAssortedSans'),
                '--font-size', str(self.params.get('font_size', 80)),
                '--primary-color', self.params.get('primary_color', '&H00FFFFFF'),
                '--outline-color', self.params.get('outline_color', '&H00000000'),
                '--outline-width', str(self.params.get('outline_width', 8)),
                '--margin-v', str(self.params.get('margin_v', 100)),
                '--margin-l', str(self.params.get('margin_l', 80)),
                '--margin-r', str(self.params.get('margin_r', 80)),
                '--max-line-chars', str(self.params.get('max_line_chars', 0)),
                '--orientation', self.params.get('orientation', 'auto'),
            ]
            if self.params.get('shadow', True):
                cmd.append('--shadow')
            shadow_color = self.params.get('shadow_color', 'black')
            if self.params.get('shadow', True) and shadow_color != 'black':
                cmd.extend(['--shadow-color', shadow_color])
            if self.params.get('skip_asr'):
                cmd.append('--skip-asr')
            speed_val = self.params.get('speed', 'auto')
            if speed_val and speed_val != 'auto':
                cmd.extend(['--speed', speed_val])
        else:
            cmd = [python, '--version']
            
        return cmd
    
    def parse_progress(self, line):
        """解析进度信息 - 增强版：支持多种格式

        覆盖以下子进程输出格式：
        - auto_pipeline: "找到 N 个视频文件" / "▶ 步骤N" / "[i/N] filename"
        - FunASR worker: "[Worker] Transcribing: video.mp4" / "Input: video.mp4"
        - FFmpeg: "frame= 1234 fps= 60" / "Press [q] to stop"
        - 投稿: "上传中 45.0% (分片 3/10)"
        """
        import re
        data = {}

        # 1. 文件总数
        if '找到' in line and ('个视频文件' in line or '个文件' in line):
            match = re.search(r'(\d+)\s*个(?:视频)?文件', line)
            if match:
                data['total_files'] = int(match.group(1))

        # 2. 当前处理文件 — 多种格式
        # 2a. 原有: "开始识别字幕: xxx" / "Processing: xxx"
        if '开始识别字幕:' in line or '识别字幕:' in line or 'Processing:' in line:
            data['current_file'] = line.split(':')[-1].strip()
        # 2b. FunASR worker: "[Worker] Transcribing: xxx" / "[Worker] Input: xxx"
        elif re.search(r'\[Worker\]\s+(?:Transcribing|Input|Stage)', line):
            # 取冒号后部分，再取文件名（可能带路径，兼容 / 和 \）
            after_colon = line.split(':', 1)[-1].strip() if ':' in line else ''
            if after_colon:
                # 同时处理 Windows (\) 和 Unix (/) 路径分隔符
                data['current_file'] = re.split(r'[\\/]', after_colon)[-1]
        # 2c. auto_pipeline: "[i/N] filename.mp4" 形式的切片进度
        elif re.search(r'\[\d+\s*/\s*\d+\]\s*\S+\.(?:mp4|mkv|avi)', line):
            match = re.search(r'\[\d+\s*/\s*\d+\]\s*(\S+\.(?:mp4|mkv|avi))', line)
            if match:
                data['current_file'] = os.path.basename(match.group(1))
        # 2d. "▶ 步骤N: xxx" → 更新阶段（和下面的步骤识别互补）
        elif line.startswith('▶ 步骤') or line.startswith('$ '):
            # 命令行输出，提取正在处理的脚本名作为线索
            if 'generate_subtitles' in line:
                data['current_file'] = '字幕识别中...'
            elif 'generate_clips' in line:
                data['current_file'] = '高光分析中...'
            elif 'burn_subtitles' in line:
                data['current_file'] = '字幕烧录中...'
            elif 'generate_covers' in line:
                data['current_file'] = '封面生成中...'

        # 3. 方括号进度 [X/Y] 或 (X/Y)
        if '[' in line and ']' in line:
            match = re.search(r'\[(\d+)\s*/\s*(\d+)\]', line)
            if match:
                data['completed_files'] = int(match.group(1))
                data['total_files'] = int(match.group(2))
        
        # 4. 圆括号进度 (X/Y)
        if '(' in line and ')' in line:
            match = re.search(r'\((\d+)\s*/\s*(\d+)\)', line)
            if match and 'total_files' not in data:
                data['completed_files'] = int(match.group(1))
                data['total_files'] = int(match.group(2))
        
        # 5. 百分比 x% 或 x.x%
        pct_match = re.search(r'(\d+(?:\.\d+)?)\s*%', line)
        if pct_match:
            pct = float(pct_match.group(1))
            data['percent'] = pct
            if 'total_files' not in data:
                data['total_files'] = 100
            data['completed_files'] = int(pct)
        
        # 6. 字节/文件大小进度 (上传场景)
        size_match = re.search(r'(\d+(?:\.\d+)?)\s*(MB|GB|KB|mb|gb|kb)\s*/\s*(\d+(?:\.\d+)?)\s*(MB|GB|KB|mb|gb|kb)', line)
        if size_match:
            data['bytes_info'] = f'{size_match.group(1)}{size_match.group(2)} / {size_match.group(3)}{size_match.group(4)}'
        
        # 7. 步骤识别 - pipeline 各阶段
        if not any(k in data for k in ('completed_files', 'total_files', 'current_file')):
            if '步骤2' in line or '字幕' in line:
                data['step'] = 2
                data['stage'] = '字幕识别'
            elif '步骤3' in line or '高光' in line:
                data['step'] = 3
                data['stage'] = '高光分析'
            elif '步骤4' in line or '切片' in line:
                data['step'] = 4
                data['stage'] = '视频切片'
            elif '步骤5' in line or '烧录' in line:
                data['step'] = 5
                data['stage'] = '字幕烧录'
            elif '步骤6' in line or '封面' in line:
                data['step'] = 6
                data['stage'] = '封面生成'
            elif '步骤7' in line or '投稿清单' in line or '投稿' in line:
                data['step'] = 7
                data['stage'] = '生成投稿清单'
        
        if data:
            self.progress_signal.emit(data)
    
    def stop(self):
        """停止任务"""
        self.stop_requested = True
        if self.process:
            self.process.terminate()

    def stop_and_wait(self, timeout_ms=5000):
        """请求任务停止，并短暂等待子进程退出，方便启动窗口一起关闭。"""
        self.stop()
        if self.isRunning() and not self.wait(timeout_ms):
            if self.process and self.process.poll() is None:
                self.process.kill()
            self.wait(1000)


class MemeSearchThread(QThread):
    """Background Bilibili meme search + LLM analysis."""

    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool, dict, str)

    def __init__(self, streamer, months, max_results, llm_config):
        super().__init__()
        self.streamer = streamer
        self.months = months
        self.max_results = max_results
        self.llm_config = llm_config

    def run(self):
        try:
            import asyncio
            from utils.bilibili_meme_search import search_videos, analyze_titles

            self.log_signal.emit(
                f'搜索B站关于「{self.streamer}」的视频（近{self.months}个月，最多{self.max_results}条）...'
            )
            titles = asyncio.run(search_videos(
                keyword=self.streamer,
                months=self.months,
                max_results=self.max_results,
            ))
            if not titles:
                self.finished_signal.emit(False, {}, '未搜到相关视频')
                return

            self.log_signal.emit(f'找到 {len(titles)} 条视频标题，正在进行 LLM 梗分析...')
            result = analyze_titles(
                titles=titles[:min(len(titles), 40)],
                streamer=self.streamer,
                llm_config=self.llm_config,
                progress_cb=self.log_signal.emit,
            )
            result['keywords'] = normalize_keywords(result.get('keywords', []))
            self.finished_signal.emit(True, result, '')
        except Exception as e:
            self.finished_signal.emit(False, {}, str(e))

class BatchUploadDialog(QDialog):
    """批量投稿筛选窗口"""
    def __init__(self, parent, input_dir, config):
        super().__init__(parent)
        self.setWindowTitle('📤 批量投稿 - 筛选确认')
        self.setMinimumSize(780, 560)
        self.parent_gui = parent
        self.input_dir = input_dir
        self.config = config

        self._items = []  # 所有投稿项
        self._checkboxes = []  # 复选框列表

        self._setup_ui()
        self._load_manifest()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        # 标题
        title = QLabel(f'📦 投稿清单 — {os.path.basename(self.input_dir)}')
        title.setStyleSheet('font-size: 15px; font-weight: bold; color: #feca57;')
        layout.addWidget(title)

        # 工具栏
        toolbar = QHBoxLayout()
        btn_all = QPushButton('☑ 全选')
        btn_all.clicked.connect(lambda: self._toggle_all(True))
        btn_none = QPushButton('☐ 反选')
        btn_none.clicked.connect(self._invert_selection)
        self._count_label = QLabel('0 / 0 个已选')
        self._count_label.setStyleSheet('color: #48dbfb; font-weight: bold;')
        toolbar.addWidget(btn_all)
        toolbar.addWidget(btn_none)
        toolbar.addStretch()
        toolbar.addWidget(self._count_label)
        layout.addLayout(toolbar)

        # 滚动列表
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet('QScrollArea { border: 1px solid #4a4a5a; border-radius: 8px; background: #2b2b3c; }')
        self._list_widget = QWidget()
        self._list_layout = QVBoxLayout(self._list_widget)
        self._list_layout.setSpacing(8)
        self._list_layout.setContentsMargins(8, 8, 8, 8)
        self._list_layout.addStretch()
        scroll.setWidget(self._list_widget)
        layout.addWidget(scroll, 1)

        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_upload = QPushButton('🚀 批量投稿')
        btn_upload.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b6b, stop:1 #feca57);
                color: white; border: none; padding: 12px 36px;
                border-radius: 8px; font-size: 15px; font-weight: bold;
            }
            QPushButton:hover { background: #ee5a6f; }
        ''')
        btn_upload.clicked.connect(self._do_batch_upload)
        btn_layout.addWidget(btn_upload)
        layout.addLayout(btn_layout)

    def _load_manifest(self):
        """加载 upload_manifest.json 并渲染列表"""
        manifest_path = os.path.join(self.input_dir, 'upload_manifest.json')
        if not os.path.exists(manifest_path):
            label = QLabel('⚠️ 未找到投稿清单，请先运行「一键流转」生成')
            label.setStyleSheet('color: #feca57; font-size: 14px; padding: 20px;')
            self._list_layout.insertWidget(0, label)
            return

        import json as _json
        with open(manifest_path, 'r', encoding='utf-8') as f:
            self._items = _json.load(f)
        self._normalize_title_suffixes(manifest_path)

        for i, item in enumerate(self._items):
            row = self._build_item_row(i, item)
            self._list_layout.insertWidget(self._list_layout.count() - 1, row)

        self._update_count()

    def _normalize_title_suffixes(self, manifest_path):
        """按主播配置修正已存在清单中的标题前缀。"""
        streamer_name = os.path.basename(os.path.dirname(self.input_dir))
        cfg_path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer_name}.json')
        suffix = streamer_name
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                s7 = cfg.get('step7_upload', {})
                suffix = str(s7.get('title_suffix_name') or cfg.get('streamer_name') or streamer_name).strip() or streamer_name
            except Exception:
                suffix = streamer_name

        changed = False
        for item in self._items:
            title = item.get('title', '')
            if not title:
                continue
            base_title = re.sub(r'^【[^】]*】', '', title)
            base_title = re.sub(r'【[^】]*】$', '', base_title)
            new_title = f'【{suffix}】{base_title}'
            if new_title != title:
                item['title'] = new_title
                changed = True
        if changed:
            with open(manifest_path, 'w', encoding='utf-8') as f:
                json.dump(self._items, f, ensure_ascii=False, indent=2)

    def _build_item_row(self, idx, item):
        row = QWidget()
        row.setStyleSheet('''
            QWidget { background: #35354a; border-radius: 8px; padding: 4px; }
            QWidget:hover { background: #3d3d56; }
        ''')
        row_layout = QHBoxLayout(row)
        row_layout.setSpacing(10)

        status = item.get('upload_status', 'pending')
        cb = QCheckBox()
        cb.setChecked(status != 'success')
        # 已投稿项默认不选中，避免误重复投稿；但允许手动选择后再次投稿。
        cb.setEnabled(True)
        self._checkboxes.append(cb)
        cb.stateChanged.connect(self._update_count)
        row_layout.addWidget(cb)

        # 封面缩略图
        cover = item.get('cover_path', '')
        if cover and os.path.exists(cover):
            lbl = QLabel()
            pix = QPixmap(cover).scaled(80, 50, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            lbl.setPixmap(pix)
            lbl.setFixedSize(80, 50)
            row_layout.addWidget(lbl)
        else:
            lbl = QLabel('无封面')
            lbl.setStyleSheet('color: #8a8aa0; font-size: 11px;')
            lbl.setFixedSize(80, 50)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row_layout.addWidget(lbl)

        # 信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(8)
        t = item.get('title', '无标题')
        if len(t) > 50:
            t = t[:47] + '...'
        title_lbl = QLabel(t)
        title_lbl.setStyleSheet('color: #eee; font-weight: bold; font-size: 14px;')
        info_layout.addWidget(title_lbl)

        video = os.path.basename(item.get('video_path', ''))
        tags = ', '.join(item.get('tags', [])[:4])
        sub_lbl = QLabel(f'{video}  |  分区: {item.get("tid", "?")}  |  {tags}')
        sub_lbl.setStyleSheet('color: #a0a0b8; font-size: 11px;')
        info_layout.addWidget(sub_lbl)

        status_text = {
            'pending': '待投稿',
            'uploading': '投稿中',
            'success': '已投稿',
            'failed': '投稿失败',
            'skipped': '未投稿/已中断',
        }.get(status, status or '待投稿')
        status_color = {
            'pending': '#feca57',
            'uploading': '#48dbfb',
            'success': '#1dd1a1',
            'failed': '#ff6b6b',
            'skipped': '#a0a0b8',
        }.get(status, '#a0a0b8')
        attempts = int(item.get('upload_attempts', 0) or 0)
        bvid = item.get('bvid') or ''
        last_error = item.get('last_error') or ''
        detail = f'状态: {status_text}  |  投稿次数: {attempts}'
        if bvid:
            detail += f'  |  BV: {bvid}'
        if last_error and status in ('failed', 'skipped'):
            detail += f'  |  {last_error[:80]}'
        state_lbl = QLabel(detail)
        state_lbl.setStyleSheet(f'color: {status_color}; font-size: 11px; font-weight: bold;')
        state_lbl.setWordWrap(True)
        info_layout.addWidget(state_lbl)
        row_layout.addLayout(info_layout, 1)

        return row

    def _toggle_all(self, checked):
        for cb in self._checkboxes:
            if cb.isEnabled():
                cb.setChecked(checked)

    def _invert_selection(self):
        for cb in self._checkboxes:
            cb.setChecked(not cb.isChecked())

    def _update_count(self):
        sel = sum(1 for cb in self._checkboxes if cb.isChecked())
        total = len(self._checkboxes)
        self._count_label.setText(f'{sel} / {total} 个已选')

    def _do_batch_upload(self):
        selected = [self._items[i] for i, cb in enumerate(self._checkboxes) if cb.isChecked()]
        if not selected:
            QMessageBox.warning(self, '提示', '请至少选择一个视频投稿')
            return
        reply = QMessageBox.question(
            self, '确认投稿',
            f'确认要投稿 {len(selected)} 个视频吗？\n\n投稿后将无法撤回。',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self.accept()
        self.parent_gui.start_task('batch_upload', {
            'items': selected,
            'manifest_path': os.path.join(self.input_dir, 'upload_manifest.json'),
        })

class AutoClipGUI(QMainWindow):
    """主窗口"""
    
    WINDOW_TITLE = 'AutoClip Mobile'
    SHOW_MAXIMIZED = True
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle(self.WINDOW_TITLE)
        self.setMinimumSize(1000, 680)

        self.worker = None
        self.task_start_time = None

        # 进度增强相关
        self._progress_history = deque(maxlen=20)
        self._current_pipeline_step = -1
        self._fonts_loaded = False
        # 紧凑信息行的数据源（替代原 info_grid 的 4 个独立 label）
        self._progress_completed = 0
        self._progress_total = 0
        self._eta_text = ''

        # ── 日志批量刷新（解决高频 stdout 导致的 UI 卡顿）──
        # WorkerThread 每行 emit 一次 log_signal，这里不直接渲染，
        # 而是入队 deque，由 flush 定时器 50ms 批量 append 到 QPlainTextEdit。
        # 实测 ASR/FFmpeg 高速输出时，渲染次数从 ~100/s 降到 ~20/s。
        self._log_queue = deque()
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setInterval(50)  # 50ms 批量刷新 → 20fps
        self._log_flush_timer.timeout.connect(self._flush_log_queue)

        # ── 进度条样式缓存（避免每次 on_progress 都重解析 QSS）──
        # _update_progress_bar_color 原来每次都 setStyleSheet 重新解析整段 QSS，
        # 改为只在跨档（0-30 / 30-70 / 70-100）时才切换。
        self._progress_color_tier = -1  # 0/1/2 三档，-1 表示未初始化

        # 加载配置（读 JSON 很快，同步执行无妨）
        self.config = self.load_config()
        self._step_groups = {}
        self._ui_initialized = False  # 防止 showEvent 重复触发 apply_config

        self.init_ui()
        # apply_config 推迟到首次 showEvent 后执行
        # 原版在 __init__ 同步执行 apply_config + apply_global_config，
        # 大量 setText/setCurrentIndex 阻塞了窗口显示。
        # 改为窗口 show() 后用 QTimer(0) 立即填充，用户感知"窗口秒开"。
        # 注意：不要在这里调 apply_config，否则失去延迟意义。

        # 定时器更新进度时间
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_elapsed_time)

    def showEvent(self, event):
        """窗口首次显示时异步填充配置，避免阻塞启动"""
        super().showEvent(event)
        if not self._ui_initialized:
            self._ui_initialized = True
            # singleShot(0) 在当前事件循环迭代结束后立即执行
            # 此时窗口已显示，用户看到的是"空窗口 → 瞬间填充数据"
            QTimer.singleShot(0, self._apply_config_async)

    def _apply_config_async(self):
        """异步应用配置 + 加载字体列表（启动后立即执行）"""
        self.apply_config()
        # 字体加载不再用 singleShot(200)，直接在配置应用后执行
        # （此时 UI 已稳定，扫描 assets/font 目录不会阻塞感知）
        self.load_font_list()
        
    def init_ui(self):
        """初始化UI — 方案A: 顶部全局配置条 + 左右双栏(左进度日志 / 右Tab步骤)"""
        central = QWidget()
        self.setCentralWidget(central)

        # 顶部全局配置条（原 nav_bar 位置，改为紧凑的全局配置 + 一键流转按钮）
        global_bar = self._create_global_bar()

        # 底部：左侧面板 + 右侧 Tab 步骤区
        main_layout = QHBoxLayout()
        main_layout.setSpacing(16)

        right_content = self.create_right_content()  # 现在返回 QTabWidget
        left_panel = self.create_left_panel()

        main_layout.addWidget(left_panel, 1)
        main_layout.addWidget(right_content, 2)

        # 整体垂直布局：全局配置条在上，内容在下
        layout = QVBoxLayout(central)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 12, 16, 16)
        layout.addWidget(global_bar)
        layout.addLayout(main_layout, 1)

    def _create_global_bar(self):
        """创建顶部全局配置条 — 替代原 _create_nav_bar

        把全局配置（输入目录/主播/API/模型）压缩到一行，加上一键流转和批量投稿按钮。
        原来这些控件散落在右侧滚动区的第一个 QGroupBox，现在提到顶部固定，
        右侧 Tab 区只放步骤1-7，滚动距离从 7 屏降到 1 屏。
        """
        bar = QWidget()
        bar.setStyleSheet('''
            QWidget#globalBar {
                background: #35354a;
                border: 1px solid #4a4a5e;
                border-radius: 10px;
            }
        ''')
        bar.setObjectName('globalBar')
        layout = QHBoxLayout(bar)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 10, 14, 10)

        # 输入目录
        layout.addWidget(QLabel('📁'))
        self.global_input_dir = QLineEdit()
        self.global_input_dir.setPlaceholderText('选择录播目录')
        self.global_input_dir.setMinimumWidth(200)
        layout.addWidget(self.global_input_dir, 2)
        btn_browse = QPushButton('浏览')
        btn_browse.setStyleSheet(self._btn_ghost_style())
        btn_browse.clicked.connect(lambda: self.browse_dir(self.global_input_dir))
        layout.addWidget(btn_browse)

        # 主播
        layout.addWidget(QLabel('🎤'))
        self.global_streamer = QComboBox()
        self.global_streamer.setEditable(True)
        self.global_streamer.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.global_streamer.addItems(['栞栞Shiori', '灰泽满Hazel', '岁己SUI'])
        self.global_streamer.setMinimumWidth(120)
        layout.addWidget(self.global_streamer)

        meme_btn = QPushButton('梗搜索')
        meme_btn.setStyleSheet(self._btn_small_blue())
        meme_btn.clicked.connect(self.open_meme_search_dialog)
        layout.addWidget(meme_btn)

        # 应用按钮
        apply_btn = QPushButton('⬇ 应用到所有步骤')
        apply_btn.setStyleSheet(self._btn_ghost_style())
        apply_btn.clicked.connect(self.apply_global_config)
        layout.addWidget(apply_btn)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet('color: #4a4a5e;')
        layout.addWidget(sep)

        # 一键流转
        pipeline_btn = QPushButton('🚀 一键流转')
        pipeline_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00b894, stop:1 #00cec9);
                color: white; border: none; padding: 10px 24px;
                border-radius: 8px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #00a381; }
        ''')
        pipeline_btn.clicked.connect(self.run_pipeline)
        layout.addWidget(pipeline_btn)

        # 批量投稿
        batch_btn = QPushButton('📤 批量投稿')
        batch_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b6b, stop:1 #feca57);
                color: white; border: none; padding: 10px 24px;
                border-radius: 8px; font-weight: bold; font-size: 14px;
            }
            QPushButton:hover { background: #ee5a6f; }
        ''')
        batch_btn.clicked.connect(self.open_batch_upload)
        layout.addWidget(batch_btn)

        # 分隔
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet('color: #4a4a5e;')
        layout.addWidget(sep2)

        # B站登录状态 + 按钮
        self.global_bili_login_label = QLabel('🔒 未登录')
        self.global_bili_login_label.setStyleSheet('color: #feca57; font-weight: bold; font-size: 12px;')
        layout.addWidget(self.global_bili_login_label)

        qr_btn = QPushButton('📱 扫码')
        qr_btn.setStyleSheet(self._btn_small_blue())
        qr_btn.clicked.connect(self.start_qr_login)
        layout.addWidget(qr_btn)

        cookie_btn = QPushButton('🔑 Cookie')
        cookie_btn.setStyleSheet(self._btn_small_red())
        cookie_btn.clicked.connect(self.show_cookie_dialog)
        layout.addWidget(cookie_btn)

        refresh_btn = QPushButton('🔄')
        refresh_btn.setStyleSheet(self._btn_small_ghost())
        refresh_btn.clicked.connect(self.refresh_bili_login_status)
        layout.addWidget(refresh_btn)

        # API Key 和模型放到右侧（可折叠，避免顶部太挤）
        # 用一个展开按钮控制
        self._api_toggle_btn = QPushButton('⚙️ 高级')
        self._api_toggle_btn.setCheckable(True)
        self._api_toggle_btn.setStyleSheet(self._btn_small_ghost())
        self._api_toggle_btn.toggled.connect(self._toggle_api_panel)
        layout.addWidget(self._api_toggle_btn)

        # API/模型面板（默认隐藏，点击"高级"展开）
        self._api_panel = QWidget()
        api_layout = QHBoxLayout(self._api_panel)
        api_layout.setSpacing(8)
        api_layout.setContentsMargins(0, 0, 0, 0)
        api_layout.addWidget(QLabel('API Key:'))
        self.global_api_key = QLineEdit()
        self.global_api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.global_api_key.setPlaceholderText('sk-...')
        self.global_api_key.setMinimumWidth(180)
        api_layout.addWidget(self.global_api_key, 1)
        api_layout.addWidget(QLabel('供应商:'))
        self.llm_provider = QComboBox()
        self.llm_provider.addItem('硅基流动 (SiliconFlow)', 'siliconflow')
        self.llm_provider.addItem('DeepSeek 官方', 'deepseek')
        self.llm_provider.addItem('OpenCode Go (Zen)', 'opencode-go')
        self.llm_provider.addItem('智谱 (Zhipu)', 'zhipu')
        self.llm_provider.addItem('智谱 Coding (编码套餐)', 'zhipu-coding')
        # 分 provider 的 key 缓存：切换时先存旧、再载新（apply_config 会据 gui_config 覆盖）
        self._llm_keys = {'siliconflow': '', 'deepseek': '', 'opencode-go': '', 'zhipu': '', 'zhipu-coding': ''}
        self._last_provider = 'siliconflow'
        self.llm_provider.currentIndexChanged.connect(self._on_provider_changed)
        api_layout.addWidget(self.llm_provider)
        api_layout.addWidget(QLabel('模型:'))
        self.global_model = QComboBox()
        self.global_model.setEditable(True)
        self.global_model.setMinimumWidth(180)
        self.global_model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.global_model.addItem('deepseek-ai/DeepSeek-V4-Flash')
        self.global_model.setCurrentText('deepseek-ai/DeepSeek-V4-Flash')
        api_layout.addWidget(self.global_model, 1)
        self._api_panel.setVisible(False)
        layout.addWidget(self._api_panel, 2)

        layout.addStretch()
        return bar

    def _toggle_api_panel(self, checked):
        """切换 API Key/模型 面板显隐"""
        self._api_panel.setVisible(checked)
        self._api_toggle_btn.setText('⚙️ 高级 ▼' if checked else '⚙️ 高级')

    def _on_provider_changed(self, idx):
        """切换 LLM 供应商时联动 key 与模型：
        - key：先把当前输入框值存回「旧 provider」，再载入「新 provider」已保存的 key。
        - 模型：硬切换到新 provider 的默认模型，并重建模型下拉候选列表
          （siliconflow -> deepseek-ai/DeepSeek-V4-Flash；deepseek -> deepseek-v4-pro / deepseek-v4-flash）。
        """
        provider = self.llm_provider.itemData(idx) if idx >= 0 else None
        if not provider:
            return
        # 1) 保存旧 provider 的 key，载入新 provider 的 key
        old = getattr(self, '_last_provider', None)
        if old and old != provider and hasattr(self, '_llm_keys'):
            self._llm_keys[old] = self.global_api_key.text().strip()
        self._last_provider = provider
        if hasattr(self, '_llm_keys'):
            self.global_api_key.setText(self._llm_keys.get(provider, ''))
        # 2) 重建模型候选列表并硬切换到默认模型
        try:
            from utils.llm_config import LLM_PROVIDER_MODELS, LLM_PROVIDER_MODEL_OPTIONS
        except Exception:
            return
        options = LLM_PROVIDER_MODEL_OPTIONS.get(provider, [])
        default_model = LLM_PROVIDER_MODELS.get(provider, '')
        self.global_model.blockSignals(True)
        try:
            self.global_model.clear()
            if options:
                self.global_model.addItems(options)
            self.global_model.setCurrentText(default_model)
        finally:
            self.global_model.blockSignals(False)

    def open_meme_search_dialog(self):
        """打开主播梗搜索窗口，结果直接合并进 streamer keywords。"""
        streamer = self.global_streamer.currentText().strip()
        if not streamer:
            QMessageBox.warning(self, '提示', '请先选择或填写主播名称')
            return

        dlg = QDialog(self)
        dlg.setWindowTitle(f'梗搜索 - {streamer}')
        dlg.setMinimumSize(620, 460)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)

        form = QGridLayout()
        form.addWidget(QLabel('主播:'), 0, 0)
        streamer_label = QLabel(streamer)
        streamer_label.setStyleSheet('font-weight: bold;')
        form.addWidget(streamer_label, 0, 1, 1, 3)

        form.addWidget(QLabel('搜索月份:'), 1, 0)
        months_spin = QSpinBox()
        months_spin.setRange(1, 12)
        months_spin.setValue(3)
        form.addWidget(months_spin, 1, 1)

        form.addWidget(QLabel('最多标题:'), 1, 2)
        max_spin = QSpinBox()
        max_spin.setRange(10, 100)
        max_spin.setSingleStep(10)
        max_spin.setValue(50)
        form.addWidget(max_spin, 1, 3)
        layout.addLayout(form)

        log_box = QTextEdit()
        log_box.setReadOnly(True)
        log_box.setPlaceholderText('点击开始后会搜索 B 站标题并调用 LLM 分析梗词')
        layout.addWidget(log_box, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        start_btn = QPushButton('开始搜索并合并 keywords')
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(start_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        def append_log(text):
            log_box.append(text)
            self.log(text, 'info')

        def start_search():
            api_key = self.global_api_key.text().strip()
            if not api_key:
                QMessageBox.warning(dlg, '提示', '请先在顶部「高级」里填写 LLM API Key')
                return
            start_btn.setEnabled(False)
            log_box.clear()
            try:
                from utils.llm_config import resolve_llm_base_url, resolve_default_model
                _base_url = resolve_llm_base_url()
                _default_model = resolve_default_model()
            except Exception:
                _base_url = 'https://api.siliconflow.cn/v1'
                _default_model = 'deepseek-ai/DeepSeek-V4-Flash'
            llm_config = {
                'api_key': api_key,
                'base_url': _base_url,
                'model': self.global_model.currentText().strip() or _default_model,
            }
            self._meme_search_thread = MemeSearchThread(
                streamer=streamer,
                months=months_spin.value(),
                max_results=max_spin.value(),
                llm_config=llm_config,
            )
            self._meme_search_thread.log_signal.connect(append_log)
            self._meme_search_thread.finished_signal.connect(
                lambda ok, result, err: self._on_meme_search_finished(
                    streamer, ok, result, err, log_box, start_btn
                )
            )
            self._meme_search_thread.start()

        start_btn.clicked.connect(start_search)
        self._meme_search_dialog = dlg
        dlg.show()

    def _on_meme_search_finished(self, streamer, ok, result, error, log_box, start_btn):
        start_btn.setEnabled(True)
        if not ok:
            msg = f'梗搜索失败: {error}'
            log_box.append(msg)
            self.log(msg, 'error')
            return

        keywords = normalize_keywords(result.get('keywords', []))
        summary = result.get('summary', '')
        if summary:
            log_box.append(f'\n梗分析摘要:\n{summary}')
        log_box.append(f'\n推荐关键词 ({len(keywords)}):')
        for kw in keywords:
            log_box.append(f'  - {kw}')

        cfg_path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer}.json')
        cfg = {'streamer_name': streamer, 'version': 1}
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
            except Exception:
                cfg = {'streamer_name': streamer, 'version': 1}
        cfg.pop('_meme_result', None)
        before = len(normalize_keywords(cfg.get('keywords', [])))
        cfg['keywords'] = merge_keywords(cfg.get('keywords', []), keywords)
        added = len(cfg['keywords']) - before
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        msg = f'已合并 {added} 个新关键词到 {cfg_path}'
        log_box.append(f'\n{msg}')
        self.log(msg, 'success')

    @staticmethod
    def _btn_ghost_style():
        return '''QPushButton { background: #4a4a5e; color: #ccc; border: none;
            padding: 6px 12px; border-radius: 6px; font-size: 12px; }
            QPushButton:hover { background: #5a5a6e; }'''

    @staticmethod
    def _btn_small_blue():
        return '''QPushButton { background: #6c78fa; color: white; border: none;
            padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: #5b6bf0; }'''

    @staticmethod
    def _btn_small_red():
        return '''QPushButton { background: #ff6b6b; color: white; border: none;
            padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: #ee5a6f; }'''

    @staticmethod
    def _btn_small_ghost():
        return '''QPushButton { background: #4a4a5e; color: #ccc; border: none;
            padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; }
            QPushButton:hover { background: #5a5a6e; }'''
        
    def create_left_panel(self):
        """创建左侧面板 — 精简版

        原版有 8 个元素垂直堆叠（胶囊+大标签+状态+脉冲点+脉冲标签+进度条+文件名+4行网格+按钮），
        其中 stage_label/status_label/pulse_label/task_name_label 四个都在显示"当前在干嘛"，冗余。
        精简后保留 5 个核心元素：步骤胶囊 + 当前阶段(1行) + 进度条 + 紧凑信息(1行) + 中断按钮。
        """
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(10)
        layout.setContentsMargins(0, 0, 0, 0)

        # ── 进度面板（精简）──
        progress_group = QGroupBox('实时进度')
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setSpacing(8)

        # 1. 步骤指示器（7 步骤小胶囊 — 保留，视觉里程碑）
        self.step_indicator = StepIndicatorWidget()
        progress_layout.addWidget(self.step_indicator)

        # 2. 当前阶段（合并原 stage_label + status_label + pulse_label）
        #    原来三个标签都在说"现在在干嘛"，合并成一行
        self.stage_label = QLabel('● 准备就绪')
        self.stage_label.setStyleSheet(
            'font-size: 14px; font-weight: bold; color: #feca57; padding: 2px 0;'
        )
        progress_layout.addWidget(self.stage_label)

        # 3. 进度条（核心 — 保留）
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setStyleSheet('''
            QProgressBar {
                border: none;
                border-radius: 8px;
                background: #3d3d56;
                height: 28px;
                text-align: center;
                font-weight: bold;
            }
            QProgressBar::chunk {
                border-radius: 8px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #ff6b6b, stop:0.5 #feca57, stop:1 #1dd1a1);
            }
        ''')
        progress_layout.addWidget(self.progress_bar)

        # 4. 紧凑信息行（合并原 info_grid 4 行 → 1 行）
        #    原来有 任务/进度/耗时/预计剩余 四行，其中任务和进度都和上面的标签/进度条重复。
        #    合并为："3/5 · 耗时 2分15秒 · 剩 1分"
        self.compact_info_label = QLabel('— · — · —')
        self.compact_info_label.setStyleSheet(
            'color: #a0a0b8; font-size: 12px; padding: 2px 0;'
        )
        progress_layout.addWidget(self.compact_info_label)

        # 5. 当前文件（保留，去掉花哨背景）
        self.current_file_label = QLabel('当前文件: -')
        self.current_file_label.setStyleSheet(
            'color: #48dbfb; font-size: 12px; padding: 4px 0;'
        )
        progress_layout.addWidget(self.current_file_label)

        # 6. 中断按钮（保留）
        self.stop_btn = QPushButton('⏹ 中断任务')
        self.stop_btn.setStyleSheet('''
            QPushButton {
                background: #ff6b6b;
                color: white;
                border: none;
                padding: 16px;
                font-size: 15px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background: #ee5a6f; }
            QPushButton:disabled { background: #5a5a6e; }
        ''')
        self.stop_btn.clicked.connect(self.stop_task)
        self.stop_btn.setVisible(False)
        progress_layout.addWidget(self.stop_btn)

        layout.addWidget(progress_group)

        # ── 日志面板（保持不变）──
        log_group = QGroupBox('执行日志')
        log_layout = QVBoxLayout(log_group)
        
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMinimumHeight(120)
        # 限制日志最大块数，防止长时间运行后累积上万行越来越慢
        # QPlainTextEdit 超过上限会自动丢弃最旧的块，滚动条不受影响
        self.log_text.setMaximumBlockCount(2000)
        self.log_text.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.log_text.setStyleSheet('''
            QPlainTextEdit {
                background: #35354a;
                color: #aaa;
                border: 1px solid #4a4a5a;
                border-radius: 8px;
                padding: 10px;
                font-family: Consolas, monospace;
                font-size: 12px;
            }
        ''')
        log_layout.addWidget(self.log_text)
        
        clear_btn = QPushButton('清空日志')
        clear_btn.clicked.connect(self.clear_log)
        log_layout.addWidget(clear_btn)
        
        layout.addWidget(log_group, 1)
        
        return panel

    def _create_step_container(self, title, description):
        """创建统一的步骤容器 — 替代原 QGroupBox

        在 Tab 模式下 QGroupBox 的边框是多余的（Tab 已提供容器感）。
        改为无边框 QWidget + 统一的标题栏 + 步骤说明，确保 7 个 Tab 视觉一致。

        返回 (container, content_widget)：
          - container: 顶层 QWidget，传给 addTab
          - content_widget: 调用方对其设置 QGridLayout/QVBoxLayout 放具体控件

        Args:
            title: 步骤标题，如 "🎙 步骤2: 字幕识别"
            description: 一句话说明这个步骤做什么
        """
        container = QWidget()
        container.setObjectName('stepContainer')
        outer = QVBoxLayout(container)
        outer.setSpacing(8)
        outer.setContentsMargins(16, 12, 16, 16)

        # 统一标题栏（带下划线，面包屑风格）
        header = QLabel(title)
        header.setStyleSheet('''
            QLabel {
                font-size: 15px;
                font-weight: bold;
                color: #6c78fa;
                padding: 0 0 4px 0;
                border-bottom: 2px solid #6c78fa;
            }
        ''')
        outer.addWidget(header)

        # 步骤说明
        desc = QLabel(description)
        desc.setWordWrap(True)
        desc.setStyleSheet('''
            QLabel {
                color: #a0a0b8;
                font-size: 12px;
                padding: 4px 0 8px 0;
            }
        ''')
        outer.addWidget(desc)

        # 内容区 — 调用方对这个 widget 设置 layout
        content = QWidget()
        outer.addWidget(content, 1)
        return container, content
    
    def create_right_content(self):
        """创建右侧内容区 — 方案A: QTabWidget 分页，每个步骤一个 Tab

        原版用 QScrollArea 把全局配置 + 步骤1-7 垂直堆叠，滚动距离 7 屏。
        现在改为 QTabWidget，全局配置提到顶部 _create_global_bar，
        步骤1-7 各占一个 Tab，互斥显示，零滚动。
        """
        # ── QTabWidget 替代 QScrollArea ──
        self.step_tabs = QTabWidget()
        self.step_tabs.setStyleSheet('''
            QTabWidget::pane {
                border: 1px solid #4a4a5e;
                border-radius: 8px;
                background: #2b2b3c;
            }
            QTabBar::tab {
                background: #35354a;
                color: #a0a0b8;
                border: 1px solid #4a4a5e;
                border-bottom: none;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                padding: 8px 14px;
                margin-right: 2px;
                font-size: 12px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background: #6c78fa;
                color: white;
                border-color: #6c78fa;
            }
            QTabBar::tab:hover:!selected {
                background: #45455e;
                color: #e0e0f0;
            }
        ''')

        # 步骤构建函数会把 group 赋值到 self._step_groups[idx]
        # 并 addTab 到 self.step_tabs
        # （下面的步骤1-7构建代码保持不变，只把 layout.addWidget(group) 改为 addTab）

        # 步骤1: 准备文件
        step1_group, step1_content = self._create_step_container('📁 步骤1: 准备录播文件',
            '选择包含录播视频的目录；一键流转会自动将 FLV 用 ffmpeg -c copy 转换为同名 MP4。')
        self._step_groups[1] = step1_group
        step1_layout = QVBoxLayout(step1_content)
        step1_layout.setSpacing(12)
        step1_layout.setContentsMargins(0, 0, 0, 0)
        
        step1_dir_layout = QHBoxLayout()
        self.step1_input_dir = QLineEdit()
        self.step1_input_dir.setPlaceholderText('选择包含录播文件的目录')
        step1_dir_layout.addWidget(self.step1_input_dir)
        btn_browse_step1 = QPushButton('浏览...')
        btn_browse_step1.clicked.connect(lambda: self.browse_dir(self.step1_input_dir))
        step1_dir_layout.addWidget(btn_browse_step1)
        step1_layout.addLayout(step1_dir_layout)
        
        convert_btn = QPushButton('转换 FLV 为 MP4（copy）')
        convert_btn.clicked.connect(self.run_convert_flv)
        step1_layout.addWidget(convert_btn)

        portrait_btn = QPushButton('竖屏转横屏 1080p')
        portrait_btn.setToolTip('独立可选功能：只处理竖屏 MP4，横屏自动跳过，输出横屏背景模糊版')
        portrait_btn.clicked.connect(self.run_portrait_landscape)
        step1_layout.addWidget(portrait_btn)

        burn_danmu_btn = QPushButton('压制同名 MP4 + XML 为弹幕版')
        burn_danmu_btn.setToolTip('独立可选功能：匹配同名 MP4/XML，输出“原名-弹幕版.mp4”')
        burn_danmu_btn.clicked.connect(self.run_burn_danmu)
        step1_layout.addWidget(burn_danmu_btn)

        refresh_btn = QPushButton('刷新文件列表')
        refresh_btn.clicked.connect(self.refresh_file_list)
        step1_layout.addWidget(refresh_btn)
        
        self.file_list_text = QTextEdit()
        self.file_list_text.setReadOnly(True)
        self.file_list_text.setMaximumHeight(150)
        self.file_list_text.setPlaceholderText('点击"刷新文件列表"查看目录内容')
        step1_layout.addWidget(self.file_list_text)
        
        self.step_tabs.addTab(step1_group, '📁 1.准备')
        
        # 步骤2: 字幕识别
        step2_group, step2_content = self._create_step_container('🎙 步骤2: 字幕识别',
            '将视频音频转写为 SRT 字幕文件。可选 FunASR（GPU 便携版）、Whisper、Belle 或 Qwen3-ASR。')
        self._step_groups[2] = step2_group
        step2_layout = QGridLayout(step2_content)
        step2_layout.setSpacing(12)
        step2_layout.setContentsMargins(0, 0, 0, 0)
        step2_layout.setSpacing(10)
        
        step2_layout.addWidget(QLabel('输入目录:'), 0, 0)
        self.whisper_input_dir = QLineEdit()
        step2_layout.addWidget(self.whisper_input_dir, 0, 1)
        btn_browse_whisper = QPushButton('浏览...')
        btn_browse_whisper.clicked.connect(lambda: self.browse_dir(self.whisper_input_dir))
        step2_layout.addWidget(btn_browse_whisper, 0, 2)
        
        step2_layout.addWidget(QLabel('引擎:'), 1, 0)
        self.asr_engine = QComboBox()
        self.asr_engine.addItem('FunASR (GPU便携版)', 'funasr')
        self.asr_engine.addItem('Whisper (本地通用)', 'whisper')
        self.asr_engine.addItem('Belle (中文优化)', 'belle')
        self.asr_engine.addItem('Qwen3-ASR (Qwen官方本地)', 'qwen3')
        self.asr_engine.addItem('MOSS-Transcribe (转写+说话人分离)', 'moss')
        self.asr_engine.currentTextChanged.connect(self.on_engine_changed)
        step2_layout.addWidget(self.asr_engine, 1, 1)

        # FunASR 模型下拉
        step2_layout.addWidget(QLabel('模型:'), 2, 0)
        self.funasr_model = QComboBox()
        self.funasr_model.addItem('SenseVoiceSmall (多语言+情绪+事件)', 'sensevoice')
        self.funasr_model.addItem('Paraformer-zh (中文专用)', 'paraformer-zh')
        self.funasr_model.addItem('Paraformer-en (英文专用)', 'paraformer-en')
        step2_layout.addWidget(self.funasr_model, 2, 1)

        # Whisper 模型下拉（初始隐藏）
        self.whisper_model_label = QLabel('模型:')
        step2_layout.addWidget(self.whisper_model_label, 2, 0)
        self.whisper_model = QComboBox()
        self.whisper_model.addItem('tiny (72MB - 最快)', os.path.join(WHISPER_MODEL_DIR, 'tiny.pt'))
        self.whisper_model.addItem('base (138MB - 轻量)', os.path.join(WHISPER_MODEL_DIR, 'base.pt'))
        self.whisper_model.addItem('medium (1.4GB - 均衡)', os.path.join(WHISPER_MODEL_DIR, 'medium.pt'))
        self.whisper_model.addItem('large-v3-turbo (1.5GB - 推荐)', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))
        # BELLE-2 中文优化版（HuggingFace transformers 格式，目录而非 .pt）
        belle_dir = os.path.join(WHISPER_MODEL_DIR, 'belle-whisper-large-v3-turbo-zh')
        if os.path.isdir(belle_dir):
            self.whisper_model.addItem('Belle-whisper-zh (1.5GB - 中文优化)', belle_dir)
        self.whisper_model.setCurrentIndex(3)  # 默认 large-v3-turbo
        step2_layout.addWidget(self.whisper_model, 2, 1)
        self.whisper_model_label.hide()
        self.whisper_model.hide()

        # Qwen3 模型下拉（初始隐藏）
        self.qwen3_model_label = QLabel('模型:')
        step2_layout.addWidget(self.qwen3_model_label, 2, 0)
        self.qwen3_model = QComboBox()
        self.qwen3_model.addItem('Qwen3-ASR-1.7B (默认 - 质量优先)', 'Qwen3-ASR-1.7B')
        self.qwen3_model.addItem('Qwen3-ASR-0.6B (轻量 - 更快)', 'Qwen3-ASR-0.6B')
        step2_layout.addWidget(self.qwen3_model, 2, 1)
        self.qwen3_model_label.hide()
        self.qwen3_model.hide()

        step2_layout.addWidget(QLabel('语言:'), 3, 0)
        self.whisper_lang = QComboBox()
        self.whisper_lang.addItems(['zh', 'en', 'ja'])
        step2_layout.addWidget(self.whisper_lang, 3, 1)

        self.whisper_auto_lang = QCheckBox('自动检测语言')
        step2_layout.addWidget(self.whisper_auto_lang, 3, 2)
        self.whisper_auto_lang.hide()

        # FunASR 选项（初始隐藏）
        self.funasr_speaker = QCheckBox('启用说话人分离')
        step2_layout.addWidget(self.funasr_speaker, 4, 1)
        
        whisper_btn = QPushButton('执行字幕识别')
        whisper_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b6b, stop:1 #feca57);
                color: white;
                border: none;
                padding: 14px 24px; font-size: 14px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { opacity: 0.9; }
        ''')
        whisper_btn.clicked.connect(self.run_whisper)
        step2_layout.addWidget(whisper_btn, 6, 0, 1, 3)
        
        self.step_tabs.addTab(step2_group, '🎙 2.字幕')
        
        # 步骤3: 高光片段提取
        step3_group, step3_content = self._create_step_container('✨ 步骤3: 高光片段提取',
            '识别直播中有节目效果的时间段。支持弹幕热度分析、LLM 语义分析或双保险融合。')
        self._step_groups[3] = step3_group
        step3_layout = QGridLayout(step3_content)
        step3_layout.setSpacing(12)
        step3_layout.setContentsMargins(0, 0, 0, 0)
        step3_layout.setSpacing(10)
        
        step3_layout.addWidget(QLabel('输入目录:'), 0, 0)
        self.llm_input_dir = QLineEdit()
        step3_layout.addWidget(self.llm_input_dir, 0, 1)
        btn_browse_llm = QPushButton('浏览...')
        btn_browse_llm.clicked.connect(lambda: self.browse_dir(self.llm_input_dir))
        step3_layout.addWidget(btn_browse_llm, 0, 2)
        
        step3_layout.addWidget(QLabel('主播:'), 1, 0)
        self.llm_streamer = QComboBox()
        self.llm_streamer.setEditable(True)
        self.llm_streamer.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        self.llm_streamer.addItems(['栞栞Shiori', '灰泽满Hazel', '岁己SUI'])
        step3_layout.addWidget(self.llm_streamer, 1, 1)
        
        step3_layout.addWidget(QLabel('分析方式:'), 2, 0)
        self.llm_method = QComboBox()
        self.llm_method.addItems(['danmu', 'llm', 'emotion', 'multi'])
        self.llm_method.setItemText(0, '纯弹幕分析 (danmu)')
        self.llm_method.setItemText(1, '纯LLM分析 (llm)')
        self.llm_method.setItemText(2, '情绪驱动分析 (emotion)')
        self.llm_method.setItemText(3, '多保险融合 (multi)')
        step3_layout.addWidget(self.llm_method, 2, 1)

        # 多保险来源选择（仅 multi 模式显示）
        self.multi_sources_group = QGroupBox('组合来源')
        multi_src_layout = QHBoxLayout(self.multi_sources_group)
        self.multi_danmu = QCheckBox('弹幕分析')
        self.multi_danmu.setChecked(True)
        self.multi_llm = QCheckBox('LLM分析')
        self.multi_llm.setChecked(True)
        self.multi_emotion = QCheckBox('情绪分析')
        self.multi_emotion.setChecked(False)
        multi_src_layout.addWidget(self.multi_danmu)
        multi_src_layout.addWidget(self.multi_llm)
        multi_src_layout.addWidget(self.multi_emotion)
        self.multi_sources_group.setVisible(False)
        step3_layout.addWidget(self.multi_sources_group, 3, 0, 1, 3)
        self.llm_method.currentIndexChanged.connect(self._on_llm_method_changed)

        step3_layout.addWidget(QLabel('提示词目录:'), 4, 0)
        self.llm_prompts_dir = QLineEdit()
        self.llm_prompts_dir.setText('')
        step3_layout.addWidget(self.llm_prompts_dir, 4, 1)
        btn_browse_prompts = QPushButton('浏览...')
        btn_browse_prompts.clicked.connect(lambda: self.browse_dir(self.llm_prompts_dir))
        step3_layout.addWidget(btn_browse_prompts, 4, 2)
        
        llm_btn = QPushButton('执行高光提取')
        llm_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #48dbfb, stop:1 #1dd1a1);
                color: white;
                border: none;
                padding: 14px 24px; font-size: 14px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { opacity: 0.9; }
        ''')
        llm_btn.clicked.connect(self.run_llm)
        step3_layout.addWidget(llm_btn, 5, 0, 1, 3)
        
        self.step_tabs.addTab(step3_group, '✨ 3.高光')
        
        # 步骤4: 视频切片（不含字幕和封面）
        step4_group, step4_content = self._create_step_container('✂️ 步骤4: 视频切片生成',
            '根据高光检测结果用 FFmpeg 切出视频片段（不含字幕和封面）。')
        self._step_groups[4] = step4_group
        step4_layout = QGridLayout(step4_content)
        step4_layout.setSpacing(12)
        step4_layout.setContentsMargins(0, 0, 0, 0)
        step4_layout.setSpacing(10)
        
        step4_layout.addWidget(QLabel('输入目录:'), 0, 0)
        self.clip_input_dir = QLineEdit()
        step4_layout.addWidget(self.clip_input_dir, 0, 1)
        btn_browse_clip = QPushButton('浏览...')
        btn_browse_clip.clicked.connect(lambda: self.browse_dir(self.clip_input_dir))
        step4_layout.addWidget(btn_browse_clip, 0, 2)
        
        step4_layout.addWidget(QLabel('输出目录:'), 1, 0)
        self.clip_output_dir = QLineEdit()
        self.clip_output_dir.setPlaceholderText('默认: 输入目录/切片')
        step4_layout.addWidget(self.clip_output_dir, 1, 1)
        btn_browse_clip_out = QPushButton('浏览...')
        btn_browse_clip_out.clicked.connect(lambda: self.browse_dir(self.clip_output_dir))
        step4_layout.addWidget(btn_browse_clip_out, 1, 2)
        
        clip_btn = QPushButton('执行切片')
        clip_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1dd1a1, stop:1 #48dbfb);
                color: white;
                border: none;
                padding: 14px 24px; font-size: 14px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { opacity: 0.9; }
        ''')
        clip_btn.clicked.connect(self.run_clips)
        step4_layout.addWidget(clip_btn, 2, 1)
        
        self.step_tabs.addTab(step4_group, '✂️ 4.切片')
        
        # 步骤5: 字幕烧录
        step5_group, step5_content = self._create_step_container('📝 步骤5: 字幕烧录',
            '将 SRT 字幕烧录为视频内嵌硬字幕。可配置字体、颜色、描边等样式。')
        self._step_groups[5] = step5_group
        step5_layout = QGridLayout(step5_content)
        step5_layout.setSpacing(12)
        step5_layout.setContentsMargins(0, 0, 0, 0)
        step5_layout.setSpacing(10)

        step5_layout.addWidget(QLabel('切片目录:'), 0, 0)
        self.subtitle_input_dir = QLineEdit()
        step5_layout.addWidget(self.subtitle_input_dir, 0, 1)
        btn_browse_sub = QPushButton('浏览...')
        btn_browse_sub.clicked.connect(lambda: self.browse_dir(self.subtitle_input_dir))
        step5_layout.addWidget(btn_browse_sub, 0, 2)

        step5_layout.addWidget(QLabel('SRT目录:'), 1, 0)
        self.subtitle_srt_dir = QLineEdit()
        self.subtitle_srt_dir.setPlaceholderText('默认: 输入目录的上级')
        step5_layout.addWidget(self.subtitle_srt_dir, 1, 1)
        btn_browse_srt = QPushButton('浏览...')
        btn_browse_srt.clicked.connect(lambda: self.browse_dir(self.subtitle_srt_dir))
        step5_layout.addWidget(btn_browse_srt, 1, 2)

        # ASR 引擎设置
        step5_layout.addWidget(QLabel('ASR引擎:'), 2, 0)
        self.subtitle_asr_engine = QComboBox()
        self.subtitle_asr_engine.addItem('FunASR (GPU便携)', 'funasr')
        self.subtitle_asr_engine.addItem('Whisper (本地通用)', 'whisper')
        self.subtitle_asr_engine.addItem('Belle (中文优化)', 'belle')
        self.subtitle_asr_engine.addItem('Qwen3-ASR (Qwen官方本地)', 'qwen3')
        self.subtitle_asr_engine.addItem('MOSS-Transcribe (转写+说话人分离)', 'moss')
        self.subtitle_asr_engine.setCurrentIndex(1)  # 默认 whisper
        self.subtitle_asr_engine.currentTextChanged.connect(self.on_subtitle_engine_changed)
        step5_layout.addWidget(self.subtitle_asr_engine, 2, 1)

        step5_layout.addWidget(QLabel('模型:'), 2, 2)
        # FunASR 模型
        self.subtitle_asr_model = QComboBox()
        self.subtitle_asr_model.addItem('SenseVoiceSmall (情感+语音+事件)', 'sensevoice')
        self.subtitle_asr_model.addItem('Paraformer-zh (中文专用)', 'paraformer-zh')
        self.subtitle_asr_model.addItem('Paraformer-en (英文专用)', 'paraformer-en')
        step5_layout.addWidget(self.subtitle_asr_model, 2, 3)
        # Whisper 模型
        self.subtitle_whisper_model = QComboBox()
        self.subtitle_whisper_model.addItem('tiny (72MB - 极速)', os.path.join(WHISPER_MODEL_DIR, 'tiny.pt'))
        self.subtitle_whisper_model.addItem('base (138MB - 快速)', os.path.join(WHISPER_MODEL_DIR, 'base.pt'))
        self.subtitle_whisper_model.addItem('medium (1.4GB - 均衡)', os.path.join(WHISPER_MODEL_DIR, 'medium.pt'))
        self.subtitle_whisper_model.addItem('large-v3-turbo (1.5GB - 推荐)', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))
        # BELLE-2 中文优化版
        belle_dir = os.path.join(WHISPER_MODEL_DIR, 'belle-whisper-large-v3-turbo-zh')
        if os.path.isdir(belle_dir):
            self.subtitle_whisper_model.addItem('Belle-whisper-zh (1.5GB - 中文优化)', belle_dir)
        self.subtitle_whisper_model.setCurrentIndex(3)  # 默认 large-v3-turbo
        step5_layout.addWidget(self.subtitle_whisper_model, 2, 3)
        # Qwen3 模型（初始隐藏）
        self.subtitle_qwen3_model = QComboBox()
        self.subtitle_qwen3_model.addItem('Qwen3-ASR-1.7B (默认 - 质量优先)', 'Qwen3-ASR-1.7B')
        self.subtitle_qwen3_model.addItem('Qwen3-ASR-0.6B (轻量 - 更快)', 'Qwen3-ASR-0.6B')
        step5_layout.addWidget(self.subtitle_qwen3_model, 2, 3)
        self.subtitle_asr_model.hide()  # 初始隐藏（whisper 默认）
        self.subtitle_qwen3_model.hide()

        step5_layout.addWidget(QLabel('语言:'), 3, 0)
        self.subtitle_asr_lang = QComboBox()
        self.subtitle_asr_lang.addItems(['zh', 'en', 'ja', 'auto'])
        step5_layout.addWidget(self.subtitle_asr_lang, 3, 1)

        self.subtitle_skip_asr = QCheckBox('跳过ASR（使用已有SRT）')
        step5_layout.addWidget(self.subtitle_skip_asr, 3, 2, 1, 2)

        self.subtitle_enabled = QCheckBox('启用字幕烧录')
        self.subtitle_enabled.setChecked(True)
        step5_layout.addWidget(self.subtitle_enabled, 4, 0, 1, 2)

        # 字幕样式
        style_group = QGroupBox('字幕样式')
        style_layout = QGridLayout(style_group)
        style_layout.setSpacing(6)

        style_layout.addWidget(QLabel('字体:'), 0, 0)
        self.subtitle_font = QComboBox()
        self.subtitle_font.setEditable(True)
        # 从 assets/font 目录加载字体
        font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')
        font_names = ['MaokenAssortedSans', '微软雅黑', 'SimHei', 'Arial']
        if os.path.isdir(font_dir):
            for fname in sorted(os.listdir(font_dir)):
                if fname.lower().endswith(('.ttf', '.otf')):
                    base = os.path.splitext(fname)[0]
                    if base not in font_names:
                        font_names.append(base)
        self.subtitle_font.addItems(font_names)
        style_layout.addWidget(self.subtitle_font, 0, 1)

        style_layout.addWidget(QLabel('字号:'), 0, 2)
        self.subtitle_size = QSpinBox()
        self.subtitle_size.setRange(12, 300)
        self.subtitle_size.setValue(80)
        style_layout.addWidget(self.subtitle_size, 0, 3)

        # 颜色预设
        COLOR_PRESETS = {
            '白底': ('&H00FFFFFF', '&H00000000'),
            '蓝底': ('&H00f6dcb7', '&H00000000'),
            '褐色底': ('&H00c4f3f9', '&H0038424a'),
            '白底+红边': ('&H00FFFFFF', '&H003718ff'),
            '自定义': ('', ''),
        }
        self._color_presets = COLOR_PRESETS

        style_layout.addWidget(QLabel('预设:'), 1, 0)
        self.subtitle_preset = QComboBox()
        self.subtitle_preset.addItems(list(COLOR_PRESETS.keys()))
        self.subtitle_preset.setCurrentIndex(0)
        self.subtitle_preset.currentTextChanged.connect(self.on_preset_changed)
        style_layout.addWidget(self.subtitle_preset, 1, 1, 1, 3)

        style_layout.addWidget(QLabel('颜色:'), 2, 0)
        self.subtitle_color = QComboBox()
        self.subtitle_color.setEditable(True)
        self.subtitle_color.addItems(['&H00FFFFFF', '&H00f6dcb7', '&H00c4f3f9'])
        self.subtitle_color.setCurrentText('&H00FFFFFF')
        self.subtitle_color.currentTextChanged.connect(self._on_color_edited)
        style_layout.addWidget(self.subtitle_color, 2, 1)

        style_layout.addWidget(QLabel('描边色:'), 2, 2)
        self.subtitle_outline = QComboBox()
        self.subtitle_outline.setEditable(True)
        self.subtitle_outline.addItems(['&H00000000', '&H003718ff', '&H0038424a'])
        self.subtitle_outline.setCurrentText('&H00000000')
        self.subtitle_outline.currentTextChanged.connect(self._on_color_edited)
        style_layout.addWidget(self.subtitle_outline, 2, 3)

        style_layout.addWidget(QLabel('描边宽:'), 3, 0)
        self.subtitle_outline_w = QSpinBox()
        self.subtitle_outline_w.setRange(0, 10)
        self.subtitle_outline_w.setValue(8)
        style_layout.addWidget(self.subtitle_outline_w, 3, 1)

        style_layout.addWidget(QLabel('阴影:'), 3, 2)
        self.subtitle_shadow = QCheckBox('')
        self.subtitle_shadow.setChecked(True)
        style_layout.addWidget(self.subtitle_shadow, 3, 3)

        style_layout.addWidget(QLabel('底部边距:'), 4, 0)
        self.subtitle_margin = QSpinBox()
        self.subtitle_margin.setRange(0, 300)
        self.subtitle_margin.setValue(100)
        style_layout.addWidget(self.subtitle_margin, 4, 1)

        style_layout.addWidget(QLabel('方向:'), 4, 2)
        self.subtitle_orient = QComboBox()
        self.subtitle_orient.addItems(['horizontal', 'vertical'])
        style_layout.addWidget(self.subtitle_orient, 4, 3)

        # 编码速度
        style_layout.addWidget(QLabel('编码速度:'), 5, 0)
        self.subtitle_speed = QComboBox()
        self.subtitle_speed.addItems(['auto (自动GPU加速)', 'fast (最快)', 'balanced (均衡)', 'quality (画质优先)'])
        self.subtitle_speed.setCurrentIndex(0)
        style_layout.addWidget(self.subtitle_speed, 5, 1, 1, 3)

        step5_layout.addWidget(style_group, 4, 0, 1, 3)

        sub_btn = QPushButton('执行字幕烧录')
        sub_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #feca57, stop:1 #ff9ff3);
                color: white; border: none; padding: 14px 24px; font-size: 14px;
                border-radius: 8px; font-weight: bold;
            }
            QPushButton:hover { opacity: 0.9; }
        ''')
        sub_btn.clicked.connect(self.run_subtitles_burn)
        step5_layout.addWidget(sub_btn, 5, 0, 1, 3)

        self.step_tabs.addTab(step5_group, '📝 5.烧录')

        # 步骤6: 封面生成
        step6_group, step6_content = self._create_step_container('🖼 步骤6: 封面生成',
            '为每个切片生成 B 站风格封面图（1080×608）。支持 4 种风格随机或指定。')
        self._step_groups[6] = step6_group
        step6_layout = QGridLayout(step6_content)
        step6_layout.setSpacing(12)
        step6_layout.setContentsMargins(0, 0, 0, 0)
        step6_layout.setSpacing(10)
        
        step6_layout.addWidget(QLabel('输入目录:'), 0, 0)
        self.cover_input_dir = QLineEdit()
        step6_layout.addWidget(self.cover_input_dir, 0, 1)
        btn_browse_cover = QPushButton('浏览...')
        btn_browse_cover.clicked.connect(lambda: self.browse_dir(self.cover_input_dir))
        step6_layout.addWidget(btn_browse_cover, 0, 2)
        
        step6_layout.addWidget(QLabel('样式:'), 1, 0)
        self.cover_style = QComboBox()
        self.cover_style.addItems(['style1', 'style2', 'style3', 'style4', 'style5', '🎲 随机 (1-3)'])
        self.cover_style.setItemData(5, 'random_1_4')
        step6_layout.addWidget(self.cover_style, 1, 1)
        
        step6_layout.addWidget(QLabel('字体:'), 2, 0)
        self.cover_font = QComboBox()
        self.cover_font.setEditable(True)
        self.cover_font.setInsertPolicy(QComboBox.InsertPolicy.InsertAtTop)
        step6_layout.addWidget(self.cover_font, 2, 1)
        btn_load_fonts = QPushButton('加载字体')
        btn_load_fonts.clicked.connect(self.load_font_list)
        step6_layout.addWidget(btn_load_fonts, 2, 2)
        
        cover_btn = QPushButton('执行封面生成')
        cover_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff9ff3, stop:1 #54a0ff);
                color: white;
                border: none;
                padding: 14px 24px; font-size: 14px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { opacity: 0.9; }
        ''')
        cover_btn.clicked.connect(self.run_covers)
        step6_layout.addWidget(cover_btn, 3, 1)
        
        self.step_tabs.addTab(step6_group, '🖼 6.封面')
        
        # 步骤7: B站投稿
        step7_group, step7_content = self._create_step_container('📤 步骤7: B站视频投稿',
            '将切片视频 + 封面一键投稿到 B 站。需先在顶部登录 B 站账号。')
        self._step_groups[7] = step7_group
        step7_layout = QGridLayout(step7_content)
        step7_layout.setSpacing(12)
        step7_layout.setContentsMargins(0, 0, 0, 0)
        step7_layout.setSpacing(10)
        

        step7_layout.addWidget(QLabel('视频文件:'), 1, 0)
        self.upload_video = QLineEdit()
        self.upload_video.setPlaceholderText('选择要上传的视频文件')
        step7_layout.addWidget(self.upload_video, 1, 1)
        btn_browse_upvideo = QPushButton('浏览...')
        btn_browse_upvideo.clicked.connect(lambda: self.browse_file(self.upload_video, '视频文件 (*.mp4 *.mkv *.flv)'))
        step7_layout.addWidget(btn_browse_upvideo, 1, 2)
        
        # 标题
        step7_layout.addWidget(QLabel('视频标题:'), 2, 0)
        self.upload_title = QLineEdit()
        self.upload_title.setPlaceholderText('必填，不超过80字')
        step7_layout.addWidget(self.upload_title, 2, 1, 1, 2)
        
        # 简介
        step7_layout.addWidget(QLabel('视频简介:'), 3, 0, 1, 3)
        self.upload_desc = QTextEdit()
        self.upload_desc.setMaximumHeight(80)
        self.upload_desc.setPlaceholderText('可选，不超过2000字')
        step7_layout.addWidget(self.upload_desc, 4, 0, 1, 3)
        
        # 标签
        step7_layout.addWidget(QLabel('视频标签:'), 5, 0)
        self.upload_tags = QLineEdit()
        self.upload_tags.setPlaceholderText('逗号分隔，最多10个，如: A-SOUL,切片,搞笑')
        step7_layout.addWidget(self.upload_tags, 5, 1, 1, 2)
        
        # 分区
        step7_layout.addWidget(QLabel('投稿分区:'), 6, 0)
        self.upload_tid = QComboBox()
        self.upload_tid.setEditable(True)
        self.upload_tid.addItem('生活·日常 (124)', 124)
        self.upload_tid.addItem('娱乐 (160)', 160)
        self.upload_tid.addItem('搞笑 (138)', 138)
        self.upload_tid.addItem('动画·综合 (217)', 217)
        self.upload_tid.addItem('鬼畜 (119)', 119)
        self.upload_tid.addItem('音乐·翻唱 (129)', 129)
        self.upload_tid.addItem('知识 (36)', 36)
        self.upload_tid.addItem('科技 (188)', 188)
        self.upload_tid.addItem('游戏 (4)', 4)
        self.upload_tid.addItem('美食 (211)', 211)
        self.upload_tid.addItem('时尚 (155)', 155)
        self.upload_tid.setCurrentIndex(0)
        step7_layout.addWidget(self.upload_tid, 6, 1, 1, 2)
        
        # 封面路径
        step7_layout.addWidget(QLabel('封面文件:'), 7, 0)
        self.upload_cover = QLineEdit()
        self.upload_cover.setPlaceholderText('选择封面图片路径（可选）')
        step7_layout.addWidget(self.upload_cover, 7, 1)
        btn_browse_cover_up = QPushButton('浏览...')
        btn_browse_cover_up.clicked.connect(lambda: self.browse_file(self.upload_cover, '图片文件 (*.jpg *.png *.jpeg)'))
        step7_layout.addWidget(btn_browse_cover_up, 7, 2)
        
        # 原创/转载
        step7_layout.addWidget(QLabel('版权类型:'), 8, 0)
        self.upload_copyright = QComboBox()
        self.upload_copyright.addItem('原创（自制）', 1)
        self.upload_copyright.addItem('转载（需填来源）', 2)
        self.upload_copyright.setCurrentIndex(0)
        step7_layout.addWidget(self.upload_copyright, 8, 1)
        
        self.upload_source_label = QLabel('转载来源URL:')
        self.upload_source_label.setVisible(False)
        step7_layout.addWidget(self.upload_source_label, 9, 0)
        self.upload_source = QLineEdit()
        self.upload_source.setPlaceholderText('转载来源视频链接')
        self.upload_source.setVisible(False)
        step7_layout.addWidget(self.upload_source, 9, 1, 1, 2)
        self.upload_copyright.currentIndexChanged.connect(
            lambda i: [self.upload_source_label.setVisible(i==1), self.upload_source.setVisible(i==1)]
        )
        
        # 禁止转载
        self.upload_no_reprint = QCheckBox('禁止他人转载我的投稿（哔哩哔哩创作公约要求）')
        self.upload_no_reprint.setChecked(True)
        step7_layout.addWidget(self.upload_no_reprint, 10, 0, 1, 3)
        
        # 可选字段说明
        info_label = QLabel('💡 其他可选字段（如需使用请在 gui_config.json 中配置 upload_ 前缀字段）:\n'
            '定时发布 | 粉丝动态 | 互动投票 | 杜比音效 | 无损音乐 | 字幕设置 | 活动话题 | 关闭弹幕 | 关闭评论 | 评论精选 | 播放器水印 | 展示充电'
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet('color: #a0a0b8; font-size: 11px; padding: 6px; background: rgba(255,255,255,0.03); border-radius: 6px;')
        step7_layout.addWidget(info_label, 11, 0, 1, 3)
        
        upload_btn = QPushButton('执行B站投稿')
        upload_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b6b, stop:1 #feca57);
                color: white;
                border: none;
                padding: 14px;
                font-size: 16px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background: #ee5a6f; }
        ''')
        upload_btn.clicked.connect(self.run_upload)
        step7_layout.addWidget(upload_btn, 12, 0, 1, 3)
        
        # 生成投稿清单按钮（基于实际文件扫描，不依赖流水线）
        manifest_btn = QPushButton('📄 根据文件生成投稿清单')
        manifest_btn.setStyleSheet('''
            QPushButton {
                background: #2d5a3d;
                color: #c3e88d;
                border: 1px solid #4a8a5e;
                padding: 14px;
                font-size: 15px;
                border-radius: 8px;
                font-weight: bold;
            }
            QPushButton:hover { background: #3a704a; }
        ''')
        manifest_btn.clicked.connect(self.generate_manifest_from_files)
        step7_layout.addWidget(manifest_btn, 13, 0, 1, 3)
        
        self._update_upload_login_status()
        
        self.step_tabs.addTab(step7_group, '📤 7.投稿')

        # 默认显示第一个 Tab
        self.step_tabs.setCurrentIndex(0)
        return self.step_tabs
    
    def _create_nav_bar(self):
        """创建步骤导航栏 — 固定在顶部，水平一行"""
        nav = QWidget()
        nav.setStyleSheet('''
            QWidget {
                background: #1e1e32;
                border: 1px solid #444;
                border-radius: 10px;
            }
        ''')
        nav_layout = QHBoxLayout(nav)
        nav_layout.setSpacing(8)
        nav_layout.setContentsMargins(12, 8, 12, 8)
        
        nav_layout.addWidget(QLabel('🔗'))
        nav_layout.addSpacing(4)
        
        names = [
            ('📁 准备', 1),
            ('🎙 字幕', 2),
            ('✨ 高光', 3),
            ('✂️ 切片', 4),
            ('📝 烧录', 5),
            ('🖼 封面', 6),
            ('📤 投稿', 7),
        ]
        
        for name, idx in names:
            btn = QPushButton(name)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFixedHeight(30)
            btn.setStyleSheet('''
                QPushButton {
                    background: #3d3d56;
                    color: #ccc;
                    border: 1px solid #5a5a6e;
                    padding: 4px 14px;
                    border-radius: 6px;
                    font-size: 14px;
                }
                QPushButton:hover { background: #3a3a5e; border-color: #48dbfb; color: #fff; }
            ''')
            btn.clicked.connect(lambda checked, idx=idx: self._scroll_to_step(idx))
            nav_layout.addWidget(btn)
        
        nav_layout.addStretch()
        return nav
    
    def _scroll_to_step(self, idx):
        """切换到指定步骤的 Tab（原版是滚动定位，方案A 改为 Tab 切换）"""
        if hasattr(self, 'step_tabs') and 1 <= idx <= 7:
            # Tab 索引 0-6 对应步骤 1-7
            self.step_tabs.setCurrentIndex(idx - 1)
    
    def browse_dir(self, line_edit):
        """浏览目录"""
        dir_path = QFileDialog.getExistingDirectory(self, '选择目录', line_edit.text() or '')
        if dir_path:
            line_edit.setText(dir_path)
    
    def load_font_list(self):
        """加载字体列表（仅加载 assets/font 目录）

        优化：二次启动优先读 sessions/.cache.json 缓存，跳过磁盘 glob。
        首次启动扫描完写入缓存。后台 1s 后异步验证缓存是否过期。
        """
        if self._fonts_loaded:
            return
        self._fonts_loaded = True

        assets_font_dir = os.path.join(PROJECT_ROOT, 'assets', 'font')

        # ── 尝试读缓存 ──
        cached_fonts = self._read_font_cache()
        if cached_fonts:
            # 二次启动快路径：直接用缓存填充 ComboBox
            self.cover_font.clear()
            seen = set()
            for entry in cached_fonts:
                name = entry.get('name', '')
                path = entry.get('path', '')
                if name and name not in seen:
                    seen.add(name)
                    self.cover_font.addItem(name, path)
            self._restore_cover_font_selection()
            # 后台异步验证缓存（1s 后重新扫描，有变化才更新 UI）
            QTimer.singleShot(1000, lambda: self._refresh_font_cache(assets_font_dir, silent=True))
            return

        # ── 首次启动慢路径：扫描磁盘 ──
        self._scan_and_populate_fonts(assets_font_dir)

    def _read_font_cache(self):
        """读取字体缓存，返回 list[dict] 或 None"""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
                return cache.get('fonts')
        except Exception:
            pass
        return None

    def _write_font_cache(self, fonts_list):
        """写入字体缓存（合并到现有缓存文件）"""
        try:
            cache = {}
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                    cache = json.load(f)
            cache['fonts'] = fonts_list
            os.makedirs(os.path.dirname(CACHE_FILE), exist_ok=True)
            with open(CACHE_FILE, 'w', encoding='utf-8') as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _scan_and_populate_fonts(self, assets_font_dir, silent=False):
        """扫描字体目录并填充 ComboBox"""
        try:
            import glob as _glob
            self.cover_font.clear()
            seen = set()
            fonts_cache = []  # 用于写缓存

            if os.path.exists(assets_font_dir):
                for ext in ['*.ttf', '*.otf', '*.TTF', '*.OTF']:
                    for font_path in _glob.glob(os.path.join(assets_font_dir, ext)):
                        name = os.path.basename(font_path)
                        if name not in seen:
                            seen.add(name)
                            self.cover_font.addItem(name, font_path)
                            fonts_cache.append({'name': name, 'path': font_path})

            if not silent:
                self.log(f'已加载 {len(seen)} 个字体', 'success')

            # 写入缓存供二次启动使用
            self._write_font_cache(fonts_cache)
            self._restore_cover_font_selection()
        except Exception as e:
            self.log(f'加载字体失败: {e}', 'error')

    def _refresh_font_cache(self, assets_font_dir, silent=False):
        """后台异步刷新字体缓存，发现变化才更新 UI"""
        try:
            import glob as _glob
            current = set()
            if os.path.exists(assets_font_dir):
                for ext in ['*.ttf', '*.otf', '*.TTF', '*.OTF']:
                    for p in _glob.glob(os.path.join(assets_font_dir, ext)):
                        current.add(os.path.basename(p))
            # 对比当前 ComboBox 内容
            ui_fonts = set()
            for i in range(self.cover_font.count()):
                ui_fonts.add(self.cover_font.itemText(i))
            if current != ui_fonts:
                # 有变化，重新扫描填充
                self._scan_and_populate_fonts(assets_font_dir, silent=False)
        except Exception:
            pass  # 后台刷新失败不影响主流程

    def _restore_cover_font_selection(self):
        """恢复上次选择的封面字体"""
        cfg = self.config or {}
        cft = cfg.get('cover_font', 'MaoKenShiJinHei-2.ttf')
        idx = self.cover_font.findData(cft)
        if idx >= 0:
            self.cover_font.setCurrentIndex(idx)
        else:
            self.cover_font.setCurrentText(cft)

    def _on_llm_method_changed(self, idx):
        """多保险来源复选框显隐控制"""
        method = self.llm_method.currentText()
        is_multi = 'multi' in method
        self.multi_sources_group.setVisible(is_multi)

    def apply_global_config(self):
        """应用全局配置到所有步骤"""
        input_dir = self.global_input_dir.text()
        streamer = self.global_streamer.currentText()
        
        self.step1_input_dir.setText(input_dir)
        self.whisper_input_dir.setText(input_dir)
        self.llm_input_dir.setText(input_dir)
        self.llm_streamer.setCurrentText(streamer)
        self.clip_input_dir.setText(input_dir)
        # 步骤5的切片目录始终指向 {全局输入目录}\切片\视频
        # 步骤4执行切片时自动创建这个路径，步骤5直接复用
        clips_video_dir = os.path.join(input_dir, '切片', '视频')
        self.subtitle_input_dir.setText(clips_video_dir)
        self.cover_input_dir.setText(input_dir)
        
        self.log('全局配置已应用到所有步骤', 'success')
    
    def refresh_file_list(self):
        """刷新文件列表"""
        input_dir = self.step1_input_dir.text()
        if not input_dir or not os.path.exists(input_dir):
            self.log('错误：目录不存在', 'error')
            return
        
        try:
            files = []
            for item in os.listdir(input_dir):
                item_path = os.path.join(input_dir, item)
                if os.path.isfile(item_path):
                    ext = os.path.splitext(item)[1].lower()
                    if ext in ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv']:
                        files.append(f'[视频] {item}')
                    elif ext == '.srt':
                        files.append(f'[字幕] {item}')
            
            if files:
                self.file_list_text.setText('\n'.join(files))
                self.log(f'找到 {len(files)} 个文件', 'success')
            else:
                self.file_list_text.setText('目录为空')
                self.log('目录为空', 'info')
        except Exception as e:
            self.log(f'读取目录失败: {e}', 'error')

    def run_convert_flv(self):
        """手动执行步骤1：FLV 转 MP4。"""
        input_dir = self.step1_input_dir.text().strip() or self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('错误：请先选择有效的录播目录', 'error')
            return
        self.start_task('convert_flv', {
            'input_dir': input_dir,
        })

    def run_portrait_landscape(self):
        """手动执行步骤1：竖屏 MP4 转横屏。"""
        input_dir = self.step1_input_dir.text().strip() or self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('错误：请先选择有效的录播目录', 'error')
            return
        self.start_task('portrait_landscape', {
            'input_dir': input_dir,
            'width': 1920,
            'height': 1080,
            'blur': 20,
        })

    def run_burn_danmu(self):
        """手动执行可选任务：压制同名 MP4 + XML 弹幕版。"""
        input_dir = self.step1_input_dir.text().strip() or self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('错误：请先选择有效的录播目录', 'error')
            return
        self.start_task('burn_danmu', {
            'input_dir': input_dir,
        })
    
    def on_engine_changed(self, text):
        """引擎切换时显示/隐藏对应控件"""
        is_funasr = 'funasr' in text.lower() or 'gpu' in text.lower()
        is_whisper = 'whisper' in text.lower()
        is_belle = 'belle' in text.lower()
        is_qwen3 = 'qwen3' in text.lower()

        # FunASR 控件
        self.funasr_model.setVisible(is_funasr)
        self.funasr_speaker.setVisible(is_funasr)

        # Whisper 控件
        self.whisper_model_label.setVisible(is_whisper)
        self.whisper_model.setVisible(is_whisper)
        self.whisper_auto_lang.setVisible(is_whisper)

        # Qwen3 控件
        self.qwen3_model_label.setVisible(is_qwen3)
        self.qwen3_model.setVisible(is_qwen3)

        # Belle 无额外配置项

    def on_subtitle_engine_changed(self, text):
        """步骤5引擎切换时显示/隐藏对应模型控件"""
        is_funasr = 'funasr' in text.lower() or 'gpu' in text.lower()
        is_whisper = 'whisper' in text.lower()
        is_belle = 'belle' in text.lower()
        is_qwen3 = 'qwen3' in text.lower()
        self.subtitle_asr_model.setVisible(is_funasr)
        self.subtitle_whisper_model.setVisible(is_whisper)
        self.subtitle_qwen3_model.setVisible(is_qwen3)
        # Belle 无额外配置项

    def on_preset_changed(self, text):
        """预设切换：联动颜色+描边"""
        preset = self._color_presets.get(text)
        if preset and preset[0] and preset[1]:
            self.subtitle_color.blockSignals(True)
            self.subtitle_outline.blockSignals(True)
            self.subtitle_color.setCurrentText(preset[0])
            self.subtitle_outline.setCurrentText(preset[1])
            self.subtitle_color.blockSignals(False)
            self.subtitle_outline.blockSignals(False)

    def _on_color_edited(self):
        """颜色手动修改后切到自定义预设"""
        c = self.subtitle_color.currentText()
        o = self.subtitle_outline.currentText()
        matched = None
        for name, (pc, po) in self._color_presets.items():
            if pc == c and po == o:
                matched = name
                break
        if matched:
            self.subtitle_preset.blockSignals(True)
            self.subtitle_preset.setCurrentText(matched)
            self.subtitle_preset.blockSignals(False)
        else:
            self.subtitle_preset.blockSignals(True)
            self.subtitle_preset.setCurrentText('自定义')
            self.subtitle_preset.blockSignals(False)
    
    def run_whisper(self):
        """执行字幕识别"""
        engine_key = self.asr_engine.currentData() or 'funasr'
        is_funasr = (engine_key == 'funasr')
        is_belle = (engine_key == 'belle')
        is_qwen3 = (engine_key == 'qwen3')
        is_moss = (engine_key == 'moss')

        params = {
            'input_dir': self.whisper_input_dir.text(),
            'language': self.whisper_lang.currentText(),
            'engine': 'qwen3' if is_qwen3 else ('belle' if is_belle else ('portable' if is_funasr else ('moss' if is_moss else 'whisper'))),
        }

        if is_belle:
            # Belle 使用默认模型位置，无需额外参数
            pass
        elif is_qwen3:
            params['qwen3_model'] = self.qwen3_model.currentData() or 'Qwen3-ASR-1.7B'
        elif is_moss:
            # MOSS 使用 models/ 下默认模型目录，无需额外参数
            pass
        elif is_funasr:
            params['funasr_model'] = self.funasr_model.currentData() or 'sensevoice'
            params['speaker'] = self.funasr_speaker.isChecked()
        else:
            params['model'] = self.whisper_model.currentData() or os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt')
            params['auto_lang'] = self.whisper_auto_lang.isChecked()

        self.start_task('subtitles', params)
    
    def run_llm(self):
        """执行LLM分析"""
        method = self.llm_method.currentText().split('(')[-1].strip(')')
        params = {
            'input_dir': self.llm_input_dir.text(),
            'streamer': self.llm_streamer.currentText(),
            'method': method,
            'prompts_dir': self.llm_prompts_dir.text(),
            'api_key': self.global_api_key.text(),
            'model': self.global_model.currentText()
        }
        if method == 'multi':
            sources = []
            if self.multi_danmu.isChecked():
                sources.append('danmu')
            if self.multi_llm.isChecked():
                sources.append('llm')
            if self.multi_emotion.isChecked():
                sources.append('emotion')
            if sources:
                params['multi_sources'] = ','.join(sources)
        self.start_task('llm', params)
    
    def run_clips(self):
        """执行视频切片"""
        self.start_task('clips', {
            'input_dir': self.clip_input_dir.text(),
            'output_dir': self.clip_output_dir.text()
        })
    
    def run_subtitles_burn(self):
        """执行字幕烧录"""
        engine_key = self.subtitle_asr_engine.currentData() or 'whisper'
        is_funasr = (engine_key == 'funasr')
        is_belle = (engine_key == 'belle')
        is_qwen3 = (engine_key == 'qwen3')
        # FunASR (GPU便携) → CLI 用 portable 引擎
        cli_engine = ('qwen3' if is_qwen3 else
                      ('belle' if is_belle else ('portable' if is_funasr else engine_key)))
        if is_belle:
            asr_model = 'belle-whisper-large-v3-turbo-zh'
        elif is_qwen3:
            asr_model = self.subtitle_qwen3_model.currentData() or 'Qwen3-ASR-1.7B'
        elif engine_key == 'moss':
            # MOSS 忽略 --asr-model，仅用于日志展示
            asr_model = 'MOSS-Transcribe-Diarize-0.9B'
        elif is_funasr:
            asr_model = self.subtitle_asr_model.currentData() or 'sensevoice'
        else:
            asr_model = self.subtitle_whisper_model.currentData() or os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt')
        self.start_task('subtitles_burn', {
            'input_dir': self.subtitle_input_dir.text(),
            'srt_dir': self.subtitle_srt_dir.text(),
            'speed': self.subtitle_speed.currentText().split(' ')[0],
            'asr_engine': cli_engine,
            'asr_model': asr_model,
            'asr_language': self.subtitle_asr_lang.currentText(),
            'skip_asr': self.subtitle_skip_asr.isChecked(),
            'font_family': self.subtitle_font.currentText(),
            'font_size': self.subtitle_size.value(),
            'primary_color': self.subtitle_color.currentText(),
            'outline_color': self.subtitle_outline.currentText(),
            'outline_width': self.subtitle_outline_w.value(),
            'shadow': self.subtitle_shadow.isChecked(),
            'margin_v': self.subtitle_margin.value(),
            'orientation': self.subtitle_orient.currentText(),
        })

    def run_covers(self):
        """执行封面生成"""
        font_data = self.cover_font.currentData()
        style = self.cover_style.currentData() or self.cover_style.currentText()
        self.start_task('covers', {
            'input_dir': self.cover_input_dir.text(),
            'output_dir': '',
            'style': style,
            'font': font_data or self.cover_font.currentText()
        })
    
    def run_pipeline(self):
        """一键流转：步骤2→6"""
        input_dir = self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('错误：请先在全局配置中填写有效输入目录', 'error')
            return
        streamer = self.global_streamer.currentText().strip()
        streamer_dir = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer}.json')
        config_path = streamer_dir if os.path.exists(streamer_dir) else ''
        self.log(f'🚀 启动一键流转: {input_dir}', 'info')
        self.log(f'   主播: {streamer}', 'info')
        if config_path:
            self.log(f'   配置: {config_path}', 'info')
        # 步骤5字幕烧录始终跟随主播配置 step5_burn.enabled（面板勾选仅同步展示，不覆盖配置）
        cfg_enabled = True
        if config_path:
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    import json as _json
                    _cfg = _json.load(f)
                cfg_enabled = _cfg.get('step5_burn', {}).get('enabled', True)
            except Exception:
                pass
        self.subtitle_enabled.setChecked(bool(cfg_enabled))
        self.log(f'   步骤5 字幕烧录: {"启用" if cfg_enabled else "禁用"} (跟随主播配置)', 'info')
        params = {
            'input_dir': input_dir,
            'streamer': streamer,
            'config_path': config_path,
        }
        self.start_task('pipeline', params)

    def generate_manifest_from_files(self):
        """根据切片目录实际文件生成 upload_manifest.json

        扫描 切片/视频/ 下的 *_subbed.mp4 和 切片/封面/ 下的对应封面图，
        不依赖 Data_source.txt，纯文件级别匹配。
        同时加载主播专属配置 (streamer_configs/) 获取默认投稿参数。
        """
        import json as _json, re as _re
        input_dir = self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('❌ 请先在全局配置中设置有效的输入目录', 'error')
            return

        videos_dir = os.path.join(input_dir, '切片', '视频')
        covers_dir = os.path.join(input_dir, '切片', '封面')
        if not os.path.isdir(videos_dir):
            videos_dir = os.path.join(input_dir, '切片')
            covers_dir = os.path.join(input_dir, '切片')

        subbed = []
        if os.path.isdir(videos_dir):
            for f in sorted(os.listdir(videos_dir)):
                if f.endswith('_subbed.mp4'):
                    subbed.append(f)
        if not subbed:
            self.log(f'❌ 未在 {videos_dir} 找到 _subbed.mp4 视频', 'error')
            return

        # 优先加载主播专属配置 (streamer_configs/)
        streamer = self.global_streamer.currentText().strip()
        scfg_path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{streamer}.json')
        scfg = {}
        if os.path.exists(scfg_path):
            with open(scfg_path, 'r', encoding='utf-8') as f:
                scfg = _json.load(f)
            self.log(f'📋 已加载主播配置: {scfg_path}', 'info')

        # 从主播配置读取投稿默认值，没有则从 GUI 取
        s7 = scfg.get('step7_upload', {})
        links = scfg.get('bilibili_links', self.load_config() and self.load_config().get('bilibili_links', {}) or {})
        space = links.get('space', '')
        live = links.get('live', '')

        # tags/tid/copyright：优先主播配置
        tags = s7.get('tags', [])
        tid = s7.get('tid', self.upload_tid.currentData() or 217)
        copyright_val = 1 if s7.get('copyright') == 'original' else (self.upload_copyright.currentData() or 1)
        title_suffix_name = str(s7.get('title_suffix_name') or streamer).strip() or streamer

        # 加载 Data_source.txt 的 summary 映射 (sanitized_title → summary)
        # ── 关键：文件名经过 sanitize_filename 处理（替换 <>:"/\|?* 为 _），
        # 所以匹配键也必须做同样的 sanitize，否则含特殊字符的标题永远匹配不上。
        _invalid_chars2 = '<>:"/\\|?*'
        title_meta = {}
        ds_path = os.path.join(input_dir, 'Data_source.txt')
        if os.path.exists(ds_path):
            with open(ds_path, 'r', encoding='utf-8') as _fds:
                for clip in _json.load(_fds):
                    t = clip.get('title', '').strip()
                    if t:
                        key = t
                        for ch in _invalid_chars2:
                            key = key.replace(ch, '_')
                        key = key[:100]
                        title_meta[key] = {
                            'title': t,
                            'summary': clip.get('summary', '').strip(),
                            'real_timestamp': clip.get('real_timestamp', '').strip(),
                        }

        old_state = {}
        manifest_path = os.path.join(input_dir, 'upload_manifest.json')
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path, 'r', encoding='utf-8') as f:
                    for old in _json.load(f):
                        old_state[old.get('video_path', '')] = {
                            k: old.get(k) for k in (
                                'upload_status', 'upload_attempts', 'bvid', 'aid',
                                'last_error', 'last_upload_at', 'last_attempt_at'
                            )
                        }
            except Exception:
                old_state = {}

        upload_list = []
        for video_file in subbed:
            m = _re.match(r'^\[([^\]]+)\]\s*(.+?)_subbed\.mp4$', video_file)
            if not m:
                self.log(f'⚠️ 跳过无法解析的文件名: {video_file}', 'warning')
                continue
            seg = m.group(1)
            title_raw = m.group(2)
            # 投稿标题正文优先用 Data_source.txt 原始标题（文件名里的 ? : 等已被清洗成 _）
            original_title = title_meta.get(title_raw, {}).get('title', '')
            title = f'【{title_suffix_name}】{original_title or title_raw}'  # 标题前加配置显示名

            # 匹配封面：按完整文件名前缀精确匹配（同 segment 下可能有多个切片）
            cover = None
            if os.path.isdir(covers_dir):
                cover_base = video_file[:-len('_subbed.mp4')]
                for ext in ('.jpg', '.png', '.jpeg'):
                    candidate = os.path.join(covers_dir, cover_base + ext)
                    if os.path.exists(candidate):
                        cover = candidate
                        break
                # 回退：按 segment ID 匹配（兼容旧命名 / 封面标题与视频标题不一致的情况）
                if not cover:
                    for cf in sorted(os.listdir(covers_dir)):
                        if cf.startswith(f'[{seg}]') and cf.lower().endswith(('.jpg', '.png', '.jpeg')):
                            cover = os.path.join(covers_dir, cf)
                            break

            desc_parts = []
            if space:
                desc_parts.append(f'{streamer}的个人主页：{space}')
            if live:
                desc_parts.append(f'{streamer}的直播间：{live}')
            # 从 Data_source.txt 匹配 summary / real_timestamp（按 title 精确匹配）
            meta = title_meta.get(title_raw, {})
            real_timestamp = meta.get('real_timestamp', '')
            if real_timestamp:
                desc_parts.append('')
                desc_parts.append(f'直播时间：{real_timestamp}')
            summary = meta.get('summary', '')
            if summary:
                desc_parts.append('')
                desc_parts.append(summary)

            video_path = os.path.join(videos_dir, video_file)
            entry = {
                'video_path': video_path,
                'title': title,
                'desc': '\n'.join(desc_parts),
                'tags': tags,
                'tid': tid,
                'copyright': copyright_val,
                'cover_path': cover,
                'upload_status': 'pending',
                'upload_attempts': 0,
                'bvid': '',
                'aid': '',
                'last_error': '',
                'last_upload_at': '',
                'last_attempt_at': '',
            }
            entry.update({k: v for k, v in old_state.get(video_path, {}).items() if v not in (None, '')})
            upload_list.append(entry)
            self.log(f'  📎 [{seg}] {title}', 'info')

        with open(manifest_path, 'w', encoding='utf-8') as f:
            _json.dump(upload_list, f, ensure_ascii=False, indent=2)
        self.log(f'📄 投稿清单已生成: {manifest_path}', 'success')
        self.log(f'   ✅ {len(upload_list)} 个视频 / {sum(1 for u in upload_list if u["cover_path"])} 个有封面', 'success')

    def open_batch_upload(self):
        """打开批量投稿筛选窗口"""
        input_dir = self.global_input_dir.text().strip()
        if not input_dir or not os.path.isdir(input_dir):
            self.log('错误：请先在全局配置中填写有效输入目录', 'error')
            return
        manifest_path = os.path.join(input_dir, 'upload_manifest.json')
        if not os.path.exists(manifest_path):
            reply = QMessageBox.question(
                self, '提示',
                '该目录没有投稿清单 (upload_manifest.json)。\n\n'
                '可以点击「📄 根据文件生成投稿清单」按钮，\n'
                '基于切片目录的实际文件自动生成。\n\n'
                '是否先运行「一键流转」生成？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.run_pipeline()
            return
        dlg = BatchUploadDialog(self, input_dir, self.config)
        dlg.setStyleSheet('''
            QDialog { background: #2b2b3c; color: #ddd; }
            QCheckBox { color: #ddd; }
            QCheckBox::indicator { width: 20px; height: 20px; }
        ''')
        dlg.exec()

    def run_upload(self):
        """执行B站投稿"""
        video_path = self.upload_video.text().strip()
        if not video_path:
            # 尝试从切片目录自动找第一个 mp4
            clips_dir = self.clip_output_dir.text().strip()
            if clips_dir and os.path.isdir(clips_dir):
                import glob
                candidates = glob.glob(os.path.join(clips_dir, '*.mp4'))
                if candidates:
                    video_path = candidates[0]
                    self.log(f'自动选择视频: {os.path.basename(video_path)}', 'info')
            if not video_path:
                self.log('错误：请先选择视频文件，或确保切片目录存在mp4文件', 'error')
                return
        if not os.path.exists(video_path):
            self.log(f'错误：视频文件不存在: {video_path}', 'error')
            return
        
        title = self.upload_title.text().strip()
        if not title:
            self.log('错误：视频标题不能为空', 'error')
            return
        if len(title) > 80:
            self.log(f'错误：标题超过80字（当前{len(title)}字）', 'error')
            return
        
        desc = self.upload_desc.toPlainText().strip()
        tags_str = self.upload_tags.text().strip()
        tags = [t.strip() for t in tags_str.split(',') if t.strip()] if tags_str else []
        if len(tags) > 10:
            self.log(f'警告：标签超过10个，仅使用前10个', 'info')
            tags = tags[:10]
        
        tid = self.upload_tid.currentData()
        cover_path = self.upload_cover.text().strip() or None
        copyright_val = self.upload_copyright.currentData()
        source = self.upload_source.text().strip() if copyright_val == 2 else None
        no_reprint = 1 if self.upload_no_reprint.isChecked() else 0
        
        if copyright_val == 2 and source and len(source) > 200:
            self.log('警告：转载来源URL超过200字', 'info')
        
        params = {
            'video_path': video_path,
            'title': title,
            'desc': desc,
            'tags': tags,
            'tid': tid,
            'cover_path': cover_path,
            'copyright': copyright_val,
            'source': source,
            'no_reprint': no_reprint,
        }
        self.start_task('upload', params)
    
    def browse_file(self, line_edit, filter_str):
        """浏览文件"""
        path, _ = QFileDialog.getOpenFileName(self, '选择文件', line_edit.text() or '', filter_str)
        if path:
            line_edit.setText(path)
    
    def start_task(self, task_type, params):
        """启动任务"""
        if self.worker and self.worker.isRunning():
            self.log('已有任务在运行中', 'error')
            return
        
        self.save_config()
        
        self.worker = WorkerThread(task_type, params)
        self.worker.log_signal.connect(self.on_log)
        self.worker.progress_signal.connect(self.on_progress)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()
        
        self.stop_btn.setVisible(True)
        self.task_start_time = time.time()
        self.timer.start(1000)

        # 重置进度状态
        self._progress_history.clear()
        self._progress_completed = 0
        self._progress_total = 0
        self.progress_bar.setValue(0)
        self._update_progress_bar_color(0)
        self.current_file_label.setText('当前文件: -')
        self._update_compact_info()
        self._current_pipeline_step = -1
        self.step_indicator.set_active_step(-1)

        task_names = {
            'subtitles': '字幕生成',
            'llm': '高光提取',
            'clips': '视频切片',
            'subtitles_burn': '字幕烧录',
            'covers': '封面生成',
            'upload': 'B站投稿',
            'pipeline': '一键流转',
            'batch_upload': '批量投稿',
            'convert_flv': 'FLV 转 MP4',
            'portrait_landscape': '竖屏转横屏',
            'burn_danmu': '压制弹幕',
        }
        task_display = task_names.get(task_type, task_type)

        # 设置阶段标签（合并了原 status_label 的功能）
        self._update_stage_label(f'正在{task_display}...', True)

    def _update_compact_info(self):
        """更新紧凑信息行：合并原 info_grid 的 4 行为 1 行

        格式："3/5 · 耗时 2分15秒 · 剩 1分"
        没有数据时显示 "— · — · —"
        """
        parts = []
        # 进度计数
        if getattr(self, '_progress_total', 0) > 0:
            parts.append(f"{self._progress_completed}/{self._progress_total}")
        else:
            parts.append('—')

        # 耗时
        if self.task_start_time:
            elapsed = int(time.time() - self.task_start_time)
            m, s = elapsed // 60, elapsed % 60
            parts.append(f'耗时 {m}分{s}秒')
        else:
            parts.append('—')

        # 预计剩余
        eta = getattr(self, '_eta_text', '')
        parts.append(f'剩 {eta}' if eta else '—')

        self.compact_info_label.setText(' · '.join(parts))
    
    def _update_stage_label(self, text, active=True):
        """更新阶段标签（合并了原 status_label 的功能）"""
        if active:
            self.stage_label.setText(f'● {text}')
            self.stage_label.setStyleSheet(
                'font-size: 14px; font-weight: bold; color: #48dbfb; padding: 2px 0;')
        else:
            self.stage_label.setText(f'○ {text}')
            self.stage_label.setStyleSheet(
                'font-size: 14px; font-weight: bold; color: #a0a0b8; padding: 2px 0;')

    def _calculate_eta(self):
        """基于进度历史计算预计剩余时间，更新 _eta_text 和紧凑信息行"""
        now = time.time()
        completed = getattr(self, '_progress_completed', 0)
        total = getattr(self, '_progress_total', 0)

        if total <= 0 or completed <= 0 or not self.task_start_time:
            self._eta_text = ''
            self._update_compact_info()
            return

        self._progress_history.append((now, completed))

        eta_seconds = 0
        if len(self._progress_history) >= 3:
            first_time, first_completed = self._progress_history[0]
            elapsed = now - first_time
            done = completed - first_completed
            if done > 0 and elapsed > 0:
                speed = done / elapsed
                remaining = total - completed
                eta_seconds = remaining / speed if speed > 0 else 0
        else:
            elapsed = now - self.task_start_time
            if completed > 0 and elapsed > 0:
                speed = completed / elapsed
                remaining = total - completed
                eta_seconds = remaining / speed if speed > 0 else 0

        if eta_seconds <= 0:
            self._eta_text = ''
        elif eta_seconds < 60:
            self._eta_text = f'{int(eta_seconds)}秒'
        elif eta_seconds < 3600:
            self._eta_text = f'{int(eta_seconds // 60)}分{int(eta_seconds % 60)}秒'
        else:
            h = int(eta_seconds // 3600)
            m = int((eta_seconds % 3600) // 60)
            self._eta_text = f'{h}时{m}分'

        self._update_compact_info()
    
    def _update_progress_bar_color(self, percent):
        """根据进度百分比动态改变进度条渐变色

        优化：原版每次 on_progress 都 setStyleSheet 重新解析整段 QSS，
        高频进度更新时（如上传分片）会卡顿。改为三档缓存，
        只在跨档（0-30 / 30-70 / 70-100）时才 setStyleSheet。
        """
        if percent < 30:
            tier = 0
        elif percent < 70:
            tier = 1
        else:
            tier = 2
        # 同档不重复 setStyleSheet，避免 QSS 重解析开销
        if tier == self._progress_color_tier:
            return
        self._progress_color_tier = tier

        if tier == 0:
            self.progress_bar.setStyleSheet('''
                QProgressBar {
                    border: none; border-radius: 8px;
                    background: #3d3d56; height: 32px;
                    text-align: center; font-weight: bold;
                }
                QProgressBar::chunk {
                    border-radius: 8px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #ff6b6b, stop:1 #feca57);
                }
            ''')
        elif tier == 1:
            self.progress_bar.setStyleSheet('''
                QProgressBar {
                    border: none; border-radius: 8px;
                    background: #3d3d56; height: 32px;
                    text-align: center; font-weight: bold;
                }
                QProgressBar::chunk {
                    border-radius: 8px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #feca57, stop:1 #1dd1a1);
                }
            ''')
        else:
            self.progress_bar.setStyleSheet('''
                QProgressBar {
                    border: none; border-radius: 8px;
                    background: #3d3d56; height: 32px;
                    text-align: center; font-weight: bold;
                }
                QProgressBar::chunk {
                    border-radius: 8px;
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                        stop:0 #1dd1a1, stop:1 #00cec9);
                }
            ''')

    def stop_task(self):
        """中断任务"""
        if self.worker:
            self.worker.stop()
            self.log('正在中断任务...', 'info')
    
    def on_log(self, message, msg_type):
        """接收日志 — 入队等待批量刷新，不直接渲染

        WorkerThread 高频 emit log_signal 时，这里只是 deque.append，
        真正的 QTextEdit 渲染由 _log_flush_timer 50ms 批量执行。
        启动 flush 定时器（幂等，已在运行则无副作用）。
        """
        self._log_queue.append((message, msg_type))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _flush_log_queue(self):
        """批量刷新日志队列到 QPlainTextEdit

        把 deque 里积攒的多条日志拼成一段 HTML 一次 append，
        将 N 次 appendPlainText 调用合并为 1 次 appendHtml，
        显著减少 QPlainTextEdit 的重排版次数。
        队列空了就停定时器，无任务时零开销。
        """
        if not self._log_queue:
            self._log_flush_timer.stop()
            return

        color_map = {
            'info': '#aaa',
            'error': '#ff6b6b',
            'success': '#1dd1a1',
            'warning': '#feca57',
        }
        # 批量拼成一段 HTML，一次 append
        # QPlainTextEdit.appendHtml 会自动换行，无需手动 <br>
        parts = []
        ts = datetime.now().strftime('%H:%M:%S')
        while self._log_queue:
            message, msg_type = self._log_queue.popleft()
            color = color_map.get(msg_type, '#aaa')
            # 转义 HTML 特殊字符，防止日志里的 < > & 破坏渲染
            safe_msg = (message.replace('&', '&amp;')
                              .replace('<', '&lt;')
                              .replace('>', '&gt;'))
            parts.append(
                f'<span style="color: #feca57;">[{ts}]</span> '
                f'<span style="color: {color};">{safe_msg}</span>'
            )
        # 一次性追加，触发一次重排版
        self.log_text.appendHtml('<br>'.join(parts))

    def log(self, message, msg_type='info'):
        """添加日志（直接渲染，用于非高频场景如启动/手动操作）

        高频场景（WorkerThread stdout）走 on_log → 队列 → _flush_log_queue 批量渲染。
        低频场景（启动信息、按钮点击反馈）直接调用此方法，立即渲染。
        """
        # log_text 在 create_left_panel() 中创建，可能在 log() 首次调用时尚未就绪
        if not hasattr(self, 'log_text') or self.log_text is None:
            print(f'[LOG-{msg_type}] {message}')
            return
        if self._log_flush_timer.isActive() and self._log_queue:
            # 有批量刷新在进行时，并入队列避免乱序
            self._log_queue.append((message, msg_type))
            return
        timestamp = datetime.now().strftime('%H:%M:%S')
        color_map = {
            'info': '#aaa',
            'error': '#ff6b6b',
            'success': '#1dd1a1',
            'warning': '#feca57',
        }
        color = color_map.get(msg_type, '#aaa')
        safe_msg = (message.replace('&', '&amp;')
                          .replace('<', '&lt;')
                          .replace('>', '&gt;'))
        self.log_text.appendHtml(
            f'<span style="color: #feca57;">[{timestamp}]</span> '
            f'<span style="color: {color};">{safe_msg}</span>'
        )
    
    def on_progress(self, data):
        """接收进度"""
        if 'total_files' in data:
            completed = data.get('completed_files', 0)
            total = data['total_files']
            self._progress_completed = completed
            self._progress_total = total
            if total > 0:
                percent = int(completed / total * 100)
                self.progress_bar.setValue(percent)
                self._update_progress_bar_color(percent)
            self._update_compact_info()

        if 'current_file' in data:
            self.current_file_label.setText(f"当前文件: {data['current_file']}")

        # 步骤信息
        if 'step' in data:
            step_id = data['step']
            self._current_pipeline_step = step_id
            self.step_indicator.set_active_step(step_id)
            step_names = {1: '准备', 2: '字幕', 3: '高光', 4: '切片', 5: '烧录', 6: '封面', 7: '投稿'}
            step_name = step_names.get(step_id, '')
            if step_name:
                self._update_stage_label(f'步骤{step_id}: {step_name}', True)

        if 'stage' in data:
            self._update_stage_label(data['stage'], True)

        # 计算 ETA
        self._calculate_eta()
    
    def on_finished(self, success, message):
        """任务完成"""
        self.timer.stop()
        self.stop_btn.setVisible(False)

        if success:
            self._update_stage_label('✅ 全部完成', False)
            self.log(f'✅ {message}', 'success')
        else:
            self._update_stage_label('❌ 任务中断/失败', False)
            self.log(f'❌ {message}', 'error')

        self.progress_bar.setValue(100 if success else 0)
        self._update_progress_bar_color(100 if success else 0)
        self.current_file_label.setText('当前文件: -')
        self._eta_text = ''
        self._update_compact_info()
    
    def update_elapsed_time(self):
        """更新耗时（通过紧凑信息行显示，不再单独设 elapsed_label）"""
        if self.task_start_time:
            self._update_compact_info()

    def clear_log(self):
        """清空日志"""
        self._log_queue.clear()
        if self._log_flush_timer.isActive():
            self._log_flush_timer.stop()
        self.log_text.clear()
    
    def load_config(self):
        """加载配置"""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass
        return {}

    def _load_gui_config_for_pipeline(self):
        """一键流转专用：读取 gui_config.json 全局配置（AutoClipGUI 版，未使用）"""
        return self.load_config()

    def start_qr_login(self):
        """B站扫码登录 — 展示二维码，轮询直到扫码成功"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QMessageBox
        from PyQt6.QtCore import QTimer
        from PyQt6.QtGui import QPixmap
        from utils.bilibili_login import BilibiliQRLogin, save_credential_to_config, check_login_status
        
        qr_login = BilibiliQRLogin()
        try:
            qr_img_path, terminal_str = qr_login.generate()
        except Exception as e:
            QMessageBox.warning(self, '扫码登录失败', f'生成二维码失败:\n{e}')
            return
        
        dlg = QDialog(self)
        dlg.setWindowTitle('📱 B站扫码登录')
        dlg.setMinimumSize(360, 420)
        dlg.setStyleSheet('''
            QDialog { background: #35354a; color: #ddd; }
            QLabel { color: #ccc; }
        ''')
        
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(12)
        
        title_lbl = QLabel('请用 Bilibili APP 扫描二维码登录')
        title_lbl.setStyleSheet('font-size: 15px; font-weight: bold; color: #6c78fa;')
        title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(title_lbl)
        
        # 二维码图片
        qr_label = QLabel()
        qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap(qr_img_path)
        if pixmap.width() > 240:
            pixmap = pixmap.scaled(240, 240, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        qr_label.setPixmap(pixmap)
        vbox.addWidget(qr_label)
        
        # 状态文字
        status_lbl = QLabel('等待扫码...')
        status_lbl.setStyleSheet('font-size: 14px; color: #feca57; font-weight: bold;')
        status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        vbox.addWidget(status_lbl)
        
        # 手动取消按钮
        cancel_btn = QPushButton('取消')
        cancel_btn.setStyleSheet('''
            QPushButton { background: #4a4a5e; color: #ccc; border: none; padding: 8px; border-radius: 6px; font-size: 14px; }
            QPushButton:hover { background: #5a5a6e; }
        ''')
        cancel_btn.clicked.connect(dlg.reject)
        vbox.addWidget(cancel_btn)
        
        # 轮询定时器
        poll_timer = QTimer(dlg)
        _qr_login = qr_login
        _status_lbl = status_lbl
        
        def poll_qr_state():
            try:
                state = _qr_login.check()
                if state == 'scan':
                    _status_lbl.setText('📱 已扫描，请在手机上确认...')
                    _status_lbl.setStyleSheet('font-size: 14px; color: #00cec9; font-weight: bold;')
                elif state == 'confirm':
                    _status_lbl.setText('✅ 已确认，正在获取登录信息...')
                    _status_lbl.setStyleSheet('font-size: 14px; color: #00b894; font-weight: bold;')
                elif state == 'done':
                    _status_lbl.setText('🎉 登录成功！')
                    _status_lbl.setStyleSheet('font-size: 14px; color: #1dd1a1; font-weight: bold;')
                    poll_timer.stop()
                    cred = _qr_login.get_credential()
                    if cred:
                        try:
                            save_credential_to_config(cred)
                        except Exception:
                            pass
                    self._update_upload_login_status()
                    self.log('📱 B站扫码登录完成', 'success')
                    QTimer.singleShot(1500, dlg.accept)
                elif state == 'timeout':
                    _status_lbl.setText('⏰ 二维码已过期，请关闭后重新扫码')
                    _status_lbl.setStyleSheet('font-size: 14px; color: #ff6b6b; font-weight: bold;')
                    poll_timer.stop()
            except Exception as e:
                _status_lbl.setText(f'轮询异常: {str(e)[:30]}')
                _status_lbl.setStyleSheet('font-size: 12px; color: #ff6b6b;')
                poll_timer.stop()
        
        poll_timer.timeout.connect(poll_qr_state)
        poll_timer.start(1500)
        
        dlg.exec()
    
    def refresh_bili_login_status(self):
        """刷新 B站登录状态，检查 cookie 有效性并获取用户信息"""
        from PyQt6.QtWidgets import QMessageBox
        from utils.bilibili_login import check_and_refresh_credential
        
        self.global_bili_login_label.setText('B站 ⏳ 检查中...')
        self.global_bili_login_label.setStyleSheet('color: #feca57; font-weight: bold; font-size: 14px; padding: 4px 0;')
        
        result = check_and_refresh_credential()
        status = result.get('status', 'error')
        
        if status == 'ok':
            uname = result.get('uname', '未知')
            level = result.get('level', 0)
            vip = ' 大会员' if result.get('vip_status') else ''
            self.global_bili_login_label.setText(f'B站 ✅ {uname} (Lv.{level}{vip})')
            self.global_bili_login_label.setStyleSheet('color: #1dd1a1; font-weight: bold; font-size: 14px; padding: 4px 0;')
            uid = result.get('uid', '?')
            self.global_bili_login_label.setToolTip(f'UID: {uid} | Cookie 有效，无需刷新')
            self.log(f'🔑 B站已登录: {uname} (Lv.{level}{vip}, UID:{uid})', 'success')
        elif status == 'refreshed':
            uname = result.get('uname', '未知')
            level = result.get('level', 0)
            vip = ' 大会员' if result.get('vip_status') else ''
            self.global_bili_login_label.setText(f'B站 ✅ {uname} (Lv.{level}{vip})')
            self.global_bili_login_label.setStyleSheet('color: #1dd1a1; font-weight: bold; font-size: 14px; padding: 4px 0;')
            uid = result.get('uid', '?')
            self.global_bili_login_label.setToolTip(f'UID: {uid} | Cookie 已自动刷新并保存')
            self.log(f'🔄 B站 Cookie已自动刷新: {uname} (Lv.{level}{vip}, UID:{uid})', 'success')
        elif status == 'not_configured':
            self.global_bili_login_label.setText('B站 🔒 未登录')
            self.global_bili_login_label.setStyleSheet('color: #feca57; font-weight: bold; font-size: 14px; padding: 4px 0;')
            self.global_bili_login_label.setToolTip('请扫码登录或粘贴Cookie')
            self.log('⚠️ B站未配置登录凭据', 'error')
            QMessageBox.information(self, '未配置', '尚未配置 B站登录凭据。\n请使用「📱 扫码登录」或「🔑 粘贴Cookie」进行登录。')
        else:
            self.global_bili_login_label.setText('B站 ❌ 已过期')
            self.global_bili_login_label.setStyleSheet('color: #ff6b6b; font-weight: bold; font-size: 14px; padding: 4px 0;')
            error = result.get('error', '凭据已过期')
            self.global_bili_login_label.setToolTip(f'Cookie 已过期: {error}')
            self.log(f'❌ B站 Cookie已过期: {error}', 'error')
            QMessageBox.warning(self, '凭据过期', f'B站 Cookie 已过期，请重新登录。\n\n错误: {error}\n\n点击确定后请使用「📱 扫码登录」或「🔑 粘贴Cookie」重新授权。')

    def show_cookie_dialog(self):
        """B站Cookie登录弹窗 — 手动粘贴方式"""
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QTextEdit, QPushButton, QLabel
        
        dlg = QDialog(self)
        dlg.setWindowTitle('B站Cookie登录')
        dlg.setMinimumSize(520, 380)
        dlg.setStyleSheet('''
            QDialog { background: #35354a; color: #ddd; }
            QLabel { color: #ccc; }
            QTextEdit { background: #16213e; color: #ddd; border: 1px solid #444; border-radius: 6px; padding: 8px; font-family: Consolas, monospace; font-size: 11px; }
        ''')
        
        vbox = QVBoxLayout(dlg)
        vbox.setSpacing(12)
        
        title_lbl = QLabel('🔑 粘贴浏览器 Cookie 字符串，程序自动解析关键字段')
        title_lbl.setStyleSheet('font-size: 14px; font-weight: bold; color: #feca57;')
        vbox.addWidget(title_lbl)
        
        help_lbl = QLabel('打开B站 → F12 → 应用程序/Application → Cookies → bilibili.com → 复制整段Cookie（以SESSDATA、bili_jct、buvid3为核心）')
        help_lbl.setWordWrap(True)
        help_lbl.setStyleSheet('color: #a0a0b8; font-size: 12px; padding: 4px 0;')
        vbox.addWidget(help_lbl)
        
        self._cookie_text = QTextEdit()
        self._cookie_text.setPlaceholderText('SESSDATA=xxx; bili_jct=xxx; buvid3=xxx; ...')
        self._cookie_text.setMinimumHeight(150)
        vbox.addWidget(self._cookie_text)
        
        result_lbl = QLabel('')
        result_lbl.setWordWrap(True)
        result_lbl.setStyleSheet('color: #1dd1a1; font-size: 12px; padding: 4px;')
        self._cookie_result = result_lbl
        vbox.addWidget(result_lbl)
        
        parse_btn = QPushButton('解析并保存')
        parse_btn.setStyleSheet('''
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #ff6b6b, stop:1 #feca57);
                color: white;
                border: none;
                padding: 8px;
                font-size: 14px;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover { background: #ee5a6f; }
        ''')
        parse_btn.clicked.connect(lambda: self._parse_and_save_cookies(result_lbl))
        vbox.addWidget(parse_btn)
        
        dlg.exec()
    
    def _parse_and_save_cookies(self, result_label):
        """解析Cookie字符串并保存到配置文件"""
        cookie_str = self._cookie_text.toPlainText().strip()
        if not cookie_str:
            result_label.setText('⚠️ 请先粘贴 Cookie 字符串')
            result_label.setStyleSheet('color: #ff6b6b; font-size: 12px; padding: 4px;')
            return
        
        found = {}
        # 处理换行和分号分隔
        for item in cookie_str.replace('\n', ';').split(';'):
            item = item.strip()
            if '=' not in item:
                continue
            key, _, value = item.partition('=')
            key = key.strip()
            value = value.strip()
            if key == 'SESSDATA':
                found['sessdata'] = unquote(value)
            elif key == 'bili_jct':
                found['bili_jct'] = value
            elif key == 'buvid3':
                found['buvid3'] = value
            elif key == 'buvid4':
                found['buvid4'] = unquote(value)
            elif key == 'DedeUserID':
                found['dedeuserid'] = value
        
        if not found.get('sessdata') or not found.get('bili_jct'):
            result_label.setText('❌ 未找到有效的 SESSDATA 和 bili_jct，请检查Cookie内容')
            result_label.setStyleSheet('color: #ff6b6b; font-size: 12px; padding: 4px;')
            return
        
        # 保存到 gui_config.json
        import json
        config_path = CONFIG_FILE
        config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            except:
                pass
        # 合并保存：保留粘贴内容里没有的字段（buvid4/ac_time_value 等），避免被整体覆盖清空
        bili_section = dict(config.get('bilibili') or {})
        bili_section.update(found)
        config['bilibili'] = bili_section
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            uid = found.get('dedeuserid', '?')
            msg = f'✅ Cookie解析成功！UID={uid} | SESSDATA={found["sessdata"][:20]}... | bili_jct={found["bili_jct"][:8]}...'
            result_label.setText(msg)
            result_label.setStyleSheet('color: #1dd1a1; font-size: 12px; padding: 4px;')
            self._update_upload_login_status()
            self.log('🔑 B站 Cookie 粘贴登录成功', 'success')
        except Exception as e:
            result_label.setText(f'❌ 保存配置失败: {e}')
            result_label.setStyleSheet('color: #ff6b6b; font-size: 12px; padding: 4px;')
    
    def _update_upload_login_status(self):
        """更新B站登录状态显示（全局）"""
        from utils.bilibili_login import check_and_refresh_credential
        config = self.load_config()
        bili = config.get('bilibili', {})
        if bili.get('sessdata') and bili.get('bili_jct'):
            result = check_and_refresh_credential()
            status = result.get('status', 'error')
            if status in ('ok', 'refreshed'):
                uname = result.get('uname', '')
                level = result.get('level', 0)
                vip = ' 大会员' if result.get('vip_status') else ''
                uid = result.get('uid', '?')
                if uname:
                    self.global_bili_login_label.setText(f'B站 ✅ {uname} (Lv.{level}{vip})')
                    self.global_bili_login_label.setToolTip(f'UID: {uid}')
                else:
                    self.global_bili_login_label.setText('B站 ✅ 已登录')
                    self.global_bili_login_label.setToolTip(f'UID: {uid}')
                self.global_bili_login_label.setStyleSheet('color: #1dd1a1; font-weight: bold; font-size: 14px; padding: 4px 0;')
            else:
                uid = bili.get('dedeuserid', '?')
                self.global_bili_login_label.setText('B站 ⚠️ Cookie可能已过期')
                self.global_bili_login_label.setToolTip(f'UID: {uid} | 请使用「🔄 刷新信息」检查')
                self.global_bili_login_label.setStyleSheet('color: #feca57; font-weight: bold; font-size: 14px; padding: 4px 0;')
                self.log('⚠️ B站 Cookie可能已过期，请刷新', 'error')
        else:
            self.global_bili_login_label.setText('B站 🔒 未登录')
            self.global_bili_login_label.setToolTip('点击下方按钮登录B站账号')
            self.global_bili_login_label.setStyleSheet('color: #feca57; font-weight: bold; font-size: 14px; padding: 4px 0;')

    def save_config(self):
        """保存配置"""
        # 先加载现有配置，保留 bilibili 等非GUI直接管理的字段
        existing = self.load_config()
        config = {} if not existing else dict(existing)
        # 分 provider 保存 key：把当前输入框值归到当前 provider，再合并进 llm_api_keys
        cur_provider = self.llm_provider.currentData() or 'siliconflow'
        llm_keys = dict(config.get('llm_api_keys') or {})
        llm_keys[cur_provider] = self.global_api_key.text().strip()
        for prov in ('siliconflow', 'deepseek', 'opencode-go', 'zhipu', 'zhipu-coding'):
            llm_keys.setdefault(prov, '')
        config.update({
            'global_input_dir': self.global_input_dir.text(),
            'global_streamer': self.global_streamer.currentText(),
            'global_api_key': self.global_api_key.text(),
            'global_model': self.global_model.currentText(),
            'llm_provider': cur_provider,
            'llm_api_keys': llm_keys,
            'whisper_model': self.whisper_model.currentData(),
            'whisper_lang': self.whisper_lang.currentText(),
            'whisper_auto_lang': self.whisper_auto_lang.isChecked(),
            'llm_method': self.llm_method.currentIndex(),
            'llm_prompts_dir': self.llm_prompts_dir.text(),
            'cover_style': self.cover_style.currentIndex(),
            'multi_danmu': self.multi_danmu.isChecked(),
            'multi_llm': self.multi_llm.isChecked(),
            'multi_emotion': self.multi_emotion.isChecked(),
            'upload_video': self.upload_video.text(),
            'upload_title': self.upload_title.text(),
            'upload_desc': self.upload_desc.toPlainText(),
            'upload_tags': self.upload_tags.text(),
            'upload_tid_idx': self.upload_tid.currentIndex(),
            'upload_copyright_idx': self.upload_copyright.currentIndex(),
            'upload_source': self.upload_source.text(),
            'upload_no_reprint': self.upload_no_reprint.isChecked(),
            # Step 2 字幕识别
            'funasr_model': self.funasr_model.currentData(),
            'funasr_speaker': self.funasr_speaker.isChecked(),
            # Step 5 字幕烧录 - 引擎与模型
            'subtitle_asr_engine': self.subtitle_asr_engine.currentData(),
            'subtitle_asr_model': self.subtitle_asr_model.currentData(),
            'subtitle_whisper_model': self.subtitle_whisper_model.currentData(),
            'subtitle_qwen3_model': self.subtitle_qwen3_model.currentData(),
            'subtitle_asr_lang': self.subtitle_asr_lang.currentText(),
            'subtitle_skip_asr': self.subtitle_skip_asr.isChecked(),
            'subtitle_enabled': self.subtitle_enabled.isChecked(),
            # Step 5 字幕烧录 - 样式
            'subtitle_font': self.subtitle_font.currentText(),
            'subtitle_size': self.subtitle_size.value(),
            'subtitle_color': self.subtitle_color.currentText(),
            'subtitle_outline': self.subtitle_outline.currentText(),
            'subtitle_outline_w': self.subtitle_outline_w.value(),
            'subtitle_shadow': self.subtitle_shadow.isChecked(),
            'subtitle_margin': self.subtitle_margin.value(),
            'subtitle_orient': self.subtitle_orient.currentText(),
            # Step 6 封面
            'cover_font': self.cover_font.currentData() or self.cover_font.currentText(),
            # Step 4 切片
            'clip_output_dir': self.clip_output_dir.text(),
        })
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'保存配置失败: {e}')
    
    def apply_config(self):
        """应用配置"""
        cfg = self.config
        
        self.global_input_dir.setText(cfg.get('global_input_dir', ''))
        streamer = cfg.get('global_streamer', '栞栞Shiori')
        self.global_streamer.setCurrentText(streamer)
        # 初始化「分 provider 的 key 缓存」：llm_api_keys 优先，旧 global_api_key 视为 siliconflow 的 key
        self._llm_keys = dict(cfg.get('llm_api_keys') or {})
        legacy_key = cfg.get('global_api_key', '')
        if legacy_key and not self._llm_keys.get('siliconflow'):
            self._llm_keys['siliconflow'] = legacy_key
        for prov in ('siliconflow', 'deepseek', 'opencode-go', 'zhipu', 'zhipu-coding'):
            self._llm_keys.setdefault(prov, '')
        provider = cfg.get('llm_provider', 'siliconflow')
        self._last_provider = provider
        pidx = self.llm_provider.findData(provider)
        if pidx >= 0:
            self.llm_provider.setCurrentIndex(pidx)
        # 载入当前 provider 对应的 key 与保存的模型（放到 setCurrentIndex 之后，避免被切换回调覆盖）
        self.global_api_key.setText(self._llm_keys.get(provider, ''))
        self.global_model.setCurrentText(cfg.get('global_model', 'deepseek-ai/DeepSeek-V4-Flash'))
        
        whisper_path = cfg.get('whisper_model', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))
        idx = self.whisper_model.findData(whisper_path)
        if idx >= 0:
            self.whisper_model.setCurrentIndex(idx)
        self.whisper_lang.setCurrentText(cfg.get('whisper_lang', 'zh'))
        self.whisper_auto_lang.setChecked(cfg.get('whisper_auto_lang', False))
        
        self.llm_method.setCurrentIndex(cfg.get('llm_method', 0))
        self.llm_prompts_dir.setText(cfg.get('llm_prompts_dir', ''))

        self.multi_danmu.setChecked(cfg.get('multi_danmu', True))
        self.multi_llm.setChecked(cfg.get('multi_llm', True))
        self.multi_emotion.setChecked(cfg.get('multi_emotion', False))

        self.cover_style.setCurrentIndex(cfg.get('cover_style', 0))
        
        # 步骤7 投稿配置
        self.upload_video.setText(cfg.get('upload_video', ''))
        self.upload_title.setText(cfg.get('upload_title', ''))
        self.upload_desc.setPlainText(cfg.get('upload_desc', ''))
        self.upload_tags.setText(cfg.get('upload_tags', ''))
        self.upload_tid.setCurrentIndex(cfg.get('upload_tid_idx', 0))
        self.upload_copyright.setCurrentIndex(cfg.get('upload_copyright_idx', 0))
        self.upload_source.setText(cfg.get('upload_source', ''))
        self.upload_no_reprint.setChecked(cfg.get('upload_no_reprint', True))
        
        # Step 2 字幕识别 - FunASR 模型
        funasr_m = cfg.get('funasr_model', 'sensevoice')
        idx = self.funasr_model.findData(funasr_m)
        if idx >= 0:
            self.funasr_model.setCurrentIndex(idx)
        self.funasr_speaker.setChecked(cfg.get('funasr_speaker', False))
        
        # Step 5 字幕烧录 - 引擎和模型
        se = cfg.get('subtitle_asr_engine', 'whisper')
        idx = self.subtitle_asr_engine.findData(se)
        if idx >= 0:
            self.subtitle_asr_engine.setCurrentIndex(idx)
        sm = cfg.get('subtitle_asr_model', 'sensevoice')
        idx = self.subtitle_asr_model.findData(sm)
        if idx >= 0:
            self.subtitle_asr_model.setCurrentIndex(idx)
        sw = cfg.get('subtitle_whisper_model', os.path.join(WHISPER_MODEL_DIR, 'large-v3-turbo.pt'))
        idx = self.subtitle_whisper_model.findData(sw)
        if idx >= 0:
            self.subtitle_whisper_model.setCurrentIndex(idx)
        sq = cfg.get('subtitle_qwen3_model', 'Qwen3-ASR-1.7B')
        idx = self.subtitle_qwen3_model.findData(sq)
        if idx >= 0:
            self.subtitle_qwen3_model.setCurrentIndex(idx)
        self.subtitle_asr_lang.setCurrentText(cfg.get('subtitle_asr_lang', 'zh'))
        self.subtitle_skip_asr.setChecked(cfg.get('subtitle_skip_asr', False))
        # 面板"启用字幕烧录"：始终跟随当前主播配置 step5_burn.enabled
        # （不读 gui_config 的 subtitle_enabled，避免旧值覆盖主播配置）
        _streamer = self.global_streamer.currentText().strip()
        _scfg_path = os.path.join(PROJECT_ROOT, 'configs', 'streamers', f'{_streamer}.json')
        _enabled = True
        try:
            if os.path.exists(_scfg_path):
                with open(_scfg_path, 'r', encoding='utf-8') as f:
                    _scfg = json.load(f)
                _enabled = _scfg.get('step5_burn', {}).get('enabled', True)
        except Exception:
            pass
        self.subtitle_enabled.setChecked(bool(_enabled))
        
        # Step 5 字幕烧录 - 样式
        self.subtitle_font.setCurrentText(cfg.get('subtitle_font', 'MaokenAssortedSans'))
        self.subtitle_size.setValue(cfg.get('subtitle_size', 80))
        self.subtitle_color.setCurrentText(cfg.get('subtitle_color', '&H00FFFFFF'))
        self.subtitle_outline.setCurrentText(cfg.get('subtitle_outline', '&H00000000'))
        self.subtitle_outline_w.setValue(cfg.get('subtitle_outline_w', 8))
        self.subtitle_shadow.setChecked(cfg.get('subtitle_shadow', True))
        self.subtitle_margin.setValue(cfg.get('subtitle_margin', 100))
        self.subtitle_orient.setCurrentText(cfg.get('subtitle_orient', 'horizontal'))
        
        # Step 4 切片输出目录
        cod = cfg.get('clip_output_dir', '')
        if cod:
            self.clip_output_dir.setText(cod)

        # 字体列表加载已移至 _apply_config_async（showEvent 后）
        # 避免在 __init__ → apply_config 链路里阻塞窗口显示

        # 应用全局配置
        self.apply_global_config()
    
    def closeEvent(self, event):
        """关闭时保存配置"""
        # 停止日志刷新定时器，防止退出后回调已销毁的控件
        if hasattr(self, '_log_flush_timer') and self._log_flush_timer.isActive():
            self._log_flush_timer.stop()
        if hasattr(self, 'timer') and self.timer.isActive():
            self.timer.stop()
        self.save_config()
        if self.worker and self.worker.isRunning():
            reply = QMessageBox.question(
                self, '确认',
                '任务正在运行中，确定要退出吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.worker.stop_and_wait()
                event.accept()
            else:
                event.ignore()
        else:
            event.accept()

def main(window_class=None):
    """启动 GUI 应用（可传入子类以复用主题/字体配置）"""
    if window_class is None:
        window_class = AutoClipGUI
    
    app = QApplication(sys.argv)

    # 移动端远程操控 — 全局字体放大
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)

    # 暗色主题
    app.setStyle('Fusion')
    app.setStyleSheet(
        '''
        QGroupBox {
            font-size: 14px;
            font-weight: bold;
            padding-top: 20px;
            margin-top: 6px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 4px;
        }
        '''
    )
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor('#2b2b3c'))
    palette.setColor(QPalette.ColorRole.WindowText, QColor('#e0e0e0'))
    palette.setColor(QPalette.ColorRole.Base, QColor('#35354a'))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#3d3d56'))
    palette.setColor(QPalette.ColorRole.Text, QColor('#e0e0e0'))
    palette.setColor(QPalette.ColorRole.Button, QColor('#3d3d56'))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor('#e0e0e0'))
    app.setPalette(palette)
    
    window = window_class()
    if window_class.SHOW_MAXIMIZED:
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()
