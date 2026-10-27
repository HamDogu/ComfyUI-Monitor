"""Thin proxy to the local Ollama server: streaming chat, saved conversations, prompt rewriting.

Ollama does the real work (model loading, chat templates, the model's own system prompt and
defaults); this module only relays /api/chat and keeps a small JSON history per conversation.
"""
import base64
import binascii
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.request
import uuid

OLLAMA_URL = os.environ.get('OLLAMA_URL', 'http://127.0.0.1:11434').rstrip('/')
DEFAULT_MODEL = 'goonsai/qwen2.5-3B-goonsai-nsfw-100k:latest'
CHATS_DIR = Path(__file__).resolve().parent / 'chats'
MAX_CHATS = 200
MAX_MESSAGES = 400
MAX_TEXT = 60000
MAX_FILE_TEXT = 512 * 1024
MAX_IMAGES = 4
MAX_IMAGE_B64 = 14 * 1024 * 1024
LOCK = threading.RLock()
CAPABILITIES = {}

# The model's Modelfile sets repeat_penalty 1.3, which mangles text copied from the original
# prompt (dropped spaces, merged words). Rewrites need to copy most of it verbatim.
REWRITE_OPTIONS = {'repeat_penalty': 1.05, 'presence_penalty': 0, 'num_predict': 4096}
# Replaces the model's built-in "write tags" system prompt, which turned descriptive prompts into tag lists.
REWRITE_SYSTEM = ('You edit image and video generation prompts. Apply the requested changes to the original '
                  'prompt and keep everything else, including its writing style and length. '
                  'Reply with only the full updated prompt.')


def configure(config):
    global OLLAMA_URL, DEFAULT_MODEL
    OLLAMA_URL = str(config.get('ollama_url') or OLLAMA_URL).rstrip('/')
    DEFAULT_MODEL = str(config.get('ollama_model') or DEFAULT_MODEL)


def ollama_request(route, data=None, timeout=30):
    body = json.dumps(data).encode('utf-8') if data is not None else None
    req = urllib.request.Request(OLLAMA_URL + route, data=body, method='POST' if body else 'GET',
                                 headers={'Content-Type': 'application/json'})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        detail = exc.read(4096).decode('utf-8', 'replace')
        try:
            detail = json.loads(detail).get('error', detail)
        except ValueError:
            pass
        raise ValueError(f'Ollama: {detail}'[:900]) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise ValueError(f'Cannot reach Ollama at {OLLAMA_URL}; is it running? ({exc})') from exc


def capabilities(model):
    if model not in CAPABILITIES:
        with ollama_request('/api/show', {'model': model}) as resp:
            CAPABILITIES[model] = json.load(resp).get('capabilities') or []
    return CAPABILITIES[model]


def models():
    with ollama_request('/api/tags') as resp:
        names = [m['name'] for m in json.load(resp).get('models', []) if m.get('name')]
    result = []
    for name in names:
        try:
            vision = 'vision' in capabilities(name)
        except ValueError:
            vision = False
        result.append({'name': name, 'vision': vision})
    return {'default': DEFAULT_MODEL, 'models': result}


def check_model(model):
    if not isinstance(model, str) or not model or len(model) > 200:
        raise ValueError('Invalid model name')
    return model


def stream_chat(model, messages, write, options=None):
    """Relay Ollama's NDJSON stream to `write(dict)`; return the full reply text.

    Stops quietly (keeping the partial reply) if the browser disconnects.
    """
    body = {'model': model, 'messages': messages, 'stream': True}
    if options:
        body['options'] = options
    reply = []
    resp = ollama_request('/api/chat', body, timeout=600)
    with resp:
        for line in resp:
            if not line.strip():
                continue
            chunk = json.loads(line)
            if chunk.get('error'):
                raise ValueError('Ollama: ' + str(chunk['error'])[:900])
            piece = (chunk.get('message') or {}).get('content') or ''
            reply.append(piece)
            try:
                write({'delta': piece} if not chunk.get('done') else
                      {'delta': piece, 'done': True, 'reason': chunk.get('done_reason')})
            except (BrokenPipeError, ConnectionError):
                break
    return ''.join(reply)


def rewrite_messages(prompt, changes):
    if not isinstance(prompt, str) or not isinstance(changes, str) or not changes.strip():
        raise ValueError('Describe the changes you want')
    if len(prompt) > 20000 or len(changes) > 4000:
        raise ValueError('Prompt or change request is too long')
    content = (f'Original prompt:\n{prompt.strip()}\n\nRequested changes:\n{changes.strip()}\n\n'
               'Write the full updated prompt.')
    return [{'role': 'system', 'content': REWRITE_SYSTEM}, {'role': 'user', 'content': content}]


# ---- Conversation history: one JSON file per conversation in ./chats ----

def chat_file(chat_id):
    if not isinstance(chat_id, str) or not re.fullmatch(r'[0-9a-f]{32}', chat_id):
        raise ValueError('Invalid conversation id')
    return CHATS_DIR / f'{chat_id}.json'


def save_chat(chat):
    CHATS_DIR.mkdir(exist_ok=True)
    path = chat_file(chat['id'])
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    try:
        temp.write_text(json.dumps(chat, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def load_chat(chat_id):
    try:
        return json.loads(chat_file(chat_id).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Conversation not found') from exc


def list_chats():
    items = []
    for path in CHATS_DIR.glob('*.json') if CHATS_DIR.is_dir() else []:
        try:
            chat = json.loads(path.read_text(encoding='utf-8'))
            items.append({'id': chat['id'], 'title': chat.get('title', 'Chat'),
                          'model': chat.get('model'), 'updated': chat.get('updated', 0)})
        except (OSError, ValueError, KeyError):
            continue
    return sorted(items, key=lambda c: c['updated'], reverse=True)


def delete_chat(chat_id):
    with LOCK:
        chat_file(chat_id).unlink(missing_ok=True)


def prune_chats():
    for old in list_chats()[MAX_CHATS:]:
        delete_chat(old['id'])


def user_message(data):
    """Validate one composed message: text plus attached text files and base64 images."""
    text = data.get('content', '')
    files = data.get('files') or []
    images = data.get('images') or []
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError('Message is too long')
    if not isinstance(files, list) or not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError(f'Attach at most {MAX_IMAGES} images')
    clean_files = []
    for f in files[:8]:
        if (not isinstance(f, dict) or not isinstance(f.get('name'), str) or
                not isinstance(f.get('text'), str) or len(f['text']) > MAX_FILE_TEXT):
            raise ValueError('Attached files must be text, up to 512 KiB each')
        clean_files.append({'name': f['name'][:200], 'text': f['text']})
    for image in images:
        if not isinstance(image, str) or len(image) > MAX_IMAGE_B64:
            raise ValueError('Images must be under 10 MiB')
        try:
            base64.b64decode(image, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('Invalid image data') from None
    if not text.strip() and not clean_files and not images:
        raise ValueError('Type a message or attach something')
    return {'role': 'user', 'content': text, 'files': clean_files, 'images': images}


def for_ollama(messages, vision=True):
    """Ollama's message shape: attached files are inlined into the text content.

    Earlier images are dropped for text-only models (e.g. after switching model mid-chat).
    """
    out = []
    for msg in messages:
        content = msg.get('content', '')
        files = msg.get('files') or []
        if files:
            content = '\n\n'.join(f"File: {f['name']}\n```\n{f['text']}\n```" for f in files) + '\n\n' + content
        item = {'role': msg['role'], 'content': content}
        if vision and msg.get('images'):
            item['images'] = msg['images']
        out.append(item)
    return out


def chat_turn(data, write):
    """Append the user's message, stream the reply and save both to the conversation."""
    model = check_model(data.get('model') or DEFAULT_MODEL)
    message = user_message(data)
    if message['images'] and 'vision' not in capabilities(model):
        raise ValueError(f'{model} cannot read images. Pick a model marked 👁 (vision) or remove the image.')
    with LOCK:
        if data.get('id'):
            chat = load_chat(data['id'])
        else:
            title = (message['content'].strip() or (message['files'][0]['name'] if message['files'] else 'Image chat'))
            chat = {'id': uuid.uuid4().hex, 'title': ' '.join(title.split())[:70],
                    'created': time.time(), 'messages': []}
        if len(chat['messages']) >= MAX_MESSAGES:
            raise ValueError('This conversation is full; start a new chat')
        chat['model'] = model
        chat['messages'].append(message)
        chat['updated'] = time.time()
        save_chat(chat)
    write({'id': chat['id'], 'title': chat['title']})
    reply = ''
    try:
        reply = stream_chat(model, for_ollama(chat['messages'], 'vision' in capabilities(model)), write)
    finally:
        with LOCK:
            if reply:
                chat['messages'].append({'role': 'assistant', 'content': reply})
            chat['updated'] = time.time()
            save_chat(chat)
            prune_chats()
