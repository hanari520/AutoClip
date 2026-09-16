# -*- coding: utf-8 -*-
"""
LLM高光切片生成脚本
支持多文件分析，自动调用LLM API生成切片元数据
"""
import os
import sys
import json
import glob
import re
from pathlib import Path

# 设置UTF-8编码
sys.stdout.reconfigure(encoding='utf-8')

# 导入主播提示词管理器
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'utils'))
from streamer_prompts import get_prompt_manager
from llm_config import resolve_llm_base_url

# ==========================================
# 用户配置区域（可通过命令行参数覆盖）
# ==========================================

def parse_args():
    """解析命令行参数"""
    import argparse
    parser = argparse.ArgumentParser(description='LLM高光切片生成工具')
    parser.add_argument('--input-dir', '-i', type=str, 
                        help='输入目录（包含mp4和srt文件）')
    parser.add_argument('--streamer', '-s', type=str,
                        help='主播名称')
    parser.add_argument('--prompts-dir', '-p', type=str,
                        help='自定义提示词目录')
    parser.add_argument('--api-key', '-k', type=str,
                        help='API Key')
    parser.add_argument('--model', '-m', type=str,
                        help='模型名称')
    parser.add_argument('--base-url', type=str,
                        help='LLM API 端点(如 https://api.siliconflow.cn/v1 或 https://api.deepseek.com/v1)；不传时从 configs/gui_config.json 的 llm_provider 读取，再兜底默认硅基流动')
    parser.add_argument('--output', '-o', type=str,
                        help='输出文件名')
    return parser.parse_args()

# 默认配置
DEFAULT_CONFIG = {
    # 输入目录（包含多个mp4和srt文件）
    "input_dir": r"<录播根目录>\<主播名>\<日期 标题>",
    
    # 主播名称（用于加载对应提示词）
    "streamer_name": "栞栞Shiori",
    
    # 提示词目录（可选，用于加载自定义提示词）
    "prompts_dir": r"",
    
    # LLM API配置
    "llm": {
        "api_key": "",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V4-Pro",
        "max_tokens": 8192,
        "temperature": 0.7
    },
    
    # 输出文件路径
    "output_file": "Data_source.txt"
}

def get_config():
    """获取配置，命令行参数优先"""
    args = parse_args()
    config = DEFAULT_CONFIG.copy()
    
    # 命令行参数覆盖默认值
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
    
    return config

CONFIG = get_config()

# ==========================================
# 工具函数
# ==========================================

def parse_srt_file(srt_path):
    """解析SRT字幕文件，返回带时间戳的字幕列表"""
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
                start_time = time_match.group(1).replace(',', '.')
                end_time = time_match.group(2).replace(',', '.')
                text = '\n'.join(lines[2:])
                subtitles.append({
                    'start': start_time,
                    'end': end_time,
                    'text': text.strip()
                })
    
    return subtitles


def format_subtitles_for_prompt(subtitles, max_length=8000):
    """将字幕格式化为提示词可用的文本，控制总长度"""
    lines = []
    current_length = 0
    
    for sub in subtitles:
        line = f"[{sub['start']} --> {sub['end']}] {sub['text']}"
        if current_length + len(line) > max_length:
            break
        lines.append(line)
        current_length += len(line) + 1
    
    return '\n'.join(lines)


def get_file_prefix(filename):
    """从文件名提取前缀（如 10-21-48）"""
    name = os.path.splitext(filename)[0]
    match = re.match(r'(\d{2}-\d{2}-\d{2})', name)
    if match:
        return match.group(1)
    return name[:8] if len(name) >= 8 else name


def call_llm_api(prompt, api_config):
    """调用LLM API

    401 排查要点：
    1. api_key 为空 → Authorization: Bearer 为空 → 401
    2. api_key 过期/错误 → 401
    3. base_url 不匹配（如用 SiliconFlow 的 key 访问 DeepSeek 官方）→ 401
    """
    try:
        import requests

        # ── 前置校验：api_key 非空 ──
        api_key = api_config.get('api_key', '')
        if not api_key:
            print("  ❌ API Key 为空！请通过 --api-key 参数或 gui_config.json 的 global_api_key 配置")
            print("  提示: 在 GUI 顶部点击「⚙️ 高级」填入 API Key，或编辑 configs/gui_config.json")
            return None

        base_url = api_config.get('base_url', 'https://api.siliconflow.cn/v1')
        model = api_config.get('model', 'deepseek-ai/DeepSeek-V4-Pro')

        # ── 前置校验：端点 URL 与 key 前缀匹配 ──
        # SiliconFlow: sk-xxx → https://api.siliconflow.cn/v1
        # DeepSeek 官方: sk-xxx → https://api.deepseek.com/v1
        # OpenAI: sk-xxx → https://api.openai.com/v1
        if 'siliconflow' in base_url and not api_key.startswith('sk-'):
            print(f"  ⚠️ SiliconFlow 端点但 API Key 前缀不是 sk- (实际: {api_key[:4]}...)")
        if 'deepseek.com' in base_url and not api_key.startswith('sk-'):
            print(f"  ⚠️ DeepSeek 端点但 API Key 前缀不是 sk- (实际: {api_key[:4]}...)")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是一个专业的直播切片分析师，擅长从字幕中提取高光片段。"},
                {"role": "user", "content": prompt}
            ],
            "max_tokens": api_config['max_tokens'],
            "temperature": api_config['temperature']
        }
        
        print(f"  发送请求到 {base_url}/chat/completions (模型: {model})")
        print(f"  Authorization: Bearer {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else '***'}")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=300
        )

        print(f"  收到响应，状态码: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            return result['choices'][0]['message']['content']
        elif response.status_code == 401:
            print(f"  ❌ 401 Unauthorized — 认证失败")
            print(f"  排查: 1) API Key 是否为空  2) API Key 是否过期  3) 端点 {base_url} 与 Key 是否匹配")
            print(f"  当前 Key: {api_key[:8]}...{api_key[-4:] if len(api_key)>12 else '***'}")
            print(f"  服务端响应: {response.text[:300]}")
            return None
        elif response.status_code == 403:
            print(f"  ❌ 403 Forbidden — Key 有效但无权访问模型 {model}")
            print(f"  排查: 检查该 Key 是否有 {model} 的调用权限")
            print(f"  服务端响应: {response.text[:300]}")
            return None
        else:
            print(f"  API请求失败: {response.status_code}")
            print(f"  错误信息: {response.text[:300]}")
            return None
            
    except ImportError:
        print("错误: 未安装requests模块，请先安装: pip install requests")
        return None
    except Exception as e:
        print(f"  API调用异常: {e}")
        return None


def sanitize_llm_text_fields(clips):
    """清洗 LLM 输出的文本字段，防止英文单引号破坏 FFmpeg/ASS 脚本。"""
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
    """从LLM响应中提取JSON数组"""
    if not response_text:
        return []

    def _load(raw):
        return sanitize_llm_text_fields(json.loads(raw))

    try:
        return _load(response_text)
    except:
        pass
    
    # 尝试从markdown代码块提取
    json_match = re.search(r'```(?:json)?\s*(\[.*?\])\s*```', response_text, re.S)
    if json_match:
        try:
            return _load(json_match.group(1))
        except:
            pass
    
    # 尝试直接匹配JSON数组
    json_match = re.search(r'(\[.*\])', response_text, re.S)
    if json_match:
        try:
            return _load(json_match.group(1))
        except:
            pass
    
    # 尝试修复常见JSON格式错误（如缺少外层数组括号）
    # 检查是否有多个JSON对象但没有外层数组
    objects = re.findall(r'\{\s*"segment".*?\}', response_text, re.S)
    if len(objects) > 0:
        try:
            fixed_json = "[" + ",".join(objects) + "]"
            return _load(fixed_json)
        except:
            pass
    
    # 尝试修复：对象之间缺少逗号的情况
    # 匹配 "segment" 开头的对象，并在它们之间添加逗号
    fixed_text = re.sub(r'(\}\s*)(?=\{\s*"segment")', r'\1,', response_text)
    if fixed_text != response_text:
        try:
            # 尝试提取数组
            array_match = re.search(r'(\[.*\])', fixed_text, re.S)
            if array_match:
                return _load(array_match.group(1))
            # 如果没有外层数组，添加
            objects_only = re.findall(r'\{.*?\}', fixed_text, re.S)
            if objects_only:
                fixed_json = "[" + ",".join(objects_only) + "]"
                return _load(fixed_json)
        except:
            pass
    
    print("警告: 无法从响应中提取JSON，保存原始响应到 debug_response.txt")
    with open('debug_response.txt', 'w', encoding='utf-8') as f:
        f.write(response_text)
    return []


# ==========================================
# 主程序
# ==========================================

def main():
    input_dir = CONFIG['input_dir']
    streamer_name = CONFIG['streamer_name']
    
    print("=" * 60)
    print("LLM高光切片生成工具")
    print("=" * 60)
    print(f"输入目录: {input_dir}")
    print(f"主播: {streamer_name}")
    print()
    
    # 1. 获取主播提示词
    print("[1/5] 加载主播提示词...")
    prompt_manager = get_prompt_manager(CONFIG.get('prompts_dir'))
    base_prompt = prompt_manager.get_prompt(streamer_name)
    
    if not base_prompt:
        print(f"[错误] 未找到主播 [{streamer_name}] 的提示词")
        print("可用的主播:", ", ".join(prompt_manager.list_streamers()))
        return
    
    print(f"[OK] 已加载 [{streamer_name}] 的提示词")
    print(f"提示词长度: {len(base_prompt)} 字符")
    
    # 2. 扫描目录下的视频和srt文件
    print("\n[2/5] 扫描视频文件...")
    
    # 支持多种视频格式
    video_extensions = ['*.mp4', '*.mkv', '*.avi', '*.mov', '*.wmv', '*.flv']
    video_files = []
    for ext in video_extensions:
        video_files.extend(glob.glob(os.path.join(input_dir, ext)))
    
    video_files = sorted(video_files)
    
    if not video_files:
        print("[错误] 未找到视频文件")
        return
    
    print(f"找到 {len(video_files)} 个视频文件:")
    for vf in video_files:
        print(f"  - {os.path.basename(vf)}")
    
    # 3. 收集所有字幕文件内容
    print("\n[3/5] 解析字幕文件...")
    all_subtitles = []
    file_prefixes = []
    
    for video_path in video_files:
        video_name = os.path.basename(video_path)
        file_prefix = get_file_prefix(video_name)
        
        # 尝试多种字幕文件命名方式
        srt_candidates = [
            os.path.splitext(video_name)[0] + ".srt",  # 同名srt
            video_name + ".srt",  # 视频名.srt
        ]
        
        srt_path = None
        for candidate in srt_candidates:
            candidate_path = os.path.join(input_dir, candidate)
            if os.path.exists(candidate_path):
                srt_path = candidate_path
                break
        
        if not srt_path:
            print(f"  [跳过] {video_name} (未找到字幕文件)")
            continue
        
        subtitles = parse_srt_file(srt_path)
        if not subtitles:
            print(f"  [警告] 字幕文件为空或解析失败: {os.path.basename(srt_path)}")
            continue
        
        all_subtitles.append({
            'prefix': file_prefix,
            'video_name': video_name,
            'subtitles': subtitles,
            'srt_path': srt_path
        })
        file_prefixes.append(file_prefix)
        
        print(f"  [OK] {file_prefix}: {len(subtitles)} 条字幕 ({os.path.basename(srt_path)})")
    
    if not all_subtitles:
        print("[错误] 没有可用的字幕文件")
        return
    
    # 4. 显示字幕统计
    print("\n[4/5] 字幕文件统计...")
    print(f"总共收集到 {len(all_subtitles)} 个字幕文件:")
    for item in all_subtitles:
        print(f"  - [{item['prefix']}] {item['video_name']}: {len(item['subtitles'])} 条字幕")
    
    # 5. 调用LLM API
    print("\n[5/5] 调用LLM API分析...")
    print(f"总共需要处理 {len(all_subtitles)} 个字幕文件")
    
    MAX_PROMPT_LENGTH = 12000
    all_segments = []
    
    # 始终使用分批处理，确保每个文件都被单独分析
    print(f"[提示] 将逐个文件分析...")
    
    for idx, item in enumerate(all_subtitles, 1):
        prefix = item['prefix']
        video_name = item['video_name']
        subtitles = item['subtitles']
        
        print(f"\n[{idx}/{len(all_subtitles)}] 处理文件: {video_name} (标识: {prefix})")
        print(f"  字幕条数: {len(subtitles)}")
        
        subtitle_text = format_subtitles_for_prompt(subtitles, max_length=8000)
        
        single_prompt = f"""{base_prompt}

### 当前分析文件
文件名: {video_name}
文件标识: {prefix}

### 字幕内容
{subtitle_text}

### 重要说明
1. 当前分析的文件标识为 "{prefix}"，请在输出中设置 "segment": "{prefix}"
2. timestamp 格式为 "HH:MM:SS-HH:MM:SS"，是相对于该视频起始时间的偏移量（从00:00:00开始）
3. 请只从这个文件中提取高光片段

### segment字段填写规则（重要）
- segment字段必须完整填写当前分析的视频文件名（不含扩展名）
- 示例：如果当前文件是 "10-21-48.mp4"，则 segment = "10-21-48"
- 示例：如果当前文件是 "2026-05-27_20-30-00.mkv"，则 segment = "2026-05-27_20-30-00"
- 不要只写时间部分，必须写完整文件名前缀
- 不要截取或省略任何字符，保持原文件名完整

请严格按照JSON数组格式输出结果。"""
        
        print(f"  提示词长度: {len(single_prompt)} 字符")
        response = call_llm_api(single_prompt, CONFIG['llm'])
        
        if response:
            clips = extract_json_from_response(response)
            if clips:
                for clip in clips:
                    if 'segment' not in clip:
                        clip['segment'] = prefix
                all_segments.extend(clips)
                print(f"  [OK] 提取到 {len(clips)} 个片段")
            else:
                print(f"  [警告] 未能提取到有效片段")
        else:
            print(f"  [错误] API调用失败")
    
    # 6. 保存结果
    print(f"\n{'='*60}")
    print("处理完成!")
    print(f"{'='*60}")
    
    if all_segments:
        output_path = os.path.join(input_dir, CONFIG['output_file'])
        
        output_json = json.dumps(all_segments, ensure_ascii=False, indent=2)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(output_json)
        
        print(f"\n[OK] 已保存到: {output_path}")
        print(f"总共提取 {len(all_segments)} 个高光片段")
        
        segment_counts = {}
        for clip in all_segments:
            seg = clip.get('segment', 'unknown')
            segment_counts[seg] = segment_counts.get(seg, 0) + 1
        
        print(f"\n各文件片段分布:")
        for seg, count in sorted(segment_counts.items()):
            print(f"  [{seg}]: {count} 个片段")
        
        print(f"\n片段摘要:")
        for i, clip in enumerate(all_segments[:15], 1):
            segment = clip.get('segment', 'unknown')
            title = clip.get('title', '未命名')
            timestamp = clip.get('timestamp', 'unknown')
            print(f"  [{i}] [{segment}] {title} ({timestamp})")
        
        if len(all_segments) > 15:
            print(f"  ... 还有 {len(all_segments) - 15} 个片段")
    else:
        print("\n[警告] 未提取到任何高光片段")


if __name__ == "__main__":
    main()
