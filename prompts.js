// Comfy Mobile v6: Ollama chat ("Prompts" tab) and the ✨ Generate prompt rewriter.
(() => {
  'use strict';
  const get = (id) => document.getElementById(id);
  const TOKEN = document.querySelector('meta[name="monitor-token"]').content;
  const MAX_IMAGE = 10 * 1024 * 1024, MAX_TEXT_FILE = 512 * 1024;
  let models = [], chatId = null, pending = {files: [], images: []}, controller = null, loaded = false;

  async function getJSON(path) {
    const r = await fetch(path, {credentials:'same-origin', redirect:'manual', cache:'no-store'});
    const type = r.headers.get('Content-Type') || '';
    if (r.type==='opaqueredirect'||r.status===0||!type.includes('application/json'))
      throw Error('Authentication redirected. Reopen the dashboard.');
    const data = await r.json();
    if (!r.ok) throw Error(data.error || `HTTP ${r.status}`);
    return data;
  }
  const postJSON = (path, data) => fetch(path, {method:'POST', credentials:'same-origin', cache:'no-store',
    headers:{'Content-Type':'application/json', 'X-Monitor-Token':TOKEN}, body:JSON.stringify(data)})
    .then(async r => { const d = await r.json(); if (!r.ok) throw Error(d.error || `HTTP ${r.status}`); return d; });

  // POST and read Ollama's relayed NDJSON stream, calling onChunk for each line.
  async function streamPost(path, data, onChunk, signal) {
    const r = await fetch(path, {method:'POST', credentials:'same-origin', redirect:'manual', cache:'no-store', signal,
      headers:{'Content-Type':'application/json', 'X-Monitor-Token':TOKEN}, body:JSON.stringify(data)});
    if (r.type==='opaqueredirect'||r.status===0) throw Error('Authentication redirected. Reopen the dashboard.');
    if (!(r.headers.get('Content-Type') || '').includes('ndjson')) {
      const d = await r.json().catch(() => ({}));
      throw Error(d.error || `HTTP ${r.status}`);
    }
    const reader = r.body.getReader(), decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const {value, done} = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, {stream:true});
      let at;
      while ((at = buffer.indexOf('\n')) >= 0) {
        const line = buffer.slice(0, at); buffer = buffer.slice(at + 1);
        if (!line.trim()) continue;
        const chunk = JSON.parse(line);
        if (chunk.error) throw Error(chunk.error);
        onChunk(chunk);
      }
    }
  }
  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
  const imageSrc = b64 => 'data:image/' + (b64.startsWith('/9j/') ? 'jpeg' : b64.startsWith('R0lG') ? 'gif' :
    b64.startsWith('UklG') ? 'webp' : 'png') + ';base64,' + b64;
  function copyButton(getText) {
    const b = el('button', 'msg-copy', 'Copy'); b.type = 'button';
    b.onclick = async () => {
      try { await navigator.clipboard.writeText(getText()); }
      catch { const t = el('textarea'); t.value = getText(); document.body.append(t); t.select(); document.execCommand('copy'); t.remove(); }
      b.textContent = 'Copied'; setTimeout(() => b.textContent = 'Copy', 1200);
    };
    return b;
  }

  // ---------- Chat tab ----------
  function status(text, bad=false) { const s = get('chat-status'); s.textContent = text; s.classList.toggle('error-text', bad); }
  const currentModel = () => models.find(m => m.name === get('chat-model').value);

  async function loadModels() {
    const data = await getJSON('/api/ollama/models');
    models = data.models;
    const sel = get('chat-model'), keep = sel.value || data.default;
    sel.replaceChildren(...models.map(m => new Option(m.name + (m.vision ? ' 👁' : ''), m.name)));
    if (!models.some(m => m.name === data.default)) sel.prepend(new Option(data.default + ' (not installed)', data.default));
    sel.value = models.some(m => m.name === keep) ? keep : data.default;
  }
  async function loadChats() {
    const list = get('chat-list'), chats = await getJSON('/api/chats');
    list.replaceChildren();
    if (!chats.length) list.append(el('div', 'muted', 'No conversations yet.'));
    for (const c of chats) {
      const row = el('div', 'chat-item' + (c.id === chatId ? ' active' : ''));
      const open = el('button', 'chat-item-open'); open.type = 'button';
      open.append(el('span', '', c.title), el('small', '', new Date(c.updated * 1000).toLocaleString([], {dateStyle:'short', timeStyle:'short'})));
      open.onclick = () => openChat(c.id);
      const del = el('button', 'chat-item-del', '✕'); del.type = 'button'; del.title = 'Delete conversation';
      del.onclick = async () => {
        if (!confirm('Delete this conversation?')) return;
        try { await postJSON('/api/chat/delete', {id:c.id}); if (c.id === chatId) newChat(); loadChats(); }
        catch (e) { status(e.message, true); }
      };
      row.append(open, del); list.append(row);
    }
  }
  function messageView(msg) {
    const box = el('div', 'msg ' + msg.role), bubble = el('div', 'msg-bubble');
    for (const b64 of msg.images || []) { const img = el('img', 'msg-image'); img.src = imageSrc(b64); img.alt = 'Attached image'; bubble.append(img); }
    for (const f of msg.files || []) bubble.append(el('div', 'msg-file', '📄 ' + f.name));
    const text = el('div', 'msg-text', msg.content); bubble.append(text); box.append(bubble);
    if (msg.role === 'assistant') box.append(copyButton(() => text.textContent));
    return {box, text};
  }
  function renderLog(messages) {
    const log = get('chat-log'); log.replaceChildren();
    if (!messages.length) log.append(el('div', 'chat-empty', 'Ask a question, or attach an image or text file with 📎.'));
    for (const m of messages) log.append(messageView(m).box);
    log.scrollTop = log.scrollHeight;
  }
  async function openChat(id) {
    try {
      const chat = await getJSON('/api/chat?id=' + encodeURIComponent(id));
      chatId = chat.id; renderLog(chat.messages); status('');
      if (models.some(m => m.name === chat.model)) get('chat-model').value = chat.model;
      showHistory(false); loadChats();
    } catch (e) { status(e.message, true); }
  }
  function newChat() { chatId = null; renderLog([]); status(''); showHistory(false); loadChats().catch(() => {}); get('chat-input').focus(); }
  function showHistory(show) {
    get('chat-sidebar').classList.toggle('open', show);
    get('chat-history-toggle').setAttribute('aria-expanded', String(show));
  }

  function renderAttachments() {
    const box = get('chat-attachments'); box.replaceChildren();
    const chip = (label, remove, src) => {
      const c = el('span', 'chat-chip');
      if (src) { const i = el('img'); i.src = src; i.alt = ''; c.append(i); }
      c.append(el('span', '', label));
      const x = el('button', '', '✕'); x.type = 'button'; x.title = 'Remove'; x.onclick = () => { remove(); renderAttachments(); };
      c.append(x); return c;
    };
    pending.images.forEach((img, i) => box.append(chip(img.name, () => pending.images.splice(i, 1), img.url)));
    pending.files.forEach((f, i) => box.append(chip('📄 ' + f.name, () => pending.files.splice(i, 1))));
    const model = currentModel();
    if (pending.images.length && model && !model.vision) status(model.name + " can't see images. Pick a 👁 model to ask about them.", true);
  }
  async function addFiles(fileList) {
    for (const f of fileList) {
      try {
        if (f.type.startsWith('image/')) {
          if (f.size > MAX_IMAGE) throw Error(f.name + ' is larger than 10 MiB');
          if (pending.images.length >= 4) throw Error('At most 4 images per message');
          const url = await new Promise((ok, bad) => { const r = new FileReader(); r.onload = () => ok(r.result); r.onerror = () => bad(r.error); r.readAsDataURL(f); });
          pending.images.push({name:f.name, url, data:url.slice(url.indexOf(',') + 1)});
        } else {
          if (f.size > MAX_TEXT_FILE) throw Error(f.name + ' is larger than 512 KiB');
          const text = await f.text();
          if (text.includes('\u0000')) throw Error(f.name + ' is not a text file; only images and text files are supported');
          pending.files.push({name:f.name, text});
        }
      } catch (e) { status(e.message, true); }
    }
    renderAttachments();
  }
  function setBusy(busy) {
    get('chat-send').textContent = busy ? '■ Stop' : 'Send';
    get('chat-send').classList.toggle('secondary', busy);
  }
  async function send() {
    if (controller) { controller.abort(); return; }
    const input = get('chat-input'), content = input.value;
    if (!content.trim() && !pending.files.length && !pending.images.length) return;
    const message = {role:'user', content, files:pending.files, images:pending.images.map(i => i.data)};
    const log = get('chat-log');
    log.querySelector('.chat-empty')?.remove();
    log.append(messageView(message).box);
    const reply = messageView({role:'assistant', content:''});
    reply.box.classList.add('streaming'); log.append(reply.box); log.scrollTop = log.scrollHeight;
    input.value = ''; autoGrow(); pending = {files: [], images: []}; renderAttachments(); status('');
    controller = new AbortController(); setBusy(true);
    try {
      await streamPost('/api/chat', {id:chatId, model:get('chat-model').value, content, files:message.files, images:message.images}, c => {
        if (c.id) chatId = c.id;
        if (c.delta) { reply.text.textContent += c.delta; log.scrollTop = log.scrollHeight; }
        if (c.done && c.reason === 'length') status('Reply stopped at the model\'s length limit (num_predict).');
      }, controller.signal);
    } catch (e) {
      if (e.name !== 'AbortError') { status(e.message, true); if (!reply.text.textContent) reply.box.remove(); }
    } finally {
      reply.box.classList.remove('streaming'); controller = null; setBusy(false); loadChats().catch(() => {});
    }
  }
  function autoGrow() { const t = get('chat-input'); t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight, 200) + 'px'; }

  get('chat-form').onsubmit = e => { e.preventDefault(); send(); };
  get('chat-input').addEventListener('input', autoGrow);
  get('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !matchMedia('(pointer:coarse)').matches) { e.preventDefault(); send(); }
  });
  get('chat-input').addEventListener('paste', e => { const files = [...(e.clipboardData?.files || [])]; if (files.length) { e.preventDefault(); addFiles(files); } });
  get('chat-file').onchange = async e => { await addFiles(e.target.files); e.target.value = ''; };
  get('chat-model').onchange = renderAttachments;
  get('chat-new').onclick = newChat;
  get('chat-history-toggle').onclick = () => showHistory(!get('chat-sidebar').classList.contains('open'));

  function init() {
    if (loaded) return;
    loaded = true;
    loadModels().catch(e => status(e.message, true));
    loadChats().catch(e => { get('chat-list').textContent = e.message; });
  }
  window.addEventListener('comfy:tab', e => { if (e.detail.tab === 'prompts') init(); });
  if (location.hash === '#prompts') init();

  // ---------- ✨ Generate: rewrite a workflow prompt field with Ollama ----------
  const dialog = get('prompt-gen');
  let genTarget = null, genBase = '', genController = null;
  function genStatus(text, bad=false) { const s = get('gen-status'); s.textContent = text; s.classList.toggle('error-text', bad); }
  function showBase(text) { genBase = text; get('gen-current').textContent = text || '(empty prompt)'; }
  window.openPromptGenerator = (textarea, label) => {
    genTarget = textarea; showBase(textarea.value);
    get('gen-label').textContent = 'Current prompt · ' + label;
    get('gen-request').value = ''; get('gen-result-wrap').hidden = true; genStatus('');
    dialog.showModal(); get('gen-request').focus();
  };
  async function generate() {
    if (genController) { genController.abort(); return; }
    const changes = get('gen-request').value;
    if (!changes.trim()) { genStatus('Describe what should change first.', true); return; }
    const result = get('gen-result'); result.value = ''; result.readOnly = true;
    get('gen-result-wrap').hidden = false;
    genController = new AbortController();
    get('gen-run').textContent = '■ Stop'; genStatus('Generating with Ollama…');
    try {
      await streamPost('/api/prompt/rewrite', {prompt:genBase, changes}, c => { if (c.delta) result.value += c.delta; }, genController.signal);
      result.value = result.value.trim(); genStatus('Done. Edit it below if needed, then use it.');
    } catch (e) { genStatus(e.name === 'AbortError' ? 'Stopped.' : e.message, e.name !== 'AbortError'); }
    finally { result.readOnly = false; genController = null; get('gen-run').textContent = '✨ Generate'; }
  }
  get('gen-run').onclick = generate;
  get('gen-use').onclick = () => {
    if (genTarget) { genTarget.value = get('gen-result').value; genTarget.dispatchEvent(new Event('input', {bubbles:true})); }
    dialog.close();
  };
  get('gen-again').onclick = () => {
    showBase(get('gen-result').value); get('gen-request').value = '';
    get('gen-result-wrap').hidden = true; genStatus('Describe the next change to this result.'); get('gen-request').focus();
  };
  get('gen-close').onclick = () => dialog.close();
  dialog.addEventListener('close', () => { genController?.abort(); genTarget = null; });
})();
