// Comfy Mobile v6 — read-only output directory browser. Same origin, no filesystem writes.
(()=>{
 'use strict';
 const get=id=>document.getElementById(id), current={roots:[],root:0,path:'',offset:0,items:[],next:null,loading:false,lastLoaded:0,view:'grid'};
 const pic=/\.(png|jpe?g|webp|gif|bmp|avif|apng|tif|tiff)$/i;
 function el(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
 function size(bytes){if(!Number.isFinite(bytes))return '';const unit=['B','KB','MB','GB','TB'];let n=bytes,i=0;while(n>=1024&&i<4){n/=1024;i++;}return (i?n.toFixed(1):n)+' '+unit[i];}
 // v= (the file's mtime) lets the phone cache files and thumbnails until the file changes.
 function fileURL(item,download=false){return '/api/output-file?'+new URLSearchParams({root:current.root,path:item.path,dl:download?'1':'0',v:item.modified});}
 function thumbURL(item){return '/api/output-thumb?'+new URLSearchParams({root:current.root,path:item.path,v:item.modified});}
 function feedback(message,bad=false){const node=get('output-status');node.textContent=message;node.classList.toggle('error-text',bad);}
 async function load(reset=true){
  if(current.loading)return;
  current.loading=true;get('output-next').disabled=true;
  if(reset){current.items=[];current.offset=0;clearGrid();}
  feedback('Loading folder…');
  try{
   const url='/api/outputs?'+new URLSearchParams({root:current.root,path:current.path,offset:current.offset,limit:120});
   const r=await fetch(url,{credentials:'same-origin',redirect:'manual',cache:'no-store'});
   if(!r.ok||!(r.headers.get('Content-Type')||'').includes('application/json'))throw Error('Output folder unavailable or remote session expired');
   const data=await r.json();
   if(data.error)throw Error(data.error);
   current.roots=data.roots;current.next=data.next_offset;current.items.push(...data.items);current.lastLoaded=Date.now();
   const sel=get('output-root'),old=String(current.root);sel.replaceChildren();
   data.roots.forEach(root=>sel.add(new Option(root.label,root.index)));
   if(data.roots.length)sel.value=old;
   renderBreadcrumbs();renderItems();
   get('output-next').hidden=current.next==null;get('output-next').disabled=false;
   feedback(`Showing ${current.items.length} of ${data.count} items · files sorted newest first`);
  }catch(err){feedback(err.message,true);get('output-next').hidden=true;
   if(!current.roots.length)get('output-grid').replaceChildren(el('p','muted','Set output_dirs in config.json to your Windows ComfyUI output folder and restart the monitor.'));
  }finally{current.loading=false;}
 }
 function navigate(path){current.path=path;current.offset=0;load(true);}
 function renderBreadcrumbs(){
  const area=get('output-crumbs');area.replaceChildren();
  const root=el('button','crumb','🏠 Outputs');root.type='button';root.onclick=()=>navigate('');area.append(root);
  let partial='';current.path.split('/').filter(Boolean).forEach(segment=>{
   partial+=(partial?'/':'')+segment;
   area.append(el('span','muted','/'));
   const crumb=el('button','crumb',segment),route=partial;
   crumb.type='button';crumb.onclick=()=>navigate(route);area.append(crumb);
  });
 }
 // Videos autoplay muted and looped, but only the first MAX_PLAYING on screen. A video is only
 // fetched near the viewport and is unloaded again off-screen: every playing video holds a
 // hardware decoder and buffers, and too many at once makes phones kill the browser tab.
 const MAX_PLAYING=4,visible=new Set();
 function unload(vid){if(vid.getAttribute('src')){vid.pause();vid.removeAttribute('src');vid.load();}}
 function schedule(){
  // Videos the user opened controls on keep their slot; the rest go top to bottom.
  const order=[...visible].sort((a,b)=>(b.controls-a.controls)||(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1));
  order.forEach((vid,i)=>{
   if(i<MAX_PLAYING){if(!vid.getAttribute('src'))vid.src=vid.dataset.src;vid.play().catch(()=>{});}
   else unload(vid);
  });
 }
 const watcher='IntersectionObserver' in window?new IntersectionObserver(entries=>{
  for(const entry of entries){
   if(entry.isIntersecting)visible.add(entry.target);
   else{visible.delete(entry.target);unload(entry.target);}
  }
  schedule();
 },{rootMargin:'150px 0px'}):null;
 function clearGrid(){
  if(watcher)watcher.disconnect();visible.clear();
  const grid=get('output-grid');grid.querySelectorAll('video').forEach(unload);grid.replaceChildren();
 }
 function makeVideo(item){
  const vid=el('video');vid.muted=true;vid.defaultMuted=true;vid.loop=true;vid.autoplay=true;
  vid.playsInline=true;vid.setAttribute('playsinline','');vid.setAttribute('muted','');
  vid.preload='none';vid.poster=thumbURL(item);vid.dataset.src=fileURL(item);
  vid.title=item.name+' · tap for controls';
  // Tap reveals native controls (unmute, seek, fullscreen) without stopping the loop.
  vid.onclick=()=>{if(!vid.controls){vid.controls=true;schedule();}};
  vid.onerror=()=>vid.closest('.output-video-shell')?.classList.add('no-poster');
  if(watcher)watcher.observe(vid);else{vid.src=vid.dataset.src;vid.preload='auto';}
  return vid;
 }
 function setView(view){
  current.view=view==='list'?'list':'grid';
  try{localStorage.setItem('comfyOutputView',current.view);}catch(e){}
  get('output-grid').classList.toggle('list-view',current.view==='list');
  for(const name of ['grid','list'])get('output-view-'+name).setAttribute('aria-pressed',String(current.view===name));
  if(current.lastLoaded)renderItems();
 }
 function makeCard(item){
  const box=el('article','output-card');
  if(item.kind==='folder'){
   const folder=el('button','folder-card','📁');folder.type='button';folder.title='Open '+item.name;folder.onclick=()=>navigate(item.path);
   folder.append(el('span','',item.name));box.append(folder);return box;
  }
  const media=el('div','output-media');
  if(item.media==='image'){
   const link=el('a');link.href=fileURL(item);link.target='_blank';link.rel='noopener noreferrer';
   // A small cached thumbnail; tapping still opens the full-size original.
   const image=el('img');image.loading='lazy';image.decoding='async';image.alt=item.name;image.src=thumbURL(item);
   image.onerror=()=>{image.onerror=null;image.src=fileURL(item);};link.append(image);media.append(link);
  }else if(item.media==='video'){
   const shell=el('div','output-video-shell');shell.append(makeVideo(item));media.append(shell);
  }else media.append(el('div','file-icon','📄'));
  const text=el('div','output-cardbody');text.append(el('div','output-filename',item.name));
  text.append(el('div','muted',size(item.size)+' · '+new Date(item.modified*1000).toLocaleString()));
  const list=current.view==='list';
  const actions=el('div','actions'),dl=el('a',list?'button secondary dl-small':'button secondary',list?'↓':'↓ Download');
  dl.href=fileURL(item,true);dl.download=item.name;dl.title='Download '+item.name;dl.setAttribute('aria-label','Download '+item.name);
  dl.onclick=event=>{event.preventDefault();downloadFile(dl.href,item.name,dl);};actions.append(dl);
  text.append(actions);box.append(media,text);return box;
 }
 function renderItems(){
  clearGrid();const grid=get('output-grid');
  const term=get('output-search').value.trim().toLowerCase();
  const shown=current.items.filter(row=>!term||row.name.toLowerCase().includes(term));
  if(!shown.length)grid.append(el('p','muted',term?'No matching items on this loaded page.':'This directory is empty.'));
  for(const item of shown)grid.append(makeCard(item));
 }
 window.addEventListener('comfy:tab', event=>{if((event.detail.tab==='home'||event.detail.tab==='outputs')&&!current.loading&&Date.now()-current.lastLoaded>30000)load(true);});
 get('output-refresh').onclick=()=>load(true);
 get('output-root').onchange=()=>{current.root=Number(get('output-root').value);navigate('');};
 get('output-search').oninput=renderItems;
 get('output-next').onclick=()=>{if(current.next!=null){current.offset=current.next;load(false);}};
 get('output-view-grid').onclick=()=>setView('grid');
 get('output-view-list').onclick=()=>setView('list');
 let saved='grid';try{saved=localStorage.getItem('comfyOutputView')||'grid';}catch(e){}
 setView(saved);
 load(true); // Home includes the browser on first visit, even before tab events fire.
})();
