# -*- coding: utf-8 -*-
"""
情绪检测模块 - 基于 FunASR emotion2vec
参考: FunASR 上游仓库 funasr/models/emotion2vec

功能:
1. 使用 emotion2vec 模型对音频进行情绪识别
2. 支持 utterance/frame 两种粒度
3. 输出情绪标签和置信度分数
4. 生成带情绪标签的 SRT 字幕

情绪标签映射:
- happy: 开心
- sad: 难过  
- angry: 生气
- neutral: 平静
- excited: 兴奋
- surprised: 惊讶
- disgusted: 厌恶
- fearful: 恐惧
"""

import os
import sys
import json
import torch
import numpy as np
from pathlib import Path
from typing import List, Dict, Optional, Tuple


class Emotion2VecAnalyzer:
    """基于 emotion2vec 的音频情绪分析器"""
    
    # 情绪标签映射（英文 -> 中文）
    EMOTION_MAP = {
        'happy': '开心',
        'sad': '难过',
        'angry': '生气',
        'neutral': '平静',
        'excited': '兴奋',
        'surprised': '惊讶',
        'disgusted': '厌恶',
        'fearful': '恐惧',
        'unknown': '未知'
    }
    
    # 模型配置
    MODEL_ID = "iic/emotion2vec_plus_base"  # 基础版模型（约300MB）
    # MODEL_ID = "iic/emotion2vec_plus_large"  # 大型模型（约800MB）
    
    def __init__(self, model_id: str = None, device: str = None, use_local: bool = True):
        """
        初始化情绪分析器
        
        Args:
            model_id: 模型ID，默认使用 emotion2vec_plus_base
            device: 计算设备 (cuda/cpu)，None为自动选择
            use_local: 是否优先使用本地缓存
        """
        self.model_id = model_id or self.MODEL_ID
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.use_local = use_local
        self.model = None
        
        # 检查 FunASR 安装
        self._check_installation()
        
    def _check_installation(self):
        """检查 FunASR 是否已安装"""
        try:
            import funasr
            print(f"[Emotion2Vec] FunASR 版本: {funasr.__version__}")
        except ImportError:
            print("[Emotion2Vec] FunASR 未安装，正在安装...")
            import subprocess
            subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'funasr', '-q'])
            print("[Emotion2Vec] FunASR 安装完成")
    
    def load_model(self):
        """加载 emotion2vec 模型"""
        if self.model is not None:
            return
            
        from funasr import AutoModel
        
        print(f"[Emotion2Vec] 加载模型: {self.model_id}")
        print(f"[Emotion2Vec] 设备: {self.device}")
        
        # 检查本地缓存
        model_path = self.model_id
        if self.use_local:
            local_cache = self._get_local_cache_path()
            if local_cache and os.path.exists(local_cache):
                print(f"[Emotion2Vec] 使用本地缓存: {local_cache}")
                model_path = local_cache
        
        try:
            # 按 FunASR 标准用法加载模型
            # 参考: FunASR 上游 examples/industrial_data_pretraining/emotion2vec/demo.py
            self.model = AutoModel(
                model=model_path,
                hub="ms",  # 使用 ModelScope 国内镜像
                device=self.device,  # 使用指定的设备（cpu/cuda）
                disable_update=True,  # 禁用更新检查
                ncpu=4,  # CPU 线程数
            )
            print("[Emotion2Vec] 模型加载完成")
        except Exception as e:
            print(f"[Emotion2Vec] 模型加载失败: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def _get_local_cache_path(self) -> Optional[str]:
        """获取本地缓存路径"""
        cache_dir = os.path.expanduser(r"~\.cache\modelscope\hub\models")
        model_name = self.model_id.replace("/", os.sep)
        local_path = os.path.join(cache_dir, model_name)
        return local_path if os.path.exists(local_path) else None
    
    def analyze_audio(self, audio_path: str, granularity: str = "utterance") -> List[Dict]:
        """
        分析音频文件的情绪
        
        Args:
            audio_path: 音频文件路径 (.wav, .mp3, etc.)
            granularity: 分析粒度 ("utterance"=整段, "frame"=帧级别)
        
        Returns:
            情绪分析结果列表，每项包含:
            - label: 情绪标签 (英文)
            - label_cn: 情绪标签 (中文)
            - score: 置信度分数 (0-1)
            - timestamp: 时间戳 (frame粒度时有)
        """
        if self.model is None:
            self.load_model()
        
        print(f"[Emotion2Vec] 分析音频: {os.path.basename(audio_path)}")
        print(f"[Emotion2Vec] 粒度: {granularity}")
        
        # 调用模型进行推理
        # 参考 model.py 的 inference 方法
        try:
            result = self.model.generate(
                audio_path,
                granularity=granularity,
                extract_embedding=False,  # 不需要提取嵌入向量
            )
        except Exception as e:
            print(f"[Emotion2Vec] 分析失败: {e}")
            # 如果失败，尝试使用基础推理方式
            result = self._fallback_analyze(audio_path)
        
        # 解析结果
        return self._parse_result(result, granularity)
    
    def _fallback_analyze(self, audio_path: str) -> List[Dict]:
        """备用分析方式（当 generate 失败时）"""
        print("[Emotion2Vec] 使用备用分析方式...")
        
        # 这里可以实现基于规则的简单情绪检测
        # 或者使用其他模型作为后备
        return [{
            'key': 'fallback',
            'labels': ['neutral'],
            'scores': [1.0]
        }]
    
    def _parse_result(self, result, granularity: str = "utterance") -> List[Dict]:
        """
        解析模型输出结果
        
        参考 model.py inference 方法的返回格式:
        {
            'key': 样本标识,
            'labels': [情绪标签列表],
            'scores': [对应分数列表],
            'feats': 嵌入向量 (可选)
        }
        """
        parsed_results = []
        
        if not result:
            return parsed_results
        
        # 处理批量结果
        if isinstance(result, list):
            for item in result:
                parsed = self._parse_single_result(item)
                parsed_results.extend(parsed)
        else:
            parsed = self._parse_single_result(result)
            parsed_results.extend(parsed)
        
        return parsed_results
    
    def _parse_single_result(self, result_item: Dict) -> List[Dict]:
        """解析单个结果项"""
        parsed = []
        
        if isinstance(result_item, list):
            # 处理列表格式
            for item in result_item:
                parsed.extend(self._parse_single_result(item))
            return parsed
        
        if not isinstance(result_item, dict):
            return parsed
        
        # 获取标签和分数
        labels = result_item.get('labels', [])
        scores = result_item.get('scores', [])
        
        if not labels or not scores:
            return parsed
        
        # 找到最高分的情绪
        best_idx = np.argmax(scores)
        best_label = labels[best_idx] if best_idx < len(labels) else 'unknown'
        best_score = scores[best_idx] if best_idx < len(scores) else 0.0
        
        # 构建结果
        emotion_result = {
            'label': best_label,
            'label_cn': self.EMOTION_MAP.get(best_label, best_label),
            'score': round(float(best_score), 4),
            'all_labels': labels,
            'all_scores': [round(float(s), 4) for s in scores],
            'timestamp': result_item.get('timestamp', 0),
        }
        
        parsed.append(emotion_result)
        return parsed
    
    def analyze_video(self, video_path: str, srt_path: str = None, 
                      granularity: str = "utterance") -> Dict:
        """
        分析视频的情绪（基于音频轨道）
        
        Args:
            video_path: 视频文件路径
            srt_path: 对应的 SRT 字幕文件路径（用于时间对齐）
            granularity: 分析粒度
        
        Returns:
            {
                'video_path': 视频路径,
                'emotion_segments': [情绪片段列表],
                'dominant_emotion': 主导情绪,
                'emotion_distribution': 情绪分布统计
            }
        """
        from funasr.utils.load_utils import load_audio_text_image_video
        
        print(f"[Emotion2Vec] 分析视频: {os.path.basename(video_path)}")
        
        # 加载音频
        audio_data = load_audio_text_image_video(
            video_path,
            fs=16000,
            audio_fs=16000,
            data_type="sound"
        )
        
        # 分析情绪
        if self.model is None:
            self.load_model()
        
        # 使用模型生成结果
        result = self.model.generate(
            video_path,
            granularity=granularity,
            extract_embedding=False
        )
        
        # 解析结果
        emotions = self._parse_result(result, granularity)
        
        # 统计情绪分布
        emotion_dist = {}
        for e in emotions:
            label = e['label']
            emotion_dist[label] = emotion_dist.get(label, 0) + 1
        
        # 找出主导情绪
        dominant = max(emotion_dist.items(), key=lambda x: x[1])[0] if emotion_dist else 'unknown'
        
        return {
            'video_path': video_path,
            'emotion_segments': emotions,
            'dominant_emotion': dominant,
            'dominant_emotion_cn': self.EMOTION_MAP.get(dominant, dominant),
            'emotion_distribution': emotion_dist,
            'total_segments': len(emotions)
        }
    
    def analyze_directory(self, input_dir: str, granularity: str = "utterance",
                          model_id: str = None):
        """
        Analyze all SRT files in a directory, find matching video files,
        run emotion detection, and write emotions directly into SRT files.

        Args:
            input_dir: Directory containing SRT files and video/audio files
            granularity: Analysis granularity (utterance or frame)
            model_id: Model ID override

        Returns:
            Summary dict with per-file results
        """
        import glob

        if not os.path.isdir(input_dir):
            raise ValueError(f"Not a directory: {input_dir}")

        # If model specified and different, reload
        if model_id and model_id != self.model_id:
            self.model_id = model_id
            self.model = None

        # Find all SRT files
        srt_files = glob.glob(os.path.join(input_dir, '*.srt'))
        print(f"[Emotion2Vec] Found {len(srt_files)} SRT file(s) in: {input_dir}")

        if not srt_files:
            print("[Emotion2Vec] No SRT files found, trying to process video files directly...")
            video_exts = ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.webm', '.ts', '.m4v')
            video_files = []
            for ext in video_exts:
                video_files.extend(glob.glob(os.path.join(input_dir, '*' + ext)))

            if not video_files:
                print("[Emotion2Vec] No video files found either.")
                return {'error': 'No SRT or video files found', 'results': []}

            results = []
            for vf in video_files:
                r = self.analyze_video(vf, granularity=granularity)
                results.append(r)
            return {'results': results, 'total': len(results)}

        audio_exts = ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.webm', '.ts', '.m4v',
                       '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma')

        results = []
        for srt_path in srt_files:
            base = os.path.splitext(srt_path)[0]
            srt_name = os.path.basename(srt_path)
            print(f"\n[Emotion2Vec] Processing: {srt_name}")

            # Find matching video/audio file (same basename, different extension)
            video_path = None
            for ext in audio_exts:
                candidate = base + ext
                if os.path.exists(candidate):
                    video_path = candidate
                    break

            if video_path is None:
                # Try finding any video in the directory as fallback
                for ext in audio_exts:
                    candidates = glob.glob(os.path.join(input_dir, '*' + ext))
                    if candidates:
                        video_path = candidates[0]
                        print(f"  Using fallback video: {os.path.basename(video_path)}")
                        break

            if video_path is None:
                print(f"  SKIP: No matching video found for {srt_name}")
                continue

            print(f"  Video: {os.path.basename(video_path)}")

            # Run emotion detection on the video
            result = self.analyze_video(video_path, granularity=granularity)
            emotions = result.get('emotion_segments', [])

            if not emotions:
                print(f"  No emotions detected, skipping SRT write")
                results.append(result)
                continue

            # Write emotions directly into SRT file (overwrite original)
            self.generate_emotion_srt(srt_path, emotions, output_path=srt_path)

            result['srt_path'] = srt_path
            result['emotions_written'] = len(emotions)
            results.append(result)

        return {'results': results, 'total': len(results), 'srt_dir': input_dir}

    def generate_emotion_srt(self, srt_path: str, emotion_data: List[Dict], 
                            output_path: str = None) -> str:
        """
        生成带情绪标签的 SRT 字幕
        
        在每条字幕前添加情绪标签，如 [开心] 或 [难过·0.95]
        
        Args:
            srt_path: 原始 SRT 文件路径
            emotion_data: 情绪分析结果
            output_path: 输出路径，None则覆盖原文件
        
        Returns:
            输出文件路径
        """
        if output_path is None:
            output_path = srt_path  # Default: overwrite original SRT file
        
        print(f"[Emotion2Vec] 生成情绪字幕: {os.path.basename(output_path)}")
        
        # 读取原始 SRT
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
        
        # 解析 SRT
        subtitles = self._parse_srt(srt_content)
        
        # 为每条字幕匹配情绪
        # 简化处理：按顺序分配情绪结果
        for i, sub in enumerate(subtitles):
            if i < len(emotion_data):
                emotion = emotion_data[i]
                tag = f"[{emotion['label_cn']}]"
                if emotion['score'] > 0.8:
                    tag = f"[{emotion['label_cn']}·{emotion['score']:.2f}]"
                sub['text'] = f"{tag} {sub['text']}"
        
        # 写入新 SRT
        with open(output_path, 'w', encoding='utf-8') as f:
            for sub in subtitles:
                f.write(f"{sub['index']}\n")
                f.write(f"{sub['start']} --> {sub['end']}\n")
                f.write(f"{sub['text']}\n\n")
        
        print(f"[Emotion2Vec] 情绪字幕已保存: {output_path}")
        return output_path
    
    def _parse_srt(self, srt_content: str) -> List[Dict]:
        """解析 SRT 内容"""
        subtitles = []
        blocks = srt_content.strip().split('\n\n')
        
        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            
            try:
                index = int(lines[0].strip())
                time_line = lines[1].strip()
                text = ' '.join(lines[2:]).strip()
                
                subtitles.append({
                    'index': index,
                    'start': time_line.split(' --> ')[0],
                    'end': time_line.split(' --> ')[1],
                    'text': text
                })
            except:
                continue
        
        return subtitles
    
    def save_results(self, results: Dict, output_path: str):
        """保存分析结果到 JSON"""
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"[Emotion2Vec] 结果已保存: {output_path}")


def main():
    """Command-line entry point"""
    import argparse

    parser = argparse.ArgumentParser(description='Emotion detection tool (based on emotion2vec)')
    parser.add_argument('input', help='Input: directory (with SRT files), SRT file, or video/audio file')
    parser.add_argument('-o', '--output', help='Output JSON path (optional)')
    parser.add_argument('-g', '--granularity', default='utterance',
                        choices=['utterance', 'frame'],
                        help='Analysis granularity (default: utterance)')
    parser.add_argument('-m', '--model', default='iic/emotion2vec_plus_base',
                        help='Model ID (default: iic/emotion2vec_plus_base)')
    parser.add_argument('-d', '--device', default=None,
                        help='Device (cuda/cpu, default: auto)')

    args = parser.parse_args()

    # Initialize analyzer
    analyzer = Emotion2VecAnalyzer(
        model_id=args.model,
        device=args.device
    )

    # Route based on input type
    if os.path.isdir(args.input):
        # Directory mode: process all SRT files, find videos, write emotions to SRT
        results = analyzer.analyze_directory(
            args.input,
            granularity=args.granularity,
            model_id=args.model
        )
    elif args.input.endswith('.srt'):
        # SRT file mode: find matching video, analyze, write emotions directly to SRT
        base = args.input.rsplit('.', 1)[0]
        audio_exts = ('.mp4', '.mkv', '.flv', '.avi', '.mov', '.webm', '.ts', '.m4v',
                       '.mp3', '.wav', '.flac', '.m4a', '.aac', '.ogg', '.wma')
        video_path = None
        for ext in audio_exts:
            candidate = base + ext
            if os.path.exists(candidate):
                video_path = candidate
                break

        if video_path is None:
            print(f"[Error] No matching video/audio found for: {args.input}")
            sys.exit(1)

        print(f"[Emotion2Vec] Found video: {os.path.basename(video_path)}")
        results = analyzer.analyze_video(video_path, granularity=args.granularity)
        emotions = results.get('emotion_segments', [])
        analyzer.generate_emotion_srt(args.input, emotions, output_path=args.input)
        print(f"[Done] Emotions written to: {args.input}")

    elif args.input.endswith(('.wav', '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wma')):
        # Audio file
        emotions = analyzer.analyze_audio(args.input, args.granularity)
        results = {
            'audio_path': args.input,
            'emotion_segments': emotions,
            'total_segments': len(emotions)
        }
    else:
        # Video file
        results = analyzer.analyze_video(args.input, granularity=args.granularity)

    # Save JSON output
    if args.output:
        analyzer.save_results(results, args.output)
    elif not os.path.isdir(args.input):
        # Don't print huge JSON for directory mode
        print("\nResults:")
        print(json.dumps(results, ensure_ascii=False, indent=2))

    print("\n[Done] Emotion detection completed")



if __name__ == '__main__':
    main()
