"""Integration tests for the v4 monitor using a local simulated ComfyUI API."""
import datetime as dt
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import comfy_monitor as m

PIC=b'\x89PNG\r\n\x1a\nFAKEPNGDATA'
PROMPT={'1': {'class_type':'CLIPTextEncode', 'inputs': {'text':'before'}},
        '2': {'class_type':'LoraLoader', 'inputs': {'lora_name':'a.safetensors','strength_model':1.0,'strength_clip':0.5}},
        '3': {'class_type':'LoadImage','inputs': {'image':'start.png'}},
        '4': {'class_type':'KSampler','inputs': {'seed':44, 'steps':10}}}
RECORD={'outputs': {'5': {'images':[{'filename':'frame.png', 'subfolder':'', 'type':'output'}]}},
        'status': {'status_str':'success'},'prompt':[3,'job-1',PROMPT]}
class ComfyMock(BaseHTTPRequestHandler):
    posted=[]
    uploaded=[]
    def out(self,code,value,ctype='application/json'):
        body=json.dumps(value).encode() if ctype=='application/json' else value
        self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith('/history/job-1'):
            self.out(200,{'job-1': RECORD})
        elif self.path.startswith('/history'):
            self.out(200,{'job-1':RECORD})
        elif self.path.startswith('/queue'):
            self.out(200,{'queue_running':[],'queue_pending':[]})
        elif self.path.startswith('/models/loras'):
            self.out(200,['a.safetensors','b.safetensors'])
        elif self.path.startswith('/view'):
            self.out(200,PIC,'image/png')
        else:self.out(404,{})
    def do_POST(self):
        body=self.rfile.read(int(self.headers.get('Content-Length',0)))
        if self.path=='/prompt':
            data=json.loads(body);ComfyMock.posted.append(data)
            self.out(200,{'prompt_id':str(len(ComfyMock.posted)),'number':1,'node_errors':{}})
        elif self.path=='/upload/image':
            ComfyMock.uploaded.append(body)
            import re
            mt=re.search(rb'filename="([^"]+)"',body)
            self.out(200,{'name':mt.group(1).decode(),'subfolder':'','type':'input'})
        else:self.out(404,{})
    def log_message(self,*args):pass

class TestDashboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        root=Path(cls.temp.name);(root/'workflows').mkdir();(root/'assets').mkdir()
        (root/'workflows'/'sample.json').write_text(json.dumps(PROMPT))
        (root/'workflows'/'ui.json').write_text(json.dumps({'nodes': [],'links': []}))
        (root/'assets'/'pic.png').write_bytes(PIC)
        m.WORKFLOW_ROOTS[:]=[root/'workflows'];m.ASSET_ROOTS[:]=[root/'assets']
        m.SNAPSHOTS_FILE=root/'snapshots.json';m.SCHEDULES_FILE=root/'schedules.json'
        m.SCHEDULES.clear();m.SNAPSHOTS.clear();m.STAGED.clear()
        cls.comfy=ThreadingHTTPServer(('127.0.0.1',0),ComfyMock)
        m.core.COMFY_URL=f'http://127.0.0.1:{cls.comfy.server_port}'
        threading.Thread(target=cls.comfy.serve_forever,daemon=True).start()
        cls.app=ThreadingHTTPServer(('127.0.0.1',0),m.Handler)
        cls.base=f'http://127.0.0.1:{cls.app.server_port}'
        threading.Thread(target=cls.app.serve_forever,daemon=True).start()
        threading.Thread(target=m.scheduler_loop,daemon=True).start()
    @classmethod
    def tearDownClass(cls):
        cls.app.shutdown();cls.comfy.shutdown();cls.temp.cleanup()
    def get(self,path):
        with urllib.request.urlopen(self.base+path,timeout=10) as r:
            return r.read(),r.headers
    def post(self,path,data,token=True,origin=None,bridge=False,raw=False):
        body=data if raw else json.dumps(data).encode()
        h={'Content-Type': 'application/octet-stream' if raw else 'application/json'}
        if token:h['X-Monitor-Token']=m.WRITE_TOKEN
        if origin:h['Origin']=origin
        if bridge:h['X-Comfy-Monitor-Bridge']='local-comfy-ui'
        if raw:h['X-File-Name']='test.png'
        req=urllib.request.Request(self.base+path,body,headers=h,method='POST')
        with urllib.request.urlopen(req,timeout=10) as r:
            return json.load(r)
    def test_page_and_monitor_history(self):
        body,_=self.get('/');self.assertIn(b'Run &amp; schedule',body)
        self.assertIn(b'Queue &amp; history',body)
        js,_=self.get('/schedule.js');self.assertIn(b'/api/stage-asset',js)
        self.assertEqual(json.loads(self.get('/api/state')[0])['history'][0]['id'],'job-1')
        self.assertEqual(self.get('/api/file?job=job-1&node=5&kind=images&index=0')[0],PIC)
    def test_security(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/run',{'source':'disk:0:sample.json'},token=False)
        self.assertEqual(ctx.exception.code,403)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/run',{'source':'disk:0:sample.json'},origin='https://bad.example')
        self.assertEqual(ctx.exception.code,403)
    def test_workflows_and_nodes(self):
        listing=json.loads(self.get('/api/workflows')[0]);self.assertEqual(len([a for a in listing if a['id'].startswith('disk:')]),2)
        info=json.loads(self.get('/api/workflow?id=disk:0:sample.json')[0])
        self.assertEqual(len(info['nodes']),3)
        with self.assertRaises(urllib.error.HTTPError):
            self.get('/api/workflow?id=disk:0:ui.json')
    def test_bridge_and_run(self):
        result=self.post('/api/bridge',{'graph_id':'graph-1','title':'Current Tab','prompt':PROMPT}, token=False,bridge=True)
        self.assertTrue(result['id'].startswith('open:'))
        out=self.post('/api/run',{'source':result['id'],'changes':[
            {'node':'1','key':'text','value':'new prompt'},
            {'node':'2','key':'strength_model','value':0.45}]})
        self.assertTrue(out['prompt_id'])
        p=ComfyMock.posted[-1]['prompt']
        self.assertEqual(p['1']['inputs']['text'],'new prompt')
        self.assertEqual(p['2']['inputs']['strength_model'],0.45)
        self.assertEqual(PROMPT['1']['inputs']['text'],'before')
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/run',{'source':result['id'],'changes':[
                {'node':'4','key':'steps','value':1200}]})
    def test_staging_generated_pc_and_device(self):
        dev=self.post('/api/upload-image',PIC,raw=True)
        self.assertIn(dev['filename'],m.STAGED)
        pc=self.post('/api/stage-asset',{'source':'pc','root':0,'path':'pic.png'})
        self.assertIn(pc['filename'],m.STAGED)
        generated=self.post('/api/stage-asset',{'source':'generated','job':'job-1',
                                                   'node':'5','kind':'images','index':'0'})
        self.assertIn(generated['filename'],m.STAGED)
        source='disk:0:sample.json'
        self.post('/api/run',{'source':source,'changes':[{'node':'3','key':'image','value':generated['filename']}]})
        self.assertEqual(ComfyMock.posted[-1]['prompt']['3']['inputs']['image'],generated['filename'])
        with self.assertRaises(urllib.error.HTTPError):
            self.post('/api/run',{'source':source,'changes':[{'node':'3','key':'image','value':'../../secrets.png'}]})
    def test_schedule_creation_and_delete(self):
        target=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(minutes=10)).isoformat()
        result=self.post('/api/schedule',{'source':'disk:0:sample.json', 'changes':[],
                                           'repeat':'once','when':target})
        current=json.loads(self.get('/api/schedules')[0]);self.assertTrue(any(s['id']==result['id'] for s in current))
        self.assertNotIn('prompt',current[0])
        self.assertTrue(self.post('/api/schedule/delete',{'id':result['id']})['ok'])
    def test_scheduler_actually_submits(self):
        target=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=5)).isoformat()
        result=self.post('/api/schedule',{'source':'disk:0:sample.json', 'changes':[
            {'node':'1','key':'text','value':'scheduled job'}], 'repeat':'once','when':target})
        deadline=time.monotonic()+12
        while time.monotonic()<deadline:
            items=json.loads(self.get('/api/schedules')[0])
            found=next((s for s in items if s['id']==result['id']),None)
            if found and found.get('status')=='Submitted':
                break
            time.sleep(.4)
        self.assertEqual(found['status'],'Submitted',found)
        self.assertEqual(ComfyMock.posted[-1]['prompt']['1']['inputs']['text'],'scheduled job')
        self.assertFalse(found['enabled'])
        self.post('/api/schedule/delete',{'id':result['id']})

    def test_path_traversal(self):
        with self.assertRaises(urllib.error.HTTPError):
            self.get('/api/local-image?root=0&path=..%2Fsecrets.png')
        with self.assertRaises(urllib.error.HTTPError):
            self.get('/api/workflow?id=disk:0:..%2Fsecrets.json')

if __name__=='__main__':unittest.main(verbosity=2)
