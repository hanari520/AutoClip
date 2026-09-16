#!/usr/bin/env python3
"""
Bilibili 主播梗/关键词搜索分析模块
搜索近N个月 B站视频标题，通过 LLM 分析推荐当前热门梗
"""

import os
import re
import sys
import json
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Callable

import bilibili_api.utils.network as _bili_net
_bili_net.select_client("httpx")

from bilibili_api import search

sys.stdout.reconfigure(encoding='utf-8')
logger = logging.getLogger("meme_search")


# ═══════════════════════════════════════════════════════════════════
# B站视频搜索
# ═══════════════════════════════════════════════════════════════════

async def search_videos(
    keyword: str,
    months: int = 3,
    max_results: int = 50,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list:
    """在B站搜索近N个月的视频，返回标题列表

    Returns:
        list[dict]: [{"title": str, "play": int, "pubdate": int, "bvid": str}, ...]
    """
    end = datetime.now()
    start = end - timedelta(days=months * 30)

    all_results = []
    page = 1

    def _log(msg):
        if progress_cb:
            progress_cb(msg)
        logger.info(msg)

    _log(f"搜索关键词: {keyword}，时间 {start.strftime('%Y-%m-%d')} ~ {end.strftime('%Y-%m-%d')}")

    while len(all_results) < max_results:
        try:
            result = await search.search_by_type(
                keyword=keyword,
                search_type=search.SearchObjectType.VIDEO,
                order_type=search.OrderVideo.PUBDATE,
                time_start=start.strftime('%Y-%m-%d'),
                time_end=end.strftime('%Y-%m-%d'),
                page=page,
                page_size=30,
            )
        except Exception as e:
            _log(f"搜索失败(page={page}): {e}")
            break

        items = result.get('result', [])
        if not items:
            break

        for item in items:
            title_raw = item.get('title', '')
            # 去除 HTML 标签 (如 <em class="keyword">xxx</em>)
            title = re.sub(r'<[^>]+>', '', title_raw).strip()
            if not title:
                continue
            all_results.append({
                'title': title,
                'play': item.get('play', 0),
                'pubdate': item.get('pubdate', 0),
                'bvid': item.get('bvid', ''),
                'author': item.get('author', ''),
            })

        _log(f"  第{page}页: {len(items)}条，累计 {len(all_results)} 条")
        page += 1

        # 最多翻5页
        if page > 5:
            break

    # 按播放量排序，取前 max_results
    all_results.sort(key=lambda x: x['play'], reverse=True)
    all_results = all_results[:max_results]
    _log(f"最终获取 {len(all_results)} 条视频标题")

    return all_results


# ═══════════════════════════════════════════════════════════════════
# LLM 梗分析
# ═══════════════════════════════════════════════════════════════════

def build_meme_analysis_prompt(titles: list, streamer: str) -> str:
    """构建发送给LLM的分析提示词"""
    title_list = "\n".join(f"- {t['title']}" for t in titles[:40])

    prompt = f"""你是虚拟主播{streamer}的粉丝文化分析专家。以下是近3个月B站上与{streamer}相关的视频标题（按播放量排序，共{len(titles)}条）。

请分析这些标题，提取{streamer}当前的热门梗、流行语、粉丝讨论焦点。

### 视频标题列表
{title_list}

### 分析要求
1. 提取出现频率高、有梗属性的关键词/短语（至少5个，最多15个）
2. 区分：观众玩梗的关键词 vs 主播本身相关的特质词
3. 标注每个梗的热度（🔥高频/正常）和新颖度（🆕新梗/经典老梗）
4. 如果某个梗在多个标题里反复出现，说明它最近很火，务必收录
5. 给出每个梗的简短解释（10字以内）

### 输出格式
直接输出一个JSON对象，不要加markdown代码块标记：
{{
  "streamer": "{streamer}",
  "keywords": [
    {{
      "keyword": "梗关键词",
      "explanation": "简短解释",
      "heat": "🔥高频 或 正常",
      "novelty": "🆕新梗 或 经典老梗",
      "recommend": true
    }}
  ],
  "summary": "一句话总结当前的粉丝讨论风向"
}}

只输出JSON，不要加其他文字。"""
    return prompt


def analyze_titles(
    titles: list,
    streamer: str,
    llm_config: dict,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """用LLM分析视频标题，提取热门梗"""
    if not titles:
        return {"streamer": streamer, "keywords": [], "summary": "无数据"}

    import requests

    def _log(msg):
        if progress_cb:
            progress_cb(msg)
        logger.info(msg)

    _log(f"发送 {len(titles)} 条标题给LLM分析...")

    prompt = build_meme_analysis_prompt(titles, streamer)

    headers = {
        "Authorization": f"Bearer {llm_config.get('api_key', '')}",
        "Content-Type": "application/json",
    }

    body = {
        "model": llm_config.get("model", "deepseek-ai/DeepSeek-V4-Pro"),
        "messages": [
            {"role": "system", "content": "你是一个专业的虚拟主播粉丝文化分析专家。只输出JSON，不要加任何解释。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    try:
        resp = requests.post(
            f"{llm_config.get('base_url', 'https://api.siliconflow.cn/v1')}/chat/completions",
            headers=headers,
            json=body,
            timeout=120,
        )
        if resp.status_code != 200:
            _log(f"LLM请求失败: {resp.status_code} {resp.text[:200]}")
            return {"streamer": streamer, "keywords": [], "summary": f"API错误: {resp.status_code}"}

        content = resp.json()["choices"][0]["message"]["content"].strip()

        # 清理可能的markdown代码块
        if content.startswith("```"):
            content = re.sub(r'^```\w*\n?', '', content)
            content = re.sub(r'\n?```$', '', content)

        result = json.loads(content)
        _log(f"LLM分析完成: {len(result.get('keywords', []))} 个梗")
        return result
    except json.JSONDecodeError as e:
        _log(f"JSON解析失败: {e}")
        _log(f"原始响应: {content[:500]}")
        return {"streamer": streamer, "keywords": [], "summary": f"解析失败: {e}"}
    except Exception as e:
        _log(f"LLM分析异常: {e}")
        return {"streamer": streamer, "keywords": [], "summary": f"异常: {e}"}


# ═══════════════════════════════════════════════════════════════════
# 便捷入口
# ═══════════════════════════════════════════════════════════════════

def search_and_analyze(
    streamer: str,
    months: int = 3,
    max_results: int = 50,
    llm_config: Optional[dict] = None,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """一步完成搜索+分析"""
    if llm_config is None:
        # 兜底：从 configs/gui_config.json 读当前 provider 的 key / 端点 / 模型，避免硬编码密钥
        try:
            from llm_config import get_api_key, resolve_llm_base_url, resolve_default_model
            llm_config = {
                "api_key": get_api_key(),
                "base_url": resolve_llm_base_url(),
                "model": resolve_default_model(),
            }
        except Exception:
            llm_config = {
                "api_key": "",
                "base_url": "https://api.siliconflow.cn/v1",
                "model": "deepseek-ai/DeepSeek-V4-Pro",
            }

    titles = asyncio.run(search_videos(
        keyword=streamer,
        months=months,
        max_results=max_results,
        progress_cb=progress_cb,
    ))

    if not titles:
        return {"streamer": streamer, "keywords": [], "summary": "未搜到相关视频", "titles": []}

    result = analyze_titles(
        titles=titles,
        streamer=streamer,
        llm_config=llm_config,
        progress_cb=progress_cb,
    )

    result["titles"] = titles
    return result


# ═══════════════════════════════════════════════════════════════════
# CLI 入口（调试用）
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='B站主播梗搜索分析')
    parser.add_argument('streamer', help='主播名')
    parser.add_argument('--months', type=int, default=3, help='搜索月数(默认3)')
    parser.add_argument('--output', '-o', help='输出JSON文件路径')
    args = parser.parse_args()

    def log(msg):
        print(msg)

    result = search_and_analyze(
        streamer=args.streamer,
        months=args.months,
        progress_cb=log,
    )

    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(output)
        print(f"已保存到 {args.output}")
    else:
        print(output)
