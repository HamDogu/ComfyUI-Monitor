// Comfy Mobile v6: same-origin authenticated workflow runner & scheduler.
(() => {
  'use strict';
  const get = (id) => document.getElementById(id);
  const TOKEN = document.querySelector('meta[name="monitor-token"]').content;
  let selectedWorkflow = '', workflowNodes = [], imageTarget = null;
  let loraNames = [], workflowIndex = new Map();
  // Fantastic H3 Media Loader limits (pictures, videos, and audio incl. paired soundtracks).
  const MEDIA_MAX = {picture:9, video:3, audio:3};
  const MEDIA_ACCEPT = 'image/*,video/*,audio/*,.mkv,.avi,.mpg,.mpeg,.m4v,.opus,.flac,.tif,.tiff';
  const IMAGE_ACCEPT = '.png,.jpg,.jpeg,.webp,.gif,.bmp,image/png,image/jpeg,image/webp,image/gif,image/bmp';
  const el = (tag, cls, text) => {const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
  const previewURL = file => '/api/input-preview?'+new URLSearchParams({file});

  async function request(path, opts={}) {
    const write = !!opts.method && opts.method !== 'GET';
    const headers = {...(opts.headers || {})};
    if(write) headers['X-Monitor-Token'] = TOKEN;
    const r = await fetch(path, {...opts, headers, credentials:'same-origin', redirect:'manual', cache:'no-store'});
    const type = r.headers.get('Content-Type') || '';
    if (r.type==='opaqueredirect'||r.status===0||!type.includes('application/json'))
      throw Error('Authentication redirected. Reopen the dashboard; on Cloudflare, use named Access or Tailscale.');
    const data = await r.json();
    if(!r.ok) throw Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  const getJSON = url => request(url);
  const postJSON = (url, data) => request(url, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  function message(s, bad=false) {
    const p=get('schedule-feedback'); p.hidden=false; p.className='controlpanel'+(bad?' error':''); p.textContent=s;
    p.scrollIntoView({behavior:'smooth',block:'nearest'});
  }
  // v6 navigation is embedded in the page, so it works even if this module fails to load.
  // The planner listens for tab changes but never takes ownership of nav buttons.
  window.addEventListener('comfy:tab', event=>{
    if(event.detail.tab==='plan'){loadWorkflows();loadSchedules();}
  });
  get('reload-workflows').onclick=loadWorkflows;
  get('refresh-schedules').onclick=loadSchedules;

  async function loadWorkflows() {
    const status=get('workflow-status');status.textContent='Loading…';
    try {
      const data=await getJSON('/api/workflows');
      const sel=get('workflow-select'),old=selectedWorkflow;
      workflowIndex=new Map(data.map(d=>[d.id,d]));
      sel.replaceChildren(new Option('Choose a workflow…',''));
      const open=data.filter(d=>d.id.startsWith('open:'));
      const disk=data.filter(d=>d.id.startsWith('disk:'));
      const groups=[[open,'Open / recently seen ComfyUI tabs'],
        [disk.filter(d=>(d.group||'api')==='api'),'PC workflows (API format)'],
        [disk.filter(d=>d.group==='ui'),'PC workflows (UI format · converted automatically)'],
        [disk.filter(d=>d.group==='converted'),'Converted workflows (API copies)']];
      for (const [items,label] of groups) {
        if(!items.length)continue;
        const optgroup=document.createElement('optgroup');optgroup.label=label;
        for(const item of items){const suffix=item.updated?' · '+new Date(item.updated*1000).toLocaleTimeString():'';
          const o=new Option(item.title+(item.format==='ui'?' ⇄':'')+suffix,item.id);optgroup.append(o);}
        sel.append(optgroup);
      }
      if(data.some(i=>i.id===old))sel.value=old;
      else{sel.value='';selectedWorkflow='';get('parameter-panel').hidden=true;}
      const ui=disk.filter(d=>d.format==='ui').length;
      status.textContent=open.length+' open snapshots · '+disk.length+' PC JSON files'+(ui?' ('+ui+' UI format)':'');
    }catch(e){status.textContent=String(e.message||e);}
  }

  function showConversion(info){
    const box=get('conversion-status');box.replaceChildren();
    get('convert-workflow').hidden=(workflowIndex.get(selectedWorkflow)||{}).format!=='ui';
    if(!info){box.hidden=true;return;}
    box.hidden=false;
    const when=info.converted_at?' · '+new Date(info.converted_at*1000).toLocaleString():'';
    box.append(el('div','','⇄ UI workflow converted to API format ('+(info.nodes||'?')+' nodes) · saved as '+info.path+when));
    if(info.warnings&&info.warnings.length){
      const list=el('ul');info.warnings.forEach(w=>list.append(el('li','',w)));
      box.append(el('div','error-text','Check before running:'),list);
    }
  }
  async function loadWorkflow(){
    selectedWorkflow=get('workflow-select').value;
    get('parameter-panel').hidden=true; get('asset-picker').hidden=true; showConversion(null);
    if(!selectedWorkflow)return;
    const ui=(workflowIndex.get(selectedWorkflow)||{}).format==='ui';
    if(ui)get('workflow-status').textContent='Converting UI workflow…';
    try{
      const d=await getJSON('/api/workflow?id='+encodeURIComponent(selectedWorkflow));
      workflowNodes=d.nodes;renderFields(d.nodes);get('parameter-panel').hidden=false;showConversion(d.conversion);
      if(ui)get('workflow-status').textContent='';
    }catch(e){if(ui)get('workflow-status').textContent='';message('Cannot load workflow: '+e.message,true);}
  }
  get('workflow-select').onchange=loadWorkflow;
  get('convert-workflow').onclick=async()=>{
    const btn=get('convert-workflow');btn.disabled=true;
    try{const info=await postJSON('/api/convert',{id:selectedWorkflow,force:true});
      message('✅ Re-converted with current ComfyUI node definitions → '+info.path);await loadWorkflow();}
    catch(e){message('Conversion failed: '+e.message,true);}finally{btn.disabled=false;}
  };
  // LoRA strength: a -0.2…1 slider (widened for values outside it) beside an exact number box.
  const LORA_MIN=-0.2,LORA_MAX=1;
  const round2=v=>Math.round(v*100)/100;
  const loraLabel=name=>String(name||'').split(/[\\/]/).pop().replace(/\.(safetensors|ckpt|pt|pth|bin)$/i,'');
  function strengthControl(text,value,changed){
    const row=el('div','strength'),range=el('input'),num=el('input');value=Number(value)||0;
    const fit=v=>{range.min=String(Math.min(LORA_MIN,v));range.max=String(Math.max(LORA_MAX,v));range.value=String(v);};
    range.type='range';range.step='0.05';fit(value);
    num.type='number';num.step='0.05';num.value=String(value);
    range.setAttribute('aria-label',(text||'LoRA')+' strength');num.setAttribute('aria-label',(text||'LoRA')+' strength value');
    range.oninput=()=>{const v=round2(Number(range.value));num.value=String(v);changed(v);};
    num.oninput=()=>{const v=Number(num.value);if(num.value.trim()&&Number.isFinite(v)){fit(v);changed(v);}};
    const tag=el('small','',text);tag.hidden=!text;row.append(tag,range,num);return row;
  }
  // One LoRA: on/off switch, name, and one strength (model+clip together) unless they differ.
  function loraRow(name,item,key,twoKey,changed){
    const box=el('div','lora-row'+(item.on?'':' off')),head=el('div','lora-head'),toggle=el('input','lora-toggle');
    toggle.type='checkbox';toggle.checked=!!item.on;toggle.setAttribute('aria-label','Use '+loraLabel(name));
    toggle.onchange=()=>{item.on=toggle.checked;box.classList.toggle('off',!item.on);changed();};
    head.title=name;head.append(toggle,el('strong','',loraLabel(name)));box.append(head);
    const linked=item[twoKey]==null||item[twoKey]===item[key];
    box.append(strengthControl(linked?'':'Model',item[key],v=>{item[key]=v;if(linked&&item[twoKey]!=null)item[twoKey]=v;changed();}));
    if(!linked)box.append(strengthControl('CLIP',item[twoKey],v=>{item[twoKey]=v;changed();}));
    return box;
  }
  // Pixaroma LoRA Loader: every row of its LoraLoaderState list.
  function loraStackEditor(ref,text){
    let state={};try{state=JSON.parse(text)||{};}catch(e){}
    const rows=Array.isArray(state.loras)?state.loras.filter(r=>r&&typeof r==='object'):[];
    const wrap=el('div'),summary=el('div','muted');
    const count=()=>{summary.textContent=rows.filter(r=>r.on).length+' / '+rows.length+' LoRAs on';};
    const changed=()=>{ref.dataset.value=JSON.stringify({loras:rows.map(r=>({name:r.name,on:!!r.on,sm:r.sm,sc:r.sc}))});
      ref.dataset.dirty='1';count();};
    wrap.append(summary);rows.forEach(r=>wrap.append(loraRow(r.name,r,'sm','sc',changed)));count();
    if(!rows.length)summary.textContent='No LoRAs in this loader.';
    return wrap;
  }
  function controlFor(node,field){
    const container=document.createElement('div');container.className='field';
    const label=document.createElement('label');label.textContent=field.label||field.key;
    const ref=document.createElement('span');ref.className='node-edit';ref.dataset.node=node.id;ref.dataset.key=field.key;
    ref.dataset.kind=field.kind;ref.dataset.original=JSON.stringify(field.value);
    const name=node.id+'-'+field.key.replace(/[^a-zA-Z0-9_-]/g,'_');
    if(field.kind==='media'){
      label.textContent='Media Loader items';container.append(label);
      ref.append(mediaEditor(ref,field.value));container.append(ref);return container;
    }
    if(field.kind==='lora_stack'){
      ref.append(loraStackEditor(ref,field.value));container.append(ref);return container;
    }
    if(field.kind==='lora_row'){  // rgthree Power Lora Loader row
      const item={...field.value};
      ref.append(loraRow(item.lora,item,'strength','strengthTwo',()=>{ref.dataset.value=JSON.stringify(item);ref.dataset.dirty='1';}));
      container.append(ref);return container;
    }
    if(field.kind==='number'){
      label.textContent=field.key;container.append(label);
      ref.append(strengthControl('',field.value,()=>{}));container.append(ref);return container;
    }
    let input;
    if(field.kind==='prompt') { input=document.createElement('textarea');input.value=field.value; }
    else if(field.kind==='setting'){input=document.createElement('input');input.type='number';input.min='0';
      input.step=String(field.step||1);input.value=field.value;}
    else {input=document.createElement('input');input.type='text';input.value=field.value;}
    input.id=name;input.className='editor-input';
    label.htmlFor=name;container.append(label);
    if(field.kind==='setting'){
      // − / + like the ComfyUI widget; the box still takes any exact value.
      const step=Number(field.step)||1,stepper=el('div','stepper');
      const bump=(d,t)=>{const b=el('button','button secondary',t);b.type='button';b.setAttribute('aria-label',(d<0?'Decrease ':'Increase ')+(field.label||field.key));
        b.onclick=()=>{input.value=String(Math.max(0,Math.round(((Number(input.value)||0)+d*step)*1000)/1000));};return b;};
      stepper.append(bump(-1,'−'),input,bump(1,'+'));ref.append(stepper);container.append(ref);return container;
    }
    ref.append(input);container.append(ref);
    if(field.kind==='image'){
      // Like the Load Image node: show the current input picture under its filename.
      const thumb=el('img','input-thumb');thumb.alt='Current input image';thumb.loading='lazy';
      const refresh=()=>{thumb.hidden=!input.value;if(input.value)thumb.src=previewURL(input.value);};
      thumb.onerror=()=>{thumb.hidden=true;};input.addEventListener('change',refresh);refresh();
      const btn=document.createElement('button');btn.className='button secondary';btn.type='button';btn.textContent='🖼️ Select image';
      btn.style.marginTop='8px';
      btn.onclick=()=>openPicker({label:field.key,media:false,apply:info=>{input.value=info.filename;refresh();
        message('✅ Image copied into ComfyUI/input: '+info.filename);}});
      container.append(thumb,btn);
    }
    if(field.kind==='lora_model'){
      const list=document.createElement('datalist');list.id='loras-'+name;
      loraNames.forEach(n=>{const opt=document.createElement('option');opt.value=n;list.append(opt);});
      input.setAttribute('list',list.id);container.append(list);
    }
    if(field.kind==='prompt'){
      const btn=document.createElement('button');btn.className='button secondary';btn.type='button';btn.textContent='✨ Generate';
      btn.style.marginTop='8px';
      btn.onclick=()=>window.openPromptGenerator?window.openPromptGenerator(input,node.title+' · '+field.key)
        :message('The prompt generator did not load; refresh the page.',true);
      container.append(btn);
    }
    return container;
  }
  function renderFields(nodes){
    const target=get('parameter-nodes');target.replaceChildren();
    if(!nodes.length){const p=document.createElement('p');p.className='muted';
      p.textContent='No compatible LoRA/prompt/LoadImage literal fields found. Linked inputs and advanced custom widgets require support for those nodes.';target.append(p);}
    // Output settings (megapixels, video length) from any node, gathered in one card first.
    const settings=nodes.flatMap(node=>node.fields.filter(f=>f.kind==='setting').map(f=>[node,f]));
    if(settings.length){
      const card=el('div','nodecard');card.append(el('h3','','🎬 Output settings'),el('small','','Video size and length used by this run'));
      for(const [node,field] of settings)card.append(controlFor(node,field.label===node.title?field:{...field,label:field.label+' · '+node.title}));
      target.append(card);
    }
    for(const node of nodes){
      const fields=node.fields.filter(f=>f.kind!=='setting');
      if(!fields.length)continue;
      const card=document.createElement('div');card.className='nodecard';
      const h=document.createElement('h3');h.textContent=node.title;card.append(h);
      const hint=document.createElement('small');hint.textContent=node.class_type+' · Node '+node.id;card.append(hint);
      for(const field of fields) card.append(controlFor(node,field));
      target.append(card);
    }
    getJSON('/api/models/loras').then(names=>{loraNames=Array.isArray(names)?names:[];
      document.querySelectorAll('[list^="loras-"]').forEach(list=>{
        list.replaceChildren();loraNames.forEach(n=>{const opt=document.createElement('option');opt.value=n;list.append(opt);});
      });
    }).catch(()=>{});
  }
  // Fantastic H3 Media Loader: the same item list its node panel keeps in media_state.
  function parseMedia(text){try{const v=JSON.parse(text||'[]');return Array.isArray(v)?v.filter(i=>i&&typeof i==='object'):[];}catch(e){return [];}}
  const isOn=item=>item.enabled!==false;
  function audioCount(items){return items.filter(i=>isOn(i)&&(i.kind==='audio'||
    (i.kind==='video'&&i.has_audio&&(i.audio_mode||'paired')!=='off'))).length;}
  function mediaThumb(item){
    const box=el('div','media-thumb');
    if(item.kind==='picture'){const im=el('img');im.loading='lazy';im.alt='';im.src=previewURL(item.file);
      im.onerror=()=>box.replaceChildren('🖼️');box.append(im);}
    else if(item.kind==='video'){const v=el('video');v.muted=true;v.playsInline=true;v.preload='metadata';
      v.src=previewURL(item.file)+'#t=0.1';v.onerror=()=>box.replaceChildren('🎞️');box.append(v);}
    else box.textContent='🎵';
    return box;
  }
  function mediaEditor(ref,text){
    let items=parseMedia(text);
    const wrap=el('div','media-editor'),list=el('div','media-list'),summary=el('div','muted');
    const add=el('button','button secondary','＋ Add media');add.type='button';
    const commit=()=>{ref.dataset.value=JSON.stringify(items);ref.dataset.dirty='1';render();};
    function row(item,i){
      const box=el('div','media-item'+(isOn(item)?'':' off')),meta=el('div','media-meta');
      const facts=[item.kind];
      if(Number.isFinite(item.duration))facts.push(item.duration.toFixed(1)+' s');
      if(item.width&&item.height)facts.push(item.width+'×'+item.height);
      if(item.trim||item.crop||item.mirror||item.rotate)facts.push('edited on PC');
      meta.append(el('strong','',item.name||item.file),el('span','muted',facts.join(' · ')));
      if(item.kind==='video'&&item.has_audio){
        const sel=el('select');sel.setAttribute('aria-label','Soundtrack of '+(item.name||'video'));
        for(const [v,t] of [['paired','Soundtrack: paired'],['standalone','Soundtrack: standalone audio'],['off','Soundtrack: off']])sel.add(new Option(t,v));
        sel.value=item.audio_mode||'paired';
        sel.onchange=()=>{const prev=item.audio_mode;item.audio_mode=sel.value;
          if(sel.value!=='off'&&audioCount(items)>MEDIA_MAX.audio){item.audio_mode=prev;message('H3 takes 3 audio clips in total, including video soundtracks.',true);}
          commit();};
        meta.append(el('br'),sel);
      }
      const tools=el('div','media-tools'),btn=(t,title,fn)=>{const b=el('button','button secondary',t);b.type='button';b.title=title;b.setAttribute('aria-label',title);b.onclick=fn;tools.append(b);};
      btn(isOn(item)?'On':'Off',isOn(item)?'Switch off (kept in the list)':'Switch on',()=>{item.enabled=!isOn(item);commit();});
      if(i>0)btn('↑','Move up',()=>{[items[i-1],items[i]]=[items[i],items[i-1]];commit();});
      if(i<items.length-1)btn('↓','Move down',()=>{[items[i+1],items[i]]=[items[i],items[i+1]];commit();});
      btn('✕','Remove from loader',()=>{items.splice(i,1);commit();});
      box.append(mediaThumb(item),meta,tools);return box;
    }
    function render(){
      list.replaceChildren(...items.map(row));
      const count=k=>items.filter(i=>i.kind===k).length;
      summary.textContent=`${count('picture')}/${MEDIA_MAX.picture} pictures · ${count('video')}/${MEDIA_MAX.video} videos · ${audioCount(items)}/${MEDIA_MAX.audio} audio`+
        (items.length?'':' — the loader is empty');
    }
    add.onclick=()=>openPicker({label:'Media Loader',media:true,apply:info=>{
      const kind=info.kind;
      if((kind==='picture'||kind==='video')&&items.filter(i=>i.kind===kind).length>=MEDIA_MAX[kind])
        throw Error(`H3 takes ${MEDIA_MAX[kind]} ${kind}s; remove one first.`);
      if(kind==='audio'&&audioCount(items)>=MEDIA_MAX.audio)throw Error('H3 takes 3 audio clips in total, including video soundtracks.');
      const pairable=kind==='video'&&info.has_audio;
      items.push({kind,file:info.file,name:info.original||info.name,duration:info.duration??null,
        width:info.width??null,height:info.height??null,has_audio:!!info.has_audio,
        audio_mode:pairable&&audioCount(items)<MEDIA_MAX.audio?'paired':'off'});
      commit();message('✅ Added '+(info.original||info.name)+' to the Media Loader');
    }});
    render();wrap.append(summary,list,add);return wrap;
  }
  function changedValues(){
    const result=[];
    document.querySelectorAll('.node-edit').forEach(field=>{
      const kind=field.dataset.kind;
      if(kind==='media'||kind==='lora_stack'||kind==='lora_row'){
        if(field.dataset.dirty==='1')result.push({node:field.dataset.node,key:field.dataset.key,
          value:kind==='lora_row'?JSON.parse(field.dataset.value):field.dataset.value});
        return;
      }
      const numeric=kind==='number'||kind==='setting';
      const inp=field.querySelector(numeric?'input[type=number]':'input,textarea');const original=JSON.parse(field.dataset.original);
      const value=numeric?Number(inp.value):inp.value;
      if(numeric && !inp.value.trim())throw Error((kind==='setting'?'Setting':'Strength')+' value cannot be blank');
      if(value!==original)result.push({node:field.dataset.node,key:field.dataset.key,value});
    });
    return result;
  }
  async function action(url,payload){
    try{
      if(!selectedWorkflow)throw Error('Select a workflow first');
      const d=await postJSON(url,{source:selectedWorkflow,changes:changedValues(),...payload});
      // ComfyUI still queues a prompt when some outputs fail validation; say which it dropped.
      if(d.skipped&&d.skipped.length)message('⚠ Queued '+(d.prompt_id||'')+', but ComfyUI skipped these outputs:\n• '+d.skipped.join('\n• '),true);
      else message(url.endsWith('/run')?'✅ Added to ComfyUI queue: '+(d.prompt_id||'accepted'):
        '✅ Scheduled! Next execution: '+new Date(d.next_at*1000).toLocaleString());
      if(url.endsWith('schedule'))loadSchedules();
    }catch(e){message('⚠ '+e.message,true);}
  }
  get('run-now').onclick=()=>action('/api/run',{});
  get('create-schedule').onclick=()=>{
    const value=get('start-at').value;
    if(!value){message('Choose a future time to schedule.',true);return;}
    const date=new Date(value);
    if(!Number.isFinite(date.getTime())){message('Invalid date/time.',true);return;}
    action('/api/schedule',{when:date.toISOString(),repeat:get('repeat-mode').value});
  };
  function setDefaultTime(){const d=new Date(Date.now()+10*60*1000);
    // datetime-local is local, toISOString UTC; format manually.
    const pad=n=>String(n).padStart(2,'0');
    get('start-at').value=`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  setDefaultTime();
  async function loadSchedules(){
    try{const arr=await getJSON('/api/schedules'),outer=get('schedule-list');outer.replaceChildren();
      if(!arr.length){const p=document.createElement('p');p.className='muted';p.textContent='No scheduled jobs yet.';outer.append(p);}
      for(const item of arr){
        const box=document.createElement('div');box.className='scheduled-item';
        const strong=document.createElement('strong');strong.textContent=item.title+' · '+item.repeat;
        const meta=document.createElement('div');meta.className='muted';
        meta.textContent=(item.enabled?'Next: '+new Date(item.next_at*1000).toLocaleString():'No future run')
          +' · '+item.status+(item.last_prompt_id?' · '+item.last_prompt_id:'');
        const desc=document.createElement('div');desc.className='muted';desc.textContent=item.error||'';
        const del=document.createElement('button');del.className='button secondary';del.textContent='Delete schedule';
        del.onclick=async()=>{if(!confirm('Delete this scheduled entry?'))return;
          try{await postJSON('/api/schedule/delete',{id:item.id});loadSchedules();}
          catch(e){message(e.message,true);}};
        box.append(strong,meta,desc,del);outer.append(box);
      }
    }catch(e){get('schedule-list').textContent='Unable to load schedules: '+e.message;}
  }

  // imageTarget: {label, media, apply(info)} — a Load Image field or a Media Loader editor.
  function openPicker(target){imageTarget=target;get('asset-picker').hidden=false;
    get('asset-title').textContent=target.media?'Add media to the Media Loader':'Select image for '+target.label;
    get('phone-label').textContent=target.media?'Upload a picture, video or audio file from phone/device':'Upload from phone/device';
    get('phone-image').accept=target.media?MEDIA_ACCEPT:IMAGE_ACCEPT;
    loadPicker(true);
    get('asset-picker').scrollIntoView({behavior:'smooth',block:'start'});
  }
  // Picker sources: ComfyUI's Media Assets (Generated / Imported) or the configured PC folders.
  const picker={source:'generated',next:0,shown:0,loading:false};
  for(const b of get('picker-sources').querySelectorAll('button'))b.onclick=()=>{
    picker.source=b.dataset.source;
    for(const o of get('picker-sources').querySelectorAll('button'))o.setAttribute('aria-pressed',String(o===b));
    loadPicker(true);
  };
  let pickerTyping=null;
  get('picker-search').oninput=()=>{clearTimeout(pickerTyping);pickerTyping=setTimeout(()=>loadPicker(true),350);};
  get('picker-more').onclick=()=>loadPicker(false);
  async function loadPicker(reset){
    if(picker.loading||!imageTarget)return;picker.loading=true;
    const grid=get('picker-grid'),more=get('picker-more'),term=get('picker-search').value.trim();
    if(reset){picker.next=0;picker.shown=0;grid.replaceChildren(el('div','muted','Loading…'));}
    more.hidden=true;
    try{
      if(picker.source==='pc'){await loadPCAssets(term);return;}
      if(!window.ComfyAssets)throw Error('The assets script did not load; refresh the page.');
      const data=await window.ComfyAssets.fetchPage(picker.source,picker.next,term);
      if(reset)grid.replaceChildren();
      // Load Image takes pictures only; the Media Loader also takes videos and audio.
      const usable=data.items.filter(a=>imageTarget&&(imageTarget.media?a.media!=='other':a.media==='image'));
      for(const a of usable)grid.append(pickCard(a.name,window.ComfyAssets.thumb(a),window.ComfyAssets.facts(a),
        ()=>stageImage({source:'asset',type:a.type,file:a.file})));
      picker.next=data.next;picker.shown+=usable.length;more.hidden=!data.has_more;
      if(!picker.shown)grid.replaceChildren(el('div','muted',data.has_more?'Nothing usable on this page; tap Load more.':
        'No '+(imageTarget&&imageTarget.media?'media':'images')+' in '+picker.source+' assets.'));
    }catch(e){grid.replaceChildren(el('div','error-text',e.message));}
    finally{picker.loading=false;}
  }
  get('close-assets').onclick=()=>{get('asset-picker').hidden=true;imageTarget=null;};
  async function finishPick(info){
    const target=imageTarget;get('asset-picker').hidden=true;imageTarget=null;
    await target.apply(info);
  }
  async function stageImage(data){
    if(!imageTarget)throw Error('Choose an image input node first');
    const info=await postJSON('/api/stage-asset',imageTarget.media?{...data,target:'media'}:data);
    await finishPick(info);
  }
  // XHR rather than fetch so large phone videos show upload progress.
  function uploadMedia(file){
    return new Promise((resolve,reject)=>{
      const xhr=new XMLHttpRequest();xhr.open('POST','/api/upload-media');
      xhr.setRequestHeader('X-Monitor-Token',TOKEN);xhr.setRequestHeader('X-File-Name',encodeURIComponent(file.name));
      xhr.setRequestHeader('Content-Type','application/octet-stream');
      xhr.upload.onprogress=e=>{if(e.lengthComputable)message('Uploading '+file.name+' … '+Math.round(e.loaded*100/e.total)+'%');};
      xhr.onload=()=>{
        if(!(xhr.getResponseHeader('Content-Type')||'').includes('application/json'))
          return reject(Error('Authentication redirected. Reopen the dashboard; on Cloudflare, use named Access or Tailscale.'));
        let data={};try{data=JSON.parse(xhr.responseText);}catch(e){}
        if(xhr.status>=200&&xhr.status<300)resolve(data);else reject(Error(data.error||'HTTP '+xhr.status));
      };
      xhr.onerror=()=>reject(Error('Network error while uploading'));
      xhr.send(file);
    });
  }
  function pickCard(label,visual,detail,fn){
    const btn=el('button','asset-card');btn.type='button';btn.title=label;
    btn.append(visual,el('span','',label));if(detail)btn.append(el('small','',detail));
    btn.onclick=async()=>{btn.disabled=true;message('Preparing '+label+' …');
      try{await fn();}catch(e){message('Selection failed: '+e.message,true);}finally{btn.disabled=false;}};
    return btn;
  }
  async function loadPCAssets(term){
    const target=get('picker-grid');
    const files=(await getJSON('/api/assets')).filter(f=>!term||f.path.toLowerCase().includes(term.toLowerCase()));
    target.replaceChildren();
    if(!files.length)target.append(el('div','muted',term?'No matching PC images.':'No PC images. Set asset_dirs in config.json.'));
    for(const f of files.slice(0,150)){
      const im=el('img');im.loading='lazy';im.alt=f.path;im.src='/api/local-image?'+new URLSearchParams({root:f.root,path:f.path});
      target.append(pickCard(f.path,im,'',()=>stageImage({source:'pc',root:f.root,path:f.path})));
    }
  }
  get('phone-image').onchange=async e=>{
    const f=e.target.files?.[0];if(!f)return;
    if(!imageTarget){message('Select an image input first.',true);e.target.value='';return;}
    const media=imageTarget.media,limit=media?1024:40;
    if(f.size>limit*1024*1024){message((media?'File':'Image')+' exceeds the '+(media?'1 GiB':'40 MiB')+' limit.',true);e.target.value='';return;}
    message('Uploading '+f.name+' …');
    try{
      const uploaded=media?await uploadMedia(f):await request('/api/upload-image',{method:'POST',headers:{
        'Content-Type':'application/octet-stream','X-File-Name':encodeURIComponent(f.name)},body:f});
      await finishPick(uploaded);
    }catch(ex){message('Upload failed: '+ex.message,true);}finally{e.target.value='';}
  };
  // A direct bookmark to #plan executes navigation before this module loads.
  if (window.location.hash==='#plan') {loadWorkflows();loadSchedules();}
  // The shared monitor state still refreshes queue/history every five seconds.
})();
