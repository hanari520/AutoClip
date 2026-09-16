# -*- coding: utf-8 -*-
"""AutoClip Web Console light edition.

This entry reuses the original ``web_console`` backend and REST APIs, and only
replaces the embedded frontend presentation with a light office-style theme.
"""

import sys

import web_console as base


LIGHT_CSS = r"""
:root{
  --bg:#F7F8FA;--bg2:#FFFFFF;--card:#FFFFFF;--card2:#F2F6FF;
  --surface:#F7F8FA;--border:#E4E7ED;--text:#1D2129;--muted:#6B7785;
  --accent:#4080FF;--accent2:#2F6FED;--accent-pressed:#1F5FDB;
  --green:#00B42A;--red:#F53F3F;--amber:#FF7D00;--yellow:#FF7D00;--blue:#4080FF;
  --log-info:#1D2129;--log-success:#008A22;--log-warning:#C95F00;--log-error:#D91E36;
  --safe-top:env(safe-area-inset-top,0px);
  --safe-bottom:env(safe-area-inset-bottom,0px);
}
body[data-theme="dark"]{
  --bg:#111827;--bg2:#1F2937;--card:#1F2937;--card2:#263244;
  --surface:#111827;--border:#374151;--text:#F9FAFB;--muted:#CBD5E1;
  --accent:#60A5FA;--accent2:#3B82F6;--accent-pressed:#2563EB;
  --green:#22C55E;--red:#F87171;--amber:#F59E0B;--yellow:#F59E0B;--blue:#60A5FA;
  --log-info:#F9FAFB;--log-success:#4ADE80;--log-warning:#FBBF24;--log-error:#FCA5A5;
}
body{
  background:var(--bg)!important;
  color:var(--text)!important;
  font-size:13px;
}
.header{
  background:var(--card)!important;
  color:var(--text)!important;
  border-bottom:1px solid var(--border);
  box-shadow:0 2px 10px rgba(29,33,41,.06)!important;
  text-align:left!important;
}
.header h1{color:var(--text)!important;font-size:17px!important;letter-spacing:0!important}
.header .sub{color:var(--muted)!important}
.tabbar{
  background:var(--card)!important;
  border-top:1px solid var(--border)!important;
  box-shadow:0 -2px 12px rgba(29,33,41,.06);
}
.tab.active{color:var(--accent)!important}
.view{padding:14px!important}
.card,.file-item,.upload-card,.files-nav{
  background:var(--card)!important;
  border:1px solid var(--border)!important;
  border-radius:8px!important;
  box-shadow:0 1px 3px rgba(29,33,41,.04);
}
.card-title{font-size:14px!important;color:var(--text)!important}
.card-meta,.label,.file-meta,.upload-meta{color:var(--muted)!important;font-weight:600!important}
#pipe-step-text,#pipe-step-text-2{
  color:var(--text)!important;
  font-weight:800!important;
  font-size:12px!important;
}
#pipe-cwd-text,#pipe-cwd-text-2{
  color:var(--muted)!important;
  font-weight:650!important;
}
.chip{
  color:var(--text)!important;
  font-weight:700!important;
}
.chip.active{
  color:#FFFFFF!important;
  font-weight:800!important;
}
.btn{border-radius:6px!important;min-height:34px!important;padding:8px 14px!important}
.btn-primary{
  background:var(--accent)!important;
  color:#FFFFFF!important;
}
.btn-primary:active{background:var(--accent-pressed)!important}
.btn-ghost{
  background:var(--card)!important;
  color:var(--text)!important;
  border:1px solid var(--border)!important;
}
.btn-ghost:active{background:var(--card2)!important;color:var(--accent)!important}
.btn-danger{background:var(--red)!important;color:#FFFFFF!important}
.input,.textarea,.select{
  background:var(--bg2)!important;
  color:var(--text)!important;
  border:1px solid var(--border)!important;
  border-radius:6px!important;
}
.input:focus,.textarea:focus,.select:focus{
  border-color:var(--accent)!important;
  box-shadow:0 0 0 2px rgba(64,128,255,.12);
}
.upload-card.selected{
  border-color:var(--accent)!important;
  box-shadow:0 0 0 1px var(--accent),0 2px 8px rgba(64,128,255,.08)!important;
}
.upload-card{position:relative;overflow:hidden}
.upload-card::before{
  content:"";
  position:absolute;
  left:0;top:0;bottom:0;
  width:4px;
  background:var(--border);
}
.upload-card.status-success::before{background:var(--green)}
.upload-card.status-failed::before{background:var(--red)}
.upload-card.status-uploading::before{background:var(--accent)}
.upload-card.status-skipped::before{background:var(--muted)}
.upload-card.status-pending::before{background:var(--amber)}
.upload-state{
  display:inline-flex;
  align-items:center;
  gap:6px;
  margin-top:4px;
  padding:3px 8px;
  border-radius:999px;
  background:var(--surface);
  border:1px solid var(--border);
  font-size:12px;
  font-weight:800;
}
.upload-state.success{color:var(--green);border-color:color-mix(in srgb,var(--green) 35%,var(--border))}
.upload-state.failed{color:var(--red);border-color:color-mix(in srgb,var(--red) 35%,var(--border))}
.upload-state.uploading{color:var(--accent);border-color:color-mix(in srgb,var(--accent) 35%,var(--border))}
.upload-state.skipped{color:var(--muted)}
.upload-state.pending{color:var(--amber);border-color:color-mix(in srgb,var(--amber) 35%,var(--border))}
.upload-error{
  margin-top:6px;
  color:var(--red);
  font-size:12px;
  line-height:1.45;
  font-weight:700;
}
.bvid-link{
  color:var(--accent);
  font-weight:800;
  cursor:pointer;
  text-decoration:underline;
  text-underline-offset:2px;
}
.upload-checkbox{border-color:var(--border)!important;background:var(--card)!important}
.upload-checkbox.checked{background:var(--accent)!important;border-color:var(--accent)!important}
.upload-thumb,.upload-thumb-placeholder{background:var(--surface)!important}
.tag-chip{
  background:var(--surface)!important;
  border:1px solid var(--border)!important;
  color:var(--muted)!important;
}
.progress-bar{background:var(--border)!important}
.progress-fill{background:var(--accent)!important}
.log-container{
  background:var(--card)!important;
  border:1px solid var(--border)!important;
  color:var(--text)!important;
  font-family:"SF Mono","Consolas","Microsoft YaHei UI",monospace!important;
  font-size:12px!important;
  line-height:1.62!important;
  letter-spacing:0!important;
}
.log-line{
  padding:3px 0!important;
  border-bottom:1px solid var(--border)!important;
  word-break:break-all!important;
}
.log-line:last-child{border-bottom:none!important}
.log-line .ts{
  color:var(--muted)!important;
  font-weight:700!important;
  margin-right:7px!important;
}
.log-line .level{
  display:inline-block!important;
  min-width:54px!important;
  margin-right:7px!important;
  font-size:11px!important;
  font-weight:800!important;
  letter-spacing:0!important;
}
.log-line .msg{font-weight:650!important}
.log-line.info{color:var(--log-info)!important}
.log-line.success{color:var(--log-success)!important}
.log-line.warning{color:var(--log-warning)!important}
.log-line.error{color:var(--log-error)!important}
.log-line.success .level{color:var(--log-success)!important}
.log-line.warning .level{color:var(--log-warning)!important}
.log-line.error .level{color:var(--log-error)!important}
.modal-overlay{background:rgba(29,33,41,.32)!important}
.modal{
  background:var(--card)!important;
  color:var(--text)!important;
  border:1px solid var(--border);
  box-shadow:0 -8px 32px rgba(29,33,41,.14);
}
.modal pre,.light-preview-pre{
  background:var(--surface)!important;
  color:var(--text)!important;
  border:1px solid var(--border)!important;
  border-radius:8px!important;
  padding:12px!important;
  font-size:12px!important;
  line-height:1.6!important;
  overflow:auto!important;
  max-height:58vh!important;
}
.light-preview-media{
  width:100%;
  max-height:62vh;
  object-fit:contain;
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:8px;
}
.light-preview-title{
  margin-bottom:12px;
  color:var(--text);
  font-size:15px;
  word-break:break-all;
}
.empty{color:var(--muted)!important}
.empty-action{
  margin-top:10px;
  display:flex;
  gap:8px;
  justify-content:center;
  flex-wrap:wrap;
}
.view-alert{
  display:none;
  margin:0 0 12px;
  padding:10px 12px;
  border-radius:8px;
  border:1px solid var(--border);
  background:var(--card2);
  color:var(--text);
  font-size:12px;
  line-height:1.55;
  font-weight:700;
}
.view-alert.error{
  display:block;
  border-color:color-mix(in srgb,var(--red) 35%,var(--border));
  background:color-mix(in srgb,var(--red) 8%,var(--card));
}
.view-alert.warning{
  display:block;
  border-color:color-mix(in srgb,var(--amber) 35%,var(--border));
  background:color-mix(in srgb,var(--amber) 9%,var(--card));
}
.view-alert.success{
  display:block;
  border-color:color-mix(in srgb,var(--green) 35%,var(--border));
  background:color-mix(in srgb,var(--green) 8%,var(--card));
}
.log-toolbar{
  display:flex;
  gap:8px;
  align-items:center;
  margin-bottom:10px;
  flex-wrap:wrap;
}
.log-filter{
  min-height:28px;
  padding:0 10px;
  border-radius:999px;
  border:1px solid var(--border);
  background:var(--card);
  color:var(--muted);
  font-size:12px;
  font-weight:800;
}
.log-filter.active{
  background:var(--accent);
  border-color:var(--accent);
  color:#fff;
}
.latest-error{
  display:none;
  margin-bottom:10px;
  padding:9px 11px;
  border-radius:8px;
  color:var(--red);
  background:color-mix(in srgb,var(--red) 8%,var(--card));
  border:1px solid color-mix(in srgb,var(--red) 35%,var(--border));
  font-size:12px;
  font-weight:800;
}
.latest-error.active{display:block}
.toast{
  background:var(--card)!important;
  color:var(--text)!important;
  border:1px solid var(--border);
  box-shadow:0 8px 28px rgba(29,33,41,.14)!important;
}
.upload-select-toolbar{
  display:none;
  align-items:center;
  gap:8px;
  margin:0 0 12px;
  padding:10px 12px;
  background:var(--card);
  border:1px solid var(--border);
  border-radius:8px;
  box-shadow:0 1px 3px rgba(29,33,41,.04);
}
.upload-select-toolbar .spacer{flex:1}
.upload-select-count{font-size:12px;color:var(--muted);font-weight:600}
.theme-toggle{
  position:fixed;
  right:14px;
  top:calc(12px + var(--safe-top));
  z-index:220;
  min-height:30px;
  padding:0 12px;
  border-radius:16px;
  border:1px solid var(--border);
  background:var(--card);
  color:var(--text);
  font-size:12px;
  font-weight:700;
  box-shadow:0 2px 10px rgba(29,33,41,.10);
}
@media (min-width: 900px){
  body{padding-bottom:0!important}
  .tabbar{left:0;top:0;bottom:auto;width:176px;height:100dvh;display:block;padding-top:82px}
  .tab{display:flex;gap:10px;align-items:center;text-align:left;padding:12px 18px}
  .tab .icon{display:inline;margin:0;font-size:18px}
  .header{margin-left:176px}
  .theme-toggle{right:18px}
  .view{margin-left:176px;max-width:1180px}
}
"""


SELECT_TOOLBAR_HTML = r"""
  <div class="upload-select-toolbar" id="upload-select-toolbar">
    <button class="btn btn-ghost btn-sm" onclick="selectAllUploads()">全选</button>
    <button class="btn btn-ghost btn-sm" onclick="invertUploadSelection()">反选</button>
    <div class="spacer"></div>
    <span class="upload-select-count" id="upload-select-count">0 / 0 个已选</span>
  </div>
"""


SELECT_JS = r"""
let lightLogEntries=[];
let lightLogFilter='all';
function applyConsoleTheme(theme){
  const next=theme==='dark'?'dark':'light';
  document.body.dataset.theme=next;
  localStorage.setItem('autoclip-web-theme',next);
  const btn=document.getElementById('theme-toggle');
  if(btn)btn.textContent=next==='dark'?'浅色':'深色';
}
function toggleConsoleTheme(){
  applyConsoleTheme(document.body.dataset.theme==='dark'?'light':'dark');
}
applyConsoleTheme(localStorage.getItem('autoclip-web-theme')||'light');
function logLevelLabel(level){
  const lv=(level||'info').toLowerCase();
  if(lv==='success')return 'SUCCESS';
  if(lv==='warning')return 'WARN';
  if(lv==='error')return 'ERROR';
  return 'INFO';
}
function ensureViewAlert(viewName){
  const view=document.getElementById('view-'+viewName);
  if(!view)return null;
  let alert=view.querySelector('.view-alert');
  if(alert)return alert;
  alert=document.createElement('div');
  alert.className='view-alert';
  view.insertBefore(alert,view.firstChild);
  return alert;
}
function showViewAlert(viewName,message,level='error'){
  const alert=ensureViewAlert(viewName);
  if(!alert)return;
  alert.className='view-alert '+level;
  alert.innerHTML=message;
}
function clearViewAlert(viewName){
  const alert=ensureViewAlert(viewName);
  if(!alert)return;
  alert.className='view-alert';
  alert.innerHTML='';
}
function emptyState(icon,title,body,actions=''){
  return `<div class="empty"><div class="icon">${icon}</div><strong>${title}</strong><br><small>${body}</small>${actions?`<div class="empty-action">${actions}</div>`:''}</div>`;
}
function browseParentDir(){
  const path=(typeof currentPath==='string'?currentPath:'');
  if(!path){ browseFiles(''); return; }
  const normalized=path.replace(/\//g,'\\\\');
  const parts=normalized.split('\\\\').filter(Boolean);
  if(parts.length<=1){ browseFiles(''); return; }
  browseFiles(parts.slice(0,-1).join('\\\\'));
}
function ensureLogTools(){
  const c=document.getElementById('log-container');
  if(!c||document.getElementById('log-toolbar'))return;
  const toolbar=document.createElement('div');
  toolbar.className='log-toolbar';
  toolbar.id='log-toolbar';
  toolbar.innerHTML=[
    ['all','全部'],['error','错误'],['warning','警告'],['success','成功']
  ].map(([id,label])=>`<button class="log-filter ${id==='all'?'active':''}" data-filter="${id}" onclick="setLogFilter('${id}')">${label}</button>`).join('');
  const latest=document.createElement('div');
  latest.className='latest-error';
  latest.id='latest-error';
  c.parentNode.insertBefore(toolbar,c);
  c.parentNode.insertBefore(latest,c);
}
function renderLightLogs(){
  ensureLogTools();
  const c=document.getElementById('log-container');
  if(!c)return;
  const rows=lightLogEntries.filter(e=>lightLogFilter==='all'||e.level===lightLogFilter);
  if(!rows.length){
    c.innerHTML='<div class="log-line info"><span class="ts">--:--:--</span><span class="level">[INFO]</span><span class="msg">启动流转后这里会显示实时日志</span></div>';
    return;
  }
  c.innerHTML=rows.map(e=>`<div class="log-line ${e.level}"><span class="ts">${esc(e.ts||'--:--:--')}</span><span class="level">[${logLevelLabel(e.level)}]</span><span class="msg">${esc(e.msg||'')}</span></div>`).join('');
  c.scrollTop=c.scrollHeight;
}
function setLogFilter(filter){
  lightLogFilter=filter;
  document.querySelectorAll('.log-filter').forEach(btn=>btn.classList.toggle('active',btn.dataset.filter===filter));
  renderLightLogs();
}
appendLog=function(ts,msg,level){
  const c=document.getElementById('log-container');
  if(!c)return;
  const lv=(level||'info').toLowerCase();
  lightLogEntries.push({ts,msg,level:lv});
  if(lightLogEntries.length>500)lightLogEntries=lightLogEntries.slice(-500);
  if(lv==='error'){
    const latest=document.getElementById('latest-error');
    if(latest){
      latest.classList.add('active');
      latest.textContent='最新错误：'+(msg||'未知错误');
    }
  }
  renderLightLogs();
};
clearLogs=function(){
  lightLogEntries=[];
  const latest=document.getElementById('latest-error');
  if(latest){latest.classList.remove('active');latest.textContent='';}
  renderLightLogs();
  toast('日志已清空');
};
function ensureLightPreviewModal(){
  let modal=document.getElementById('light-preview-modal');
  if(modal)return modal;
  modal=document.createElement('div');
  modal.className='modal-overlay';
  modal.id='light-preview-modal';
  modal.innerHTML='<div class="modal"></div>';
  modal.addEventListener('click',e=>{ if(e.target===modal)closeLightPreviewModal(); });
  document.body.appendChild(modal);
  return modal;
}
function closeLightPreviewModal(){
  const modal=document.getElementById('light-preview-modal');
  if(modal)modal.classList.remove('active');
}
document.addEventListener('keydown',e=>{
  if(e.key==='Escape'){
    closeLightPreviewModal();
    if(typeof closeEditModal==='function')closeEditModal();
  }
});
async function previewFile(path){
  const ext=path.split('.').pop().toLowerCase();
  const url='/api/files/preview?path='+encodeURIComponent(path);
  const name=path.split('\\\\').pop();
  const modal=ensureLightPreviewModal();
  const box=modal.querySelector('.modal');
  if(['png','jpg','jpeg','gif','webp','bmp'].includes(ext)){
    box.innerHTML=`
      <h3 class="light-preview-title">${esc(name)}</h3>
      <img class="light-preview-media" src="${url}" alt="${esc(name)}">
      <button class="btn btn-primary btn-block" style="margin-top:12px" onclick="closeLightPreviewModal()">关闭</button>
    `;
    modal.classList.add('active');
    return;
  }
  if(['mp4','mkv','webm','avi','mov','wmv','flv'].includes(ext)){
    box.innerHTML=`
      <h3 class="light-preview-title">${esc(name)}</h3>
      <video class="light-preview-media" src="${url}" controls playsinline></video>
      <button class="btn btn-primary btn-block" style="margin-top:12px" onclick="closeLightPreviewModal()">关闭</button>
    `;
    modal.classList.add('active');
    return;
  }
  const r=await fetch(url);
  const d=await r.json();
  if(d.error){showViewAlert('files',`预览失败：${esc(d.error)}`);toast(d.error);return;}
  if(d.type==='text'){
    box.innerHTML=`
      <h3 class="light-preview-title">${esc(name)}</h3>
      <pre class="light-preview-pre">${esc(d.content)}</pre>
      ${d.truncated?'<p style="color:var(--muted);font-size:12px;margin-top:8px">文件过大，仅显示前50KB</p>':''}
      <button class="btn btn-primary btn-block" style="margin-top:12px" onclick="closeLightPreviewModal()">关闭</button>
    `;
    modal.classList.add('active');
  }else{
    showViewAlert('files','该文件类型暂不支持预览。可以下载后在本地应用打开。','warning');
    toast('该文件类型暂不支持预览');
  }
}
function selectableUploadIndices(){
  const arr=[];
  manifestItems.forEach((item,i)=>{
    arr.push(i);
  });
  return arr;
}
function updateUploadSelectionUI(){
  const total=manifestItems.length;
  const selectable=selectableUploadIndices().length;
  const selected=selectedIndices.size;
  const toolbar=document.getElementById('upload-select-toolbar');
  const count=document.getElementById('upload-select-count');
  const submitCount=document.getElementById('sel-count');
  if(toolbar)toolbar.style.display=total?'flex':'none';
  if(count)count.textContent=`${selected} / ${selectable} 个已选`;
  if(submitCount)submitCount.textContent=selected;
}
function selectAllUploads(){
  selectedIndices.clear();
  selectableUploadIndices().forEach(i=>selectedIndices.add(i));
  renderManifest();
  toast(`已选择 ${selectedIndices.size} 个可投稿项`);
}
function invertUploadSelection(){
  const selectable=selectableUploadIndices();
  const next=new Set();
  selectable.forEach(i=>{ if(!selectedIndices.has(i))next.add(i); });
  selectedIndices=next;
  renderManifest();
  toast(`已选择 ${selectedIndices.size} 个可投稿项`);
}
const __lightRenderManifest=renderManifest;
renderManifest=function(){
  const list=document.getElementById('upload-list');
  if(list&&!manifestItems.length){
    list.innerHTML=emptyState(
      '📭',
      '还没有投稿清单',
      '运行一键流转后会生成 upload_manifest.json，也可以重新扫描当前目录。',
      '<button class="btn btn-primary btn-sm" onclick="switchTab(\'pipeline\')">启动流转</button><button class="btn btn-ghost btn-sm" onclick="loadManifest()">重新扫描</button>'
    );
    updateUploadSelectionUI();
    return;
  }
  let html='';
  manifestItems.forEach((item,i)=>{
    const sel=selectedIndices.has(i)?'selected':'';
    const chk=selectedIndices.has(i)?'checked':'';
    const status=item.upload_status||'pending';
    const statusText={pending:'待投稿',uploading:'投稿中',success:'已投稿',failed:'投稿失败',skipped:'未投稿/已中断'}[status]||status;
    const disabled='';
    const cover=item.thumb_path?`<img class="upload-thumb" src="/api/thumb?p=${encodeURIComponent(item.cover_path)}">`
      :`<div class="upload-thumb-placeholder">🎬</div>`;
    const tagsHtml=(item.tags||[]).map(t=>`<span class="tag-chip">${esc(t)}</span>`).join('');
    const attempts=parseInt(item.upload_attempts||0);
    const bvid=item.bvid?` · <span class="bvid-link" onclick="event.stopPropagation();navigator.clipboard&&navigator.clipboard.writeText('${esc(item.bvid)}');toast('已复制 BV 号')">BV:${esc(item.bvid)}</span>`:'';
    const err=(item.last_error&&(status==='failed'||status==='skipped'))?`<div class="upload-error" title="${esc(String(item.last_error))}">错误：${esc(String(item.last_error).slice(0,110))}</div>`:'';
    const retry=status==='failed'?`<button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();selectedIndices.add(${i});renderManifest();toast('已选中失败稿件，可重新提交')">选中重试</button>`:'';
    html+=`<div class="upload-card status-${status} ${sel}" id="card-${i}">
      <div class="upload-card-header" onclick="toggleCard(${i})">
        <div class="upload-checkbox ${chk} ${disabled}" onclick="event.stopPropagation();toggleSelect(${i})"></div>
        ${cover}
        <div class="upload-info">
          <div class="upload-title">${esc(item.title)}</div>
          <div class="upload-meta">${esc(item.video_path.split('\\\\').pop())} · tid:${item.tid}${bvid}</div>
          <div class="upload-state ${status}">状态：${statusText} · 投稿次数：${attempts}</div>
          ${err}
        </div>
      </div>
      <div class="upload-card-body">
        <div style="font-size:12px;color:var(--muted);margin-bottom:8px">${esc(item.desc||'无简介')}</div>
        <div style="margin-bottom:8px">${tagsHtml||'<span style="color:var(--muted);font-size:12px">无标签</span>'}</div>
        <button class="btn btn-ghost btn-sm" onclick="event.stopPropagation();openEditModal(${i})">编辑</button>
        ${retry}
      </div>
    </div>`;
  });
  if(list)list.innerHTML=html;
  updateUploadSelectionUI();
};
const __lightToggleSelect=toggleSelect;
toggleSelect=function(i){
  __lightToggleSelect(i);
  updateUploadSelectionUI();
};
const __lightSubmitUploads=submitUploads;
submitUploads=async function(){
  clearViewAlert('uploads');
  if(!selectedIndices.size){
    showViewAlert('uploads','请先选择至少一个投稿项。已投稿成功的稿件可以手动选择后再次投稿。','warning');
    toast('请先选择投稿项');
    return;
  }
  const indices=Array.from(selectedIndices).sort((a,b)=>a-b);
  const selected=indices.map(i=>manifestItems[i]);
  const failed=selected.filter(x=>(x.upload_status||'pending')==='failed').length;
  const pending=selected.filter(x=>(x.upload_status||'pending')==='pending').length;
  const uploading=selected.filter(x=>(x.upload_status||'pending')==='uploading').length;
  const repeat=selected.filter(x=>(x.upload_status||'pending')==='success').length;
  const message=`确认开始批量投稿？\n\n将投稿：${selected.length} 个\n待投稿：${pending} 个\n失败重试：${failed} 个\n重复投稿：${repeat} 个\n投稿中状态：${uploading} 个\n\n系统会逐条投稿、每条间隔约 4 分钟（防 B 站限频），命中 406 会自动冷却重试，20 个约需 80 分钟。`;
  if(!confirm(message))return;
  return __lightSubmitUploads();
};
const __lightStartPipeline=startPipeline;
startPipeline=async function(){
  clearViewAlert('pipeline');
  if(!selectedSteps||!selectedSteps.size){
    showViewAlert('pipeline','请选择至少一个流转步骤。建议从「准备文件」开始，或只选择需要补跑的步骤。','warning');
    toast('请选择至少一个步骤');
    return;
  }
  const streamerEl=document.getElementById('up-streamer');
  const folderEl=document.getElementById('up-folder');
  const streamer=streamerEl?streamerEl.value:'';
  const folder=folderEl?folderEl.value:'';
  if(!streamer||!folder){
    showViewAlert('pipeline','还没有选择运行目录。请先到投稿页选择主播和录播文件夹，再返回启动流转。 <button class="btn btn-primary btn-sm" onclick="switchTab(\'uploads\')">去选择目录</button>','warning');
    toast('请先选择主播和文件夹');
    return;
  }
  return __lightStartPipeline();
};
const __lightAbortPipeline=abortPipeline;
abortPipeline=async function(){
  const step=(document.getElementById('pipe-step-text')||{}).textContent||'当前步骤未知';
  const cwd=(document.getElementById('pipe-cwd-text')||{}).textContent||'目录未知';
  if(!confirm(`确定中断当前任务？\n\n${step}\n${cwd}\n\n中断后已生成的文件会保留，未完成项需要重新运行。`))return;
  return __lightAbortPipeline();
};
const __lightBrowseFiles=browseFiles;
browseFiles=async function(path){
  clearViewAlert('files');
  await __lightBrowseFiles(path);
  const list=document.getElementById('file-list');
  if(list&&list.textContent.trim()==='空目录'){
    list.innerHTML=emptyState('📂','这个目录是空的','可以返回上级目录，或上传文件后再刷新。','<button class="btn btn-ghost btn-sm" onclick="browseParentDir()">返回上级</button>');
  }
};
const __lightInitPipeline=initPipeline;
initPipeline=function(){
  __lightInitPipeline();
  const streamerEl=document.getElementById('up-streamer');
  const folderEl=document.getElementById('up-folder');
  if((!streamerEl||!streamerEl.value)||(!folderEl||!folderEl.value)){
    showViewAlert('pipeline','先选择录播目录后再启动流转。目录选择在投稿页完成。 <button class="btn btn-primary btn-sm" onclick="switchTab(\'uploads\')">去选择目录</button>','warning');
  }else{
    clearViewAlert('pipeline');
  }
};
ensureLogTools();
renderLightLogs();
"""


def _build_light_html():
    html = base.HTML
    html = html.replace("<title>AutoClip Console</title>", "<title>AutoClip Light Console</title>")
    html = html.replace('<meta name="theme-color" content="#0d1b2a">', '<meta name="theme-color" content="#F7F8FA">')
    html = html.replace("</style>", LIGHT_CSS + "\n</style>", 1)
    html = html.replace(
        '<div class="header">\n  <h1>AutoClip Console</h1>\n  <div class="sub" id="header-sub">Tailscale 远程控制台</div>\n</div>',
        '<div class="header">\n  <h1>AutoClip Console</h1>\n  <div class="sub" id="header-sub">Tailscale 远程控制台</div>\n</div>\n<button class="theme-toggle" id="theme-toggle" onclick="toggleConsoleTheme()">深色</button>',
        1,
    )
    html = html.replace(
        '<div class="log-line" style="color:var(--muted)">等待日志...</div>',
        '<div class="log-line info"><span class="ts">--:--:--</span><span class="level">[INFO]</span><span class="msg">等待日志...</span></div>',
        1,
    )
    html = html.replace(
        '<button class="btn btn-danger btn-block" onclick="if(confirm(\'确定停止服务器？\')){fetch(\'/api/pipeline/abort\',{method:\'POST\'});setTimeout(()=>location.reload(),500)}">\n    🔄 重启服务\n  </button>',
        '<button class="btn btn-danger btn-block" onclick="abortPipeline()">中断当前任务</button>',
        1,
    )
    html = html.replace('  <div id="upload-list">', SELECT_TOOLBAR_HTML + '\n  <div id="upload-list">', 1)
    html = html.replace("browseFiles('');", SELECT_JS + "\nbrowseFiles('');", 1)
    html = html.replace("#0a1520", "var(--surface)")
    return html


base.HTML = _build_light_html()


def main():
    base.main()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
