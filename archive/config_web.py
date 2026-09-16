# -*- coding: utf-8 -*-
"""
AutoClip 配置生成器 — 桌面版（电脑浏览器）
启动: python archive\config_web.py
浏览器: http://localhost:5001
"""
import json, os, sys, threading, webbrowser, asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from utils.meme_keywords import merge_keywords, normalize_keywords

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_DIR = os.path.join(PROJECT_ROOT, "configs", "streamers")
INPUT_BASE = os.environ.get("AUTOCLIP_RECORDING_ROOTS", "").split(";")[0]

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
        "name": "🎭 VUP虚拟主播（通用）",
        "desc": "虚拟UP主：SenseVoiceSmall + LLM + 虚拟主播分区(tid=208)",
        "cfg": {
            "description": "默认一键切片+投稿配置",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
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
        "desc": "音乐翻唱分区(tid=36) + LLM高光",
        "cfg": {
            "description": "默认一键切片+投稿配置",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
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
        "desc": "杂谈/日常向：生活·日常分区(tid=154)",
        "cfg": {
            "description": "默认一键切片+投稿配置",
            "step1_prepare": DEFAULT_STEP1_PREPARE.copy(),
            "step2_subtitle": {"engine":"funasr","model":"SenseVoiceSmall","language":"zh","vad":True,"speaker_diarization":False},
            "step3_highlight": {"mode":"llm","prompt_dir":""},
            "step4_clip": {"output_dir":"{input_dir}/切片"},
            "step5_burn": {"skip_asr":False,"engine":"whisper","model":"large-v3-turbo","language":"zh","vad":True,"font":"MaokenAssortedSans","font_size":80,"outline":8,"shadow":False,"shadow_color":"black","margin_v":80,"color":"&H00FFFFFF","outline_color":"&H00000000"},
            "step6_cover": {"style":"random_1_4","font":"MaoKenShiJinHei-2.ttf","output_dir":"{input_dir}/切片/封面","match_subbed_only":True},
            "step7_upload": {"tid":154,"tid_name":"生活·日常","copyright":"original","no_reprint":False,"tags":["日常","ai切片"]},
        }
    },
}

HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AutoClip 配置生成器</title>
<style>
:root{--bg:#0d0d1a;--card:#141428;--card2:#1a1a30;--border:#2a2a45;--accent:#6366f1;--green:#22c55e;--amber:#f59e0b;--red:#ef4444;--text:#e0e0f0;--muted:#7a7a99}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,sans-serif;padding:16px;max-width:600px;margin:0 auto;min-height:100vh}
h1{font-size:20px;margin-bottom:4px}
.sub{font-size:13px;color:var(--muted);margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:14px;margin-bottom:10px}
.card-title{font-weight:700;font-size:15px;margin-bottom:10px;display:flex;align-items:center;gap:8px}
.selector,.input{width:100%;padding:10px 12px;border-radius:10px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:14px;margin-bottom:6px}
.input:focus,.selector:focus{outline:none;border-color:var(--accent)}
.btn{padding:10px 16px;border-radius:10px;border:none;font-size:14px;font-weight:600;cursor:pointer}
.btn-primary{background:var(--accent);color:#fff}
.btn-outline{background:var(--card2);color:var(--text);border:1px solid var(--border)}
.btn-sm{padding:6px 12px;font-size:12px}
.row{display:flex;gap:8px;align-items:flex-end}
.col{flex:1}
.section{cursor:pointer;display:flex;justify-content:space-between;align-items:center;padding:10px 14px;background:var(--card2);border-radius:10px;margin-bottom:4px;font-size:14px;font-weight:600;border:1px solid var(--border)}
.section .arr{color:var(--muted);transition:.2s}
.section.open .arr{transform:rotate(180deg)}
.body{display:none;padding:8px 14px 14px}
.body.open{display:block}
.tag{display:inline-flex;align-items:center;gap:4px;padding:4px 10px;background:var(--accent);color:#fff;border-radius:8px;font-size:12px;margin:2px}
.tag .x{cursor:pointer;font-weight:700;font-size:14px;opacity:.7}
.tag .x:hover{opacity:1}
.flex-wrap{display:flex;flex-wrap:wrap;gap:6px}
.log{font-size:13px;color:var(--muted);white-space:pre-wrap;background:var(--card2);padding:10px;border-radius:8px;max-height:200px;overflow-y:auto;margin-top:8px}
.status{text-align:center;font-size:12px;color:var(--green);margin-top:6px}
.footer{text-align:center;font-size:12px;color:var(--muted);margin:20px 0}
</style>
</head>
<body>

<h1>⚙️ AutoClip 配置生成器</h1>
<div class="sub">桌面版 · 配置保存到 configs/streamers/</div>

<div class="card">
  <div class="card-title">📺 主播</div>
  <div class="row">
    <select class="selector" id="streamer" onchange="loadCfg()" style="flex:1">
      <option value="">选择已有主播...</option>
    </select>
    <button class="btn btn-outline btn-sm" onclick="newCfg()">+ 新建</button>
  </div>
  <div id="newNameRow" class="row" style="display:none;margin-top:8px">
    <input class="input" id="newName" placeholder="输入主播名（如：嘉然Today）" style="flex:1">
    <button class="btn btn-outline btn-sm" onclick="confirmNew()">确认</button>
  </div>
  <div class="row" style="margin-top:8px">
    <select class="selector" id="template" style="flex:1">
      <option value="">从模板加载...</option>
    </select>
    <button class="btn btn-outline btn-sm" onclick="applyTemplate()">应用</button>
  </div>
</div>

<div class="card">
  <div class="card-title">📝 基本信息</div>
  <input class="input" id="cfgDesc" placeholder="主播描述（如：游戏区VUP，擅长整活）">
  <div class="row">
    <input class="input" id="cfgUid" placeholder="B站UID">
    <input class="input" id="cfgRoom" placeholder="直播间ID">
  </div>
</div>

<!-- Steps 1-7 -->
<div class="section" onclick="tog(this)">步骤1 · 准备录播 <span class="arr">▼</span></div>
<div class="body" id="s1">
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_convert_flv" checked> 1. FLV 转 MP4（成功后默认删除 FLV）</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_keep_flv"> 保留原 FLV 文件</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_portrait" checked> 2. 竖屏转横屏 1080p（横屏自动跳过）</label>
  <div class="row">
    <input class="input" id="s1_landscape_size" value="1920x1080" placeholder="横屏尺寸，如 1920x1080" style="flex:1">
    <input class="input" id="s1_landscape_blur" type="number" value="20" min="0" max="80" placeholder="背景模糊" style="flex:1">
  </div>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_portrait_overwrite"> 重新处理已转过的竖屏视频</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_delete_portrait"> 竖屏转横屏后不保留竖屏原版备份</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_burn_danmu" checked> 3. 压制同名 MP4 + XML 弹幕</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_burn_danmu_overwrite"> 重新压制已存在的弹幕版</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_keep_danmu_ass"> 保留弹幕 ASS 文件</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_recursive"> 递归扫描子目录</label>
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s1_overwrite"> FLV 转 MP4 时覆盖已有 MP4</label>
</div>

<div class="section" onclick="tog(this)">步骤2 · 字幕转录 <span class="arr">▼</span></div>
<div class="body" id="s2">
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">引擎</label><select class="selector" id="s2_engine"><option value="funasr">FunASR</option><option value="whisper">Whisper</option></select></div><div class="col"><label style="font-size:12px;color:var(--muted)">模型</label><select class="selector" id="s2_model"><option value="SenseVoiceSmall">SenseVoiceSmall</option><option value="large-v3-turbo">large-v3-turbo</option></select></div></div>
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">语言</label><input class="input" id="s2_lang" value="zh"></div><div class="col"><label style="font-size:12px;color:var(--muted)">VAD</label><select class="selector" id="s2_vad"><option value="true" selected>开启</option><option value="false">关闭</option></select></div></div>
  <label style="font-size:12px;color:var(--muted)">说话人分离</label><select class="selector" id="s2_dia"><option value="false" selected>关闭</option><option value="true">开启</option></select>
</div>

<div class="section" onclick="tog(this)">步骤3 · 高光分析 <span class="arr">▼</span></div>
<div class="body" id="s3">
  <label style="font-size:12px;color:var(--muted)">分析模式</label><select class="selector" id="s3_mode"><option value="llm">LLM语义</option><option value="danmaku">弹幕热度</option><option value="combined">双保险</option></select>
  <label style="font-size:12px;color:var(--muted)">提示词目录（可选）</label><input class="input" id="s3_prompt" placeholder="留空使用默认">
</div>

<div class="section" onclick="tog(this)">步骤4 · 视频切片 <span class="arr">▼</span></div>
<div class="body" id="s4">
  <label style="font-size:12px;color:var(--muted)">输出目录</label><input class="input" id="s4_out" value="{input_dir}/切片">
</div>

<div class="section" onclick="tog(this)">步骤5 · 字幕烧录 <span class="arr">▼</span></div>
<div class="body" id="s5">
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">字幕引擎</label><select class="selector" id="s5_engine"><option value="whisper">Whisper</option><option value="funasr">FunASR</option></select></div><div class="col"><label style="font-size:12px;color:var(--muted)">模型</label><select class="selector" id="s5_model"><option value="large-v3-turbo">large-v3-turbo</option><option value="SenseVoiceSmall">SenseVoiceSmall</option></select></div></div>
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">语言</label><input class="input" id="s5_lang" value="zh"></div><div class="col"><label style="font-size:12px;color:var(--muted)">VAD</label><select class="selector" id="s5_vad"><option value="true" selected>开启</option><option value="false">关闭</option></select></div></div>
  <label style="font-size:12px;color:var(--muted)">字体</label><input class="input" id="s5_font" value="MaokenAssortedSans">
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">字号</label><input class="input" id="s5_size" type="number" value="80"></div><div class="col"><label style="font-size:12px;color:var(--muted)">描边</label><input class="input" id="s5_outline" type="number" value="8"></div></div>
  <label style="font-size:12px;color:var(--muted)">颜色 (ASS格式)</label><input class="input" id="s5_color" value="&amp;H00FFFFFF">
  <label style="font-size:12px;color:var(--muted)">描边颜色</label><input class="input" id="s5_ocolor" value="&amp;H00000000">
  <div class="row"><div class="col"><label style="font-size:12px;color:var(--muted)">阴影</label><select class="selector" id="s5_shadow"><option value="off">关闭</option><option value="on">开启</option></select></div><div class="col"><label style="font-size:12px;color:var(--muted)">阴影颜色</label><select class="selector" id="s5_shadow_color"><option value="black">黑色</option><option value="white">白色</option></select></div></div>
</div>

<div class="section" onclick="tog(this)">步骤6 · 封面生成 <span class="arr">▼</span></div>
<div class="body" id="s6">
  <label style="font-size:12px;color:var(--muted)">样式</label><select class="selector" id="s6_style"><option value="random_1_4" selected>随机样式(1-5)</option><option value="style1">样式1</option><option value="style2">样式2</option><option value="style3">样式3</option><option value="style4">样式4</option><option value="style5">样式5</option></select>
  <label style="font-size:12px;color:var(--muted)">字体</label><input class="input" id="s6_font" value="MaoKenShiJinHei-2.ttf">
  <label style="font-size:12px;color:var(--muted)">封面输出目录</label><input class="input" id="s6_out" value="{input_dir}/切片/封面">
  <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;padding:6px 0"><input type="checkbox" id="s6_match" checked> 仅匹配 _subbed.mp4</label>
</div>

<div class="section" onclick="tog(this)">步骤7 · B站投稿 <span class="arr">▼</span></div>
<div class="body" id="s7">
  <label style="font-size:12px;color:var(--muted)">分区TID</label><input class="input" id="s7_tid" type="number" value="124" placeholder="如 208(虚拟主播), 36(音乐翻唱), 154(生活日常)">
  <label style="font-size:12px;color:var(--muted)">版权</label><select class="selector" id="s7_cr"><option value="original" selected>自制</option><option value="reprint">转载</option></select>
  <label style="font-size:12px;color:var(--muted)">标签（逗号分隔）</label><input class="input" id="s7_tags" placeholder="VUP,虚拟主播,ai切片">
</div>

<div class="card">
  <div class="card-title">🏷️ 关键词</div>
  <div class="row">
    <input class="input" id="kwInput" placeholder="输入关键词后回车" style="flex:1" onkeydown="if(event.key==='Enter'){addKw();event.preventDefault()}">
    <button class="btn btn-outline btn-sm" onclick="addKw()">添加</button>
  </div>
  <div class="flex-wrap" id="kwTags"></div>
</div>

<div class="card">
  <div class="card-title">🔍 梗搜索 <small style="font-weight:400;color:var(--muted);font-size:12px">B站 + LLM分析</small></div>
  <div class="row">
    <input class="input" id="memeMonths" type="number" value="3" min="1" max="12" placeholder="月数" style="flex:1">
    <input class="input" id="memeMax" type="number" value="50" min="10" max="100" placeholder="条数" style="flex:1">
  </div>
  <button class="btn btn-primary" id="memeBtn" onclick="memeSearch()" style="width:100%;margin-top:8px">🔍 搜索B站 + LLM分析</button>
  <div class="log" id="memeLog">点击按钮开始分析…</div>
</div>

<div style="display:flex;gap:8px;margin:16px 0">
  <button class="btn btn-primary" onclick="saveCfg()" style="flex:1">💾 保存配置</button>
</div>
<div class="status" id="status"></div>

<div class="footer">配置保存路径: configs/streamers/{主播名}.json</div>

<script>
let config = {};
let keywords = [];
let currentStreamer = '';

async function init(){
  // 加载主播列表
  try{
    const r = await fetch('/api/streamers');
    const list = await r.json();
    const sel = document.getElementById('streamer');
    sel.innerHTML = '<option value="">选择已有主播...</option>' +
      list.map(s => `<option value="${s.name}">${s.name}</option>`).join('');
  }catch(e){}

  // 加载模板列表
  try{
    const r = await fetch('/api/templates');
    const tpls = await r.json();
    const sel = document.getElementById('template');
    sel.innerHTML = '<option value="">从模板加载...</option>' +
      Object.entries(tpls).map(([k,v]) => `<option value="${k}">${v.name}</option>`).join('');
  }catch(e){}
}

async function loadCfg(){
  currentStreamer = document.getElementById('streamer').value;
  if(!currentStreamer){ resetForm(); return; }
  try{
    const r = await fetch('/api/config/'+encodeURIComponent(currentStreamer));
    if(!r.ok) throw new Error(r.status);
    config = await r.json();
    fillForm(config);
    document.getElementById('status').textContent = '✅ 已加载: ' + currentStreamer;
  }catch(e){ document.getElementById('status').textContent = '❌ 加载失败: '+e.message; }
}

function resetForm(){
  config = {};
  keywords = [];
  document.getElementById('cfgDesc').value = '';
  document.getElementById('cfgUid').value = '';
  document.getElementById('cfgRoom').value = '';
  setStep1Defaults();
  renderKw();
}

function fillForm(cfg){
  document.getElementById('cfgDesc').value = cfg.description || '';
  document.getElementById('cfgUid').value = (cfg.bilibili_links||{}).uid || '';
  document.getElementById('cfgRoom').value = (cfg.bilibili_links||{}).room || '';
  // Step 1
  const s1 = cfg.step1_prepare || {};
  document.getElementById('s1_convert_flv').checked = s1.convert_flv !== false;
  document.getElementById('s1_keep_flv').checked = s1.keep_flv_source === true || s1.keep_source === true;
  document.getElementById('s1_portrait').checked = s1.portrait_to_landscape === true;
  document.getElementById('s1_landscape_size').value = `${s1.landscape_width || 1920}x${s1.landscape_height || 1080}`;
  document.getElementById('s1_landscape_blur').value = s1.landscape_blur ?? 20;
  document.getElementById('s1_portrait_overwrite').checked = s1.portrait_overwrite === true || s1.landscape_overwrite === true;
  document.getElementById('s1_delete_portrait').checked = s1.delete_portrait_source === true;
  document.getElementById('s1_burn_danmu').checked = s1.burn_danmu === true;
  document.getElementById('s1_burn_danmu_overwrite').checked = s1.burn_danmu_overwrite === true || s1.overwrite_danmu === true;
  document.getElementById('s1_keep_danmu_ass').checked = s1.keep_danmu_ass === true;
  document.getElementById('s1_recursive').checked = s1.recursive === true;
  document.getElementById('s1_overwrite').checked = s1.overwrite === true;
  // Step 2
  const s2 = cfg.step2_subtitle || {};
  document.getElementById('s2_engine').value = s2.engine || 'funasr';
  document.getElementById('s2_model').value = s2.model || 'SenseVoiceSmall';
  document.getElementById('s2_lang').value = s2.language || 'zh';
  document.getElementById('s2_vad').value = String(s2.vad !== false);
  document.getElementById('s2_dia').value = String(s2.speaker_diarization === true);
  // Step 3
  const s3 = cfg.step3_highlight || {};
  document.getElementById('s3_mode').value = s3.mode || 'llm';
  document.getElementById('s3_prompt').value = s3.prompt_dir || '';
  // Step 4
  const s4 = cfg.step4_clip || {};
  document.getElementById('s4_out').value = s4.output_dir || '{input_dir}/切片';
  // Step 5
  const s5 = cfg.step5_burn || {};
  document.getElementById('s5_engine').value = s5.engine || 'whisper';
  document.getElementById('s5_model').value = s5.model || 'large-v3-turbo';
  document.getElementById('s5_lang').value = s5.language || 'zh';
  document.getElementById('s5_vad').value = String(s5.vad !== false);
  document.getElementById('s5_font').value = s5.font || 'MaokenAssortedSans';
  document.getElementById('s5_size').value = s5.font_size || 80;
  document.getElementById('s5_outline').value = s5.outline || 8;
  document.getElementById('s5_color').value = s5.color || '&H00FFFFFF';
  document.getElementById('s5_ocolor').value = s5.outline_color || '&H00000000';
  document.getElementById('s5_shadow').value = s5.shadow ? 'on' : 'off';
  document.getElementById('s5_shadow_color').value = s5.shadow_color || 'black';
  // Step 6
  const s6 = cfg.step6_cover || {};
  document.getElementById('s6_style').value = s6.style || 'random_1_4';
  document.getElementById('s6_font').value = s6.font || 'MaoKenShiJinHei-2.ttf';
  document.getElementById('s6_out').value = s6.output_dir || '{input_dir}/切片/封面';
  document.getElementById('s6_match').checked = s6.match_subbed_only !== false;
  // Step 7
  const s7 = cfg.step7_upload || {};
  document.getElementById('s7_tid').value = s7.tid || 124;
  document.getElementById('s7_cr').value = s7.copyright || 'original';
  document.getElementById('s7_tags').value = (s7.tags||[]).join(',');
  // Keywords
  keywords = normalizeKeywords(cfg.keywords || []);
  renderKw();
}

function newCfg(){
  document.getElementById('newNameRow').style.display = 'flex';
  document.getElementById('newName').focus();
}

function confirmNew(){
  const name = document.getElementById('newName').value.trim();
  if(!name) return;
  currentStreamer = name;
  config = {streamer_name:name, version:1};
  keywords = [];
  resetForm();
  document.getElementById('newNameRow').style.display = 'none';
  document.getElementById('status').textContent = '🆕 新建: ' + name;
}

async function applyTemplate(){
  const tid = document.getElementById('template').value;
  if(!tid) return;
  try{
    const r = await fetch('/api/templates');
    const tpls = await r.json();
    const tpl = tpls[tid];
    if(!tpl) return;
    config = {streamer_name: currentStreamer || '新主播', version: 1, ...tpl.cfg};
    fillForm(config);
    document.getElementById('status').textContent = '📋 已应用模板: ' + tpl.name;
  }catch(e){}
}

function addKw(){
  const inp = document.getElementById('kwInput');
  const kw = inp.value.trim();
  inp.value = '';
  if(!kw || keywords.includes(kw)) return;
  keywords.push(kw);
  renderKw();
}

function removeKw(i){ keywords.splice(i,1); renderKw(); }

function renderKw(){
  document.getElementById('kwTags').innerHTML = keywords.map((k,i) =>
    `<span class="tag">${esc(k)}<span class="x" onclick="removeKw(${i})">&times;</span></span>`
  ).join('');
}

function collectCfg(){
  const [landscapeWidth, landscapeHeight] = parseLandscapeSize();
  return {
    streamer_name: currentStreamer || '新主播',
    version: 1,
    description: document.getElementById('cfgDesc').value,
    bilibili_links: {
      uid: document.getElementById('cfgUid').value,
      room: document.getElementById('cfgRoom').value,
    },
    step1_prepare: {
      convert_flv: document.getElementById('s1_convert_flv').checked,
      keep_flv_source: document.getElementById('s1_keep_flv').checked,
      overwrite: document.getElementById('s1_overwrite').checked,
      portrait_to_landscape: document.getElementById('s1_portrait').checked,
      portrait_overwrite: document.getElementById('s1_portrait_overwrite').checked,
      delete_portrait_source: document.getElementById('s1_delete_portrait').checked,
      landscape_width: landscapeWidth,
      landscape_height: landscapeHeight,
      landscape_blur: parseInt(document.getElementById('s1_landscape_blur').value)||20,
      burn_danmu: document.getElementById('s1_burn_danmu').checked,
      burn_danmu_overwrite: document.getElementById('s1_burn_danmu_overwrite').checked,
      keep_danmu_ass: document.getElementById('s1_keep_danmu_ass').checked,
      recursive: document.getElementById('s1_recursive').checked,
    },
    step2_subtitle: {
      engine: document.getElementById('s2_engine').value,
      model: document.getElementById('s2_model').value,
      language: document.getElementById('s2_lang').value,
      vad: document.getElementById('s2_vad').value === 'true',
      speaker_diarization: document.getElementById('s2_dia').value === 'true',
    },
    step3_highlight: {
      mode: document.getElementById('s3_mode').value,
      prompt_dir: document.getElementById('s3_prompt').value,
    },
    step4_clip: {
      output_dir: document.getElementById('s4_out').value,
    },
    step5_burn: {
      skip_asr: false,
      engine: document.getElementById('s5_engine').value,
      model: document.getElementById('s5_model').value,
      language: document.getElementById('s5_lang').value,
      vad: document.getElementById('s5_vad').value === 'true',
      font: document.getElementById('s5_font').value,
      font_size: parseInt(document.getElementById('s5_size').value)||80,
      outline: parseInt(document.getElementById('s5_outline').value)||8,
      shadow: document.getElementById('s5_shadow').value === 'on',
      shadow_color: document.getElementById('s5_shadow_color').value,
      margin_v: 80,
      color: document.getElementById('s5_color').value,
      outline_color: document.getElementById('s5_ocolor').value,
    },
    step6_cover: {
      style: document.getElementById('s6_style').value,
      font: document.getElementById('s6_font').value,
      output_dir: document.getElementById('s6_out').value,
      match_subbed_only: document.getElementById('s6_match').checked,
    },
    step7_upload: {
      tid: parseInt(document.getElementById('s7_tid').value)||124,
      copyright: document.getElementById('s7_cr').value,
      no_reprint: false,
      tags: document.getElementById('s7_tags').value.split(',').map(s=>s.trim()).filter(Boolean),
    },
    keywords: normalizeKeywords(keywords),
  };
}

async function saveCfg(){
  if(!currentStreamer){ document.getElementById('status').textContent='⚠️ 请先输入主播名'; return; }
  const cfg = collectCfg();
  try{
    const r = await fetch('/api/config/'+encodeURIComponent(currentStreamer), {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify(cfg),
    });
    const res = await r.json();
    if(res.ok) document.getElementById('status').textContent = '✅ 已保存: ' + res.path;
    else document.getElementById('status').textContent = '❌ ' + (res.error||'保存失败');
  }catch(e){ document.getElementById('status').textContent = '❌ ' + e.message; }
}

function tog(el){ el.classList.toggle('open'); el.nextElementSibling.classList.toggle('open'); }

function esc(s){ const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

function setStep1Defaults(){
  document.getElementById('s1_convert_flv').checked = true;
  document.getElementById('s1_keep_flv').checked = false;
  document.getElementById('s1_portrait').checked = true;
  document.getElementById('s1_landscape_size').value = '1920x1080';
  document.getElementById('s1_landscape_blur').value = 20;
  document.getElementById('s1_portrait_overwrite').checked = false;
  document.getElementById('s1_delete_portrait').checked = false;
  document.getElementById('s1_burn_danmu').checked = true;
  document.getElementById('s1_burn_danmu_overwrite').checked = false;
  document.getElementById('s1_keep_danmu_ass').checked = false;
  document.getElementById('s1_recursive').checked = false;
  document.getElementById('s1_overwrite').checked = false;
}

function parseLandscapeSize(){
  const parts = String(document.getElementById('s1_landscape_size').value || '')
    .toLowerCase().replace('×', 'x').split('x');
  if(parts.length !== 2) return [1920, 1080];
  const w = parseInt(parts[0]) || 1920;
  const h = parseInt(parts[1]) || 1080;
  return [w, h];
}

// ===== 梗搜索 =====

async function memeSearch(){
  if(!currentStreamer){ document.getElementById('status').textContent='⚠️ 请先选择或新建主播'; return; }
  const btn = document.getElementById('memeBtn');
  const log = document.getElementById('memeLog');
  btn.disabled = true; btn.textContent = '⏳ 分析中...';
  log.textContent = '';

  const months = parseInt(document.getElementById('memeMonths').value)||3;
  const max = parseInt(document.getElementById('memeMax').value)||50;

  try{
    const r = await fetch('/api/meme-search', {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body: JSON.stringify({streamer:currentStreamer, months, max_results:max})
    });
    if(!r.ok){ log.textContent = '❌ 请求失败: '+r.status; return; }
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    while(true){
      const {done, value} = await reader.read();
      if(done) break;
      log.textContent += dec.decode(value, {stream:true});
      log.scrollTop = log.scrollHeight;
    }
    // 重新加载配置获取已合并的 keywords
    const r2 = await fetch('/api/config/'+encodeURIComponent(currentStreamer));
    if(r2.ok){
      const cfg = await r2.json();
      keywords = normalizeKeywords(cfg.keywords || []);
      renderKw();
      log.textContent += '\n✅ 分析完成，关键词已合并到 keywords';
    }
  }catch(e){ log.textContent = '❌ '+e; }
  finally{ btn.disabled = false; btn.textContent = '🔍 搜索B站 + LLM分析'; }
}

function normalizeKeyword(value){
  if(typeof value === 'string') return value.trim();
  if(value && typeof value === 'object'){
    if(value.recommend === false) return '';
    return String(value.keyword || '').trim();
  }
  return '';
}

function normalizeKeywords(values){
  const out = [];
  const seen = new Set();
  for(const value of values || []){
    const kw = normalizeKeyword(value);
    if(!kw || seen.has(kw)) continue;
    seen.add(kw);
    out.push(kw);
  }
  return out;
}

init();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args): pass

    def _json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)

        if p.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

        elif p.path == "/api/streamers":
            streamers = []
            if os.path.isdir(CONFIG_DIR):
                for f in sorted(os.listdir(CONFIG_DIR)):
                    if f.endswith(".json"):
                        streamers.append({"name": f[:-5]})
            self._json(streamers)

        elif p.path == "/api/templates":
            self._json(TEMPLATES)

        elif p.path.startswith("/api/config/"):
            name = p.path.split("/api/config/", 1)[1]
            name = unquote(name)
            cfg_path = os.path.join(CONFIG_DIR, f"{name}.json")
            if os.path.isfile(cfg_path):
                try:
                    with open(cfg_path, "r", encoding="utf-8") as f:
                        self._json(json.load(f))
                except Exception:
                    self._json({"error": "读取失败"}, 500)
            else:
                self._json({"streamer_name": name, "version": 1})

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        p = urlparse(self.path)
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            self._json({"error": "bad JSON"}, 400)
            return

        if p.path == "/api/meme-search":
            streamer = data.get("streamer", "")
            months = data.get("months", 3)
            max_results = data.get("max_results", 50)
            if not streamer:
                self._json({"error": "need streamer"}, 400)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self._run_meme_search(streamer, months, max_results)

        elif p.path.startswith("/api/config/"):
            name = p.path.split("/api/config/", 1)[1]
            name = unquote(name)
            cfg_path = os.path.join(CONFIG_DIR, f"{name}.json")
            os.makedirs(CONFIG_DIR, exist_ok=True)
            try:
                data.pop("_meme_result", None)
                data["keywords"] = normalize_keywords(data.get("keywords", []))
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._json({"ok": True, "path": cfg_path})
            except Exception as e:
                self._json({"error": str(e)}, 500)

        else:
            self.send_response(404)
            self.end_headers()

    def _run_meme_search(self, streamer, months, max_results):
        sys.path.insert(0, PROJECT_ROOT)
        try:
            from utils.bilibili_meme_search import search_videos, analyze_titles
        except Exception as e:
            try:
                self.wfile.write(f"[FAIL] 导入梗搜索模块失败: {e}\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
            return

        self.wfile.write(f"🔎 搜索B站关于「{streamer}」的视频（近{months}个月，最多{max_results}条）...\n".encode("utf-8"))
        self.wfile.flush()

        try:
            titles = asyncio.run(search_videos(keyword=streamer, months=months, max_results=max_results))
        except Exception as e:
            self.wfile.write(f"❌ B站搜索失败: {e}\n".encode("utf-8"))
            self.wfile.flush()
            return

        if not titles:
            self.wfile.write("⚠️ 未搜到相关视频\n".encode("utf-8"))
            self.wfile.flush()
            return

        self.wfile.write(f"📊 找到 {len(titles)} 条视频标题，发送LLM分析...\n".encode("utf-8"))
        self.wfile.flush()

        # LLM 配置
        try:
            gui_path = os.path.join(PROJECT_ROOT, "configs", "gui_config.json")
            gui_cfg = {}
            if os.path.isfile(gui_path):
                with open(gui_path, "r", encoding="utf-8") as f:
                    gui_cfg = json.load(f)
        except Exception:
            gui_cfg = {}

        llm_config = {
            "api_key": gui_cfg.get("global_api_key", ""),
            "base_url": "https://api.siliconflow.cn/v1",
            "model": gui_cfg.get("global_model", "deepseek-ai/DeepSeek-V4-Pro"),
        }

        try:
            result = analyze_titles(
                titles=titles[:min(len(titles), 40)],
                streamer=streamer,
                llm_config=llm_config,
            )
        except Exception as e:
            self.wfile.write(f"❌ LLM分析失败: {e}\n".encode("utf-8"))
            self.wfile.flush()
            return

        keywords = normalize_keywords(result.get("keywords", []))
        summary = result.get("summary", "")
        self.wfile.write(f"\n📝 梗分析摘要:\n{summary}\n".encode("utf-8"))
        self.wfile.write(f"\n🏷️ 推荐关键词 ({len(keywords)}):\n".encode("utf-8"))
        for kw in keywords:
            self.wfile.write(f"  • {kw}\n".encode("utf-8"))
        self.wfile.write(f"\n✅ 分析完成，已自动合并到 keywords\n".encode("utf-8"))
        self.wfile.flush()

        # 保存合并后的 keywords
        cfg_path = os.path.join(CONFIG_DIR, f"{streamer}.json")
        try:
            cfg = {}
            if os.path.isfile(cfg_path):
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg.pop("_meme_result", None)
            cfg["keywords"] = merge_keywords(cfg.get("keywords", []), keywords)
            with open(cfg_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    # 自动打开浏览器
    threading.Thread(target=lambda: (__import__("time").sleep(0.8), webbrowser.open(f"http://localhost:{args.port}")), daemon=True).start()
    print(f"⚙️ 配置生成器已启动 → http://localhost:{args.port}")
    print("按 Ctrl+C 退出")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()


if __name__ == "__main__":
    main()
