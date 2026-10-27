#!/usr/bin/env python3
"""Comfy Monitor: local, read-only mobile dashboard for ComfyUI.

Windows: double-click start_comfy_monitor.bat, or run `python comfy_monitor.py`.
Local dashboard: http://127.0.0.1:8765

Access remotely using *only* a protected Cloudflare Tunnel or Tailscale Serve.
Never forward ComfyUI's actual port 8188 to the internet.

Uses only the Python standard library (Python 3.9+). No ComfyUI plugins needed.
Reads: /queue, /history, /history/{job_id}, /view.
Serves: queue + last 60 jobs, previews, individual output downloads,
per-job ZIP archives (including workflow JSON), history JSON export.
No endpoint can start, stop, delete, or edit a ComfyUI job.

Optional Windows environment variables:
  set COMFY_URL=http://127.0.0.1:8188
  set MONITOR_PORT=8765
  set HISTORY_LIMIT=60
"""

import json
import io
import mimetypes
import os
import re
import shutil
import tempfile
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188").rstrip("/")
PORT = int(os.environ.get("MONITOR_PORT", "8765"))
HISTORY_LIMIT = max(1, min(150, int(os.environ.get("HISTORY_LIMIT", "60"))))
SLOW_REQUEST = 3  # Seconds; slower requests are printed to the console to help find hangs.
# Streams, downloads, uploads and chat replies are expected to take a while.
LONG_ROUTES = ("/api/file", "/api/zip", "/api/output-file", "/api/asset-file", "/api/upload",
               "/api/chat", "/api/prompt/rewrite", "/api/history.json")
MAX_ARCHIVE_FILES = 120
MAX_ARCHIVE_SIZE = 2 * 1024 ** 3  # Building ZIPs uses temporary disk space; save >2 GiB files individually.
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".apng")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".gif")
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")
ALLOWED_EXT = IMAGE_EXT + tuple(x for x in VIDEO_EXT if x not in IMAGE_EXT) + AUDIO_EXT

HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="color-scheme" content="dark"/><title>Comfy Monitor</title>
<style>
:root{font-family:system-ui,-apple-system,Segoe UI,Arial,sans-serif;color:#e7eef7;background:#0a0f19}
*{box-sizing:border-box}body{margin:0 auto;max-width:1150px;padding:22px 14px 70px}
a{color:#b5d6ff}button,a{touch-action:manipulation}header{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap;margin-bottom:24px}
h1{font-size:clamp(1.6rem,5vw,2.25rem);margin:0;letter-spacing:-.05em}.sub{color:#9aaabe;margin-top:5px;font-size:.88rem}
#connection{font-size:.8rem;color:#bcf2cb;border:1px solid #3c624e;border-radius:40px;padding:8px 11px;white-space:nowrap}#connection.down{color:#ffb1ba;border-color:#7a4248}
.stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.stat,.panel,.card{background:#151f2e;border:1px solid #2a3850;border-radius:14px}
.stat{padding:16px}.stat b{font-size:clamp(1.6rem,5vw,2.4rem);display:block;line-height:1.3}.stat span{font-size:.8rem;color:#a4b4c8}
section{margin-top:27px}h2{font-size:1.16rem;letter-spacing:-.025em;margin:0 0 13px}.heading{display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:12px}.heading h2{margin:0}
.panel{padding:14px}.muted{color:#94a5ba;font-size:.87rem}.row{padding:11px 0;border-bottom:1px solid #2a3850;display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}.row:last-child{border-bottom:0}.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;color:#bed0e3;font-size:.82rem}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:13px}.card{overflow:hidden}
.preview{display:flex;gap:3px;background:#090e17;min-height:140px;max-height:260px;overflow:hidden}.preview>*{flex:1;min-width:0;min-height:140px;max-height:260px}
.preview img,.preview video{display:block;width:100%;height:100%;min-height:140px;max-height:260px;object-fit:contain;background:#090e17}.preview>a{display:block;min-width:0}.preview .no-preview{display:grid;place-content:center;text-align:center;color:#71849d;font-size:.9rem}
.cardbody{padding:13px}.pill{display:inline-block;border-radius:40px;background:#224033;color:#b1f2ce;padding:4px 9px;font-size:.76rem}.pill.error{background:#492d36;color:#ffbdc8}
.jobline{display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap}.jobid{margin:10px 0 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.button{display:inline-block;text-decoration:none;font-size:.83rem;font-weight:600;line-height:1.1;padding:10px 12px;border:1px solid #5b80a9;border-radius:9px;color:#e7f4ff;background:#234260}
.button:hover{background:#31577a}.button.secondary{background:#1b2a3d;border-color:#354966}.button.secondary:hover{background:#273e5a}.actions{display:flex;flex-wrap:wrap;gap:8px}
details{border-top:1px solid #2e3c50;margin-top:13px;padding-top:11px}summary{cursor:pointer;color:#d5e7fb;font-size:.85rem}
.files{display:grid;gap:8px;margin-top:10px}.file{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px;border:1px solid #334258;border-radius:9px}.filename{color:#d6e3f4;font-size:.8rem;overflow-wrap:anywhere;min-width:0}.file .button{flex-shrink:0}

#notice{position:sticky;top:8px;z-index:5;padding:12px 14px;border:1px solid #a98240;background:#3a2b1d;color:#ffe4ad;border-radius:10px;margin:10px 0 18px;line-height:1.5;font-size:.88rem}
#notice[hidden]{display:none}
#notice button{padding:6px 11px;margin-left:10px;background:#415a78;color:white;border-radius:6px;border:1px solid #6382a8}
.video-box{position:relative;background:#090e17;flex:1;min-width:0}
.video-box video{width:100%;height:100%}
.video-info{position:absolute;bottom:0;left:0;right:0;font-size:.72rem;padding:5px 8px;background:#172437bb;color:#e0ecfc;pointer-events:none}
.video-box:has(video:not([src])) .video-info{display:block}
.video-error{color:#ffd4c4;padding:12px 9px;text-align:center;font-size:.8rem}
.busy{opacity:.65;pointer-events:none}
footer{margin-top:35px;color:#8599b1;font-size:.79rem;line-height:1.5}
@media(max-width:400px){.stat{padding:12px}.stat span{font-size:.73rem}.gallery{grid-template-columns:1fr}.button{padding:10px}}
</style></head><body>
<header><div><h1>◉ Comfy Monitor</h1><div class="sub">Read-only queue, history &amp; downloads · updates every 5 seconds</div></div>
<span id="connection">Connecting…</span></header>
<div id="notice" role="alert" hidden><span id="notice-text"></span><button type="button" id="retry">Retry / sign in</button></div>
<div class="stats"><div class="stat"><b id="running">–</b><span>Running</span></div>
<div class="stat"><b id="pending">–</b><span>Waiting</span></div><div class="stat"><b id="completed">–</b><span>Recent history</span></div></div>
<section><h2>Current queue</h2><div class="panel" id="queue"><div class="muted">Loading…</div></div></section>
<section><div class="heading"><h2>Recent jobs &amp; outputs</h2><a class="button secondary" href="/api/history.json">↓ Download history JSON</a></div>
<div class="gallery" id="gallery"><div class="muted">Loading…</div></div></section>
<footer>Saved outputs stay on your PC. The remote workflow runner can submit jobs. For reliable mobile playback and downloads use Tailscale Serve, or a named Cloudflare Tunnel + Access. Protected Cloudflare Quick Tunnels have a reported short-session authentication bug (Oct 2026).</footer>
<script>
const $=id=>document.getElementById(id);
const pic=/\.(png|jpe?g|webp|gif|bmp|avif|apng)$/i, vid=/\.(mp4|webm|mov|m4v|mkv)$/i;
const MAX_BLOB_SIZE=128*1024*1024; // Save mobile memory: stream larger files via native download instead.
const UNKNOWN_AUTH='Your remote session expired (or Cloudflare redirected the request). Reopen the dashboard to sign in. If this happens every few seconds on a protected Quick Tunnel, switch to Tailscale Serve or a named Cloudflare Tunnel + Access.';
let historySig='',inflight=false,pausePolling=false;
function el(t,c,txt){const n=document.createElement(t);if(c)n.className=c;if(txt!==undefined)n.textContent=txt;return n;}
function short(id){return String(id||'').slice(0,16)+'…';}
// Jobs often save several videos (e.g. silent + with audio, or a low-res draft); preview the
// largest one, which is the highest quality. Sizes come from the output folder when configured.
function largestVideo(files){return files.filter(f=>vid.test(f.filename)).reduce((best,f)=>!best||(f.size||0)>(best.size||0)?f:best,null);}
function previewFiles(job,limit){const v=largestVideo(job.files),pics=job.files.filter(f=>pic.test(f.filename));return (v?[v,...pics]:pics).slice(0,limit);}
function fileUrl(job,f,download){return '/api/file?'+new URLSearchParams({job:job.id,node:f.node,kind:f.kind,index:String(f.index),dl:download?'1':'0'});}
// Small JPEG for tiles; falls back to the full file where no thumbnail service exists.
function thumbImg(job,f){const im=el('img');im.loading='lazy';im.decoding='async';im.alt=f.filename;im.src='/api/job-thumb?'+new URLSearchParams({job:job.id,node:f.node,kind:f.kind,index:String(f.index)});im.onerror=()=>{im.onerror=null;im.src=fileUrl(job,f,false);};return im;}
function notify(message, auth=false){$('notice-text').textContent=message;$('notice').hidden=false;if(auth){pausePolling=true;$('connection').textContent='● Sign-in required';$('connection').className='down';}}
function clearNotice(){$('notice').hidden=true;}
function isAuthRedirect(r){return r.type==='opaqueredirect'||r.status===0||r.status===401||r.status===403||r.status>=300&&r.status<400;}
function failIfNotOurResponse(r,expected){
 if(isAuthRedirect(r))throw Error('AUTH:'+UNKNOWN_AUTH);
 const type=r.headers.get('Content-Type')||'';
 if(expected==='json'&&!type.includes('application/json'))throw Error('AUTH:'+UNKNOWN_AUTH);
 if(expected==='file'&&type.includes('text/html'))throw Error('AUTH:'+UNKNOWN_AUTH);
 if(!r.ok)throw Error('HTTP '+r.status);
}
function downloadName(h,fallback){
 const m=/filename\*=UTF-8''([^;]+)/i.exec(h||'');
 if(m){try{return decodeURIComponent(m[1]);}catch(e){}}
 return fallback;
}
function triggerLocalSave(blob,name){
 const u=URL.createObjectURL(blob),a=el('a');a.href=u;a.download=name;
 document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(u),90000);
}
async function downloadFile(url,fallback,button){
 if(button.dataset.busy==='1')return;
 button.dataset.busy='1';button.classList.add('busy');
 const prior=button.textContent;button.textContent='Preparing…';
 try{
  // `fetch` keeps credentials in the same browser context. This avoids Android
  // sending ordinary file links to a separate download context without auth cookies.
  const ctrl=new AbortController();
  const r=await fetch(url,{credentials:'same-origin',redirect:'manual',cache:'no-store',signal:ctrl.signal});
  failIfNotOurResponse(r,'file');
  const length=Number(r.headers.get('Content-Length')||'0');
  if(length>MAX_BLOB_SIZE){
   ctrl.abort();notify('This file exceeds 128 MiB. Using a normal streamed download to avoid exhausting phone memory. If it asks for Cloudflare login again, use Tailscale Serve or a named Cloudflare Access tunnel.');
   const a=el('a');a.href=url;a.download=fallback;document.body.append(a);a.click();a.remove();return;
  }
  const reader=r.body&&r.body.getReader?r.body.getReader():null;
  let blob;
  if(reader){
   const chunks=[];let total=0;
   for(;;){const {done,value}=await reader.read();if(done)break;total+=value.byteLength;
    if(total>MAX_BLOB_SIZE){await reader.cancel();notify('File larger than 128 MiB; switching to browser streamed download. Use Tailscale if Cloudflare requests login again.');const a=el('a');a.href=url;a.download=fallback;document.body.append(a);a.click();a.remove();return;}
    chunks.push(value);button.textContent='Downloading '+Math.round(total/(1024*1024))+' MB…';
   }
   blob=new Blob(chunks,{type:r.headers.get('Content-Type')||'application/octet-stream'});
  } else blob=await r.blob();
  const name=downloadName(r.headers.get('Content-Disposition'),fallback);
  triggerLocalSave(blob,name);clearNotice();
 }catch(e){const m=String(e.message||e);notify(m.startsWith('AUTH:')?m.slice(5):'Download failed: '+m,m.startsWith('AUTH:'));}
 finally{button.dataset.busy='0';button.classList.remove('busy');button.textContent=prior;}
}
function dlBtn(text,url,name,cls='button'){const a=el('a',cls,text);a.href=url;a.download=name;
 a.addEventListener('click',e=>{e.preventDefault();downloadFile(url,name,a);});return a;}
function niceDuration(secs){if(!Number.isFinite(secs))return '—';secs=Math.round(secs);const h=Math.floor(secs/3600),m=Math.floor(secs%3600/60),t=secs%60;return (h?h+'h ':'')+(m||h?m+'m ':'')+t+'s';}
function makeQueueCard(job){
 const wrap=el('div','queue-card'),title=el('div','jobline');
 title.append(el('strong','',job.running?'🟢 Running':'⏳ Waiting'),el('span','muted',job.title||'ComfyUI workflow'));
 wrap.append(title,el('div','mono jobid',job.id));
 const live=job.live||{},details=el('div','queue-facts');
 if(job.running){
  const node=live.node_title||live.node_type||(live.node?'Node '+live.node:null);
  if(live.total_percent!=null){
   // Mirrors ComfyUI's queue badge: whole-workflow share, then the running node's own progress.
   const box=el('div','total-progress'),fill=el('div','total-fill'),text=el('div','total-text');
   fill.style.width=Math.max(0,Math.min(100,live.total_percent))+'%';
   text.append(el('strong','','Total: '+Math.round(live.total_percent)+'%'));
   if(node)text.append(el('span','',node+(live.step_percent!=null?': '+Math.round(live.step_percent)+'%':'')));
   box.append(fill,text);wrap.append(box);
  }
  if(node)details.append(el('span','', 'Node: '+node));
  if(live.step_percent!==null&&live.step_percent!==undefined){
   details.append(el('span','',`Step ${live.step} / ${live.total_steps} (${live.step_percent}%)`));
   const bar=el('div','progress-track'),fill=el('div','progress-fill');fill.style.width=Math.max(0,Math.min(100,live.step_percent))+'%';bar.append(fill);wrap.append(bar);
  }else details.append(el('span','muted','Sampling progress: not reported'));
  details.append(el('span','', 'Elapsed: '+(live.elapsed_seconds==null?'Not available':niceDuration(live.elapsed_seconds)+(live.elapsed_is_observed?' (since detected)':''))));
  details.append(el('span','', 'ETA for current node: '+(live.eta_seconds==null?'Not available':niceDuration(live.eta_seconds))));
 }else details.append(el('span','muted','Awaiting execution'));
 if(job.total_nodes){details.append(el('span','',job.total_nodes+' nodes in workflow'));if(Number.isFinite(live.finished_nodes))details.append(el('span','',live.finished_nodes+' finished nodes reported'));}
 wrap.append(details);
 if(job.running&&live.has_preview){
  const img=el('img','queue-preview');img.loading='lazy';img.alt='Latest live ComfyUI preview';img.src='/api/live-preview?'+new URLSearchParams({job:job.id,seq:String(live.preview_seq||0)});wrap.append(img);
 }else if(job.running&&live.preview_file&&pic.test(live.preview_file.filename||'')){
  const img=el('img','queue-preview');img.loading='lazy';img.alt='Intermediate saved output';img.src='/api/queue-output?'+new URLSearchParams({job:job.id});wrap.append(img);
 }else if(job.preview_input_node){
  const img=el('img','queue-preview');img.loading='lazy';img.alt='Workflow input preview';img.src='/api/queue-input?'+new URLSearchParams({job:job.id,node:job.preview_input_node});wrap.append(img);
 }
 return wrap;
}
function renderQueue(q){
 const target=$('queue'),home=$('current-job');target.replaceChildren();home.replaceChildren();
 const all=[...q.running.map(x=>({...x,running:true})),...q.pending.map(x=>({...x,running:false}))];
 if(q.running.length){
  for(const job of q.running)home.append(makeQueueCard({...job,running:true}));
 }else home.append(el('div','home-empty','No job currently running. '+(q.pending.length?'Jobs waiting: '+q.pending.length+'.':'The queue is idle.')));
 if(!all.length){target.append(el('div','muted','No running or queued jobs'));return;}
 for(const job of all)target.append(makeQueueCard(job));
}
const videoObserver='IntersectionObserver'in window?new IntersectionObserver((entries,o)=>{
 for(const entry of entries)if(entry.isIntersecting){o.unobserve(entry.target);startVideo(entry.target);}
},{rootMargin:'200px'}):null;
function startVideo(v){if(v.src)return;v.src=v.dataset.src;v.preload='metadata';v.load();}
function videoBox(job,f){
 const box=el('div','video-box'),v=el('video'),tip=el('div','video-info','Loading video preview…');
 v.dataset.src=fileUrl(job,f,false);v.poster='/api/job-poster?'+new URLSearchParams({job:job.id,node:f.node,kind:f.kind,index:String(f.index)});v.controls=true;v.playsInline=true;v.setAttribute('playsinline','');v.preload='none';
 // All videos are loaded lazily on scroll; off-screen videos won't make dozens of auth requests.
 v.addEventListener('loadedmetadata',()=>{
  tip.remove();
  // Request a frame near the beginning so paused previews show an actual thumbnail.
  if(Number.isFinite(v.duration)&&v.duration>.3){try{v.currentTime=Math.min(.12,v.duration/12);}catch(e){}}
 });
 v.addEventListener('loadeddata',()=>tip.remove());
 v.addEventListener('error',()=>{
  tip.remove();if(box.querySelector('.video-error'))return;
  const msg=el('div','video-error','Video unavailable here — authentication, server range requests, or unsupported video codec. Try Save video below.');
  box.append(msg);
 });
 box.append(v,tip);
 if(videoObserver)videoObserver.observe(v);else startVideo(v);
 return box;
}
function renderGallery(history){
 const g=$('gallery');
 // Preserve expanded items as new jobs are added to ComfyUI history.
 const opened=new Set([...g.querySelectorAll('details.history-item[open]')].map(d=>d.dataset.job));
 g.replaceChildren();
 if(!history.length){g.append(el('div','muted','No jobs found in recent ComfyUI history.'));return;}
 for(const [index,job] of history.entries()){
  const accordion=el('details','history-item');accordion.dataset.job=job.id;
  const summary=el('summary','history-summary');
  const status=el('span','history-summary-status',(job.success?'✓':'⚠')+' Recent job '+(index+1));
  const time=el('span','history-summary-elapsed',job.elapsed_seconds==null?'⏱ Time unavailable':'⏱ '+niceDuration(job.elapsed_seconds));
  const right=el('span','history-summary-status');
  for(const s of job.settings||[])right.append(el('span','history-summary-setting',s.label+' '+s.value));
  right.append(time);summary.append(status,right);accordion.append(summary);
  accordion.open=opened.has(job.id);
  // Build previews and downloads on demand. This prevents dozens of dormant
  // videos/images from loading while recent history is collapsed.
  accordion.addEventListener('toggle',()=>{
   if(!accordion.open||accordion.querySelector('.history-content'))return;
   const c=el('article','card history-content'),preview=el('div','preview');let added=0;
   for(const f of previewFiles(job,2)){const u=fileUrl(job,f,false);
    if(pic.test(f.filename)){const a=el('a');a.href=u;a.target='_blank';a.rel='noopener noreferrer';a.append(thumbImg(job,f));preview.append(a);added++;}
    else if(vid.test(f.filename)){preview.append(videoBox(job,f));added++;}}
   if(!added)preview.append(el('div','no-preview','No image/video preview available'));
   const body=el('div','cardbody'),line=el('div','jobline');
   line.append(el('span','pill'+(job.success?'':' error'),job.success?'✓ Completed':'⚠ Check status'),el('span','muted',job.files.length+' downloadable file'+(job.files.length===1?'':'s')));
   body.append(line,el('div','mono jobid',job.id));
   const actions=el('div','actions'),mp4=largestVideo(job.files.filter(f=>/\.mp4$/i.test(f.filename)));
   if(mp4)actions.append(dlBtn('↓ Download MP4',fileUrl(job,mp4,true),mp4.filename));
   if(actions.childNodes.length)body.append(actions);
   const d=el('details'),fileSummary=el('summary','', 'Files · tap to download individually');d.append(fileSummary);
   const extras=el('div','files');
   for(const f of job.files){const r=el('div','file');r.append(el('span','filename',f.filename),dlBtn('↓ Save',fileUrl(job,f,true),f.filename,'button secondary'));extras.append(r);}
   const extrasActions=el('div','actions');
   if(job.files.length)extrasActions.append(dlBtn('↓ Download job ZIP','/api/zip?job='+encodeURIComponent(job.id),'comfy_job_'+job.id.slice(0,30)+'.zip','button secondary'));
   extrasActions.append(dlBtn('↓ Job JSON','/api/job.json?job='+encodeURIComponent(job.id),'comfy_job_'+job.id.slice(0,30)+'.json','button secondary'));
   extras.append(extrasActions);d.append(extras);body.append(d);
   if(job.files.some(f=>vid.test(f.filename)))body.append(el('div','muted','Video thumbnails use FFmpeg when configured. Browser playback requires a supported codec.'));
   c.append(preview,body);accordion.append(c);
  });
  g.append(accordion);
 }
}
async function refresh(){if(inflight||pausePolling||document.hidden)return;inflight=true;
 try{const r=await fetch('/api/state',{credentials:'same-origin',redirect:'manual',cache:'no-store'});failIfNotOurResponse(r,'json');const d=await r.json();
  $('connection').textContent='● Connected';$('connection').className='';
  $('running').textContent=d.queue.running.length;$('pending').textContent=d.queue.pending.length;$('completed').textContent=d.history.length;
  renderQueue(d.queue);const sig=JSON.stringify(d.history);if(sig!==historySig){renderGallery(d.history);historySig=sig;}
  window.comfyLastState=d;window.dispatchEvent(new CustomEvent('comfy:state',{detail:d}));
 }catch(e){const m=String(e.message||e);if(m.startsWith('AUTH:'))notify(m.slice(5),true);
  else{$('connection').textContent='● Offline / Error';$('connection').className='down';if(!historySig)$('gallery').replaceChildren(el('div','muted','Cannot read ComfyUI: '+m));}}
 finally{inflight=false;}}
$('retry').addEventListener('click',()=>window.location.reload());
const allHistory=$('history-export');
if(allHistory){allHistory.addEventListener('click',e=>{e.preventDefault();downloadFile('/api/history.json','comfy_recent_history.json',allHistory);});}
refresh();setInterval(refresh,5000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh();});
</script></body></html>'''


def comfy_get(route, timeout=15):
    with urlopen(COMFY_URL + route, timeout=timeout) as response:
        return json.load(response)


def queue_entries(values):
    result = []
    for entry in values if isinstance(values, list) else []:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        prompt = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
        extra = entry[3] if len(entry) > 3 and isinstance(entry[3], dict) else {}
        title = (extra.get('extra_pnginfo') or {}).get('workflow_name') or 'ComfyUI workflow'
        if not isinstance(title, str):
            title = 'ComfyUI workflow'
        preview_node = None
        for node_id, node in prompt.items():
            if (isinstance(node, dict) and 'loadimage' in str(node.get('class_type', '')).lower()
                    and isinstance((node.get('inputs') or {}).get('image'), str)):
                preview_node = str(node_id)
                break
        result.append({'id': str(entry[1]), 'title': title[:100],
                       'total_nodes': len(prompt), 'preview_input_node': preview_node})
    return result


def collect_files(record):
    """Only outputs ComfyUI itself has recorded as saved files are accessible."""
    result = []
    outputs = record.get("outputs", {})
    if not isinstance(outputs, dict):
        return result
    for node, output in outputs.items():
        if not isinstance(output, dict):
            continue
        for kind, group in output.items():
            if not isinstance(group, list):
                continue
            for index, item in enumerate(group):
                if (isinstance(item, dict)
                        and isinstance(item.get("filename"), str)
                        and item.get("type", "output") == "output"
                        and item["filename"].lower().endswith(ALLOWED_EXT)):
                    result.append({"node": str(node), "kind": str(kind), "index": index,
                                   "filename": item["filename"]})
    return result


FINISHED_JOBS = {}  # Completed history records never change; keep them so files skip ComfyUI.


def get_job(job_id):
    if not job_id or len(job_id) > 128 or any(ord(ch) < 32 for ch in job_id):
        raise ValueError("Invalid job ID")
    if job_id in FINISHED_JOBS:
        return FINISHED_JOBS[job_id]
    data = comfy_get("/history/" + quote(job_id, safe=""))
    record = data.get(job_id) if isinstance(data, dict) else None
    if not isinstance(record, dict):
        raise LookupError("Job not found in ComfyUI history")
    if (record.get("status") or {}).get("completed"):
        if len(FINISHED_JOBS) > 500:
            FINISHED_JOBS.clear()
        FINISHED_JOBS[job_id] = record
    return record


def job_file(record, query):
    node = query.get("node", [""])[0]
    kind = query.get("kind", [""])[0]
    index_str = query.get("index", [""])[0]
    if not index_str.isdecimal() or len(index_str) > 7:
        raise ValueError("Invalid file index")
    index = int(index_str)
    files = collect_files(record)
    if not any(f["node"] == node and f["kind"] == kind and f["index"] == index for f in files):
        raise LookupError("Output not found (or not a saved file)")
    return record["outputs"][node][kind][index]


def output_url(file):
    qs = urlencode({"filename": file["filename"], "subfolder": file.get("subfolder", ""), "type": file.get("type", "output")})
    return COMFY_URL + "/view?" + qs


def safe_filename(value):
    """Clean names for HTTP headers and archive members, including Windows paths."""
    base = str(value).replace("\\", "/").split("/")[-1]
    return re.sub(r'[^\w.()\- ]', "_", base, flags=re.UNICODE)[:180] or "output"


def disposition(name):
    # RFC 5987 form and an ASCII-only fallback; no reflected response headers.
    ascii_name = safe_filename(name).encode("ascii", "ignore").decode("ascii") or "download"
    return 'attachment; filename="' + ascii_name + '"; filename*=UTF-8\'\'' + quote(safe_filename(name))


class ArchiveTooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = "ComfyMonitor/3"

    def base_headers(self, cache="no-store"):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", cache)
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; script-src 'self' 'unsafe-inline'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'")

    def reply(self, status, payload, content_type, attachment=None, cache="no-store"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.base_headers(cache)
        if attachment:
            self.send_header("Content-Disposition", disposition(attachment))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def json_reply(self, payload, attachment=None):
        return self.reply(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8", attachment=attachment)

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        try:
            if parts.path == "/":
                return self.reply(200, HTML.encode("utf-8"), "text/html; charset=utf-8")
            if parts.path == "/api/state":
                queue = comfy_get("/queue")
                history = comfy_get("/history?max_items=" + str(HISTORY_LIMIT))
                items = []
                for job_id, record in history.items():
                    if not isinstance(record, dict):
                        continue
                    status = record.get("status") or {}
                    prompt = record.get("prompt") or []
                    num = prompt[0] if isinstance(prompt, list) and prompt and isinstance(prompt[0], (int, float)) else -1
                    items.append({"id": job_id, "number": num,
                                  "success": status.get("status_str", "success") == "success",
                                  "files": collect_files(record)})
                items.sort(key=lambda j: j["number"], reverse=True)
                for item in items:
                    del item["number"]
                return self.json_reply({"queue": {"running": queue_entries(queue.get("queue_running")),
                                                  "pending": queue_entries(queue.get("queue_pending"))},
                                        "history": items})
            if parts.path == "/api/history.json":
                return self.json_reply(comfy_get("/history?max_items=" + str(HISTORY_LIMIT)),
                                       "comfy_recent_history.json")
            if parts.path in ("/api/job.json", "/api/file", "/api/zip"):
                job_id = query.get("job", [""])[0]
                record = get_job(job_id)
                if parts.path == "/api/job.json":
                    return self.json_reply({job_id: record}, "comfy_job_" + job_id[:30] + ".json")
                if parts.path == "/api/file":
                    file = job_file(record, query)
                    download = query.get("dl", ["0"])[0] == "1"
                    return self.send_file(file, download)
                return self.send_zip(job_id, record)
            return self.reply(404, b"Not found", "text/plain")
        except (LookupError, ValueError, KeyError, TypeError) as exc:
            return self.reply(404, str(exc).encode("utf-8")[:300], "text/plain")
        except ArchiveTooLarge as exc:
            return self.reply(413, str(exc).encode("utf-8"), "text/plain")
        except HTTPError as exc:
            return self.reply(502, ("ComfyUI returned HTTP " + str(exc.code)).encode(), "text/plain")
        except (URLError, OSError) as exc:
            return self.reply(502, ("Cannot read ComfyUI: " + str(exc)).encode("utf-8")[:350], "text/plain")

    def do_HEAD(self):
        """Some mobile media players probe metadata with HEAD before Range GET."""
        parts = urlsplit(self.path)
        if parts.path != "/api/file":
            self.send_response(405)
            self.send_header("Allow", "GET")
            self.end_headers()
            return
        try:
            query = parse_qs(parts.query)
            record = get_job(query.get("job", [""])[0])
            file = job_file(record, query)
            filename = safe_filename(file["filename"])
            with urlopen(Request(output_url(file), method="HEAD"), timeout=30) as stream:
                self.send_response(stream.status)
                mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                if filename.lower().endswith(".mkv"):
                    mime = "video/x-matroska"
                self.send_header("Content-Type", mime)
                self.base_headers()
                self.send_header("Accept-Ranges", stream.headers.get("Accept-Ranges", "bytes"))
                for key in ("Content-Length", "Content-Range"):
                    if stream.headers.get(key):
                        self.send_header(key, stream.headers[key])
                self.end_headers()
        except (LookupError, ValueError, OSError, HTTPError, URLError) as exc:
            self.send_response(404)
            self.end_headers()

    def send_file(self, file, download):
        filename = safe_filename(file["filename"])
        mimetype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        if filename.lower().endswith(".mkv"):
            mimetype = "video/x-matroska"
        headers = {}
        user_range = self.headers.get("Range", "")
        if re.fullmatch(r"bytes=\d*-\d*", user_range) and len(user_range) <= 80:
            headers["Range"] = user_range
        with urlopen(Request(output_url(file), headers=headers), timeout=60) as stream:
            self.send_response(stream.status)
            self.send_header("Content-Type", mimetype)
            self.base_headers()
            if download:
                self.send_header("Content-Disposition", disposition(filename))
            for key in ("Content-Length", "Content-Range"):
                if stream.headers.get(key):
                    self.send_header(key, stream.headers[key])
            self.send_header("Accept-Ranges", stream.headers.get("Accept-Ranges", "bytes"))
            if not download:
                self.send_header("Content-Disposition", "inline")
            self.end_headers()
            try:
                shutil.copyfileobj(stream, self.wfile, length=128 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass  # User cancelled the download or navigated away.

    def send_zip(self, job_id, record):
        files = collect_files(record)
        if not files:
            raise LookupError("This job has no saved files")
        if len(files) > MAX_ARCHIVE_FILES:
            raise ArchiveTooLarge("Too many files for a ZIP. Download them individually instead.")
        # ZIP is created in a temporary file so large video archives don't fill RAM.
        with tempfile.TemporaryFile(mode="w+b") as archive:
            total = 0
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as zipped:
                zipped.writestr("job_history.json", json.dumps({job_id: record}, ensure_ascii=False, indent=2))
                for n, ref in enumerate(files):
                    file = record["outputs"][ref["node"]][ref["kind"]][ref["index"]]
                    name = f"outputs/{n+1:03d}_{safe_filename(ref['filename'])}"
                    with urlopen(output_url(file), timeout=90) as stream:
                        with zipped.open(name, mode="w", force_zip64=True) as out:
                            while True:
                                chunk = stream.read(256 * 1024)
                                if not chunk:
                                    break
                                total += len(chunk)
                                if total > MAX_ARCHIVE_SIZE:
                                    raise ArchiveTooLarge("ZIP exceeds 2 GiB. Download large files individually instead.")
                                out.write(chunk)
            archive.seek(0, os.SEEK_END)
            size = archive.tell()
            archive.seek(0)
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.base_headers()
            self.send_header("Content-Disposition", disposition("comfy_job_" + job_id[:30] + ".zip"))
            self.send_header("Content-Length", str(size))
            self.end_headers()
            try:
                shutil.copyfileobj(archive, self.wfile, length=128 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        return self.reply(405, b"Read-only dashboard. Changes to jobs are disabled.", "text/plain")

    do_PUT = do_DELETE = do_PATCH = do_POST

    def handle_one_request(self):
        started = time.monotonic()
        try:
            super().handle_one_request()
        except ConnectionError:
            # The browser gave up on this request (page reloaded, folder changed, app backgrounded).
            self.close_connection = True
        elapsed = time.monotonic() - started
        route = urlsplit(getattr(self, "path", "") or "").path
        if elapsed > SLOW_REQUEST and route and not route.startswith(LONG_ROUTES):
            print(f"[{time.strftime('%H:%M:%S')}] Slow request: {self.command} {route} took {elapsed:.1f}s", flush=True)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    print("=" * 55)
    print("Comfy Monitor · read-only, with mobile downloads")
    print(f"Open on this PC: http://127.0.0.1:{PORT}")
    print(f"ComfyUI address: {COMFY_URL}")
    print(f"Showing up to {HISTORY_LIMIT} recent jobs")
    print("Only expose this monitor, never ComfyUI port 8188.")
    print("Press Ctrl+C to stop.")
    print("=" * 55)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
