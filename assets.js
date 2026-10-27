// Comfy Mobile: ComfyUI's Media Assets (Generated / Imported) as a tab, plus helpers the input picker uses.
(()=>{
 'use strict';
 const get=id=>document.getElementById(id);
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
 const fileURL=(a,download=false)=>'/api/asset-file?'+new URLSearchParams({type:a.type,file:a.file,dl:download?'1':'0'});
 const posterURL=a=>'/api/asset-poster?'+new URLSearchParams({type:a.type,file:a.file});
 function size(bytes){if(!Number.isFinite(bytes))return '';const unit=['B','KB','MB','GB','TB'];let n=bytes,i=0;while(n>=1024&&i<4){n/=1024;i++;}return (i?n.toFixed(1):n)+' '+unit[i];}
 function facts(a){const out=[];if(a.width&&a.height)out.push(a.width+'×'+a.height);
  if(Number.isFinite(a.duration))out.push(a.duration.toFixed(1)+' s');if(size(a.size))out.push(size(a.size));return out.join(' · ');}
 async function fetchPage(kind,offset=0,search='',limit=60){
  const r=await fetch('/api/media-assets?'+new URLSearchParams({kind,offset,limit,q:search}),{credentials:'same-origin',redirect:'manual',cache:'no-store'});
  if(!(r.headers.get('Content-Type')||'').includes('application/json'))throw Error('Assets unavailable or the remote session expired; reopen the dashboard.');
  const data=await r.json();if(!r.ok||data.error)throw Error(data.error||'HTTP '+r.status);return data;
 }
 // Small preview for picker tiles; only generated videos have FFmpeg posters (they live under output_dirs).
 function thumb(a){
  if(a.media==='image'||(a.media==='video'&&a.type==='output')){
   const im=el('img');im.loading='lazy';im.alt=a.name;im.src=a.media==='image'?fileURL(a):posterURL(a);
   im.onerror=()=>im.replaceWith(el('div','asset-icon',a.media==='video'?'🎞️':'🖼️'));return im;
  }
  return el('div','asset-icon',a.media==='video'?'🎞️':a.media==='audio'?'🎵':'📄');
 }
 window.ComfyAssets={fetchPage,thumb,facts};

 // Assets tab.
 const state={kind:'generated',next:0,count:0,loading:false,loaded:0};
 const status=(text,bad=false)=>{const n=get('assets-status');n.textContent=text;n.classList.toggle('error-text',bad);};
 // Videos fetch nothing until near the viewport; imported ones (no poster) then load their first frame.
 const watcher='IntersectionObserver' in window?new IntersectionObserver((entries,o)=>{
  for(const entry of entries)if(entry.isIntersecting){o.unobserve(entry.target);start(entry.target);}
 },{rootMargin:'200px 0px'}):null;
 function start(v){if(v.src)return;if(!v.poster)v.preload='metadata';v.src=v.dataset.src;}
 function card(a){
  const box=el('article','output-card'),media=el('div','output-media');
  if(a.media==='image'){
   const link=el('a');link.href=fileURL(a);link.target='_blank';link.rel='noopener noreferrer';
   const image=el('img');image.loading='lazy';image.alt=a.name;image.src=fileURL(a);link.append(image);media.append(link);
  }else if(a.media==='video'){
   const shell=el('div','output-video-shell'),v=el('video');v.controls=true;v.playsInline=true;v.setAttribute('playsinline','');
   v.preload='none';if(a.type==='output')v.poster=posterURL(a);v.dataset.src=fileURL(a)+(a.type==='output'?'':'#t=0.1');
   shell.append(v);media.append(shell);if(watcher)watcher.observe(v);else start(v);
  }else media.append(el('div','file-icon',a.media==='audio'?'🎵':'📄'));
  const body=el('div','output-cardbody');body.append(el('div','output-filename',a.name));
  if(facts(a))body.append(el('div','muted',facts(a)));
  if(a.media==='audio'){const au=el('audio');au.controls=true;au.preload='none';au.src=fileURL(a);au.style.width='100%';body.append(au);}
  const actions=el('div','actions'),dl=el('a','button secondary','↓ Download');
  dl.href=fileURL(a,true);dl.download=a.name;dl.setAttribute('aria-label','Download '+a.name);
  dl.onclick=e=>{if(typeof downloadFile==='function'){e.preventDefault();downloadFile(dl.href,a.name,dl);}};
  actions.append(dl);body.append(actions);box.append(media,body);return box;
 }
 async function load(reset=true){
  if(state.loading)return;state.loading=true;get('assets-more').disabled=true;
  if(reset){state.next=0;state.count=0;if(watcher)watcher.disconnect();get('assets-grid').replaceChildren();}
  status('Loading…');
  try{
   const data=await fetchPage(state.kind,state.next,get('assets-search').value.trim());
   state.next=data.next;state.count+=data.items.length;state.loaded=Date.now();
   for(const a of data.items)get('assets-grid').append(card(a));
   if(!state.count)get('assets-grid').replaceChildren(el('p','muted','No '+state.kind+' assets found.'));
   status(state.count+' shown'+(data.has_more?' · more available':''));
   get('assets-more').hidden=!data.has_more;
  }catch(e){status(e.message,true);get('assets-more').hidden=true;}
  finally{state.loading=false;get('assets-more').disabled=false;}
 }
 for(const b of get('assets-kinds').querySelectorAll('button'))b.onclick=()=>{
  state.kind=b.dataset.kind;
  for(const o of get('assets-kinds').querySelectorAll('button'))o.setAttribute('aria-pressed',String(o===b));
  load(true);
 };
 let typing=null;
 get('assets-search').oninput=()=>{clearTimeout(typing);typing=setTimeout(()=>load(true),350);};
 get('assets-refresh').onclick=()=>load(true);
 get('assets-more').onclick=()=>load(false);
 window.addEventListener('comfy:tab',e=>{if(e.detail.tab==='assets'&&Date.now()-state.loaded>30000)load(true);});
 if(location.hash==='#assets')load(true);
})();
