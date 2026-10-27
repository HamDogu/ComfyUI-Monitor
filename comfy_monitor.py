#!/usr/bin/env python3
"""Comfy Mobile v6: responsive home, collapsible history, monitoring and workflow scheduling.

Uses Python 3.9+ standard library. Requires monitor_core.py and schedule.js.
Bind localhost only; publish via Tailscale Serve or Cloudflare Access.
"""
import copy
import datetime as dt
import hashlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import monitor_core as core
import media_browser as media
import ollama_chat as ollama
import workflow_convert as wc
from live_monitor import LiveMonitor

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / 'config.json'
CONFIG = json.loads(CONFIG_FILE.read_text(encoding='utf-8')) if CONFIG_FILE.exists() else {}
WORKFLOW_ROOTS = [Path(p).expanduser().resolve() for p in CONFIG.get('workflow_dirs', []) if p]
ASSET_ROOTS = [Path(p).expanduser().resolve() for p in CONFIG.get('asset_dirs', []) if p]
OUTPUT_ROOTS = [Path(p).expanduser().resolve() for p in CONFIG.get('output_dirs', []) if p]
# UI/canvas workflows are converted to API format and saved here (mirroring their relative path).
CONVERTED_DIR = (Path(CONFIG['converted_workflow_dir']).expanduser().resolve()
                 if CONFIG.get('converted_workflow_dir')
                 else (WORKFLOW_ROOTS[0] / 'converted' if WORKFLOW_ROOTS else None))
if CONVERTED_DIR and not any(CONVERTED_DIR.is_relative_to(root) for root in WORKFLOW_ROOTS):
    WORKFLOW_ROOTS.append(CONVERTED_DIR)
AUTO_CONVERT = bool(CONFIG.get('auto_convert_workflows', True))
ollama.configure(CONFIG)
MAX_CHAT_BODY = 64 * 1024 * 1024
LIVE = LiveMonitor()
SNAPSHOTS_FILE = BASE / 'snapshots.json'
SCHEDULES_FILE = BASE / 'schedules.json'
CONVERSIONS_FILE = BASE / 'conversions.json'
WRITE_TOKEN = secrets.token_hex(32)
LOCK = threading.RLock()
MAX_JSON = 3 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024
MAX_MEDIA = 1024 * 1024 * 1024
IMAGE_TYPES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
               '.webp': 'image/webp', '.gif': 'image/gif', '.bmp': 'image/bmp'}
# Extensions the Fantastic H3 Media Loader accepts (its web_api.kind_for).
MEDIA_KINDS = {**{ext: 'picture' for ext in ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff')},
               **{ext: 'video' for ext in ('.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.mpg', '.mpeg')},
               **{ext: 'audio' for ext in ('.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac', '.opus')}}
MEDIA_LOADER = 'MiniMaxH3MediaLoader'
LORA_STACK = 'PixaromaLoraLoader'
# Constant nodes worth exposing as output settings, by title ("Video Length (seconds)").
SETTING_TITLE = re.compile(r'length|duration|seconds|megapixel', re.I)
# ComfyUI's Media Assets panel: Generated = output-tagged assets, Imported = input-tagged.
ASSET_TAGS = {'generated': 'output', 'imported': 'input'}
MEDIA_LIMITS = {'picture': 9, 'video': 3}
STAGED = set()
STAGED_MEDIA = set()
OBJECT_INFO = {'data': None, 'at': 0.0}
FORMAT_CACHE = {}


def load_data(path, fallback):
    try:
        result = json.loads(path.read_text(encoding='utf-8'))
        return result if isinstance(result, type(fallback)) else copy.deepcopy(fallback)
    except (OSError, ValueError):
        return copy.deepcopy(fallback)


def save_data(path, data):
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


SNAPSHOTS = load_data(SNAPSHOTS_FILE, {})
SCHEDULES = load_data(SCHEDULES_FILE, {})
CONVERSIONS = load_data(CONVERSIONS_FILE, {})


def http_json(route, method='GET', data=None):
    if method == 'GET':
        return core.comfy_get(route)
    body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(core.COMFY_URL + route, data=body,
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        error_body = exc.read(16384).decode('utf-8', 'replace')
        raise ValueError(f'ComfyUI returned {exc.code}: {error_body[:1200]}') from exc


def root_file(roots, root_idx, relative):
    if not isinstance(root_idx, int) or root_idx < 0 or root_idx >= len(roots):
        raise ValueError('Invalid folder')
    if not isinstance(relative, str) or not relative or '\\' in relative or '\x00' in relative:
        raise ValueError('Invalid file path')
    parts = relative.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError('Relative paths cannot leave their folder')
    root = roots[root_idx]
    file = root.joinpath(*parts).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError('File unavailable or outside configured directory')
    return file


def is_converted(file):
    return CONVERTED_DIR is not None and file.resolve().is_relative_to(CONVERTED_DIR)


def workflow_format(file):
    """'ui', 'api' or 'other', cached on mtime/size so listings stay cheap."""
    try:
        stat = file.stat()
    except OSError:
        return 'other'
    key = str(file)
    cached = FORMAT_CACHE.get(key)
    if cached and cached[:2] == (stat.st_mtime, stat.st_size):
        return cached[2]
    kind = 'other'
    if stat.st_size <= MAX_JSON:
        try:
            raw = json.loads(file.read_text(encoding='utf-8'))
            if wc.is_ui_workflow(raw):
                kind = 'ui'
            else:
                parse_prompt(raw)
                kind = 'api'
        except (OSError, ValueError):
            pass
    FORMAT_CACHE[key] = (stat.st_mtime, stat.st_size, kind)
    return kind


def workflow_files():
    """(root_idx, relative, file) for every JSON in the workflow folders."""
    for root_idx, root in enumerate(WORKFLOW_ROOTS):
        if not root.is_dir():
            continue
        for file in list(root.rglob('*.json'))[:500]:
            if file.is_symlink() or not file.is_file():
                continue
            try:
                if not file.resolve().is_relative_to(root):
                    continue
                yield root_idx, file.relative_to(root).as_posix(), file
            except (ValueError, OSError):
                pass


def workflow_file_list():
    found = []
    for root_idx, relative, file in workflow_files():
        kind = workflow_format(file)
        if kind == 'other':
            continue
        group = 'converted' if is_converted(file) else kind
        found.append({'id': f'disk:{root_idx}:{relative}', 'title': relative, 'format': kind,
                      'group': group, 'source': 'PC workflow directory'})
    return found


def object_info(max_age=600):
    if OBJECT_INFO['data'] is None or time.time() - OBJECT_INFO['at'] > max_age:
        data = core.comfy_get('/object_info')
        if not isinstance(data, dict) or not data:
            raise ValueError('ComfyUI returned no node definitions')
        OBJECT_INFO.update(data=data, at=time.time())
    return OBJECT_INFO['data']


def converted_path(root_idx, relative):
    if CONVERTED_DIR is None:
        raise ValueError('No workflow_dirs configured, so there is nowhere to save converted workflows')
    root = WORKFLOW_ROOTS[root_idx]
    base = CONVERTED_DIR if root_idx == 0 else CONVERTED_DIR / (re.sub(r'[^\w.-]', '_', root.name) or f'root{root_idx}')
    target = base.joinpath(*relative.split('/')).resolve()
    if not target.is_relative_to(CONVERTED_DIR):
        raise ValueError('Converted path would leave the converted folder')
    return target


def converted_id(target):
    for root_idx, root in enumerate(WORKFLOW_ROOTS):
        if target.is_relative_to(root):
            return f'disk:{root_idx}:{target.relative_to(root).as_posix()}'
    return None


def ensure_converted(root_idx, relative, file, force=False):
    """Convert a UI workflow once (or again if the source changed) and save the API copy."""
    if is_converted(file):
        raise ValueError('Files in the converted folder are already API format')
    target = converted_path(root_idx, relative)
    key = str(target)
    with LOCK:
        record = copy.deepcopy(CONVERSIONS.get(key))
    fresh = (not force and record and target.is_file() and
             target.stat().st_mtime >= file.stat().st_mtime and record.get('source') == str(file))
    if fresh:
        return json.loads(target.read_text(encoding='utf-8')), record
    if file.stat().st_size > MAX_JSON:
        raise ValueError('Workflow file exceeds 3 MB')
    raw = json.loads(file.read_text(encoding='utf-8'))
    prompt, warnings = wc.convert(raw, object_info(0 if force else 600))
    target.parent.mkdir(parents=True, exist_ok=True)
    save_data(target, prompt)
    record = {'source': str(file), 'target': str(target), 'warnings': warnings,
              'converted_at': time.time(), 'nodes': len(prompt)}
    with LOCK:
        CONVERSIONS[key] = record
        save_data(CONVERSIONS_FILE, CONVERSIONS)
    return prompt, record


def conversion_summary(record):
    target = Path(record['target'])
    shown = target.relative_to(CONVERTED_DIR.parent).as_posix() if CONVERTED_DIR else target.name
    return {'path': shown, 'id': converted_id(target), 'warnings': record.get('warnings', []),
            'converted_at': record.get('converted_at'), 'nodes': record.get('nodes')}


def auto_convert_loop():
    """Convert UI workflows as they are added to (or changed in) the workflow folders."""
    while True:
        try:
            for root_idx, relative, file in list(workflow_files()):
                if is_converted(file) or workflow_format(file) != 'ui':
                    continue
                failed = CONVERSIONS.get(f'error:{file}')
                if failed and failed.get('mtime') == file.stat().st_mtime:
                    continue  # Same file already failed; retry only after it changes.
                try:
                    ensure_converted(root_idx, relative, file)
                except (urllib.error.URLError, TimeoutError, ConnectionError):
                    break  # ComfyUI offline: node definitions unavailable, retry later
                except (ValueError, OSError) as exc:
                    with LOCK:
                        CONVERSIONS[f'error:{file}'] = {'source': str(file), 'error': str(exc)[:500],
                                                        'mtime': file.stat().st_mtime,
                                                        'converted_at': time.time()}
                        save_data(CONVERSIONS_FILE, CONVERSIONS)
        except Exception as exc:  # Keep the thread alive whatever a folder contains.
            print('Auto-convert error:', exc)
        time.sleep(30)


def parse_prompt(raw):
    """ComfyUI queue API format only; UI canvas JSON must first be exported/bridged."""
    if not isinstance(raw, dict):
        raise ValueError('Workflow must be a JSON object')
    if isinstance(raw.get('prompt'), dict):
        raw = raw['prompt']
    elif isinstance(raw.get('output'), dict) and not raw.get('class_type'):
        raw = raw['output']
    if not raw or not all(isinstance(node, dict) and
                          isinstance(node.get('class_type'), str) and
                          isinstance(node.get('inputs'), dict) for node in raw.values()):
        raise ValueError('This is not an executable API-format workflow, and it is not '
                         'ComfyUI UI/canvas JSON that could be converted either.')
    if len(raw) > 1500:
        raise ValueError('Workflow contains too many nodes')
    return copy.deepcopy(raw)


def fill_frontend_links(prompt, workflow):
    """Add the links graphToPrompt() leaves out of a bridge snapshot, in place.

    cg-use-everywhere only injects its broadcasts (VAE, SAMPLER, SIGMAS...) as the
    frontend queues, so a snapshot lacks them and ComfyUI silently skips every output
    that needs one. The converter resolves them from the UI graph. Returns the count added.
    """
    converted, _ = wc.convert(workflow, object_info())
    added = 0
    for node_id, node in prompt.items():
        source = converted.get(node_id)
        if not source or source.get('class_type') != node.get('class_type'):
            continue
        for name, value in source['inputs'].items():
            if (name not in node['inputs'] and isinstance(value, list) and wc.Converter.is_link(value)
                    and value[0] in prompt):
                node['inputs'][name] = value
                added += 1
    return added


def node_error_lines(result, prompt):
    """Readable lines for ComfyUI's node_errors: outputs it dropped while still queueing the rest."""
    lines = []
    for node_id, info in ((result or {}).get('node_errors') or {}).items():
        if not isinstance(info, dict):
            continue
        title = ((prompt.get(node_id) or {}).get('_meta') or {}).get('title') or info.get('class_type') or node_id
        reasons = '; '.join(': '.join(str(e.get(k)) for k in ('message', 'details') if e.get(k))
                            for e in info.get('errors') or [] if isinstance(e, dict))
        lines.append(f'{title} (node {node_id}): {reasons or "invalid"}')
    return lines


def disk_source(source_id):
    try:
        _, idx, relative = source_id.split(':', 2)
        return int(idx), relative, root_file(WORKFLOW_ROOTS, int(idx), relative)
    except (TypeError, ValueError) as exc:
        raise ValueError('Invalid workflow selection: ' + str(exc)) from exc


def get_source(source_id, info=None):
    """Return (api_prompt, title); UI workflows are converted (and cached on disk) first.

    `info`, when given, receives the conversion summary for the caller to show.
    """
    if source_id.startswith('open:'):
        with LOCK:
            snap = copy.deepcopy(SNAPSHOTS.get(source_id))
        if not snap:
            raise ValueError('Open workflow snapshot not found; switch to its tab on your PC')
        return parse_prompt(snap['prompt']), snap.get('title', 'Open workflow')
    if source_id.startswith('disk:'):
        try:
            idx, relative, file = disk_source(source_id)
            if file.stat().st_size > MAX_JSON:
                raise ValueError('Workflow file exceeds 3 MB')
            raw = json.loads(file.read_text(encoding='utf-8'))
            if wc.is_ui_workflow(raw):
                prompt, record = ensure_converted(idx, relative, file)
                if info is not None:
                    info.update(conversion_summary(record))
                return parse_prompt(prompt), relative
            return parse_prompt(raw), relative
        except urllib.error.URLError as exc:
            raise ValueError('ComfyUI must be running to convert this UI workflow: ' + str(exc)) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError('Could not read workflow: ' + str(exc)) from exc
    raise ValueError('Invalid workflow selection')


def node_editors(prompt):
    """Select plain-text prompts, LoRA values/models, output settings and LoadImage filenames."""
    out = []
    for node_id, node in prompt.items():
        cls = node['class_type']
        inputs = node['inputs']
        title = (node.get('_meta') or {}).get('title') or cls
        if not isinstance(title, str):
            title = cls
        fields = []
        lora = 'lora' in cls.lower()
        image_loader = ('loadimage' in cls.lower() or cls.lower() in ('loadimagemask',))
        constant = cls.startswith('Primitive') or cls.endswith('Constant')
        for key, value in inputs.items():
            if lora and isinstance(value, dict) and isinstance(value.get('lora'), str) and 'on' in value:
                fields.append({'key': key, 'value': value, 'kind': 'lora_row'})  # rgthree Power Lora Loader
                continue
            if isinstance(value, (dict, list)) or isinstance(value, bool):
                continue  # Linked inputs, not literal widgets
            lower = key.lower()
            kind = None
            if cls == MEDIA_LOADER and key == 'media_state' and isinstance(value, str):
                kind = 'media'
            elif cls == LORA_STACK and key == 'LoraLoaderState' and isinstance(value, str):
                kind = 'lora_stack'
            elif isinstance(value, (int, float)) and (
                    'megapixel' in lower or (constant and lower == 'value' and SETTING_TITLE.search(title))):
                label = 'Megapixels' if 'megapixel' in lower else title
                # JSON saves PrimitiveFloat's 18.0 as 18, so the class decides integer-ness.
                integer = 'megapixel' not in lower and 'int' in cls.lower() and 'float' not in cls.lower()
                fields.append({'key': key, 'value': value, 'kind': 'setting', 'label': label[:80], 'integer': integer,
                               'step': 0.05 if 'megapixel' in lower else (1 if integer else 0.5)})
                continue
            elif image_loader and lower in ('image', 'image_path') and isinstance(value, str):
                kind = 'image'
            elif lora and isinstance(value, (int, float, str)) and (
                    'lora' in lower or 'strength' in lower or 'weight' in lower):
                kind = 'number' if isinstance(value, (int, float)) else 'lora_model'
            elif isinstance(value, str) and (
                    lower in ('text', 'text_g', 'text_l', 'prompt', 'positive_prompt',
                              'negative_prompt', 'positive_text', 'negative_text')
                    or lower.endswith('_prompt') or lower.startswith('prompt_')
                    or (lower in ('positive', 'negative') and 'prompt' in cls.lower())):
                kind = 'prompt'
            if kind:
                fields.append({'key': key, 'value': value, 'kind': kind})
        if fields:
            out.append({'id': str(node_id), 'class_type': cls,
                        'title': title[:120], 'fields': fields})
    return out


def apply_overrides(prompt, changes):
    descriptors = {(n['id'], f['key']): f for n in node_editors(prompt) for f in n['fields']}
    if not isinstance(changes, list) or len(changes) > 1000:
        raise ValueError('Invalid list of changes')
    for edit in changes:
        if not isinstance(edit, dict):
            raise ValueError('Invalid edit')
        node_id, key, value = str(edit.get('node')), edit.get('key'), edit.get('value')
        field = descriptors.get((node_id, key))
        if field is None:
            raise ValueError(f'{node_id}/{key} is not an editable LoRA, prompt, image or media widget')
        if field['kind'] == 'media':
            value = media_state(value, field['value'])
        elif field['kind'] == 'number':
            value = strength(value)
        elif field['kind'] == 'lora_stack':
            value = lora_stack(value, field['value'])
        elif field['kind'] == 'lora_row':
            value = lora_row(value, field['value'])
        elif field['kind'] == 'setting':
            value = number(value, 0.0, 100000.0, field['label'])
            if field['integer']:
                value = int(round(value))
        else:
            if not isinstance(value, str) or len(value) > (20000 if field['kind'] == 'prompt' else 500):
                raise ValueError('Invalid text/image/LoRA field')
            if field['kind'] == 'image' and value != field['value']:
                with LOCK:
                    if value not in STAGED:
                        raise ValueError('Select an image using the asset picker before running')
        prompt[node_id]['inputs'][key] = value
    return prompt


def number(value, low, high, label):
    if isinstance(value, bool):
        raise ValueError(f'Expected a number for {label}')
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f'Expected a number for {label}') from None
    if not low <= value <= high:
        raise ValueError(f'{label} must be between {low:g} and {high:g}')
    return value


def strength(value):
    return number(value, -100.0, 100.0, 'LoRA strength')


def lora_stack(value, original):
    """Apply on/off and strengths to a Pixaroma LoRA Loader state, keeping everything else."""
    try:
        state, edited = json.loads(original), json.loads(value)
    except (TypeError, ValueError):
        raise ValueError('Invalid LoRA list') from None
    entries = state.get('loras') if isinstance(state, dict) else None
    rows = edited.get('loras') if isinstance(edited, dict) else None
    if not isinstance(entries, list) or not isinstance(rows, list) or len(rows) != len(entries):
        raise ValueError('The LoRA list no longer matches the workflow; reload it')
    for entry, row in zip(entries, rows):
        if not isinstance(entry, dict) or not isinstance(row, dict) or row.get('name') != entry.get('name'):
            raise ValueError('The LoRA list no longer matches the workflow; reload it')
        entry['on'] = bool(row.get('on'))
        entry['sm'] = strength(row.get('sm'))
        entry['sc'] = strength(row.get('sc', row.get('sm')))
    return json.dumps(state, separators=(',', ':'), ensure_ascii=False)


def lora_row(value, original):
    """Apply on/off and strengths to one rgthree Power Lora Loader row."""
    if not isinstance(value, dict) or value.get('lora') != original.get('lora'):
        raise ValueError('The LoRA row no longer matches the workflow; reload it')
    row = dict(original, on=bool(value.get('on')))
    for key in ('strength', 'strengthTwo'):
        if isinstance(original.get(key), (int, float)) and not isinstance(original.get(key), bool):
            row[key] = strength(value.get(key))
    return row


def media_items(text):
    try:
        items = json.loads(text or '[]')
    except (TypeError, ValueError):
        return []
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def media_state(value, original):
    """Validate an edited Fantastic H3 Media Loader item list (its media_state JSON)."""
    if not isinstance(value, str) or len(value) > 200000:
        raise ValueError('Invalid Media Loader state')
    try:
        items = json.loads(value)
    except ValueError:
        raise ValueError('Media Loader state is not valid JSON') from None
    if not isinstance(items, list) or len(items) > 40:
        raise ValueError('Media Loader state must be a list of at most 40 items')
    known = {i.get('file') for i in media_items(original)}
    counts = {}
    for item in items:
        if not isinstance(item, dict) or item.get('kind') not in ('picture', 'video', 'audio'):
            raise ValueError('Each Media Loader item needs a picture, video or audio kind')
        file = item.get('file')
        if not isinstance(file, str) or not file or len(file) > 500:
            raise ValueError('Each Media Loader item needs a file')
        with LOCK:
            if file not in known and file not in STAGED_MEDIA:
                raise ValueError('Add media with the Media Loader buttons before running')
        counts[item['kind']] = counts.get(item['kind'], 0) + 1
    for kind, limit in MEDIA_LIMITS.items():
        if counts.get(kind, 0) > limit:
            raise ValueError(f'{counts[kind]} {kind}s loaded; MiniMax H3 accepts {limit}')
    return json.dumps(items, ensure_ascii=False)


def prepared_run(source, changes):
    prompt, title = get_source(source)
    prompt = apply_overrides(prompt, changes)
    return prompt, title


def queue_prompt(prompt, title='Remote job'):
    # User workflow contains its node connections; ComfyUI performs final node validation.
    # %date:...% in filename prefixes is expanded per submission, as the ComfyUI frontend does.
    prompt = wc.apply_text_replacements(copy.deepcopy(prompt))
    return http_json('/prompt', 'POST', {'prompt': prompt, 'client_id': 'comfy-mobile-dashboard',
                                       'extra_data': {'extra_pnginfo': {'workflow_name': title}}})


def image_mime(name, body):
    ext = Path(name).suffix.lower()
    if ext not in IMAGE_TYPES:
        raise ValueError('Only PNG, JPEG, WebP, GIF and BMP files are supported')
    sig = body[:16]
    signatures = {'.png': sig.startswith(b'\x89PNG\r\n\x1a\n'),
                  '.jpg': sig.startswith(b'\xff\xd8\xff'), '.jpeg': sig.startswith(b'\xff\xd8\xff'),
                  '.webp': sig.startswith(b'RIFF') and sig[8:12] == b'WEBP',
                  '.gif': sig.startswith((b'GIF87a', b'GIF89a')),
                  '.bmp': sig.startswith(b'BM')}
    if not signatures[ext]:
        raise ValueError('The file does not appear to contain a valid ' + ext + ' image')
    return IMAGE_TYPES[ext]


def upload_image(name, body):
    mime = image_mime(name, body)
    if not body or len(body) > MAX_IMAGE:
        raise ValueError('Images must be between 1 byte and 40 MiB')
    clean = core.safe_filename(name)
    final_name = f'mobile_{uuid.uuid4().hex[:16]}_{clean}'
    boundary = 'comfy_mobile_' + secrets.token_hex(12)
    # ComfyUI accepts multipart/form-data POST /upload/image.
    header = (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
              f'filename="{final_name}"\r\nContent-Type: {mime}\r\n\r\n').encode('utf-8')
    tail = (f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\n'
            f'input\r\n--{boundary}--\r\n').encode()
    req = urllib.request.Request(core.COMFY_URL + '/upload/image', data=header + body + tail,
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
                                 method='POST')
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            uploaded = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise ValueError(f'ComfyUI rejected the image: {exc.code} {exc.read(512)!r}') from exc
    path = uploaded.get('name')
    subfolder = uploaded.get('subfolder') or ''
    if not isinstance(path, str) or not path:
        raise ValueError('ComfyUI did not return an uploaded filename')
    full_name = (subfolder.strip('/') + '/' if subfolder else '') + path
    with LOCK:
        STAGED.add(full_name)
    return {'filename': full_name, 'type': uploaded.get('type', 'input')}


def h3_token(refresh=False):
    with LOCK:
        if refresh or not OBJECT_INFO.get('h3_token'):
            try:
                OBJECT_INFO['h3_token'] = core.comfy_get('/minimax_h3/token')['token']
            except (urllib.error.HTTPError, KeyError, TypeError) as exc:
                raise ValueError('The Fantastic H3 Media Loader extension is not loaded in ComfyUI') from exc
        return OBJECT_INFO['h3_token']


def upload_media(name, source, size):
    """Stream a file into ComfyUI/input/minimax_h3 through the Media Loader's own upload route.

    Returns its metadata (file, kind, duration, has_audio, width, height) for a new item.
    """
    kind = MEDIA_KINDS.get(Path(name).suffix.lower())
    if kind is None:
        raise ValueError('The Media Loader accepts images, videos and audio files only')
    if not 0 < size <= MAX_MEDIA:
        raise ValueError('Media files must be between 1 byte and 1 GiB')
    clean = core.safe_filename(name)
    mime = mimetypes.guess_type(clean)[0] or 'application/octet-stream'
    boundary = 'comfy_mobile_' + secrets.token_hex(12)
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{clean}"\r\n'
            f'Content-Type: {mime}\r\n\r\n').encode('utf-8')
    tail = f'\r\n--{boundary}--\r\n'.encode()
    # Spool to disk so phone videos never sit in memory; the body is replayed if the token is stale.
    with tempfile.TemporaryFile() as spool:
        spool.write(head)
        remaining = size
        while remaining:
            chunk = source.read(min(remaining, 1024 * 1024))
            if not chunk:
                raise ValueError('Upload ended early')
            spool.write(chunk)
            remaining -= len(chunk)
        spool.write(tail)
        total = spool.tell()
        if kind == 'picture' and Path(clean).suffix.lower() in IMAGE_TYPES:
            spool.seek(len(head))
            image_mime(clean, spool.read(16))
        for attempt in (0, 1):
            spool.seek(0)
            req = urllib.request.Request(core.COMFY_URL + '/minimax_h3/upload', data=spool, method='POST',
                                         headers={'Content-Type': f'multipart/form-data; boundary={boundary}',
                                                  'Content-Length': str(total),
                                                  'X-MiniMaxH3-Token': h3_token(refresh=attempt == 1)})
            try:
                with urllib.request.urlopen(req, timeout=600) as resp:
                    info = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                detail = exc.read(1024).decode('utf-8', 'replace')
                if exc.code == 403 and attempt == 0:
                    continue  # ComfyUI restarted: fetch a fresh session token and retry once
                raise ValueError(f'Media Loader upload failed ({exc.code}): {detail[:300]}') from exc
    if not isinstance(info, dict) or not isinstance(info.get('file'), str):
        raise ValueError('The Media Loader did not return an uploaded file')
    with LOCK:
        STAGED_MEDIA.add(info['file'])
    return info


def input_reference(value):
    """A ComfyUI input-folder file reference ('sub/name.png' or 'name.png [input]') for /view."""
    if not isinstance(value, str) or not value or len(value) > 500 or '\x00' in value:
        raise ValueError('Invalid input file')
    value = re.sub(r'\s*\[input\]$', '', value.strip())
    if re.search(r'\[(output|temp)\]$', value) or '\\' in value:
        raise ValueError('Only ComfyUI input files can be previewed')
    parts = value.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError('Invalid input file path')
    if Path(parts[-1]).suffix.lower() not in core.IMAGE_EXT + core.VIDEO_EXT:
        raise ValueError('Only images and videos can be previewed')
    return {'filename': parts[-1], 'subfolder': '/'.join(parts[:-1]), 'type': 'input'}


def media_kind(name):
    ext = Path(name).suffix.lower()
    if ext in core.IMAGE_EXT:
        return 'image'
    if ext in core.VIDEO_EXT:
        return 'video'
    return 'audio' if ext in core.AUDIO_EXT + ('.opus',) else 'other'


def asset_reference(kind, value):
    """A ComfyUI input/output media file ('sub/name.png') for /view."""
    if kind not in ASSET_TAGS.values():
        raise ValueError('Invalid asset type')
    if not isinstance(value, str) or not value or len(value) > 500 or '\x00' in value or '\\' in value:
        raise ValueError('Invalid asset file')
    parts = value.split('/')
    if any(p in ('', '.', '..') for p in parts):
        raise ValueError('Invalid asset file path')
    if media_kind(parts[-1]) == 'other':
        raise ValueError('Only images, videos and audio can be opened')
    return {'filename': parts[-1], 'subfolder': '/'.join(parts[:-1]), 'type': kind}


def asset_item(record, tag):
    view = parse_qs(urlsplit(str(record.get('preview_url') or '')).query)
    name = view.get('filename', [record.get('name')])[0]
    sub = view.get('subfolder', [''])[0].strip('/')
    if view.get('type', [tag])[0] != tag or not isinstance(name, str) or not name:
        return None
    meta = record.get('metadata') if isinstance(record.get('metadata'), dict) else {}
    return {'name': record.get('display_name') or name, 'file': (sub + '/' if sub else '') + name,
            'type': tag, 'media': media_kind(name), 'size': record.get('size'),
            'width': meta.get('width'), 'height': meta.get('height'), 'duration': meta.get('duration')}


def media_assets(kind, offset=0, limit=60, search=''):
    """ComfyUI's Media Assets lists, newest first: generated (output) or imported (input) files."""
    tag = ASSET_TAGS.get(kind)
    if tag is None:
        raise ValueError('Choose generated or imported assets')
    offset, limit, search = max(0, int(offset)), min(max(1, int(limit)), 200), str(search)[:100]
    params = {'tags_all': tag, 'limit': limit, 'offset': offset, 'sort': 'created_at', 'order': 'desc'}
    if search:
        params['name_contains'] = search
    try:
        data = core.comfy_get('/api/assets?' + urllib.parse.urlencode(params))
        items = [asset_item(r, tag) for r in data.get('assets') or [] if isinstance(r, dict)]
        return {'items': [i for i in items if i], 'has_more': bool(data.get('has_more')), 'next': offset + limit}
    except urllib.error.HTTPError:
        # Assets API disabled or an older ComfyUI: its flat folder listing instead.
        names = [re.sub(r'\s*\[\w+\]$', '', n) for n in core.comfy_get('/internal/files/' + tag)
                 if isinstance(n, str)]
        names = [n for n in names if search.lower() in n.lower() and media_kind(n) != 'other']
        return {'items': [{'name': n, 'file': n, 'type': tag, 'media': media_kind(n)}
                          for n in names[offset:offset + limit]],
                'has_more': offset + limit < len(names), 'next': offset + limit}


def local_assets():
    items = []
    for idx, root in enumerate(ASSET_ROOTS):
        if not root.is_dir():
            continue
        for f in list(root.rglob('*'))[:1800]:
            if len(items) >= 500:
                return sorted(items, key=lambda x: x['modified'], reverse=True)
            if not f.is_file() or f.is_symlink() or f.suffix.lower() not in IMAGE_TYPES:
                continue
            try:
                if not f.resolve().is_relative_to(root):
                    continue
                s = f.stat()
                if s.st_size > MAX_IMAGE:
                    continue
                items.append({'root': idx, 'path': f.relative_to(root).as_posix(),
                              'size': s.st_size, 'modified': s.st_mtime})
            except (ValueError, OSError):
                continue
    return sorted(items, key=lambda x: x['modified'], reverse=True)


def scheduler_loop():
    while True:
        now = time.time()
        tasks = []
        with LOCK:
            for task in SCHEDULES.values():
                if task.get('enabled') and isinstance(task.get('next_at'), (int, float)) and task['next_at'] <= now:
                    task['enabled'] = task.get('repeat') == 'daily'
                    if task['enabled']:
                        nxt = task['next_at'] + 86400
                        while nxt <= now:
                            nxt += 86400
                        task['next_at'] = nxt
                    else:
                        task['next_at'] = None
                    task['status'] = 'Submitting'
                    task['last_at'] = now
                    tasks.append((task['id'], copy.deepcopy(task['prompt']), task['title']))
            if tasks:
                save_data(SCHEDULES_FILE, SCHEDULES)
        for task_id, prompt, title in tasks:
            try:
                result = queue_prompt(prompt, title)
                skipped = node_error_lines(result, prompt)
                update = {'status': 'Submitted', 'last_prompt_id': result.get('prompt_id'),
                          'error': ('ComfyUI skipped: ' + ' | '.join(skipped))[:1200] if skipped else ''}
            except Exception as exc:
                update = {'status': 'Failed', 'error': str(exc)[:1200]}
            with LOCK:
                if task_id in SCHEDULES:
                    SCHEDULES[task_id].update(update)
                    save_data(SCHEDULES_FILE, SCHEDULES)
        time.sleep(3)


CSS = '''
[hidden]{display:none!important}
nav.mainnav{display:flex;gap:9px;margin:14px 0 22px;flex-wrap:wrap;position:relative;z-index:3}
nav.mainnav button{border:1px solid #334b6d;background:#182539;color:#d7e7fa;padding:11px 16px;border-radius:10px;font-weight:650}
nav.mainnav button.active{background:#306aa1;border-color:#619acb}
[hidden]{display:none!important}.tab-page{min-width:0}.tab-page[hidden]{display:none!important}
.controlpanel{background:#151f2e;border:1px solid #2a3850;border-radius:14px;padding:15px;margin:0 0 15px}
.field{margin:12px 0}.field label{display:block;font-size:.83rem;color:#a4b4c8;margin:0 0 6px}
.field input,.field select,.field textarea{display:block;width:100%;max-width:100%;background:#0b1320;border:1px solid #3e5270;border-radius:9px;color:#e7eef7;padding:10px;font:inherit}
.field textarea{min-height:96px;resize:vertical}.field input[type=number]{max-width:240px}
.field-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}
.nodecard{border:1px solid #314561;border-radius:12px;padding:12px;margin:12px 0;background:#10192a}
.nodecard h3{font-size:1rem;margin:0 0 6px}.nodecard small{color:#9db1c9}
.inline-buttons{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.inline-buttons button{cursor:pointer}
.asset-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(124px,1fr));gap:10px;max-height:450px;overflow-y:auto}
.asset-card{border:1px solid #344761;border-radius:9px;overflow:hidden;background:#0b1423;color:#e0ecfa;text-align:left;cursor:pointer;padding:0}
.asset-card img{width:100%;height:116px;object-fit:contain;display:block;background:#030813}
.asset-card span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.7rem;padding:7px}
#asset-picker[hidden]{display:none}#asset-picker{border:1px solid #4b7399;border-radius:12px;padding:14px;margin-top:12px}
#schedule-feedback{white-space:pre-wrap;font-size:.87rem}#schedule-feedback.error{color:#ffb5af}
.scheduled-item{padding:12px 0;border-top:1px solid #33435b}
.queue-card{padding:12px 3px;border-bottom:1px solid #35435a}.queue-card:last-child{border-bottom:0}.queue-facts{display:flex;flex-wrap:wrap;gap:7px 15px;font-size:.81rem;color:#c1d3e7;margin:7px 0}.queue-preview{max-width:100%;max-height:225px;border-radius:8px;margin-top:8px;display:block;object-fit:contain;background:#091423}.progress-track{height:8px;background:#27344c;border-radius:9px;margin:8px 0;overflow:hidden}.progress-fill{height:100%;background:#72b7ed;border-radius:9px;transition:width .4s}
.output-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin:14px 0}.output-card{border:1px solid #34445b;border-radius:12px;overflow:hidden;background:#111a29;min-width:0}.output-media{height:170px;background:#080e18}.output-media>a,.output-video-shell{display:block;width:100%;height:100%;position:relative}.output-media img,.output-media video{display:block;width:100%;height:100%;object-fit:contain}.output-cardbody{padding:11px;display:grid;gap:8px}.output-filename{font-size:.85rem;overflow-wrap:anywhere}.folder-card{border:0;background:none;width:100%;min-height:170px;color:#e0efff;font-size:3.3rem;cursor:pointer}.folder-card span{font-size:.84rem;display:block;overflow-wrap:anywhere;padding:8px}.file-icon{display:grid;place-content:center;height:100%;font-size:3rem}.no-poster{background:#273245}.output-crumbs{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.crumb{background:#1b2c42;color:#cde5ff;border:1px solid #334c6b;border-radius:7px;padding:7px 10px;cursor:pointer}.error-text{color:#ffb8ad}.output-grid .button{font-size:.76rem}.output-grid .muted{font-size:.74rem}
.view-toggle{display:inline-flex;border:1px solid #334c6b;border-radius:8px;overflow:hidden}.view-toggle button{background:#1b2c42;color:#cde5ff;border:0;padding:7px 12px;cursor:pointer;font:inherit;font-size:.85rem}.view-toggle button+button{border-left:1px solid #334c6b}.view-toggle button[aria-pressed="true"]{background:#2f5d8a;color:#fff}
.output-grid.list-view{grid-template-columns:1fr;gap:14px}.output-grid.list-view .output-media,.output-grid.list-view .output-video-shell,.output-grid.list-view .output-media>a{height:auto}.output-grid.list-view .output-media img,.output-grid.list-view .output-media video{height:auto}.output-grid.list-view .output-cardbody{grid-template-columns:minmax(0,1fr) auto;gap:2px 10px;padding:8px 10px}.output-grid.list-view .output-cardbody .actions{grid-column:2;grid-row:1/span 2;align-self:center}.output-grid .dl-small{padding:5px 10px;min-width:0;font-size:.9rem;line-height:1}.output-grid.list-view .folder-card{height:auto;min-height:0;display:flex;align-items:center;gap:10px;padding:10px 12px;font-size:1.5rem;text-align:left}.output-grid.list-view .folder-card span{padding:0;font-size:.9rem}.output-grid.list-view .file-icon{height:90px}
.output-media video{cursor:pointer}
/* Grid tiles are 9:16 so vertical phone videos fill the preview; landscape media letterboxes. */
.output-grid:not(.list-view) .output-media{height:auto;aspect-ratio:9/16}
.output-grid:not(.list-view) .folder-card{height:100%}
.output-grid.list-view .output-media{aspect-ratio:auto}
.input-thumb{display:block;max-width:min(100%,220px);max-height:220px;border-radius:8px;margin-top:8px;background:#030813;object-fit:contain}
.media-editor{border:1px dashed #456182;border-radius:10px;padding:10px;margin-top:6px}
.media-list{display:grid;gap:8px;margin:8px 0}
.media-item{display:grid;grid-template-columns:64px minmax(0,1fr);gap:6px 10px;align-items:center;border:1px solid #344761;border-radius:10px;padding:8px;background:#0b1423}
.media-item.off{opacity:.5}
.media-thumb{width:64px;height:64px;object-fit:cover;border-radius:6px;background:#030813;display:grid;place-content:center;font-size:1.6rem;overflow:hidden}
.media-thumb img,.media-thumb video{width:64px;height:64px;object-fit:cover;display:block}
.media-meta{min-width:0}.media-meta strong{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.86rem}
.media-meta select{margin-top:5px;background:#0b1320;color:#e7eef7;border:1px solid #3e5270;border-radius:7px;padding:5px;font:inherit;font-size:.8rem}
.media-tools{grid-column:1/-1;display:flex;gap:6px;flex-wrap:wrap}
.media-tools button{min-width:40px;min-height:36px;padding:6px 10px}
.total-progress{position:relative;overflow:hidden;border-radius:12px;background:#1d2a3d;margin:10px 0 4px;min-height:54px}
.total-fill{position:absolute;inset:0 auto 0 0;background:#1f5f99;transition:width .4s}
.total-text{position:relative;display:grid;gap:2px;padding:9px 14px;font-size:.9rem}.total-text span{color:#b9d2ea;font-size:.82rem}
.lora-row{border:1px solid #344761;border-radius:10px;padding:9px 10px;margin:8px 0;background:#0b1423}
.lora-row.off{opacity:.5}
.lora-head{display:flex;align-items:center;gap:10px;min-width:0}.lora-head strong{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.86rem}
.field input.lora-toggle{display:inline-block;width:22px;height:22px;flex:none;padding:0;margin:0;accent-color:#ef6c3a}
.field .strength input[type=range]{display:block;padding:0;border:0;background:none}
.strength{display:grid;grid-template-columns:auto minmax(0,1fr) 76px;gap:10px;align-items:center;margin-top:7px}
.strength small{color:#9db1c9;font-size:.74rem;min-width:34px}.strength input[type=range]{width:100%;accent-color:#ef6c3a;margin:0}
.field .strength input[type=number]{max-width:76px;padding:6px 8px;text-align:right}
.stepper{display:flex;gap:6px;align-items:center}.stepper input{max-width:140px}.stepper .button{min-width:44px;min-height:40px;padding:4px 0}
.segmented{display:inline-flex;border:1px solid #334c6b;border-radius:9px;overflow:hidden;margin:6px 0 10px}
.segmented button{background:#1b2c42;color:#cde5ff;border:0;padding:9px 16px;cursor:pointer;font:inherit;font-size:.88rem;min-height:40px}
.segmented button+button{border-left:1px solid #334c6b}.segmented button[aria-pressed="true"]{background:#2f5d8a;color:#fff}
.asset-card .asset-icon{display:grid;place-content:center;height:116px;font-size:2.4rem;background:#030813}
.asset-card video{width:100%;height:116px;object-fit:contain;display:block;background:#030813}
.asset-card small{display:block;padding:0 7px 7px;color:#8fa2b9;font-size:.66rem}
#conversion-status{margin-top:10px;font-size:.85rem;line-height:1.45}#conversion-status ul{margin:6px 0 0;padding-left:20px;color:#ffd2a8}

/* v6: hard boundaries between independent pages and tap-sized navigation. */
.mainnav button{cursor:pointer;min-height:44px;touch-action:manipulation}
.mainnav button:focus-visible,.history-item summary:focus-visible{outline:2px solid #8bc9fa;outline-offset:3px}
.home-current-card{margin-top:10px}
.home-empty{color:#a9bbce;font-size:.9rem;padding:18px}
.history-stack{display:grid;gap:9px}
.history-item{margin:0;padding:0;background:#151f2e;border:1px solid #31415a;border-radius:12px;overflow:hidden}
.history-item>summary{display:flex;justify-content:space-between;align-items:center;cursor:pointer;gap:10px;min-height:54px;padding:13px 16px;color:#e6f1ff;font-weight:600;list-style:none}
.history-item>summary::-webkit-details-marker{display:none}
.history-summary-status{color:#b8d5ed;display:flex;gap:9px;align-items:center;font-size:.83rem}
.history-summary-elapsed{font-variant-numeric:tabular-nums}
.history-summary-status{flex-wrap:wrap;justify-content:flex-end}
.history-summary-setting{padding:2px 8px;border-radius:999px;background:#22344d;color:#cfe3f5;font-weight:500;white-space:nowrap}
.history-item[open]>summary{background:#1e3049;border-bottom:1px solid #3b526f}
.history-item[open]>summary::after{content:'▴';color:#b8d7ef}
.history-item:not([open])>summary::after{content:'▾';color:#b8d7ef}
.history-item>summary::after{margin-left:5px}
.history-item .card{border:none;border-radius:0}
#queue-tab .gallery{display:grid;grid-template-columns:1fr;gap:0}
#queue-tab .card{width:100%}
#queue-tab .preview{height:min(55vw,280px);max-height:280px;min-height:160px}
#queue-tab .preview video{max-height:280px}
#home-tab .output-grid{margin-bottom:22px}
.recent-facts{display:flex;flex-wrap:wrap;align-items:center;gap:5px 10px;font-size:.78rem;color:#c1d3e7;font-variant-numeric:tabular-nums}
.recent-card .video-box,.recent-card .video-box video{width:100%;height:100%;object-fit:contain;display:block}
.tab-page>.section-heading{margin-top:20px}
@media(max-width:550px){nav.mainnav{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))}nav.mainnav button{min-height:48px;padding:11px 6px;font-size:.81rem} .history-summary-status{font-size:.78rem}}
nav.mainnav button:last-child:nth-child(odd){grid-column:1/-1}

/* Prompts tab: Ollama chat with conversation history. */
.chat-app{display:grid;grid-template-columns:250px minmax(0,1fr);gap:14px}
.chat-sidebar{display:flex;flex-direction:column;gap:10px;min-width:0}
.chat-list{display:grid;gap:6px;max-height:min(66vh,680px);overflow-y:auto}
.chat-item{display:flex;align-items:stretch;border:1px solid #2a3850;border-radius:10px;background:#151f2e;overflow:hidden}
.chat-item.active{border-color:#619acb;background:#1e3049}
.chat-item-open{flex:1;min-width:0;background:none;border:0;color:#e0ecfa;text-align:left;padding:9px 10px;cursor:pointer;font:inherit}
.chat-item-open span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:.87rem}
.chat-item-open small{color:#8fa2b9;font-size:.72rem}
.chat-item-del{background:none;border:0;color:#8fa2b9;padding:0 11px;cursor:pointer}.chat-item-del:hover{color:#ffb8ad}
.chat-main{display:flex;flex-direction:column;gap:10px;min-width:0}
.chat-top{display:flex;gap:8px;align-items:center}.chat-top select{flex:1;min-width:0;background:#0b1320;border:1px solid #3e5270;border-radius:9px;color:#e7eef7;padding:9px;font:inherit;font-size:.85rem}
#chat-history-toggle{display:none}
.chat-log{background:#0d1522;border:1px solid #2a3850;border-radius:14px;padding:14px;height:min(62vh,640px);min-height:300px;overflow-y:auto;display:flex;flex-direction:column;gap:12px}
.chat-empty{margin:auto;color:#8fa2b9;text-align:center;font-size:.9rem}
.msg{display:flex;flex-direction:column;align-items:flex-start;max-width:88%}
.msg.user{align-self:flex-end;align-items:flex-end}
.msg-bubble{background:#1b2a3d;border:1px solid #2f4360;border-radius:14px;padding:10px 13px;display:grid;gap:7px;min-width:0}
.msg.user .msg-bubble{background:#234260;border-color:#3d6893}
.msg-text{white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.45;font-size:.93rem}
.msg-text:empty{display:none}.msg.streaming .msg-text:empty{display:block}.msg.streaming .msg-text::after{content:'▍';animation:blink 1s steps(1) infinite;color:#8bc9fa}
@keyframes blink{50%{opacity:0}}
.msg-image{max-width:220px;max-height:220px;border-radius:9px;display:block;object-fit:contain}
.msg-file{font-size:.8rem;color:#c1d3e7}
.msg-copy{background:none;border:0;color:#8fa2b9;font-size:.74rem;padding:4px 2px;cursor:pointer}.msg-copy:hover{color:#d7e7fa}
.chat-attachments{display:flex;flex-wrap:wrap;gap:6px}.chat-attachments:empty{display:none}
.chat-chip{display:inline-flex;align-items:center;gap:6px;border:1px solid #344761;border-radius:8px;background:#151f2e;padding:4px 6px;font-size:.78rem;max-width:100%}
.chat-chip img{width:34px;height:34px;object-fit:cover;border-radius:5px}.chat-chip span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:180px}
.chat-chip button{background:none;border:0;color:#8fa2b9;cursor:pointer;padding:2px 4px}
.chat-composer{display:flex;gap:8px;align-items:flex-end}
.chat-composer textarea{flex:1;min-width:0;resize:none;background:#0b1320;border:1px solid #3e5270;border-radius:12px;color:#e7eef7;padding:11px;font:inherit;line-height:1.35;max-height:200px}
.chat-composer .button{min-height:44px;cursor:pointer}.chat-attach{display:grid;place-content:center;font-size:1.1rem;padding:0 13px}
#chat-status:empty{display:none}
dialog.prompt-gen{background:#151f2e;color:#e7eef7;border:1px solid #4b7399;border-radius:14px;padding:16px;width:min(640px,calc(100vw - 24px));max-height:calc(100dvh - 24px)}
dialog.prompt-gen::backdrop{background:rgba(3,8,19,.72)}
.gen-preview{background:#0b1320;border:1px solid #2f4360;border-radius:9px;padding:9px 11px;font-size:.84rem;color:#c1d3e7;white-space:pre-wrap;overflow-wrap:anywhere;max-height:7.5em;overflow-y:auto;margin:6px 0 4px}
#gen-result{min-height:140px}
@media(max-width:800px){.chat-app{grid-template-columns:1fr}#chat-history-toggle{display:inline-block}.chat-sidebar{display:none}.chat-sidebar.open{display:flex}.chat-list{max-height:45vh}.chat-log{height:58vh}.msg{max-width:94%}}
@media(max-width:550px){.output-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.folder-card{min-height:135px}.output-grid.list-view{margin-left:-14px;margin-right:-14px}.output-grid.list-view .output-card{border-radius:0;border-left:0;border-right:0}.controlpanel{padding:12px}.asset-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
'''

SCHEDULE_HTML = '''
<nav class="mainnav" aria-label="Dashboard sections">
 <button id="nav-home" data-page="home" class="active" aria-current="page" type="button">🏠 Home</button>
 <button id="nav-queue" data-page="queue" type="button">📋 Queue &amp; history</button>
 <button id="nav-plan" data-page="plan" type="button">🗓️ Run &amp; schedule</button>
 <button id="nav-outputs" data-page="outputs" type="button">📂 Output browser</button>
 <button id="nav-assets" data-page="assets" type="button">🗂️ Assets</button>
 <button id="nav-prompts" data-page="prompts" type="button">💬 Prompts</button>
</nav>'''

PROMPTS_HTML = '''
<section id="prompts-tab" class="tab-page" hidden>
 <h2>💬 Prompts</h2>
 <div class="chat-app">
  <aside id="chat-sidebar" class="chat-sidebar" aria-label="Conversation history">
   <button id="chat-new" class="button" type="button">+ New chat</button>
   <div id="chat-list" class="chat-list"><div class="muted">Loading…</div></div>
  </aside>
  <div class="chat-main">
   <div class="chat-top"><button id="chat-history-toggle" class="button secondary" type="button" aria-expanded="false">☰ History</button>
    <select id="chat-model" aria-label="Ollama model"></select></div>
   <div id="chat-log" class="chat-log" aria-live="polite"><div class="chat-empty">Ask a question, or attach an image or text file with 📎.</div></div>
   <div id="chat-attachments" class="chat-attachments"></div>
   <form id="chat-form" class="chat-composer">
    <label class="button secondary chat-attach" title="Attach image or text file">📎<input id="chat-file" type="file" multiple hidden></label>
    <textarea id="chat-input" rows="1" placeholder="Message Ollama…" aria-label="Message"></textarea>
    <button id="chat-send" class="button" type="submit">Send</button>
   </form>
   <div id="chat-status" class="muted" role="status"></div>
  </div>
 </div>
</section>
<dialog id="prompt-gen" class="prompt-gen" aria-labelledby="gen-title">
 <div class="heading"><h2 id="gen-title">✨ Generate prompt</h2><button id="gen-close" class="button secondary" type="button">✕ Close</button></div>
 <div class="muted" id="gen-label">Current prompt</div>
 <div id="gen-current" class="gen-preview"></div>
 <div class="field"><label for="gen-request">What should change?</label>
  <textarea id="gen-request" placeholder="e.g. make it night time with rain, more cinematic lighting"></textarea></div>
 <div class="inline-buttons"><button id="gen-run" type="button" class="button">✨ Generate</button><span id="gen-status" class="muted" role="status"></span></div>
 <div id="gen-result-wrap" hidden><div class="field"><label for="gen-result">New prompt</label><textarea id="gen-result"></textarea></div>
  <div class="inline-buttons"><button id="gen-use" type="button" class="button">✅ Use this prompt</button>
  <button id="gen-again" type="button" class="button secondary">↻ Modify this result</button></div></div>
</dialog>'''

PLAN_HTML = '''
<section id="plan-tab" class="tab-page" hidden>
 <h2>🗓️ Workflow runner &amp; scheduler</h2>
 <div class="controlpanel"><div class="muted">Run the latest snapshot from an open ComfyUI tab (via the optional bridge) or a JSON workflow from your configured PC directories. UI/canvas workflows are converted to API format automatically and saved in the <code>converted</code> folder. All changes apply to the queued copy only.</div>
 <div class="field"><label for="workflow-select">Workflow</label><select id="workflow-select"><option value="">Choose a workflow…</option></select></div>
 <div class="inline-buttons"><button id="reload-workflows" class="button secondary" type="button">↻ Refresh workflow list</button><button id="convert-workflow" class="button secondary" type="button" hidden>⇄ Re-convert from UI file</button><span id="workflow-status" class="muted"></span></div>
 <div id="conversion-status" hidden></div></div>
 <div id="parameter-panel" class="controlpanel" hidden><h2>Parameters</h2><div id="parameter-nodes"></div>
 <h2>🖼️ Inputs &amp; media</h2><div class="muted">Use Select image beside a Load Image node, or Add media in a Fantastic H3 Media Loader. Choose from ComfyUI's Media Assets (Generated or Imported), configured PC folders, or upload directly from your device.</div>
 <div id="asset-picker" hidden><div class="inline-buttons"><strong id="asset-title">Select an image</strong><button id="close-assets" class="button secondary" type="button">Close</button></div>
 <div class="field"><label for="phone-image" id="phone-label">Upload from phone/device</label><input id="phone-image" type="file" accept=".png,.jpg,.jpeg,.webp,.gif,.bmp,image/png,image/jpeg,image/webp,image/gif,image/bmp"></div>
 <div class="segmented" id="picker-sources" role="group" aria-label="Asset source"><button type="button" data-source="generated" aria-pressed="true">Generated</button><button type="button" data-source="imported" aria-pressed="false">Imported</button><button type="button" data-source="pc" aria-pressed="false">PC folders</button></div>
 <div class="field"><input id="picker-search" type="search" placeholder="Search assets…" aria-label="Search assets"></div>
 <div id="picker-grid" class="asset-grid"></div><button id="picker-more" class="button secondary" type="button" style="margin-top:10px" hidden>Load more</button></div>
 <div class="inline-buttons" style="margin-top:14px"><button id="run-now" type="button" class="button">▶ Queue now</button></div>
 <h2 style="margin-top:24px">Schedule execution</h2><div class="field-grid"><div class="field"><label for="start-at">Run at (your device's local time)</label><input id="start-at" type="datetime-local"></div>
 <div class="field"><label for="repeat-mode">Repeat</label><select id="repeat-mode"><option value="once">Once</option><option value="daily">Every 24 hours</option></select></div></div>
 <button id="create-schedule" type="button" class="button secondary">+ Create scheduled job</button>
 <p class="muted">Scheduled jobs are saved on the Windows PC and run while this monitor, ComfyUI and the PC are running. Daily repetition uses a 24-hour interval (daylight-saving clock shifts may affect local time).</p></div>
 <div id="schedule-feedback" class="controlpanel" hidden></div>
 <div class="controlpanel"><div class="heading"><h2>Scheduled jobs</h2><button class="button secondary" id="refresh-schedules" type="button">↻ Refresh</button></div><div id="schedule-list" class="muted">Loading…</div></div>
</section>'''

OUTPUT_HTML = '''
<section id="output-browser-panel">
 <h2>📂 Output browser</h2>
 <p class="muted">Browse the saved files and subfolders under <code>output_dirs</code> configured on your Windows PC. Tap a thumbnail to view it or download the original file. This is read-only.</p>
 <div class="controlpanel"><div class="field-grid"><div class="field"><label for="output-root">Output location</label><select id="output-root"><option value="0">Loading…</option></select></div>
 <div class="field"><label for="output-search">Filter current loaded page</label><input id="output-search" placeholder="Search names…" type="search"></div></div>
 <div class="inline-buttons"><button class="button secondary" id="output-refresh" type="button">↻ Refresh folder</button><div class="view-toggle" role="group" aria-label="Layout"><button id="output-view-grid" type="button" aria-pressed="true">▦ Grid</button><button id="output-view-list" type="button" aria-pressed="false">☰ List</button></div><span id="output-status" class="muted" role="status"></span></div>
 <div id="output-crumbs" class="output-crumbs" style="margin-top:15px"></div></div>
 <div id="output-grid" class="output-grid"></div><button id="output-next" class="button secondary" type="button" hidden>Load more files</button>
 <p class="muted">Video thumbnails use local FFmpeg when available. Browser playback needs a supported codec.</p>
</section>'''

ASSETS_HTML = '''
<section id="assets-tab" class="tab-page" hidden>
 <h2>🗂️ Media assets</h2>
 <p class="muted">The same lists as ComfyUI's Media Assets panel, newest first: <b>Generated</b> outputs and <b>Imported</b> input files.</p>
 <div class="controlpanel"><div class="segmented" id="assets-kinds" role="group" aria-label="Asset list"><button type="button" data-kind="generated" aria-pressed="true">Generated</button><button type="button" data-kind="imported" aria-pressed="false">Imported</button></div>
 <div class="field"><input id="assets-search" type="search" placeholder="Search assets…" aria-label="Search assets"></div>
 <div class="inline-buttons"><button id="assets-refresh" class="button secondary" type="button">↻ Refresh</button><span id="assets-status" class="muted" role="status"></span></div></div>
 <div id="assets-grid" class="output-grid"></div><button id="assets-more" class="button secondary" type="button" hidden>Load more</button>
</section>'''

MAIN_HTML = '''
<section id="home-tab" class="tab-page">
 <div class="stats"><div class="stat"><b id="running">–</b><span>Running</span></div>
 <div class="stat"><b id="pending">–</b><span>Waiting</span></div>
 <div class="stat"><b id="completed">–</b><span>Recent history</span></div></div>
 <section><div class="heading"><h2>🟢 Current job</h2><button type="button" class="button secondary" id="view-queue">See full queue</button></div>
 <div class="panel" id="current-job"><div class="muted">Loading current job…</div></div></section>
 <section id="recent-outputs"><div class="heading"><h2>🖼 Recent outputs</h2><button type="button" class="button secondary" id="recent-more" hidden>All history</button></div>
 <div id="recent-grid" class="output-grid"><div class="muted">Loading recent jobs…</div></div></section>
 <div id="home-browser-host">''' + OUTPUT_HTML + '''</div>
</section>
<section id="queue-tab" class="tab-page" hidden>
 <section><h2>📋 Full queue</h2><div class="panel" id="queue"><div class="muted">Loading queue…</div></div></section>
 <section><div class="heading"><h2>Recent jobs</h2><a class="button secondary" id="history-export" href="/api/history.json">↓ History JSON</a></div>
 <p class="muted">Tap a job to expand its previews and downloads. Collapsed rows show the recorded execution time when available.</p>
 <div class="history-stack" id="gallery"><div class="muted">Loading recent jobs…</div></div></section>
</section>
''' + PLAN_HTML + '''
<section id="outputs-page" class="tab-page" hidden><div id="outputs-browser-host"></div></section>
''' + ASSETS_HTML + PROMPTS_HTML

NAV_JS = r'''
(()=>{
 'use strict';
 const names=['home','queue','plan','outputs','assets','prompts'];
 function switchTab(tab,updateHash=true){
  if(!names.includes(tab))tab='home';
  const browser=document.getElementById('output-browser-panel');
  const host=document.getElementById(tab==='outputs'?'outputs-browser-host':'home-browser-host');
  if(browser&&host&&browser.parentElement!==host)host.appendChild(browser);
  for(const name of names){
   const page=document.getElementById(name==='outputs'?'outputs-page':name+'-tab');
   const button=document.getElementById('nav-'+name);
   if(page)page.hidden=name!==tab;
   if(button){button.classList.toggle('active',name===tab);
    if(name===tab)button.setAttribute('aria-current','page');else button.removeAttribute('aria-current');}
  }
  if(updateHash&&location.hash!=='#'+tab)history.replaceState(null,'','#'+tab);
  window.dispatchEvent(new CustomEvent('comfy:tab',{detail:{tab}}));
  window.scrollTo({top:0,behavior:'instant'});
 }
 window.switchComfyTab=switchTab;
 for(const name of names){document.getElementById('nav-'+name).addEventListener('click',()=>switchTab(name));}
 document.getElementById('view-queue').addEventListener('click',()=>switchTab('queue'));
 window.addEventListener('hashchange',()=>switchTab(location.hash.slice(1),false));
 switchTab(location.hash.slice(1)||'home',false);
})();
'''


def full_html():
    content = core.HTML.replace('</style>', CSS + '\n</style>')
    content = content.replace('Read-only queue, history &amp; downloads · updates every 5 seconds',
                              'Home, job queue, workflow scheduling &amp; output browsing · updates every 5 seconds')
    start = content.index('<div class="stats">')
    end = content.index('<footer>', start)
    content = content[:start] + SCHEDULE_HTML + MAIN_HTML + content[end:]
    content = content.replace('The dashboard is read-only.',
                              'The dashboard can queue and schedule workflows; secure its URL and access.')
    content = content.replace('</head>', f'<meta name="monitor-token" content="{WRITE_TOKEN}"></head>', 1)
    scripts = '<script>' + NAV_JS + '</script><script src="/assets.js?v=7"></script><script src="/schedule.js?v=7"></script><script src="/outputs.js?v=7"></script><script src="/recent.js?v=8"></script><script src="/prompts.js?v=7"></script>'
    content = content.replace('</body>', scripts + '</body>', 1)
    return content



def current_queue_ids():
    queue = core.comfy_get('/queue')
    return {x['id'] for x in (core.queue_entries(queue.get('queue_running')) +
                               core.queue_entries(queue.get('queue_pending')))}


def queue_image_input(job_id, node_id):
    queue = core.comfy_get('/queue')
    for category in ('queue_running', 'queue_pending'):
        for entry in queue.get(category, []):
            if not isinstance(entry, list) or len(entry) < 3 or str(entry[1]) != job_id:
                continue
            prompt = entry[2]
            node = prompt.get(node_id, {}) if isinstance(prompt, dict) else {}
            if 'loadimage' not in str(node.get('class_type', '')).lower():
                raise ValueError('This is not an image input node')
            filename = (node.get('inputs') or {}).get('image')
            if not isinstance(filename, str) or Path(filename).suffix.lower() not in core.IMAGE_EXT:
                raise ValueError('Input image unavailable')
            # ComfyUI validates filenames and locations when processing its /view route.
            return {'filename': filename, 'subfolder': '', 'type': 'input'}
    raise ValueError('Input job/node not found')


def history_times(record):
    """Real ComfyUI execution_start/end timestamps (epoch seconds), or None."""
    messages = (record.get('status') or {}).get('messages') or []
    start, end = None, None
    for message in messages:
        if not isinstance(message, (list, tuple)) or len(message) < 2 or not isinstance(message[1], dict):
            continue
        kind, value = message[0], message[1].get('timestamp')
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        stamp = value / 1000 if value > 10**11 else float(value)
        if kind == 'execution_start' and start is None:
            start = stamp
        if kind in ('execution_success', 'execution_error', 'execution_interrupted'):
            end = stamp
    return start, end


def history_elapsed(record):
    """Use real ComfyUI execution_start/end timestamps, never estimate job runtime."""
    start, end = history_times(record)
    if start is None or end is None or not 0 <= end - start <= 14 * 86400:
        return None
    return round(end - start, 1)


def history_completed(record):
    start, end = history_times(record)
    stamp = end if end is not None else start
    return round(stamp, 1) if stamp is not None else None


def add_video_sizes(record, files):
    """Attach on-disk sizes to videos so previews can pick the largest (best quality) one."""
    for f in files:
        if Path(f['filename']).suffix.lower() not in media.VIDEO_EXT:
            continue
        try:
            disk = media.related_job_file(OUTPUT_ROOTS, record['outputs'][f['node']][f['kind']][f['index']])
            if disk is not None:
                f['size'] = disk.stat().st_size
        except (LookupError, TypeError, OSError):
            pass
    return files


def history_settings(record):
    """Output settings (megapixels, length/duration) the job actually ran with, from its saved prompt."""
    prompt = record.get('prompt')
    if not isinstance(prompt, list) or len(prompt) < 3 or not isinstance(prompt[2], dict):
        return []
    try:
        editors = node_editors(prompt[2])
    except (KeyError, TypeError, AttributeError):
        return []
    out, seen = [], set()
    for node in editors:
        for f in node['fields']:
            if f['kind'] != 'setting':
                continue
            value = round(f['value'], 3)
            item = (f['label'], int(value) if value == int(value) else value)
            if item not in seen:
                seen.add(item)
                out.append({'label': item[0], 'value': item[1]})
    return out[:6]


def execution_nodes(prompt, outputs):
    """Node ids ComfyUI runs for a queued prompt: its output nodes and everything they depend on."""
    if not isinstance(prompt, dict):
        return None
    pending = [str(o) for o in outputs] if isinstance(outputs, list) and outputs else list(prompt)
    seen = set()
    while pending:
        node_id = pending.pop()
        if node_id in seen or not isinstance(prompt.get(node_id), dict):
            continue
        seen.add(node_id)
        for value in (prompt[node_id].get('inputs') or {}).values():
            if isinstance(value, list) and wc.Converter.is_link(value):
                pending.append(value[0])
    return seen


def state_data():
    queue = core.comfy_get('/queue')
    history = core.comfy_get('/history?max_items=' + str(core.HISTORY_LIMIT))
    items = []
    if isinstance(history, dict):
        for job_id, record in history.items():
            if not isinstance(record, dict):
                continue
            status = record.get('status') or {}
            prompt = record.get('prompt') or []
            num = prompt[0] if isinstance(prompt, list) and prompt and isinstance(prompt[0], (float, int)) else -1
            items.append({'id': job_id, 'number': num, 'success': status.get('status_str', 'success') == 'success',
                          'files': add_video_sizes(record, core.collect_files(record)),
                          'elapsed_seconds': history_elapsed(record),
                          'completed_at': history_completed(record),
                          'settings': history_settings(record)})
    items.sort(key=lambda item: item['number'], reverse=True)
    for item in items:
        item.pop('number', None)
    running = core.queue_entries(queue.get('queue_running'))
    pending = core.queue_entries(queue.get('queue_pending'))
    rows = {str(row[1]): row for row in queue.get('queue_running', [])
            if isinstance(row, list) and len(row) > 2 and isinstance(row[2], dict)}
    for job in running:
        LIVE.mark_running(job['id'])
        row = rows.get(job['id']) or [None, None, None]
        job['live'] = LIVE.snapshot(job['id'], row[2], execution_nodes(row[2], row[4] if len(row) > 4 else None))
    return {'queue': {'running': running, 'pending': pending}, 'history': items,
            'live_connection': LIVE.online, 'server_time': time.time()}


class Handler(core.Handler):
    server_version = 'ComfyMobile/6'

    def send_header(self, keyword, value):
        # Chat attachments and saved chat images are shown from data:/blob: URLs.
        if keyword == 'Content-Security-Policy':
            value = value.replace("img-src 'self'", "img-src 'self' data: blob:")
        return super().send_header(keyword, value)

    def stream_ndjson(self, work):
        """Run work(write), sending each dict it writes as one line of NDJSON as it arrives.

        Errors before the first line become a normal JSON error reply; later ones an error line.
        """
        started = False

        def write(item):
            nonlocal started
            if not started:
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8')
                self.send_header('X-Accel-Buffering', 'no')
                self.base_headers()
                self.end_headers()
                started = True
            self.wfile.write(json.dumps(item, ensure_ascii=False).encode('utf-8') + b'\n')
            self.wfile.flush()

        try:
            work(write)
        except (ValueError, OSError) as exc:
            if not started:
                raise
            try:
                write({'error': str(exc)[:900]})
            except OSError:
                pass
        self.close_connection = True

    def json_response(self, payload, code=200):
        return self.reply(code, json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')

    def read_body(self, limit=MAX_JSON):
        try:
            size = int(self.headers.get('Content-Length', '-1'))
        except ValueError:
            raise ValueError('Invalid Content-Length') from None
        if size < 0 or size > limit:
            raise ValueError(f'Body too large (limit {limit // 1024 // 1024} MiB)')
        return self.rfile.read(size)

    def check_write(self):
        if self.headers.get('X-Monitor-Token') != WRITE_TOKEN:
            raise PermissionError('Write token missing; refresh the page')
        origin = self.headers.get('Origin')
        if origin:
            parsed = urllib.parse.urlsplit(origin)
            if parsed.netloc.lower() != self.headers.get('Host', '').lower() or parsed.scheme not in ('http', 'https'):
                raise PermissionError('Cross-origin modification blocked')

    def do_GET(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        try:
            if parts.path == '/':
                return self.reply(200, full_html().encode('utf-8'), 'text/html; charset=utf-8')
            if parts.path == '/recent.js':
                return self.reply(200, (BASE / 'recent.js').read_bytes(), 'text/javascript; charset=utf-8')
            if parts.path == '/outputs.js':
                return self.reply(200, (BASE / 'outputs.js').read_bytes(), 'text/javascript; charset=utf-8')
            if parts.path == '/prompts.js':
                return self.reply(200, (BASE / 'prompts.js').read_bytes(), 'text/javascript; charset=utf-8')
            if parts.path == '/api/ollama/models':
                return self.json_response(ollama.models())
            if parts.path == '/api/chats':
                return self.json_response(ollama.list_chats())
            if parts.path == '/api/chat':
                return self.json_response(ollama.load_chat(query.get('id', [''])[0]))
            if parts.path == '/api/state':
                return self.json_response(state_data())
            if parts.path == '/api/outputs':
                if not OUTPUT_ROOTS:
                    raise ValueError('No output_dirs configured in config.json. Add the ComfyUI output folder path and restart.')
                return self.json_response(media.browse(OUTPUT_ROOTS, query.get('root', ['0'])[0],
                         query.get('path', [''])[0], query.get('offset', ['0'])[0], query.get('limit', ['120'])[0]))
            if parts.path == '/api/output-file':
                f = media.get_file(OUTPUT_ROOTS, query.get('root', ['0'])[0], query.get('path', [''])[0])
                return media.send_local(self, f, query.get('dl', ['0'])[0] == '1')
            if parts.path == '/api/output-poster':
                f = media.get_file(OUTPUT_ROOTS, query.get('root', ['0'])[0], query.get('path', [''])[0])
                if f.suffix.lower() not in media.VIDEO_EXT:
                    raise ValueError('Not a video')
                return self.reply(200, media.poster(f, CONFIG), 'image/jpeg')
            if parts.path == '/api/job-poster':
                record = core.get_job(query.get('job', [''])[0])
                f = core.job_file(record, query)
                if Path(f['filename']).suffix.lower() not in media.VIDEO_EXT:
                    raise ValueError('Not a video')
                disk = media.related_job_file(OUTPUT_ROOTS, f)
                if disk is None:
                    raise ValueError('Video poster unavailable: configure output_dirs matching the ComfyUI output path')
                return self.reply(200, media.poster(disk, CONFIG), 'image/jpeg')
            if parts.path == '/api/live-preview':
                ident = query.get('job', [''])[0]
                if ident not in current_queue_ids():
                    raise ValueError('Job is not currently running or queued')
                result = LIVE.get_preview(ident)
                if result is None:
                    raise ValueError('No live preview for this job')
                return self.reply(200, result[0], result[1])
            if parts.path == '/api/queue-output':
                ident = query.get('job', [''])[0]
                if ident not in current_queue_ids():
                    raise ValueError('Job is not active')
                f = LIVE.snapshot(ident).get('preview_file')
                if not f or Path(f['filename']).suffix.lower() not in core.IMAGE_EXT:
                    raise ValueError('No intermediate image preview exists')
                return self.send_file(f, False)
            if parts.path == '/api/queue-input':
                ident = query.get('job', [''])[0]
                node_id = query.get('node', [''])[0]
                f = queue_image_input(ident, node_id)
                return self.send_file(f, False)
            if parts.path == '/schedule.js':
                return self.reply(200, (BASE / 'schedule.js').read_bytes(), 'text/javascript; charset=utf-8')
            if parts.path == '/api/workflows':
                with LOCK:
                    open_items = [{'id': k, 'title': v.get('title', 'Active workflow'),
                                   'source': 'ComfyUI tab snapshot', 'updated': v.get('saved_at')}
                                  for k, v in SNAPSHOTS.items()]
                return self.json_response(open_items + workflow_file_list())
            if parts.path == '/api/workflow':
                conversion = {}
                prompt, title = get_source(query.get('id', [''])[0], conversion)
                return self.json_response({'title': title, 'nodes': node_editors(prompt),
                                           'conversion': conversion or None})
            if parts.path == '/api/input-preview':
                return self.send_file(input_reference(query.get('file', [''])[0]), False)
            if parts.path == '/api/models/loras':
                value = core.comfy_get('/models/loras')
                return self.json_response(value if isinstance(value, list) else [])
            if parts.path == '/api/assets':
                return self.json_response(local_assets())
            if parts.path == '/api/media-assets':
                return self.json_response(media_assets(query.get('kind', [''])[0], query.get('offset', ['0'])[0],
                                                       query.get('limit', ['60'])[0], query.get('q', [''])[0]))
            if parts.path == '/api/asset-file':
                ref = asset_reference(query.get('type', [''])[0], query.get('file', [''])[0])
                return self.send_file(ref, query.get('dl', ['0'])[0] == '1')
            if parts.path == '/api/asset-poster':
                ref = asset_reference(query.get('type', [''])[0], query.get('file', [''])[0])
                disk = media.related_job_file(OUTPUT_ROOTS, ref) if media_kind(ref['filename']) == 'video' else None
                if disk is None:
                    raise ValueError('Poster unavailable for this asset')
                return self.reply(200, media.poster(disk, CONFIG), 'image/jpeg')
            if parts.path == '/assets.js':
                return self.reply(200, (BASE / 'assets.js').read_bytes(), 'text/javascript; charset=utf-8')
            if parts.path == '/api/local-image':
                file = root_file(ASSET_ROOTS, int(query.get('root', ['-1'])[0]), query.get('path', [''])[0])
                if file.suffix.lower() not in IMAGE_TYPES or file.stat().st_size > MAX_IMAGE:
                    raise ValueError('Not a supported image')
                return self.reply(200, file.read_bytes(), IMAGE_TYPES[file.suffix.lower()])
            if parts.path == '/api/schedules':
                with LOCK:
                    items = [{k: v for k, v in entry.items() if k != 'prompt'}
                             for entry in SCHEDULES.values()]
                return self.json_response(sorted(items, key=lambda x: x.get('created', 0), reverse=True))
            return super().do_GET()
        except (ValueError, LookupError, OSError, json.JSONDecodeError, TimeoutError) as exc:
            return self.json_response({'error': str(exc)[:900]}, 400)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            return self.json_response({'error': str(exc)}, 502)

    def do_HEAD(self):
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        if parts.path == '/api/output-file':
            try:
                f = media.get_file(OUTPUT_ROOTS, query.get('root', ['0'])[0], query.get('path', [''])[0])
                return media.send_local(self, f, query.get('dl', ['0'])[0] == '1', head=True)
            except (ValueError, OSError):
                self.send_response(404)
                self.send_header('Content-Length', '0')
                self.base_headers()
                self.end_headers()
                return
        return super().do_HEAD()

    def do_POST(self):
        route = urlsplit(self.path).path
        try:
            if route == '/api/bridge':
                # The ComfyUI custom-node backend forwards snapshots over localhost.
                if self.client_address[0] not in ('127.0.0.1', '::1') or self.headers.get('X-Comfy-Monitor-Bridge') != 'local-comfy-ui':
                    raise PermissionError('Bridge is for the locally installed extension only')
                body = json.loads(self.read_body())
                prompt = parse_prompt(body.get('prompt'))
                if wc.is_ui_workflow(body.get('workflow')):
                    try:
                        fill_frontend_links(prompt, body['workflow'])
                    except Exception as exc:  # Best effort: keep the plain snapshot.
                        print('Bridge snapshot: Use Everywhere links not added:', exc)
                graph_key = str(body.get('graph_id') or 'active')[:160]
                sid = 'open:' + hashlib.sha256(graph_key.encode()).hexdigest()[:24]
                title = str(body.get('title') or 'ComfyUI active workflow')[:140]
                with LOCK:
                    SNAPSHOTS[sid] = {'title': title, 'prompt': prompt, 'saved_at': time.time()}
                    if len(SNAPSHOTS) > 25:
                        for key in sorted(SNAPSHOTS, key=lambda k: SNAPSHOTS[k].get('saved_at', 0))[:-25]:
                            SNAPSHOTS.pop(key, None)
                    save_data(SNAPSHOTS_FILE, SNAPSHOTS)
                return self.json_response({'id': sid})
            self.check_write()
            if route == '/api/chat':
                data = json.loads(self.read_body(MAX_CHAT_BODY))
                return self.stream_ndjson(lambda write: ollama.chat_turn(data, write))
            if route == '/api/prompt/rewrite':
                data = json.loads(self.read_body())
                messages = ollama.rewrite_messages(data.get('prompt'), data.get('changes'))
                return self.stream_ndjson(lambda write: ollama.stream_chat(
                    ollama.DEFAULT_MODEL, messages, write, ollama.REWRITE_OPTIONS))
            if route == '/api/chat/delete':
                ollama.delete_chat(json.loads(self.read_body()).get('id'))
                return self.json_response({'ok': True})
            if route == '/api/upload-image':
                name = self.headers.get('X-File-Name', '')
                return self.json_response(upload_image(urllib.parse.unquote(name), self.read_body(MAX_IMAGE)))
            if route == '/api/upload-media':
                name = urllib.parse.unquote(self.headers.get('X-File-Name', ''))
                try:
                    size = int(self.headers.get('Content-Length', '-1'))
                except ValueError:
                    raise ValueError('Invalid Content-Length') from None
                if not 0 < size <= MAX_MEDIA:
                    raise ValueError('Media files must be between 1 byte and 1 GiB')
                return self.json_response(upload_media(name, self.rfile, size))
            data = json.loads(self.read_body())
            if route == '/api/convert':
                source = str(data.get('id', ''))
                if not source.startswith('disk:'):
                    raise ValueError('Only PC workflow files can be converted')
                idx, relative, file = disk_source(source)
                try:
                    _, record = ensure_converted(idx, relative, file, force=bool(data.get('force')))
                except urllib.error.URLError as exc:
                    raise ValueError('ComfyUI must be running to convert workflows: ' + str(exc)) from exc
                return self.json_response(conversion_summary(record))
            if route == '/api/stage-asset':
                to_media = data.get('target') == 'media'
                if data.get('source') == 'pc':
                    file = root_file(ASSET_ROOTS, data.get('root'), data.get('path'))
                    if file.suffix.lower() not in IMAGE_TYPES or file.stat().st_size > MAX_IMAGE:
                        raise ValueError('PC image exceeds 40 MiB or has unsupported format')
                    if to_media:
                        with file.open('rb') as stream:
                            return self.json_response(upload_media(file.name, stream, file.stat().st_size))
                    return self.json_response(upload_image(file.name, file.read_bytes()))
                if data.get('source') == 'generated':
                    record = core.get_job(str(data.get('job', '')))
                    f = core.job_file(record, {'node': [str(data.get('node', ''))],
                                               'kind': [str(data.get('kind', ''))],
                                               'index': [str(data.get('index', ''))]})
                    if Path(f['filename']).suffix.lower() not in IMAGE_TYPES:
                        raise ValueError('Only image outputs can be used as inputs')
                    with urllib.request.urlopen(core.output_url(f), timeout=90) as resp:
                        raw = resp.read(MAX_IMAGE + 1)
                    if to_media:
                        return self.json_response(upload_media(f['filename'], io.BytesIO(raw), len(raw)))
                    return self.json_response(upload_image(f['filename'], raw))
                if data.get('source') == 'asset':
                    # A ComfyUI Media Assets entry (Generated = output, Imported = input).
                    ref = asset_reference(data.get('type'), data.get('file'))
                    if not to_media:
                        if Path(ref['filename']).suffix.lower() not in IMAGE_TYPES:
                            raise ValueError('Choose an image for a Load Image node')
                        if ref['type'] == 'input':
                            with LOCK:  # Already in ComfyUI/input: Load Image uses it as it is.
                                STAGED.add(data['file'])
                            return self.json_response({'filename': data['file'], 'type': 'input'})
                    with urllib.request.urlopen(core.output_url(ref), timeout=600) as resp:
                        if to_media:
                            size = int(resp.headers.get('Content-Length') or 0)
                            return self.json_response(upload_media(ref['filename'], resp, size))
                        raw = resp.read(MAX_IMAGE + 1)
                    return self.json_response(upload_image(ref['filename'], raw))
                raise ValueError('Invalid asset source')
            if route in ('/api/run', '/api/schedule'):
                prompt, title = prepared_run(data.get('source', ''), data.get('changes', []))
                if route == '/api/run':
                    result = queue_prompt(prompt, title)
                    return self.json_response({'prompt_id': result.get('prompt_id'),
                                               'skipped': node_error_lines(result, prompt)})
                when = data.get('when')
                mode = data.get('repeat', 'once')
                if mode not in ('once', 'daily') or not isinstance(when, str):
                    raise ValueError('Provide a valid time and recurrence')
                try:
                    stamp = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
                    if stamp.tzinfo is None:
                        raise ValueError('Schedule time must include timezone')
                    run_at = stamp.timestamp()
                except (ValueError, OverflowError) as exc:
                    raise ValueError('Invalid scheduled time: ' + str(exc)) from exc
                if not (time.time() + 3 < run_at < time.time() + 366 * 86400):
                    raise ValueError('Schedule must be in the next 3 seconds to 366 days')
                ident = uuid.uuid4().hex
                entry = {'id': ident, 'title': title, 'prompt': prompt, 'repeat': mode,
                         'next_at': run_at, 'created': time.time(), 'enabled': True,
                         'status': 'Scheduled', 'last_at': None, 'last_prompt_id': None, 'error': ''}
                with LOCK:
                    if len(SCHEDULES) >= 300:
                        raise ValueError('Maximum 300 scheduled entries; delete some')
                    SCHEDULES[ident] = entry
                    save_data(SCHEDULES_FILE, SCHEDULES)
                return self.json_response({'id': ident, 'next_at': run_at})
            if route == '/api/schedule/delete':
                ident = data.get('id', '')
                with LOCK:
                    if ident not in SCHEDULES:
                        raise ValueError('Schedule not found')
                    SCHEDULES.pop(ident)
                    save_data(SCHEDULES_FILE, SCHEDULES)
                return self.json_response({'ok': True})
            raise LookupError('Endpoint not found')
        except PermissionError as exc:
            return self.json_response({'error': str(exc)}, 403)
        except (ValueError, TypeError, LookupError, OSError, json.JSONDecodeError) as exc:
            return self.json_response({'error': str(exc)[:1300]}, 400)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            return self.json_response({'error': str(exc)[:800]}, 502)

    do_PUT = do_DELETE = do_PATCH = core.Handler.do_POST


if __name__ == '__main__':
    print('=' * 60)
    print('Comfy Mobile v6 — Home, Queue, Schedule and Output Browser')
    print('Local dashboard: http://127.0.0.1:' + str(core.PORT))
    print('ComfyUI: ' + core.COMFY_URL)
    print('Workflow folders:', [str(p) for p in WORKFLOW_ROOTS])
    print('Converted UI workflows:', CONVERTED_DIR, '(auto)' if AUTO_CONVERT else '(on demand)')
    print('Asset folders:', [str(p) for p in ASSET_ROOTS])
    print('Output folders:', [str(p) for p in OUTPUT_ROOTS])
    print('FFmpeg:', media.ffmpeg_executable(CONFIG) or 'not found (posters unavailable)')
    print('Live tabs require ComfyUI_MobileBridge custom node and open ComfyUI browser.')
    print('Publish only via private Tailscale Serve or secured Cloudflare Access.')
    print('NEVER expose port 8765 directly to the public internet.')
    print('=' * 60)
    threading.Thread(target=scheduler_loop, daemon=True, name='Scheduler').start()
    if AUTO_CONVERT and CONVERTED_DIR:
        threading.Thread(target=auto_convert_loop, daemon=True, name='AutoConvert').start()
    threading.Thread(target=LIVE.run, args=(core.COMFY_URL,), daemon=True, name='ComfyWebSocket').start()
    ThreadingHTTPServer(('127.0.0.1', core.PORT), Handler).serve_forever()
