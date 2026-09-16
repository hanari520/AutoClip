# -*- coding: utf-8 -*-
"""
LLM 供应商统一配置。

GUI、命令行流水线、各分析脚本共用这一处映射，保证「硅基流动 / DeepSeek 官方」
两种 provider 在任意入口下行为一致。

provider 取值（存在 configs/gui_config.json 的 llm_provider 字段）：
    - "siliconflow"  -> 硅基流动，端点 https://api.siliconflow.cn/v1
    - "deepseek"     -> DeepSeek 官方，端点 https://api.deepseek.com/v1
    - "zhipu"        -> 智谱 BigModel，端点 https://open.bigmodel.cn/api/paas/v4
    - "zhipu-coding" -> 智谱 Coding 套餐，专属端点 https://open.bigmodel.cn/api/coding/paas/v4

未配置 llm_provider 时默认硅基流动（保持历史行为不变）。
也可通过 llm_base_url 直接指定完整端点（优先级高于 llm_provider）。
"""
import os

LLM_PROVIDER_BASE_URLS = {
    "siliconflow": "https://api.siliconflow.cn/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "opencode-go": "https://opencode.ai/zen/go/v1",
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "zhipu-coding": "https://open.bigmodel.cn/api/coding/paas/v4",
}
# 各 provider 的默认模型名（键不一致：硅基流动带 deepseek-ai/ 前缀，官方无前缀）
# 注意：DeepSeek 官方已停用 deepseek-chat / deepseek-reasoner（2026-07-24），迁移到 V4 系列。
LLM_PROVIDER_MODELS = {
    "siliconflow": "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek": "deepseek-v4-flash",
    "opencode-go": "deepseek-v4-flash",
    "zhipu": "glm-5.3-flash",
    "zhipu-coding": "glm-5.3-flash",
}
# 各 provider 的常用候选模型（供 GUI 下拉选择 / 用户参考）
LLM_PROVIDER_MODEL_OPTIONS = {
    "siliconflow": [
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4-Pro",
    ],
    "deepseek": [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ],
    "opencode-go": [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ],
    "zhipu": [
        "glm-5.3-flash",
        "glm-5.3",
        "glm-5.2",
    ],
    # Coding 套餐仅正式支持 GLM-5.3 / GLM-5.3-Flash（旧型号自动映射到这两个）
    "zhipu-coding": [
        "glm-5.3-flash",
        "glm-5.3",
    ],
}
DEFAULT_PROVIDER = "siliconflow"

# 项目根目录下 configs/gui_config.json 的默认位置
DEFAULT_GUI_CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "configs",
    "gui_config.json",
)


def load_gui_config(gui_config_path=None):
    """读取 gui_config.json（失败返回空字典）。"""
    import json
    path = gui_config_path or DEFAULT_GUI_CONFIG
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def resolve_provider(gui_config=None):
    """返回当前 provider（siliconflow / deepseek），非法或缺失时回退默认。"""
    cfg = gui_config if gui_config is not None else load_gui_config()
    provider = str(cfg.get("llm_provider", "") or "").strip().lower()
    if provider in LLM_PROVIDER_BASE_URLS:
        return provider
    return DEFAULT_PROVIDER


def resolve_llm_base_url(override=None, gui_config=None):
    """解析 LLM API 端点，优先级：
    1. 显式 override（通常来自 --base-url 参数）
    2. gui_config 的 llm_base_url（完整端点直配）
    3. gui_config 的 llm_provider 映射
    4. 默认硅基流动
    """
    if override:
        return str(override).strip()
    cfg = gui_config if gui_config is not None else load_gui_config()
    base_url = str(cfg.get("llm_base_url", "") or "").strip()
    if base_url:
        return base_url
    return LLM_PROVIDER_BASE_URLS[resolve_provider(cfg)]


def resolve_default_model(gui_config=None):
    """返回当前 provider 的默认模型名（仅当未显式指定模型时使用）。"""
    cfg = gui_config if gui_config is not None else load_gui_config()
    return LLM_PROVIDER_MODELS[resolve_provider(cfg)]


def get_api_key_for_provider(provider, gui_config=None):
    """返回指定 provider 的 API Key。

    优先读 gui_config 的 llm_api_keys[provider]（分 provider 独立存储）；
    兼容旧的 global_api_key（历史上只存过一个 key，一律视为 siliconflow 的 key）。
    """
    cfg = gui_config if gui_config is not None else load_gui_config()
    provider = (provider or "").strip().lower()
    keys = cfg.get("llm_api_keys") or {}
    if isinstance(keys, dict):
        val = str(keys.get(provider, "") or "").strip()
        if val:
            return val
    # 兼容旧字段：global_api_key 视为 siliconflow 的 key
    if provider == "siliconflow" or provider not in LLM_PROVIDER_BASE_URLS:
        legacy = str(cfg.get("global_api_key", "") or "").strip()
        if legacy:
            return legacy
    return ""


def get_api_key(gui_config=None):
    """返回「当前 provider」的 API Key（跟随 llm_provider）。"""
    cfg = gui_config if gui_config is not None else load_gui_config()
    return get_api_key_for_provider(resolve_provider(cfg), cfg)
