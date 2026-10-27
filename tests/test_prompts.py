"""Ollama chat tab and ✨ Generate prompt rewriter, against a simulated Ollama server."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as m
import ollama_chat
from playwright.sync_api import sync_playwright

PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='


class OllamaMock(BaseHTTPRequestHandler):
    chats = []

    def log_message(self, *args):
        pass

    def out(self, value):
        body = json.dumps(value).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        self.out({'models': [{'name': 'text:latest'}, {'name': 'eyes:latest'}]})

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/api/show':
            return self.out({'capabilities': ['completion', 'vision'] if data['model'] == 'eyes:latest' else ['completion']})
        OllamaMock.chats.append(data)
        self.send_response(200); self.send_header('Content-Type', 'application/x-ndjson'); self.end_headers()
        for word in ('Hello ', 'there'):
            self.wfile.write(json.dumps({'message': {'content': word}, 'done': False}).encode() + b'\n')
        self.wfile.write(json.dumps({'message': {'content': ''}, 'done': True, 'done_reason': 'stop'}).encode() + b'\n')


class PromptsAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        ollama_chat.CHATS_DIR = Path(cls.temp.name) / 'chats'
        ollama_chat.CAPABILITIES.clear()
        cls.mock = ThreadingHTTPServer(('127.0.0.1', 0), OllamaMock)
        ollama_chat.OLLAMA_URL = f'http://127.0.0.1:{cls.mock.server_port}'
        ollama_chat.DEFAULT_MODEL = 'text:latest'
        cls.app = ThreadingHTTPServer(('127.0.0.1', 0), m.Handler)
        cls.base = f'http://127.0.0.1:{cls.app.server_port}'
        for server in (cls.mock, cls.app):
            threading.Thread(target=server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown(); cls.mock.shutdown(); cls.temp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as r:
            return json.load(r)

    def stream(self, path, data, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Monitor-Token'] = m.WRITE_TOKEN
        req = urllib.request.Request(self.base + path, json.dumps(data).encode(), headers)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return [json.loads(line) for line in r if line.strip()]
        except urllib.error.HTTPError as exc:
            return [dict(json.load(exc), status=exc.code)]

    def test_models_report_vision(self):
        data = self.get('/api/ollama/models')
        self.assertEqual(data['default'], 'text:latest')
        self.assertEqual({x['name']: x['vision'] for x in data['models']}, {'text:latest': False, 'eyes:latest': True})

    def test_rewrite_streams_with_edit_instructions(self):
        out = self.stream('/api/prompt/rewrite', {'prompt': 'a cat', 'changes': 'make it a dog'})
        self.assertEqual(''.join(c.get('delta', '') for c in out), 'Hello there')
        sent = OllamaMock.chats[-1]
        self.assertEqual(sent['messages'][0]['role'], 'system')
        self.assertIn('a cat', sent['messages'][1]['content']); self.assertIn('make it a dog', sent['messages'][1]['content'])
        self.assertEqual(sent['options']['repeat_penalty'], 1.05)
        self.assertEqual(self.stream('/api/prompt/rewrite', {'prompt': 'a cat', 'changes': ' '})[0]['status'], 400)

    def test_chat_history_files_and_images(self):
        out = self.stream('/api/chat', {'content': 'hi', 'files': [{'name': 'a.txt', 'text': 'FILE BODY'}]})
        chat_id = out[0]['id']
        self.assertIn('FILE BODY', OllamaMock.chats[-1]['messages'][0]['content'])
        self.stream('/api/chat', {'id': chat_id, 'model': 'eyes:latest', 'content': 'look', 'images': [PNG_B64]})
        self.assertEqual(len(OllamaMock.chats[-1]['messages']), 3)
        self.assertEqual(OllamaMock.chats[-1]['messages'][2]['images'], [PNG_B64])
        # Text-only models refuse new images and never receive earlier ones.
        self.assertIn('cannot read images', self.stream('/api/chat', {'id': chat_id, 'content': 'x', 'images': [PNG_B64]})[0]['error'])
        self.stream('/api/chat', {'id': chat_id, 'model': 'text:latest', 'content': 'again'})
        self.assertFalse(any('images' in msg for msg in OllamaMock.chats[-1]['messages']))
        chat = self.get('/api/chat?id=' + chat_id)
        self.assertEqual([x['role'] for x in chat['messages']], ['user', 'assistant'] * 3)
        self.assertEqual(chat['messages'][1]['content'], 'Hello there')
        self.assertIn(chat_id, [c['id'] for c in self.get('/api/chats')])
        self.stream('/api/chat/delete', {'id': chat_id})
        self.assertNotIn(chat_id, [c['id'] for c in self.get('/api/chats')])

    def test_security(self):
        self.assertEqual(self.stream('/api/chat', {'content': 'hi'}, token=False)[0]['status'], 403)
        self.assertEqual(self.stream('/api/chat', {'id': '../config', 'content': 'hi'})[0]['status'], 400)


WORKFLOW = {'title': 'Example', 'nodes': [{'id': '5', 'title': 'Positive', 'class_type': 'CLIPTextEncode',
                                          'fields': [{'key': 'text', 'kind': 'prompt', 'value': 'a cat'}]}]}
FAKE_FETCH = '''fixtures=>{window.sent=[];window.fetch=async(url,opts={})=>{
  const key=String(url).split('?')[0];window.sent.push([key,opts.body]);
  if(key==='/api/chat'||key==='/api/prompt/rewrite'){
    const lines=(key==='/api/chat'?[{id:'c1'.padEnd(32,'0'),title:'hi'}]:[]).concat([{delta:'a dog'},{delta:' at night',done:true}]);
    return new Response(lines.map(l=>JSON.stringify(l)).join('\\n')+'\\n',{headers:{'Content-Type':'application/x-ndjson'}});}
  const data=fixtures[key];return new Response(JSON.stringify(data===undefined?{error:'missing '+key}:data),
  {status:data===undefined?404:200,headers:{'Content-Type':'application/json'}});}}'''


class PromptsUI(unittest.TestCase):
    def test_chat_and_generate(self):
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'), headless=True, args=['--no-sandbox'])
            page = browser.new_page(viewport={'width': 390, 'height': 844})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            base = Path(m.__file__).parent
            html = m.full_html()
            for name in ('schedule', 'prompts'):
                html = html.replace(f'<script src="/{name}.js?v=7"></script>', '<script>' + (base / f'{name}.js').read_text(encoding='utf-8') + '</script>')
            page.goto('about:blank')
            page.evaluate(FAKE_FETCH, {'/api/state': {'queue': {'running': [], 'pending': []}, 'history': []},
                                       '/api/ollama/models': {'default': 'text:latest', 'models': [{'name': 'text:latest', 'vision': False}]},
                                       '/api/chats': [], '/api/workflows': [{'id': 'disk:0:a.json', 'title': 'A'}],
                                       '/api/workflow': WORKFLOW, '/api/schedules': [], '/api/models/loras': []})
            page.set_content(html, wait_until='load')
            page.locator('#nav-prompts').click()
            self.assertTrue(page.locator('#prompts-tab').is_visible())
            self.assertFalse(page.locator('#chat-sidebar').is_visible())  # history is behind ☰ on phones
            page.wait_for_selector('#chat-model option', state='attached')
            page.fill('#chat-input', 'hello')
            page.locator('#chat-send').click()
            page.wait_for_selector('.msg.assistant:not(.streaming)')
            self.assertEqual(page.locator('.msg.assistant .msg-text').inner_text(), 'a dog at night')
            sent = json.loads([b for k, b in page.evaluate('window.sent') if k == '/api/chat'][0])
            self.assertEqual((sent['content'], sent['model']), ('hello', 'text:latest'))
            page.locator('#chat-history-toggle').click()
            self.assertTrue(page.locator('#chat-sidebar').is_visible())

            page.locator('#nav-plan').click()
            page.wait_for_selector('select#workflow-select option[value="disk:0:a.json"]', state='attached')
            page.locator('#workflow-select').select_option('disk:0:a.json')
            page.locator('#parameter-nodes button', has_text='Generate').click()
            self.assertTrue(page.locator('#prompt-gen').is_visible())
            self.assertEqual(page.locator('#gen-current').inner_text(), 'a cat')
            page.fill('#gen-request', 'make it a dog')
            page.locator('#gen-run').click()
            page.wait_for_selector('#gen-run:has-text("Generate")')
            self.assertEqual(page.input_value('#gen-result'), 'a dog at night')
            page.locator('#gen-use').click()
            self.assertFalse(page.locator('#prompt-gen').is_visible())
            self.assertEqual(page.input_value('#parameter-nodes textarea'), 'a dog at night')
            self.assertEqual(errors, [])
            browser.close()


if __name__ == '__main__':
    unittest.main()
