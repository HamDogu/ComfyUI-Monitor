"""Tests for output browsing, byte ranges, queue telemetry, and poster extraction."""
import io
import struct
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import unittest
import urllib.request
import urllib.parse
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as m
import media_browser as media

VIDEO_BYTES=b''


class MockComfy(BaseHTTPRequestHandler):
    def respond(self, code, data, content='application/json'):
        body=json.dumps(data).encode() if content=='application/json' else data
        self.send_response(code)
        self.send_header('Content-Type', content)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/queue'):
            self.respond(200,{'queue_running':[[1,'run-1',{'3':{'class_type':'LoadImage','inputs':{'image':'foo.png'}},'4':{'class_type':'KSampler','inputs':{'steps':30}}},{'extra_pnginfo':{'workflow_name':'Preview Test'}}]],'queue_pending':[]})
        elif self.path.startswith('/history/run-2'):
            self.respond(200, {'run-2':{'outputs':{'7':{'gifs':[{'filename':'example.mp4','subfolder':'','type':'output'}]}}}})
        elif self.path.startswith('/history'):
            self.respond(200,{})
        elif self.path.startswith('/view'):
            self.respond(200,b'fake image','image/png')
        else:self.respond(404,{})
    def log_message(self,*args):pass


class V5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.root=Path(cls.tmp.name)
        output=cls.root/'output';output.mkdir()
        (output/'inbetween').mkdir()
        (output/'inbetween'/'output 1.png').write_bytes(b'IMG')
        (output/'large.mp4').write_bytes(b'ABCDEFGHIJKL')
        (output/'visible.txt').write_text('text')
        (output/'.private.env').write_text('no')
        (cls.root/'secret.txt').write_text('private')
        cls.app=ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        cls.base=f'http://127.0.0.1:{cls.app.server_port}'
        m.OUTPUT_ROOTS[:]=[output]
        cls.comfy=ThreadingHTTPServer(('127.0.0.1',0),MockComfy)
        m.core.COMFY_URL=f'http://127.0.0.1:{cls.comfy.server_port}'
        threading.Thread(target=cls.app.serve_forever,daemon=True).start()
        threading.Thread(target=cls.comfy.serve_forever,daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown();cls.comfy.shutdown();cls.tmp.cleanup()

    def get(self,path,headers=None,method='GET'):
        return urllib.request.urlopen(urllib.request.Request(self.base+path,headers=headers or {},method=method),timeout=25)

    def test_browse_paths_and_no_outside_access(self):
        data=json.load(self.get('/api/outputs?root=0'))
        self.assertEqual(data['items'][0]['kind'],'folder')
        self.assertFalse(any(i['name'].startswith('.') for i in data['items']))
        sub=json.load(self.get('/api/outputs?root=0&path=inbetween'))
        self.assertEqual(sub['items'][0]['name'],'output 1.png')
        for p in ('../secret.txt','.private.env','inbetween/../../secret.txt'):
            with self.assertRaises(urllib.error.HTTPError):
                self.get('/api/output-file?root=0&path='+urllib.parse.quote(p))
        with self.assertRaises(urllib.error.HTTPError):
            self.get('/api/outputs?root=0&path=..')

    def test_download_byte_range_and_head(self):
        with self.get('/api/output-file?root=0&path=large.mp4',{'Range':'bytes=2-6'}) as r:
            self.assertEqual(r.status,206)
            self.assertEqual(r.read(),b'CDEFG')
            self.assertEqual(r.headers['Content-Range'],'bytes 2-6/12')
        with self.get('/api/output-file?root=0&path=large.mp4',{'Range':'bytes=-4'}) as r:
            self.assertEqual(r.read(),b'IJKL')
        with self.get('/api/output-file?root=0&path=large.mp4&dl=1',method='HEAD') as r:
            self.assertEqual(r.headers['Content-Length'],'12')
            self.assertIn('attachment',r.headers['Content-Disposition'])
        with self.assertRaises(urllib.error.HTTPError) as bad:
            self.get('/api/output-file?root=0&path=large.mp4',{'Range':'bytes=100-101'})
        self.assertEqual(bad.exception.code,416)

    def test_history_main_controls_and_new_navigation(self):
        html=self.get('/').read().decode()
        for content in ('Output browser','output-root','Files · tap to download individually', 'Download MP4', 'Download job ZIP','Job JSON'):
            # Dynamic card labels are in core HTML's JavaScript.
            self.assertIn(content,html)
        js=self.get('/outputs.js').read().decode()
        self.assertIn('output-poster',js)
        self.assertIn('output-file',js)

    def test_queue_live_data_without_fabricating_eta(self):
        info=json.load(self.get('/api/state'))
        job=info['queue']['running'][0]
        self.assertEqual(job['title'],'Preview Test')
        self.assertEqual(job['total_nodes'],2)
        self.assertEqual(job['preview_input_node'],'3')
        self.assertIsNone(job['live'].get('eta_seconds'))
        self.assertEqual(self.get('/api/queue-input?job=run-1&node=3').read(),b'fake image')
        m.LIVE.handle_text(json.dumps({'type':'execution_start','data':{'prompt_id':'run-1'}}))
        m.LIVE.handle_text(json.dumps({'type':'executing','data':{'prompt_id':'run-1','node':'4'}}))
        m.LIVE.handle_text(json.dumps({'type':'progress','data':{'prompt_id':'run-1','node':'4','value':12,'max':30}}))
        m.LIVE.handle_text(json.dumps({'type':'progress_state','data':{'prompt_id':'run-1','nodes':{'4':{'state':'running','node_id':'4','value':12,'max':30},'3':{'state':'finished','value':1,'max':1}}}}))
        m.LIVE.handle_binary(b'\0\0\0\1\0\0\0\1\xff\xd8\xffPREVIEW')
        live=json.load(self.get('/api/state'))['queue']['running'][0]['live']
        self.assertEqual(live['step_percent'],40.0)
        self.assertEqual(live['finished_nodes'],1)
        self.assertTrue(live['has_preview'])
        self.assertEqual(self.get('/api/live-preview?job=run-1').read(),b'\xff\xd8\xffPREVIEW')

    def test_output_poster_if_ffmpeg_available(self):
        ffmpeg=media.ffmpeg_executable(m.CONFIG)
        if not ffmpeg:self.skipTest('FFmpeg unavailable')
        file=m.OUTPUT_ROOTS[0]/'tiny.mp4'
        cmd=[ffmpeg,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=blue:s=180x120:d=2:r=5',
             '-c:v','mpeg4','-y',str(file)]
        subprocess.run(cmd,check=True,timeout=20,capture_output=True)
        with self.get('/api/output-poster?root=0&path=tiny.mp4') as r:
            self.assertEqual(r.headers['Content-Type'],'image/jpeg')
            self.assertTrue(r.read().startswith(b'\xff\xd8'))
        shutil.copyfile(file,m.OUTPUT_ROOTS[0]/'example.mp4')
        with self.get('/api/job-poster?job=run-2&node=7&kind=gifs&index=0') as r:
            self.assertTrue(r.read().startswith(b'\xff\xd8'))


if __name__=='__main__':unittest.main(verbosity=2)
