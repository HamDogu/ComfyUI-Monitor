// Comfy Mobile v6 — Home "Recent outputs": always-visible history cards fed by the shared /api/state poll.
(()=>{
 'use strict';
 const SHOWN=12;
 let sig='',skew=0; // skew = PC clock minus this device's clock, in seconds
 function ago(stamp){
  if(stamp==null)return 'Time unknown';
  const secs=Math.max(0,Date.now()/1000+skew-stamp);
  if(secs<45)return 'Just now';
  const m=Math.round(secs/60);if(m<60)return m+' min ago';
  const h=Math.round(secs/3600);if(h<24)return h+' h ago';
  const d=Math.round(secs/86400);return d+(d===1?' day ago':' days ago');
 }
 function updateAges(){for(const node of document.querySelectorAll('#recent-outputs [data-completed]'))node.textContent='🕒 '+ago(node.dataset.completed===''?null:Number(node.dataset.completed));}
 function makeCard(job){
  const box=el('article','output-card recent-card'),media=el('div','output-media');
  const shown=previewFiles(job,1)[0]; // largest video when the job saved several
  if(shown&&pic.test(shown.filename)){
   const link=el('a');link.href=fileUrl(job,shown,false);link.target='_blank';link.rel='noopener noreferrer';
   const image=el('img');image.loading='lazy';image.alt=shown.filename;image.src=fileUrl(job,shown,false);link.append(image);media.append(link);
  }else if(shown)media.append(videoBox(job,shown));
  else media.append(el('div','file-icon',job.files.length?'📄':'∅'));
  const body=el('div','output-cardbody'),facts=el('div','recent-facts');
  const when=el('span','',''),run=el('span','',job.elapsed_seconds==null?'':'⏱ '+niceDuration(job.elapsed_seconds));
  when.dataset.completed=job.completed_at==null?'':String(job.completed_at);
  facts.append(el('span','pill'+(job.success?'':' error'),job.success?'✓':'⚠'),when,run);
  for(const s of job.settings||[])facts.append(el('span','',s.label+' '+s.value));
  body.append(facts,el('div','output-filename',shown?shown.filename:(job.files[0]||{}).filename||'No saved files'));
  const actions=el('div','actions'),main=shown||job.files[0];
  if(main)actions.append(dlBtn('↓ Download',fileUrl(job,main,true),main.filename));
  if(job.files.length>1)actions.append(dlBtn('↓ ZIP ('+job.files.length+')','/api/zip?job='+encodeURIComponent(job.id),'comfy_job_'+job.id.slice(0,30)+'.zip','button secondary'));
  if(actions.childNodes.length)body.append(actions);
  box.append(media,body);return box;
 }
 function render(history){
  const grid=$('recent-grid');grid.replaceChildren();
  if(!history.length){grid.append(el('p','muted','No jobs in recent ComfyUI history yet.'));return;}
  for(const job of history.slice(0,SHOWN))grid.append(makeCard(job));
  $('recent-more').hidden=history.length<=SHOWN;
 }
 function onState(d){
  if(Number.isFinite(d.server_time))skew=d.server_time-Date.now()/1000;
  // Only rebuild when history changes, so previews and playing videos aren't reloaded every poll.
  const next=JSON.stringify(d.history.slice(0,SHOWN));
  if(next!==sig){render(d.history);sig=next;}
  updateAges();
 }
 window.addEventListener('comfy:state',event=>onState(event.detail));
 if(window.comfyLastState)onState(window.comfyLastState); // first poll may finish before this script loads
 $('recent-more').onclick=()=>window.switchComfyTab&&window.switchComfyTab('queue');
 setInterval(updateAges,30000);
})();
