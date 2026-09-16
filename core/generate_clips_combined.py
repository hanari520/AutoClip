# -*- coding: utf-8 -*-
"""
AutoClip 三模态高光切片生成脚本
支持三种分析方法：
  1. danmu  - 纯弹幕热度分析
  2. llm    - 纯LLM语义分析
  3. combined - 弹幕+LLM双保险分析（推荐）

用法:
  python generate_clips_combined.py --method danmu -i "输入目录"
  python generate_clips_combined.py --method llm -i "输入目录" -s "主播名"
  python generate_clips_combined.py --method combined -i "输入目录" -s "主播名"
"""
import os
import sys
import json
import glob
import re
import time
import subprocess
import argparse
from pathlib import Path
from collections import defaultdict

sys.stdout.reconfigure(encoding='utf-8')

# 添加项目根目录到路径，支持 from utils.xxx import xxx
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'utils'))
# 脚本所在 core/ 目录也入路径，保证以包方式导入本模块时 from convert_danmaku_xml import 仍可解析
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ============================================================
# 配置区域
# ============================================================

DEFAULT_CONFIG = {
    "input_dir": r"",
    "streamer_name": "",
    "prompts_dir": os.path.join(PROJECT_ROOT, 'configs', 'streamer_titles'),
    "output_file": "Data_source.txt",
    "llm": {
        "api_key": "",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "max_tokens": 8192,
        "temperature": 0.7
    }
}

# LLM 供应商 → API 端点（统一从 utils.llm_config 读取，保证 GUI/CLI 一致）
from utils.llm_config import resolve_llm_base_url

# 弹幕分析配置
DANMU_CONFIG = {
    "top_n": 25,           # 初始筛选多少个高光片段
    "window_size": 10,     # 密度计算窗口(秒)
    "min_density": 5,      # 最小热度阈值
    "merge_threshold": 10, # 合并间隔(秒)
    "min_duration": 10,    # 最小保留时长
    "max_duration": 90,    # 最大保留时长
}

# 弹幕权重规则
DANMAKU_WEIGHTS = [
    (r'警告|绷|笑死|名场面|锐评|蚌埠住了', 2.0),
    (r'[?\uff1f]', 1.8),
    (r'(哈{2,}|h{3,}|[啊]{2,})', 1.5),
    (r'可爱捏|急了|牛', 1.2)
]
LONG_TEXT_BONUS = (15, 1.2)


# ============================================================
# 工具函数
# ============================================================

def parse_args():
    parser = argparse.ArgumentParser(description='AutoClip 三模态高光切片生成工具')
    parser.add_argument('--method', '-m', type=str, default='combined',
                        choices=['danmu', 'llm', 'combined', 'emotion', 'multi'],
                        help='分析方法: danmu=纯弹幕, llm=纯LLM, combined=弹幕+LLM双保险(默认), emotion=情绪驱动分析, multi=多保险融合')
    parser.add_argument('--multi-sources', type=str,
                        help='多保险来源(逗号分隔): danmu,llm,emotion (默认: danmu,llm)')
    parser.add_argument('--input-dir', '-i', type=str,
                        help='输入目录（包含视频、ass、srt文件）')
    parser.add_argument('--streamer', '-s', type=str,
                        help='主播名称（LLM和combined模式需要）')
    parser.add_argument('--prompts-dir', '-p', type=str,
                        help='自定义提示词目录')
    parser.add_argument('--api-key', '-k', type=str,
                        help='API Key')
    parser.add_argument('--model', type=str,
                        help='模型名称')
    parser.add_argument('--base-url', type=str,
                        help='LLM API 端点(如 https://api.siliconflow.cn/v1 或 https://api.deepseek.com/v1)；不传时从 configs/gui_config.json 的 llm_provider 读取，再兜底默认硅基流动')
    parser.add_argument('--output', '-o', type=str,
                        help='输出文件名')
    parser.add_argument('--top-n', type=int, default=25,
                        help='弹幕模式：筛选多少个片段(默认25)')
    return parser.parse_args()


def get_config():
    args = parse_args()
    config = DEFAULT_CONFIG.copy()
    
    if args.input_dir:
        config['input_dir'] = args.input_dir
    if args.streamer:
        config['streamer_name'] = args.streamer
    if args.prompts_dir:
        config['prompts_dir'] = args.prompts_dir
    if args.api_key:
        config['llm']['api_key'] = args.api_key
    if args.model:
        config['llm']['model'] = args.model
    config['llm']['base_url'] = resolve_llm_base_url(args.base_url)
    if args.output:
        config['output_file'] = args.output
    
    config['method'] = args.method
    config['top_n'] = args.top_n
    if args.multi_sources:
        config['multi_sources'] = [s.strip() for s in args.multi_sources.split(',')]
    else:
        config['multi_sources'] = ['danmu', 'llm']  # 默认
    return config


# ============================================================
# 文件扫描
# ============================================================

def scan_files(input_dir):
    """扫描目录下的所有相关文件"""
    print(f"\n[扫描目录] {input_dir}")
    
    if not os.path.exists(input_dir):
        print(f"❌ 错误: 目录不存在")
        return None
    
    files = os.listdir(input_dir)
    
    # 视频文件
    video_exts = ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv']
    video_files = sorted([f for f in files if any(f.lower().endswith(ext) for ext in video_exts)])

    # 弹幕版优先：同目录存在 xxx-弹幕版.mp4 时跳过 xxx.mp4
    from utils.video_filter import filter_danmaku_versions_in_dir
    video_files = filter_danmaku_versions_in_dir(input_dir, video_files)
    
    # 字幕文件
    srt_files = [f for f in files if f.lower().endswith('.srt')]
    
    # 弹幕文件
    ass_files = [f for f in files if f.lower().endswith('.ass')]
    
    # 情绪数据文件 (emotion2vec生成)
    emotion_files = [f for f in files if f.lower().endswith('_emotion.json')]
    
    # 说话人数据文件 (FunASR生成)
    speaker_files = [f for f in files if f.lower().endswith('_speaker.json')]
    
    print(f"  视频: {len(video_files)} 个")
    print(f"  字幕: {len(srt_files)} 个")
    print(f"  弹幕: {len(ass_files)} 个")
    if emotion_files:
        print(f"  情绪数据: {len(emotion_files)} 个 (emotion2vec)")
    if speaker_files:
        print(f"  说话人数据: {len(speaker_files)} 个 (FunASR)")
    
    return {
        'videos': video_files,
        'srts': srt_files,
        'asses': ass_files,
        'emotions': emotion_files,
        'speakers': speaker_files
    }


def match_files(video_files, srt_files, ass_files, emotion_files=None, speaker_files=None):
    """匹配视频-字幕-弹幕-情绪-说话人文件"""
    matched = []
    emotion_files = emotion_files or []
    speaker_files = speaker_files or []
    
    for video in video_files:
        base = os.path.splitext(video)[0]
        
        # 找对应的srt
        srt_match = None
        for srt in srt_files:
            srt_base = os.path.splitext(srt)[0]
            if srt_base == base or srt_base == video:
                srt_match = srt
                break
        
        # 找对应的ass（按文件名前缀匹配）
        ass_match = None
        for ass in ass_files:
            # 取ass文件名前8个字符或时间戳部分匹配
            ass_base = os.path.splitext(ass)[0]
            if base in ass_base or ass_base in base or base[:8] == ass_base[:8]:
                ass_match = ass
                break
        
        # 找对应的情绪数据文件
        emotion_match = None
        for ef in emotion_files:
            ef_base = os.path.splitext(ef)[0].replace('_emotion', '')
            if ef_base == base:
                emotion_match = ef
                break
        
        # 找对应的说话人数据文件
        speaker_match = None
        for sf in speaker_files:
            sf_base = os.path.splitext(sf)[0].replace('_speaker', '')
            if sf_base == base:
                speaker_match = sf
                break
        
        matched.append({
            'video': video,
            'srt': srt_match,
            'ass': ass_match,
            'emotion': emotion_match,
            'speaker': speaker_match,
            'prefix': get_file_prefix(video)
        })
    
    return matched


def get_file_prefix(filename):
    """返回视频完整文件名（不含扩展名），作为 segment 标识。

    原实现提取时间戳前缀（如 01-04-03）或前 8 字符，导致无时间戳命名
    （如 分集_2026-08-09_【直播回放】...）时 segment 不完整、切片匹配失败。
    现统一使用完整文件名，保证 generate_clips.py 的精确匹配。
    real_timestamp 仍由 _parse_segment_start_seconds 从文件名前缀解析。
    """
    return os.path.splitext(filename)[0]


def get_video_duration(video_path):
    """用 ffprobe 获取视频时长（秒），失败返回 0"""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
             '-of', 'default=noprint_wrappers=1:nokey=1', video_path],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except Exception:
        pass
    return 0


# 高光数量推荐规则：每 30 分钟产出 1-3 个，按时长线性递推（仅作 LLM 软提示，无硬截断）
CLIP_UNIT_SECONDS = 30 * 60


def compute_clip_range(duration_seconds):
    """按每 30 分钟 1-3 个高光线性递推推荐数量区间（时长四舍五入到最近的 30 分钟档）"""
    if duration_seconds <= 0:
        return 1, 3
    units = max(1, round(duration_seconds / CLIP_UNIT_SECONDS))
    return units, units * 3


# ============================================================
# 弹幕分析模块
# ============================================================

def parse_ass_time(time_str):
    try:
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except:
        return 0


def parse_srt_time(time_str):
    try:
        time_str = time_str.replace(',', '.')
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except:
        return 0


def load_danmaku(ass_path):
    """加载ASS弹幕文件"""
    if not os.path.exists(ass_path):
        return []
    
    try:
        with open(ass_path, 'r', encoding='utf-8-sig') as f:
            content = f.read()
    except:
        return []
    
    pattern = r'Dialogue:\s*(\d+),(\d+:\d+:\d+\.\d+),(\d+:\d+:\d+\.\d+),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),([^,]*),(.*)'
    matches = re.findall(pattern, content)
    
    danmaku_data = []
    for match in matches:
        try:
            start_time = parse_ass_time(match[1])
            raw_text = match[9]
            clean_text = re.sub(r'\{[^}]*\}', '', raw_text).strip()
            clean_text = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_text)
            if clean_text and start_time > 0:
                danmaku_data.append({'time': start_time, 'text': clean_text})
        except:
            continue
    
    danmaku_data.sort(key=lambda x: x['time'])
    return danmaku_data


def get_danmaku_weight(text):
    weight = 1.0
    for pattern, score in DANMAKU_WEIGHTS:
        if re.search(pattern, text, re.IGNORECASE):
            weight = max(weight, score)
    limit, bonus = LONG_TEXT_BONUS
    if len(text) >= limit:
        weight = max(weight, bonus)
    return weight


def calculate_density(danmaku_data, window_size=10):
    """计算弹幕密度热力图"""
    if not danmaku_data:
        return {}, {}
    
    raw_score = defaultdict(float)
    raw_count = defaultdict(int)
    
    for d in danmaku_data:
        t = int(d['time'])
        weight = get_danmaku_weight(d['text'])
        raw_score[t] += weight
        raw_count[t] += 1
    
    max_time = max(raw_score.keys())
    min_time = min(raw_score.keys())
    
    score_map = {}
    count_map = {}
    
    for t in range(min_time, max_time + 1):
        curr_score = 0
        curr_count = 0
        for i in range(window_size):
            if (t + i) in raw_score:
                curr_score += raw_score[t + i]
                curr_count += raw_count[t + i]
        if curr_score > 0:
            score_map[t] = curr_score
            count_map[t] = curr_count
    
    return score_map, count_map


def find_highlights_by_danmu(danmaku_data, config=None):
    """弹幕分析法找高光片段"""
    if not danmaku_data:
        return []
    
    cfg = config or DANMU_CONFIG
    score_map, count_map = calculate_density(danmaku_data, cfg['window_size'])
    
    if not score_map:
        return []
    
    # 自适应阈值
    all_scores = sorted(score_map.values(), reverse=True)
    p90 = all_scores[int(len(all_scores) * 0.1)]
    adaptive_min = max(cfg['min_density'], p90 * 0.5)
    
    # 筛选高分窗口
    high_windows = [(t, s) for t, s in score_map.items() if s >= adaptive_min]
    high_windows.sort(key=lambda x: x[1], reverse=True)
    
    if not high_windows:
        return []
    
    # 生成原始片段
    raw_highlights = []
    lookback = 5
    
    for start_time, score in high_windows[:cfg['top_n'] * 5]:
        raw_highlights.append({
            'start': max(0, start_time - lookback),
            'end': start_time + cfg['window_size'] + lookback,
            'score': score,
            'count': count_map.get(start_time, 0)
        })
    
    raw_highlights.sort(key=lambda x: x['start'])
    
    # 合并相邻片段
    merged = []
    for h in raw_highlights:
        if not merged:
            merged.append(h)
            continue
        last = merged[-1]
        gap = h['start'] - last['end']
        if gap <= cfg['merge_threshold']:
            last['end'] = max(last['end'], h['end'])
            last['score'] = max(last['score'], h['score'])
            last['count'] += h['count']
            if last['end'] - last['start'] > cfg['max_duration']:
                last['end'] = last['start'] + cfg['max_duration']
        else:
            merged.append(h)
    
    # 过滤时长
    final = []
    for h in merged:
        duration = h['end'] - h['start']
        if cfg['min_duration'] <= duration <= cfg['max_duration']:
            final.append(h)
    
    final.sort(key=lambda x: x['score'], reverse=True)
    final = final[:cfg['top_n']]
    final.sort(key=lambda x: x['start'])
    
    return final


# ============================================================
# LLM分析模块
# ============================================================

def parse_srt_file(srt_path):
    """解析SRT字幕文件"""
    if not os.path.exists(srt_path):
        return []
    
    with open(srt_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    subtitles = []
    blocks = re.split(r'\n\n+', content.strip())
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) >= 3:
            time_match = re.search(
                r'(\d+:\d+:\d+[,\.]\d+)\s*-->\s*(\d+:\d+:\d+[,\.]\d+)',
                lines[1]
            )
            if time_match:
                subtitles.append({
                    'start': time_match.group(1).replace(',', '.'),
                    'end': time_match.group(2).replace(',', '.'),
                    'text': '\n'.join(lines[2:]).strip()
                })
    
    return subtitles


def load_emotion_data(emotion_path):
    """加载情绪数据"""
    if not emotion_path or not os.path.exists(emotion_path):
        return []
    try:
        with open(emotion_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # 支持emotion2vec的新格式
            if isinstance(data, dict) and 'emotion_segments' in data:
                return data['emotion_segments']
            return data
    except:
        return []


def summarize_emotion_data(emotion_data):
    """生成情绪数据摘要"""
    if not emotion_data:
        return ""
    
    # 统计情绪分布
    emotion_counts = {}
    for ed in emotion_data:
        label = ed.get('label', 'unknown')
        emotion_counts[label] = emotion_counts.get(label, 0) + 1
    
    # 找出主导情绪
    if emotion_counts:
        dominant = max(emotion_counts.items(), key=lambda x: x[1])[0]
        total = len(emotion_data)
        return f"主导情绪: {dominant} ({emotion_counts[dominant]}/{total})"
    return ""


def load_speaker_data(speaker_path):
    """加载说话人数据"""
    if not speaker_path or not os.path.exists(speaker_path):
        return {}
    try:
        with open(speaker_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}


def format_subtitles_for_prompt(subtitles, max_length=50000, emotion_data=None, speaker_data=None):
    """格式化字幕为提示词文本，包含情绪和说话人信息"""
    lines = []
    current_length = 0
    
    # 添加情绪数据摘要
    if emotion_data:
        lines.append("【情绪标记数据】(来自emotion2vec情绪检测)")
        for ed in emotion_data[:20]:  # 限制数量
            ts = ed.get('timestamp', 0)
            label = ed.get('label', ed.get('emotion', 'unknown'))
            label_cn = ed.get('label_cn', label)
            score = ed.get('score', 0)
            text = ed.get('text', '')
            score_str = f"·{score:.2f}" if score > 0.8 else ""
            lines.append(f"  [{seconds_to_time(int(ts))}] 情绪:{label_cn}{score_str} | {text}")
        lines.append("")
    
    # 添加说话人数据摘要
    if speaker_data:
        lines.append("【说话人分离数据】(来自FunASR说话人分离)")
        for speaker, segments in speaker_data.items():
            lines.append(f"  说话人 {speaker}: {len(segments)} 段")
            for seg in segments[:5]:  # 每个说话人限制5段示例
                start = seg.get('start', 0)
                text = seg.get('text', '')
                lines.append(f"    [{seconds_to_time(int(start))}] {text}")
        lines.append("")
    
    # 添加字幕内容
    lines.append("【字幕内容】")
    for sub in subtitles:
        line = f"[{sub['start']} --> {sub['end']}] {sub['text']}"
        if current_length + len(line) > max_length:
            break
        lines.append(line)
        current_length += len(line) + 1
    
    return '\n'.join(lines)


def call_llm_api(prompt, api_config):
    """调用LLM API（401/403 诊断 + 5xx/429/网络异常自动重试）"""
    try:
        import requests
    except ImportError:
        print("错误: 未安装requests模块")
        return None

    api_key = api_config.get('api_key', '')
    base_url = api_config.get('base_url', 'https://api.siliconflow.cn/v1')
    model = api_config.get('model', 'deepseek-ai/DeepSeek-V4-Pro')

    # 前置校验：api_key 为空直接报错（不发请求）
    if not api_key:
        print("  ❌ API Key 为空！请通过 --api-key 参数或 gui_config.json 配置")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个专业的直播切片分析师。"},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": api_config.get('max_tokens', 8192),
        "temperature": api_config.get('temperature', 0.7)
    }

    # GLM 思考模型可能把 max_tokens 预算全部耗在推理上，导致 200 但 content 为空；
    # 智谱端点显式关闭思考模式（其他供应商不发送该参数，避免严格校验 400）
    if 'bigmodel.cn' in base_url:
        payload['thinking'] = {'type': 'disabled'}

    print(f"  发送请求到 {base_url}/chat/completions (模型: {model})")
    print(f"  Authorization: Bearer {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else '***'}")

    # 智谱等供应商偶发瞬时 500/429：退避重试；401/403 属配置错误，不重试
    max_retries = 3
    retry_delays = (2, 5, 10)
    for attempt in range(max_retries + 1):
        try:
            response = requests.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=300
            )
        except Exception as e:
            if attempt < max_retries:
                delay = retry_delays[attempt]
                print(f"  [重试 {attempt + 1}/{max_retries}] 网络异常: {e}，{delay} 秒后重试")
                time.sleep(delay)
                continue
            print(f"  API异常（已重试 {max_retries} 次仍失败）: {e}")
            return None

        if response.status_code == 200:
            try:
                content = response.json()['choices'][0]['message']['content'] or ''
            except Exception:
                content = ''
            if content.strip():
                return content
            # 200 但内容为空（如思考模型耗尽 max_tokens）→ 按可重试失败处理
            if attempt < max_retries:
                delay = retry_delays[attempt]
                print(f"  [重试 {attempt + 1}/{max_retries}] 响应内容为空，{delay} 秒后重试")
                time.sleep(delay)
                continue
            print("  API返回200但内容为空（已重试仍为空）")
            return None
        if response.status_code in (401, 403):
            if response.status_code == 401:
                print(f"  ❌ 401 Unauthorized — API Key 为空/过期/不匹配端点")
                print(f"  当前 Key: {api_key[:8]}...  端点: {base_url}")
            else:
                print(f"  ❌ 403 Forbidden — Key 无权访问模型 {model}")
            print(f"  响应: {response.text[:300]}")
            return None
        if (response.status_code >= 500 or response.status_code == 429) and attempt < max_retries:
            delay = retry_delays[attempt]
            print(f"  [重试 {attempt + 1}/{max_retries}] API {response.status_code}，{delay} 秒后重试")
            time.sleep(delay)
            continue

        tried = f"（已重试 {max_retries} 次仍失败）" if attempt > 0 else ""
        print(f"  API错误{tried}: {response.status_code} - {response.text[:300]}")
        return None


def sanitize_llm_text_fields(clips):
    """清洗 LLM 输出的文本字段，防止非法字符导致后续脚本（FFmpeg/ASS）报错。

    已知问题：LLM 标题/封面文字可能输出英文单引号（'），会被 FFmpeg 命令行
    或 ASS 样式解析破坏。统一替换为中文引号。
    """
    TEXT_FIELDS = ('title', 'cover_text_1', 'cover_text_2', 'cover_text',
                   'summary', 'highlight_reason')
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        for field in TEXT_FIELDS:
            val = clip.get(field)
            if isinstance(val, str) and "'" in val:
                clip[field] = val.replace("'", "’")
    return clips


def extract_json_from_response(response_text):
    """从LLM响应中提取JSON"""
    if not response_text:
        return []

    def _load(raw):
        return sanitize_llm_text_fields(json.loads(raw))

    try:
        return _load(response_text)
    except:
        pass
    
    # 从markdown代码块提取
    json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.S)
    if json_match:
        try:
            return _load(json_match.group(1))
        except:
            pass
    
    # 直接匹配JSON数组
    json_match = re.search(r'(\[.*\])', response_text, re.S)
    if json_match:
        try:
            return _load(json_match.group(1))
        except:
            pass
    
    # 修复对象间缺少逗号
    fixed_text = re.sub(r'(\}\s*)(?=\{\s*"segment")', r'\1,', response_text)
    if fixed_text != response_text:
        try:
            array_match = re.search(r'(\[.*\])', fixed_text, re.S)
            if array_match:
                return _load(array_match.group(1))
        except:
            pass
    
    print("  警告: 无法解析JSON，保存到 debug_response.txt")
    with open('debug_response.txt', 'w', encoding='utf-8') as f:
        f.write(response_text)
    return []


# ============================================================
# 结合分析模块（核心）
# ============================================================

def time_to_seconds(time_str):
    """将HH:MM:SS转换为秒数"""
    parts = time_str.split(':')
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def seconds_to_time(seconds):
    """将秒数转换为HH:MM:SS"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _parse_hms_to_seconds(time_str):
    """解析 HH:MM:SS，失败返回 None。"""
    if not isinstance(time_str, str):
        return None
    m = re.fullmatch(r'\s*(\d{1,2}):(\d{2}):(\d{2})\s*', time_str)
    if not m:
        return None
    h, minute, second = map(int, m.groups())
    if minute >= 60 or second >= 60:
        return None
    return h * 3600 + minute * 60 + second


def _parse_segment_start_seconds(segment):
    """从 segment 前缀提取现实起始时间，如 01-04-03 或 01:04:03。

    只匹配文件名开头的标准时间戳（\d{2}-\d{2}-\d{2}），避免把完整文件名中
    的日期（如 分集_2026-08-09_... 中的 26-08-09）误解析为时间。
    """
    if not isinstance(segment, str):
        return None
    m = re.match(r'(\d{1,2})[-:](\d{2})[-:](\d{2})', segment.strip())
    if not m:
        return None
    h, minute, second = map(int, m.groups())
    if minute >= 60 or second >= 60:
        return None
    return h * 3600 + minute * 60 + second


def compute_real_timestamp(segment, timestamp):
    """
    根据视频现实起始时间(segment)和视频内时间(timestamp)计算现实时间范围。
    例如 segment=01-04-03, timestamp=00:02:23-00:03:22
    => 01:06:26-01:07:25。
    """
    if not isinstance(timestamp, str) or '-' not in timestamp:
        return None
    base = _parse_segment_start_seconds(segment)
    if base is None:
        return None
    start_str, end_str = timestamp.split('-', 1)
    start = _parse_hms_to_seconds(start_str)
    end = _parse_hms_to_seconds(end_str)
    if start is None or end is None:
        return None
    return f"{seconds_to_time(base + start)}-{seconds_to_time(base + end)}"


def add_real_timestamps(clips):
    """为片段补充 real_timestamp，无法计算时移除旧值以避免陈旧数据。"""
    count = 0
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        real_ts = compute_real_timestamp(clip.get('segment', ''), clip.get('timestamp', ''))
        if real_ts:
            clip['real_timestamp'] = real_ts
            count += 1
        else:
            clip.pop('real_timestamp', None)
    return count


def _is_template_timestamp(ts):
    """检测是否为 LLM 误输出的格式模板（如 HH:MM:SS-HH:MM:SS）"""
    if not ts:
        return False
    # 含字母且不是纯数字+冒号+横线的组合 → 模板
    return bool(re.search(r'[A-Za-z]', ts))


def merge_overlapping_segments(segments, merge_gap=5):
    """合并重叠或相邻的片段"""
    if not segments:
        return []
    
    # 按开始时间排序
    sorted_segs = sorted(segments, key=lambda x: x['start'])
    
    merged = []
    for seg in sorted_segs:
        if not merged:
            merged.append(seg.copy())
            continue
        
        last = merged[-1]
        # 检查是否重叠或相邻
        if seg['start'] <= last['end'] + merge_gap:
            # 合并
            last['end'] = max(last['end'], seg['end'])
            last['score'] = max(last['score'], seg.get('score', 0))
            # 合并来源标记
            if 'sources' in seg:
                last.setdefault('sources', set()).update(seg['sources'])
            # 保留更长的摘要
            if len(seg.get('summary', '')) > len(last.get('summary', '')):
                last['summary'] = seg['summary']
        else:
            merged.append(seg.copy())
    
    return merged


def combine_danmu_and_llm(danmu_highlights, llm_segments, video_duration=None):
    """
    弹幕+LLM双保险分析
    
    策略：
    1. 弹幕找到的高能时段作为"锚点"
    2. LLM找到的语义高光作为"补充"
    3. 两者重叠的时段优先保留（双验证）
    4. 单独出现的时段根据质量决定是否保留
    """
    print("\n[结合分析] 弹幕 + LLM 双保险...")
    
    # 转换LLM片段为统一格式
    llm_unified = []
    for seg in llm_segments:
        timestamp = seg.get('timestamp', '')
        if '-' in timestamp:
            start_str, end_str = timestamp.split('-')
            try:
                start = time_to_seconds(start_str.strip())
                end = time_to_seconds(end_str.strip())
                llm_unified.append({
                    'start': start,
                    'end': end,
                    'score': 1.0,  # LLM片段默认基础分
                    'title': seg.get('title', ''),
                    'summary': seg.get('summary', ''),
                    'cover_text_1': seg.get('cover_text_1', ''),
                    'cover_text_2': seg.get('cover_text_2', ''),
                    'highlight_reason': seg.get('highlight_reason', ''),
                    'sources': {'llm'}
                })
            except:
                continue
    
    # 转换弹幕片段为统一格式
    danmu_unified = []
    for h in danmu_highlights:
        danmu_unified.append({
            'start': h['start'],
            'end': h['end'],
            'score': h['score'] * 0.5,  # 弹幕热度作为分数
            'danmaku_count': h['count'],
            'summary': f"弹幕热度: {h['score']:.1f}, 弹幕数: {h['count']}",
            'sources': {'danmu'}
        })
    
    print(f"  弹幕片段: {len(danmu_unified)} 个")
    print(f"  LLM片段: {len(llm_unified)} 个")
    
    # 合并所有片段
    all_segments = danmu_unified + llm_unified
    
    # 按时间排序并合并重叠
    merged = merge_overlapping_segments(all_segments, merge_gap=10)
    
    print(f"  合并后: {len(merged)} 个")
    
    # 为每个合并后的片段补充信息
    final_segments = []
    for seg in merged:
        # 检查这个时段内是否有LLM的详细元数据
        llm_match = None
        for ls in llm_unified:
            # 检查重叠
            if not (ls['end'] < seg['start'] or ls['start'] > seg['end']):
                llm_match = ls
                break
        
        # 检查是否有弹幕数据
        danmu_match = None
        for ds in danmu_unified:
            if not (ds['end'] < seg['start'] or ds['start'] > seg['end']):
                danmu_match = ds
                break
        
        # 构建最终片段
        final_seg = {
            'timestamp': f"{seconds_to_time(seg['start'])}-{seconds_to_time(seg['end'])}",
            'start_sec': seg['start'],
            'end_sec': seg['end'],
            'score': seg['score'],
            'sources': []
        }
        
        # 优先使用LLM的元数据（更详细）
        if llm_match:
            final_seg.update({
                'title': llm_match.get('title', ''),
                'summary': llm_match.get('summary', ''),
                'cover_text_1': llm_match.get('cover_text_1', ''),
                'cover_text_2': llm_match.get('cover_text_2', ''),
                'highlight_reason': llm_match.get('highlight_reason', ''),
            })
            final_seg['sources'].append('llm')
        
        # 补充弹幕信息
        if danmu_match:
            final_seg['danmaku_count'] = danmu_match.get('danmaku_count', 0)
            final_seg['danmaku_score'] = danmu_match.get('score', 0)
            if not llm_match:
                # 如果没有LLM数据，用弹幕摘要
                final_seg['title'] = f"高能弹幕区 ({danmu_match.get('danmaku_count', 0)}条弹幕)"
                final_seg['summary'] = danmu_match.get('summary', '')
                final_seg['highlight_reason'] = "弹幕密度异常高，观众反应热烈"
            final_seg['sources'].append('danmu')
        
        # 标记双验证
        if llm_match and danmu_match:
            final_seg['dual_verified'] = True
            final_seg['score'] *= 1.5  # 双验证加分
            final_seg['highlight_reason'] = f"[双验证] {final_seg.get('highlight_reason', '')}"
        
        final_segments.append(final_seg)
    
    # 按分数排序
    final_segments.sort(key=lambda x: x['score'], reverse=True)
    
    # 限制数量
    final_segments = final_segments[:80]
    
    # 再按时间排序输出
    final_segments.sort(key=lambda x: x['start_sec'])
    
    print(f"  最终片段: {len(final_segments)} 个")
    print(f"  双验证片段: {sum(1 for s in final_segments if s.get('dual_verified'))} 个")
    
    return final_segments


def _normalize_segments_to_unified(source_segments, source_name):
    """
    将各种来源的片段归一化为统一格式: {start, end, score, sources, ...}
    source_segments 是 run_xxx_mode 的返回值（已含 segment/timestamp/title 等字段）
    """
    unified = []
    for seg in source_segments:
        ts = seg.get('timestamp', '')
        if '-' not in ts:
            continue
        try:
            start_str, end_str = ts.split('-')
            start = time_to_seconds(start_str.strip())
            end = time_to_seconds(end_str.strip())
        except Exception:
            continue

        entry = {
            'start': start,
            'end': end,
            'score': seg.get('score', 1.0),
            'title': seg.get('title', ''),
            'summary': seg.get('summary', ''),
            'cover_text_1': seg.get('cover_text_1', ''),
            'cover_text_2': seg.get('cover_text_2', ''),
            'highlight_reason': seg.get('highlight_reason', ''),
            'segment': seg.get('segment', ''),
            'sources': {source_name},
        }
        # 传递附属字段
        if seg.get('danmaku_count'):
            entry['danmaku_count'] = seg['danmaku_count']
        if seg.get('emotion_label'):
            entry['emotion_label'] = seg['emotion_label']
            entry['emotion_label_cn'] = seg.get('emotion_label_cn', '')
            entry['emotion_intensity'] = seg.get('emotion_intensity', '')
        unified.append(entry)
    return unified


def merge_overlapping_segments(all_segments, merge_gap=10):
    """合并重叠/相邻的片段（通用版）"""
    if not all_segments:
        return []
    sorted_segs = sorted(all_segments, key=lambda x: x['start'])
    merged = [dict(sorted_segs[0])]
    for seg in sorted_segs[1:]:
        last = merged[-1]
        if seg['start'] <= last['end'] + merge_gap:
            last['end'] = max(last['end'], seg['end'])
            last['score'] = max(last['score'], seg['score'])
            last['sources'] = last.get('sources', set()) | seg.get('sources', set())
        else:
            merged.append(dict(seg))
    return merged


def combine_multi_sources(all_source_segments, max_count=25, min_verified=2):
    """
    多保险融合：将多个来源的片段合并为最终列表

    策略：
    1. 所有来源的片段归一到统一格式
    2. 按视频分组（各分段视频的时间轴互相独立，禁止跨视频合并）
    3. 组内时间段合并（间隙 ≤ 10s）
    4. 统计每个区间的验证来源数
    5. 分数加成：1源=1x, 2源=1.5x, 3源=2.0x
    6. 元数据优先级：LLM > 弹幕 > 情绪
    7. 仅保留验证数 ≥ min_verified 的片段（来源被自动裁剪时随之放宽）
    8. 按分数排序取 top max_count
    """
    print(f"\n[多保险融合] 融合 {len(all_source_segments)} 个来源...")

    # 1. 归一化
    all_unified = []
    for source_name, segments in all_source_segments.items():
        unified = _normalize_segments_to_unified(segments, source_name)
        print(f"  [{source_name}] {len(unified)} 个片段")
        all_unified.extend(unified)

    if not all_unified:
        print("  [警告] 无有效片段")
        return []

    # 2. 按视频分组，组内合并重叠（不同分段视频的时间戳互相不可比）
    groups = defaultdict(list)
    for u in all_unified:
        groups[u.get('segment', '')].append(u)

    merged_with_group = []
    for seg_name, group_unified in groups.items():
        group_merged = merge_overlapping_segments(group_unified, merge_gap=10)
        print(f"  [{seg_name or '未知视频'}] {len(group_unified)} 个片段合并为 {len(group_merged)} 个区间")
        merged_with_group.extend((seg_name, group_unified, seg) for seg in group_merged)

    # 3. 为每个合并区间补充完整信息
    final_segments = []
    for seg_name, group_unified, seg in merged_with_group:
        source_set = seg.get('sources', set())
        n_sources = len(source_set)

        # 找元数据（优先级：llm > danmu > emotion 对应来源中的第一条匹配）
        metadata = None
        for source_priority in ['llm', 'danmu', 'emotion']:
            if source_priority not in source_set:
                continue
            for u in group_unified:
                if source_priority not in u.get('sources', set()):
                    continue
                if not (u['end'] < seg['start'] or u['start'] > seg['end']):
                    metadata = u
                    break
            if metadata:
                break

        final = {
            'timestamp': f"{seconds_to_time(seg['start'])}-{seconds_to_time(seg['end'])}",
            'start_sec': seg['start'],
            'end_sec': seg['end'],
            'score': seg['score'],
            'sources': sorted(source_set),
        }

        if metadata:
            final['title'] = metadata.get('title', '')
            final['summary'] = metadata.get('summary', '')
            final['cover_text_1'] = metadata.get('cover_text_1', '')
            final['cover_text_2'] = metadata.get('cover_text_2', '')
            final['highlight_reason'] = metadata.get('highlight_reason', '')
            final['segment'] = metadata.get('segment', '')
        else:
            final['title'] = '高光片段'
            final['summary'] = ''
            final['highlight_reason'] = ''
            final['segment'] = seg_name

        # 传递额外字段
        for u in group_unified:
            if not (u['end'] < seg['start'] or u['start'] > seg['end']):
                if u.get('danmaku_count'):
                    final['danmaku_count'] = u['danmaku_count']
                if u.get('emotion_label'):
                    final['emotion_label'] = u['emotion_label']
                    final['emotion_label_cn'] = u.get('emotion_label_cn', '')
                    final['emotion_intensity'] = u.get('emotion_intensity', '')

        # 来源验证标记（multi_verified 始终记录实际来源数，加分与标记仅 ≥2 重时生效）
        n = len(source_set)
        final['multi_verified'] = n
        if n >= 2:
            final['score'] *= 0.5 + 0.5 * n  # 2源=1.5x, 3源=2.0x
            final['highlight_reason'] = f"[{n}重验证] {final.get('highlight_reason', '')}"

        final_segments.append(final)

    # 4. 排序 + 限制
    # 仅保留验证数达标的片段
    all_before_filter = final_segments
    final_segments = [s for s in final_segments if s.get('multi_verified', 0) >= min_verified]
    
    final_segments.sort(key=lambda x: x['score'], reverse=True)
    final_segments = final_segments[:max_count]
    final_segments.sort(key=lambda x: x['start_sec'])

    # 统计
    multi_counts = {}
    for s in final_segments:
        n = s.get('multi_verified', 1)
        multi_counts[n] = multi_counts.get(n, 0) + 1
    for n in sorted(multi_counts):
        print(f"  {n}重验证片段: {multi_counts[n]} 个")
    print(f"  最终: {len(final_segments)} 个")
    print(f"  已过滤掉：{len([s for s in all_before_filter if s.get('multi_verified', 0) < min_verified])} 个不足 {min_verified} 重验证的片段")

    return final_segments


def save_intermediate_result(config, results, source_name):
    """保存单个子模式的分析结果"""
    output_dir = config['input_dir']
    filename = f"Data_source_{source_name}.json"
    output_path = os.path.join(output_dir, filename)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  [中间文件] {filename} ({len(results)} 条)")
    return output_path


def run_multi_insurance_mode(config, matched_files):
    """多保险分析：用户选择多种方法组合分析

    勾选的来源若在目录内找不到对应输入文件（弹幕/字幕/情绪），
    自动忽略该来源，用剩余来源继续分析。
    """
    enabled = list(config.get('multi_sources', ['danmu', 'llm']))

    # 来源可用性检测：至少有一个视频具备该来源的输入文件才算可用
    file_labels = {
        'danmu': '弹幕文件(.ass)',
        'llm': '字幕文件(.srt)',
        'emotion': '情绪数据文件(_emotion.json)',
    }
    counts = {
        'danmu': sum(1 for m in matched_files if m.get('ass')),
        'llm': sum(1 for m in matched_files if m.get('srt')),
        'emotion': sum(1 for m in matched_files if m.get('emotion')),
    }
    missing = [s for s in enabled if counts.get(s, 0) == 0]
    if missing:
        for s in missing:
            print(f"[多保险检测] 忽略 {s} 来源：目录内没有可用的{file_labels[s]}")
        enabled = [s for s in enabled if counts.get(s, 0) > 0]
        if enabled:
            print(f"[多保险检测] 使用剩余来源继续: {', '.join(enabled)}")
            config['multi_sources'] = enabled
        else:
            print("[多保险检测] 所有勾选来源都缺少输入文件，无法分析")
            return []

    # 验证门槛随实际可用来源数放宽：3源/2源要求≥2重，仅剩1源时保留其结果
    min_verified = min(2, len(enabled))
    if min_verified < 2:
        print(f"[多保险检测] 仅剩 {len(enabled)} 个来源，验证门槛放宽为 {min_verified} 重")

    print("\n" + "=" * 60)
    print(f"[模式] 多保险融合分析 ({', '.join(enabled)})")
    print("=" * 60)

    all_source_segments = {}
    sub_order = [('danmu', '弹幕热度分析', run_danmu_mode),
                 ('llm', 'LLM语义分析', run_llm_mode),
                 ('emotion', '情绪驱动分析', run_emotion_mode)]

    step = 0
    for source_name, label, runner in sub_order:
        if source_name not in enabled:
            continue
        step += 1
        print(f"\n{'─' * 50}")
        print(f"[子模式 {step}/{len(enabled)}] {label}...")
        print(f"{'─' * 50}")
        results = runner(config, matched_files)
        all_source_segments[source_name] = results
        save_intermediate_result(config, results, source_name)

    # 融合
    print(f"\n{'─' * 50}")
    print(f"[融合] 多源融合...")
    print(f"{'─' * 50}")
    combined = combine_multi_sources(all_source_segments, min_verified=min_verified)

    return combined


# ============================================================
# 主程序
# ============================================================

def run_danmu_mode(config, matched_files):
    """纯弹幕分析模式"""
    print("\n" + "="*60)
    print("[模式] 纯弹幕分析")
    print("="*60)
    
    all_highlights = []
    
    for item in matched_files:
        if not item['ass']:
            print(f"\n[跳过] {item['video']} (无弹幕文件)")
            continue
        
        print(f"\n[分析] {item['video']}")
        ass_path = os.path.join(config['input_dir'], item['ass'])
        
        danmaku_data = load_danmaku(ass_path)
        print(f"  加载 {len(danmaku_data)} 条弹幕")
        
        highlights = find_highlights_by_danmu(danmaku_data)
        print(f"  找到 {len(highlights)} 个高光片段")
        
        for h in highlights:
            all_highlights.append({
                'segment': item['prefix'],
                'timestamp': f"{seconds_to_time(h['start'])}-{seconds_to_time(h['end'])}",
                'title': f"高能弹幕区 ({h['count']}条弹幕)",
                'summary': f"弹幕热度: {h['score']:.1f}, 弹幕数: {h['count']}",
                'cover_text_1': "弹幕高能",
                'cover_text_2': f"{h['count']}条弹幕",
                'highlight_reason': "弹幕密度异常高，观众反应热烈",
                'score': h['score'],
                'danmaku_count': h['count']
            })
    
    # 排序
    all_highlights.sort(key=lambda x: x['score'], reverse=True)
    all_highlights = all_highlights[:80]
    all_highlights.sort(key=lambda x: x['timestamp'])
    
    return all_highlights


def run_llm_mode(config, matched_files):
    """纯LLM分析模式 - 优化并发版本"""
    print("\n" + "="*60)
    print("[模式] 纯LLM分析 (并发优化)")
    print("="*60)
    
    # 加载主播提示词
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils'))
    from streamer_prompts import get_prompt_manager
    
    prompt_manager = get_prompt_manager(config.get('prompts_dir'))
    base_prompt = prompt_manager.get_prompt(config['streamer_name'])
    
    if not base_prompt:
        print(f"[错误] 未找到主播 [{config['streamer_name']}] 的提示词")
        return []
    
    # 过滤出有字幕的文件
    valid_files = [item for item in matched_files if item['srt']]
    
    if not valid_files:
        print("[错误] 没有找到带字幕的文件")
        return []
    
    print(f"[信息] 共 {len(valid_files)} 个文件需要分析")
    print("[优化] 使用并发处理加速...\n")
    
    all_segments = []
    failed_files = []
    completed_count = 0
    
    # 使用ThreadPoolExecutor进行并发处理
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    def analyze_single_file(item):
        """分析单个文件"""
        nonlocal completed_count
        
        srt_path = os.path.join(config['input_dir'], item['srt'])
        subtitles = parse_srt_file(srt_path)
        
        # 加载情绪数据（如果存在）
        emotion_data = None
        emotion_summary = None
        if item.get('emotion'):
            emotion_path = os.path.join(config['input_dir'], item['emotion'])
            emotion_data = load_emotion_data(emotion_path)
            if emotion_data:
                print(f"  [emotion2vec] {item['video']} 情绪数据: {len(emotion_data)} 条")
                # 添加情绪摘要到提示词
                emotion_summary = summarize_emotion_data(emotion_data)
                if emotion_summary:
                    print(f"    情绪摘要: {emotion_summary}")
        
        # 加载说话人数据（如果存在）
        speaker_data = None
        if item.get('speaker'):
            speaker_path = os.path.join(config['input_dir'], item['speaker'])
            speaker_data = load_speaker_data(speaker_path)
            if speaker_data:
                print(f"  [FunASR] {item['video']} 说话人: {len(speaker_data)} 个")
        
        subtitle_text = format_subtitles_for_prompt(subtitles, emotion_data=emotion_data, speaker_data=speaker_data)
        
        # 构建情绪提示
        emotion_hint = ""
        if emotion_summary:
            emotion_hint = f"\n【情绪分析摘要】\n{emotion_summary}\n该视频的情绪分布已提供，请优先关注情绪高涨（兴奋、开心、惊讶）的片段，这些片段通常包含更多节目效果。"
        
        # 获取视频时长，按每 30 分钟 1-3 个线性递推给出推荐片段数量区间
        # 先四舍五入成整数分钟再递推：提示词里的"约N分钟"与数量判定保证同一口径
        video_path = os.path.join(config['input_dir'], item['video'])
        duration_s = get_video_duration(video_path)
        duration_min = round(duration_s / 60)
        min_c, max_c = compute_clip_range(duration_min * 60)
        clip_count_hint = f"4. 本视频时长约{duration_min:.0f}分钟，请**精选 {min_c}-{max_c} 个**最精彩的高光片段（宁缺毋滥，不要凑数）"
        
        prompt = f"""{base_prompt}

### 当前分析文件
文件名: {item['video']}
文件标识: {item['prefix']}

{subtitle_text}{emotion_hint}

### 重要说明
1. segment字段必须完整填写当前分析的视频文件名（不含扩展名）
2. timestamp格式为 "HH:MM:SS-HH:MM:SS"
3. 请只从这个文件中提取高光片段
{clip_count_hint}
5. 如果提供了情绪数据，请优先关注情绪高涨（兴奋、开心、惊讶）的片段
6. 如果提供了说话人数据，请优先关注主播本人说话的片段

请严格按照JSON数组格式输出结果。"""
        
        response = call_llm_api(prompt, config['llm'])
        clips = []
        api_failed = response is None
        if not api_failed:
            clips = extract_json_from_response(response)
            for clip in clips:
                clip['segment'] = item['prefix']
            # 过滤 LLM 误输出的格式模板（如 timestamp="HH:MM:SS-HH:MM:SS"）
            clips = [c for c in clips if not _is_template_timestamp(c.get('timestamp', ''))]
        else:
            failed_files.append(item['video'])

        completed_count += 1
        result_tag = "❌ API调用失败（已自动重试仍失败）" if api_failed else f"{len(clips)} 个片段"
        print(f"  [{completed_count}/{len(valid_files)}] {item['video']} -> {result_tag}")
        return clips
    
    # 并发执行，最多3个同时请求
    with ThreadPoolExecutor(max_workers=3) as executor:
        future_to_item = {
            executor.submit(analyze_single_file, item): item 
            for item in valid_files
        }
        
        for future in as_completed(future_to_item):
            try:
                clips = future.result()
                all_segments.extend(clips)
            except Exception as e:
                item = future_to_item[future]
                print(f"  [错误] {item['video']}: {e}")
    
    print(f"\n[完成] 共提取 {len(all_segments)} 个片段")
    if failed_files:
        print(f"\n[警告] {len(failed_files)} 个文件 API 调用失败（已自动重试仍失败），这些视频本次为 0 片段，建议重跑：")
        for v in failed_files:
            print(f"  - {v}")
    return all_segments




def run_emotion_mode(config, matched_files):
    """Emotion-driven analysis mode - Extract highlights based on emotion2vec"""
    print("\n" + "="*60)
    print("[Mode] Emotion-driven Analysis")
    print("="*60)
    
    all_highlights = []
    
    for item in matched_files:
        if not item.get('emotion'):
            print(f"\n[Skip] {item['video']} (no emotion data)")
            continue
        
        print(f"\n[Analyze] {item['video']}")
        emotion_path = os.path.join(config['input_dir'], item['emotion'])
        
        # Load emotion data
        emotion_data = load_emotion_data(emotion_path)
        if not emotion_data:
            print(f"  Failed to load emotion data")
            continue
        
        print(f"  Loaded {len(emotion_data)} emotion records")
        
        # Filter high intensity emotion segments
        high_emotion_segments = []
        for ed in emotion_data:
            label = ed.get('label', 'unknown')
            score = ed.get('score', 0)
            
            # Define high intensity emotion labels
            high_intensity_labels = ['excited', 'happy', 'surprised', 'angry', 'fear']
            if label in high_intensity_labels and score > 0.7:
                high_emotion_segments.append(ed)
        
        print(f"  Found {len(high_emotion_segments)} high intensity emotion segments")
        
        # Merge adjacent emotion segments
        merged_segments = []
        for seg in high_emotion_segments:
            if not merged_segments:
                merged_segments.append(seg)
                continue
            
            last = merged_segments[-1]
            last_end = last.get('end', last.get('timestamp', 0))
            curr_start = seg.get('start', seg.get('timestamp', 0))
            
            # Merge if gap less than 10 seconds
            if curr_start - last_end < 10:
                last['end'] = seg.get('end', curr_start + 5)
                last['score'] = max(last.get('score', 0), seg.get('score', 0))
            else:
                merged_segments.append(seg)
        
        # Convert to standard format
        for seg in merged_segments[:80]:  # Limit count
            start = seg.get('start', seg.get('timestamp', 0))
            end = seg.get('end', start + 10)
            label = seg.get('label', 'unknown')
            label_cn = seg.get('label_cn', label)
            score = seg.get('score', 0)
            
            all_highlights.append({
                'segment': item['prefix'],
                'timestamp': f"{seconds_to_time(int(start))}-{seconds_to_time(int(end))}",
                'title': f"Emotion Highlight: {label_cn}",
                'summary': f"High intensity {label_cn} emotion detected",
                'cover_text_1': f"{label_cn} Emotion",
                'cover_text_2': f"Intensity {score:.2f}",
                'highlight_reason': f"emotion2vec detected high intensity {label_cn} emotion",
                'score': score,
                'emotion_label': label,
                'emotion_label_cn': label_cn,
                'emotion_intensity': 'high',
                'sources': ['emotion']
            })
    
    # Sort by score
    all_highlights.sort(key=lambda x: x['score'], reverse=True)
    all_highlights = all_highlights[:80]
    all_highlights.sort(key=lambda x: x['timestamp'])
    
    print(f"\n[Done] Extracted {len(all_highlights)} emotion highlight segments")
    return all_highlights


def run_combined_mode(config, matched_files):
    """弹幕+LLM双保险分析模式 - 优化并发版本"""
    print("\n" + "="*60)
    print("[模式] 弹幕 + LLM 双保险分析 (并发优化)")
    print("="*60)
    
    # 第一步：弹幕分析（快速，不需要并发）
    print("\n[步骤1/3] 弹幕热度分析...")
    danmu_highlights = []
    
    for item in matched_files:
        if not item['ass']:
            continue
        
        ass_path = os.path.join(config['input_dir'], item['ass'])
        danmaku_data = load_danmaku(ass_path)
        
        if danmaku_data:
            highlights = find_highlights_by_danmu(danmaku_data)
            # 标记来源
            for h in highlights:
                h['segment'] = item['prefix']
            danmu_highlights.extend(highlights)
            print(f"  [{item['prefix']}] {len(highlights)} 个弹幕高光")
    
    # 第二步：LLM分析（使用并发优化）
    print("\n[步骤2/3] LLM语义分析 (并发处理)...")
    llm_segments = run_llm_mode(config, matched_files)
    
    # 第三步：结合分析
    print("\n[步骤3/3] 双保险融合...")
    combined = combine_danmu_and_llm(danmu_highlights, llm_segments)
    
    # 转换为标准输出格式
    results = []
    for seg in combined:
        result = {
            'segment': seg.get('segment', ''),
            'timestamp': seg['timestamp'],
            'title': seg.get('title', ''),
            'summary': seg.get('summary', ''),
            'cover_text_1': seg.get('cover_text_1', ''),
            'cover_text_2': seg.get('cover_text_2', ''),
            'highlight_reason': seg.get('highlight_reason', ''),
            'score': seg.get('score', 0),
            'sources': seg.get('sources', [])
        }
        if 'danmaku_count' in seg:
            result['danmaku_count'] = seg['danmaku_count']
        if 'dual_verified' in seg:
            result['dual_verified'] = True
        results.append(result)
    
    return results


def ensure_danmaku_ass_files(config):
    """弹幕分析前置：把视频配套的 XML 弹幕转成 ASS（已有同名 ASS 时跳过）。

    实现委托给 core/convert_danmaku_xml.py（与流水线步骤1-4 共用一份逻辑），
    DanmakuFactory 缺失或单个文件转换失败只警告不中断。
    """
    try:
        from convert_danmaku_xml import convert_dir_xml_to_ass
    except Exception as e:
        print(f"[弹幕前置] 无法加载转换模块 convert_danmaku_xml: {type(e).__name__}: {e}")
        return
    try:
        convert_dir_xml_to_ass(config['input_dir'], log=print)
    except Exception as e:
        print(f"[弹幕前置] 弹幕转换执行失败（不中断分析）: {type(e).__name__}: {e}")


def main():
    config = get_config()
    
    print("="*60)
    print("AutoClip 三模态高光切片生成工具")
    print("="*60)
    print(f"分析方法: {config['method']}")
    print(f"输入目录: {config['input_dir']}")
    if config['method'] in ['llm', 'combined']:
        print(f"主播: {config['streamer_name']}")
    print()
    
    # 弹幕分析前置：需要弹幕源时，把目录里缺 ASS 的配套 XML 弹幕先转成 ASS
    danmu_needed = config['method'] in ('danmu', 'combined') or (
        config['method'] == 'multi' and 'danmu' in config.get('multi_sources', ['danmu', 'llm'])
    )
    if danmu_needed:
        ensure_danmaku_ass_files(config)

    # 扫描文件
    files = scan_files(config['input_dir'])
    if not files:
        return
    
    # 匹配文件
    matched = match_files(files['videos'], files['srts'], files['asses'], files.get('emotions'), files.get('speakers'))
    print(f"\n[文件匹配] 成功匹配 {len(matched)} 组文件:")
    for m in matched:
        srt_status = "✓" if m['srt'] else "✗"
        ass_status = "✓" if m['ass'] else "✗"
        emotion_status = "✓" if m.get('emotion') else "✗"
        speaker_status = "✓" if m.get('speaker') else "✗"
        print(f"  [{m['prefix']}] 视频:{m['video']} 字幕:{srt_status} 弹幕:{ass_status} 情绪:{emotion_status} 说话人:{speaker_status}")
    
    # 根据模式执行分析
    if config['method'] == 'danmu':
        results = run_danmu_mode(config, matched)
    elif config['method'] == 'llm':
        results = run_llm_mode(config, matched)
    elif config['method'] == 'emotion':
        results = run_emotion_mode(config, matched)
    elif config['method'] == 'combined':
        results = run_combined_mode(config, matched)
    elif config['method'] == 'multi':
        results = run_multi_insurance_mode(config, matched)
    else:
        print(f"[错误] 未知的分析方法: {config['method']}")
        return
    
    # 保存结果
    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"{'='*60}")
    
    if results:
        output_path = os.path.join(config['input_dir'], config['output_file'])
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        
        print(f"\n[OK] 已保存到: {output_path}")
        
        # 气口缓冲：前后各加 1 秒，自动检测视频时长防越界
        try:
            from utils.edit_data_source_timestep import apply_buffer
            apply_buffer(output_path, video_dir=config['input_dir'], buffer_seconds=1)
        except Exception as e:
            print(f"[警告] 气口缓冲失败: {e}")
        
        # 补充现实时间：放在气口缓冲之后，确保与最终 timestamp 对齐
        try:
            with open(output_path, 'r', encoding='utf-8') as f:
                saved_results = json.load(f)
            real_ts_count = add_real_timestamps(saved_results)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(saved_results, f, ensure_ascii=False, indent=2)
            results = saved_results
            print(f"[real_time] 已补充 {real_ts_count} 条 real_timestamp")
        except Exception as e:
            print(f"[警告] 现实时间字段补充失败: {e}")
        print(f"总共 {len(results)} 个高光片段")
        
        # 统计
        if config['method'] == 'combined':
            dual = sum(1 for r in results if r.get('dual_verified'))
            print(f"双验证片段: {dual} 个")
        
        # 情绪分析统计
        if config['method'] == 'emotion':
            emotion_high = sum(1 for r in results if r.get('emotion_intensity') == 'high')
            print(f"高强度情绪片段: {emotion_high} 个")

        # 多保险统计
        if config['method'] == 'multi':
            multi_counts = {}
            for r in results:
                n = r.get('multi_verified', 1)
                multi_counts[n] = multi_counts.get(n, 0) + 1
            for n in sorted(multi_counts):
                print(f"{n}重验证片段: {multi_counts[n]} 个")
            # 中间文件提示
            enabled = config.get('multi_sources', ['danmu', 'llm'])
            for src in enabled:
                path = os.path.join(config['input_dir'], f"Data_source_{src}.json")
                if os.path.exists(path):
                    print(f"中间文件: {path}")
        
        print(f"\n片段摘要:")
        for i, r in enumerate(results[:15], 1):
            seg = r.get('segment', '')
            title = r.get('title', '未命名')
            ts = r.get('timestamp', '')
            real_ts = r.get('real_timestamp', '')
            real_tag = f" / 现实:{real_ts}" if real_ts else ""
            sources = r.get('sources', [])
            source_tag = f"[{' + '.join(sources)}]" if sources else ""
            emotion_tag = ""
            if r.get('emotion_label'):
                emotion_tag = f" [情绪:{r.get('emotion_label_cn', r['emotion_label'])}]"
            multi_tag = ""
            if r.get('multi_verified'):
                multi_tag = f" [{r['multi_verified']}重验证]"
            print(f"  [{i}] [{seg}] {title} ({ts}{real_tag}){emotion_tag}{multi_tag} {source_tag}")
        
        if len(results) > 15:
            print(f"  ... 还有 {len(results) - 15} 个片段")
    else:
        print("\n[警告] 未提取到任何高光片段")


if __name__ == "__main__":
    main()
