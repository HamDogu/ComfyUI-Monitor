"""Optional local-only ComfyUI WebSocket listener, using Python standard library.

Publishes best-effort live steps, ETA, node names, intermediate outputs, JPEG/PNG
latent previews. Some ComfyUI installations restrict previews to the initiating
clientId, so unrelated desktop-initiated jobs may have no preview.
"""
import base64
import hashlib
import json
import secrets
import socket
import ssl
import struct
import threading
import time
from urllib.parse import urlsplit, urlencode

MAX_FRAME = 12 * 1024 * 1024


class LiveMonitor:
    def __init__(self):
        self.lock = threading.RLock()
        self.jobs = {}
        self.active_job = None
        self.online = False
        self.last_error = ''
        self.stop = threading.Event()

    def snapshot(self, job_id, prompt=None, expected=None):
        """`expected`: node ids the job will run, for the whole-workflow percentage."""
        with self.lock:
            j = self.jobs.get(job_id)
            if not j:
                return {}
            out = {k: v for k, v in j.items() if k not in ('preview', 'step_first', 'step_last', 'done')}
            preview = j.get('preview')
            out['has_preview'] = bool(preview)
            # Older previews should not accidentally appear on unrelated jobs.
            if j.get('node') and prompt and isinstance(prompt, dict):
                node = prompt.get(str(j['node']))
                if isinstance(node, dict):
                    out['node_type'] = node.get('class_type', '')
                    out['node_title'] = (node.get('_meta') or {}).get('title') or node.get('class_type', '')
            value, total = j.get('step'), j.get('total_steps')
            out['step_percent'] = round(value / total * 100, 1) if isinstance(value, (int, float)) and isinstance(total, (int, float)) and total > 0 else None
            # Like ComfyUI's "Total: 38%": finished nodes plus the running node's share.
            out['total_percent'] = None
            if expected:
                done = j.get('done', set()) & expected
                current = out['step_percent'] if j.get('node') in expected - done and out['step_percent'] else 0
                out['total_percent'] = round(min(100, (len(done) + current / 100) / len(expected) * 100), 1)
            out['elapsed_seconds'] = round(max(0, time.time() - j['started_at'])) if j.get('started_at') else None
            out['elapsed_is_observed'] = bool(j.get('started_is_observed'))
            out['eta_seconds'] = None
            last = j.get('step_last')
            first = j.get('step_first')
            if (last and first and total and value is not None and value < total and
                    last[0] > first[0] and last[1] > first[1]):
                speed = (last[1] - first[1]) / (last[0] - first[0])
                if speed > .005 and time.time() - last[0] < 25:
                    out['eta_seconds'] = round(min(30 * 24 * 3600, (total - value) / speed))
            return out

    def get_preview(self, job_id):
        with self.lock:
            info = self.jobs.get(job_id) or {}
            return info.get('preview')

    def mark_running(self, job_id):
        if not job_id:
            return
        with self.lock:
            record = self.jobs.setdefault(job_id, {})
            if not record.get('started_at'):
                record.update(started_at=time.time(), started_is_observed=True)
            self._prune()

    def _prune(self):
        if len(self.jobs) > 70:
            for ident in sorted(self.jobs, key=lambda k: self.jobs[k].get('updated', 0))[:-50]:
                self.jobs.pop(ident, None)

    def handle_text(self, data):
        try:
            msg = json.loads(data)
            kind = msg.get('type')
            info = msg.get('data') or {}
            if not isinstance(info, dict):
                return
            ident = info.get('prompt_id') or info.get('task_id')
            with self.lock:
                if kind == 'execution_start' and ident:
                    j = self.jobs.setdefault(str(ident), {})
                    j.update(started_at=time.time(), started_is_observed=False, updated=time.time())
                    self.active_job = str(ident)
                elif kind == 'executing':
                    if ident:
                        self.active_job = str(ident)
                    ident = ident or self.active_job
                    if ident:
                        j = self.jobs.setdefault(str(ident), {})
                        node = info.get('node')
                        if node != j.get('node'):
                            if j.get('node'):
                                j.setdefault('done', set()).add(j['node'])
                            for k in ('step', 'total_steps', 'step_first', 'step_last'):
                                j.pop(k, None)
                        j.update(node=str(node) if node is not None else None, updated=time.time())
                        if node is None and self.active_job == str(ident):
                            self.active_job = None
                elif kind == 'progress':
                    ident = str(ident or self.active_job or '')
                    if ident:
                        j = self.jobs.setdefault(ident, {})
                        v, maximum = info.get('value'), info.get('max')
                        if isinstance(v, (int, float)) and isinstance(maximum, (int, float)) and maximum > 0:
                            if v < j.get('step', -1) or maximum != j.get('total_steps'):
                                j.pop('step_first', None)
                            j.update(step=v, total_steps=maximum, updated=time.time())
                            now = time.time()
                            j.setdefault('step_first', (now, v))
                            j['step_last'] = (now, v)
                        if info.get('node') is not None:
                            j['node'] = str(info['node'])
                elif kind == 'progress_state' and ident:
                    nodes = info.get('nodes')
                    if isinstance(nodes, dict):
                        j = self.jobs.setdefault(str(ident), {})
                        j['finished_nodes'] = sum(1 for n in nodes.values() if isinstance(n, dict) and n.get('state') == 'finished')
                        j.setdefault('done', set()).update(str(k) for k, n in nodes.items()
                                                           if isinstance(n, dict) and n.get('state') == 'finished')
                        for node_id, node in nodes.items():
                            if not isinstance(node, dict) or node.get('state') != 'running':
                                continue
                            value, maximum = node.get('value'), node.get('max')
                            if not isinstance(value, (int, float)) or not isinstance(maximum, (int, float)) or maximum <= 1:
                                continue
                            if str(node_id) != j.get('node'):
                                for key in ('step', 'total_steps', 'step_first', 'step_last'):
                                    j.pop(key, None)
                            if value < j.get('step', -1) or maximum != j.get('total_steps'):
                                j.pop('step_first', None)
                            j.update(node=str(node.get('node_id') or node_id), step=value,
                                     total_steps=maximum, updated=time.time())
                            now = time.time()
                            j.setdefault('step_first', (now, value))
                            j['step_last'] = (now, value)
                            break
                elif kind == 'execution_cached' and ident:
                    j = self.jobs.setdefault(str(ident), {})
                    j['cached_nodes'] = [str(x) for x in (info.get('nodes') or [])][:500]
                    j.setdefault('done', set()).update(str(x) for x in (info.get('nodes') or []))
                    j['updated'] = time.time()
                elif kind == 'executed' and ident:
                    j = self.jobs.setdefault(str(ident), {})
                    if info.get('node') is not None:
                        j.setdefault('done', set()).add(str(info['node']))
                    output = info.get('output') or {}
                    # Only use output references ComfyUI itself reports. No local path passthrough.
                    if isinstance(output, dict):
                        for group in ('images', 'gifs', 'videos'):
                            media = output.get(group)
                            if isinstance(media, list):
                                for f in media:
                                    if (isinstance(f, dict) and isinstance(f.get('filename'), str)
                                            and f.get('type', 'output') in ('output', 'temp')):
                                        j['preview_file'] = {k: f.get(k) for k in ('filename', 'subfolder', 'type')}
                                        break
                            if j.get('preview_file'):
                                break
                    j['updated'] = time.time()
                elif kind in ('execution_error', 'execution_interrupted') and ident:
                    j = self.jobs.setdefault(str(ident), {})
                    j['error'] = str(info.get('exception_message') or kind)[:300]
                    j['updated'] = time.time()
                self._prune()
        except (ValueError, TypeError, AttributeError):
            return

    def handle_binary(self, payload):
        if len(payload) < 9 or len(payload) > MAX_FRAME:
            return
        event_type = struct.unpack('!I', payload[:4])[0]
        if event_type == 1:  # legacy [event][format][image]
            fmt = struct.unpack('!I', payload[4:8])[0]
            content = payload[8:]
            mime = 'image/jpeg' if fmt == 1 else 'image/png' if fmt == 2 else None
        elif event_type in (3, 4):  # newer [event][metadata size][JSON][image]
            length = struct.unpack('!I', payload[4:8])[0]
            if length > 16384 or 8 + length >= len(payload):
                return
            try:
                metadata = json.loads(payload[8:8 + length])
            except (ValueError, UnicodeDecodeError):
                return
            if not isinstance(metadata, dict):
                return
            content = payload[8 + length:]
            mime = metadata.get('image_type', '').lower()
        else:
            return
        if content[:3] == b'\xff\xd8\xff':
            mime = 'image/jpeg'
        elif content[:8] == b'\x89PNG\r\n\x1a\n':
            mime = 'image/png'
        else:
            return
        with self.lock:
            if self.active_job:
                j = self.jobs.setdefault(self.active_job, {})
                j['preview'] = (content, mime)
                j['preview_seq'] = j.get('preview_seq', 0) + 1
                j['updated'] = time.time()

    @staticmethod
    def _recvall(sock, size):
        data = bytearray()
        while len(data) < size:
            b = sock.recv(size - len(data))
            if not b:
                raise ConnectionError('ComfyUI WebSocket disconnected')
            data.extend(b)
        return bytes(data)

    @staticmethod
    def _frame(sock, opcode, data=b''):
        # Client-to-server frames MUST have a mask.
        mask = secrets.token_bytes(4)
        n = len(data)
        header = bytes([0x80 | opcode, 0x80 | n]) if n < 126 else bytes([0x80 | opcode, 0x80 | 126]) + struct.pack('!H', n) if n < 65536 else bytes([0x80 | opcode, 0x80 | 127]) + struct.pack('!Q', n)
        sock.sendall(header + mask + bytes(v ^ mask[i % 4] for i, v in enumerate(data)))

    def connect_once(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or parsed.hostname not in ('127.0.0.1', 'localhost', '::1'):
            raise ValueError('ComfyUI listener must be on localhost')
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        sock = socket.create_connection((parsed.hostname, port), timeout=12)
        try:
            if parsed.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
            sock.settimeout(30)
            key = base64.b64encode(secrets.token_bytes(16)).decode()
            path = '/ws?' + urlencode({'clientId': 'comfy-mobile-dashboard'})
            host = parsed.hostname + ':' + str(port)
            request = (f'GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                       f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
            sock.sendall(request.encode('ascii'))
            response = bytearray()
            while b'\r\n\r\n' not in response and len(response) < 16384:
                b = sock.recv(1)
                if not b:
                    raise ConnectionError('WebSocket handshake aborted')
                response.extend(b)
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            headers = response.decode('latin1', 'replace').split('\r\n')
            if ' 101 ' not in headers[0] or not any(h.lower().strip() == ('sec-websocket-accept: ' + expected).lower() for h in headers[1:]):
                raise ConnectionError('WebSocket handshake rejected')
            with self.lock:
                self.online = True
                self.last_error = ''
            frame_buf = bytearray()
            frag_op = None
            while not self.stop.is_set():
                try:
                    first, second = self._recvall(sock, 2)
                except socket.timeout:
                    self._frame(sock, 9, b'check')
                    continue
                fin = first & 0x80
                opcode = first & 15
                size = second & 127
                if size == 126:
                    size = struct.unpack('!H', self._recvall(sock, 2))[0]
                elif size == 127:
                    size = struct.unpack('!Q', self._recvall(sock, 8))[0]
                if size > MAX_FRAME:
                    raise ValueError('WebSocket event too large')
                mask = self._recvall(sock, 4) if second & 0x80 else None
                payload = self._recvall(sock, size)
                if mask:
                    payload = bytes(x ^ mask[i % 4] for i, x in enumerate(payload))
                if opcode == 8:
                    return
                if opcode == 9:
                    self._frame(sock, 10, payload[:125])
                    continue
                if opcode == 10:
                    continue
                if opcode in (1, 2):
                    frag_op = opcode
                    frame_buf = bytearray()
                elif opcode != 0:
                    continue
                frame_buf.extend(payload)
                if len(frame_buf) > MAX_FRAME:
                    raise ValueError('Fragmented event too large')
                if fin:
                    if frag_op == 1:
                        self.handle_text(bytes(frame_buf).decode('utf-8', 'replace'))
                    elif frag_op == 2:
                        self.handle_binary(bytes(frame_buf))
                    frag_op = None
                    frame_buf.clear()
        finally:
            with self.lock:
                self.online = False
            sock.close()

    def run(self, url):
        while not self.stop.is_set():
            try:
                self.connect_once(url)
            except (OSError, ValueError, ConnectionError) as exc:
                with self.lock:
                    self.last_error = str(exc)[:180]
                    self.online = False
            self.stop.wait(4)
