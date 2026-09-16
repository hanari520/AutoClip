# -*- coding: utf-8 -*-
"""
多主播提示词管理模块
支持 A-SOUL 和 VirtuaReal 主播的切片提示词配置
"""

import os
import json
from pathlib import Path
try:
    from meme_keywords import merge_keywords, normalize_keywords
except ImportError:
    from .meme_keywords import merge_keywords, normalize_keywords


class StreamerPromptManager:
    """主播提示词管理器"""
    
    # 内置主播配置
    STREAMERS = {
        # A-SOUL 成员
        "嘉然": {
            "group": "A-SOUL",
            "keywords": ["嘉然", "然然", "嘉心糖", "一米吧", "草莓", "小草莓"],
            "traits": "甜美可爱，偶尔腹黑，身高梗，吃货属性",
            "style": "活泼元气，软萌",
            "color": "#9972F0"
        },
        "贝拉": {
            "group": "A-SOUL",
            "keywords": ["贝拉", "拉姐", "贝极星", "无头骑士", "山药", "队长"],
            "traits": "队长担当，武担，游戏力高，反差萌",
            "style": "帅气可靠，偶尔憨憨",
            "color": "#747DDB"
        },
        "乃琳": {
            "group": "A-SOUL",
            "keywords": ["乃琳", "乃老师", "奶淇琳", "坏女人", "知性", "御姐"],
            "traits": "知性大姐姐，坏女人人设，控场能力强",
            "style": "温柔知性，偶尔调皮",
            "color": "#906657"
        },
        "心宜": {
            "group": "A-SOUL",
            "keywords": ["心宜", "宜宝", "心仪", "植物之神", "游戏"],
            "traits": "游戏力高，植物之神，活泼",
            "style": "元气满满",
            "color": "#9555FF"
        },
        "思诺": {
            "group": "A-SOUL",
            "keywords": ["思诺", "诺诺", "思思", "歌姬", "唱歌"],
            "traits": "歌姬担当，温柔，努力",
            "style": "温柔治愈",
            "color": "#C889A8"
        },
        
        # VirtuaReal 成员
        "栞栞Shiori": {
            "group": "VirtuaReal",
            "keywords": ["栞栞", "Shiori", "獭獭栞", "老馋", "大家", "VirtuaReal"],
            "traits": "温柔灵动，共情力强，獭獭梗，老馋梗",
            "style": "温柔/灵动/共情",
            "color": "#6B8E23"
        },
        "岁己SUI": {
            "group": "VirtuaReal",
            "keywords": ["岁己", "SUI", "饼干岁", "大家", "VirtuaReal"],
            "traits": "温柔灵动，共情力强，饼干岁梗",
            "style": "温柔/灵动/共情",
            "color": "#DAA520"
        },
        "灰泽满Hazel": {
            "group": "VirtuaReal",
            "keywords": ["灰泽满", "Hazel", "下头", "绿冻", "懒狗", "大大方方", 
                        "弟弟", "好女孩", "我去你大爷的"],
            "traits": "温柔灵动，共情力强，下头梗，绿冻梗，懒狗梗",
            "style": "温柔/灵动/共情",
            "color": "#4682B4"
        }
    }
    
    def __init__(self, prompts_dir=None):
        """
        初始化提示词管理器
        
        Args:
            prompts_dir: 外部提示词目录路径，如 "D:\\提示词\\标题封面提示词"
        """
        self.prompts_dir = prompts_dir
        self.custom_prompts = {}
        
        # 加载外部提示词
        if prompts_dir and os.path.exists(prompts_dir):
            self._load_custom_prompts()
    
    def _load_custom_prompts(self):
        """从目录加载自定义提示词文件"""
        for filename in os.listdir(self.prompts_dir):
            if filename.endswith('.txt'):
                streamer_key = filename.replace('.txt', '')
                filepath = os.path.join(self.prompts_dir, filename)
                
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.custom_prompts[streamer_key] = content
                    print(f"[PromptManager] 已加载提示词: {streamer_key}")
                except Exception as e:
                    print(f"[PromptManager] 加载失败 {streamer_key}: {e}")
    
    def _load_streamer_config(self, name):
        """从 configs/streamers/{name}.json 加载梗搜索写入的关键词"""
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(project_root, 'configs', 'streamers', f'{name}.json')
        if not os.path.exists(config_path):
            return {}
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    
    def get_streamer(self, name):
        """
        获取主播配置
        
        Args:
            name: 主播名或关键词
            
        Returns:
            dict: 主播配置，未找到返回None
        """
        # 直接匹配
        if name in self.STREAMERS:
            config = dict(self.STREAMERS[name])  # 拷贝，避免污染原字典
        else:
            # 关键词模糊匹配
            config = None
            for key, cfg in self.STREAMERS.items():
                if name in key or key in name:
                    config = dict(cfg)
                    break
                for keyword in cfg.get("keywords", []):
                    if name in keyword or keyword in name:
                        config = dict(cfg)
                        break
                if config:
                    break
        
        if not config:
            return None
        
        # 合并 configs/streamers/{name}.json 中的关键词（梗搜索写入的）
        streamer_cfg = self._load_streamer_config(name)
        dynamic_keywords = normalize_keywords(streamer_cfg.get('keywords', []))
        if dynamic_keywords:
            config['keywords'] = merge_keywords(config.get('keywords', []), dynamic_keywords)
        
        return config
    
    def get_prompt(self, streamer_name):
        """
        获取主播的完整提示词
        
        Args:
            streamer_name: 主播名称
            
        Returns:
            str: 提示词内容
        """
        # 优先使用自定义提示词
        for key, prompt in self.custom_prompts.items():
            if streamer_name in key or key in streamer_name:
                # 检查梗搜索写入的动态关键词
                streamer_cfg = self._load_streamer_config(streamer_name)
                dynamic_keywords = normalize_keywords(streamer_cfg.get('keywords', []))
                if dynamic_keywords:
                    extra = "\n\n**【梗搜索动态更新】粉丝近期热议关键词：**\n" + "、".join(dynamic_keywords)
                    extra += "\n请重点关注涉及以上关键词的互动片段，这些是近期粉丝圈子里的热门话题。\n"
                    prompt = prompt + extra
                return prompt
        
        # 返回内置提示词模板（get_streamer 会合并动态关键词）
        return self._build_default_prompt(streamer_name)
    
    def _build_default_prompt(self, streamer_name):
        """构建默认提示词"""
        config = self.get_streamer(streamer_name)
        if not config:
            return None
        
        keywords_str = "、".join(config.get("keywords", []))
        
        prompt = f"""现有{config['group']}虚拟主播{streamer_name}的多个字幕文件，每个文件对应一个视频片段，文件名包含时间标识（如"10-21-48"表示该片段开始于10点21分48秒）。请基于文件标题（直播主题）和字幕内容，理解整个直播流程，筛选并总结其中的"高光互动片段"。

### 重要说明
1. 输入包含多个字幕文件，每个文件有独立的文件名前缀（如"10-21-48"、"10-51-54"等）
2. 请在分析时关注文件名前缀，每个高光片段必须标注它来自哪个文件
3. timestamp 时间戳是相对于该文件起始时间的偏移量（从00:00:00开始）
4. 输出时设置 "segment" 字段为对应的文件名前缀（如 "10-21-48"）

### 一、名场面/高光互动片段判断标准
1. **核心类型：** 粉丝热议度高的梗点、{streamer_name}的{config['traits']}类名场面、控场感极强的互动发言、突发小状况及从容应对
2. **重点关注：** 涉及"{keywords_str}"等玩梗互动或自我调侃

### 二、筛选与处理原则
1. **时长强制管控：** 每个输出片段的时长应该在 **15秒-150秒** 之间。若核心互动点（如一句吐槽）时长不足 15 秒，必须基于上下文逻辑向前寻找铺垫（如话题引入）或向后延伸（如全员的笑声、后续反应），起始时间和结束时间必须是txt文件中某一个字幕的时间点，必须保证语义完整才能结束，并且前后增加有30秒的可编辑时间。
2. **内容筛选：**  优先保留辨识度高、记忆点强、适合二次传播或回顾的片段，梗相关、直播中出现意外的情况的片段可适当提高入选优先级。
3. **片段数量：** 请精选优先级最高的高光片段，宁缺毋滥，不要凑数。具体数量根据视频时长动态调整。

### 三、文案生成策略
请按照以下逻辑为每个片段生成标题和封面文字：

1. **Title（标题）：叙事感 + 情绪钩子**
   * **要求：** 不要只用"震惊"二字，要用一句话概括发生的趣事，但语气要夸张或带情绪。
   * **结构：** [情绪/状态前缀] + [主语做了什么/发生了什么事]。
   * *示例：* "乃琳当初面试枝江，还以为这边是配音的呢！"、"乃琳手滑取关嘉然多邻国好友！连胜天数中断破防了！"

2. **Cover Text 1（封面主字）：视觉核心**
   * **要求：** 提炼片段中最核心的冲突、事件或梗，字数控制在 **3-10字**，要求醒目、直接。
   * *示例：* "我以为枝江是配音的"、"乃琳手滑取关嘉然"。

3. **Cover Text 2（封面副字）：补充/吐槽/反转**
   * **要求：** 对主字进行补充说明，或者是一句犀利的吐槽、后续的结果，字数控制在 **3-10字**。
   * *示例：* "结果要出道"、"连胜中断破防了！"。

### 三.五、多文件输出格式重要说明
1. 每个高光片段必须包含 "segment" 字段，值为该片段所属的完整视频文件名（不含扩展名），以便脚本匹配正确的视频文件
2. segment字段必须完整填写，不要截取或省略任何字符。例如文件名为 "10-21-48.mp4" 时，segment 必须是 "10-21-48"，不能写成 "10-21" 或其他缩写
3. timestamp 格式为 "HH:MM:SS-HH:MM:SS"，是相对于该 segment 视频起始时间的偏移量（从00:00:00开始，不是绝对时间）
4. 不同文件的片段可以混合输出，但每个片段的 segment 字段必须正确标注来源
5. 如果某个高光片段跨越多个文件，选择包含核心内容最多的那个文件作为 segment

### segment字段填写规则（重要）
- segment字段必须填写完整的视频文件名（不含扩展名）
- 示例："10-21-48"（对应 10-21-48.mp4）
- 示例："2026-05-27_20-30-00"（对应 2026-05-27_20-30-00.mkv）
- 不要只截取部分字符，必须保持原文件名完整
- 不要添加额外内容，也不要省略任何字符

例如：如果一个片段在文件 "10-21-48.mp4" 的第5分钟开始，第8分钟结束，则输出：
- "segment": "10-21-48"
- "timestamp": "00:05:00-00:08:00"

### 四、输出格式
最终输出一个JSON数组，每个元素结构如下：
    "segment": "完整视频文件名（不含扩展名，如 10-21-48、2026-05-27_20-30-00）",
    "timestamp": "起始时间-结束时间",
    "title": "标题（叙事感强，如：乃琳手滑取关嘉然多邻国好友！连胜天数中断破防了！）",
    "cover_text_1": "封面文字1（核心事件，如：乃琳手滑取关嘉然）",
    "cover_text_2": "封面文字2（补充或吐槽，如：连胜中断破防了！）",
    "summary": "1-2 句总结片段内容（根据字幕自动纠错还原原意）",
    "highlight_reason": "说明为何这是高光/有意思片段，并标注对应的梗（如有）"
"""
        return prompt
    
    def list_streamers(self):
        """列出所有支持的主播"""
        return list(self.STREAMERS.keys())
    
    def list_groups(self):
        """按分组列出主播"""
        groups = {}
        for name, config in self.STREAMERS.items():
            group = config.get("group", "其他")
            if group not in groups:
                groups[group] = []
            groups[group].append(name)
        return groups


# 全局实例
_prompt_manager = None

def get_prompt_manager(prompts_dir=None):
    """获取全局提示词管理器实例"""
    global _prompt_manager
    if _prompt_manager is None:
        _prompt_manager = StreamerPromptManager(prompts_dir)
    return _prompt_manager


def main():
    """测试"""
    import argparse
    
    parser = argparse.ArgumentParser(description='主播提示词管理工具')
    parser.add_argument('--dir', '-d', default='',
                        help='提示词目录路径')
    parser.add_argument('--list', '-l', action='store_true',
                        help='列出所有主播')
    parser.add_argument('--get', '-g', help='获取指定主播的提示词')
    
    args = parser.parse_args()
    
    manager = StreamerPromptManager(args.dir)
    
    if args.list:
        print("=" * 50)
        print("支持的主播列表:")
        print("=" * 50)
        groups = manager.list_groups()
        for group, names in groups.items():
            print(f"\n【{group}】")
            for name in names:
                config = manager.get_streamer(name)
                print(f"  - {name}: {config['traits']}")
    
    if args.get:
        print(f"\n{'=' * 50}")
        print(f"主播 [{args.get}] 的提示词:")
        print("=" * 50)
        prompt = manager.get_prompt(args.get)
        if prompt:
            print(prompt[:500] + "...")
        else:
            print("未找到该主播的提示词")


if __name__ == "__main__":
    main()
